"""Testes da autenticação do dashboard.

Cobrem o fail-safe (sem senha => aberto), o gating das rotas web quando
há senha, o login do SPA (``GET /auth/session`` + ``POST /login`` em JSON),
a tela de login servida pelo SPA e o logout. A rota da API fica fora do
escopo da auth de dashboard e permanece acessível.
"""
from unittest.mock import patch

import pytest

from app import create_app, db

from tests.helpers import csrf_token, login, post_form

SPA_INDEX = "<!doctype html><title>FeedLens SPA</title><div id=\"app\"></div>"


@pytest.fixture()
def secured_client():
    """Client de um app com DASHBOARD_PASSWORD configurada (auth ligada)."""
    application = create_app()
    application.config.update(TESTING=True, DASHBOARD_PASSWORD="segredo")
    with application.app_context():
        db.create_all()
        yield application.test_client()
        db.session.remove()
        db.drop_all()


def fake_spa_build(application, tmp_path):
    """Pasta estática isolada com um index do SPA (o CI dos testes não builda o front)."""
    application.static_folder = str(tmp_path)
    (tmp_path / "spa").mkdir()
    (tmp_path / "spa" / "index.html").write_text(SPA_INDEX, encoding="utf-8")


# --------------------------------------------------------------- gating web

def test_dashboard_open_when_no_password(client):
    """Sem senha, a URL antiga /dashboard redireciona ao painel SPA (não bloqueia)."""
    resp = client.get("/dashboard")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/")


def test_dashboard_redirects_to_login_when_secured(secured_client):
    """Com senha, uma rota web protegida redireciona para o login."""
    resp = secured_client.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# ------------------------------------------------------ estado da sessão

def test_session_state_when_login_is_disabled(client):
    """Sem senha o painel é aberto: o SPA não mostra login nem botão Sair."""
    body = client.get("/auth/session").get_json()
    assert body["auth_enabled"] is False
    assert body["authenticated"] is True
    assert body["csrf_token"]


def test_session_state_before_and_after_login(secured_client):
    before = secured_client.get("/auth/session")
    assert before.headers["Cache-Control"] == "no-store"
    assert before.get_json()["auth_enabled"] is True
    assert before.get_json()["authenticated"] is False

    assert login(secured_client, "segredo").status_code == 200
    assert secured_client.get("/auth/session").get_json()["authenticated"] is True


def test_session_token_is_stable_within_a_session(secured_client):
    assert csrf_token(secured_client) == csrf_token(secured_client)


def test_session_reports_no_analysis_without_any_key(app):
    """Sem nenhuma chave (ambiente ou cofre): análise não configurada.

    Zera as chaves no config em vez de confiar no ambiente: o job Postgres do
    CI exporta OPENAI_API_KEY, então o estado "clone limpo" tem de ser forçado
    aqui para o teste valer em qualquer ambiente.
    """
    app.config.update(
        OPENAI_API_KEY=None,
        ANTHROPIC_API_KEY=None,
        GEMINI_API_KEY=None,
        GROQ_API_KEY=None,
        OPENROUTER_API_KEY=None,
        LLM_MODELS=[],
    )
    body = app.test_client().get("/auth/session").get_json()
    assert body["analysis_configured"] is False


def test_session_reports_analysis_configured_with_env_key(app):
    """Uma chave de provedor no ambiente já faz o flag virar True."""
    app.config.update(
        OPENAI_API_KEY="sk-fake-para-o-flag",
        ANTHROPIC_API_KEY=None,
        GEMINI_API_KEY=None,
        GROQ_API_KEY=None,
        OPENROUTER_API_KEY=None,
        LLM_MODELS=[],
    )
    body = app.test_client().get("/auth/session").get_json()
    assert body["analysis_configured"] is True


def test_panel_session_routes_are_not_part_of_the_public_cors_api(secured_client):
    """/auth/session e /login são internos ao painel: sem CORS, ao contrário de /api/*."""
    origin = {"Origin": "https://evil.example.com"}
    assert "Access-Control-Allow-Origin" not in secured_client.get(
        "/auth/session", headers=origin).headers
    assert "Access-Control-Allow-Origin" not in login(
        secured_client, "errada", headers=origin).headers
    # Controle: a API pública tem CORS, então o teste acima não é vazio.
    assert "Access-Control-Allow-Origin" in secured_client.get(
        "/api/feedbacks", headers=origin).headers


# ------------------------------------------------------------ tela de login

def test_login_page_is_served_by_the_spa(secured_client, tmp_path):
    """Anônimo com senha configurada: GET /login entrega o SPA, sem exigir sessão."""
    fake_spa_build(secured_client.application, tmp_path)
    resp = secured_client.get("/login?next=/providers")
    assert resp.status_code == 200
    assert b"FeedLens SPA" in resp.data


def test_login_page_explains_a_missing_build(secured_client, tmp_path):
    secured_client.application.static_folder = str(tmp_path)  # sem spa/index.html
    resp = secured_client.get("/login")
    assert resp.status_code == 503
    assert b"fetch_panel" in resp.data


def test_login_page_redirects_when_already_authenticated(secured_client):
    login(secured_client, "segredo")
    resp = secured_client.get("/login?next=/providers")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/providers"
    # O next continua filtrado aqui também.
    assert secured_client.get("/login?next=//evil.example.com").headers["Location"] == "/"


def test_login_page_redirects_when_login_is_disabled(client):
    resp = client.get("/login")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


# ------------------------------------------------------------ POST /login

def test_login_with_correct_password_grants_access(secured_client):
    """Senha correta autentica e devolve para onde o SPA deve ir."""
    resp = login(secured_client, "segredo")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "next": "/"}
    assert resp.headers["Cache-Control"] == "no-store"
    # Autenticado, /dashboard deixa de bloquear e redireciona ao painel SPA.
    assert secured_client.get("/dashboard").status_code == 301


def test_login_with_wrong_password_is_rejected(secured_client):
    """Senha errada não autentica e a mensagem diz o que fazer."""
    resp = login(secured_client, "errada")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["code"] == "invalid_password"
    assert "Senha incorreta" in body["error"]
    assert "tente de novo" in body["error"]
    # Ainda sem acesso.
    assert secured_client.get("/dashboard").status_code == 302


@pytest.mark.parametrize("body", [["segredo"], "segredo", {"password": 123}, {}])
def test_login_with_a_malformed_body_is_a_wrong_password_not_a_500(secured_client, body):
    headers = {"X-CSRF-Token": csrf_token(secured_client)}
    resp = secured_client.post("/login", json=body, headers=headers)
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "invalid_password"


def test_form_encoded_login_no_longer_authenticates(secured_client):
    """O login agora é JSON: o formulário antigo não autentica (nem quebra)."""
    resp = post_form(secured_client, "/login", {"password": "segredo"})
    assert resp.status_code == 401
    assert secured_client.get("/dashboard").status_code == 302


def test_login_when_disabled_just_points_to_the_panel(client):
    resp = client.post("/login", json={"password": "qualquer"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "next": "/"}


def test_login_rejects_open_redirect(secured_client):
    """O parâmetro next só aceita caminhos locais (anti open-redirect)."""
    resp = login(secured_client, "segredo", next_url="https://evil.example.com")
    assert resp.status_code == 200
    assert resp.get_json()["next"] == "/"


# ------------------------------------------------------------------ logout

def test_logout_clears_session(secured_client):
    """Logout encerra a sessão e volta a bloquear o dashboard."""
    login(secured_client, "segredo")
    assert secured_client.get("/dashboard").status_code == 301  # logado: redireciona ao SPA

    post_form(secured_client, "/logout")
    assert secured_client.get("/dashboard").status_code == 302  # deslogado: vai ao login


def test_logout_get_also_logs_out(secured_client):
    """GET /logout (URL na barra) desloga e redireciona, sem tela de 400."""
    login(secured_client, "segredo")
    assert secured_client.get("/dashboard").status_code == 301  # logado

    resp = secured_client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    # Deslogou: o dashboard volta a bloquear (redireciona ao login).
    assert secured_client.get("/dashboard").status_code == 302


def test_logout_without_csrf_still_logs_out(secured_client):
    """POST /logout sem token (sessão expirada/servidor reiniciado) nunca
    mostra 400: desloga mesmo assim e redireciona."""
    login(secured_client, "segredo")
    assert secured_client.get("/dashboard").status_code == 301  # logado

    resp = secured_client.post("/logout")  # sem csrf_token
    assert resp.status_code == 302
    assert secured_client.get("/dashboard").status_code == 302


def test_api_route_not_gated_by_dashboard_auth(secured_client):
    """A API não é protegida pela auth do dashboard (contrato próprio)."""
    with patch("app.routes.api.feedback_analyzer"):
        # Request sem corpo válido => 400 da validação, NÃO 302 de login.
        resp = secured_client.post("/api/feedbacks", json={})
    assert resp.status_code != 302
