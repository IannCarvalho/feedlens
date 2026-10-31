# Versão do painel (Vue) embutida na imagem. O painel vive no repositório
# IannCarvalho/feedlens-panel e é publicado como release (tarball + sha256).
# Para atualizar: troque as DUAS linhas abaixo pelos valores do release novo
# (o .sha256 do release traz o checksum) ou deixe o workflow panel-bump.yml
# abrir o PR. O build FALHA se o checksum não bater com o arquivo baixado.
ARG PANEL_VERSION=0.2.1
ARG PANEL_SHA256=862b04aa0438ecbd30e3dc481b4374e3a06afdf67fefb37dfe3938674b7302bd


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

# Entrypoint: aplica as migrations (flask db upgrade) e então sobe o gunicorn.
# Feito no arranque do contêiner porque o free tier do Render não suporta
# pre-deploy command. bind/workers/threads vêm do gunicorn.conf.py.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
# FLASK_APP é necessário para o `flask db upgrade` do entrypoint achar a app.
ENV FLASK_APP=wsgi:app
CMD ["/usr/local/bin/entrypoint.sh"]
