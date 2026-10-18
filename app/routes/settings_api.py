"""API de gerenciamento das chaves de provedor (BYOK do operador).

Base: ``/api/settings/providers``. Pensada para a tela de configuração, que é
da mesma origem do painel e usa o MESMO cookie de sessão do login:

- exige ``DASHBOARD_PASSWORD`` (sem senha o painel é aberto, e abrir o
  cadastro de chaves pagas a qualquer um não é aceitável) e sessão
  autenticada;
- métodos que alteram estado exigem ``X-CSRF-Token`` igual ao token da sessão
  (entregue no GET), mesmo com ``SameSite=Lax``;
- a chave só entra pelo POST/PATCH e nunca sai: as respostas trazem apenas
  ``key_hint`` (``...a1b2``).
"""
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

from app.auth import auth_enabled, is_authenticated
from app.csrf import CSRF_HEADER, csrf_is_valid, csrf_token
from app.extensions import limiter
from app.llm import (
    PROVIDERS,
    Credential,
    LLMRequestError,
    complete,
    llm_settings,
    server_chain,
)
from app.llm.credentials import API_KEY_RE
from app.llm.providers import get_provider, validate_model_name
from app.llm.vault import VaultDecryptError
from app.services import credential_store as store

settings_api = Blueprint('settings_api', __name__)
logger = logging.getLogger(__name__)


def _error(message: str, status: int, code: str, **extra):
    return jsonify({'error': message, 'code': code, **extra}), status


def guarded(view):
    """Sessão do painel + CSRF, com respostas JSON (nada de redirect)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not auth_enabled():
            return _error(
                'Defina DASHBOARD_PASSWORD para gerenciar chaves de provedor.',
                403, 'auth_not_configured')
        if not is_authenticated():
            return _error('Faça login no painel.', 401, 'unauthenticated')
        if not csrf_is_valid():
            return _error(f'Header {CSRF_HEADER} ausente ou inválido.', 403, 'csrf')
        return view(*args, **kwargs)
    return wrapped


def vault_required(view):
    """Escritas só com o cofre ligado (nunca grava chave em claro)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not store.get_vault().enabled:
            return _error(
                'Cofre de chaves desligado: defina FEEDLENS_ENCRYPTION_KEY.',
                503, 'vault_disabled')
        return view(*args, **kwargs)
    return wrapped


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _validate_api_key(value) -> Optional[str]:
    if not isinstance(value, str) or not API_KEY_RE.match(value.strip()):
        return None
    return value.strip()


def _validate_priority(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        return None
    return value


def _test_limit():
    return current_app.config.get('RATELIMIT_SETTINGS_TEST', '10 per minute')


def probe(credential: Credential) -> tuple[bool, Optional[str], Optional[int]]:
    """Uma chamada mínima para saber se a chave funciona: (ok, kind, latency_ms)."""
    settings = llm_settings()
    try:
        completion = complete(
            [{'role': 'user', 'content': 'Reply with the single word: ok'}],
            [credential],
            max_tokens=16,
            timeout=settings.get('LLM_TIMEOUT', 30),
            max_retries=0,
        )
    except LLMRequestError as e:
        kind = e.attempts[-1].kind if e.attempts else 'error'
        # Resposta vazia com um limite de 16 tokens (modelos que "pensam")
        # prova que a chave foi aceita: conta como verificada.
        if kind == 'empty':
            return True, None, None
        return False, kind, None
    return True, None, completion.latency_ms


def _verification(body: dict, credential: Credential):
    """Aplica ``verify`` (padrão true). Devolve (resultado, resposta_de_erro)."""
    if body.get('verify', True) is False:
        return None, None
    ok, kind, _ = probe(credential)
    if not ok and kind == 'auth':
        return None, _error('O provedor recusou essa chave.', 422, 'llm_auth_failed')
    return (ok, kind), None


@settings_api.route('/providers', methods=['GET'])
@guarded
def list_providers():
    """Catálogo, cadeia do ambiente (sem chaves) e as chaves salvas."""
    vault_enabled = store.get_vault().enabled
    settings = llm_settings()
    return jsonify({
        'vault_enabled': vault_enabled,
        'csrf_token': csrf_token(),
        'catalog': [
            {
                'id': provider.id,
                'label': provider.label,
                'default_model': provider.default_model,
                'server_key_configured': bool(settings.get(provider.env_key)),
            }
            for provider in PROVIDERS.values()
        ],
        'environment_chain': [c.model_ref for c in server_chain(settings)],
        'providers': [row.to_dict() for row in store.list_credentials()]
        if vault_enabled else [],
    })


@settings_api.route('/providers', methods=['POST'])
@guarded
@vault_required
@limiter.limit(_test_limit)
def create_provider():
    body = _json_object()
    if body is None:
        return _error('O corpo deve ser um objeto JSON.', 400, 'invalid_body')

    try:
        provider = get_provider(str(body.get('provider', '')))
        model = (validate_model_name(body['model'], provider) if body.get('model')
                 else provider.default_model)
    except ValueError as e:
        return _error(str(e), 400, 'invalid_provider')
    api_key = _validate_api_key(body.get('api_key'))
    if api_key is None:
        return _error('api_key ausente ou em formato inválido.', 400, 'invalid_api_key')
    priority = None
    if 'priority' in body:
        priority = _validate_priority(body['priority'])
        if priority is None:
            return _error('priority deve ser um inteiro de 1 a 1000.', 400, 'invalid_priority')
    enabled = body.get('enabled', True)
    if not isinstance(enabled, bool):
        return _error('enabled deve ser booleano.', 400, 'invalid_enabled')

    verified, failure = _verification(body, Credential(provider, model, api_key))
    if failure:
        return failure
    try:
        row = store.create_credential(
            provider.id, api_key, model, priority, enabled, verified)
    except store.DuplicateCredentialError:
        return _error('Já existe uma chave para esse provedor e modelo.', 409, 'duplicate')
    except store.CredentialLimitError:
        return _error(
            f'Limite de {store.MAX_STORED_CREDENTIALS} chaves atingido.', 409, 'limit_reached')
    logger.info('Provider key saved: %s/%s (id=%s)', row.provider, row.model, row.id)
    return jsonify(row.to_dict()), 201


@settings_api.route('/providers/<credential_id>', methods=['PATCH'])
@guarded
@vault_required
@limiter.limit(_test_limit)
def update_provider(credential_id):
    row = store.get_credential(credential_id)
    if row is None:
        return _error('Chave não encontrada.', 404, 'not_found')
    body = _json_object()
    if body is None:
        return _error('O corpo deve ser um objeto JSON.', 400, 'invalid_body')

    changes: dict = {}
    if 'enabled' in body:
        if not isinstance(body['enabled'], bool):
            return _error('enabled deve ser booleano.', 400, 'invalid_enabled')
        changes['enabled'] = body['enabled']
    if 'priority' in body:
        priority = _validate_priority(body['priority'])
        if priority is None:
            return _error('priority deve ser um inteiro de 1 a 1000.', 400, 'invalid_priority')
        changes['priority'] = priority
    if 'model' in body:
        try:
            changes['model'] = validate_model_name(str(body['model']), get_provider(row.provider))
        except ValueError as e:
            return _error(str(e), 400, 'invalid_model')
    new_key = None
    if 'api_key' in body:
        new_key = _validate_api_key(body['api_key'])
        if new_key is None:
            return _error('api_key em formato inválido.', 400, 'invalid_api_key')
        changes['api_key'] = new_key
    if not changes:
        return _error('Nada para alterar.', 400, 'no_changes')

    if new_key is not None:
        credential = Credential(
            get_provider(row.provider), changes.get('model', row.model), new_key)
        verified, failure = _verification(body, credential)
        if failure:
            return failure
        changes['verified'] = verified
    try:
        store.update_credential(row, **changes)
    except store.DuplicateCredentialError:
        return _error('Já existe uma chave para esse provedor e modelo.', 409, 'duplicate')
    return jsonify(row.to_dict())


@settings_api.route('/providers/<credential_id>', methods=['DELETE'])
@guarded
def delete_provider(credential_id):
    row = store.get_credential(credential_id)
    if row is None:
        return _error('Chave não encontrada.', 404, 'not_found')
    store.delete_credential(row)
    logger.info('Provider key deleted: id=%s', credential_id)
    return '', 204


@settings_api.route('/providers/reorder', methods=['POST'])
@guarded
def reorder_providers():
    body = _json_object()
    ids = body.get('ids') if body else None
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return _error('ids deve ser uma lista de ids.', 400, 'invalid_ids')
    try:
        rows = store.reorder(ids)
    except ValueError as e:
        return _error(str(e), 400, 'invalid_ids')
    return jsonify({'providers': [row.to_dict() for row in rows]})


@settings_api.route('/providers/<credential_id>/test', methods=['POST'])
@guarded
@vault_required
@limiter.limit(_test_limit)
def test_provider(credential_id):
    """Testa a chave salva (1 chamada mínima paga) e registra o resultado."""
    row = store.get_credential(credential_id)
    if row is None:
        return _error('Chave não encontrada.', 404, 'not_found')
    try:
        credential = store.to_credential(row, store.get_vault())
    except VaultDecryptError:
        return _error(
            'Não foi possível decifrar a chave: FEEDLENS_ENCRYPTION_KEY mudou?',
            409, 'decrypt_failed')
    ok, kind, latency_ms = probe(credential)
    store.update_credential(row, verified=(ok, kind))
    return jsonify({
        'ok': ok,
        'kind': kind,
        'latency_ms': latency_ms,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'provider': row.to_dict(),
    })
