# FeedLens · formulário público

Front do **cliente final** do FeedLens: uma tela para escrever e enviar um feedback. É um projeto separado do painel (`../frontend`) e consome a API de ingestão existente (`POST /api/feedbacks`).

Documentação completa (arquitetura, CORS, chave de ingestão, modo demonstração): seção "Formulário público" do [README da raiz](../README.md).

## Comandos

Requer Node 24.15 ou superior (`.node-version`; com `fnm`, rode `fnm use` nesta pasta).

```powershell
npm ci
npm start            # http://127.0.0.1:4200 (modo demonstração, sem servidor)
npm run typecheck    # tipos, inclusive dos templates e dos testes
npm run lint
npm test             # Vitest
npm run build        # dist/feedback-form/browser (com orçamento de bundle)
```

## Estrutura

```
public/config.json            apiBaseUrl, useMock, productName (lido em tempo de execução)
src/main.ts                   lê o config e sobe o app
src/app/app.config.ts         escolhe o gateway: mock ou HTTP
src/app/core/                 FeedbackGateway (contrato), HTTP, mock + dados, sessão de envio
src/app/features/send/        formulário (Signal Forms)
src/app/features/thanks/      confirmação (só abre depois de um envio)
src/app/features/demo/        painel do modo demonstração (não existe com useMock: false)
```

A tela só conhece `FeedbackGateway.send(id, texto)`, que devolve `ok` ou um motivo (`invalid`, `busy`, `offline`, `unavailable`). Status HTTP e formato da API ficam dentro de `http-feedback.gateway.ts`.
