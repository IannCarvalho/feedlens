"""Testes do endpoint GET /api/stats (estatísticas do dashboard Vue).

Mesma proteção dos GET de feedbacks: sessão do painel ou API_READ_TOKEN.
"""
from unittest.mock import patch


def _post(client, feedback_id, text, analysis):
    with patch(
        "app.routes.api.feedback_analyzer.analyze_feedback",
        return_value=analysis,
    ):
        return client.post("/api/feedbacks", json={"id": feedback_id, "feedback": text})


POSITIVE = {"sentiment": "POSITIVO", "is_spam": False, "requested_features": []}
NEGATIVE = {
    "sentiment": "NEGATIVO",
    "is_spam": False,
    "requested_features": [{"code": "MODO_ESCURO", "reason": "pediu modo escuro"}],
}


def test_stats_empty_has_no_data(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert resp.get_json() == {"has_data": False, "unanalyzed": 0}


def test_stats_with_data(client):
    _post(client, "11111111-1111-1111-1111-111111111111", "otimo", POSITIVE)
    _post(client, "22222222-2222-2222-2222-222222222222", "falta modo escuro", NEGATIVE)

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_data"] is True
    assert data["total_feedbacks"] == 2
    assert data["sentiment_counts"]["POSITIVO"] == 1
    assert data["sentiment_counts"]["NEGATIVO"] == 1
    assert abs(data["sentiment_percentages"]["POSITIVO"] - 50.0) < 0.01
    # A feature pedida aparece no ranking.
    codes = {f["code"] for f in data["top_features"]}
    assert "MODO_ESCURO" in codes
    # Os recentes trazem o conteúdo do feedback.
    assert len(data["recent_feedbacks"]) == 2
    assert all("feedback" in item for item in data["recent_feedbacks"])


def test_stats_requires_auth_when_configured(client, app):
    """Com DASHBOARD_PASSWORD definido e sem sessão, /api/stats responde 401."""
    app.config["DASHBOARD_PASSWORD"] = "segredo-de-teste"
    resp = client.get("/api/stats")
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "unauthenticated"
