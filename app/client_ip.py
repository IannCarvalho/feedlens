"""IP real do cliente para rate limit e logs.

Em produção o FeedLens roda no Render atrás da Cloudflare, e TODA requisição
externa chega com ``request.remote_addr == 127.0.0.1`` (o proxy). O
Flask-Limiter com ``get_remote_address`` passa a ver um único cliente, então
os limites por-IP (login, ingestão pública) viram limites GLOBAIS: um visitante
derruba o login de todo mundo e a força-bruta não fica contida.

A correção é ler o IP de um header que o proxy à frente SEMPRE reescreve
(``CF-Connecting-IP`` na Cloudflare), configurado em
``TRUSTED_CLIENT_IP_HEADER``. Sem a configuração, o comportamento é o
histórico: ``request.remote_addr`` direto.

Importante: um header só é confiável quando o proxy à frente o SOBRESCREVE em
toda requisição. Se o app ficasse exposto direto, qualquer cliente poderia
forjar ``CF-Connecting-IP`` e escapar do limite — por isso o default é vazio.
"""
import ipaddress
import logging
import threading

from flask import current_app, request
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)

# Avisa UMA vez por processo quando o header configurado não chega, para não
# inundar o log com uma linha por requisição.
_missing_header_warned = False
_warn_lock = threading.Lock()


def _valid_ip(value: str) -> bool:
    """True se ``value`` é um IPv4/IPv6 bem-formado."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _warn_header_missing_once(header_name: str) -> None:
    """Loga, no máximo uma vez por processo, que o header configurado faltou.

    Só o NOME do header é registrado, nunca valores de headers (poderiam
    conter IPs de clientes ou dados forjados).
    """
    global _missing_header_warned
    if _missing_header_warned:
        return
    with _warn_lock:
        if _missing_header_warned:
            return
        _missing_header_warned = True
    logger.warning(
        'TRUSTED_CLIENT_IP_HEADER=%s está configurado, mas o header não chegou '
        '(ou trouxe um IP inválido) numa requisição: usando request.remote_addr. '
        'Confirme que o proxy à frente reescreve esse header.', header_name)


def get_client_ip() -> str:
    """IP do cliente da requisição atual.

    Com ``TRUSTED_CLIENT_IP_HEADER`` definido e o header presente com um IP
    válido, devolve esse IP; senão cai em ``request.remote_addr`` (via
    ``get_remote_address``) e registra UM warning por processo (só o nome do
    header). Sem a configuração, é exatamente ``get_remote_address``.
    """
    header_name = current_app.config.get('TRUSTED_CLIENT_IP_HEADER', '')
    if not header_name:
        return get_remote_address()

    raw = request.headers.get(header_name, '').strip()
    if raw and _valid_ip(raw):
        return raw

    _warn_header_missing_once(header_name)
    return get_remote_address()
