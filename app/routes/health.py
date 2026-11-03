"""Endpoints de health check para orquestradores (Docker, Render, etc.).

Ficam fora do prefixo /api e sem autenticação, pois precisam responder a probes
antes de qualquer login.

- ``/health`` (readiness): verifica a conectividade com o banco — 200 quando o
  ``SELECT 1`` responde, 503 quando a conexão falha.
- ``/livez`` (liveness): só confirma que o processo está de pé, SEM tocar o
  banco. É o probe do Render (healthCheckPath) e o alvo do pinger de uptime:
  o Render bate nele a cada poucos segundos e, se ele fizesse ``SELECT 1``,
  manteria o compute do Neon acordado 24/7 e esgotaria as 100 CU-h/mês do free.
"""
from flask import Blueprint, jsonify
from sqlalchemy import text

from app import db

health = Blueprint('health', __name__)


@health.route('/health', methods=['GET'])
def healthcheck():
    """Readiness probe: valida o app e a conexão com o banco."""
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify({'status': 'ok', 'database': 'ok'}), 200
    except Exception:
        db.session.rollback()
        return jsonify({'status': 'degraded', 'database': 'error'}), 503


@health.route('/livez', methods=['GET'])
def livez():
    """Liveness probe: o processo está de pé. NÃO toca o banco.

    Sem auth e sem rate-limit (como o /health). Usado como healthCheckPath do
    Render e alvo do uptime: não acorda o compute do Neon, preservando CU-h.
    """
    return jsonify({'status': 'ok'}), 200
