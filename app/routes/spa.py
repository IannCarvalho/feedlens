"""Serve o front Vue (build do Vite) como o painel padrão, na raiz.

O SPA é servido em ``/`` e assume o roteamento do painel (Vue Router em
history mode, base ``/``); os assets saem de ``/static/spa`` (Vite ``base``,
gerados pelo ``vite build``). As rotas Jinja antigas (``/dashboard``,
``/feedbacks``, ``/feedback/<id>``) viram redirects para o SPA, preservando
links salvos e o link do e-mail do resumo semanal (ver ``app/routes/web.py``).

A tela de login também é do SPA, mas é a única rota PÚBLICA dele: quem a
entrega é ``GET /login`` (``app/auth.py``), via ``serve_index``. As demais
exigem sessão (``login_required``).

Se o painel ainda não foi instalado em ``app/static/spa`` (no Docker ele vem do
release do feedlens-panel; fora dele, ``python scripts/fetch_panel.py``), o painel
responde um
aviso claro (503) em vez de 404, para não confundir com rota inexistente.

O catch-all entrega o ``index.html`` para qualquer caminho de navegação que
não seja de um blueprint próprio (API, sessão, health, login/logout,
estáticos), para que um reload num deep-link do SPA (ex.: ``/feedbacks/<id>``)
funcione.
"""
import os

from flask import Blueprint, abort, current_app, send_from_directory

from app.auth import login_required

spa = Blueprint('spa', __name__)

# Prefixos que NÃO são do SPA: têm blueprints/arquivos próprios e nunca devem
# receber o index. O catch-all ignora qualquer caminho sob eles.
_RESERVED_PREFIXES = ('api/', 'auth/', 'health', 'login', 'logout', 'static/')


def _spa_dir() -> str:
    return os.path.join(current_app.static_folder, 'spa')


def serve_index():
    """Entrega o ``index.html`` do build, ou 503 explicando como gerá-lo."""
    index = os.path.join(_spa_dir(), 'index.html')
    if not os.path.isfile(index):
        abort(
            503,
            description=(
                'O painel ainda não foi instalado. Rode '
                '"python scripts/fetch_panel.py" para baixar o release do painel '
                'em app/static/spa (ou compile o repositório feedlens-panel).'
            ),
        )
    return send_from_directory(_spa_dir(), 'index.html')


@spa.route('/')
@login_required
def serve_root():
    """O SPA é o painel padrão."""
    return serve_index()


@spa.route('/<path:subpath>')
@login_required
def serve_spa(subpath: str):
    """Entrega o index para deep-links do SPA; o roteamento fino é do Vue Router.

    Caminhos reservados (API, sessão, health, login/logout, estáticos) têm
    rotas próprias e, se chegarem aqui, é porque não existem: devolve 404 em
    vez de mascarar com o index.
    """
    if subpath.startswith(_RESERVED_PREFIXES):
        abort(404)
    return serve_index()
