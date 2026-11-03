"""Testes da integração opcional com o Sentry.

Sem SENTRY_DSN, o Sentry não deve inicializar (comportamento = nada muda). Com
DSN, deve inicializar com PII desligada e as opções esperadas.
"""
from unittest.mock import MagicMock, patch

from app import init_sentry


def _fake_app(config):
    app = MagicMock()
    app.config = config
    return app


def test_sentry_not_initialized_without_dsn():
    """Sem SENTRY_DSN, init_sentry não chama sentry_sdk.init."""
    app = _fake_app({'SENTRY_DSN': None})
    with patch('sentry_sdk.init') as init:
        init_sentry(app)
    init.assert_not_called()


def test_sentry_initialized_with_dsn():
    """Com DSN, init_sentry chama sentry_sdk.init com as opções certas."""
    app = _fake_app({
        'SENTRY_DSN': 'https://examplekey@o0.ingest.sentry.io/0',
        'SENTRY_TRACES_SAMPLE_RATE': 0.25,
        'FLASK_ENV': 'production',
    })
    with patch('sentry_sdk.init') as init:
        init_sentry(app)
    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert kwargs['dsn'] == 'https://examplekey@o0.ingest.sentry.io/0'
    # PII desligada: os feedbacks têm texto de usuário.
    assert kwargs['send_default_pii'] is False
    assert kwargs['include_local_variables'] is False
    assert kwargs['traces_sample_rate'] == 0.25
    assert kwargs['environment'] == 'production'


def test_create_app_boots_without_sentry_dsn(app):
    """A app sobe normalmente sem DSN (fixture não define SENTRY_DSN)."""
    assert app.config.get('SENTRY_DSN') is None
