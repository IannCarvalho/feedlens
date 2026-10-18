"""Visão geral: feedbacks que a IA não analisou ficam fora dos números e gráficos."""
import pytest

from app import create_app, db
from app.models.feedback import Feedback, RequestedFeature
from app.services.statistics_service import Statistics

GOOD = "11111111-1111-4111-8111-111111111111"
BAD = "22222222-2222-4222-8222-222222222222"
PENDING = "33333333-3333-4333-8333-333333333333"
PENDING_2 = "44444444-4444-4444-8444-444444444444"
SPAM = "55555555-5555-4555-8555-555555555555"


@pytest.fixture()
def build():
    contexts = []

    def _build(*, seed_rows=True):
        application = create_app()
        application.config.update(TESTING=True)
        ctx = application.app_context()
        ctx.push()
        db.create_all()
        contexts.append(ctx)
        if seed_rows:
            seed()
        return application.test_client()

    yield _build
    for ctx in contexts:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def seed():
    good = Feedback(id=GOOD, feedback_text="App rápido", sentiment="POSITIVO",
                    analysis_status="analyzed")
    bad = Feedback(id=BAD, feedback_text="Trava sempre", sentiment="NEGATIVO",
                   analysis_status="analyzed")
    pending = Feedback(id=PENDING, feedback_text="Foi uma experiência incrível",
                       sentiment="INCONCLUSIVO", analysis_status="unanalyzed",
                       analysis_error="rate_limit")
    pending_2 = Feedback(id=PENDING_2, feedback_text="O app trava na câmera",
                         sentiment="INCONCLUSIVO", analysis_status="unanalyzed",
                         analysis_error="timeout")
    spam = Feedback(id=SPAM, feedback_text="Compre já", sentiment="NEGATIVO",
                    is_spam=True, analysis_status="analyzed")
    db.session.add_all([good, bad, pending, pending_2, spam])
    db.session.add_all([
        RequestedFeature(code="EXPORT_PDF", reason="r", feedback=good),
        RequestedFeature(code="DARK_MODE", reason="r", feedback=bad),
        # Funcionalidade de um não analisado (ex.: gravada por reanálise parcial):
        # também não pode entrar no ranking.
        RequestedFeature(code="FANTASMA", reason="r", feedback=pending),
    ])
    db.session.commit()


def test_dashboard_numbers_ignore_the_unanalyzed(build):
    build()
    stats = Statistics.get_dashboard_statistics()

    assert stats["total_feedbacks"] == 2
    assert stats["sentiment_counts"] == {"POSITIVO": 1, "NEGATIVO": 1, "INCONCLUSIVO": 0}
    assert stats["sentiment_percentages"]["POSITIVO"] == 50.0
    assert stats["sentiment_percentages"]["INCONCLUSIVO"] == 0.0


def test_dashboard_feature_ranking_ignores_the_unanalyzed(build):
    build()
    ranking = Statistics.get_dashboard_statistics()["top_features"]
    assert {item["code"] for item in ranking} == {"EXPORT_PDF", "DARK_MODE"}
    assert all(item["percentage"] == 50.0 for item in ranking)


def test_api_stats_keeps_the_unanalyzed_visible_in_the_recent_list(build):
    client = build()
    body = client.get("/api/stats").get_json()

    assert body["total_feedbacks"] == 2
    assert body["unanalyzed"] == 2
    recent = {item["id"]: item for item in body["recent_feedbacks"]}
    assert {GOOD, BAD, PENDING, PENDING_2} == set(recent)
    assert recent[PENDING]["analysis_status"] == "unanalyzed"
    assert recent[GOOD]["analysis_status"] == "analyzed"


def test_the_listing_still_shows_the_unanalyzed(build):
    client = build()
    ids = {item["id"] for item in client.get("/api/feedbacks").get_json()["items"]}
    assert ids == {GOOD, BAD, PENDING, PENDING_2}


def test_only_unanalyzed_feedbacks_means_no_dashboard_data_but_a_pending_count(build):
    client = build(seed_rows=False)
    db.session.add(Feedback(id=PENDING, feedback_text="x", sentiment="INCONCLUSIVO",
                            analysis_status="unanalyzed", analysis_error="rate_limit"))
    db.session.commit()

    assert Statistics.get_dashboard_statistics() == {"has_data": False}
    assert client.get("/api/stats").get_json() == {"has_data": False, "unanalyzed": 1}


def test_a_reanalyzed_feedback_enters_the_numbers(build):
    client = build()
    pending = db.session.get(Feedback, PENDING)
    pending.analysis_status = "analyzed"
    pending.sentiment = "POSITIVO"
    db.session.commit()

    body = client.get("/api/stats").get_json()
    assert body["total_feedbacks"] == 3
    assert body["sentiment_counts"]["POSITIVO"] == 2
    assert body["unanalyzed"] == 1
    assert {item["code"] for item in body["top_features"]} == {"EXPORT_PDF", "DARK_MODE", "FANTASMA"}


def test_a_manual_sentiment_correction_does_not_make_it_count_as_analyzed(build):
    """Corrigir o sentimento à mão não troca o estado: ele segue pendente."""
    build()
    db.session.get(Feedback, PENDING).sentiment = "POSITIVO"
    db.session.commit()
    assert Statistics.get_dashboard_statistics()["sentiment_counts"]["POSITIVO"] == 1


def test_other_callers_keep_the_old_behaviour_unless_they_ask(build):
    build()
    # Sem o argumento, nada muda: o pendente ainda conta.
    assert Statistics.get_total_feedbacks() == 4
    assert Statistics.get_sentiment_counts()["INCONCLUSIVO"] == 2
    assert Statistics.get_total_feedbacks(analyzed_only=True) == 2
    assert Statistics.get_sentiment_counts(analyzed_only=True)["INCONCLUSIVO"] == 0
    codes = {f["code"] for f in Statistics.get_top_features()}
    assert "FANTASMA" in codes
    assert "FANTASMA" not in {f["code"] for f in Statistics.get_top_features(analyzed_only=True)}
