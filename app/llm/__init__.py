"""Camada de LLM do FeedLens: BYOK multi-provedor sobre o LiteLLM.

- ``providers``: catálogo/allowlist (OpenAI, Anthropic, Google Gemini, Groq, OpenRouter).
- ``credentials``: de onde vem a chave (BYOK por requisição > chaves do servidor).
- ``client``: chamada com fallback entre provedores e erros sem a chave.

O LiteLLM só é importado na primeira chamada (veja ``client.get_litellm``).
"""
from app.llm.client import Completion, complete, warm_up
from app.llm.credentials import (
    Credential,
    credential_from_headers,
    llm_settings,
    server_chain,
    validate_llm_config,
)
from app.llm.errors import ByokHeaderError, LLMError, LLMNotConfiguredError, LLMRequestError
from app.llm.providers import PROVIDERS, Provider

__all__ = [
    'PROVIDERS',
    'ByokHeaderError',
    'Completion',
    'Credential',
    'LLMError',
    'LLMNotConfiguredError',
    'LLMRequestError',
    'Provider',
    'complete',
    'credential_from_headers',
    'llm_settings',
    'server_chain',
    'validate_llm_config',
    'warm_up',
]
