from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
from typing import Type
import logging

from app.config import Config
from app.logging_config import configure_logging

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
scheduler = BackgroundScheduler()

# Configure logging (formato consistente + nível via LOG_LEVEL)
configure_logging()
logger = logging.getLogger(__name__)

# Placeholders que já apareceram no repositório (config, .env-example, compose).
KNOWN_PLACEHOLDER_SECRETS = frozenset({
    'default-secret-key',
    'change-me',
    'sua-chave-secreta-aqui',
    'sua_chave_secreta',
})
MIN_SECRET_KEY_LENGTH = 32
MIN_API_TOKEN_LENGTH = 24


def is_insecure_secret_key(secret_key: str | None) -> bool:
    """True para chave ausente, placeholder conhecido ou curta demais."""
    if not secret_key or secret_key in KNOWN_PLACEHOLDER_SECRETS:
        return True
    return len(secret_key) < MIN_SECRET_KEY_LENGTH


def validate_config(config: Type[Config]) -> None:
    """
    Validate application configuration

    Args:
        config: Configuration class to validate

    Raises:
        ValueError: If required configuration is missing
    """
    # Check required Flask configuration
    if is_insecure_secret_key(config.SECRET_KEY):
        if config.is_production():
            # Com uma chave conhecida qualquer um assina um cookie de sessão
            # 'autenticado' e entra no dashboard sem a senha.
            raise ValueError(
                'SECRET_KEY insegura em produção: defina uma chave aleatória '
                f'com pelo menos {MIN_SECRET_KEY_LENGTH} caracteres.')
        logger.warning(
            'Using default secret key. This is not secure for production.')

    # Check LLM configuration (BYOK: no server key is required)
    from app.llm import PROVIDERS, validate_llm_config
    validate_llm_config(config)

    # Check email configuration if in production
    if config.is_production() and not config.has_email_config():
        logger.warning(
            'Email is not configured: the weekly summary will not be sent.')

    # Warn when the dashboard is left open in production
    if config.is_production() and not config.DASHBOARD_PASSWORD:
        logger.warning(
            'DASHBOARD_PASSWORD is not set. The dashboard is publicly '
            'accessible without authentication.')

    # Token de leitura da API: curto demais é força-bruta viável (o limiter
    # só cobre POST), então exige comprimento mínimo quando definido.
    api_token = config.API_READ_TOKEN
    if api_token and len(api_token) < MIN_API_TOKEN_LENGTH:
        raise ValueError(
            f'API_READ_TOKEN muito curto: use pelo menos {MIN_API_TOKEN_LENGTH} '
            'caracteres (ex.: python -c "import secrets; print(secrets.token_urlsafe(32))").')
    if config.is_production() and not (config.DASHBOARD_PASSWORD or api_token):
        logger.warning(
            'Nem DASHBOARD_PASSWORD nem API_READ_TOKEN estão definidos: '
            'GET /api/feedbacks está público.')

    # Chaves de ingestão: cada uma é a única barreira contra gasto de LLM.
    from app.ingest_auth import MIN_INGEST_KEY_LENGTH
    for ingest_key in config.INGEST_API_KEYS:
        if len(ingest_key) < MIN_INGEST_KEY_LENGTH:
            raise ValueError(
                f'INGEST_API_KEY muito curta: use pelo menos {MIN_INGEST_KEY_LENGTH} '
                'caracteres (ex.: python -c "import secrets; print(secrets.token_urlsafe(32))").')
    server_pays = any(
        getattr(config, provider.env_key, None) for provider in PROVIDERS.values())
    if config.is_production() and server_pays and not config.INGEST_API_KEYS:
        logger.warning(
            'INGEST_API_KEY não está definida e há chave de LLM no servidor: '
            'POST /api/feedbacks está aberto e qualquer um gasta a sua cota.')

    # Ingestão pública (opt-in): origens exatas. '*' não faz sentido aqui —
    # seria aceitar qualquer site sem chave — e é recusado no boot.
    public_origins = config.INGEST_PUBLIC_ORIGINS
    if '*' in public_origins:
        raise ValueError(
            "INGEST_PUBLIC_ORIGINS não aceita '*': liste as origens EXATAS "
            '(ex.: https://form.exemplo.com). O curinga aceitaria qualquer '
            'site enviando sem chave e esvaziaria o teto de gasto.')
    if public_origins:
        from app.ingest_auth import normalize_origin
        cors_origins = config.CORS_ORIGINS
        cors_wildcard = '*' in cors_origins
        cors_normalized = {normalize_origin(o) for o in cors_origins}
        logger.warning(
            'Ingestão pública LIGADA para %d origem(ns): o POST /api/feedbacks '
            'aceita envios sem chave dessas origens, sob rate limit (%s) e teto '
            'diário (%d). O header Origin é forjável fora do navegador e NÃO '
            'autentica — configure um alerta de gasto no provedor de LLM.',
            len(public_origins), config.RATELIMIT_INGEST_PUBLIC,
            config.INGEST_PUBLIC_DAILY_CAP)
        for origin in public_origins:
            invalid = not normalize_origin(origin)
            if invalid:
                logger.warning(
                    "INGEST_PUBLIC_ORIGINS tem uma entrada inválida (%r): use só "
                    'esquema+host+porta, sem caminho. Ela será ignorada.', origin)
                continue
            if cors_wildcard:
                logger.warning(
                    "CORS_ORIGINS é '*': o navegador do formulário consegue ler a "
                    'resposta, mas prefira listar a origem pública exata em '
                    'CORS_ORIGINS em produção.')
            elif normalize_origin(origin) not in cors_normalized:
                logger.warning(
                    'Origem pública %r não está em CORS_ORIGINS: o navegador vai '
                    'bloquear a resposta por CORS. Adicione-a a CORS_ORIGINS.',
                    origin)


def init_extensions(app: Flask) -> None:
    """
    Initialize Flask extensions

    Args:
        app: Flask application instance
    """
    db.init_app(app)
    migrate.init_app(app, db)

    # O Flask-Limiter lê RATELIMIT_STORAGE_URI e RATELIMIT_ENABLED do
    # app.config automaticamente, então basta inicializá-lo.
    from app.extensions import limiter
    limiter.init_app(app)

    # CORS apenas nas rotas da API (/api/*). As rotas web/auth são de sessão
    # de dashboard e não precisam de CORS. Origens configuráveis via env.
    from app.extensions import cors
    origins = app.config.get('CORS_ORIGINS', ['*'])
    cors.init_app(app, resources={r'/api/*': {'origins': origins}})


def register_blueprints(app: Flask) -> None:
    """
    Register application blueprints

    Args:
        app: Flask application instance
    """
    from app.routes.api import api as api_blueprint
    app.register_blueprint(api_blueprint, url_prefix='/api')

    from app.routes.web import web as web_blueprint
    app.register_blueprint(web_blueprint)

    from app.routes.spa import spa as spa_blueprint
    app.register_blueprint(spa_blueprint)

    from app.routes.health import health as health_blueprint
    app.register_blueprint(health_blueprint)

    from app.routes.settings_api import settings_api as settings_blueprint
    app.register_blueprint(settings_blueprint, url_prefix='/api/settings')

    from app.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint)

    @app.errorhandler(429)
    def ratelimit_handler(error):
        """Resposta JSON para quando o rate-limit é atingido."""
        from flask import jsonify, request
        description = getattr(error, 'description', 'rate limit exceeded')
        # Rotas da API respondem JSON; a web herda o HTML default do Flask.
        if request.path.startswith('/api'):
            return jsonify({
                'error': 'Too many requests',
                'detail': str(description),
            }), 429
        if request.endpoint == 'auth.login':
            # O login é do SPA: JSON com a mensagem que a tela mostra.
            return jsonify({
                'error': 'Muitas tentativas de entrar. Espere um minuto e tente de novo.',
                'code': 'rate_limited',
            }), 429
        return error

    @app.errorhandler(413)
    def payload_too_large_handler(error):
        """Corpo acima de MAX_CONTENT_LENGTH: JSON na API, HTML na web."""
        from flask import jsonify, request
        if request.path.startswith('/api'):
            return jsonify({'error': 'Request body too large'}), 413
        return error


def init_scheduler(app: Flask) -> None:
    """
    Initialize background task scheduler

    Opt-in (``SCHEDULER_ENABLED``): só um processo por deploy deve agendar o
    resumo semanal, senão cada worker envia o seu e-mail. Veja app/scheduling.py.

    Args:
        app: Flask application instance
    """
    if not app.config.get('SCHEDULER_ENABLED'):
        logger.info('Background scheduler disabled (SCHEDULER_ENABLED=false)')
        return

    if not scheduler.running:
        from app.scheduling import schedule_weekly_summary
        try:
            schedule_weekly_summary(scheduler, app)
            scheduler.start()
            logger.info('Background scheduler started successfully')
        except Exception as e:
            logger.error(f'Failed to start scheduler: {str(e)}')


def create_app(config_class: Type[Config] = Config) -> Flask:
    """
    Create and configure the Flask application

    Args:
        config_class: Configuration class to use

    Returns:
        Configured Flask application

    Raises:
        ValueError: If required configuration is missing
    """
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config_class)

    try:
        # Validate configuration
        validate_config(config_class)

        # Initialize components
        init_extensions(app)
        register_blueprints(app)
        init_scheduler(app)

        from app.scheduling import register_cli
        register_cli(app)

        from app.cli import register_analysis_cli, register_vault_cli
        register_vault_cli(app)
        register_analysis_cli(app)

        logger.info(f'Application started in {config_class.FLASK_ENV} mode')

    except Exception as e:
        logger.error(f'Failed to initialize application: {str(e)}')
        raise

    return app

# Import models for Flask-Migrate to detect them
from app.models import feedback, provider_credential  # noqa: E402,F401
