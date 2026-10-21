# FeedLens

[![CI](https://github.com/IannCarvalho/feedlens/actions/workflows/ci.yml/badge.svg)](https://github.com/IannCarvalho/feedlens/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Uma lente sobre o feedback dos seus usuários: análise automática de feedback com Inteligência Artificial, usando Python, Flask e LLMs.

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura](#arquitetura)
- [Componentes e repositórios](#componentes-e-repositórios)
- [Destaques técnicos](#destaques-técnicos)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Formulário público (`feedlens-form`)](#formulário-público-feedlens-form)
- [Tecnologias Utilizadas](#tecnologias-utilizadas)
- [Configuração do Ambiente](#configuração-do-ambiente)
- [Endpoints da API](#endpoints-da-api)
- [Qualidade da classificação](#qualidade-da-classificação)
- [Interface Web](#interface-web)
- [Funcionalidade de E-mail](#funcionalidade-de-e-mail)
- [Testes e CI](#testes-e-ci)
- [Considerações de Desenvolvimento](#considerações-de-desenvolvimento)
- [Status e próximos passos](#status-e-próximos-passos)
- [Licença](#licença)

## Visão geral

O FeedLens recebe feedbacks de usuários, classifica-os por sentimento, detecta spam e identifica as funcionalidades mais pedidas — e disponibiliza tudo em um dashboard web e em um resumo semanal por e-mail. Ele resolve o gargalo de analisar manualmente feedbacks que chegam de muitos canais (atendimento, comunidades, redes sociais).

A análise é **BYOK (bring your own key) e multi-provedor**: funciona com OpenAI, Anthropic, Google Gemini, Groq ou OpenRouter, usando as chaves do operador (com fallback entre provedores) ou a chave que o próprio cliente envia na requisição.

O FeedLens tem três partes, cada uma no seu repositório. **Este repositório é o backend** (Flask): API, análise por IA, banco, login e e-mail. O **painel** (Vue 3), onde a equipe vê os números, revisa e corrige a análise, vive em [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel) e é embutido na imagem deste backend a partir de um release com checksum. O **formulário público** (Angular), onde o cliente final escreve o feedback, vive em [feedlens-form](https://github.com/IannCarvalho/feedlens-form). Os dois falam com este backend, e a IA nunca é chamada pelo navegador: ela só roda no servidor.

A aplicação é responsável por:

1. **Classificação de feedback** por sentimento (POSITIVO, NEGATIVO, INCONCLUSIVO)
2. **Identificação de funcionalidades solicitadas** nos feedbacks
3. **Detecção de spam** nos feedbacks, que ficam fora das listas e dos gráficos, mas podem ser revisados e corrigidos no painel
4. **Reprocessamento** dos feedbacks que a IA não conseguiu analisar (cota excedida, timeout etc.), que ficam marcados como "não analisados" em vez de se passarem por uma opinião
5. **Dashboard web** com estatísticas e visualizações
6. **Resumo semanal** enviado por e-mail aos stakeholders

## Arquitetura

```mermaid
flowchart TD
    customer([Cliente final])
    team([Equipe])
    form["feedlens-form<br/>(Angular 22 · formulário público)"]
    panel["feedlens-panel<br/>(Vue 3 · painel, embutido na imagem)"]
    api["Flask<br/>(API /api · login · serve o painel)"]
    llmlayer["app/llm<br/>(cadeia de provedores · LiteLLM)"]
    llm{{"OpenAI · Anthropic · Gemini · Groq · OpenRouter"}}
    db[("PostgreSQL ou SQLite")]
    mail["SMTP<br/>(resumo semanal)"]

    customer --> form
    form -- "POST /api/feedbacks {id, feedback}" --> api
    team --> panel
    panel -- "GET /api/stats · /api/feedbacks<br/>(sessão + CSRF nas alterações)" --> api
    api --> llmlayer
    llmlayer -- "qual chave e modelo respondem" --> llm
    api --> db
    api -. resumo semanal .-> mail

    classDef py fill:#2b6cb0,stroke:#1a365d,color:#fff;
    classDef js fill:#b7791f,stroke:#744210,color:#fff;
    class api,llmlayer py;
    class form,panel js;
```

O navegador nunca fala com a IA nem com o banco: o **formulário** só envia o texto, o **painel** só lê e corrige pela API, e o **Flask** decide qual provedor responde, valida a saída do modelo e é a única fonte de verdade sobre o banco.

> Para o fluxo ponta a ponta de um feedback, as decisões de arquitetura (por que dois fronts, por que a IA falhar não perde o feedback, como a cadeia de chaves funciona) e o modelo de segurança, veja o [**ARCHITECTURE.md**](ARCHITECTURE.md).

## Componentes e repositórios

| Onde | Stack | Papel |
|---|---|---|
| [`app/`](app/) (este repositório) | Python 3.10+, Flask, SQLAlchemy | API REST, login do painel, análise, estatísticas e e-mail. Serve o painel já compilado. |
| [`app/llm/`](app/llm/) | LiteLLM, cryptography (Fernet) | Catálogo de provedores, cadeia de credenciais (requisição, cofre, ambiente) e a chamada à LLM. |
| [`evals/`](evals/) | Python | Avaliação do prompt de classificação contra casos de teste (chama a LLM de verdade). |
| [`migrations/`](migrations/) | Alembic (Flask-Migrate) | Esquema do banco, versionado. |
| [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel) | Vue 3, Vite, TypeScript, PrimeVue, Pinia, Chart.js | Painel da equipe: visão geral, feedbacks, detalhe, provedores e login. Publicado como release (tarball + sha256) que a imagem deste backend baixa. |
| [feedlens-form](https://github.com/IannCarvalho/feedlens-form) | Angular 22, TypeScript, Vitest, nginx | Formulário público do cliente final: site estático independente, com CI, Docker e deploy próprios. |

Os dois repositórios de front nasceram das pastas `frontend/` e `feedback-form/` deste repositório, com o histórico preservado. O estado de antes da separação está na tag [`pre-segregacao`](https://github.com/IannCarvalho/feedlens/tree/pre-segregacao).

## Destaques técnicos

- **Multi-provedor via LiteLLM, com allowlist** — OpenAI, Anthropic, Gemini, Groq e OpenRouter. Prefixos que usariam credenciais do servidor (`bedrock/`, `ollama/`) são recusados, e o provedor sempre sai do nosso prefixo, nunca do que vier num header.
- **BYOK em três níveis, com fallback** — a chave vem, por precedência, da requisição (`X-LLM-*`), do cofre cifrado ou do ambiente. Se o provedor principal falha, a análise segue para o próximo da cadeia.
- **A falha da IA não perde o feedback** — com cota excedida ou timeout, o feedback é gravado como "não analisado", com o motivo, fora dos números do dashboard, e pode ser reanalisado pelo painel (um a um ou em lote) ou por `flask reanalyze-pending`.
- **Humano no circuito** — o que a IA marcou como spam fica fora das listas por padrão, mas dá para listar, conferir e corrigir. O sentimento também pode ser corrigido à mão.
- **Cofre de chaves cifrado** — chaves de provedor salvas com Fernet, com rotação sem parar o app, e que nunca voltam pela API (só os 4 últimos caracteres).
- **Prompt medido, não adivinhado** — o prompt de classificação tem testes de contrato e um harness de avaliação (`evals/`) com casos de ajuste e casos reservados.
- **Segurança por padrão** — senha única do painel com CSRF, `SECRET_KEY` forte exigida em produção, rate limit nos endpoints públicos, chave de ingestão opcional e proteção contra prompt injection.
- **CI completo** — testes em Python 3.10 e 3.11, migrations e testes num PostgreSQL de verdade, e build da imagem Docker (que baixa o painel do release, com checksum) com smoke test. Cada front tem a própria CI no seu repositório.

## Estrutura do Projeto

```
feedlens/
├── app/                           # Aplicação Flask e API
│   ├── __init__.py                # App factory e registro das extensões
│   ├── auth.py                    # Sessão, login do painel e proteção de rotas
│   ├── cli.py                     # Comandos flask: reanalyze-pending e rotate-llm-keys
│   ├── config.py                  # Configurações da aplicação
│   ├── csrf.py                    # Tokens CSRF da sessão
│   ├── extensions.py              # Rate limit (Flask-Limiter) e CORS
│   ├── ingest_auth.py             # Chave de ingestão opcional (X-API-Key)
│   ├── scheduling.py              # Agendador do resumo semanal (python -m app.scheduling)
│   ├── llm/                       # Camada de IA via LiteLLM
│   │   ├── providers.py           # Catálogo/allowlist de provedores e validação de modelo
│   │   ├── credentials.py         # Cadeia de credenciais: requisição, cofre e ambiente
│   │   ├── client.py              # Chamada à LLM com fallback entre provedores
│   │   └── vault.py               # Cifra (Fernet) das chaves salvas
│   ├── models/                    # Modelos SQLAlchemy (feedbacks, funcionalidades, chaves)
│   ├── routes/
│   │   ├── api.py                 # Endpoints REST de ingestão, leitura, correção e reanálise
│   │   ├── settings_api.py        # Endpoints do cofre de provedores
│   │   ├── health.py              # Probe /health
│   │   ├── spa.py                 # Entrega do painel (release do feedlens-panel) e deep-links
│   │   └── web.py                 # Compatibilidade das URLs antigas
│   └── services/                  # Análise, estatísticas, e-mail, feedbacks e cofre
├── evals/                         # Avaliação do prompt de classificação (casos e harness)
├── migrations/                    # Migrações de banco de dados (Alembic)
├── scripts/
│   └── fetch_panel.py             # Baixa o painel do release para app/static/spa (fora do Docker)
├── tests/                         # Suíte pytest do backend
├── .github/workflows/
│   ├── ci.yml                     # CI: backend, Postgres e Docker
│   └── panel-bump.yml             # Abre PR quando sai release novo do painel
├── .env-example                   # Exemplo de variáveis de ambiente
├── docker-compose.yml             # Aplicação + PostgreSQL para rodar com Docker
├── Dockerfile                     # Baixa o painel do release (com checksum) e monta a aplicação
├── gunicorn.conf.py               # Workers, threads e timeout do gunicorn
├── ARCHITECTURE.md                # Fluxos, decisões de arquitetura e segurança
├── README.md                      # Este arquivo
├── requirements.txt               # Dependências Python
├── requirements-dev.txt           # Dependências de teste (inclui as de produção)
├── run.py                         # Ponto de entrada de desenvolvimento
└── wsgi.py                        # Entrada WSGI para produção
```

O Flask serve o painel já compilado a partir de `app/static/spa/`. O painel é uma SPA: o navegador recebe o mesmo `index.html` na raiz e nos deep-links, e o Vue Router decide qual tela exibir. A API continua no Flask, sob `/api/...`. O código do painel está no repositório [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel); aqui entra só o build, baixado do release com versão e checksum fixos (veja [Painel (feedlens-panel)](#painel-feedlens-panel)).

## Formulário público (`feedlens-form`)

O painel é para a equipe. O formulário é um segundo front, **separado**, para o cliente final: uma tela só, onde ele escreve e envia o feedback. Não tem login, não mostra feedbacks de outras pessoas e não fala em termos técnicos. Ele vive no repositório [feedlens-form](https://github.com/IannCarvalho/feedlens-form) (Angular 22, com README, CI e Docker próprios) e é um site estático independente: este backend **não** o serve, então o cookie de sessão do painel nunca chega a ele.

O que o backend precisa saber sobre ele:

- **API:** usa só `POST /api/feedbacks` com `{ id, feedback }`. O `id` é um UUID gerado no navegador e reaproveitado nas tentativas: reenviar é seguro, e um `409` conta como "já recebemos". Um `GET /health` sem CORS acorda o servidor enquanto a pessoa escreve.
- **CORS:** em produção, liste a origem do formulário em `CORS_ORIGINS`.
- **Chave de ingestão (`INGEST_API_KEY`):** um site público não guarda segredo. Se a chave estiver ligada e você **não** abrir a ingestão pública, o formulário recebe `401`. Há dois caminhos para o envio público: receber no seu backend e reenviar com a chave, ou ligar a **ingestão pública** (`INGEST_PUBLIC_ORIGINS`), que aceita envios sem chave a partir das origens que você listar, sob rate limit próprio e teto diário de gasto. Veja [Enviar Feedback](#enviar-feedback).

Como rodar, configurar (`config.json` ou variáveis do Docker) e usar o modo demonstração: veja o [README do feedlens-form](https://github.com/IannCarvalho/feedlens-form/blob/main/README.md). Para enviar de verdade a este backend, veja [Rodando os três juntos](#rodando-os-três-juntos).

## Tecnologias Utilizadas

- **Backend:**
  - Python 3.10+
  - Flask (framework web) e Gunicorn (produção)
  - SQLAlchemy & Flask-SQLAlchemy (ORM)
  - Flask-Migrate (migrações de banco de dados)
  - Flask-Limiter (rate limit) e Flask-CORS
  - APScheduler (agendamento de tarefas)
  - cryptography (Fernet, cofre de chaves)

- **Banco de Dados:**
  - PostgreSQL (recomendado)
  - SQLite (alternativa para desenvolvimento)

- **Inteligência Artificial:**
  - [LiteLLM](https://docs.litellm.ai/): interface única para OpenAI, Anthropic, Google Gemini, Groq e OpenRouter (BYOK, fallback entre provedores)
  - Pydantic (validação da saída do modelo) e [nh3](https://nh3.readthedocs.io/) (sanitização do HTML gerado para o e-mail)

- **Frontend** (em repositórios próprios):
  - Painel, em [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel): Vue 3 (Composition API), Vite e TypeScript, PrimeVue e PrimeIcons, Pinia, Vue Router e Chart.js
  - Formulário público, em [feedlens-form](https://github.com/IannCarvalho/feedlens-form): Angular 22 (zoneless, Signal Forms), servido por nginx

- **Envio de E-mail:**
  - SMTP (via `smtplib` do Python)

- **Qualidade:**
  - pytest e Ruff (backend); cada front tem os próprios testes e lint no seu repositório
  - GitHub Actions e Docker

- **Ambiente:**
  - python-dotenv (gerenciamento de variáveis de ambiente)

## Configuração do Ambiente

### Pré-requisitos

- Python 3.10 ou superior
- Para o painel, **uma** destas opções: Docker (a imagem já o traz) ou o script `scripts/fetch_panel.py`, que baixa o build pronto (só Python). Node.js 24.15 ou superior é necessário apenas se você for alterar o próprio painel, no repositório feedlens-panel.
- PostgreSQL ou outro banco de dados SQL (ou SQLite para desenvolvimento)
- Chave de API de pelo menos um provedor de LLM (OpenAI, Anthropic, Google Gemini, Groq ou OpenRouter), no servidor ou enviada por requisição

### Instalação

1. Clone o repositório:
```bash
git clone https://github.com/IannCarvalho/feedlens.git
cd feedlens
```

2. Crie e ative um ambiente virtual:
```bash
python -m venv venv

# No Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# No Windows (cmd)
venv\Scripts\activate.bat

# No Linux/macOS
source venv/bin/activate
```

   Se o PowerShell recusar o script de ativação, libere scripts locais para o seu usuário com `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

3. Instale as dependências:
```bash
pip install -r requirements.txt
```

4. Crie um arquivo `.env` baseado no `.env-example`:
```bash
cp .env-example .env

# No Windows (PowerShell)
Copy-Item .env-example .env
```

5. Ajuste o `.env`. Como vem do `.env-example`, ele já roda localmente sem editar nada: banco SQLite (o arquivo fica em `instance/feedlens.db`) e painel aberto, sem login. Para testar o login e o cofre de provedores, preencha estas linhas:
```
DASHBOARD_PASSWORD=senha_de_acesso_ao_painel
# Gere com: python -c "import secrets; print(secrets.token_urlsafe(48))"
SECRET_KEY=sua_chave_secreta
# Gere com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FEEDLENS_ENCRYPTION_KEY=sua_chave_fernet
```

   Gere as duas chaves uma vez e mantenha-as fixas: trocar a `SECRET_KEY` derruba as sessões abertas, e trocar a `FEEDLENS_ENCRYPTION_KEY` sem rotação deixa ilegíveis as chaves de provedor já salvas.

   Opcionais: chaves de LLM no servidor (ou cadastre-as depois na tela de provedores) e o e-mail do resumo semanal:
```
# LLM: preencha só os provedores que usar (OPENAI_/ANTHROPIC_/GEMINI_/GROQ_/OPENROUTER_API_KEY)
ANTHROPIC_API_KEY=sua_chave_anthropic
# Ordem principal -> reservas, no formato provedor/modelo
LLM_MODELS=anthropic/claude-haiku-4-5
EMAIL_HOST=smtp.exemplo.com
EMAIL_USER=seu_email@exemplo.com
EMAIL_PASSWORD=sua_senha_email
EMAIL_RECIPIENTS=stakeholder1@exemplo.com,stakeholder2@exemplo.com
```

   Em produção, aponte o `DATABASE_URL` para o PostgreSQL (`postgresql://usuario:senha@host/feedlens`). As demais variáveis (timeouts, limites, porta e TLS do e-mail) têm padrão e estão comentadas no `.env-example`.

6. Crie as tabelas aplicando as migrations versionadas (`migrations/versions/`):
```bash
flask db upgrade
```

   **Já tem um banco criado antes das migrations versionadas** (por `flask db migrate` ou `db.create_all()`)? Marque-o como estando no baseline e só então atualize, para não tentar recriar as tabelas:
```bash
flask db stamp 0001
flask db upgrade
```

   Ao mudar um modelo, gere a revisão com `flask db migrate -m "descrição"`, **revise o arquivo gerado** e commite-o. O CI roda `flask db check` num Postgres e falha se os modelos e as migrations divergirem.

### Executando a Aplicação

### Painel (feedlens-panel)

O painel é um SPA Vue que o Flask serve já compilado. O código-fonte está no repositório [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel); aqui você só instala o build de um release, com um comando (precisa só de Python e internet):

```bash
python scripts/fetch_panel.py
```

O script usa a versão e o checksum fixados nas linhas `ARG PANEL_VERSION` e `ARG PANEL_SHA256` do `Dockerfile` (a mesma fonte da imagem Docker), recusa o arquivo se o checksum não bater e extrai em `app/static/spa/`. Sem o painel instalado, a raiz responde `503` com essa mesma instrução. Rode de novo quando a versão do `Dockerfile` mudar.

Para atualizar o painel: publique uma tag no feedlens-panel e troque as duas linhas do `Dockerfile` pelos valores do release (o `.sha256` do release traz o checksum).

Você não precisa fazer isso à mão: o workflow [`panel-bump.yml`](.github/workflows/panel-bump.yml) roda todo dia (e sob demanda, em Actions > Panel bump > Run workflow), vê se há release novo, confere o download e o checksum, builda a imagem com o smoke test e, só então, abre um PR trocando as duas linhas. Ele nunca faz merge. Como o PR aberto pelo `GITHUB_TOKEN` não dispara o CI, a verificação acontece dentro do próprio workflow. Ele exige "Allow GitHub Actions to create and approve pull requests" ligado em Settings > Actions > General. Leia as notas do release antes de mergear: se o painel novo depende de uma mudança de API, o backend precisa dela primeiro.

Para iniciar a aplicação em modo de desenvolvimento:

```bash
flask run
```

A aplicação estará disponível em `http://localhost:5000`.

> **Nota de segurança:** o `flask run` escuta só em `127.0.0.1` e sobe sem debug. O `python run.py` é diferente: em desenvolvimento ele liga o debug e escuta em `0.0.0.0`, o que expõe o debugger do Werkzeug na sua rede. Prefira o `flask run`. Em produção, use um servidor WSGI (ex: `gunicorn`) e defina `FLASK_ENV=production`.

Problemas comuns ao rodar localmente:

- **`Connection refused` na porta 5432 no `flask db upgrade`:** o `DATABASE_URL` aponta para um PostgreSQL que não está rodando. Use o SQLite padrão do `.env-example`, ou suba só o banco do compose (`docker compose up -d db`) e use `postgresql://feedlens:feedlens@localhost:5432/feedlens`.
- **A raiz responde `503`:** falta instalar o painel (`python scripts/fetch_panel.py`).
- **`no such table` no log:** faltou aplicar as migrations (`flask db upgrade`).
- **`Port 5000 is in use by another program`:** outro processo já usa a porta. Pare-o ou rode `flask run --port 5001`.
- **O servidor continua com o código antigo depois de um `git pull`:** o Python só lê o código ao subir. Reinicie o `flask run`. O painel, ao contrário, o Flask lê do disco a cada acesso: depois de instalar outra versão, basta dar `Ctrl+F5` no navegador.

Para desenvolver o próprio painel com recarga instantânea, use o repositório [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel): `npm run dev` (porta 5173) encaminha `/api`, `/auth`, `/login`, `/logout` e `/health` para este Flask em `127.0.0.1:5000`, mantendo a mesma origem para o cookie de sessão.

### Rodando os três juntos

A ordem importa (cada passo depende do anterior):

1. **Backend e painel:** com o ambiente virtual ativo e o `.env` pronto, rode `flask db upgrade`, `python scripts/fetch_panel.py` e `flask run`. O painel abre em `http://localhost:5000`.
2. **Provedor de IA:** sem nenhuma chave o envio responde `503 llm_not_configured`. Ponha uma chave no `.env` ou, com `DASHBOARD_PASSWORD` e `FEEDLENS_ENCRYPTION_KEY` definidas, cadastre-a em **Provedores**. O botão de teste da tela confirma se a chave e o modelo existem na sua conta.
3. **Formulário:** clone o [feedlens-form](https://github.com/IannCarvalho/feedlens-form), rode `npm ci` e `npm start` (`http://127.0.0.1:4200`). Ele sobe em modo demonstração; para enviar de verdade, ponha `"useMock": false` no `public/config.json` dele (o `apiBaseUrl` já aponta para `http://127.0.0.1:5000`). Não commite essa alteração: o padrão do repositório precisa continuar `true`.
4. **Conferir:** envie um texto que pareça feedback sobre um produto ou serviço e abra **Feedbacks** no painel.

Se o feedback enviado não aparece no painel, as causas mais comuns são:

- **O formulário ainda está em modo demonstração** (`useMock: true`): nada chegou ao servidor. Com `useMock: false` o painel de demonstração some da tela.
- **A IA marcou o texto como spam** (letra de música, propaganda, texto sem sentido): ele não entra nas listas nem nos gráficos. Em **Feedbacks**, mude o filtro de spam para "Só spam" ou "Com spam"; no detalhe, "Não é spam" devolve o feedback às listas.
- **A IA não conseguiu analisar** (cota excedida, timeout): o feedback é gravado, mas fica de fora dos números da Visão geral e aparece com o selo "Não analisado". Use "Reanalisar com a IA" no detalhe quando o provedor se recuperar, ou reprocesse vários de uma vez pelo botão de reanálise em lote. Cada feedback reanalisado é uma nova chamada ao provedor de IA, então o lote processa poucos por vez e para no primeiro erro.

### Autenticação do Dashboard

As rotas web do painel (`/`, `/dashboard`, `/feedbacks`, `/feedback/<id>`) são protegidas por uma **senha única compartilhada**, definida em `DASHBOARD_PASSWORD`. Ao acessar sem sessão, o usuário é levado à tela de login (`/login`, uma tela do próprio SPA Vue); a sessão fica num cookie assinado pela `SECRET_KEY`.

- **Com `DASHBOARD_PASSWORD` definida:** o painel exige login e exibe um botão "Sair".
- **Sem `DASHBOARD_PASSWORD` (vazia):** o painel fica aberto, sem login e sem botão "Sair". É conveniente em desenvolvimento. Em produção sem senha, um aviso é registrado no log.

Como o login funciona: o Vue só desenha o formulário, e toda a verificação continua no servidor.

| Rota | Pública | O que faz |
|---|---|---|
| `GET /login` | sim | Entrega a tela de login do SPA. Já autenticado (ou sem senha), redireciona para o `next`. |
| `GET /auth/session` | sim | `{auth_enabled, authenticated, csrf_token}`, com `Cache-Control: no-store`. |
| `POST /login` | sim | JSON `{"password": "..."}` + header `X-CSRF-Token`. Responde `200 {ok, next}`, `401 invalid_password`, `400 csrf` ou `429 rate_limited`. |
| `GET`/`POST /logout` | sim | Encerra a sessão e volta ao login. |

`/auth/session` e `/login` ficam **fora de `/api/*`** de propósito: são internas ao painel (mesma origem) e não entram no CORS da API pública. Se a sessão expira com o painel aberto, a próxima chamada da API responde `401` e o painel leva ao login, voltando depois para a tela onde o usuário estava. Ele só faz isso quando `/auth/session` confirma que há login e que a sessão acabou, para não entrar em loop num deploy sem senha.

Proteções do login e da sessão:

- **`SECRET_KEY` forte em produção:** o app recusa subir com placeholder ou chave com menos de 32 caracteres, já que quem conhece a chave assina um cookie "autenticado" sem saber a senha. Gere uma localmente com `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- **Força-bruta:** o `POST /login` é limitado por IP (`RATELIMIT_LOGIN`, padrão `10 per minute`).
- **Sessão com validade:** o login expira após `SESSION_LIFETIME_HOURS` (padrão 12h).
- **Cookie:** `HttpOnly`, `SameSite=Lax` e `Secure` (padrão em produção; `SESSION_COOKIE_SECURE=false` só para HTTP local).
- **Redirect pós-login:** o parâmetro `next` só aceita caminhos locais (`//host`, `/\host` e esquemas externos são descartados). O servidor devolve o destino já filtrado, e a tela nunca navega para o `next` cru da URL.
- **CSRF:** o `POST /login` e a API de configuração exigem o header `X-CSRF-Token`. É um token aleatório por sessão, comparado em tempo constante, que já existe antes do login e é trocado nele. O login também descarta a sessão anterior, contra fixação de sessão. Isso fecha o *login CSRF* e complementa o `SameSite=Lax`. Se o token estiver velho (servidor reiniciado, sessão expirada), a tela de login pega um novo e tenta de novo uma vez, sem mostrar erro. O logout não exige token: é idempotente, e o pior que um logout forjado faz é deslogar a própria vítima.

**API de leitura:** `GET /api/feedbacks` e `GET /api/feedbacks/<id>` expõem o texto de todos os feedbacks, então seguem a mesma regra do painel. Com `DASHBOARD_PASSWORD` ou `API_READ_TOKEN` definidos, a leitura exige uma credencial; sem nenhum dos dois ela continua aberta (dev), com aviso no log em produção:

- **Sessão do painel:** para um frontend da mesma origem (o cookie de login vale).
- **`Authorization: Bearer <API_READ_TOKEN>`:** para clientes servidor-a-servidor. Mínimo de 24 caracteres (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). Não vá no JavaScript de um site público: quem abre o site lê o token. O token não é aceito na query string, para não parar em log nem em histórico.

Sem credencial a resposta é `401` JSON com `WWW-Authenticate: Bearer`. O `POST /api/feedbacks` (ingestão) e o `/health` continuam públicos.

**Ações do painel (sessão + CSRF):** corrigir e reanalisar são ações de quem opera o painel, exigem a sessão do painel mais o header `X-CSRF-Token` (nunca o `API_READ_TOKEN`) e respondem `403` sem `DASHBOARD_PASSWORD`.

| Rota | O que faz |
|---|---|
| `PATCH /api/feedbacks/<id>` | Corrige a classificação (`is_spam` e/ou `sentiment`). |
| `POST /api/feedbacks/<id>/reanalyze` | Reanalisa um feedback. |
| `POST /api/feedbacks/reanalyze-pending` | Reanalisa EM LOTE os pendentes. Corpo JSON opcional `{"limit": N}` (1..10, padrão 5); fora da faixa responde `400`. Síncrono, para no primeiro erro de provedor, e responde `{processed, analyzed, failed, remaining, stopped_reason}`. |

> **Cada feedback reanalisado é uma chamada paga ao provedor de IA.** Por isso o lote é pequeno e tem rate limit próprio (`RATELIMIT_REANALYZE`, padrão `6 per minute`). Para reprocessar um volume maior, use `flask reanalyze-pending` no servidor.

A API (`/api/...`) **não** é afetada por essa autenticação de formulário: o `POST /api/feedbacks` tem o próprio contrato de ingestão. A leitura (`GET`) tem a regra descrita acima, em *API de leitura*.

### Executando com Docker

O projeto inclui um `Dockerfile` (gunicorn servindo `wsgi:app`) e um `docker-compose.yml` que sobe a aplicação junto de um PostgreSQL. A imagem baixa o painel já compilado do release do feedlens-panel (estágio à parte, com checksum conferido: o build falha se não bater), então o host não precisa de Node, e só o resultado estático vai para a imagem final. Isso exige Docker 23 ou superior (BuildKit) e acesso a `github.com` durante o build. O job `docker` do CI builda a imagem e confere que ela serve o painel.

O compose exige uma `SECRET_KEY` aleatória (32+ caracteres) e falha logo no `up` sem ela; defina-a no `.env` ou no shell (ex.: `$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"` no PowerShell). Como ele serve HTTP em `localhost`, o compose desliga o cookie `Secure` por padrão (`SESSION_COOKIE_SECURE=false`).

```bash
# Opcional: chaves de LLM do servidor (sem nenhuma, a API so analisa com BYOK por requisicao)
export ANTHROPIC_API_KEY=sua-chave   # no Windows: $env:ANTHROPIC_API_KEY="sua-chave"
docker compose up --build
```

A aplicação sobe em `http://localhost:5000` e o Postgres em `localhost:5432`. As variáveis de ambiente têm defaults no compose; sobrescreva via `.env` ou pelo shell.

Na primeira execução, crie as tabelas no banco (o container `web` compartilha o código):

```bash
docker compose exec web flask db upgrade
```

## Endpoints da API

### Enviar Feedback

**Endpoint:** `POST /api/feedbacks`

**Descrição:** Recebe e analisa um feedback, classificando-o por sentimento, detectando spam e identificando funcionalidades solicitadas.

**Chave de ingestão:** com `INGEST_API_KEY` definida, este endpoint exige o header `X-API-Key: <chave>` e responde `401 ingest_key_required` **antes** de ler o corpo ou chamar qualquer LLM, então quem não tem chave não gasta nem grava nada. As tentativas sem chave contam no rate limit. Sem `INGEST_API_KEY` ele continua aberto (dev), com aviso no log em produção se houver chave de LLM no servidor, porque assim qualquer um gasta a cota do operador.

- A chave tem no mínimo 24 caracteres (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) e só é lida do header `X-API-Key` (nunca da query string nem do corpo).
- Para **rotacionar** sem downtime, liste duas separadas por vírgula (`INGEST_API_KEY=nova,antiga`), troque os clientes e remova a antiga.
- Use a chave de **servidor para servidor**. Não a embuta no JavaScript de um site público: quem abre o site a lê. Para um formulário público, receba no seu backend e reenvie com a chave.
- `INGEST_ALLOW_BYOK_WITHOUT_KEY=true` (padrão `false`) abre uma exceção para demos: quem trouxer a **própria** chave de LLM (`X-LLM-Provider` + `X-LLM-Api-Key`) é aceito sem chave de ingestão e paga a chamada, nunca a do servidor. O feedback ainda é gravado no seu banco.

**Ingestão pública (opt-in, para o formulário Angular):** com `INGEST_PUBLIC_ORIGINS` definida (origens exatas separadas por vírgula, ex.: `https://form.exemplo.com`), um `POST` **sem** `X-API-Key` é aceito quando o header `Origin` bate exatamente com uma das origens listadas. Pensado para o [feedlens-form](https://github.com/IannCarvalho/feedlens-form), que não pode guardar segredo. Desligada por padrão: sem essa variável, o comportamento é exatamente o de hoje.

- **O `Origin` NÃO é autenticação.** Ele é definido pelo navegador, mas qualquer cliente fora de um navegador (curl, script) pode forjá-lo. A lista de origens só impede o uso a partir de páginas de **outros** sites; ela não prova quem está enviando. As barreiras reais contra gasto de LLM são as duas abaixo — e recomenda-se configurar um **alerta de gasto** no painel do provedor de LLM.
- **Rate limit por IP** (`RATELIMIT_INGEST_PUBLIC`, padrão `5 per minute`), aplicado só ao caminho público; uma requisição com chave válida segue apenas o `RATELIMIT_INGEST`.
- **Teto diário global** (`INGEST_PUBLIC_DAILY_CAP`, padrão `500`): soma de feedbacks gravados nas últimas 24h. Estourado, o endpoint responde `429` com `code: public_ingest_cap` **antes** de ler o corpo ou chamar a IA (nada é gasto). Uma `X-API-Key` válida **ignora** o teto e o rate limit público.
- Sem `Origin` permitido e sem chave: `401 ingest_key_required`, como antes. Uma origem com caminho (`.../enviar`) ou curinga é inválida; `INGEST_PUBLIC_ORIGINS='*'` é **recusado no boot**. Liste cada origem pública também em `CORS_ORIGINS`, senão o navegador bloqueia a resposta (o boot avisa no log).

**Corpo da Requisição:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "feedback": "Gosto muito do app! Só queria uma forma mais fácil de editar o meu perfil dentro da minha conta."
}
```

**Resposta de Sucesso (201 Created):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "sentiment": "POSITIVO",
  "is_spam": false,
  "requested_features": [
    {
      "code": "EDITAR_PERFIL",
      "reason": "O usuário gostaria de realizar a edição do próprio perfil"
    }
  ],
  "analyzed_by": "anthropic/claude-haiku-4-5"
}
```

`analyzed_by` indica o `provedor/modelo` que fez a análise. `null` significa que a IA **não analisou** (as chaves do servidor falharam por timeout, cota excedida, indisponibilidade ou resposta inválida): o feedback é gravado mesmo assim, com o resultado seguro padrão (`INCONCLUSIVO`, sem spam, sem features), mas **marcado como não analisado**. A resposta `201` traz `analysis_status` (`analyzed` ou `unanalyzed`) e `analysis_error` (o motivo: `rate_limit` = cota, `timeout`, `connection`, `auth`, `not_found`, `empty`, `invalid_response`...). Quem enviou não é afetado; o painel mostra esses feedbacks com o selo “Não analisado” e permite reanalisá-los (veja *Reanalisar*, abaixo).

**BYOK por requisição (opcional):** o cliente pode usar a própria chave enviando os headers abaixo. Nesse caso **só** essa chave é usada (nunca as do servidor), ela não é gravada nem logada, e uma falha volta como erro em vez de virar `INCONCLUSIVO`, sem gravar nada, para que o mesmo `id` possa ser reenviado.

| Header | Obrigatório | Exemplo |
|---|---|---|
| `X-LLM-Provider` | sim | `openai`, `anthropic`, `gemini`, `groq` ou `openrouter` |
| `X-LLM-Api-Key` | sim | a chave do provedor |
| `X-LLM-Model` | não | `claude-haiku-4-5` (padrão: o modelo padrão do provedor); no Groq e no OpenRouter aceita `fabricante/modelo`, ex.: `google/gemma-4-31b-it:free` |

```bash
curl -X POST https://seu-host/api/feedbacks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $INGEST_API_KEY" \
  -H "X-LLM-Provider: anthropic" -H "X-LLM-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{"id": "550e8400-e29b-41d4-a716-446655440000", "feedback": "Adoro o app, mas falta modo escuro."}'
```

**Erros da análise:** `401 ingest_key_required` (chave de ingestão ausente ou inválida), `503 llm_not_configured` (nenhuma chave no servidor e nenhum BYOK enviado), `400 invalid_byok_headers` (provedor fora da lista, modelo ou chave malformados), `403` (BYOK desligado com `BYOK_REQUEST_KEYS_ENABLED=false`), `422 llm_auth_failed` (o provedor recusou a chave enviada) e `502 llm_request_failed` (falha do provedor com a chave enviada).

**Rate limiting:** este endpoint público é limitado (padrão `30 per minute` por IP) via [Flask-Limiter](https://flask-limiter.readthedocs.io/). Ao exceder o limite a resposta é `429 Too Many Requests` com corpo JSON. Configurável por `RATELIMIT_INGEST`, `RATELIMIT_ENABLED` e `RATELIMIT_STORAGE_URI` (use `redis://...` em produção com múltiplos workers; o default `memory://` serve a um único processo). Os endpoints `GET` não são limitados.

**CORS:** as rotas `/api/*` têm CORS habilitado (via [Flask-CORS](https://flask-cors.readthedocs.io/)) para consumo por um frontend/cliente de outra origem. As origens permitidas são configuráveis por `CORS_ORIGINS` (lista separada por vírgula; default `*`). As rotas web do dashboard não expõem CORS.

**Validação:** o corpo precisa ser um objeto JSON com `id` (UUID) e `feedback` (texto não vazio, até `MAX_FEEDBACK_CHARS` caracteres, padrão `5000`); qualquer violação responde `400` com corpo JSON. Corpos acima de `MAX_CONTENT_LENGTH` (padrão 1 MB) recebem `413`, e um `id` já existente recebe `409`, inclusive quando duas requisições com o mesmo id chegam ao mesmo tempo.

### Listar Feedbacks

**Endpoint:** `GET /api/feedbacks`

**Descrição:** Lista os feedbacks analisados, do mais recente para o mais antigo, com paginação. Por padrão o que a IA marcou como spam fica de fora (use `spam` para revisá-lo). **Exige credencial** quando `DASHBOARD_PASSWORD` ou `API_READ_TOKEN` estão definidos (veja *API de leitura*, acima): `Authorization: Bearer <API_READ_TOKEN>` ou a sessão do painel; senão, `401`.

**Parâmetros de query:**
- `page` — número da página (padrão `1`)
- `per_page` — itens por página (padrão `20`, máximo `100`)
- `sentiment` — filtro opcional (`POSITIVO`, `NEGATIVO` ou `INCONCLUSIVO`)
- `spam` — `exclude` (padrão, sem spam), `include` (tudo) ou `only` (só os marcados como spam). Outro valor responde `400`.
- `analysis` — `any` (padrão) ou `unanalyzed` (só os que a IA não conseguiu analisar). Outro valor responde `400`.

**Campos de cada item:** além de `id`, `feedback`, `sentiment`, `is_spam`, `created_at` e `requested_features`, vêm `analysis_status`, `analysis_error` e `analyzed_by` (veja *Analisar Feedback*).

**Resposta de Sucesso (200 OK):**
```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "feedback": "Gosto muito do app!",
      "sentiment": "POSITIVO",
      "is_spam": false,
      "created_at": "2025-10-15T00:00:00+00:00",
      "requested_features": []
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 1,
    "pages": 1,
    "has_next": false,
    "has_prev": false
  },
  "sentiment": null,
  "spam": "exclude"
}
```

### Consultar um Feedback

**Endpoint:** `GET /api/feedbacks/<id>`

**Descrição:** Retorna um feedback específico pelo seu UUID. Mesma autenticação da listagem. Responde `400` para um id malformado e `404` quando não existe. Funciona também para feedbacks marcados como spam.

### Estatísticas do Dashboard

**Endpoint:** `GET /api/stats`

**Descrição:** Alimenta a Visão geral do painel: total, contagem e percentuais por sentimento, ranking de funcionalidades e feedbacks recentes. Mesma autenticação da listagem (sessão do painel ou `API_READ_TOKEN`), já que expõe o conteúdo dos feedbacks.

Os números e gráficos (`total_feedbacks`, `sentiment_counts`, `sentiment_percentages`, `top_features`) ignoram spam e os feedbacks **não analisados**. Os `recent_feedbacks` mantêm os não analisados, com `analysis_status`, e `unanalyzed` informa quantos são. Sem nenhum feedback analisado a resposta é só `{"has_data": false, "unanalyzed": N}`.

### Corrigir a Análise de um Feedback

**Endpoint:** `PATCH /api/feedbacks/<id>`

**Descrição:** Revisão humana do que a IA decidiu. Corpo: `{"is_spam": true|false}` e/ou `{"sentiment": "POSITIVO"|"NEGATIVO"|"INCONCLUSIVO"}`, pelo menos um dos dois. Só esses campos mudam (texto e funcionalidades continuam os da análise; outras chaves são ignoradas). Devolve o feedback atualizado, e repetir o mesmo valor é seguro. Listas, gráficos e o resumo semanal passam a refletir a correção.

**Autenticação:** só a sessão do painel com o header `X-CSRF-Token`. O `API_READ_TOKEN` **não** vale aqui. Sem `DASHBOARD_PASSWORD` responde `403` (`auth_not_configured`); sem sessão, `401`; com CSRF inválido, `403`. Também responde `400` para id ou corpo inválidos (sem campo, valor fora de `POSITIVO`/`NEGATIVO`/`INCONCLUSIVO`, `is_spam` que não é booleano) e `404` quando o feedback não existe.

### Reanalisar um Feedback

**Endpoint:** `POST /api/feedbacks/<id>/reanalyze`

**Descrição:** Roda a IA de novo no texto (cadeia de provedores do servidor) e grava o resultado. Pensado para os **não analisados** (cota, timeout...), mas vale para qualquer feedback: um resultado novo substitui sentimento, spam e funcionalidades, inclusive correções manuais anteriores. É síncrono (espera a IA).

- `200` com o feedback atualizado (`analysis_status: "analyzed"`).
- `503` + `code: "analysis_unavailable"` (com o motivo em `detail`) se a IA não respondeu de novo: o feedback continua não analisado e o sentimento/spam atuais ficam como estavam.
- `503` + `code: "llm_not_configured"` se não há provedor configurado.

**Autenticação:** a mesma do `PATCH` (sessão do painel + `X-CSRF-Token`; `403` sem `DASHBOARD_PASSWORD`).

**Em lote:** no servidor, `flask reanalyze-pending` reprocessa todos os não analisados, do mais antigo ao mais novo. Opções: `--limit N`, `--id <uuid>` (repetível; reanalisa esse feedback mesmo que já analisado) e `--stop-after N` (padrão 3: para depois de N falhas seguidas, por exemplo cota esgotada, em vez de gastar chamadas). Termina com código 1 se algum continuou sem análise.

**Migração:** o estado da análise vem da migration `0005` (`flask db upgrade`). Os feedbacks que já existiam ficam como `analyzed`, porque não há como saber depois quais foram o resultado padrão; reanalise os suspeitos com `flask reanalyze-pending --id <uuid>` ou pelo botão do painel.

### Health Check

**Endpoint:** `GET /health`

**Descrição:** Probe de liveness/readiness para orquestradores (Docker, Render). Sem autenticação e sem rate-limit. Valida a conexão com o banco:
- `200 OK` → `{"status": "ok", "database": "ok"}`
- `503 Service Unavailable` → `{"status": "degraded", "database": "error"}` quando o banco está inacessível

O `docker-compose.yml` usa este endpoint no `healthcheck` do serviço `web`.

### Chaves de Provedor (cofre)

Além das variáveis de ambiente e do BYOK por requisição, o operador pode salvar chaves no banco e **trocar provedor principal e reservas sem redeploy**. A cadeia usada na análise é:

1. headers `X-LLM-*` da requisição (vencem tudo e usam só a chave do cliente);
2. chaves salvas e habilitadas, por `priority` (1 = principal);
3. chaves do ambiente (`LLM_MODELS`), para o que não estiver salvo.

Se a mesma combinação provedor/modelo existe nas duas últimas, a chave salva vale. A mudança é imediata: cada análise lê a cadeia na hora.

**Como ligar:** defina `FEEDLENS_ENCRYPTION_KEY` (chave Fernet, **separada** da `SECRET_KEY`) e `DASHBOARD_PASSWORD`, e rode `flask db upgrade`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Sem `FEEDLENS_ENCRYPTION_KEY` o cofre fica desligado (nada é gravado em claro) e sem `DASHBOARD_PASSWORD` a API recusa tudo (`403`), porque um painel aberto não pode cadastrar chaves pagas. Uma `FEEDLENS_ENCRYPTION_KEY` malformada impede o app de subir. Se a chave de cifra for perdida ou trocada sem rotação, as chaves salvas ficam ilegíveis: são ignoradas (com aviso no log) e é preciso cadastrá-las de novo.

Todas as rotas usam o cookie de sessão do painel (faça login na tela `/login`); as que alteram estado exigem o header `X-CSRF-Token` com o valor de `csrf_token` devolvido pelo `GET`. A chave só entra por `POST`/`PATCH` e **nunca volta**: as respostas trazem só `key_hint` (`...a1b2`).

| Rota | O que faz |
|---|---|
| `GET /api/settings/providers` | Catálogo, `environment_chain` (sem chaves), chaves salvas e `csrf_token` |
| `POST /api/settings/providers` | Salva `{provider, api_key, model?, priority?, enabled?, verify?}` |
| `PATCH /api/settings/providers/<id>` | Altera `enabled`, `priority`, `model` ou troca `api_key` |
| `DELETE /api/settings/providers/<id>` | Remove |
| `POST /api/settings/providers/reorder` | `{"ids": [...]}` define a ordem da cadeia (o resto vai depois) |
| `POST /api/settings/providers/<id>/test` | Testa com 1 chamada mínima e registra `last_verified_at` / `last_error_kind` |

Ao salvar ou trocar uma chave, o app a testa antes (`verify`, padrão `true`): se o provedor a **recusar** a resposta é `422 llm_auth_failed` e nada é gravado; falhas passageiras (cota, timeout) gravam a chave com `last_error_kind`. Erros: `400` (entrada inválida), `403 csrf`, `404`, `409` (`duplicate`: mesmo provedor/modelo, ou `limit_reached`: até 20 chaves) e `503 vault_disabled`. Cada teste é uma chamada paga ao provedor, por isso `RATELIMIT_SETTINGS_TEST` (padrão `10 per minute`).

**Rotação da chave de cifra:** ponha a nova antes da antiga (`FEEDLENS_ENCRYPTION_KEY="nova,antiga"`), reinicie, rode `flask rotate-llm-keys` e depois remova a antiga da variável.

## Qualidade da classificação

O prompt que decide sentimento, spam e funcionalidades fica em `ANALYSIS_SYSTEM_PROMPT` (`app/services/feedback_analyzer.py`). Para medir uma mudança nele (ou trocar o modelo) sem depender de impressão:

```bash
python -m evals.classification_eval --set dev                 # casos de ajuste
python -m evals.classification_eval --set holdout --runs 2    # casos reservados: rode só no fim
python -m evals.classification_eval --prompt-file outro.txt   # testa um prompt alternativo
```

Os casos (`evals/cases.py`) cobrem elogios e críticas curtos, ironia, perguntas, pedidos de funcionalidade, feedback sobre atendimento (que não pode virar spam) e spam de verdade (propaganda, letra de música, texto sem sentido, injeção de prompt). O comando **chama a LLM de verdade** e gasta cota: cerca de uma chamada por caso e repetição. Falhas do provedor (cota, timeout) são contadas à parte, e não como erro de classificação. A suíte `pytest` testa o contrato do prompt e o harness sem chamar a LLM.

> Atenção: se a cota do provedor acabar ou a chamada expirar, o analisador grava o resultado seguro padrão (`INCONCLUSIVO`, sem spam, sem funcionalidades), mas o feedback fica marcado como **não analisado** (`analysis_status: "unanalyzed"`, com o motivo em `analysis_error`). No painel ele aparece com o selo “Não analisado”, e dá para reprocessá-lo (botão “Reanalisar com a IA” ou `flask reanalyze-pending`).

## Interface Web

O painel é uma SPA Vue 3 servida pelo Flask na raiz. A navegação acontece no Vue Router, e um reload em qualquer deep-link continua funcionando porque o Flask entrega o `index.html` do painel instalado. A interface usa PrimeVue, PrimeIcons, Pinia e Chart.js.

### Rotas do painel

- **Visão geral (`/`):** contagem e indicadores, distribuição de sentimentos, funcionalidades mais solicitadas e feedbacks recentes. Os números e gráficos ignoram spam e os feedbacks **não analisados** (a IA não respondeu; o sentimento deles é só o padrão). Esses ficam na lista de recentes com o selo “Não analisado”, e um aviso no topo diz quantos são e leva à lista filtrada para reanalisá-los.
- **Feedbacks (`/feedbacks`):** tabela paginada com filtros por sentimento e por spam (`Sem spam`, `Com spam`, `Só spam`) e navegação para o detalhe.
- **Detalhe (`/feedbacks/<id>`):** texto original, sentimento, status de spam e funcionalidades identificadas. Com login ligado, o seletor “Sentimento” corrige o que a IA atribuiu e os botões “Marcar como spam” e “Não é spam” corrigem a classificação de spam.
- **Provedores (`/providers`):** cadeia de provedores, cadastro e teste de chaves no cofre BYOK, habilitação, remoção e reordenação.
- **Login (`/login`):** tela pública do próprio SPA; a senha é validada pelo Flask e a sessão usa cookie assinado.

As URLs antigas continuam compatíveis: `/dashboard` redireciona para `/`, e `/feedback/<id>` redireciona para `/feedbacks/<id>`. `/feedbacks` já é a rota canônica da lista e não deve redirecionar para si mesma.

Se o login estiver habilitado e a sessão expirar, o cliente detecta a resposta `401`, confirma o estado em `/auth/session` e leva a pessoa de volta ao login preservando a tela de destino. Sem `DASHBOARD_PASSWORD`, o painel permanece aberto em modo de desenvolvimento.

## Funcionalidade de E-mail

A aplicação envia automaticamente um resumo semanal dos feedbacks para os stakeholders configurados. O e-mail inclui:
- Estatísticas de feedbacks da semana
- Porcentagem de feedbacks positivos e negativos
- Principais funcionalidades solicitadas

O envio é agendado via APScheduler. O texto do resumo é gerado por LLM, com fallback para um template estático quando a geração falha ou não há chave configurada.

### Agendamento do resumo

O resumo sai toda segunda-feira às 8h (fuso `SCHEDULER_TIMEZONE`, padrão `America/Sao_Paulo`) e exige **um único agendador por deploy**, senão cada processo envia o seu e-mail:

- **Processo único** (padrão do container: 1 worker do gunicorn com threads, via `gunicorn.conf.py`): `SCHEDULER_ENABLED=true` no próprio app. O `docker-compose.yml` já liga.
- **Vários workers ou réplicas:** `SCHEDULER_ENABLED=false` no web e um processo dedicado com `python -m app.scheduling`.
- **Sob demanda ou por cron externo** (ex.: Render Cron Job): `flask send-weekly-summary` (no container: `docker compose exec web flask --app wsgi send-weekly-summary`).

O SMTP usa STARTTLS com certificado verificado e `EMAIL_TIMEOUT` (padrão 30s). `APP_BASE_URL` define o link do painel no e-mail. Sem configuração de e-mail o app sobe normalmente (inclusive em produção) e só registra um aviso.

## Testes e CI

Backend (com o ambiente virtual ativo):

```bash
pip install -r requirements-dev.txt
pip install ruff
ruff check .
pytest tests
```

O `pytest.ini` já aponta para `tests/` e põe a raiz no `sys.path`. A suíte não faz chamadas reais a provedores de LLM, então não gasta cota nem precisa de chave. Ela cobre a API (ingestão, leitura, correção e reanálise), autenticação e CSRF, o cofre de chaves, a cadeia de provedores, o contrato do prompt, as estatísticas, o SPA e as migrations. As migrations também rodam num PostgreSQL de verdade no CI (`flask db upgrade`, `flask db check`, `flask db downgrade base`), porque o SQLite dos testes não pega estouro de `VARCHAR`, chave estrangeira nem `timestamptz`.

Os fronts têm testes e CI nos próprios repositórios: [feedlens-panel](https://github.com/IannCarvalho/feedlens-panel) (typecheck, lint e build; sem testes automatizados de componente) e [feedlens-form](https://github.com/IannCarvalho/feedlens-form) (typecheck, lint, Vitest, build com orçamento de bundle e a imagem Docker). Aqui, os testes do painel ficam no contrato: `tests/test_spa.py` (entrega e deep-links) e `tests/test_panel_pin.py` (pin e script de download).

O workflow [`ci.yml`](.github/workflows/ci.yml) roda em todo push e PR para a `main`, com três jobs:

| Job | O que valida |
|---|---|
| `test` | Ruff e pytest em Python 3.10 e 3.11 |
| `postgres` | Migrations (subir, checar, descer) e pytest num PostgreSQL 16 |
| `docker` | Build da imagem (baixa o painel do release e confere o checksum) e smoke test: `/health`, o `index.html` do painel e um asset JS |

## Considerações de Desenvolvimento

### Padrões Arquiteturais

- **Arquitetura em Camadas:** separação de responsabilidades
  - Routes: controladores de requisições e respostas
  - Services: lógica de negócio e consultas ao banco (`FeedbackService`, `Statistics`, `FeedbackAnalyzer`)
  - Models: estrutura de dados e persistência

- **App factory:** `create_app()` monta a aplicação, registra os blueprints e as extensões (banco, migrations, rate limit, CORS); as extensões compartilhadas ficam em `app/extensions.py` para evitar import circular.

- **Configuração de LLM resolvida a cada chamada:** o analisador não guarda chave nem modelo. A cadeia de provedores é montada em cada análise, então trocar a ordem no cofre ou enviar um BYOK vale na hora, sem reiniciar.

### Provedores de LLM (BYOK)

A camada `app/llm/` fala com os modelos via [LiteLLM](https://docs.litellm.ai/) e decide, a cada chamada, **de onde vem a chave**:

1. **BYOK por requisição** (headers `X-LLM-*`): usa só a chave do cliente.
2. **Chaves salvas no cofre** (opcional, veja [Chaves de Provedor](#chaves-de-provedor-cofre)): cifradas no banco, na ordem de `priority`, editáveis sem redeploy.
3. **Chaves do servidor** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`): `LLM_MODELS` define a cadeia `provedor/modelo`, principal primeiro e reservas depois (ex.: `anthropic/claude-haiku-4-5,openai/gpt-4o-mini`). Sem `LLM_MODELS`, todo provedor com chave entra, no modelo padrão, na ordem OpenAI → Anthropic → Gemini → Groq → OpenRouter.

Se o principal falha (timeout, cota, chave revogada, indisponibilidade), a análise segue para o próximo da cadeia. Trocar de provedor é mudar a ordem em `LLM_MODELS` (ou reordenar as chaves salvas); nenhuma chave é obrigatória para o app subir.

| Provedor | Variável | Modelo padrão |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` (ou `OPENAI_MODEL`, mantido por compatibilidade) |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| Google Gemini | `GEMINI_API_KEY` | `gemini-2.5-flash-lite` |
| Groq | `GROQ_API_KEY` | `openai/gpt-oss-20b` |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter/free` (roteador de modelos gratuitos) |

**Groq e OpenRouter (camada gratuita).** Ambos têm planos gratuitos, com limites que mudam e que você deve conferir no console de cada um (a cota por minuto e por dia, o que vale para o seu modelo e a política de dados do plano grátis). Servem bem como **reserva** do provedor principal:

- Crie a chave em `console.groq.com` ou `openrouter.ai` e informe em `GROQ_API_KEY` / `OPENROUTER_API_KEY`, ou cadastre na tela *Provedores*.
- O nome do modelo vai depois do prefixo do provedor. Nesses dois ele pode ter a barra do fabricante: `groq/openai/gpt-oss-20b`, `openrouter/google/gemma-4-31b-it:free`. O padrão do OpenRouter, `openrouter/free`, deixa o OpenRouter escolher um modelo gratuito que esteja no ar (a lista de ids `:free` muda com frequência); para fixar um, use o id completo.
- Exemplo de cadeia com reserva gratuita: `LLM_MODELS=gemini/gemini-2.5-flash-lite,groq/openai/gpt-oss-20b,openrouter/openrouter/free`.
- **Quais modelos a sua conta enxerga:** o catálogo do Groq varia por conta. Se o teste da chave falhar com `not_found`, o modelo não existe para você (aconteceu com o `llama-3.3-70b-versatile` numa conta gratuita). Liste os disponíveis com `curl -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models` e cadastre um deles em *Modelo*.
- Modelo pequeno ou roteado pode classificar pior (spam e funcionalidades, sobretudo em português). Meça com `python -m evals.classification_eval` antes de torná-lo o principal.

Cuidados de segurança:

- **Allowlist de provedores:** só os cinco acima. Prefixos como `bedrock/` ou `ollama/` usariam credenciais ou hosts internos do servidor e são recusados. A barra no nome do modelo só é aceita no Groq e no OpenRouter (uma única, `fabricante/modelo`); nos outros provedores continua proibida, para o provedor sair sempre do prefixo.
- **A chave não vaza:** não é gravada (no BYOK por requisição), não aparece em `repr` e é removida das mensagens de erro e dos logs; as chaves salvas ficam cifradas (Fernet) e a API nunca as devolve; o log do próprio LiteLLM fica em `WARNING` para não registrar o texto dos feedbacks.
- **Prompt injection:** as instruções vão na mensagem de sistema e o feedback vai delimitado, como dado. Códigos de feature são normalizados (`EDICAO_DE_PERFIL`, até 50 caracteres, no máximo 10 por feedback), e o HTML gerado para o e-mail passa por sanitização: só tags de formatação, sem links, imagens ou atributos.
- **Custo e latência:** cada chamada loga provedor, latência, tokens e custo estimado. O LiteLLM é importado sob demanda (e aquecido em segundo plano no `wsgi.py`), então boot, CLI e testes não pagam seu import.

## Status e próximos passos

**Já entregue:**

- análise por sentimento, spam e funcionalidades pedidas, com cinco provedores de LLM (OpenAI, Anthropic, Gemini, Groq e OpenRouter) e fallback entre eles;
- BYOK por requisição e cofre de chaves cifrado, com cadastro, edição de modelo e chave, teste, ordem e remoção pelo painel;
- painel Vue 3 com login por senha e CSRF e formulário público em Angular, cada um em seu repositório (feedlens-panel e feedlens-form), com o painel embutido na imagem por release e checksum;
- revisão humana: filtro de spam, correção de spam e de sentimento;
- feedbacks "não analisados" identificáveis e reprocessáveis (painel e `flask reanalyze-pending`);
- rate limiting nos endpoints públicos, chave de ingestão opcional e CORS configurável;
- resumo semanal por e-mail gerado por LLM, com fallback para um template estático;
- avaliação do prompt (`evals/`), suíte de testes, CI com Postgres e Docker.

**Limitações conhecidas** (veja também o [ARCHITECTURE.md](ARCHITECTURE.md#6-limitações-conhecidas-e-evolução)):

- **A análise é síncrona:** o `POST /api/feedbacks` espera a IA responder. Com o provedor lento, quem envia espera (o formulário avisa "Ainda enviando..."). Gravar na hora e analisar depois é a evolução natural.
- **Formulário público e chave de ingestão:** um site público não guarda segredo, então com `INGEST_API_KEY` ligada o formulário recebe `401`. Há dois caminhos: receber pelo seu backend e reenviar com a chave, ou ligar a **ingestão pública** (`INGEST_PUBLIC_ORIGINS`), que aceita envios sem chave das origens listadas sob rate limit próprio e teto diário. Como o `Origin` é forjável fora de um navegador, isso não autentica ninguém — a proteção é o limite de taxa e o teto de gasto.
- **Sem histórico de correções:** a troca manual de sentimento ou de spam fica só numa linha do log do servidor; não há registro de quem corrigiu o quê.
- **Reanálise em lote só pela linha de comando:** o painel reanalisa um feedback por vez.
- **O painel Vue não tem testes de componente**, só typecheck, lint e build.
- **Camadas gratuitas de LLM têm limites** de requisições e de tokens por minuto e por dia. Meça com `evals/` antes de usar um modelo gratuito como principal.

**Próximos passos possíveis:**

1. **UI/UX:** mais filtros, ordenação, responsividade e exportação de relatórios (PDF/CSV)
2. **Escalabilidade:** cache para consultas frequentes e fila de processamento para as análises
3. **Monitoramento:** integração com logging/observabilidade e métricas de desempenho
4. **Qualidade da IA:** registrar os resultados da avaliação por modelo e provedor

## Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.
