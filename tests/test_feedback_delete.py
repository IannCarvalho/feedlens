"""DELETE /api/feedbacks/<id>: ciclo de vida / LGPD.

Apagar um feedback tem a MESMA proteção do PATCH (sessão do painel + CSRF; o
``API_READ_TOKEN`` não vale), apaga junto as ``requested_features`` e devolve
``204``. A LLM é sempre mockada: nenhuma chamada paga.
"""
from unittest.mock import patch

import pytest

from app import create_app, db
from app.models.feedback import Feedback, RequestedFeature
from app.services.feedback_service import FeedbackService

from tests.helpers import csrf_token, login

PASSWORD = "segredo-do-painel-delete"
TOKEN = "t0ken-de-leitura-com-32-caracteres!"

# Análise mockada com uma funcionalidade, para provar o cascade.
WITH_FEATURE = {
    "sentiment": "NEGATIVO",
    "is_spam": False,
    "requested_features": [{"code": "MODO_ESCURO", "reason": "pediu modo escuro"}],
    "analyzed_by": "openai/gpt-4o-mini",
}
POSITIVE = {"sentiment": "POSITIVO", "is_spam": False, "requested_features": []}


def make_client(**config):
    application = create_app()
    application.config.update(TESTING=True, **config)
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    return application.test_client(), ctx


@pytest.fixture()
def build():
    contexts = []

    def _build(**config):
        client, ctx = make_client(**config)
        contexts.append(ctx)
        return client

    yield _build
    for ctx in contexts:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def _ingest(client, feedback_id, text, analysis, **kwargs):
    with patch(
        "app.routes.api.feedback_analyzer.analyze_feedback", return_value=analysis
    ):
        return client.post(
            "/api/feedbacks", json={"id": feedback_id, "feedback": text}, **kwargs
        )


def _authed(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    login(client, PASSWORD)
    return client


def _delete(client, fid):
    return client.delete(f"/api/feedbacks/{fid}", headers={"X-CSRF-Token": csrf_token(client)})


# ------------------------------------------------------------------ sucesso

def test_delete_removes_the_feedback_and_it_is_gone_from_get(build):
    fid = "11111111-1111-1111-1111-111111111111"
    client = _authed(build)
    _ingest(client, fid, "apagar", POSITIVE)
    assert client.get(f"/api/feedbacks/{fid}").status_code == 200

    resp = _delete(client, fid)
    assert resp.status_code == 204
    assert resp.get_data() == b""
    assert client.get(f"/api/feedbacks/{fid}").status_code == 404


def test_delete_also_removes_requested_features(build):
    fid = "22222222-2222-2222-2222-222222222222"
    client = _authed(build)
    _ingest(client, fid, "falta modo escuro", WITH_FEATURE)
    # A feature existe antes.
    assert db.session.query(RequestedFeature).count() == 1

    assert _delete(client, fid).status_code == 204
    # Feedback e feature somem juntos.
    assert db.session.query(Feedback).count() == 0
    assert db.session.query(RequestedFeature).count() == 0


# ------------------------------------------------------------------ erros

def test_delete_nonexistent_is_404(build):
    client = _authed(build)
    resp = _delete(client, "33333333-3333-3333-3333-333333333333")
    assert resp.status_code == 404
    assert resp.get_json()["error"]


def test_delete_bad_uuid_is_400(build):
    client = _authed(build)
    resp = client.delete(
        "/api/feedbacks/nao-e-uuid", headers={"X-CSRF-Token": csrf_token(client)}
    )
    assert resp.status_code == 400


# ------------------------------------------------- mesma proteção do PATCH

def test_delete_without_login_matches_patch(build):
    """Sem sessão, DELETE e PATCH respondem o MESMO código (401)."""
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    fid = "44444444-4444-4444-4444-444444444444"
    delete_resp = client.delete(f"/api/feedbacks/{fid}")
    patch_resp = client.patch(f"/api/feedbacks/{fid}", json={"is_spam": True})
    assert delete_resp.status_code == patch_resp.status_code == 401
    assert delete_resp.get_json()["code"] == "unauthenticated"


def test_delete_without_csrf_matches_patch(build):
    """Logado mas sem X-CSRF-Token, DELETE e PATCH respondem o MESMO código (403)."""
    client = _authed(build)
    fid = "55555555-5555-5555-5555-555555555555"
    _ingest(client, fid, "sem csrf", POSITIVE)
    delete_resp = client.delete(f"/api/feedbacks/{fid}")
    patch_resp = client.patch(f"/api/feedbacks/{fid}", json={"is_spam": True})
    assert delete_resp.status_code == patch_resp.status_code == 403
    assert delete_resp.get_json()["code"] == "csrf"
    # Não apagou nada.
    assert client.get(f"/api/feedbacks/{fid}").status_code == 200


def test_delete_without_dashboard_password_is_403_auth_not_configured(build):
    """Sem DASHBOARD_PASSWORD o painel é aberto: DELETE recusa, como o PATCH."""
    client = build(DASHBOARD_PASSWORD=None)
    fid = "66666666-6666-6666-6666-666666666666"
    _ingest(client, fid, "painel aberto", POSITIVE)
    resp = client.delete(
        f"/api/feedbacks/{fid}", headers={"X-CSRF-Token": csrf_token(client)}
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "auth_not_configured"


def test_read_token_does_not_authorize_delete(build):
    """O API_READ_TOKEN é só leitura: não apaga."""
    client = build(DASHBOARD_PASSWORD=None, API_READ_TOKEN=TOKEN)
    fid = "77777777-7777-7777-7777-777777777777"
    _ingest(client, fid, "so leitura", POSITIVE)
    resp = client.delete(
        f"/api/feedbacks/{fid}",
        headers={"Authorization": f"Bearer {TOKEN}", "X-CSRF-Token": csrf_token(client)},
    )
    assert resp.status_code == 403


# ------------------------------------------------------- service (cascade)

def test_service_delete_cascades_features(app):
    feedback = Feedback(feedback_text="x", sentiment="POSITIVO")
    feedback.requested_features.append(RequestedFeature(code="A", reason="r"))
    db.session.add(feedback)
    db.session.commit()
    assert db.session.query(RequestedFeature).count() == 1

    FeedbackService.delete_feedback(feedback)

    assert db.session.query(Feedback).count() == 0
    assert db.session.query(RequestedFeature).count() == 0
