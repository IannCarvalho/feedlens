"""
Autenticação do dashboard (rotas web)

Modelo de acesso: senha única compartilhada, definida em
``DASHBOARD_PASSWORD``. O dashboard é uma visão de leitura destinada
ao dono do produto, então um sistema completo de contas seria
excessivo. A sessão é guardada num cookie assinado pela ``SECRET_KEY``
do Flask.

Fail-safe: se ``DASHBOARD_PASSWORD`` não estiver definida, a proteção
fica DESLIGADA e o dashboard permanece aberto (comportamento histórico,
útil em desenvolvimento). Em produção sem senha, um aviso é registrado.
"""
from functools import wraps
from hmac import compare_digest
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

from app.csrf import csrf_is_valid, csrf_token, rotate_csrf_token
from app.extensions import limiter

auth = Blueprint('auth', __name__)

# Chave usada no cookie de sessão para marcar o usuário autenticado.
SESSION_KEY = 'dashboard_authenticated'


def safe_next_url(target: str | None) -> str:
    """Devolve ``target`` apenas se for um caminho LOCAL; senão, o dashboard.

    Checar só ``startswith('/')`` não basta: ``//evil.com`` é uma URL
    relativa ao protocolo (host externo) e os navegadores tratam ``\\`` como
    ``/`` e descartam tab/quebra de linha, então ``/\\evil.com`` e
    ``/\\t/evil.com`` também viram ``//evil.com``.
    """
    fallback = '/'
    if not target:
        return fallback
    if '\\' in target or any(ord(char) < 32 for char in target):
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return fallback
    if not target.startswith('/') or target.startswith('//'):
        return fallback
    return target


def _configured_password() -> str | None:
    """Retorna a senha configurada do dashboard, ou None se ausente."""
    password = current_app.config.get('DASHBOARD_PASSWORD')
    return password or None


def auth_enabled() -> bool:
    """A proteção só é aplicada quando há uma senha configurada."""
    return _configured_password() is not None


def is_authenticated() -> bool:
    """True se a sessão atual já passou pelo login (ou se a auth está off)."""
    if not auth_enabled():
        return True
    return bool(session.get(SESSION_KEY))


def login_required(view):
    """Protege uma rota web: redireciona para o login quando não autenticado."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _configured_api_token() -> str | None:
    """Token de leitura da API (``API_READ_TOKEN``), ou None se ausente."""
    return current_app.config.get('API_READ_TOKEN') or None


def read_api_open() -> bool:
    """A API de leitura só fica aberta se NADA estiver configurado (dev)."""
    return not auth_enabled() and _configured_api_token() is None


def _bearer_token() -> str:
    scheme, _, value = request.headers.get('Authorization', '').partition(' ')
    return value.strip() if scheme.lower() == 'bearer' else ''


def api_read_allowed() -> bool:
    """True se a requisição pode ler a API.

    Aceita, nesta ordem: API aberta (nada configurado, comportamento
    histórico de dev), ``Authorization: Bearer <API_READ_TOKEN>`` (clientes
    servidor-a-servidor) ou a sessão do painel (frontend da mesma origem).
    """
    if read_api_open():
        return True
    token = _configured_api_token()
    sent = _bearer_token()
    if token and sent and compare_digest(sent.encode(), token.encode()):
        return True
    return auth_enabled() and bool(session.get(SESSION_KEY))


def api_read_required(view):
    """Protege os GET da API de feedbacks: 401 JSON (sem redirect) se negado."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not api_read_allowed():
            response = jsonify({
                'error': 'Autenticação necessária para ler os feedbacks.',
                'code': 'unauthenticated',
            })
            response.status_code = 401
            response.headers['WWW-Authenticate'] = 'Bearer'
            return response
        return view(*args, **kwargs)
    return wrapped


def _no_store(response):
    """Estado de sessão e token CSRF nunca vão para cache."""
    response.headers['Cache-Control'] = 'no-store'
    return response


def _login_error(message: str, code: str, status: int):
    return _no_store(jsonify({'error': message, 'code': code})), status


@auth.route('/auth/session', methods=['GET'])
def session_state():
    """Estado da sessão do painel para o SPA. Rota pública: o login precisa dela.

    ``authenticated`` é "pode usar o painel" (logado, ou login desligado), a
    mesma regra do ``login_required``. Fica fora de ``/api/*`` de propósito: é
    interna ao painel (mesma origem) e não entra no CORS da API pública.

    ``analysis_configured`` diz se há ALGUMA análise de IA possível no servidor
    (chave de ambiente ou chave salva no cofre). É só um booleano: não revela
    quais provedores nem nenhuma chave, e serve para o painel avisar quando
    nenhum provedor está configurado. Nunca derruba a rota: qualquer erro ao
    resolver a cadeia (ex.: banco indisponível) vira ``True`` para não assustar
    com um alarme falso quando o estado real é desconhecido.
    """
    return _no_store(jsonify({
        'auth_enabled': auth_enabled(),
        'authenticated': is_authenticated(),
        'analysis_configured': _analysis_configured(),
        'csrf_token': csrf_token(),
    }))


def _analysis_configured() -> bool:
    """True quando há ao menos uma credencial de LLM utilizável no servidor."""
    # Import tardio: não carregar a camada de LLM só para responder a sessão.
    from app.services.credential_store import operator_chain
    try:
        return len(operator_chain()) > 0
    except Exception:  # noqa: BLE001 - sinal de UI, nunca o caminho crítico
        # Estado desconhecido (ex.: banco fora): não dispara o aviso de
        # "sem provedor", que seria um falso alarme.
        return True


@auth.route('/login', methods=['GET', 'POST'])
# Só o POST conta: limita a força-bruta da senha sem travar o GET da tela.
@limiter.limit(
    lambda: current_app.config.get('RATELIMIT_LOGIN', '10 per minute'),
    methods=['POST'],
)
def login():
    """Login do painel: o GET entrega a tela (SPA Vue) e o POST confere a senha.

    O POST é JSON (``{"password": ...}`` + header ``X-CSRF-Token``, com o token
    de ``GET /auth/session``) e responde JSON, sem redirect: o SPA navega para
    o ``next`` devolvido aqui, já filtrado por ``safe_next_url``. Toda a
    verificação (CSRF, senha em tempo constante, rate limit, sessão nova)
    continua no servidor; o Vue só desenha o formulário.
    """
    next_url = safe_next_url(request.args.get('next'))

    if request.method == 'GET':
        if not auth_enabled() or is_authenticated():
            return redirect(next_url)
        # Import tardio: app.routes.spa importa este módulo (login_required).
        from app.routes.spa import serve_index
        return serve_index()

    if not auth_enabled() or is_authenticated():
        return _no_store(jsonify({'ok': True, 'next': next_url}))
    if not csrf_is_valid():
        return _login_error(
            'Sua sessão expirou. Recarregue a página e tente entrar de novo.', 'csrf', 400)

    body = request.get_json(silent=True)
    submitted = body.get('password') if isinstance(body, dict) else None
    if not isinstance(submitted, str):
        submitted = ''
    expected = _configured_password() or ''
    # compare_digest evita vazar o tamanho/igualdade por timing.
    if not compare_digest(submitted.encode(), expected.encode()):
        return _login_error(
            'Senha incorreta. Confira a senha de acesso do painel e tente de novo.',
            'invalid_password', 401)

    # Sessão nova no login (evita fixação de sessão) e token CSRF novo.
    # "Permanente" = expira após PERMANENT_SESSION_LIFETIME, em vez
    # dos 31 dias default de um cookie assinado.
    session.clear()
    session.permanent = True
    session[SESSION_KEY] = True
    rotate_csrf_token()
    return _no_store(jsonify({'ok': True, 'next': next_url}))


@auth.route('/logout', methods=['GET', 'POST'])
def logout():
    """Encerra a sessão do painel e volta para o login.

    Sem CSRF de propósito: logout é idempotente e seguro — o pior que um
    "logout forjado" faria é deslogar a própria vítima, sem ler nem alterar
    dado nenhum. Em troca, nunca mostramos a tela crua de 400: digitar
    /logout na barra (GET) ou um POST sem token válido (sessão expirada,
    servidor reiniciado com SECRET_KEY nova) sempre resulta em sair e ser
    redirecionado, que é exatamente o que o usuário quer ao clicar em Sair.
    """
    session.clear()
    return redirect(url_for('auth.login'))
