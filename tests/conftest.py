"""Shared pytest fixtures and environment setup.

No LLM key is needed: the app boots without one (BYOK) and every test that
exercises analysis mocks the LLM layer. With the dialect-agnostic GUID column
type, the models run on in-memory SQLite, so DB-backed integration tests are
possible.

A suíte tem que dar o MESMO resultado na máquina de quem desenvolve e no CI,
independente do ``.env`` local. O app carrega o ``.env`` no import de
``app.config`` (``load_dotenv()``), e ele não sobrescreve variável já presente
no ambiente — então estas são fixadas AQUI, antes de qualquer import do app.
Sem isso, um ``DASHBOARD_PASSWORD`` no ``.env`` de quem desenvolve derrubava
45 testes que esperam o painel sem login (e o mesmo vale para um token de
leitura, uma chave de LLM de verdade ou origens de ingestão pública).

``setdefault`` e não atribuição direta: quando o CI exporta o valor
(``DATABASE_URL`` do job Postgres, ``OPENAI_API_KEY`` fake), o CI manda.
"""
import os
import socket

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret")
# Desliga o rate-limit nos testes: o limitador é configurado no init_app a
# partir do config (lido do ambiente), então precisa estar definido aqui,
# antes de qualquer import do app, e não após create_app().
os.environ.setdefault("RATELIMIT_ENABLED", "false")
# Um INGEST_API_KEY do .env de quem desenvolve fecharia a ingestão em todos os
# testes; vazio = aberto. Os testes da chave a definem explicitamente.
os.environ.setdefault("INGEST_API_KEY", "")
# Painel sem senha e leitura sem token: é o estado de um clone limpo, que é o
# que os testes assumem. Quem testa login/releitura mexe em app.config.
os.environ.setdefault("DASHBOARD_PASSWORD", "")
os.environ.setdefault("API_READ_TOKEN", "")
# Ingestão pública desligada: com origens configuradas, alguns POSTs deixam de
# ser recusados e os testes de chave/origem passariam a medir outra coisa.
os.environ.setdefault("INGEST_PUBLIC_ORIGINS", "")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
# Chaves de LLM reais no ambiente fariam a cadeia do servidor existir sem mock
# nenhum. Vazio = o estado de clone limpo; os testes que precisam de chave a
# definem em app.config.
for _key_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                 "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.setdefault(_key_var, "")
os.environ.setdefault("LLM_MODELS", "")
# Cofre desligado: os testes que usam cofre definem a chave Fernet em
# app.config. Uma chave do .env ligaria o cofre em toda a suíte.
os.environ.setdefault("FEEDLENS_ENCRYPTION_KEY", "")

import pytest  # noqa: E402

from app import create_app, db  # noqa: E402

# Loopback é liberado para o job de CI que roda a suíte contra o Postgres
# (localhost:5432); todo o resto é falha, não chamada paga.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", ""})


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """Nenhum teste pode abrir conexão com a internet.

    A camada de LLM é sempre mockada (``litellm.completion``/``mock_response``,
    ``settings_api.complete``, ``feedback_analyzer.complete``) e os e-mails são
    mockados no ``smtplib``. Este guarda existe para o caso de um teste NOVO
    esquecer disso: ele falha alto e barato, em vez de gastar a cota de quem
    tem uma chave de verdade no ``.env``. Loopback continua liberado para o
    Postgres do CI.
    """
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def _address_host(address):
        if isinstance(address, tuple) and address:
            return address[0]
        return address

    def _blocked(host):
        raise RuntimeError(
            "um teste tentou abrir conexão com %r: a suíte não faz rede. "
            "Mocke a chamada (litellm.completion / settings_api.complete / "
            "feedback_analyzer.complete) em vez de chamar o provedor de "
            "verdade — isso gastaria a cota de alguém." % (host,))

    def guarded_connect(self, address, *args, **kwargs):
        if _address_host(address) not in _LOOPBACK:
            _blocked(_address_host(address))
        return real_connect(self, address, *args, **kwargs)

    def guarded_create_connection(address, *args, **kwargs):
        if _address_host(address) not in _LOOPBACK:
            _blocked(_address_host(address))
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture(autouse=True)
def _dispose_engines():
    """Fecha o pool de conexões de todo app criado no teste.

    Cada ``create_app()`` abre um engine novo e, num Postgres de verdade, o
    pool segura conexões até o app ser coletado pelo GC. Com ~200 testes isso
    estoura o ``max_connections`` do servidor ("too many clients already"),
    e o CI com Postgres pegou exatamente isso. Em SQLite é inofensivo.
    """
    yield
    for engines in list(getattr(db, "_app_engines", {}).values()):
        for engine in engines.values():
            engine.dispose()


@pytest.fixture()
def app():
    """A Flask app bound to an in-memory SQLite database with tables created."""
    application = create_app()
    application.config.update(TESTING=True)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """A test client for the app."""
    return app.test_client()
