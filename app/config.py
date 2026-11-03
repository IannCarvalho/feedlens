import os
from datetime import timedelta
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get a boolean value from environment variable"""
    value = os.environ.get(key)
    if value is None:
        return default
    return value.lower() == 'true'


def get_env_int(key: str, default: int) -> int:
    """Get an integer value from environment variable"""
    value = os.environ.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_list(key: str, default: List[str] = None, separator: str = ',') -> List[str]:
    """Get a list of values from environment variable"""
    value = os.environ.get(key)
    if not value:
        return default or []
    return [item.strip() for item in value.split(separator) if item.strip()]


def normalize_database_url(url: str) -> str:
    """Troca o esquema ``postgres://`` por ``postgresql://``.

    Alguns provedores ainda entregam a string no esquema antigo, que o
    SQLAlchemy 1.4+ recusa. Qualquer outro esquema passa sem mudança.
    """
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url


# Reciclagem das conexões do pool (s). O Neon free suspende o compute após 5 min
# sem uso e derruba as conexões abertas; reciclar nesse prazo evita entregar ao
# request uma conexão já morta.
POSTGRES_POOL_RECYCLE_SECONDS = 300


def database_engine_options(url: str) -> dict:
    """Opções do engine do SQLAlchemy conforme o banco.

    Postgres gerenciado (Neon, Render, compose) ganha ``pool_pre_ping``, que
    testa a conexão antes de usá-la e troca a que caiu, e ``pool_recycle``.
    O SQLite fica com os padrões, como antes.
    """
    if not url.startswith('postgresql'):
        return {}
    return {'pool_pre_ping': True, 'pool_recycle': POSTGRES_POOL_RECYCLE_SECONDS}


def migration_database_url(app_url: str, unpooled_url: Optional[str]) -> str:
    """URL usada pelas migrations (Alembic).

    No Neon o app usa a conexão pooled (``-pooler``, PgBouncer em modo
    transação) e as migrations devem usar a direta, que o Neon entrega em
    ``DATABASE_URL_UNPOOLED``. A direta só vale quando o próprio app está num
    Postgres: assim um ``DATABASE_URL_UNPOOLED`` esquecido no ambiente nunca
    desvia a migração de um SQLite local (ou dos testes) para outro banco.
    """
    if unpooled_url and app_url.startswith('postgresql'):
        return normalize_database_url(unpooled_url)
    return app_url


class Config:
    """Application configuration

    Attributes:
        SECRET_KEY: Flask secret key for session management
        FLASK_ENV: Current environment (development/production)
        SQLALCHEMY_DATABASE_URI: Database connection string
        SQLALCHEMY_TRACK_MODIFICATIONS: Whether to track model modifications
        OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / GROQ_API_KEY /
            OPENROUTER_API_KEY: optional provider keys (BYOK)
        LLM_MODELS: ordered 'provider/model' chain (primary first, then fallbacks)
        EMAIL_*: Email server configuration
        EMAIL_RECIPIENTS: List of email addresses for the monthly summary
    """

    # Flask configuration
    SECRET_KEY: str = os.environ.get('SECRET_KEY', 'default-secret-key')
    FLASK_ENV: str = os.environ.get('FLASK_ENV', 'development')

    # Senha de acesso ao dashboard (rotas web). Se vazia, o dashboard
    # fica aberto (comportamento histórico, útil em desenvolvimento).
    DASHBOARD_PASSWORD: Optional[str] = os.environ.get('DASHBOARD_PASSWORD')
    # Token de leitura da API (GET /api/feedbacks...): clientes servidor-a-
    # servidor enviam "Authorization: Bearer <token>". Com DASHBOARD_PASSWORD
    # ou este token definidos, a leitura exige credencial; sem nenhum dos
    # dois a API continua aberta (dev). Frontend da mesma origem usa a sessão.
    API_READ_TOKEN: Optional[str] = os.environ.get('API_READ_TOKEN')
    # Chave de ingestão (POST /api/feedbacks): quem envia feedbacks manda
    # "X-API-Key: <chave>". Aceita várias separadas por vírgula (rotação sem
    # downtime). Definida, TODO POST exige uma chave válida; vazia, a ingestão
    # fica aberta (comportamento antigo), e com chave de LLM no servidor isso
    # deixa qualquer um gastar a cota do operador.
    INGEST_API_KEYS: List[str] = get_env_list('INGEST_API_KEY')
    # Exceção opcional para demos públicas: aceita sem chave de ingestão quem
    # trouxer a PRÓPRIA chave de LLM (headers X-LLM-*), já que paga a chamada.
    # Continua gravando no seu banco, então só ligue se aceitar esse volume.
    INGEST_ALLOW_BYOK_WITHOUT_KEY: bool = get_env_bool('INGEST_ALLOW_BYOK_WITHOUT_KEY', False)
    # Ingestão pública opt-in (desligada por padrão): origens EXATAS, separadas
    # por vírgula (ex.: https://form.exemplo.com), de onde um formulário público
    # pode enviar SEM chave de ingestão. ATENÇÃO: o header Origin é forjável
    # fora de um navegador, então isto NÃO autentica ninguém — só impede o uso a
    # partir de páginas de outros sites. A barreira real contra gasto de LLM é o
    # rate limit por IP + o teto diário abaixo. '*' é recusado no boot.
    INGEST_PUBLIC_ORIGINS: List[str] = get_env_list('INGEST_PUBLIC_ORIGINS')
    # Rate limit por IP aplicado só ao caminho público (sem chave, via Origin).
    RATELIMIT_INGEST_PUBLIC: str = os.environ.get(
        'RATELIMIT_INGEST_PUBLIC', '5 per minute')
    # Teto GLOBAL de ingestões públicas (sem chave) nas últimas 24h, contado no
    # banco por Feedback.created_at. Estourado, o endpoint responde 429 antes de
    # ler o corpo ou chamar o LLM. Gasto máximo ≈ este teto × custo por análise.
    INGEST_PUBLIC_DAILY_CAP: int = get_env_int('INGEST_PUBLIC_DAILY_CAP', 500)

    # Cookie de sessão do dashboard. HttpOnly impede leitura por JS e
    # SameSite=Lax corta CSRF cross-site nos POSTs do painel. Secure (só
    # HTTPS) liga por padrão em produção; desligue apenas em HTTP local.
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
    SESSION_COOKIE_SECURE: bool = get_env_bool(
        'SESSION_COOKIE_SECURE', FLASK_ENV == 'production')
    # Validade do login no painel. Sem isso, um cookie assinado vale 31 dias.
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(
        hours=get_env_int('SESSION_LIFETIME_HOURS', 12))
    # Tentativas de login por IP (anti força-bruta da senha compartilhada).
    RATELIMIT_LOGIN: str = os.environ.get('RATELIMIT_LOGIN', '10 per minute')

    # Limites de entrada: corpo da requisição (bytes, o Flask responde 413) e
    # tamanho do texto de um feedback (cada caractere vira token pago na LLM).
    MAX_CONTENT_LENGTH: int = get_env_int('MAX_CONTENT_LENGTH', 1024 * 1024)
    MAX_FEEDBACK_CHARS: int = get_env_int('MAX_FEEDBACK_CHARS', 5000)

    # SQLAlchemy configuration. Sem DATABASE_URL, cai no SQLite local. Em
    # produção a string vem só do ambiente (segredo no painel do host), nunca
    # do código; veja a seção de deploy do README.
    SQLALCHEMY_DATABASE_URI: str = normalize_database_url(os.environ.get(
        'DATABASE_URL', 'sqlite:///feedlens.db'))
    SQLALCHEMY_ENGINE_OPTIONS: dict = database_engine_options(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # LLM: BYOK multi-provedor via LiteLLM (app/llm). Nenhuma chave é
    # obrigatória: sem chave no servidor, a análise depende do BYOK enviado
    # na requisição (headers X-LLM-*).
    OPENAI_API_KEY: Optional[str] = os.environ.get('OPENAI_API_KEY')
    ANTHROPIC_API_KEY: Optional[str] = os.environ.get('ANTHROPIC_API_KEY')
    GEMINI_API_KEY: Optional[str] = os.environ.get('GEMINI_API_KEY')
    # Groq e OpenRouter têm camada gratuita (com limites): bons como reserva.
    GROQ_API_KEY: Optional[str] = os.environ.get('GROQ_API_KEY')
    OPENROUTER_API_KEY: Optional[str] = os.environ.get('OPENROUTER_API_KEY')
    # Ordem de uso 'provedor/modelo' (principal primeiro, depois reservas),
    # ex.: anthropic/claude-haiku-4-5,openai/gpt-4o-mini. Vazio = todo
    # provedor com chave, no modelo padrão de app/llm/providers.py.
    LLM_MODELS: List[str] = get_env_list('LLM_MODELS')
    # Compatibilidade: modelo da OpenAI quando LLM_MODELS não é definido.
    OPENAI_MODEL: str = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
    # Timeout (s) e retries POR provedor da cadeia (os nomes OPENAI_* antigos
    # continuam aceitos). Evita que uma chamada travada segure o request.
    LLM_TIMEOUT: int = get_env_int('LLM_TIMEOUT', get_env_int('OPENAI_TIMEOUT', 30))
    LLM_MAX_RETRIES: int = get_env_int(
        'LLM_MAX_RETRIES', get_env_int('OPENAI_MAX_RETRIES', 2))
    # Aceita BYOK por requisição (X-LLM-Provider / X-LLM-Api-Key / X-LLM-Model).
    BYOK_REQUEST_KEYS_ENABLED: bool = get_env_bool('BYOK_REQUEST_KEYS_ENABLED', True)
    # Cofre de chaves (tela/API de provedores). Chave Fernet que cifra as
    # chaves salvas no banco; use VÁRIAS separadas por vírgula para rotacionar
    # (a primeira cifra, todas decifram). Vazia = cofre desligado.
    FEEDLENS_ENCRYPTION_KEY: str = os.environ.get('FEEDLENS_ENCRYPTION_KEY', '')
    # Teto de testes de chave (cada um é uma chamada paga ao provedor).
    RATELIMIT_SETTINGS_TEST: str = os.environ.get(
        'RATELIMIT_SETTINGS_TEST', '10 per minute')
    # Teto da reanálise EM LOTE pelo painel (POST /api/feedbacks/reanalyze-
    # pending). Cada feedback do lote é UMA chamada paga ao provedor, então o
    # limite protege a cota de quem opera o painel.
    RATELIMIT_REANALYZE: str = os.environ.get(
        'RATELIMIT_REANALYZE', '6 per minute')

    # Email configuration
    EMAIL_HOST: Optional[str] = os.environ.get('EMAIL_HOST')
    EMAIL_PORT: int = get_env_int('EMAIL_PORT', 587)
    EMAIL_USER: Optional[str] = os.environ.get('EMAIL_USER')
    EMAIL_PASSWORD: Optional[str] = os.environ.get('EMAIL_PASSWORD')
    EMAIL_USE_TLS: bool = get_env_bool('EMAIL_USE_TLS', True)
    # Remetente do e-mail. Vazio = EMAIL_USER (comportamento antigo, que serve
    # ao Gmail). Relays como o Brevo usam um login técnico (xxx@smtp-brevo.com)
    # que não é um remetente válido: ali o From é um endereço verificado.
    EMAIL_FROM: Optional[str] = os.environ.get('EMAIL_FROM') or None

    # Email recipients for the monthly summary
    EMAIL_RECIPIENTS: List[str] = get_env_list('EMAIL_RECIPIENTS')
    # Timeout (s) da conexão SMTP: sem ele, um servidor mudo trava o job.
    EMAIL_TIMEOUT: int = get_env_int('EMAIL_TIMEOUT', 30)
    # URL pública do painel, usada no link do e-mail (vazia = e-mail sem link).
    APP_BASE_URL: str = os.environ.get('APP_BASE_URL', '').rstrip('/')

    # Agendador do resumo mensal. Opt-in porque deve existir UM por deploy:
    # cada processo que o iniciasse mandaria o e-mail de novo (app/scheduling.py).
    SCHEDULER_ENABLED: bool = get_env_bool('SCHEDULER_ENABLED', False)
    SCHEDULER_TIMEZONE: str = os.environ.get('SCHEDULER_TIMEZONE', 'America/Sao_Paulo')

    # Rate limiting (Flask-Limiter)
    # Liga/desliga o limitador globalmente (desligado nos testes).
    RATELIMIT_ENABLED: bool = get_env_bool('RATELIMIT_ENABLED', True)
    # Backend de armazenamento. 'memory://' serve para single-process; em
    # produção com múltiplos workers aponte para um Redis (ex redis://...).
    RATELIMIT_STORAGE_URI: str = os.environ.get(
        'RATELIMIT_STORAGE_URI', 'memory://')
    # Limite aplicado ao endpoint público de ingestão (POST /api/feedbacks).
    RATELIMIT_INGEST: str = os.environ.get(
        'RATELIMIT_INGEST', '30 per minute')

    # CORS: origens permitidas para /api/* (lista separada por vírgula).
    # Default '*' porque a API é pública (ingestão + consulta de feedbacks).
    CORS_ORIGINS: List[str] = get_env_list('CORS_ORIGINS', ['*'])

    # Sentry (observabilidade de erros, OPCIONAL). Sem SENTRY_DSN, nada é
    # inicializado e o app se comporta exatamente como antes. send_default_pii
    # e include_local_variables ficam DESLIGADOS de propósito: os feedbacks
    # carregam texto de usuário e as variáveis locais o exporiam no Sentry.
    SENTRY_DSN: Optional[str] = os.environ.get('SENTRY_DSN') or None
    # Amostragem de tracing de performance (0.0–1.0). Padrão 0 = sem traces,
    # só captura de erros — o plano free do Sentry tem cota baixa de spans.
    SENTRY_TRACES_SAMPLE_RATE: float = float(
        os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0') or '0')

    @classmethod
    def is_development(cls) -> bool:
        """Check if running in development environment"""
        return cls.FLASK_ENV == 'development'

    @classmethod
    def is_production(cls) -> bool:
        """Check if running in production environment"""
        return cls.FLASK_ENV == 'production'

    @classmethod
    def has_email_config(cls) -> bool:
        """Check if email configuration is complete"""
        return all([
            cls.EMAIL_HOST,
            cls.EMAIL_USER,
            cls.EMAIL_PASSWORD,
            cls.EMAIL_RECIPIENTS
        ])
