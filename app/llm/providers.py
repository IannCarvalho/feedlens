"""Catálogo dos provedores de LLM suportados (BYOK).

A allowlist é deliberada: o LiteLLM fala com dezenas de provedores, e alguns
usam credenciais do AMBIENTE do servidor (``bedrock/`` usa o IAM da máquina,
``ollama/`` chama um host interno). Aceitar um prefixo arbitrário vindo de
um header deixaria um cliente usar a nuvem do operador. Só os provedores
abaixo, com chave explícita, são aceitos.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """Um provedor de LLM e como ele é configurado."""

    id: str             # prefixo do LiteLLM ("openai" em "openai/gpt-4o-mini")
    label: str          # nome para humanos
    env_key: str        # variável de ambiente com a chave do operador
    default_model: str  # modelo barato e rápido, suficiente para classificar
    # O catálogo do provedor tem ids "vendor/modelo" (ex.: OpenRouter
    # "google/gemma-4-31b-it:free", Groq "openai/gpt-oss-20b"): aceita UMA barra
    # no nome do modelo. Nos demais a barra continua proibida.
    namespaced_models: bool = False


PROVIDERS: dict[str, Provider] = {
    provider.id: provider
    for provider in (
        Provider('openai', 'OpenAI', 'OPENAI_API_KEY', 'gpt-4o-mini'),
        Provider('anthropic', 'Anthropic', 'ANTHROPIC_API_KEY', 'claude-haiku-4-5'),
        Provider('gemini', 'Google Gemini', 'GEMINI_API_KEY', 'gemini-2.5-flash-lite'),
        # Os dois têm camada gratuita (com limites). A ordem do catálogo é a da
        # cadeia automática (sem LLM_MODELS), então ficam depois dos pagos.
        Provider('groq', 'Groq', 'GROQ_API_KEY', 'openai/gpt-oss-20b',
                 namespaced_models=True),
        # "openrouter/free" é o roteador de modelos gratuitos do OpenRouter: a lista
        # de ids ":free" muda toda hora, e o roteador escolhe um que esteja no ar.
        Provider('openrouter', 'OpenRouter', 'OPENROUTER_API_KEY', 'openrouter/free',
                 namespaced_models=True),
    )
}

# Nome de modelo sem '/', para que o provedor venha SEMPRE do nosso prefixo.
# Cobre ids como 'gpt-4o-mini', 'claude-haiku-4-5' e 'ft:gpt-4o-mini:org::id'.
MODEL_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')

# Provedores com catálogo "vendor/modelo": no máximo UMA barra, e cada trecho
# começa por letra ou dígito, então '..', '/x' e 'a//b' continuam recusados.
NAMESPACED_MODEL_RE = re.compile(r'^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}/)?[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')

# O 'provedor/modelo' é gravado em feedbacks.analyzed_by (VARCHAR 100).
MODEL_REF_MAX_LENGTH = 100


def supported_ids() -> str:
    return ', '.join(PROVIDERS)


def get_provider(provider_id: str) -> Provider:
    """Resolve um provedor da allowlist (case-insensitive)."""
    provider = PROVIDERS.get(provider_id.strip().lower())
    if provider is None:
        raise ValueError(
            f"provedor de LLM não suportado: '{provider_id}'. Use: {supported_ids()}")
    return provider


def validate_model_name(model: str, provider: Provider | None = None) -> str:
    """Valida o nome do modelo. Com ``provider``, vale a regra desse provedor.

    Sem ``provider`` (ou num provedor sem catálogo "vendor/modelo") a barra é
    proibida, para o provedor sair SEMPRE do nosso prefixo.
    """
    model = model.strip()
    if provider is not None and provider.namespaced_models:
        if (not NAMESPACED_MODEL_RE.match(model)
                or len(provider.id) + 1 + len(model) > MODEL_REF_MAX_LENGTH):
            raise ValueError(f"nome de modelo inválido: '{model}'")
        return model
    if not MODEL_NAME_RE.match(model):
        raise ValueError(f"nome de modelo inválido: '{model}'")
    return model


def parse_model_ref(ref: str) -> tuple[Provider, str]:
    """Interpreta 'provedor/modelo' (ex.: 'anthropic/claude-haiku-4-5').

    O prefixo é obrigatório: 'claude-haiku-4-5' sozinho seria ambíguo. Só a
    primeira barra separa o provedor: 'openrouter/google/gemma-4-31b-it:free'
    é o provedor 'openrouter' com o modelo 'google/gemma-4-31b-it:free'.
    """
    if '/' not in ref:
        raise ValueError(
            f"use 'provedor/modelo' (ex.: 'openai/gpt-4o-mini'), recebido: '{ref}'")
    provider_id, model = ref.split('/', 1)
    provider = get_provider(provider_id)
    return provider, validate_model_name(model or provider.default_model, provider)
