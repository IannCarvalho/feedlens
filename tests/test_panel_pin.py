"""Pin do painel no Dockerfile e o script que baixa o release (scripts/fetch_panel.py).

Sem rede: o download é substituído por bytes montados em memória.
"""
import hashlib
import importlib.util
import io
import re
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location('fetch_panel', ROOT / 'scripts' / 'fetch_panel.py')
fetch_panel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_panel)


def _tar(entries: dict, extra=None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if extra:
            extra(archive)
    return buffer.getvalue()


def test_dockerfile_pins_are_well_formed():
    version, sha256 = fetch_panel.read_pins(ROOT / 'Dockerfile')
    assert re.fullmatch(r'\d+\.\d+\.\d+', version)
    assert re.fullmatch(r'[0-9a-f]{64}', sha256)


def test_dockerfile_uses_the_pins_in_the_download():
    text = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'ADD --checksum=sha256:${PANEL_SHA256}' in text
    assert 'feedlens-panel-v${PANEL_VERSION}.tgz' in text


def test_read_pins_rejects_dockerfile_without_args(tmp_path):
    dockerfile = tmp_path / 'Dockerfile'
    dockerfile.write_text('FROM alpine\n', encoding='utf-8')
    with pytest.raises(fetch_panel.PanelError):
        fetch_panel.read_pins(dockerfile)


@pytest.mark.parametrize('version', ['1.0', 'v1.0.0', '1.0.0; rm -rf /', ''])
def test_invalid_version_is_rejected(version):
    with pytest.raises(fetch_panel.PanelError):
        fetch_panel.validate_pins(version, 'a' * 64)


@pytest.mark.parametrize('sha', ['abc', 'A' * 64, 'g' * 64, ''])
def test_invalid_checksum_format_is_rejected(sha):
    with pytest.raises(fetch_panel.PanelError):
        fetch_panel.validate_pins('1.0.0', sha)


def test_checksum_mismatch_is_rejected_and_nothing_is_extracted(tmp_path):
    data = _tar({'index.html': b'<div id="app"></div>'})
    with pytest.raises(fetch_panel.PanelError, match='Checksum'):
        fetch_panel.verify_checksum(data, '0' * 64)
    fetch_panel.verify_checksum(data, hashlib.sha256(data).hexdigest())


def test_extract_installs_files_and_replaces_destination(tmp_path):
    dest = tmp_path / 'spa'
    dest.mkdir()
    (dest / 'velho.txt').write_text('antigo', encoding='utf-8')

    data = _tar({'index.html': b'<div id="app"></div>', 'assets/app.js': b'1'})
    assert fetch_panel.extract(data, dest) == 2

    assert (dest / 'index.html').read_bytes() == b'<div id="app"></div>'
    assert (dest / 'assets' / 'app.js').read_bytes() == b'1'
    assert not (dest / 'velho.txt').exists()
    assert not (tmp_path / 'spa.tmp').exists()


@pytest.mark.parametrize('name', ['../fora.txt', '/abs.txt', 'a/../../fora.txt'])
def test_extract_rejects_path_traversal(tmp_path, name):
    dest = tmp_path / 'spa'
    data = _tar({'index.html': b'x', name: b'x'})
    with pytest.raises(fetch_panel.PanelError, match='suspeito'):
        fetch_panel.extract(data, dest)
    assert not dest.exists()
    assert not (tmp_path / 'fora.txt').exists()


def test_extract_rejects_symlinks(tmp_path):
    def add_link(archive):
        link = tarfile.TarInfo('atalho')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        archive.addfile(link)

    data = _tar({'index.html': b'x'}, extra=add_link)
    with pytest.raises(fetch_panel.PanelError, match='não permitido'):
        fetch_panel.extract(data, tmp_path / 'spa')


def test_extract_requires_index_html(tmp_path):
    data = _tar({'outra-coisa.txt': b'x'})
    with pytest.raises(fetch_panel.PanelError, match='index.html'):
        fetch_panel.extract(data, tmp_path / 'spa')
    assert not (tmp_path / 'spa').exists()


def test_main_installs_with_verified_download(tmp_path, monkeypatch):
    data = _tar({'index.html': b'<div id="app"></div>'})
    monkeypatch.setattr(fetch_panel, 'download', lambda url: data)
    dest = tmp_path / 'spa'

    code = fetch_panel.main([
        '--version', '1.2.3', '--sha256', hashlib.sha256(data).hexdigest(), '--dest', str(dest),
    ])

    assert code == 0
    assert (dest / 'index.html').exists()


def test_main_fails_on_checksum_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch_panel, 'download', lambda url: _tar({'index.html': b'x'}))
    dest = tmp_path / 'spa'

    code = fetch_panel.main(['--version', '1.2.3', '--sha256', '0' * 64, '--dest', str(dest)])

    assert code == 1
    assert 'Checksum' in capsys.readouterr().err
    assert not dest.exists()


def test_main_requires_version_and_checksum_together(tmp_path, capsys):
    code = fetch_panel.main(['--version', '1.2.3', '--dest', str(tmp_path / 'spa')])
    assert code == 1
    assert 'juntos' in capsys.readouterr().err
