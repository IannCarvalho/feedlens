# Estágio 1: compila o front Vue (Vite). Só o resultado estático segue para a
# imagem final; Node e node_modules ficam neste estágio.
# Node 24 (>= 24.15, o mínimo do painel), a mesma linha do job "frontend" do CI.
FROM node:24-slim AS frontend

WORKDIR /build/frontend

# Dependências primeiro, para aproveitar o cache de camadas
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# O vite.config.ts grava o build em ../app/static/spa (aqui, /build/app/static/spa)
COPY frontend/ ./
RUN npm run build


# Estágio 2: a aplicação Python
FROM python:3.11-slim

# Evita .pyc e deixa o log sair sem buffer (bom para containers)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências de sistema para o psycopg2-binary em runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Instala as dependências primeiro para aproveitar o cache de camadas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante da aplicação
COPY . .

# O painel (SPA) é gerado, não comitado: vem do estágio 1. Sem ele, a raiz
# do painel responde 503 ("o front ainda não foi compilado").
COPY --from=frontend /build/app/static/spa ./app/static/spa

EXPOSE 5000

# gunicorn servindo a app factory via wsgi:app; bind, workers e threads vêm
# do gunicorn.conf.py (1 worker gthread, respeitando PORT/WEB_CONCURRENCY)
CMD ["gunicorn", "wsgi:app"]
