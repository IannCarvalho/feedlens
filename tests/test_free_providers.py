"""Groq e OpenRouter como provedores (prefixos ``groq/`` e ``openrouter/``).

Cobre o catálogo, a regra do nome de modelo "fabricante/modelo" (que só vale
nesses dois), a cadeia do servidor, o BYOK por requisição e o roteamento do
LiteLLM real com ``mock_response`` (sem rede). A API de cadastro de chaves
fica em ``test_provider_keys.py``.
"""
import json
from unittest.mock import patch

import pytest

from app.llm import (
    ByokHeaderError,
    Credential,
    complete,
    credential_from_headers,
    server_chain,
)
from app.llm import client as llm_client
from app.llm.providers import (
    MODEL_REF_MAX_LENGTH,
    PROVIDERS,
    parse_model_ref,
    validate_model_name,
)

NO_KEYS = {"OPENAI_API_KEY": None, "ANTHROPIC_API_KEY": None, "GEMINI_API_KEY": None,
           "GROQ_API_KEY": None, "OPENROUTER_API_KEY": None,
           "LLM_MODELS": [], "OPENAI_MODEL": "gpt-4o-mini", "BYOK_REQUEST_KEYS_ENABLED": True}
MESSAGES = [{"role": "user", "content": "responda em JSON"}]


def _settings(**overrides):
    return {**NO_KEYS, **overrides}


# --- catálogo ------------------------------------------------------------------

def test_catalog_has_the_new_providers_with_their_env_keys():
    assert list(PROVIDERS) == ["openai", "anthropic", "gemini", "groq", "openrouter"]
    assert PROVIDERS["groq"].env_key == "GROQ_API_KEY"
    assert PROVIDERS["openrouter"].env_key == "OPENROUTER_API_KEY"
    assert PROVIDERS["openrouter"].default_model == "openrouter/free"
    # Só os dois novos têm catálogo "fabricante/modelo".
    assert {p.id for p in PROVIDERS.values() if p.namespaced_models} == {"groq", "openrouter"}


@pytest.mark.parametrize("ref, provider_id, model", [
    ("groq/llama-3.3-70b-versatile", "groq", "llama-3.3-70b-versatile"),
    ("Groq/openai/gpt-oss-20b", "groq", "openai/gpt-oss-20b"),
    ("openrouter/google/gemma-4-31b-it:free", "openrouter", "google/gemma-4-31b-it:free"),
    ("openrouter/openrouter/free", "openrouter", "openrouter/free"),
    ("openrouter/", "openrouter", "openrouter/free"),     # modelo omitido = padrão
    ("groq/", "groq", "llama-3.3-70b-versatile"),
])
def test_parse_model_ref_accepts_vendor_model_ids(ref, provider_id, model):
    provider, parsed = parse_model_ref(ref)
    assert (provider.id, parsed) == (provider_id, model)


@pytest.mark.parametrize("model", [
    "a/b/c",                      # mais de uma barra
    "/modelo",                    # começa pela barra
    "vendor//modelo",
    "vendor/",
    "../../etc/passwd",           # sai do prefixo
    "vendor/../modelo",
    "vendor/.oculto",
    "modelo com espaco",
    "x" * 100,                    # 'provedor/modelo' não cabe em analyzed_by (100)
])
def test_namespaced_providers_still_reject_malformed_models(model):
    for provider_id in ("groq", "openrouter"):
        with pytest.raises(ValueError, match="inválido"):
            validate_model_name(model, PROVIDERS[provider_id])


def test_model_ref_always_fits_the_analyzed_by_column():
    provider = PROVIDERS["openrouter"]
    longest = "v/" + "m" * (MODEL_REF_MAX_LENGTH - len(provider.id) - 1 - 2)
    assert validate_model_name(longest, provider) == longest
    assert len(f"{provider.id}/{longest}") == MODEL_REF_MAX_LENGTH
    with pytest.raises(ValueError, match="inválido"):
        validate_model_name(longest + "m", provider)


def test_other_providers_keep_rejecting_the_slash():
    """A barra só é aceita onde o catálogo do provedor tem 'fabricante/modelo'."""
    for provider_id in ("openai", "anthropic", "gemini"):
        with pytest.raises(ValueError, match="inválido"):
            validate_model_name("vendor/modelo", PROVIDERS[provider_id])
        with pytest.raises(ValueError, match="inválido"):
            parse_model_ref(f"{provider_id}/vendor/modelo")
    # Sem provedor, a regra estrita de sempre.
    with pytest.raises(ValueError, match="inválido"):
        validate_model_name("vendor/modelo")
    assert validate_model_name("ft:gpt-4o-mini:org::id") == "ft:gpt-4o-mini:org::id"


# --- cadeia do servidor ----------------------------------------------------------

def test_server_chain_auto_appends_free_providers_after_paid_ones():
    chain = server_chain(_settings(
        OPENROUTER_API_KEY="sk-or-v1-x", GROQ_API_KEY="gsk_x", GEMINI_API_KEY="g-key"))
    assert [c.model_ref for c in chain] == [
        "gemini/gemini-2.5-flash-lite", "groq/llama-3.3-70b-versatile", "openrouter/openrouter/free"]
    assert all(c.source == "server" for c in chain)


def test_server_chain_respects_llm_models_with_the_new_providers():
    chain = server_chain(_settings(
        GROQ_API_KEY="gsk_x", OPENROUTER_API_KEY="sk-or-v1-x", GEMINI_API_KEY="g-key",
        LLM_MODELS=["groq/openai/gpt-oss-20b", "openrouter/google/gemma-4-31b-it:free",
                    "gemini/gemini-2.5-flash"]))
    assert [c.model_ref for c in chain] == [
        "groq/openai/gpt-oss-20b", "openrouter/google/gemma-4-31b-it:free",
        "gemini/gemini-2.5-flash"]


def test_server_chain_skips_a_new_provider_without_its_key():
    chain = server_chain(_settings(GROQ_API_KEY="gsk_x", LLM_MODELS=[
        "openrouter/openrouter/free", "groq/llama-3.3-70b-versatile"]))
    assert [c.model_ref for c in chain] == ["groq/llama-3.3-70b-versatile"]


# --- BYOK por requisição ---------------------------------------------------------

def test_headers_build_request_credentials_for_the_new_providers():
    groq = credential_from_headers(
        {"X-LLM-Provider": "groq", "X-LLM-Api-Key": "gsk_chave-do-cliente"}, _settings())
    assert groq.model_ref == "groq/llama-3.3-70b-versatile" and groq.source == "request"

    openrouter = credential_from_headers(
        {"X-LLM-Provider": "OpenRouter", "X-LLM-Api-Key": "sk-or-v1-cliente",
         "X-LLM-Model": "google/gemma-4-31b-it:free"}, _settings())
    assert openrouter.model_ref == "openrouter/google/gemma-4-31b-it:free"


@pytest.mark.parametrize("provider, model", [
    ("openrouter", "a/b/c"), ("groq", "../modelo"), ("openai", "vendor/modelo")])
def test_byok_header_rejects_bad_models(provider, model):
    with pytest.raises(ByokHeaderError, match="inválido") as info:
        credential_from_headers(
            {"X-LLM-Provider": provider, "X-LLM-Api-Key": "sk-xxxxxxxx", "X-LLM-Model": model},
            _settings())
    assert info.value.status_code == 400


# --- roteamento do LiteLLM real (sem rede) --------------------------------------------

@pytest.mark.parametrize("provider_id, model, litellm_provider, litellm_model", [
    ("groq", "llama-3.3-70b-versatile", "groq", "llama-3.3-70b-versatile"),
    ("groq", "openai/gpt-oss-20b", "groq", "openai/gpt-oss-20b"),
    ("openrouter", "openrouter/free", "openrouter", "openrouter/free"),
    ("openrouter", "google/gemma-4-31b-it:free", "openrouter", "google/gemma-4-31b-it:free"),
])
def test_litellm_routes_the_prefixes_to_the_right_provider(
        provider_id, model, litellm_provider, litellm_model):
    litellm = llm_client.get_litellm()
    credential = Credential(PROVIDERS[provider_id], model, "x" * 12, "server")
    resolved, provider, _, _ = litellm.get_llm_provider(credential.model_ref)
    assert (provider, resolved) == (litellm_provider, litellm_model)
    # JSON mode e temperatura são parâmetros que o LiteLLM aceita nesses provedores.
    params = litellm.get_supported_openai_params(model=resolved, custom_llm_provider=provider)
    assert {"response_format", "temperature"} <= set(params)


@pytest.mark.parametrize("provider_id, model", [
    ("groq", "llama-3.3-70b-versatile"), ("openrouter", "google/gemma-4-31b-it:free")])
def test_contract_real_litellm_accepts_our_call_on_new_providers(provider_id, model):
    litellm = llm_client.get_litellm()
    real_completion = litellm.completion
    payload = '{"sentiment": "POSITIVO", "is_spam": false, "requested_features": []}'
    seen = {}

    def offline(**kwargs):
        seen.update(kwargs)
        return real_completion(mock_response=payload, **kwargs)

    credential = Credential(PROVIDERS[provider_id], model, "chave-de-teste-123", "request")
    with patch.object(litellm, "completion", side_effect=offline):
        result = complete(MESSAGES, [credential], json_mode=True, timeout=5, max_retries=0)

    assert result.model_ref == f"{provider_id}/{model}"
    assert seen["model"] == f"{provider_id}/{model}"
    assert seen["api_key"] == "chave-de-teste-123"
    assert seen["response_format"] == {"type": "json_object"}
    assert json.loads(result.content)["sentiment"] == "POSITIVO"
