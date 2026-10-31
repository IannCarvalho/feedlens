#!/bin/sh
# Entrypoint da imagem: aplica as migrations do banco e sobe o gunicorn.
#
# Por que aqui e não no `preDeployCommand` do Render: o free tier do Render
# NÃO suporta pre-deploy command. Rodar a migração no arranque do contêiner
# funciona em qualquer plano e não depende de recurso pago.
#
# Sem isto, um banco novo (ex.: o SQLite efêmero do free tier, recriado a cada
# deploy) sobe sem as tabelas, e a primeira rota que lê uma tabela ausente
# responde 500 (foi o caso de `GET /api/settings/providers` com o cofre ligado).
#
# `flask db upgrade` é idempotente: num banco já migrado, não faz nada. FLASK_APP
# aponta para wsgi:app (definido no ambiente). Uma falha da migração é registrada
# mas NÃO impede o servidor de subir — o pior caso volta a ser um 500 numa rota
# específica, melhor do que um contêiner que não arranca.
set -eu

echo "entrypoint: aplicando migrations (flask db upgrade)..."
if flask db upgrade; then
  echo "entrypoint: migrations aplicadas."
else
  echo "entrypoint: AVISO - 'flask db upgrade' falhou; subindo o servidor mesmo assim." >&2
fi

echo "entrypoint: iniciando gunicorn..."
exec gunicorn wsgi:app
