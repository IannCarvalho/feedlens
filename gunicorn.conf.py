"""Configuração do gunicorn, lida automaticamente da raiz do projeto.

Padrão: 1 worker ``gthread`` com várias threads. O trabalho da app é I/O
(banco e chamadas à LLM, que levam segundos), então threads atendem
requisições em paralelo sem que uma análise lenta prenda o processo. Com um
processo só, o agendador do resumo mensal (``SCHEDULER_ENABLED``) e o
rate-limit em ``memory://`` ficam consistentes.

Ao escalar com ``WEB_CONCURRENCY`` > 1: desligue o agendador no web
(``SCHEDULER_ENABLED=false``), rode ``python -m app.scheduling`` à parte e
aponte ``RATELIMIT_STORAGE_URI`` para um Redis.
"""
import os

# Render, Railway e afins injetam PORT; localmente fica 5000.
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '1'))
worker_class = 'gthread'
threads = int(os.environ.get('GUNICORN_THREADS', '8'))
# Uma análise soma timeout x tentativas da LLM; a folga evita matar o worker.
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
accesslog = '-'
