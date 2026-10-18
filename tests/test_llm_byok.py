"""Testes da camada BYOK multi-provedor (app/llm) e da sua integração na API.

Os testes de unidade usam um LiteLLM falso (rápidos, sem rede). Dois testes
de contrato usam o LiteLLM real com ``mock_response``: provam que ele aceita
os parâmetros que enviamos e que suas exceções são classificadas, sem rede.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import validate_config
from app.config import Config
from app.llm import client as llm_client
from app.llm import (
    ByokHeaderError,
    Credential,
    LLMNotConfiguredError,
    LLMRequestError,
    complete,
    credential_from_headers,
    server_chain,
)
from app.llm.errors import Attempt
from app.llm.providers import PROVIDERS, parse_model_ref

FEEDBACK_ID = "4042f20a-45f4-4647-8050-139ac16f610b"
MESSAGES = [{"role": "user", "content": "responda em JSON"}]
NO_KEYS = {"OPENAI_API_KEY": None, "ANTHROPIC_API_KEY": None, "GEMINI_API_KEY": None,
           "GROQ_API_KEY": None, "OPENROUTER_API_KEY": None,
           "LLM_MODELS": [], "OPENAI_MODEL": "gpt-4o-mini", "BYOK_REQUEST_KEYS_ENABLED": True}


def _settings(**overrides):
    return {**NO_KEYS, **overrides}


# --- catálogo e parsing --------------------------------------------------------

def test_parse_model_ref_requires_allowlisted_provider():
    provider, model = parse_model_ref("Anthropic/claude-haiku-4-5")
    assert (provider.id, model) == ("anthropic", "claude-haiku-4-5")
    # Provedores que usam credencial do AMBIENTE do servidor ficam de fora.
    for ref in ["bedrock/anthropic.claude-3", "ollama/llama3", "vertex_ai/gemini-pro"]:
        with pytest.raises(ValueError, match="não suportado"):
            parse_model_ref(ref)
    with pytest.raises(ValueError, match="provedor/modelo"):
        parse_model_ref("claude-haiku-4-5")
    with pytest.raises(ValueError, match="inválido"):
        parse_model_ref("openai/../../etc")


# --- cadeia do servidor ----------------------------------------------------------

def test_server_chain_auto_uses_every_provider_with_a_key():
    chain = server_chain(_settings(OPENAI_API_KEY="sk-o", GEMINI_API_KEY="g-key"))
    assert [c.model_ref for c in chain] == ["openai/gpt-4o-mini", "gemini/gemini-2.5-flash-lite"]
    assert all(c.source == "server" for c in chain)


def test_server_chain_respects_llm_models_order_and_skips_missing_keys():
    chain = server_chain(_settings(
        OPENAI_API_KEY="sk-o", ANTHROPIC_API_KEY="sk-a",
        LLM_MODELS=["anthropic/claude-sonnet-4-5", "gemini/gemini-2.5-flash", "openai/gpt-4.1-mini"]))
    assert [c.model_ref for c in chain] == ["anthropic/claude-sonnet-4-5", "openai/gpt-4.1-mini"]


def test_server_chain_keeps_legacy_openai_model():
    chain = server_chain(_settings(OPENAI_API_KEY="sk-o", OPENAI_MODEL="gpt-4.1-mini"))
    assert [c.model_ref for c in chain] == ["openai/gpt-4.1-mini"]


def test_server_chain_empty_without_keys():
    assert server_chain(_settings()) == []


def test_credential_repr_never_shows_the_key():
    credential = Credential(PROVIDERS["openai"], "gpt-4o-mini", "sk-super-secreta", "server")
    assert "sk-super-secreta" not in repr(credential)


# --- BYOK por requisição ---------------------------------------------------------

def test_headers_absent_means_no_request_credential():
    assert credential_from_headers({}, _settings()) is None


def test_headers_build_request_credential_with_default_model():
    credential = credential_from_headers(
        {"X-LLM-Provider": "gemini", "X-LLM-Api-Key": "AIza-chave-do-cliente"}, _settings())
    assert credential.model_ref == "gemini/gemini-2.5-flash-lite"
    assert credential.source == "request"


@pytest.mark.parametrize("headers, status, message", [
    ({"X-LLM-Provider": "openai"}, 400, "juntos"),
    ({"X-LLM-Api-Key": "sk-solta-sem-provedor"}, 400, "juntos"),
    ({"X-LLM-Provider": "bedrock", "X-LLM-Api-Key": "sk-xxxxxxxx"}, 400, "não suportado"),
    ({"X-LLM-Provider": "openai", "X-LLM-Api-Key": "sk-xxxxxxxx", "X-LLM-Model": "a/b"}, 400, "inválido"),
    ({"X-LLM-Provider": "openai", "X-LLM-Api-Key": "curta"}, 400, "formato"),
    ({"X-LLM-Provider": "openai", "X-LLM-Api-Key": "sk com espaco"}, 400, "formato"),
])
def test_invalid_byok_headers(headers, status, message):
    with pytest.raises(ByokHeaderError, match=message) as info:
        credential_from_headers(headers, _settings())
    assert info.value.status_code == status


def test_byok_can_be_disabled_by_the_operator():
    with pytest.raises(ByokHeaderError) as info:
        credential_from_headers({"X-LLM-Provider": "openai", "X-LLM-Api-Key": "sk-xxxxxxxx"},
                                _settings(BYOK_REQUEST_KEYS_ENABLED=False))
    assert info.value.status_code == 403


# --- cliente: fallback, classificação e redação ---------------------------------

ERROR_NAMES = (
    "AuthenticationError", "PermissionDeniedError", "RateLimitError", "Timeout",
    "NotFoundError", "BadRequestError", "UnprocessableEntityError", "APIConnectionError",
    "ServiceUnavailableError", "InternalServerError",
)


class FakeLiteLLM:
    """Imita a superfície do LiteLLM que o cliente usa."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=8)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    def completion_cost(self, completion_response):
        return 0.00002


for _name in ERROR_NAMES:
    setattr(FakeLiteLLM, _name, type(_name, (Exception,), {}))


CHAIN = [
    Credential(PROVIDERS["anthropic"], "claude-haiku-4-5", "sk-ant-primaria", "server"),
    Credential(PROVIDERS["openai"], "gpt-4o-mini", "sk-openai-reserva", "server"),
]


def _complete_with(fake, chain=CHAIN, **kwargs):
    with patch.object(llm_client, "get_litellm", return_value=fake):
        return complete(MESSAGES, chain, **kwargs)


def test_first_provider_answers():
    fake = FakeLiteLLM(['{"ok": true}'])
    result = _complete_with(fake, json_mode=True, timeout=7, max_retries=1)
    assert result.model_ref == "anthropic/claude-haiku-4-5"
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (12, 8, 0.00002)
    call = fake.calls[0]
    assert call["model"] == "anthropic/claude-haiku-4-5"
    assert call["api_key"] == "sk-ant-primaria"
    assert (call["timeout"], call["num_retries"], call["drop_params"]) == (7, 1, True)
    assert call["response_format"] == {"type": "json_object"}


def test_falls_back_to_next_provider_and_redacts_the_key():
    fake = FakeLiteLLM([FakeLiteLLM.RateLimitError("429 for key sk-ant-primaria"), "resposta"])
    with patch.object(llm_client.logger, "warning") as warn:
        result = _complete_with(fake)
    assert result.model_ref == "openai/gpt-4o-mini"
    assert [c["api_key"] for c in fake.calls] == ["sk-ant-primaria", "sk-openai-reserva"]
    logged = " ".join(str(arg) for call in warn.call_args_list for arg in call.args)
    assert "sk-ant-primaria" not in logged
    assert "rate_limit" in logged


def test_all_providers_failing_raises_with_classified_attempts():
    fake = FakeLiteLLM([FakeLiteLLM.AuthenticationError("bad key sk-ant-primaria"),
                        FakeLiteLLM.Timeout("timed out")])
    with pytest.raises(LLMRequestError) as info:
        _complete_with(fake)
    attempts = info.value.attempts
    assert [(a.model_ref, a.kind) for a in attempts] == [
        ("anthropic/claude-haiku-4-5", "auth"), ("openai/gpt-4o-mini", "timeout")]
    assert "sk-ant-primaria" not in attempts[0].detail
    assert info.value.auth_failed is False


def test_empty_answer_counts_as_failure():
    fake = FakeLiteLLM(["   ", "agora sim"])
    assert _complete_with(fake).content == "agora sim"


def test_empty_chain_is_not_configured():
    with pytest.raises(LLMNotConfiguredError):
        complete(MESSAGES, [])


# --- contrato com o LiteLLM real (sem rede) ------------------------------------

def test_contract_real_litellm_accepts_our_call():
    litellm = llm_client.get_litellm()
    real_completion = litellm.completion
    payload = '{"sentiment": "POSITIVO", "is_spam": false, "requested_features": []}'

    def offline(**kwargs):
        return real_completion(mock_response=payload, **kwargs)

    credential = Credential(PROVIDERS["anthropic"], "claude-haiku-4-5", "sk-ant-teste", "request")
    with patch.object(litellm, "completion", side_effect=offline):
        result = complete(MESSAGES, [credential], json_mode=True, timeout=5, max_retries=0)

    assert result.model_ref == "anthropic/claude-haiku-4-5"
    assert result.source == "request"
    assert json.loads(result.content)["sentiment"] == "POSITIVO"


def test_contract_real_litellm_exceptions_are_classified():
    litellm = llm_client.get_litellm()
    real_completion = litellm.completion
    error = litellm.AuthenticationError(message="invalid x-api-key", llm_provider="anthropic",
                                        model="claude-haiku-4-5")

    def offline(**kwargs):
        return real_completion(mock_response=error, **kwargs)

    credential = Credential(PROVIDERS["anthropic"], "claude-haiku-4-5", "sk-ant-teste", "request")
    with patch.object(litellm, "completion", side_effect=offline):
        with pytest.raises(LLMRequestError) as info:
            complete(MESSAGES, [credential], max_retries=0)
    assert info.value.auth_failed is True


# --- boot -----------------------------------------------------------------------

def _config(**attrs):
    return type("LLMConfig", (Config,), {**NO_KEYS, **attrs})


def test_app_boots_without_any_llm_key():
    validate_config(_config())


def test_malformed_llm_models_fails_fast():
    with pytest.raises(ValueError, match="LLM_MODELS"):
        validate_config(_config(LLM_MODELS=["bedrock/anthropic.claude-3"]))


# --- API ponta a ponta ----------------------------------------------------------

ANALYSIS = {"sentiment": "POSITIVO", "is_spam": False, "requested_features": [],
            "analyzed_by": "gemini/gemini-2.5-flash-lite"}
BYOK_HEADERS = {"X-LLM-Provider": "gemini", "X-LLM-Api-Key": "AIza-chave-do-cliente"}


def _post(client, headers=None):
    return client.post("/api/feedbacks", json={"id": FEEDBACK_ID, "feedback": "adorei"},
                       headers=headers or {})


def test_api_passes_byok_credential_and_reports_analyzed_by(client):
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", return_value=ANALYSIS) as spy:
        resp = _post(client, BYOK_HEADERS)
    assert resp.status_code == 201
    assert resp.get_json()["analyzed_by"] == "gemini/gemini-2.5-flash-lite"
    credential = spy.call_args.kwargs["credential"]
    assert credential.model_ref == "gemini/gemini-2.5-flash-lite"
    assert credential.api_key == "AIza-chave-do-cliente"


def test_api_without_any_provider_is_503_and_persists_nothing(client):
    client.application.config.update(NO_KEYS)
    resp = _post(client)
    assert resp.status_code == 503
    assert resp.get_json()["code"] == "llm_not_configured"
    assert client.get(f"/api/feedbacks/{FEEDBACK_ID}").status_code == 404


def test_api_byok_rejected_key_is_422_and_persists_nothing(client):
    failure = LLMRequestError([Attempt("gemini/gemini-2.5-flash-lite", "auth", "API key not valid")])
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", side_effect=failure):
        resp = _post(client, BYOK_HEADERS)
    assert resp.status_code == 422
    assert resp.get_json()["code"] == "llm_auth_failed"
    assert "AIza-chave-do-cliente" not in resp.get_data(as_text=True)
    assert client.get(f"/api/feedbacks/{FEEDBACK_ID}").status_code == 404


def test_api_byok_provider_failure_is_502(client):
    failure = LLMRequestError([Attempt("gemini/gemini-2.5-flash-lite", "rate_limit", "quota")])
    with patch("app.routes.api.feedback_analyzer.analyze_feedback", side_effect=failure):
        resp = _post(client, BYOK_HEADERS)
    assert resp.status_code == 502
    assert resp.get_json()["detail"] == "rate_limit"


def test_api_invalid_byok_headers_is_400(client):
    resp = _post(client, {"X-LLM-Provider": "bedrock", "X-LLM-Api-Key": "sk-xxxxxxxx"})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_byok_headers"


def test_api_end_to_end_with_server_key_through_real_analyzer(client):
    """Rota -> analisador -> cadeia do servidor, com só o LiteLLM falso."""
    client.application.config.update({**NO_KEYS, "ANTHROPIC_API_KEY": "sk-ant-server"})
    payload = json.dumps({"sentiment": "NEGATIVO", "is_spam": False,
                          "requested_features": [{"code": "modo escuro", "reason": "cansa a vista"}]})
    fake = FakeLiteLLM([payload])
    with patch.object(llm_client, "get_litellm", return_value=fake):
        resp = _post(client)

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["analyzed_by"] == "anthropic/claude-haiku-4-5"
    assert body["sentiment"] == "NEGATIVO"
    assert body["requested_features"] == [{"code": "MODO_ESCURO", "reason": "cansa a vista"}]
    assert fake.calls[0]["api_key"] == "sk-ant-server"
