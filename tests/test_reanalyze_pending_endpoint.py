"""Reanálise EM LOTE pelo painel: ``POST /api/feedbacks/reanalyze-pending``.

Equivale ao ``flask reanalyze-pending`` da CLI, mas pela sessão do painel.
Toda a camada de LLM é MOCKADA (nenhuma chamada real a provedor, nenhuma
cota consumida); as chaves são falsas. Cobre sucesso, validação de ``limit``,
proteção de sessão/CSRF, respeito ao ``limit``, parada no primeiro erro de
provedor, ``remaining`` correto e o rate limit próprio da rota.
"""
from unittest.mock import patch

import pytest

from app import create_app, db
from app.config import Config
from app.llm import LLMNotConfiguredError
from app.models.feedback import Feedback

from tests.helpers import CSRF_HEADER, csrf_token, login

PASSWORD = "segredo-do-painel"


def _pending_uuid(i: int) -> str:
    """UUID v4 determinístico e válido para o i-ésimo feedback pendente."""
    return "11111111-1111-4111-8111-{:012d}".format(i)


ANALYZED = "22222222-2222-4222-8222-222222222222"

OK_RESULT = {
    "sentiment": "POSITIVO",
    "is_spam": False,
    "requested_features": [],
    "analyzed_by": "fake/model",
}
FAIL_RESULT = {
    "sentiment": "INCONCLUSIVO",
    "is_spam": False,
    "requested_features": [],
    "failure": "rate_limit",
}


@pytest.fixture()
def build():
    contexts = []

    def _build(*, pending=3, config=None):
        application = create_app()
        application.config.update(TESTING=True, **(config or {}))
        ctx = application.app_context()
        ctx.push()
        db.create_all()
        contexts.append(ctx)
        _seed(pending)
        return application.test_client()

    yield _build
    for ctx in contexts:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def _seed(pending):
    rows = [
        Feedback(id=_pending_uuid(i), feedback_text=f"Feedback pendente {i}",
                 sentiment="INCONCLUSIVO", analysis_status="unanalyzed",
                 analysis_error="rate_limit")
        for i in range(pending)
    ]
    rows.append(Feedback(id=ANALYZED, feedback_text="Já analisado",
                         sentiment="POSITIVO", analysis_status="analyzed"))
    db.session.add_all(rows)
    db.session.commit()


def logged_client(build, **kwargs):
    client = build(**kwargs)
    assert login(client, PASSWORD).status_code == 200
    return client


def post(client, body=None, token=None):
    headers = {CSRF_HEADER: csrf_token(client) if token is None else token}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return client.post("/api/feedbacks/reanalyze-pending", **kwargs)


# --------------------------------------------------------------- sucesso

def test_reanalyzes_the_pending_batch(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=3)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT):
        response = post(client, {"limit": 5})

    assert response.status_code == 200
    body = response.get_json()
    assert body == {
        "processed": 3,
        "analyzed": 3,
        "failed": 0,
        "remaining": 0,
        "stopped_reason": None,
    }
    # Os pendentes agora estão analisados.
    for i in range(3):
        assert db.session.get(Feedback, _pending_uuid(i)).analysis_status == "analyzed"


def test_default_limit_is_5(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=8)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT) as mock:
        response = post(client)  # sem corpo => limit padrão 5

    assert response.status_code == 200
    body = response.get_json()
    assert body["processed"] == 5
    assert body["analyzed"] == 5
    assert body["remaining"] == 3
    assert mock.call_count == 5


def test_empty_body_uses_the_default(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=2)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT):
        # Sem Content-Type JSON / corpo vazio: usa o padrão sem quebrar.
        response = client.post(
            "/api/feedbacks/reanalyze-pending",
            headers={CSRF_HEADER: csrf_token(client)})
    assert response.status_code == 200
    assert response.get_json()["processed"] == 2


def test_nothing_pending_returns_zeroes(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=0)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT) as mock:
        response = post(client, {"limit": 5})
    assert response.status_code == 200
    assert response.get_json() == {
        "processed": 0, "analyzed": 0, "failed": 0,
        "remaining": 0, "stopped_reason": None,
    }
    mock.assert_not_called()


# --------------------------------------------------------------- limit

def test_respects_the_limit(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=6)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT) as mock:
        response = post(client, {"limit": 2})
    assert response.status_code == 200
    body = response.get_json()
    assert body["processed"] == 2
    assert body["analyzed"] == 2
    assert body["remaining"] == 4
    assert mock.call_count == 2


@pytest.mark.parametrize("limit", [0, 11, -1, 100, 1.5, "5", True, False, None, [5]])
def test_rejects_an_invalid_limit(build, limit):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=3)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT) as mock:
        response = post(client, {"limit": limit})
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_limit"
    # Nada foi processado.
    mock.assert_not_called()
    for i in range(3):
        assert db.session.get(Feedback, _pending_uuid(i)).analysis_status == "unanalyzed"


@pytest.mark.parametrize("limit", [1, 10])
def test_accepts_the_edges_of_the_range(build, limit):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=1)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT):
        response = post(client, {"limit": limit})
    assert response.status_code == 200


def test_rejects_a_non_object_body(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=1)
    response = client.post(
        "/api/feedbacks/reanalyze-pending",
        json=[1, 2, 3],
        headers={CSRF_HEADER: csrf_token(client)})
    assert response.status_code == 400


# --------------------------------------------------------------- parada no 1o erro

def test_stops_at_the_first_provider_error(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=5)
    # 1o ok, 2o falha => para. O 3o em diante nem é tentado.
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               side_effect=[OK_RESULT, FAIL_RESULT, OK_RESULT, OK_RESULT, OK_RESULT]) as mock:
        response = post(client, {"limit": 5})

    assert response.status_code == 200
    body = response.get_json()
    assert body["processed"] == 2
    assert body["analyzed"] == 1
    assert body["failed"] == 1
    assert body["stopped_reason"] == "provider_error"
    # 4 ainda pendentes (o que falhou volta a pendente + os 3 não tentados).
    assert body["remaining"] == 4
    assert mock.call_count == 2


def test_first_feedback_failing_stops_immediately(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=3)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=FAIL_RESULT) as mock:
        response = post(client, {"limit": 5})
    body = response.get_json()
    assert body["processed"] == 1
    assert body["analyzed"] == 0
    assert body["failed"] == 1
    assert body["stopped_reason"] == "provider_error"
    assert body["remaining"] == 3
    assert mock.call_count == 1


# --------------------------------------------------------------- remaining

def test_remaining_reflects_the_full_backlog(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=6)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT):
        body = post(client, {"limit": 3}).get_json()
    # Processou 3 de 6 => sobram 3.
    assert body["processed"] == 3
    assert body["remaining"] == 3


# --------------------------------------------------------------- provedor ausente

def test_returns_503_when_no_provider_is_configured(build):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=2)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               side_effect=LLMNotConfiguredError()):
        response = post(client, {"limit": 5})
    assert response.status_code == 503
    assert response.get_json()["code"] == "llm_not_configured"


# --------------------------------------------------------------- sessão + CSRF

def test_needs_a_dashboard_password(build):
    client = build(config={"DASHBOARD_PASSWORD": None}, pending=2)
    response = client.post("/api/feedbacks/reanalyze-pending", json={"limit": 5})
    assert response.status_code == 403
    assert response.get_json()["code"] == "auth_not_configured"


def test_needs_a_session(build):
    client = build(config={"DASHBOARD_PASSWORD": PASSWORD}, pending=2)
    response = post(client, {"limit": 5})
    assert response.status_code == 401
    assert response.get_json()["code"] == "unauthenticated"


@pytest.mark.parametrize("token", ["", "token-errado"])
def test_requires_the_csrf_token(build, token):
    client = logged_client(build, config={"DASHBOARD_PASSWORD": PASSWORD}, pending=2)
    response = post(client, {"limit": 5}, token=token)
    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf"
    # Nenhum feedback foi tocado.
    for i in range(2):
        assert db.session.get(Feedback, _pending_uuid(i)).analysis_status == "unanalyzed"


# --------------------------------------------------------------- rate limit


class ReanalyzeLimitedConfig(Config):
    """Config com o rate-limit da reanálise ligado e baixo (2/min)."""
    TESTING = True
    DASHBOARD_PASSWORD = PASSWORD
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_REANALYZE = "2 per minute"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


@pytest.fixture()
def limited_client():
    application = create_app(ReanalyzeLimitedConfig)
    with application.app_context():
        db.create_all()
        from app.extensions import limiter
        limiter.reset()
        _seed(6)
        client = application.test_client()
        assert login(client, PASSWORD).status_code == 200
        yield client
        db.session.remove()
        db.drop_all()
        limiter.reset()


def test_batch_reanalyze_is_rate_limited(limited_client):
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               return_value=OK_RESULT):
        r1 = post(limited_client, {"limit": 1})
        r2 = post(limited_client, {"limit": 1})
        over = post(limited_client, {"limit": 1})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert over.status_code == 429
    assert over.is_json
