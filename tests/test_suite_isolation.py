"""A suíte não faz rede e não depende do .env de quem desenvolve.

Dois contratos, ambos invisíveis até quebrem:

1. Os testes rodam com a configuração de um clone limpo. O ``.env`` local é
   carregado pelo app (``load_dotenv()``) e, antes desta trava, um
   ``DASHBOARD_PASSWORD`` ali derrubava 45 testes.
2. Nenhum teste abre conexão com a internet. A camada de LLM é sempre mockada;
   o ``conftest`` transforma uma chamada de verdade em falha, para não gastar a
   cota de quem tem chave no ambiente.
"""
import os
import socket

import pytest


def test_the_suite_ignores_the_developers_dotenv(app):
    """O app de teste vê o clone limpo, não o .env da máquina.

    ``DATABASE_URL`` é a exceção de propósito: o job Postgres do CI exporta a
    dele e o ``setdefault`` do conftest preserva. O que se garante é que a
    config veio do ambiente do processo, não do arquivo ``.env``.
    """
    assert not app.config.get("DASHBOARD_PASSWORD")
    assert not app.config.get("API_READ_TOKEN")
    assert not app.config.get("INGEST_API_KEYS")
    assert not app.config.get("INGEST_PUBLIC_ORIGINS")
    assert not app.config.get("FEEDLENS_ENCRYPTION_KEY")
    assert app.config.get("LLM_MODELS") == []
    assert app.config.get("SQLALCHEMY_DATABASE_URI") == os.environ["DATABASE_URL"]


def test_an_outbound_connection_fails_loudly_and_cheaply():
    """IP de documentação (RFC 5737): o guarda barra antes de qualquer envio."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="não faz rede"):
            sock.connect(("203.0.113.9", 443))
    finally:
        sock.close()


def test_create_connection_is_guarded_too():
    with pytest.raises(RuntimeError, match="203.0.113.9"):
        socket.create_connection(("203.0.113.9", 443), timeout=1)


def test_loopback_stays_open_for_the_ci_postgres_job():
    """O job Postgres do CI fala com localhost:5432; o guarda não encosta nele."""
    with pytest.raises(OSError) as refused:
        socket.create_connection(("127.0.0.1", 1), timeout=1)
    assert "não faz rede" not in str(refused.value)
