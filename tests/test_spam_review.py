"""Revisão do que a IA marcou como spam: listar (``?spam=``) e corrigir (PATCH)."""
import pytest

from app import create_app, db
from app.models.feedback import Feedback, RequestedFeature
from app.services.statistics_service import Statistics

from tests.helpers import CSRF_HEADER, csrf_token, login

PASSWORD = "segredo-do-painel"
TOKEN = "t0ken-de-leitura-com-32-caracteres!"

GOOD = "11111111-1111-4111-8111-111111111111"
SPAMMY = "22222222-2222-4222-8222-222222222222"
OTHER_SPAM = "33333333-3333-4333-8333-333333333333"
UNKNOWN = "99999999-9999-4999-8999-999999999999"


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
        seed()
        return application.test_client()

    yield _build
    for ctx in contexts:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def seed():
    good = Feedback(id=GOOD, feedback_text="App rápido, gostei", sentiment="POSITIVO", is_spam=False)
    spammy = Feedback(
        id=SPAMMY, feedback_text="Letra de música qualquer", sentiment="INCONCLUSIVO", is_spam=True)
    other = Feedback(id=OTHER_SPAM, feedback_text="Compre já", sentiment="NEGATIVO", is_spam=True)
    db.session.add_all([good, spammy, other])
    db.session.add(RequestedFeature(code="EXPORT_PDF", reason="quer PDF", feedback=spammy))
    db.session.commit()


def ids(response):
    return {item["id"] for item in response.get_json()["items"]}


def logged_client(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    assert login(client, PASSWORD).status_code == 200
    return client


def patch(client, feedback_id, body, token=None):
    headers = {CSRF_HEADER: csrf_token(client) if token is None else token}
    return client.patch(f"/api/feedbacks/{feedback_id}", json=body, headers=headers)


# --------------------------------------------------------------- listagem

def test_list_hides_spam_by_default(build):
    client = build()
    response = client.get("/api/feedbacks")
    assert ids(response) == {GOOD}
    assert response.get_json()["spam"] == "exclude"


def test_list_spam_only_shows_just_the_flagged(build):
    client = build()
    response = client.get("/api/feedbacks?spam=only")
    assert ids(response) == {SPAMMY, OTHER_SPAM}
    assert response.get_json()["pagination"]["total"] == 2
    assert response.get_json()["spam"] == "only"


def test_list_spam_include_shows_everything(build):
    client = build()
    assert ids(client.get("/api/feedbacks?spam=include")) == {GOOD, SPAMMY, OTHER_SPAM}


def test_spam_filter_combines_with_sentiment(build):
    client = build()
    assert ids(client.get("/api/feedbacks?spam=only&sentiment=INCONCLUSIVO")) == {SPAMMY}
    assert ids(client.get("/api/feedbacks?spam=include&sentiment=POSITIVO")) == {GOOD}


@pytest.mark.parametrize("value", ["tudo", "true", "1", ""])
def test_list_rejects_an_unknown_spam_mode(build, value):
    client = build()
    response = client.get(f"/api/feedbacks?spam={value}")
    assert response.status_code == 400
    assert "spam" in response.get_json()["error"]


def test_spam_listing_keeps_the_read_protection(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    assert client.get("/api/feedbacks?spam=only").status_code == 401


def test_stats_still_ignore_spam(build):
    build()
    stats = Statistics.get_dashboard_statistics()
    assert stats["total_feedbacks"] == 1


# --------------------------------------------------------------- correção

def test_patch_needs_a_dashboard_password(build):
    client = build(DASHBOARD_PASSWORD=None)
    response = client.patch(f"/api/feedbacks/{SPAMMY}", json={"is_spam": False})
    assert response.status_code == 403
    assert response.get_json()["code"] == "auth_not_configured"
    assert db.session.get(Feedback, SPAMMY).is_spam is True


def test_patch_needs_a_session(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    response = patch(client, SPAMMY, {"is_spam": False})
    assert response.status_code == 401
    assert response.get_json()["code"] == "unauthenticated"
    assert db.session.get(Feedback, SPAMMY).is_spam is True


def test_read_token_cannot_change_a_feedback(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD, API_READ_TOKEN=TOKEN)
    response = client.patch(
        f"/api/feedbacks/{SPAMMY}",
        json={"is_spam": False},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 401
    assert db.session.get(Feedback, SPAMMY).is_spam is True


@pytest.mark.parametrize("token", ["", "token-errado"])
def test_patch_requires_the_csrf_token(build, token):
    client = logged_client(build)
    response = patch(client, SPAMMY, {"is_spam": False}, token=token)
    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf"
    assert db.session.get(Feedback, SPAMMY).is_spam is True


def test_patch_unflags_spam_and_the_panel_reflects_it(build):
    client = logged_client(build)
    response = patch(client, SPAMMY, {"is_spam": False})

    assert response.status_code == 200
    body = response.get_json()
    assert body["is_spam"] is False
    # A análise original fica como estava.
    assert body["sentiment"] == "INCONCLUSIVO"
    assert [f["code"] for f in body["requested_features"]] == ["EXPORT_PDF"]

    assert ids(client.get("/api/feedbacks")) == {GOOD, SPAMMY}
    assert ids(client.get("/api/feedbacks?spam=only")) == {OTHER_SPAM}
    assert Statistics.get_dashboard_statistics()["total_feedbacks"] == 2


def test_patch_can_flag_a_feedback_as_spam(build):
    client = logged_client(build)
    assert patch(client, GOOD, {"is_spam": True}).get_json()["is_spam"] is True
    assert ids(client.get("/api/feedbacks")) == set()
    assert GOOD in ids(client.get("/api/feedbacks?spam=only"))


def test_patch_is_idempotent(build):
    client = logged_client(build)
    first = patch(client, SPAMMY, {"is_spam": False})
    second = patch(client, SPAMMY, {"is_spam": False})
    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"is_spam": "true"},
        {"is_spam": 1},
        {"is_spam": None},
        {"sentiment": "feliz"},
        {"sentiment": "positivo"},
        {"sentiment": 1},
        {"sentiment": ""},
        {"sentiment": None},
        {"feedback": "outro texto"},
        [],
        "x",
    ],
)
def test_patch_rejects_an_invalid_body(build, body):
    client = logged_client(build)
    assert patch(client, SPAMMY, body).status_code == 400
    spammy = db.session.get(Feedback, SPAMMY)
    assert spammy.is_spam is True and spammy.sentiment == "INCONCLUSIVO"


def test_patch_cannot_change_the_text_or_the_features(build):
    client = logged_client(build)
    response = patch(
        client, SPAMMY, {"is_spam": False, "feedback": "outro", "requested_features": []})
    body = response.get_json()
    assert body["feedback"] == "Letra de música qualquer"
    assert [f["code"] for f in body["requested_features"]] == ["EXPORT_PDF"]


def test_patch_validates_the_id(build):
    client = logged_client(build)
    assert patch(client, "nao-e-uuid", {"is_spam": False}).status_code == 400
    assert patch(client, UNKNOWN, {"is_spam": False}).status_code == 404


# --------------------------------------------------------------- sentimento

def test_patch_changes_the_sentiment_and_the_panel_reflects_it(build):
    client = logged_client(build)
    response = patch(client, GOOD, {"sentiment": "NEGATIVO"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["sentiment"] == "NEGATIVO"
    assert body["is_spam"] is False  # só o sentimento mudou

    assert ids(client.get("/api/feedbacks?sentiment=NEGATIVO")) == {GOOD}
    assert ids(client.get("/api/feedbacks?sentiment=POSITIVO")) == set()
    counts = Statistics.get_dashboard_statistics()["sentiment_counts"]
    assert counts["NEGATIVO"] == 1 and counts["POSITIVO"] == 0


@pytest.mark.parametrize("sentiment", ["POSITIVO", "NEGATIVO", "INCONCLUSIVO"])
def test_patch_accepts_every_valid_sentiment(build, sentiment):
    client = logged_client(build)
    assert patch(client, GOOD, {"sentiment": sentiment}).get_json()["sentiment"] == sentiment


def test_patch_can_change_sentiment_and_spam_together(build):
    client = logged_client(build)
    body = patch(client, SPAMMY, {"is_spam": False, "sentiment": "POSITIVO"}).get_json()
    assert body["is_spam"] is False and body["sentiment"] == "POSITIVO"
    assert ids(client.get("/api/feedbacks?sentiment=POSITIVO")) == {GOOD, SPAMMY}


def test_changing_only_the_sentiment_keeps_the_spam_flag(build):
    client = logged_client(build)
    body = patch(client, SPAMMY, {"sentiment": "NEGATIVO"}).get_json()
    assert body["sentiment"] == "NEGATIVO" and body["is_spam"] is True
    assert SPAMMY not in ids(client.get("/api/feedbacks"))


def test_sentiment_patch_is_idempotent(build):
    client = logged_client(build)
    first = patch(client, GOOD, {"sentiment": "NEGATIVO"})
    second = patch(client, GOOD, {"sentiment": "NEGATIVO"})
    assert first.status_code == second.status_code == 200
    assert first.get_json() == second.get_json()


def test_sentiment_patch_needs_a_session(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    assert patch(client, GOOD, {"sentiment": "NEGATIVO"}).status_code == 401
    assert db.session.get(Feedback, GOOD).sentiment == "POSITIVO"


def test_sentiment_patch_requires_the_csrf_token(build):
    client = logged_client(build)
    assert patch(client, GOOD, {"sentiment": "NEGATIVO"}, token="errado").status_code == 403
    assert db.session.get(Feedback, GOOD).sentiment == "POSITIVO"


def test_sentiment_patch_needs_a_dashboard_password(build):
    client = build(DASHBOARD_PASSWORD=None)
    response = client.patch(f"/api/feedbacks/{GOOD}", json={"sentiment": "NEGATIVO"})
    assert response.status_code == 403
    assert db.session.get(Feedback, GOOD).sentiment == "POSITIVO"
