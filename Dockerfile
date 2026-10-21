# Versão do painel (Vue) embutida na imagem. O painel vive no repositório
# IannCarvalho/feedlens-panel e é publicado como release (tarball + sha256).
# Para atualizar: troque as DUAS linhas abaixo pelos valores do release novo
# (o .sha256 do release traz o checksum) ou deixe o workflow panel-bump.yml
# abrir o PR. O build FALHA se o checksum não bater com o arquivo baixado.
ARG PANEL_VERSION=0.2.0
ARG PANEL_SHA256=ffea64227927f0730e641d90ac357331ac6975006e0f57bd08d3a83effac33af


# Estágio 1: baixa o painel já compilado, confere o checksum e extrai.
# ADD --checksum exige BuildKit (Docker 23 ou superior, o padrão atual).
FROM alpine:3.21 AS panel

ARG PANEL_VERSION
ARG PANEL_SHA256

ADD --checksum=sha256:${PANEL_SHA256} \
    https://github.com/IannCarvalho/feedlens-panel/releases/download/v${PANEL_VERSION}/feedlens-panel-v${PANEL_VERSION}.tgz \
    /tmp/panel.tgz

RUN mkdir /spa && tar -xzf /tmp/panel.tgz -C /spa


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

# O painel (SPA) não é comitado aqui: vem do release do estágio 1. Sem ele, a
# raiz do painel responde 503 ("o painel ainda não foi instalado").
COPY --from=panel /spa ./app/static/spa

EXPOSE 5000

# gunicorn servindo a app factory via wsgi:app; bind, workers e threads vêm
# do gunicorn.conf.py (1 worker gthread, respeitando PORT/WEB_CONCURRENCY)
CMD ["gunicorn", "wsgi:app"]
