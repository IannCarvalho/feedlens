"""Feedbacks que a IA não conseguiu analisar: marcação, filtro, reprocessamento."""
from unittest.mock import patch

import pytest

from app import create_app, db
from app.llm import LLMNotConfiguredError
from app.llm.errors import Attempt, LLMRequestError
from app.models.feedback import Feedback
from app.services.feedback_analyzer import FeedbackAnalyzer

from tests.helpers import CSRF_HEADER, csrf_token, login

PASSWORD = "segredo-do-painel"
ANALYZED = "11111111-1111-4111-8111-111111111111"
PENDING = "22222222-2222-4222-8222-222222222222"
PENDING_2 = "33333333-3333-4333-8333-333333333333"
UNKNOWN = "99999999-9999-4999-8999-999999999999"

GOOD_ANALYSIS = {
    "sentiment": "POSITIVO",
    "is_spam": False,
    "requested_features": [{"code": "EXPORT_PDF", "reason": "quer enviar o relatório"}],
    "analyzed_by": "gemini/gemini-test",
    "failure": None,
}


def failed(kind):
    return {
        "sentiment": "INCONCLUSIVO", "is_spam": False, "requested_features": [],
        "analyzed_by": None, "failure": kind,
    }


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
    db.session.add_all([
        Feedback(id=ANALYZED, feedback_text="App rápido, gostei", sentiment="POSITIVO",
                 analysis_status="analyzed", analyzed_by="gemini/gemini-test"),
        Feedback(id=PENDING, feedback_text="Foi uma experiência incrível",
                 sentiment="INCONCLUSIVO", analysis_status="unanalyzed",
                 analysis_error="timeout"),
        Feedback(id=PENDING_2, feedback_text="O app trava ao abrir a câmera",
                 sentiment="INCONCLUSIVO", analysis_status="unanalyzed",
                 analysis_error="rate_limit"),
    ])
    db.session.commit()


def logged_client(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    assert login(client, PASSWORD).status_code == 200
    return client


def reanalyze(client, feedback_id, token=None):
    headers = {CSRF_HEADER: csrf_token(client) if token is None else token}
    return client.post(f"/api/feedbacks/{feedback_id}/reanalyze", headers=headers)


# ------------------------------------------------------------ analisador

def _fail_with(*kinds):
    return LLMRequestError([Attempt("gemini/x", kind, "detalhe") for kind in kinds])


@pytest.mark.parametrize("kind", ["rate_limit", "timeout", "connection", "auth", "not_found", "empty"])
def test_provider_failure_is_reported_with_its_kind(kind):
    with patch("app.services.feedback_analyzer.complete", side_effect=_fail_with(kind)):
        result = FeedbackAnalyzer().analyze_feedback("texto")
    assert result["failure"] == kind
    assert result["analyzed_by"] is None
    # O formato que o backend espera continua o mesmo.
    assert result["sentiment"] == "INCONCLUSIVO"
    assert result["is_spam"] is False
    assert result["requested_features"] == []


def test_the_last_attempt_decides_the_kind():
    with patch("app.services.feedback_analyzer.complete",
               side_effect=_fail_with("timeout", "rate_limit")):
        assert FeedbackAnalyzer().analyze_feedback("texto")["failure"] == "rate_limit"


def test_an_unusable_reply_counts_as_not_analyzed():
    from app.llm.client import Completion

    def reply(content):
        return Completion(content=content, model_ref="gemini/x", source="server", latency_ms=1)

    for content in ("isto nao e json", '{"sentiment": "POSITIVO", "requested_features": ["x"]}'):
        with patch("app.services.feedback_analyzer.complete", return_value=reply(content)):
            result = FeedbackAnalyzer().analyze_feedback("texto")
        assert result["failure"] == "invalid_response"
        assert result["analyzed_by"] is None


def test_a_good_reply_has_no_failure():
    from app.llm.client import Completion

    reply = Completion(content='{"sentiment": "POSITIVO"}', model_ref="gemini/x",
                       source="server", latency_ms=1)
    with patch("app.services.feedback_analyzer.complete", return_value=reply):
        result = FeedbackAnalyzer().analyze_feedback("texto")
    assert result["failure"] is None and result["analyzed_by"] == "gemini/x"


# ------------------------------------------------------------- ingestão

def post_feedback(client, feedback_id, analysis):
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=analysis):
        return client.post("/api/feedbacks", json={"id": feedback_id, "feedback": "texto novo"})


def test_ingestion_saves_a_failed_analysis_as_unanalyzed(client):
    response = post_feedback(client, ANALYZED, failed("rate_limit"))

    assert response.status_code == 201  # o cliente que enviou não é afetado
    body = response.get_json()
    assert body["analysis_status"] == "unanalyzed"
    assert body["analysis_error"] == "rate_limit"
    assert body["analyzed_by"] is None
    saved = db.session.get(Feedback, ANALYZED)
    assert saved.analysis_status == "unanalyzed" and saved.analysis_error == "rate_limit"
    assert saved.sentiment == "INCONCLUSIVO" and saved.is_spam is False


def test_ingestion_saves_a_good_analysis_as_analyzed(client):
    response = post_feedback(client, ANALYZED, GOOD_ANALYSIS)

    body = response.get_json()
    assert body["analysis_status"] == "analyzed" and body["analysis_error"] is None
    assert body["analyzed_by"] == "gemini/gemini-test"
    assert [f["code"] for f in body["requested_features"]] == ["EXPORT_PDF"]
    saved = db.session.get(Feedback, ANALYZED)
    assert saved.analyzed_by == "gemini/gemini-test"
    assert [f.code for f in saved.requested_features] == ["EXPORT_PDF"]


def test_detail_and_list_expose_the_analysis_fields(client):
    post_feedback(client, PENDING, failed("timeout"))
    detail = client.get(f"/api/feedbacks/{PENDING}").get_json()
    assert detail["analysis_status"] == "unanalyzed" and detail["analysis_error"] == "timeout"
    item = client.get("/api/feedbacks").get_json()["items"][0]
    assert {"analysis_status", "analysis_error", "analyzed_by"} <= set(item)


# --------------------------------------------------------------- listagem

def ids(response):
    return {item["id"] for item in response.get_json()["items"]}


def test_list_filters_only_the_unanalyzed(build):
    client = build()
    assert ids(client.get("/api/feedbacks")) == {ANALYZED, PENDING, PENDING_2}
    only = client.get("/api/feedbacks?analysis=unanalyzed")
    assert ids(only) == {PENDING, PENDING_2}
    assert only.get_json()["analysis"] == "unanalyzed"
    assert client.get("/api/feedbacks").get_json()["analysis"] == "any"


def test_list_analysis_filter_combines_with_the_others(build):
    client = build()
    assert ids(client.get("/api/feedbacks?analysis=unanalyzed&sentiment=POSITIVO")) == set()
    assert ids(client.get("/api/feedbacks?analysis=unanalyzed&sentiment=INCONCLUSIVO")) == {
        PENDING, PENDING_2}


@pytest.mark.parametrize("value", ["analyzed", "todos", "1", ""])
def test_list_rejects_an_unknown_analysis_filter(build, value):
    response = build().get(f"/api/feedbacks?analysis={value}")
    assert response.status_code == 400 and "analysis" in response.get_json()["error"]


def test_stats_report_how_many_are_unanalyzed(build):
    client = build()
    assert client.get("/api/stats").get_json()["unanalyzed"] == 2


def test_stats_without_data_still_report_unanalyzed(client):
    assert client.get("/api/stats").get_json() == {"has_data": False, "unanalyzed": 0}


def test_a_spam_flagged_feedback_is_not_counted_as_pending(build):
    client = build()
    db.session.get(Feedback, PENDING).is_spam = True
    db.session.commit()
    assert client.get("/api/stats").get_json()["unanalyzed"] == 1


# ---------------------------------------------------------- reanálise (API)

def test_reanalyze_needs_a_dashboard_password(build):
    response = build(DASHBOARD_PASSWORD=None).post(f"/api/feedbacks/{PENDING}/reanalyze")
    assert response.status_code == 403
    assert response.get_json()["code"] == "auth_not_configured"


def test_reanalyze_needs_a_session(build):
    client = build(DASHBOARD_PASSWORD=PASSWORD)
    assert reanalyze(client, PENDING).status_code == 401


def test_reanalyze_requires_the_csrf_token(build):
    client = logged_client(build)
    assert reanalyze(client, PENDING, token="errado").status_code == 403
    assert db.session.get(Feedback, PENDING).analysis_status == "unanalyzed"


def test_reanalyze_validates_the_id(build):
    client = logged_client(build)
    assert reanalyze(client, "nao-e-uuid").status_code == 400
    assert reanalyze(client, UNKNOWN).status_code == 404


def test_reanalyze_success_replaces_the_default_with_the_real_analysis(build):
    client = logged_client(build)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=GOOD_ANALYSIS) as spy:
        response = reanalyze(client, PENDING)

    assert response.status_code == 200
    spy.assert_called_once_with("Foi uma experiência incrível")
    body = response.get_json()
    assert body["sentiment"] == "POSITIVO"
    assert body["analysis_status"] == "analyzed" and body["analysis_error"] is None
    assert body["analyzed_by"] == "gemini/gemini-test"
    assert [f["code"] for f in body["requested_features"]] == ["EXPORT_PDF"]
    assert ids(client.get("/api/feedbacks?analysis=unanalyzed")) == {PENDING_2}


def test_reanalyze_failure_keeps_it_unanalyzed_and_updates_the_reason(build):
    client = logged_client(build)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=failed("rate_limit")):
        response = reanalyze(client, PENDING)

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "analysis_unavailable" and body["detail"] == "rate_limit"
    assert body["feedback"]["analysis_status"] == "unanalyzed"
    saved = db.session.get(Feedback, PENDING)
    assert saved.analysis_status == "unanalyzed" and saved.analysis_error == "rate_limit"
    assert saved.sentiment == "INCONCLUSIVO"  # o padrão não sobrescreve nada


def test_reanalyze_failure_does_not_wipe_a_manual_correction(build):
    client = logged_client(build)
    client.patch(f"/api/feedbacks/{PENDING}", json={"sentiment": "POSITIVO"},
                 headers={CSRF_HEADER: csrf_token(client)})
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=failed("timeout")):
        reanalyze(client, PENDING)
    assert db.session.get(Feedback, PENDING).sentiment == "POSITIVO"


def test_reanalyze_without_a_provider_says_so(build):
    client = logged_client(build)
    with patch("app.routes.api.feedback_analyzer.analyze_feedback",
               side_effect=LLMNotConfiguredError()):
        response = reanalyze(client, PENDING)
    assert response.status_code == 503
    assert response.get_json()["code"] == "llm_not_configured"


def test_reanalyze_works_on_an_already_analyzed_feedback(build):
    client = logged_client(build)
    new = {**GOOD_ANALYSIS, "sentiment": "NEGATIVO", "requested_features": []}
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=new):
        body = reanalyze(client, ANALYZED).get_json()
    assert body["sentiment"] == "NEGATIVO" and body["requested_features"] == []


# ------------------------------------------------------------- reanálise (CLI)

@pytest.fixture()
def cli(build):
    client = build()
    return client.application.test_cli_runner()


def run(cli, *args, analysis=None, side_effect=None):
    target = "app.services.feedback_analyzer.FeedbackAnalyzer.analyze_feedback"
    kwargs = {"side_effect": side_effect} if side_effect else {"return_value": analysis}
    with patch(target, **kwargs) as spy:
        result = cli.invoke(args=["reanalyze-pending", *args])
    return result, spy


def test_cli_reanalyzes_every_pending_feedback(cli):
    result, spy = run(cli, analysis=GOOD_ANALYSIS)
    assert result.exit_code == 0, result.output
    assert spy.call_count == 2
    assert "2 reanalisado(s), 0 falha(s); 0 ainda aguardam análise." in result.output
    assert db.session.get(Feedback, PENDING).analysis_status == "analyzed"
    assert db.session.get(Feedback, PENDING_2).analysis_status == "analyzed"
    assert db.session.get(Feedback, ANALYZED).analysis_status == "analyzed"


def test_cli_with_nothing_pending_says_so(cli):
    db.session.query(Feedback).update({"analysis_status": "analyzed"})
    db.session.commit()
    result, spy = run(cli, analysis=GOOD_ANALYSIS)
    assert result.exit_code == 0 and "Nenhum feedback aguardando análise." in result.output
    spy.assert_not_called()


def test_cli_limit_takes_the_oldest_first(cli):
    result, spy = run(cli, "--limit", "1", analysis=GOOD_ANALYSIS)
    assert result.exit_code == 0 and spy.call_count == 1
    assert "1 ainda aguardam análise" in result.output


def test_cli_stops_after_consecutive_failures_instead_of_burning_calls(cli):
    db.session.add_all([
        Feedback(id=f"4444444{i}-4444-4444-8444-444444444444", feedback_text=f"mais {i}",
                 analysis_status="unanalyzed", analysis_error="timeout")
        for i in range(5)
    ])
    db.session.commit()

    result, spy = run(cli, "--stop-after", "2", analysis=failed("rate_limit"))

    assert result.exit_code == 1
    assert spy.call_count == 2
    assert "Parei depois de 2 falhas seguidas (rate_limit)" in result.output


def test_cli_a_success_resets_the_failure_streak(cli):
    outcomes = [failed("timeout"), GOOD_ANALYSIS]
    result, spy = run(cli, "--stop-after", "2", side_effect=outcomes)
    assert spy.call_count == 2
    assert "1 reanalisado(s), 1 falha(s)" in result.output
    assert result.exit_code == 1  # uma falha continua pendente


def test_cli_id_reanalyzes_even_an_analyzed_feedback(cli):
    result, spy = run(cli, "--id", ANALYZED, analysis=GOOD_ANALYSIS)
    assert result.exit_code == 0 and spy.call_count == 1


def test_cli_rejects_a_bad_or_unknown_id(cli):
    result, _ = run(cli, "--id", "nao-e-uuid", analysis=GOOD_ANALYSIS)
    assert result.exit_code != 0 and "UUID" in result.output
    result, _ = run(cli, "--id", UNKNOWN, analysis=GOOD_ANALYSIS)
    assert result.exit_code != 0 and "não encontrado" in result.output


def test_cli_without_a_provider_fails_clearly(cli):
    result, _ = run(cli, side_effect=LLMNotConfiguredError())
    assert result.exit_code != 0 and "Nenhum provedor de LLM configurado" in result.output
