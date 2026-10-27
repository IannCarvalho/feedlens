"""De onde vem a chave de cada chamada à LLM.

Precedência:

1. **BYOK por requisição** (headers ``X-LLM-Provider`` + ``X-LLM-Api-Key`` e,
   opcional, ``X-LLM-Model``): usa SÓ essa credencial, sem cair para as
   chaves do servidor. Quem trouxe a chave paga a chamada, e um erro dela
   volta para o cliente em vez de ser mascarado.
2. **Chaves do operador** (variáveis de ambiente): ``LLM_MODELS`` define a
   ordem ``provedor/modelo`` (principal primeiro, depois as reservas). Sem
   ``LLM_MODELS``, entra todo provedor que tiver chave, no modelo padrão,
   na ordem do catálogo.

A chave nunca é guardada, logada nem devolvida: ``Credential`` a omite do
``repr`` e o cliente a remove das mensagens de erro.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from flask import current_app, has_app_context

from app.llm.errors import ByokHeaderError
from app.llm.providers import PROVIDERS, Provider, get_provider, parse_model_ref, validate_model_name
from app.llm.vault import parse_fernet_keys

logger = logging.getLogger(__name__)

HEADER_PROVIDER = 'X-LLM-Provider'
HEADER_API_KEY = 'X-LLM-Api-Key'
HEADER_MODEL = 'X-LLM-Model'

# Chaves dos provedores são ASCII imprimível sem espaços (sk-..., AIza...).
API_KEY_RE = re.compile(r'^[\x21-\x7e]{8,512}$')


@dataclass(frozen=True)
class Credential:
    """Uma chave + o modelo em que ela será usada."""

    provider: Provider
    model: str
    api_key: str = field(repr=False)
    source: str = 'server'  # 'request' (BYOK do cliente) | 'stored' (cofre) | 'server' (env)
    # Id da linha em ``provider_credentials`` quando source='stored'. É o que
    # permite registrar no banco que o provedor recusou a chave sem que esta
    # camada (pura, sem app context) conheça o modelo nem a sessão do banco.
    stored_id: Optional[str] = None

    @property
    def model_ref(self) -> str:
        return f'{self.provider.id}/{self.model}'


def llm_settings() -> Mapping[str, Any]:
    """Configuração viva: ``app.config`` dentro do app; senão, a classe Config."""
    if has_app_context():
        return current_app.config
    from app.config import Config

    return {name: getattr(Config, name) for name in dir(Config) if name.isupper()}


def _default_model(provider: Provider, settings: Mapping[str, Any]) -> str:
    # Compatibilidade: OPENAI_MODEL continua valendo quando LLM_MODELS não existe.
    if provider.id == 'openai' and settings.get('OPENAI_MODEL'):
        return settings['OPENAI_MODEL']
    return provider.default_model


def server_chain(settings: Optional[Mapping[str, Any]] = None) -> list[Credential]:
    """Cadeia ordenada de credenciais do operador (vazia = nenhuma chave)."""
    settings = llm_settings() if settings is None else settings
    configured = settings.get('LLM_MODELS') or []
    if configured:
        refs = [parse_model_ref(ref) for ref in configured]
    else:
        refs = [(provider, _default_model(provider, settings)) for provider in PROVIDERS.values()]

    chain: list[Credential] = []
    seen: set[str] = set()
    for provider, model in refs:
        api_key = settings.get(provider.env_key)
        credential_ref = f'{provider.id}/{model}'
        if not api_key or credential_ref in seen:
            continue
        seen.add(credential_ref)
        chain.append(Credential(provider, model, api_key, source='server'))
    return chain


def credential_from_headers(headers: Mapping[str, str],
                            settings: Optional[Mapping[str, Any]] = None) -> Optional[Credential]:
    """Lê o BYOK por requisição. None quando nenhum header X-LLM-* veio."""
    provider_id = (headers.get(HEADER_PROVIDER) or '').strip()
    api_key = (headers.get(HEADER_API_KEY) or '').strip()
    model = (headers.get(HEADER_MODEL) or '').strip()
    if not (provider_id or api_key or model):
        return None

    settings = llm_settings() if settings is None else settings
    if not settings.get('BYOK_REQUEST_KEYS_ENABLED', True):
        raise ByokHeaderError('BYOK por requisição está desativado neste servidor', 403)
    if not provider_id or not api_key:
        raise ByokHeaderError(f'envie {HEADER_PROVIDER} e {HEADER_API_KEY} juntos')
    if not API_KEY_RE.match(api_key):
        raise ByokHeaderError(f'{HEADER_API_KEY} em formato inválido')
    try:
        provider = get_provider(provider_id)
        model = validate_model_name(model, provider) if model else provider.default_model
    except ValueError as e:
        raise ByokHeaderError(str(e)) from None
    return Credential(provider, model, api_key, source='request')


def validate_llm_config(config: Any) -> None:
    """Checagem de boot: LLM_MODELS malformado falha cedo; falta de chave só avisa."""
    settings = {name: getattr(config, name) for name in dir(config) if name.isupper()}
    # Chave de cifra do cofre malformada derruba o boot (em vez de falhar só
    # ao salvar a primeira chave). Ausente = cofre desligado, sem aviso.
    parse_fernet_keys(settings.get('FEEDLENS_ENCRYPTION_KEY'))
    try:
        chain = server_chain(settings)
    except ValueError as e:
        raise ValueError(f'LLM_MODELS inválido: {e}') from None

    for ref in settings.get('LLM_MODELS') or []:
        provider, _ = parse_model_ref(ref)
        if not settings.get(provider.env_key):
            logger.warning('LLM_MODELS lista %s, mas %s não está definida; entrada ignorada.',
                           ref, provider.env_key)

    if not chain:
        logger.warning(
            'Nenhuma chave de LLM no servidor (%s). A análise só funcionará com BYOK '
            'por requisição (headers %s e %s).',
            ', '.join(p.env_key for p in PROVIDERS.values()), HEADER_PROVIDER, HEADER_API_KEY)
    else:
        logger.info('Cadeia de LLM: %s', ' -> '.join(c.model_ref for c in chain))
