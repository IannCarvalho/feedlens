"""Testes do endurecimento de segurança: IP do cliente, headers, erros JSON.

Cada teste trava uma regressão concreta:

* ``key_func`` lê o header confiável (e cai em remote_addr sem ele / com IP
  inválido), avisando UMA vez por processo que o header não chegou;
* todo response carrega os headers de segurança, com HSTS só em HTTPS, e a
  rota pode sobrescrever um header;
* 404/405/500 e exceção não tratada viram JSON em /api e HTML fora;
* senha curta do painel só AVISA em produção (não derruba o boot).
"""
import app.client_ip as client_ip
from unittest.mock import patch

import pytest

from app import MIN_DASHBOARD_PASSWORD_LENGTH, create_app, validate_config
from app.client_ip import get_client_ip
from app.config import Config
from app.security_headers import _STATIC_HEADERS


@pytest.fixture(autouse=True)
def _reset_warn_flag():
    """O aviso de header ausente é uma-vez-por-processo: reseta entre testes."""
    client_ip._missing_header_warned = False
    yield
    client_ip._missing_header_warned = False


# --- key_func (IP do cliente) -------------------------------------------------

def _app_with_header(header_name):
    application = create_app()
    application.config.update(TESTING=True, TRUSTED_CLIENT_IP_HEADER=header_name)
    return application


def test_key_func_without_header_falls_back_to_remote_addr(app):
    """Sem TRUSTED_CLIENT_IP_HEADER, usa o remote_addr (comportamento atual)."""
    app.config['TRUSTED_CLIENT_IP_HEADER'] = ''
    with app.test_request_context('/', environ_base={'REMOTE_ADDR': '203.0.113.9'}):
        assert get_client_ip() == '203.0.113.9'


def test_key_func_uses_configured_header_when_valid():
    application = _app_with_header('CF-Connecting-IP')
    with application.test_request_context(
        '/',
        headers={'CF-Connecting-IP': '198.51.100.7'},
        environ_base={'REMOTE_ADDR': '127.0.0.1'},
    ):
        assert get_client_ip() == '198.51.100.7'


def test_key_func_accepts_ipv6_in_header():
    application = _app_with_header('CF-Connecting-IP')
    with application.test_request_context(
        '/', headers={'CF-Connecting-IP': '2001:db8::1'},
        environ_base={'REMOTE_ADDR': '127.0.0.1'},
    ):
        assert get_client_ip() == '2001:db8::1'


def test_key_func_invalid_ip_falls_back_and_warns():
    application = _app_with_header('CF-Connecting-IP')
    with application.test_request_context(
        '/', headers={'CF-Connecting-IP': 'nao-e-ip'},
        environ_base={'REMOTE_ADDR': '127.0.0.1'},
    ):
        with patch.object(client_ip.logger, 'warning') as warn:
            assert get_client_ip() == '127.0.0.1'
    warn.assert_called_once()
    # Avisa só o NOME do header, nunca o valor: o nome é o arg de formatação.
    logged = ' '.join(str(a) for a in warn.call_args.args)
    assert 'CF-Connecting-IP' in logged
    assert 'nao-e-ip' not in logged


def test_key_func_missing_header_falls_back_and_warns():
    application = _app_with_header('CF-Connecting-IP')
    with application.test_request_context(
        '/', environ_base={'REMOTE_ADDR': '127.0.0.1'},
    ):
        with patch.object(client_ip.logger, 'warning') as warn:
            assert get_client_ip() == '127.0.0.1'
    warn.assert_called_once()
    assert 'CF-Connecting-IP' in ' '.join(str(a) for a in warn.call_args.args)


def test_key_func_warns_only_once_per_process():
    application = _app_with_header('CF-Connecting-IP')
    with patch.object(client_ip.logger, 'warning') as warn:
        for _ in range(3):
            with application.test_request_context(
                '/', environ_base={'REMOTE_ADDR': '127.0.0.1'},
            ):
                get_client_ip()
    assert warn.call_count == 1


# --- headers de segurança -----------------------------------------------------

def test_all_security_headers_present_on_response(client):
    resp = client.get('/health')
    for name, value in _STATIC_HEADERS.items():
        assert resp.headers.get(name) == value, name


def test_hsts_only_on_https(client):
    """HSTS não vem em HTTP puro, mas vem quando a requisição é HTTPS."""
    http = client.get('/health')
    assert 'Strict-Transport-Security' not in http.headers

    https = client.get('/health', base_url='https://localhost')
    assert https.headers.get('Strict-Transport-Security') == \
        'max-age=31536000; includeSubDomains'


def test_hsts_present_with_forwarded_proto_https(client):
    resp = client.get('/health', headers={'X-Forwarded-Proto': 'https'})
    assert resp.headers.get('Strict-Transport-Security') == \
        'max-age=31536000; includeSubDomains'


def test_security_headers_can_be_disabled(client):
    client.application.config['SECURITY_HEADERS_ENABLED'] = False
    resp = client.get('/health')
    assert 'X-Content-Type-Options' not in resp.headers
    assert 'Content-Security-Policy' not in resp.headers


def test_route_header_is_not_overwritten(app, client):
    """Um header já definido pela rota tem a palavra final (setdefault)."""
    @app.route('/__own_ct')
    def _own_ct():
        from flask import make_response
        resp = make_response('x')
        resp.headers['X-Content-Type-Options'] = 'custom-value'
        return resp

    resp = client.get('/__own_ct')
    assert resp.headers['X-Content-Type-Options'] == 'custom-value'


# --- handlers de erro JSON em /api -------------------------------------------

def test_api_404_is_json(client):
    resp = client.get('/api/rota-inexistente')
    assert resp.status_code == 404
    assert resp.is_json
    assert resp.get_json()['code'] == 'not_found'


def test_api_405_is_json(client):
    """DELETE num endpoint que só aceita GET/POST: 405 JSON."""
    resp = client.delete('/api/feedbacks')
    assert resp.status_code == 405
    assert resp.is_json
    assert resp.get_json()['code'] == 'method_not_allowed'


def test_api_500_is_json_without_leaking(client):
    """Erro não tratado numa rota /api vira 500 JSON sem vazar a mensagem."""
    with patch('app.routes.api.Statistics.get_dashboard_statistics',
               side_effect=RuntimeError('detalhe interno secreto')):
        resp = client.get('/api/stats')
    assert resp.status_code == 500
    assert resp.is_json
    body = resp.get_json()
    assert body['code'] == 'internal_error'
    assert 'secreto' not in resp.get_data(as_text=True)


def test_web_404_is_not_json(client, app, tmp_path):
    """Fora de /api o 404 mantém o comportamento web (não JSON)."""
    app.static_folder = str(tmp_path)
    (tmp_path / 'spa').mkdir()
    (tmp_path / 'spa' / 'index.html').write_text(
        '<!doctype html><title>SPA</title>', encoding='utf-8')
    # Caminho reservado inexistente -> 404 HTML do Flask, não JSON.
    resp = client.get('/auth/nao-existe')
    assert resp.status_code == 404
    assert not resp.is_json


def test_unhandled_exception_outside_api_is_not_json(app):
    """Fora de /api, o handler de Exception re-levanta (não devolve JSON).

    Prova que o 500 JSON é EXCLUSIVO de /api: numa rota web o handler não
    intercepta com JSON, deixando o Flask produzir o 500 HTML default.
    """
    @app.route('/__boom')
    def _boom():
        raise RuntimeError('falha web')

    # testing=False + sem propagação: o Flask captura e serve o 500 HTML.
    app.testing = False
    app.config['PROPAGATE_EXCEPTIONS'] = False
    resp = app.test_client().get('/__boom')
    assert resp.status_code == 500
    assert not resp.is_json


# --- aviso de senha fraca do painel ------------------------------------------

def _prod_config(password):
    return type('ProdConfig', (Config,), {
        'FLASK_ENV': 'production',
        'SECRET_KEY': 'k' * 48,
        'DASHBOARD_PASSWORD': password,
        'OPENAI_API_KEY': 'test-key',
        'EMAIL_HOST': 'smtp.example.com',
        'EMAIL_USER': 'user',
        'EMAIL_PASSWORD': 'pass',
        'EMAIL_RECIPIENTS': ['team@example.com'],
    })


def test_weak_dashboard_password_warns_in_production():
    import app as app_pkg
    with patch.object(app_pkg.logger, 'warning') as warn:
        validate_config(_prod_config('curta'))
    messages = [' '.join(str(a) for a in c.args) for c in warn.call_args_list]
    assert any('DASHBOARD_PASSWORD' in m and str(MIN_DASHBOARD_PASSWORD_LENGTH) in m
               for m in messages)


def test_strong_dashboard_password_does_not_warn():
    import app as app_pkg
    with patch.object(app_pkg.logger, 'warning') as warn:
        validate_config(_prod_config('senha-bem-longa-12345'))
    messages = [' '.join(str(a) for a in c.args) for c in warn.call_args_list]
    assert not any('use uma senha mais' in m for m in messages)
