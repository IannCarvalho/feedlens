"""Baixa o painel (Vue) compilado do release do feedlens-panel para app/static/spa.

É o equivalente, fora do Docker, do estágio "panel" do Dockerfile: usa a mesma
versão e o mesmo checksum (as linhas ``ARG PANEL_VERSION`` e ``ARG PANEL_SHA256``
do Dockerfile são a fonte única) e recusa o arquivo se o checksum não bater.

Uso (na raiz do repositório):

    python scripts/fetch_panel.py
    python scripts/fetch_panel.py --version 0.2.0 --sha256 <hash do .sha256 do release>

Para desenvolver o painel em si, use o repositório feedlens-panel
(``npm run dev`` com proxy para este backend).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / 'Dockerfile'
DEFAULT_DEST = ROOT / 'app' / 'static' / 'spa'

RELEASE_URL = (
    'https://github.com/IannCarvalho/feedlens-panel/releases/download/'
    'v{version}/feedlens-panel-v{version}.tgz'
)
VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


class PanelError(Exception):
    """Falha esperada (pin inválido, checksum errado, tarball suspeito)."""


def read_pins(dockerfile: Path = DOCKERFILE) -> tuple[str, str]:
    """Lê (versão, sha256) das linhas ARG do Dockerfile."""
    text = dockerfile.read_text(encoding='utf-8')
    version = re.search(r'^ARG PANEL_VERSION=(\S+)\s*$', text, re.MULTILINE)
    sha256 = re.search(r'^ARG PANEL_SHA256=(\S+)\s*$', text, re.MULTILINE)
    if not version or not sha256:
        raise PanelError(f'ARG PANEL_VERSION/PANEL_SHA256 não encontrados em {dockerfile}.')
    return validate_pins(version.group(1), sha256.group(1))


def validate_pins(version: str, sha256: str) -> tuple[str, str]:
    if not VERSION_RE.match(version):
        raise PanelError(f'Versão inválida: {version!r} (esperado X.Y.Z).')
    if not SHA256_RE.match(sha256):
        raise PanelError('Checksum inválido (esperado 64 caracteres hexadecimais minúsculos).')
    return version, sha256


def verify_checksum(data: bytes, expected_sha256: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise PanelError(
            f'Checksum não confere: esperado {expected_sha256}, recebido {actual}. '
            'O arquivo não foi extraído.'
        )


def _check_member(member: tarfile.TarInfo) -> None:
    """Só arquivos e pastas, dentro do destino: nada de links, dispositivos ou '..'."""
    path = PurePosixPath(member.name)
    if path.is_absolute() or '..' in path.parts:
        raise PanelError(f'Caminho suspeito no tarball: {member.name!r}.')
    if not (member.isfile() or member.isdir()):
        raise PanelError(f'Tipo de entrada não permitido no tarball: {member.name!r}.')


def extract(data: bytes, dest: Path) -> int:
    """Extrai o tarball em ``dest`` (substitui o conteúdo). Devolve o nº de arquivos."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        for member in members:
            _check_member(member)
        if not any(PurePosixPath(m.name).name == 'index.html' for m in members):
            raise PanelError('O tarball não contém index.html: não parece o build do painel.')

        staging = dest.with_name(dest.name + '.tmp')
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            # filter='data' (Python 3.12+, e backport em 3.10.12/3.11.4) é uma segunda
            # barreira além da validação acima; versões mais antigas só têm a validação.
            extra = {'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}
            archive.extractall(staging, members=members, **extra)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    if dest.exists():
        shutil.rmtree(dest)
    staging.rename(dest)
    return sum(1 for m in members if m.isfile())


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--version', help='versão do painel (padrão: a do Dockerfile)')
    parser.add_argument('--sha256', help='checksum esperado (padrão: o do Dockerfile)')
    parser.add_argument('--dest', type=Path, default=DEFAULT_DEST, help='pasta de destino')
    args = parser.parse_args(argv)

    try:
        if bool(args.version) != bool(args.sha256):
            raise PanelError('Passe --version e --sha256 juntos, ou nenhum dos dois.')
        if args.version:
            version, sha256 = validate_pins(args.version, args.sha256)
        else:
            version, sha256 = read_pins()

        url = RELEASE_URL.format(version=version)
        print(f'Baixando {url}')
        data = download(url)
        verify_checksum(data, sha256)
        count = extract(data, args.dest)
    except PanelError as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1
    except OSError as error:
        print(f'Erro de rede ou de disco: {error}', file=sys.stderr)
        return 1

    print(f'Painel v{version} instalado em {args.dest} ({count} arquivos).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
