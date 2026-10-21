"""Ingestão pública (opt-in) via Origin, sem chave, sob limites estritos."""
import logging
import uuid
from unittest.mock import patch

import pytest

from app import create_app, db
from app.config import Config
from app.ingest_auth import normalize_origin
from app.models.feedback import Feedback

ORIGIN = "https://form.exemplo.com"
KEY = "ingest-key-com-bem-mais-de-24-chars"
OK_ANALYSIS = {"sentiment": "POSITIVO", "is_spam": False, "requested_features": []}
ANALYZE = "app.routes.api.feedback_analyzer.analyze_feedback"


def payload(feedback_id=None):
    return {"id": feedback_id or str(uuid.uuid4()),
            "feedback": "Adoro o app, falta modo escuro."}


@pytest.fixture()
def build():
    contexts = []

    def _build(**config):
        application = create_app()
        application.config.update(TESTING=True, **config)
        ctx = application.app_context()
        ctx.push()
        db.create_all()
        contexts.append(ctx)
        return application.test_client()

    yield _build
    for ctx in contexts:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def stored_count():
    return db.session.query(Feedback).count()


# --------------------------------------------------------------- desligado

def test_disabled_by_default_origin_does_not_bypass_the_key(build):
    """Sem INGEST_PUBLIC_ORIGINS, o comportamento antigo é preservado: 401."""
    client = build(INGEST_API_KEYS=[KEY])
    with patch(ANALYZE) as analyze:
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.get_json()["code"] == "ingest_key_required"
    analyze.assert_not_called()
    assert stored_count() == 0


def test_disabled_open_ingestion_still_open(build):
    """Sem chave e sem origens públicas: ingestão aberta, como antes."""
    client = build(INGEST_API_KEYS=[], INGEST_PUBLIC_ORIGINS=[])
    with patch(ANALYZE, return_value=OK_ANALYSIS):
        assert client.post("/api/feedbacks", json=payload()).status_code == 201


# ------------------------------------------------------------------ ligado

def test_allowed_origin_without_key_is_accepted(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN])
    with patch(ANALYZE, return_value=OK_ANALYSIS) as analyze:
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 201
    analyze.assert_called_once()
    assert stored_count() == 1


def test_unlisted_origin_is_401(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN])
    with patch(ANALYZE) as analyze:
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": "https://malicioso.exemplo"})
    assert response.status_code == 401
    analyze.assert_not_called()
    assert stored_count() == 0


def test_missing_origin_is_401(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN])
    with patch(ANALYZE) as analyze:
        response = client.post("/api/feedbacks", json=payload())
    assert response.status_code == 401
    analyze.assert_not_called()


def test_valid_key_still_works_with_public_enabled(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN])
    with patch(ANALYZE, return_value=OK_ANALYSIS):
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"X-API-Key": KEY})
    assert response.status_code == 201


# ----------------------------------------------------------- normalização

def test_trailing_slash_in_config_matches_plain_origin(build):
    client = build(INGEST_API_KEYS=[KEY],
                   INGEST_PUBLIC_ORIGINS=["https://form.exemplo.com/"])
    with patch(ANALYZE, return_value=OK_ANALYSIS):
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 201


def test_origin_with_path_in_config_is_ignored(build):
    """Uma entrada com caminho é inválida e não autoriza ninguém."""
    client = build(INGEST_API_KEYS=[KEY],
                   INGEST_PUBLIC_ORIGINS=["https://form.exemplo.com/enviar"])
    with patch(ANALYZE) as analyze:
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 401
    analyze.assert_not_called()


@pytest.mark.parametrize("raw,expected", [
    ("https://form.exemplo.com", "https://form.exemplo.com"),
    ("https://form.exemplo.com/", "https://form.exemplo.com"),
    ("  https://form.exemplo.com//  ", "https://form.exemplo.com"),
    ("http://127.0.0.1:4200", "http://127.0.0.1:4200"),
    ("https://form.exemplo.com/enviar", ""),
    ("https://form.exemplo.com?x=1", ""),
    ("form.exemplo.com", ""),
    ("", ""),
    ("*", ""),
])
def test_normalize_origin(raw, expected):
    assert normalize_origin(raw) == expected


# ------------------------------------------------------------ teto diário

def _seed_feedbacks(n):
    for _ in range(n):
        db.session.add(Feedback(id=uuid.uuid4(), feedback_text="x",
                                sentiment="POSITIVO", is_spam=False))
    db.session.commit()


def test_daily_cap_blocks_public_without_calling_llm(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN],
                   INGEST_PUBLIC_DAILY_CAP=2)
    _seed_feedbacks(2)
    with patch(ANALYZE) as analyze:
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 429
    assert response.get_json()["code"] == "public_ingest_cap"
    analyze.assert_not_called()


def test_valid_key_ignores_the_daily_cap(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN],
                   INGEST_PUBLIC_DAILY_CAP=1)
    _seed_feedbacks(5)
    with patch(ANALYZE, return_value=OK_ANALYSIS):
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"X-API-Key": KEY})
    assert response.status_code == 201


def test_public_under_cap_is_accepted(build):
    client = build(INGEST_API_KEYS=[KEY], INGEST_PUBLIC_ORIGINS=[ORIGIN],
                   INGEST_PUBLIC_DAILY_CAP=5)
    _seed_feedbacks(2)
    with patch(ANALYZE, return_value=OK_ANALYSIS):
        response = client.post("/api/feedbacks", json=payload(),
                               headers={"Origin": ORIGIN})
    assert response.status_code == 201


# --------------------------------------------------------- rate limit próprio

class PublicLimitedConfig(Config):
    TESTING = True
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_INGEST = "100 per minute"
    RATELIMIT_INGEST_PUBLIC = "2 per minute"
    INGEST_API_KEYS = [KEY]
    INGEST_PUBLIC_ORIGINS = [ORIGIN]
    INGEST_PUBLIC_DAILY_CAP = 1000
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


def test_public_path_has_its_own_strict_rate_limit():
    application = create_app(PublicLimitedConfig)
    with application.app_context():
        from app.extensions import limiter

        db.create_all()
        limiter.reset()
        client = application.test_client()
        with patch(ANALYZE, return_value=OK_ANALYSIS):
            statuses = [client.post("/api/feedbacks", json=payload(),
                                    headers={"Origin": ORIGIN}).status_code
                        for _ in range(3)]
        limiter.reset()
        db.drop_all()
    assert statuses == [201, 201, 429]


class KeyedNotPublicLimitedConfig(PublicLimitedConfig):
    RATELIMIT_INGEST = "100 per minute"
    RATELIMIT_INGEST_PUBLIC = "1 per minute"


def test_valid_key_is_not_subject_to_the_public_rate_limit():
    """A chave segue só o RATELIMIT_INGEST, não o limite público estrito."""
    application = create_app(KeyedNotPublicLimitedConfig)
    with application.app_context():
        from app.extensions import limiter

        db.create_all()
        limiter.reset()
        client = application.test_client()
        with patch(ANALYZE, return_value=OK_ANALYSIS):
            statuses = [client.post("/api/feedbacks", json=payload(),
                                    headers={"X-API-Key": KEY}).status_code
                        for _ in range(3)]
        limiter.reset()
        db.drop_all()
    assert statuses == [201, 201, 201]


# ------------------------------------------------------------------- boot

def test_wildcard_public_origin_refuses_to_boot():
    class Wild(Config):
        INGEST_PUBLIC_ORIGINS = ["*"]
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    with pytest.raises(ValueError, match="INGEST_PUBLIC_ORIGINS"):
        create_app(Wild)


def _capture_app_warnings(build_fn, config_cls):
    """Captura WARNINGs do logger 'app' direto, imune a poluição do root.

    A suite inteira reconfigura os handlers do root logger (configure_logging),
    então caplog pode não ver estes avisos dependendo da ordem dos testes.
    Anexar um handler ao próprio logger 'app' é determinístico.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    app_logger = logging.getLogger('app')
    handler = _Collector(level=logging.WARNING)
    previous_level = app_logger.level
    previous_disabled = app_logger.disabled
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.WARNING)
    # Alembic's fileConfig (test_migrations) usa disable_existing_loggers=True e
    # pode deixar o logger 'app' desabilitado; reabilita para capturar os avisos.
    app_logger.disabled = False
    try:
        create_app(config_cls)
    finally:
        app_logger.removeHandler(handler)
        app_logger.setLevel(previous_level)
        app_logger.disabled = previous_disabled
    return '\n'.join(r.getMessage() for r in records)


def test_boot_warns_when_public_origin_absent_from_cors():
    class Prod(Config):
        FLASK_ENV = "production"
        SECRET_KEY = "s" * 40
        INGEST_API_KEYS = [KEY]
        INGEST_PUBLIC_ORIGINS = [ORIGIN]
        CORS_ORIGINS = ["https://outra.exemplo"]
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    assert "não está em CORS_ORIGINS" in _capture_app_warnings(None, Prod)


def test_boot_warns_when_cors_is_wildcard():
    class Prod(Config):
        FLASK_ENV = "production"
        SECRET_KEY = "s" * 40
        INGEST_API_KEYS = [KEY]
        INGEST_PUBLIC_ORIGINS = [ORIGIN]
        CORS_ORIGINS = ["*"]
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    assert "CORS_ORIGINS é '*'" in _capture_app_warnings(None, Prod)
