import { Injectable, signal } from '@angular/core';
import { FeedbackGateway, SendOutcome } from './feedback.gateway';
import {
  DEFAULT_MOCK_SCENARIO,
  MOCK_SCENARIOS,
  MockReceivedFeedback,
  MockScenarioId,
  SEED_RECEIVED,
  isMockScenario,
} from './mock-data';

/**
 * Gateway de demonstração: imita a API de ingestão sem rede, incluindo o
 * comportamento de id repetido (o serviço real devolve 409 e a tela trata
 * como "já recebemos").
 */
@Injectable()
export class MockFeedbackGateway extends FeedbackGateway {
  readonly scenario = signal<MockScenarioId>(initialScenario());
  /** Tempo extra para testes: 0 desliga a espera simulada. */
  readonly delayScale = signal(1);

  private readonly receivedState = signal<readonly MockReceivedFeedback[]>(SEED_RECEIVED);
  readonly received = this.receivedState.asReadonly();

  /** Último corpo enviado, como a API o receberia (visível no painel de demo). */
  private readonly lastPayloadState = signal<{ id: string; feedback: string } | null>(null);
  readonly lastPayload = this.lastPayloadState.asReadonly();

  override async send(id: string, text: string): Promise<SendOutcome> {
    const scenario = MOCK_SCENARIOS.find((item) => item.id === this.scenario());
    await wait((scenario?.delayMs ?? 0) * this.delayScale());
    this.lastPayloadState.set({ id, feedback: text });

    switch (this.scenario()) {
      case 'sem-conexao':
        return { ok: false, reason: 'offline' };
      case 'muitos-envios':
        return { ok: false, reason: 'busy' };
      case 'recusado':
        return { ok: false, reason: 'invalid' };
      case 'instavel':
        // Falha na primeira tentativa de cada id e funciona na segunda,
        // para validar "tentar de novo" sem perder o texto.
        if (!this.attempted.has(id)) {
          this.attempted.add(id);
          return { ok: false, reason: 'unavailable' };
        }
        return this.store(id, text);
      case 'repetido':
        return { ok: true };
      default:
        return this.store(id, text);
    }
  }

  private readonly attempted = new Set<string>();

  private store(id: string, text: string): SendOutcome {
    if (!this.receivedState().some((item) => item.id === id)) {
      this.receivedState.update((items) => [
        ...items,
        { id, feedback: text, receivedAt: new Date().toISOString() },
      ]);
    }
    return { ok: true };
  }
}

function initialScenario(): MockScenarioId {
  const wanted = new URLSearchParams(globalThis.location?.search ?? '').get('cenario');
  return isMockScenario(wanted) ? wanted : DEFAULT_MOCK_SCENARIO;
}

function wait(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}
