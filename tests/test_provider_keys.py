"""Fase 2 do BYOK: cofre cifrado, API de chaves e cadeia do operador.

A camada de LLM é mockada (``app.routes.settings_api.complete`` e
``app.services.feedback_analyzer.complete``): nenhum teste faz rede.
"""
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError

from app import create_app, db
from app.llm import Completion, LLMRequestError
from app.llm.errors import Attempt
from app.llm.vault import Vault, VaultDecryptError, VaultDisabledError, key_hint, parse_fernet_keys
from app.models.provider_credential import ProviderCredential
from app.services import credential_store as store
from app.services.feedback_analyzer import FeedbackAnalyzer

from tests.helpers import login

PASSWORD = "segredo-do-painel"
OPENAI_KEY = "sk-proj-AAAA1111BBBB2222a1b2"
ANTHROPIC_KEY = "sk-ant-api03-CCCC3333DDDD4444z9y8"
PROBE = "app.routes.settings_api.complete"
BASE = "/api/settings/providers"


def _fernet_key() -> str:
    return Fernet.generate_key().decode()


def _ok(model_ref="openai/gpt-4o-mini"):
    return Completion(content="ok", model_ref=model_ref, source="stored", latency_ms=12)


def _fail(kind):
    return LLMRequestError([Attempt("openai/gpt-4o-mini", kind, "boom")])


@pytest.fixture()
def secured_app():
    """App com senha no painel e cofre ligado, banco em memória."""
    application = create_app()
    application.config.update(
        TESTING=True, DASHBOARD_PASSWORD=PASSWORD, FEEDLENS_ENCRYPTION_KEY=_fernet_key(),
        OPENAI_API_KEY=None, ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None,
        GROQ_API_KEY=None, OPENROUTER_API_KEY=None, LLM_MODELS=[])
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def admin(secured_app):
    """Client logado, com o token CSRF já em mãos."""
    client = secured_app.test_client()
    assert login(client, PASSWORD).status_code == 200
    body = client.get(BASE).get_json()
    client.csrf = {"X-CSRF-Token": body["csrf_token"]}
    return client


def _create(admin, **overrides):
    payload = {"provider": "openai", "api_key": OPENAI_KEY, **overrides}
    return admin.post(BASE, json=payload, headers=admin.csrf)


# --------------------------------------------------------------------- vault

def test_vault_round_trip_and_ciphertext_hides_the_key():
    vault = Vault(_fernet_key())
    token = vault.encrypt(OPENAI_KEY)
    assert OPENAI_KEY not in token
    assert vault.decrypt(token) == OPENAI_KEY


def test_vault_disabled_without_key_refuses_to_encrypt():
    vault = Vault("")
    assert not vault.enabled
    with pytest.raises(VaultDisabledError):
        vault.encrypt(OPENAI_KEY)


def test_invalid_encryption_key_fails_without_leaking_the_value():
    with pytest.raises(ValueError) as error:
        parse_fernet_keys("isto-nao-e-uma-chave-fernet")
    assert "isto-nao-e-uma-chave-fernet" not in str(error.value)


def test_multifernet_rotation_moves_tokens_to_the_new_primary_key():
    old, new = _fernet_key(), _fernet_key()
    token = Vault(old).encrypt(OPENAI_KEY)
    both = Vault(f"{new},{old}")
    assert both.decrypt(token) == OPENAI_KEY  # a antiga ainda decifra
    rotated = both.rotate(token)
    assert Vault(new).decrypt(rotated) == OPENAI_KEY  # só a nova basta agora
    with pytest.raises(VaultDecryptError):
        Vault(new).decrypt(token)


def test_key_hint_shows_only_the_tail():
    assert key_hint(OPENAI_KEY) == "...a1b2"


def test_app_refuses_to_boot_with_a_malformed_encryption_key():
    from app.config import Config

    class BadConfig(Config):
        FEEDLENS_ENCRYPTION_KEY = "lixo"

    with pytest.raises(ValueError):
        create_app(BadConfig)


# ---------------------------------------------------------------- access rules

def test_requires_dashboard_password_to_exist(app):
    """Painel sem senha é aberto: gerenciar chaves ali seria um buraco."""
    app.config.update(FEEDLENS_ENCRYPTION_KEY=_fernet_key())
    response = app.test_client().get(BASE)
    assert response.status_code == 403
    assert response.get_json()["code"] == "auth_not_configured"


def test_requires_login(secured_app):
    response = secured_app.test_client().get(BASE)
    assert response.status_code == 401


def test_get_exposes_catalog_and_csrf_token(admin):
    body = admin.get(BASE).get_json()
    assert body["vault_enabled"] is True
    assert {p["id"] for p in body["catalog"]} == {
        "openai", "anthropic", "gemini", "groq", "openrouter"}
    assert body["providers"] == []
    assert body["csrf_token"]


@pytest.mark.parametrize("headers", [{}, {"X-CSRF-Token": "errado"}])
def test_mutations_need_a_valid_csrf_token(admin, headers):
    response = admin.post(BASE, json={"provider": "openai", "api_key": OPENAI_KEY}, headers=headers)
    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf"
    assert db.session.query(ProviderCredential).count() == 0


def test_writes_return_503_when_the_vault_is_off(admin, secured_app):
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY="")
    assert admin.get(BASE).get_json()["vault_enabled"] is False
    response = _create(admin)
    assert response.status_code == 503
    assert response.get_json()["code"] == "vault_disabled"


# ---------------------------------------------------------------------- create

def test_create_encrypts_verifies_and_never_returns_the_key(admin):
    with patch(PROBE, return_value=_ok()) as probe:
        response = _create(admin)
    assert response.status_code == 201
    item = response.get_json()
    assert item["provider"] == "openai" and item["model"] == "gpt-4o-mini"
    assert item["key_hint"] == "...a1b2"
    assert item["priority"] == 1 and item["enabled"] is True
    assert item["last_verified_at"] and item["last_error_kind"] is None
    assert OPENAI_KEY not in response.get_data(as_text=True)
    assert "encrypted_key" not in item

    row = db.session.query(ProviderCredential).one()
    assert OPENAI_KEY not in row.encrypted_key
    assert OPENAI_KEY not in repr(row)
    assert probe.call_args.args[1][0].api_key == OPENAI_KEY  # a chave em claro foi só ao provedor
    assert OPENAI_KEY not in admin.get(BASE).get_data(as_text=True)


def test_create_rejected_when_the_provider_refuses_the_key(admin):
    with patch(PROBE, side_effect=_fail("auth")):
        response = _create(admin)
    assert response.status_code == 422
    assert response.get_json()["code"] == "llm_auth_failed"
    assert db.session.query(ProviderCredential).count() == 0


def test_create_saves_on_transient_failures_and_records_the_kind(admin):
    with patch(PROBE, side_effect=_fail("rate_limit")):
        response = _create(admin)
    assert response.status_code == 201
    item = response.get_json()
    assert item["last_verified_at"] is None
    assert item["last_error_kind"] == "rate_limit"


def test_create_can_skip_verification(admin):
    with patch(PROBE) as probe:
        response = _create(admin, verify=False)
    assert response.status_code == 201
    probe.assert_not_called()
    assert response.get_json()["last_verified_at"] is None


def test_empty_probe_answer_still_counts_as_a_valid_key(admin):
    with patch(PROBE, side_effect=_fail("empty")):
        response = _create(admin)
    assert response.status_code == 201
    assert response.get_json()["last_verified_at"]


def test_priorities_follow_insertion_order_by_default(admin):
    with patch(PROBE, return_value=_ok()):
        _create(admin)
        second = _create(admin, provider="anthropic", api_key=ANTHROPIC_KEY).get_json()
    assert second["priority"] == 2
    assert second["model"] == "claude-haiku-4-5"


def test_duplicate_provider_model_is_409(admin):
    with patch(PROBE, return_value=_ok()):
        assert _create(admin).status_code == 201
        response = _create(admin, api_key="sk-outra-chave-1234567890")
    assert response.status_code == 409
    assert response.get_json()["code"] == "duplicate"


@pytest.mark.parametrize("overrides", [
    {"provider": "bedrock"},
    {"provider": "openai/gpt"},
    {"api_key": "curta"},
    {"api_key": 123},
    {"api_key": "tem espaco no meio da chave"},
    {"model": "openai/../x"},
    {"priority": 0},
    {"priority": True},
    {"enabled": "sim"},
])
def test_create_validates_input(admin, overrides):
    with patch(PROBE, return_value=_ok()):
        response = _create(admin, **overrides)
    assert response.status_code == 400
    assert db.session.query(ProviderCredential).count() == 0


def test_create_rejects_a_non_object_body(admin):
    response = admin.post(BASE, data="[]", content_type="application/json", headers=admin.csrf)
    assert response.status_code == 400


def test_number_of_stored_keys_is_capped(admin, monkeypatch):
    monkeypatch.setattr(store, "MAX_STORED_CREDENTIALS", 1)
    with patch(PROBE, return_value=_ok()):
        assert _create(admin).status_code == 201
        response = _create(admin, provider="anthropic", api_key=ANTHROPIC_KEY)
    assert response.status_code == 409
    assert response.get_json()["code"] == "limit_reached"


# ---------------------------------------------------------- update/delete/order

def _seed_two(admin):
    with patch(PROBE, return_value=_ok()):
        first = _create(admin).get_json()
        second = _create(admin, provider="anthropic", api_key=ANTHROPIC_KEY).get_json()
    return first, second


def test_patch_toggles_enabled_and_changes_priority(admin):
    first, _ = _seed_two(admin)
    response = admin.patch(f"{BASE}/{first['id']}", json={"enabled": False, "priority": 5},
                           headers=admin.csrf)
    assert response.status_code == 200
    assert response.get_json()["enabled"] is False
    assert response.get_json()["priority"] == 5


def test_patch_api_key_rotates_hint_and_reverifies(admin):
    first, _ = _seed_two(admin)
    new_key = "sk-proj-NOVA9999ZZZZ0000q1w2"
    with patch(PROBE, return_value=_ok()) as probe:
        response = admin.patch(f"{BASE}/{first['id']}", json={"api_key": new_key},
                               headers=admin.csrf)
    assert response.status_code == 200
    assert response.get_json()["key_hint"] == "...q1w2"
    assert new_key not in response.get_data(as_text=True)
    probe.assert_called_once()
    row = db.session.get(ProviderCredential, __import__("uuid").UUID(first["id"]))
    assert store.get_vault().decrypt(row.encrypted_key) == new_key


def test_patch_with_refused_key_keeps_the_old_one(admin):
    first, _ = _seed_two(admin)
    with patch(PROBE, side_effect=_fail("auth")):
        response = admin.patch(f"{BASE}/{first['id']}", json={"api_key": "sk-proj-RECUSADA123456"},
                               headers=admin.csrf)
    assert response.status_code == 422
    row = db.session.get(ProviderCredential, __import__("uuid").UUID(first["id"]))
    assert store.get_vault().decrypt(row.encrypted_key) == OPENAI_KEY


def test_patch_model_into_a_duplicate_is_409(admin):
    first, _ = _seed_two(admin)
    with patch(PROBE, return_value=_ok()):
        _create(admin, model="gpt-4o")
    response = admin.patch(f"{BASE}/{first['id']}", json={"model": "gpt-4o"}, headers=admin.csrf)
    assert response.status_code == 409


@pytest.mark.parametrize("body", [{}, {"enabled": "x"}, {"priority": 0}, {"model": "a/b"}])
def test_patch_validates_input(admin, body):
    first, _ = _seed_two(admin)
    response = admin.patch(f"{BASE}/{first['id']}", json=body, headers=admin.csrf)
    assert response.status_code == 400


@pytest.mark.parametrize("credential_id", ["nao-e-uuid", "11111111-1111-1111-1111-111111111111"])
def test_unknown_ids_are_404(admin, credential_id):
    assert admin.patch(f"{BASE}/{credential_id}", json={"enabled": False},
                       headers=admin.csrf).status_code == 404
    assert admin.delete(f"{BASE}/{credential_id}", headers=admin.csrf).status_code == 404
    assert admin.post(f"{BASE}/{credential_id}/test", headers=admin.csrf).status_code == 404


def test_delete_removes_the_key(admin):
    first, _ = _seed_two(admin)
    assert admin.delete(f"{BASE}/{first['id']}", headers=admin.csrf).status_code == 204
    assert [p["provider"] for p in admin.get(BASE).get_json()["providers"]] == ["anthropic"]


def test_reorder_swaps_primary_and_fallback(admin):
    first, second = _seed_two(admin)
    response = admin.post(f"{BASE}/reorder", json={"ids": [second["id"], first["id"]]},
                          headers=admin.csrf)
    assert response.status_code == 200
    assert [p["provider"] for p in response.get_json()["providers"]] == ["anthropic", "openai"]


def test_reorder_with_a_partial_list_keeps_the_rest_after(admin):
    first, second = _seed_two(admin)
    response = admin.post(f"{BASE}/reorder", json={"ids": [second["id"]]}, headers=admin.csrf)
    assert [p["id"] for p in response.get_json()["providers"]] == [second["id"], first["id"]]


@pytest.mark.parametrize("ids", ["x", [1], ["11111111-1111-1111-1111-111111111111"]])
def test_reorder_rejects_bad_ids(admin, ids):
    _seed_two(admin)
    assert admin.post(f"{BASE}/reorder", json={"ids": ids}, headers=admin.csrf).status_code == 400


def test_reorder_rejects_repeated_ids(admin):
    first, _ = _seed_two(admin)
    response = admin.post(f"{BASE}/reorder", json={"ids": [first["id"], first["id"]]},
                          headers=admin.csrf)
    assert response.status_code == 400


# ------------------------------------------------------------------------ test

def test_test_endpoint_records_success_then_failure(admin):
    first, _ = _seed_two(admin)
    with patch(PROBE, return_value=_ok()):
        ok = admin.post(f"{BASE}/{first['id']}/test", headers=admin.csrf).get_json()
    assert ok["ok"] is True and ok["latency_ms"] == 12
    assert ok["provider"]["last_verified_at"]

    with patch(PROBE, side_effect=_fail("auth")):
        bad = admin.post(f"{BASE}/{first['id']}/test", headers=admin.csrf).get_json()
    assert bad["ok"] is False and bad["kind"] == "auth"
    assert bad["provider"]["last_verified_at"] is None
    assert bad["provider"]["last_error_kind"] == "auth"


def test_test_endpoint_reports_an_undecryptable_key(admin, secured_app):
    first, _ = _seed_two(admin)
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY=_fernet_key())  # chave trocada
    response = admin.post(f"{BASE}/{first['id']}/test", headers=admin.csrf)
    assert response.status_code == 409
    assert response.get_json()["code"] == "decrypt_failed"


# ------------------------------------------------------------- operator chain

def _store(provider, model, key, priority, enabled=True):
    row = store.create_credential(provider, key, model, priority, enabled)
    return row


def test_operator_chain_orders_stored_by_priority_then_environment(secured_app):
    secured_app.config.update(GEMINI_API_KEY="g-env-key-123456", LLM_MODELS=[])
    _store("anthropic", "claude-haiku-4-5", ANTHROPIC_KEY, priority=2)
    _store("openai", "gpt-4o-mini", OPENAI_KEY, priority=1)
    chain = store.operator_chain()
    assert [c.model_ref for c in chain] == [
        "openai/gpt-4o-mini", "anthropic/claude-haiku-4-5", "gemini/gemini-2.5-flash-lite"]
    assert [c.source for c in chain] == ["stored", "stored", "server"]


def test_operator_chain_skips_disabled_and_lets_stored_win_over_env(secured_app):
    secured_app.config.update(OPENAI_API_KEY="sk-env-key-123456789", LLM_MODELS=[])
    _store("openai", "gpt-4o-mini", OPENAI_KEY, priority=1)
    _store("anthropic", "claude-haiku-4-5", ANTHROPIC_KEY, priority=2, enabled=False)
    chain = store.operator_chain()
    assert [(c.model_ref, c.source, c.api_key) for c in chain] == [
        ("openai/gpt-4o-mini", "stored", OPENAI_KEY)]


def test_operator_chain_skips_keys_it_cannot_decrypt(secured_app):
    _store("openai", "gpt-4o-mini", OPENAI_KEY, priority=1)
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY=_fernet_key())
    assert store.operator_chain() == []


def test_operator_chain_ignores_stored_keys_when_the_vault_is_off(secured_app):
    _store("openai", "gpt-4o-mini", OPENAI_KEY, priority=1)
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY="")
    assert store.operator_chain() == []


def test_operator_chain_falls_back_to_env_when_the_table_is_unreadable(secured_app):
    secured_app.config.update(OPENAI_API_KEY="sk-env-key-123456789")
    error = OperationalError("select", {}, Exception("no such table"))
    with patch.object(db.session, "scalars", side_effect=error):
        chain = store.operator_chain()
    assert [c.source for c in chain] == ["server"]


def test_analyzer_uses_stored_keys_before_environment_keys(secured_app):
    secured_app.config.update(OPENAI_API_KEY="sk-env-key-123456789")
    _store("anthropic", "claude-haiku-4-5", ANTHROPIC_KEY, priority=1)
    answer = Completion(
        content=json.dumps({"sentiment": "POSITIVO", "is_spam": False, "requested_features": []}),
        model_ref="anthropic/claude-haiku-4-5", source="stored", latency_ms=5)
    with patch("app.services.feedback_analyzer.complete", return_value=answer) as complete:
        result = FeedbackAnalyzer().analyze_feedback("adorei o app")
    chain = complete.call_args.args[1]
    assert [c.model_ref for c in chain] == ["anthropic/claude-haiku-4-5", "openai/gpt-4o-mini"]
    assert chain[0].api_key == ANTHROPIC_KEY
    assert result["analyzed_by"] == "anthropic/claude-haiku-4-5"


# ------------------------------------------------------------------------- cli

def test_rotate_llm_keys_command_moves_everything_to_the_new_key(secured_app):
    old = secured_app.config["FEEDLENS_ENCRYPTION_KEY"]
    new = _fernet_key()
    _store("openai", "gpt-4o-mini", OPENAI_KEY, priority=1)
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY=f"{new},{old}")

    result = secured_app.test_cli_runner().invoke(args=["rotate-llm-keys"])
    assert result.exit_code == 0, result.output
    assert "1 chave(s) recifrada(s)" in result.output

    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY=new)  # a antiga saiu
    assert [c.api_key for c in store.stored_chain()] == [OPENAI_KEY]


def test_rotate_llm_keys_command_fails_when_the_vault_is_off(secured_app):
    secured_app.config.update(FEEDLENS_ENCRYPTION_KEY="")
    result = secured_app.test_cli_runner().invoke(args=["rotate-llm-keys"])
    assert result.exit_code != 0


# ------------------------------------------------- Groq e OpenRouter no cofre

GROQ_KEY = "gsk_AAAA1111BBBB2222c3d4"
OPENROUTER_KEY = "sk-or-v1-EEEE5555FFFF6666e5f6"


def test_vault_stores_openrouter_models_with_a_vendor_slash(admin):
    with patch(PROBE, return_value=_ok("openrouter/google/gemma-4-31b-it:free")) as probe:
        response = _create(admin, provider="openrouter", api_key=OPENROUTER_KEY,
                           model="google/gemma-4-31b-it:free")
    assert response.status_code == 201
    item = response.get_json()
    assert (item["provider"], item["model"]) == ("openrouter", "google/gemma-4-31b-it:free")
    assert OPENROUTER_KEY not in response.get_data(as_text=True)
    assert probe.call_args.args[1][0].model_ref == "openrouter/google/gemma-4-31b-it:free"

    chain = store.stored_chain()
    assert [c.model_ref for c in chain] == ["openrouter/google/gemma-4-31b-it:free"]
    assert chain[0].api_key == OPENROUTER_KEY


def test_vault_uses_each_new_providers_default_model(admin):
    with patch(PROBE, return_value=_ok()):
        groq = _create(admin, provider="groq", api_key=GROQ_KEY).get_json()
        router = _create(admin, provider="openrouter", api_key=OPENROUTER_KEY).get_json()
    assert groq["model"] == "openai/gpt-oss-20b"
    assert router["model"] == "openrouter/free"
    assert [c.model_ref for c in store.stored_chain()] == [
        "groq/openai/gpt-oss-20b", "openrouter/openrouter/free"]


def test_patch_model_follows_the_providers_slash_rule(admin):
    with patch(PROBE, return_value=_ok()):
        router = _create(admin, provider="openrouter", api_key=OPENROUTER_KEY).get_json()
        paid = _create(admin).get_json()

        changed = admin.patch(f"{BASE}/{router['id']}", json={"model": "google/gemma-4-31b-it:free"},
                              headers=admin.csrf)
        assert changed.status_code == 200
        assert changed.get_json()["model"] == "google/gemma-4-31b-it:free"

        # Nos provedores que já existiam a barra continua proibida.
        refused = admin.patch(f"{BASE}/{paid['id']}", json={"model": "vendor/modelo"},
                              headers=admin.csrf)
    assert refused.status_code == 400
    assert refused.get_json()["code"] == "invalid_model"


@pytest.mark.parametrize("provider, model", [
    ("openai", "vendor/modelo"), ("openrouter", "a/b/c"), ("groq", "../modelo")])
def test_create_rejects_malformed_models_per_provider(admin, provider, model):
    response = _create(admin, provider=provider, api_key=GROQ_KEY, model=model)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_provider"
    assert db.session.query(ProviderCredential).count() == 0
