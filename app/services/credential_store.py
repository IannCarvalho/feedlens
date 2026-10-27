"""Chaves de provedor salvas no banco: CRUD cifrado e cadeia de uso.

A cadeia efetiva do operador é ``chaves salvas (por prioridade) -> variáveis
de ambiente``; o BYOK por requisição continua vencendo as duas (veja
``app.llm.credentials``). Tudo aqui exige app context.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from flask import current_app, has_app_context
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.llm import Credential, llm_settings, server_chain
from app.llm.errors import Attempt
from app.llm.providers import get_provider, validate_model_name
from app.llm.vault import Vault, VaultDecryptError, key_hint
from app.models.provider_credential import ProviderCredential

logger = logging.getLogger(__name__)

# Teto de chaves salvas: a tela é de uso humano e cada linha entra na cadeia.
MAX_STORED_CREDENTIALS = 20

# Falhas em que a credencial sai da cadeia até um novo teste: o provedor
# recusou a chave (401/403) ou o modelo não existe na conta (404). Insistir
# não resolve e só custa uma chamada falha por feedback.
PERMANENT_ERROR_KINDS = frozenset({'auth', 'not_found'})


class DuplicateCredentialError(Exception):
    """Já existe uma chave salva para esse provedor + modelo."""


class CredentialLimitError(Exception):
    """Atingiu MAX_STORED_CREDENTIALS."""


def get_vault() -> Vault:
    return Vault.from_settings(current_app.config)


def list_credentials() -> list[ProviderCredential]:
    return list(db.session.scalars(
        select(ProviderCredential).order_by(
            ProviderCredential.priority, ProviderCredential.created_at)))


def get_credential(credential_id: str) -> Optional[ProviderCredential]:
    try:
        parsed = uuid.UUID(str(credential_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return db.session.get(ProviderCredential, parsed)


def _next_priority() -> int:
    highest = db.session.scalar(select(func.max(ProviderCredential.priority)))
    return (highest or 0) + 1


def _exists(provider: str, model: str, *, ignore_id=None) -> bool:
    query = select(ProviderCredential.id).where(
        ProviderCredential.provider == provider, ProviderCredential.model == model)
    if ignore_id is not None:
        query = query.where(ProviderCredential.id != ignore_id)
    return db.session.scalar(query) is not None


def create_credential(provider_id: str, api_key: str, model: Optional[str] = None,
                      priority: Optional[int] = None, enabled: bool = True,
                      verified: Optional[tuple[bool, Optional[str]]] = None,
                      ) -> ProviderCredential:
    """Cifra e salva. ``verified`` = (ok, kind) de um teste feito antes."""
    provider = get_provider(provider_id)
    model = validate_model_name(model, provider) if model else provider.default_model
    if db.session.scalar(select(func.count(ProviderCredential.id))) >= MAX_STORED_CREDENTIALS:
        raise CredentialLimitError()
    if _exists(provider.id, model):
        raise DuplicateCredentialError()

    row = ProviderCredential(
        provider=provider.id,
        model=model,
        priority=priority if priority is not None else _next_priority(),
        enabled=enabled,
        encrypted_key=get_vault().encrypt(api_key),
        key_hint=key_hint(api_key),
    )
    _apply_verification(row, verified)
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise DuplicateCredentialError() from None
    return row


def update_credential(row: ProviderCredential, *, model: Optional[str] = None,
                      api_key: Optional[str] = None, priority: Optional[int] = None,
                      enabled: Optional[bool] = None,
                      verified: Optional[tuple[bool, Optional[str]]] = None,
                      ) -> ProviderCredential:
    if model is not None:
        model = validate_model_name(model, get_provider(row.provider))
        if model != row.model:
            if _exists(row.provider, model, ignore_id=row.id):
                raise DuplicateCredentialError()
            row.model = model
            # Outro modelo = a verificação anterior não vale mais.
            _apply_verification(row, None)
    if api_key is not None:
        row.encrypted_key = get_vault().encrypt(api_key)
        row.key_hint = key_hint(api_key)
        _apply_verification(row, verified)
    elif verified is not None:
        _apply_verification(row, verified)
    if priority is not None:
        row.priority = priority
    if enabled is not None:
        row.enabled = enabled
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise DuplicateCredentialError() from None
    return row


def delete_credential(row: ProviderCredential) -> None:
    db.session.delete(row)
    db.session.commit()


def reorder(ids: list[str]) -> list[ProviderCredential]:
    """Define a ordem da cadeia: ``ids`` primeiro, o restante depois (na ordem atual).

    Levanta ValueError se algum id for desconhecido ou repetido.
    """
    rows = {str(row.id): row for row in list_credentials()}
    if len(set(ids)) != len(ids):
        raise ValueError('ids repetidos')
    unknown = [item for item in ids if item not in rows]
    if unknown:
        raise ValueError('id desconhecido na lista')
    rest = [str(row.id) for row in rows.values() if str(row.id) not in set(ids)]
    for position, credential_id in enumerate([*ids, *rest], start=1):
        rows[credential_id].priority = position
    db.session.commit()
    return list_credentials()


def _apply_verification(row: ProviderCredential,
                        verified: Optional[tuple[bool, Optional[str]]]) -> None:
    if verified is None:
        row.last_verified_at = None
        row.last_error_kind = None
        return
    ok, kind = verified
    row.last_verified_at = datetime.now(timezone.utc) if ok else None
    row.last_error_kind = None if ok else kind


def to_credential(row: ProviderCredential, vault: Vault) -> Credential:
    """Decifra uma linha para uso na cadeia (chave só em memória)."""
    return Credential(
        get_provider(row.provider), row.model, vault.decrypt(row.encrypted_key),
        source='stored', stored_id=str(row.id))


def record_permanent_failure(credential: Credential, attempt: Attempt) -> None:
    """Marca no banco a chave que falhou de um jeito que não se resolve sozinho.

    Dois casos, os de ``PERMANENT_ERROR_KINDS``: ``auth`` (401/403, chave
    vencida ou revogada) e ``not_found`` (404, modelo que não existe mais na
    conta — inclusive aposentado pelo provedor). Insistir neles só gasta uma
    chamada falha (e a latência dela) antes de cair na reserva, enquanto a tela
    de Provedores mostra a última verificação sem erro. Marcada, a linha sai de
    ``stored_chain`` até um teste (tela de Provedores) ou uma chave nova limpar
    o estado.

    Rate limit, timeout e conexão são passageiros e continuam na cadeia.
    Nunca levanta: contabilidade não pode derrubar a análise.
    """
    if (attempt.kind not in PERMANENT_ERROR_KINDS or credential.source != 'stored'
            or not credential.stored_id or not has_app_context()):
        return
    try:
        row = db.session.get(ProviderCredential, uuid.UUID(credential.stored_id))
        if row is None or row.last_error_kind == attempt.kind:
            return
        row.last_verified_at = None
        row.last_error_kind = attempt.kind
        db.session.commit()
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        logger.warning('Could not record the %s failure of %s; the chain is '
                       'unchanged.', attempt.kind, credential.model_ref, exc_info=True)
        return
    # Só o modelo, o motivo e o final da chave: nada de credencial em log.
    logger.warning('Stored key %s (%s) failed with %s; it leaves the chain '
                   'until a new test.', credential.model_ref, row.key_hint,
                   attempt.kind)


def stored_chain() -> list[Credential]:
    """Chaves salvas e habilitadas, na ordem de prioridade. Falhas degradam."""
    vault = get_vault()
    if not vault.enabled:
        return []
    chain: list[Credential] = []
    try:
        rows = db.session.scalars(
            select(ProviderCredential)
            .where(ProviderCredential.enabled.is_(True))
            .order_by(ProviderCredential.priority, ProviderCredential.created_at)
        ).all()
    except SQLAlchemyError:
        # Tabela ainda não migrada ou banco fora: a análise segue com as
        # chaves do ambiente em vez de devolver 500.
        db.session.rollback()
        logger.warning('Could not read stored provider keys; using environment keys only.',
                       exc_info=True)
        return []
    for row in rows:
        if row.last_error_kind in PERMANENT_ERROR_KINDS:
            # O provedor já recusou esta chave (ou o modelo) numa chamada real.
            # Tentar de novo só gastaria uma chamada falha antes da reserva.
            logger.debug('Stored key %s/%s (id=%s) failed with %s; skipped until a '
                         'new test.', row.provider, row.model, row.id, row.last_error_kind)
            continue
        try:
            chain.append(to_credential(row, vault))
        except VaultDecryptError:
            logger.warning('Stored key %s/%s (id=%s) cannot be decrypted; skipped.',
                           row.provider, row.model, row.id)
    return chain


def operator_chain(settings: Optional[Mapping[str, Any]] = None) -> list[Credential]:
    """Cadeia do operador: chaves salvas (prioridade) e depois as do ambiente.

    Entradas repetidas (mesmo provedor/modelo) mantêm a primeira ocorrência,
    ou seja, a chave salva vence a do ambiente.
    """
    settings = llm_settings() if settings is None else settings

    chain: list[Credential] = list(stored_chain()) if has_app_context() else []
    seen = {credential.model_ref for credential in chain}
    for credential in server_chain(settings):
        if credential.model_ref not in seen:
            seen.add(credential.model_ref)
            chain.append(credential)
    return chain
