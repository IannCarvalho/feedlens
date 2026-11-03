"""Headers de segurança HTTP aplicados a toda resposta (``after_request``).

Ligado por ``SECURITY_HEADERS_ENABLED`` (true por padrão). Nenhum header já
definido pela rota é sobrescrito: a rota tem a palavra final (ex.: uma resposta
que precise de um CSP próprio).

A CSP é deliberadamente ENXUTA para não quebrar o SPA Vue (Vite): não restringe
``script-src`` nem ``style-src`` (o bundle e os estilos inline do build
quebrariam). Fica nas diretivas que endurecem sem risco para um SPA de mesma
origem: nega enquadramento (``frame-ancestors``), fixa a base de URLs relativas
(``base-uri``), bloqueia plugins/objetos (``object-src``) e restringe o destino
de formulários (``form-action``). O clickjacking fica coberto duas vezes
(``X-Frame-Options`` + ``frame-ancestors``), de propósito, por navegadores
antigos.

HSTS só entra quando a requisição é HTTPS (``request.is_secure`` ou o header do
proxy ``X-Forwarded-Proto: https``): mandá-lo por HTTP é inútil e, em dev
local, prenderia o navegador a HTTPS num host que não o serve.
"""
from flask import request

# Headers estáticos (iguais em toda resposta). O CSP vai aqui porque não
# depende do request; o HSTS é condicional e entra à parte.
_STATIC_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Content-Security-Policy': (
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; "
        "form-action 'self'"
    ),
}

_HSTS_HEADER = 'Strict-Transport-Security'
_HSTS_VALUE = 'max-age=31536000; includeSubDomains'


def _is_https() -> bool:
    """True se a requisição chegou por HTTPS (direto ou via proxy)."""
    if request.is_secure:
        return True
    return request.headers.get('X-Forwarded-Proto', '').lower() == 'https'


def apply_security_headers(response):
    """Adiciona os headers de segurança sem sobrescrever os já definidos."""
    for name, value in _STATIC_HEADERS.items():
        response.headers.setdefault(name, value)
    # HSTS só faz sentido sob HTTPS.
    if _is_https():
        response.headers.setdefault(_HSTS_HEADER, _HSTS_VALUE)
    return response
