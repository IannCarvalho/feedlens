# Privacidade e ciclo de vida dos dados (FeedLens)

Documento **técnico**, não um texto jurídico: descreve que dados o FeedLens
trata, onde eles ficam, quem mais os recebe e como apagá-los. Serve de base
para a adequação à LGPD, mas **não** é a política de privacidade nem substitui
assessoria jurídica. As pendências de negócio estão listadas no fim.

Tudo aqui foi conferido no código deste repositório; onde algo depende de
configuração do operador (variáveis de ambiente, provedores escolhidos), isso
está dito explicitamente.

## 1. Que dados o FeedLens trata

O produto existe para analisar **feedback de texto livre** de usuários finais.
Esse texto pode conter dado pessoal se o usuário o escrever lá (nome, e-mail,
relato), porque é campo livre. O que é gravado por feedback, no banco
(`app/models/feedback.py`):

| Campo | Conteúdo | Origem |
|---|---|---|
| `id` | UUID do feedback | fornecido por quem envia |
| `feedback_text` | o texto livre do usuário | enviado no `POST /api/feedbacks` |
| `sentiment` | `POSITIVO` / `NEGATIVO` / `INCONCLUSIVO` | classificação da IA |
| `is_spam` | booleano | classificação da IA |
| `analysis_status` / `analysis_error` / `analyzed_by` | estado da análise e qual provedor/modelo a fez | gerado pelo servidor |
| `created_at` | timestamp (UTC) | gerado pelo servidor |
| `requested_features` | funcionalidades pedidas (`code`, `reason`), tabela `requested_features` | derivadas do texto pela IA |

Além dos feedbacks, o banco guarda **chaves de provedor de LLM do operador**
(tabela `provider_credentials`), que são do operador, não dos usuários, e ficam
**cifradas** com Fernet (`FEEDLENS_ENCRYPTION_KEY`); a API nunca devolve a chave,
só um `key_hint` (`...a1b2`). Veja `app/services/credential_store.py`.

### Endereços IP

**O FeedLens não grava endereços IP no banco.** Conferido no código: a única
leitura de IP é `get_remote_address` como *chave* do rate-limiter em memória
(`app/extensions.py`), que não é persistida. Não há coluna de IP no modelo nem
log de aplicação que registre IP de usuário.

Ressalva operacional honesta: o gunicorn é configurado com `accesslog = '-'`
(`gunicorn.conf.py`), então o **log de acesso HTTP vai para o stdout** do
processo e, no formato padrão, inclui o IP de origem. Atrás do proxy do Render
esse IP chega como `127.0.0.1` (o IP real do cliente iria no cabeçalho
`X-Forwarded-For`, que o app não lê nem registra). Esses logs de stdout são
efêmeros (retenção do provedor de hospedagem), não entram no banco e não são
correlacionados a feedbacks.

### O que **não** é tratado

Não há cadastro de usuário final, autenticação de quem envia feedback, cookies
de rastreamento, nem analytics. O único cookie é o de **sessão do painel**
(operador), `HttpOnly` + `SameSite=Lax`, que guarda só o estado de login
assinado pela `SECRET_KEY` — não é dado de usuário final.

## 2. Onde os dados ficam (hospedagem)

Deploy de referência (veja o README, seção *Deploy em produção*):

| Camada | Onde | O que guarda |
|---|---|---|
| Banco | **Neon** (PostgreSQL), região `aws-us-east-2` (EUA) | feedbacks, funcionalidades, chaves cifradas |
| Backend | **Render** (serviço web Docker, plano free) | processa requisições; logs de stdout efêmeros |
| Formulário público | **Vercel** | front estático que coleta o texto e o envia ao backend |

O dado pessoal em repouso fica no **Neon, nos EUA** — ou seja, há
**transferência internacional** de dados, ponto relevante para a LGPD (ver
pendências).

## 3. Suboperadores (quem mais recebe dados)

| Suboperador | O que recebe | Observação |
|---|---|---|
| **Neon** | todo o conteúdo do banco (texto dos feedbacks, timestamps, classificação) | armazenamento em repouso, EUA |
| **Render** | as requisições HTTP em trânsito; roda o app | hospedagem do backend |
| **Vercel** | o texto que o usuário digita no formulário, em trânsito até o backend | hospedagem do front público |
| **OpenRouter** | o **texto do feedback** vai no prompt de análise | provedor de LLM (ver §4) |
| **Groq** | idem, quando é o provedor da vez da cadeia | provedor de LLM (ver §4) |
| **Brevo** | o **resumo mensal agregado** por e-mail (contagens e funcionalidades, **sem texto de feedback individual**) | relay SMTP; só se o e-mail estiver configurado |

O conjunto de provedores de LLM que efetivamente recebe dados depende da cadeia
configurada (`LLM_MODELS` ou chaves salvas). O deploy de referência usa
OpenRouter e Groq (camadas gratuitas). Se o operador configurar OpenAI,
Anthropic ou Gemini, esses passam a receber o texto também.

## 4. Envio do texto para LLMs, e registro de prompts

Cada análise manda **o texto do feedback** para um provedor de LLM
(`app/services/feedback_analyzer.py` + `app/llm/`). Pontos verificados:

- A instrução vai como mensagem de sistema e o feedback vai **delimitado, como
  dado** (mitigação de prompt injection).
- A chave enviada por BYOK (`X-LLM-*`) **não é gravada nem logada**; o log do
  próprio LiteLLM fica em `WARNING` para não registrar o texto dos feedbacks.

Atenção de privacidade: **as camadas gratuitas de OpenRouter e Groq podem
registrar e usar os prompts** (o texto do feedback) conforme a política do
plano grátis de cada um. Isso não é controlável pelo FeedLens. Para dados
sensíveis, use um provedor/plano com política de não-retenção e confirme no
contrato do provedor.

## 5. Como apagar dados

Três mecanismos, todos no código:

1. **Apagar um feedback** — `DELETE /api/feedbacks/<id>` (sessão do painel +
   CSRF). Apaga o feedback **e** as `requested_features` dele; responde `204`.
   O log registra só o `id`, nunca o texto.
2. **Retenção automática** — config `FEEDBACK_RETENTION_DAYS` (padrão `0` =
   desligado). Com `N>0`, feedbacks com `created_at` mais antigo que `N` dias
   são apagados; com `SCHEDULER_ENABLED`, um job diário às 03:00
   (`SCHEDULER_TIMEZONE`) faz a limpeza.
3. **Expurgo manual** — `flask purge-old-feedbacks` (use `--dry-run` para só
   contar, `--days N` para sobrepor a config). Útil para rodar uma limpeza sob
   demanda ou por um cron externo.

Apagar no FeedLens remove o dado **do banco (Neon)**. **Não** apaga cópias que
provedores de LLM (OpenRouter, Groq, ...) possam ter retido do prompt, nem logs
de stdout já emitidos — esses seguem a retenção de cada terceiro.

## 6. O que falta para a LGPD (pendências de negócio)

Itens **fora do escopo do código**, que dependem de decisão/assessoria do
responsável pelo produto:

- **Termos de uso e Política de Privacidade** publicados, informando o
  tratamento, a base legal, a transferência internacional (dados no Neon/EUA) e
  os direitos do titular.
- **Base legal** definida para o tratamento do texto de feedback (consentimento,
  legítimo interesse, etc.) e, se for o caso, aviso de coleta no formulário.
- **DPA (Data Processing Agreement)** com cada suboperador (Neon, Render,
  Vercel, OpenRouter, Groq, Brevo), em especial quanto a retenção de prompts
  pelos provedores de LLM.
- **Encarregado (DPO)** nomeado e canal para requisições de titulares (acesso,
  correção, exclusão).
- **Processo para atender pedidos de exclusão/portabilidade** do titular,
  apoiado nos mecanismos da §5.
- **Avaliação da transferência internacional** de dados para os EUA e das
  garantias aplicáveis.

Este documento cobre o lado técnico; os itens acima são de negócio/jurídico.
