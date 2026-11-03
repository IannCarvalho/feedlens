"""Valida que os workflows do GitHub são YAML parseável e têm a forma esperada.

Não executa nada: só garante que um erro de digitação no YAML não passe
despercebido (o GitHub só acusaria no push). PyYAML já é dependência
transitiva (litellm).
"""
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github' / 'workflows'


def _load(name):
    with open(WORKFLOWS / name, encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def test_db_backup_workflow_is_valid_yaml():
    doc = _load('db-backup.yml')
    assert isinstance(doc, dict)
    # 'on' é interpretado como booleano True por YAML 1.1; aceite as duas chaves.
    triggers = doc.get('on', doc.get(True))
    assert 'schedule' in triggers
    assert 'workflow_dispatch' in triggers
    assert triggers['schedule'][0]['cron'] == '0 6 * * *'
    assert 'backup' in doc['jobs']


def test_uptime_workflow_is_valid_yaml():
    doc = _load('uptime.yml')
    assert isinstance(doc, dict)
    triggers = doc.get('on', doc.get(True))
    assert 'workflow_dispatch' in triggers
    assert triggers['schedule'][0]['cron'] == '*/15 * * * *'
    assert 'ping' in doc['jobs']


def test_existing_ci_workflow_still_valid_yaml():
    assert isinstance(_load('ci.yml'), dict)
    assert isinstance(_load('panel-bump.yml'), dict)
