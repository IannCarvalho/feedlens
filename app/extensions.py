"""Extensões Flask compartilhadas que precisam de uma instância única.

Fica num módulo próprio para quebrar o import circular entre o app factory
(``app/__init__.py``) e os blueprints que decoram rotas com o limitador.
"""
from flask_limiter import Limiter
from flask_cors import CORS

from app.client_ip import get_client_ip

# Limitador global. Sem default_limits: cada rota declara o seu próprio
# limite via decorator; rotas não decoradas ficam ilimitadas. A key_func
# resolve o IP real do cliente (header confiável atrás de proxy, senão
# remote_addr) — ver app/client_ip.py.
limiter = Limiter(key_func=get_client_ip)

# CORS aplicado apenas às rotas da API (ver init no app factory).
cors = CORS()
