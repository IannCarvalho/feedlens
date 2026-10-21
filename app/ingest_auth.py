"""Chave de ingestão do ``POST /api/feedbacks``.

Cada ingestão com chave de LLM no servidor custa dinheiro do operador, e o
rate-limit por IP só atrasa quem tem muitos IPs. Com ``INGEST_API_KEY``
definida, o POST exige ``X-API-Key`` e responde 401 ANTES de ler o corpo ou
chamar qualquer provedor (nada é gasto nem gravado por quem não tem chave).

- Sem ``INGEST_API_KEY``: aberto, como antes (dev e instalações antigas).
- Com uma ou mais chaves (vírgula = rotação): só quem enviar uma delas.
- Opcional ``INGEST_ALLOW_BYOK_WITHOUT_KEY``: também aceita, sem chave de
  ingestão, quem trouxer a própria chave de LLM nos headers ``X-LLM-*`` (e
  nesse caso a chave do servidor nunca é usada, veja ``app.llm.credentials``).

Ingestão pública (opt-in, ``INGEST_PUBLIC_ORIGINS``): quando ligada, um POST
sem ``X-API-Key`` é aceito se o header ``Origin`` bater EXATAMENTE com uma das
origens listadas. O ``Origin`` é forjável fora de um navegador, então isto NÃO
é autenticação — só barra o uso a partir de páginas de outros sites. As
barreiras reais contra gasto de LLM ficam em ``app.routes.api``: rate limit por
IP (``RATELIMIT_INGEST_PUBLIC``) e um teto diário global
(``INGEST_PUBLIC_DAILY_CAP``). Uma requisição com ``X-API-Key`` válida ignora
tudo isso e segue o contrato normal.

Os GET de leitura têm a regra própria ``API_READ_TOKEN`` (``app.auth``).
"""
from functools import wraps
from hmac import compare_digest

from flask import current_app, jsonify, request

from app.llm.credentials import HEADER_API_KEY, HEADER_PROVIDER

HEADER_INGEST_KEY = 'X-API-Key'

# Mínimo por chave: a chave é a única barreira contra gasto de LLM.
MIN_INGEST_KEY_LENGTH = 24


def configured_ingest_keys() -> list[str]:
    return [key for key in current_app.config.get('INGEST_API_KEYS') or [] if key]


def _valid_ingest_key(sent: str) -> bool:
    if not sent:
        return False
    sent_bytes = sent.encode()
    # Percorre todas, sem parar na primeira: o tempo não revela qual bateu.
    matches = [compare_digest(sent_bytes, key.encode()) for key in configured_ingest_keys()]
    return any(matches)


def _brings_own_llm_key() -> bool:
    return bool(request.headers.get(HEADER_PROVIDER, '').strip()
                and request.headers.get(HEADER_API_KEY, '').strip())


def normalize_origin(origin: str) -> str:
    """Normaliza uma origem para comparação exata.

    Uma origem é só esquema + host + porta (ex.: ``https://form.exemplo.com``).
    Remove espaços e barra(s) finais; ``https://form.exemplo.com/`` e
    ``https://form.exemplo.com`` viram a mesma coisa. Uma entrada com caminho
    (``https://form.exemplo.com/enviar``) é recusada devolvendo ``''``, porque
    o header ``Origin`` do navegador nunca traz caminho e aceitá-la abriria uma
    comparação que nunca bate (ou, pior, uma que bate por engano).
    """
    if not origin:
        return ''
    cleaned = origin.strip().rstrip('/')
    if not cleaned:
        return ''
    # Sem esquema não é uma origem; com caminho/query/fragmento também não.
    if '://' not in cleaned:
        return ''
    scheme, _, rest = cleaned.partition('://')
    if not scheme or not rest or any(c in rest for c in '/?#'):
        return ''
    return cleaned


def configured_public_origins() -> list[str]:
    """Origens públicas normalizadas; entradas inválidas são descartadas."""
    raw = current_app.config.get('INGEST_PUBLIC_ORIGINS') or []
    return [n for n in (normalize_origin(o) for o in raw) if n]


def public_ingest_enabled() -> bool:
    return bool(configured_public_origins())


def request_origin_allowed() -> bool:
    """True se o header ``Origin`` da requisição está na lista pública."""
    if not public_ingest_enabled():
        return False
    origin = normalize_origin(request.headers.get('Origin', ''))
    return bool(origin) and origin in configured_public_origins()


def has_valid_ingest_key() -> bool:
    """True se a requisição traz uma ``X-API-Key`` válida (chave configurada)."""
    return bool(configured_ingest_keys()) and _valid_ingest_key(
        request.headers.get(HEADER_INGEST_KEY, ''))


def is_public_ingest() -> bool:
    """True quando esta requisição passa pelo caminho PÚBLICO (sem chave).

    Ou seja: a ingestão pública está ligada, o ``Origin`` é permitido e NÃO há
    chave de ingestão válida nem BYOK que já a autorizaria. É o caminho que
    responde pelos limites estritos (rate limit próprio + teto diário).
    """
    if has_valid_ingest_key():
        return False
    if _brings_own_llm_key() and current_app.config.get('INGEST_ALLOW_BYOK_WITHOUT_KEY') \
            and current_app.config.get('BYOK_REQUEST_KEYS_ENABLED', True):
        return False
    return request_origin_allowed()


def ingest_authorized() -> bool:
    """True se esta requisição pode usar a ingestão."""
    if not configured_ingest_keys():
        return True
    if _valid_ingest_key(request.headers.get(HEADER_INGEST_KEY, '')):
        return True
    config = current_app.config
    if (config.get('INGEST_ALLOW_BYOK_WITHOUT_KEY')
            and config.get('BYOK_REQUEST_KEYS_ENABLED', True)
            and _brings_own_llm_key()):
        return True
    # Caminho público opt-in: Origin permitido vale por si, sob limites estritos
    # aplicados na rota (rate limit próprio + teto diário). Origin NÃO é senha.
    return request_origin_allowed()


def ingest_key_required(view):
    """401 JSON, antes de qualquer validação de corpo ou chamada de LLM."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not ingest_authorized():
            response = jsonify({
                'error': f'Envie uma chave de ingestão válida no header {HEADER_INGEST_KEY}.',
                'code': 'ingest_key_required',
            })
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'ApiKey'
            return response
        return view(*args, **kwargs)
    return wrapped
