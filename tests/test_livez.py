"""Testes do endpoint de liveness /livez.

O /livez NÃO pode tocar o banco: é o probe de alta frequência do Render e o
alvo do uptime, e um SELECT 1 a cada ping manteria o compute do Neon acordado.
"""
from unittest.mock import patch


def test_livez_ok(client):
    """/livez responde 200 {"status": "ok"}."""
    resp = client.get('/livez')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok'}


def test_livez_does_not_touch_db(client):
    """/livez não executa nada no banco.

    Patcha db.session.execute para levantar: se o /livez tocasse o banco, o
    teste quebraria. Como ele não toca, segue 200.
    """
    with patch('app.routes.health.db.session.execute',
               side_effect=AssertionError('/livez não deve tocar o banco')):
        resp = client.get('/livez')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok'}


def test_livez_not_behind_auth(client):
    """O probe funciona mesmo com autenticação ligada (como o /health)."""
    client.application.config['DASHBOARD_PASSWORD'] = 'segredo'
    resp = client.get('/livez')
    assert resp.status_code == 200
