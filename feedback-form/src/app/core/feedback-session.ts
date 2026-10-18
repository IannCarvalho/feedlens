import { Injectable, computed, inject, signal } from '@angular/core';
import { FeedbackGateway, SendFailure } from './feedback.gateway';
import { newId } from './uuid';

/** Depois deste tempo sem resposta, a tela avisa que pode demorar. */
export const SLOW_AFTER_MS = 4000;

/**
 * Estado do envio, compartilhado entre a tela do formulário, a de agradecimento
 * e o painel de demonstração. Guarda o que precisa sobreviver à troca de rota:
 * o rascunho, o `id` da tentativa e se algo foi enviado.
 */
@Injectable({ providedIn: 'root' })
export class FeedbackSession {
  private readonly gateway = inject(FeedbackGateway);

  /** Rascunho do campo (também preenchido pelo painel de demonstração). */
  readonly draft = signal({ text: '' });

  private readonly sendingState = signal(false);
  private readonly slowState = signal(false);
  private readonly failureState = signal<SendFailure | null>(null);
  private readonly sentState = signal(false);

  readonly sending = this.sendingState.asReadonly();
  readonly slow = this.slowState.asReadonly();
  readonly failure = this.failureState.asReadonly();
  readonly sent = this.sentState.asReadonly();
  readonly hasFailed = computed(() => this.failureState() !== null);

  /**
   * O mesmo `id` vale para todas as tentativas do mesmo texto: se a rede cair
   * depois de o serviço receber, o reenvio não gera um segundo feedback.
   */
  private currentId = newId();

  warmUp(): void {
    this.gateway.warmUp();
  }

  /** Devolve `true` quando o feedback foi recebido. */
  async submit(): Promise<boolean> {
    if (this.sendingState()) {
      return false;
    }
    const text = this.draft().text.trim();
    this.failureState.set(null);
    this.sendingState.set(true);
    this.slowState.set(false);
    const slowTimer = setTimeout(() => this.slowState.set(true), SLOW_AFTER_MS);

    try {
      const outcome = await this.gateway.send(this.currentId, text);
      if (outcome.ok) {
        this.sentState.set(true);
        this.draft.set({ text: '' });
        this.currentId = newId();
        return true;
      }
      this.failureState.set(outcome.reason);
      return false;
    } finally {
      clearTimeout(slowTimer);
      this.sendingState.set(false);
      this.slowState.set(false);
    }
  }

  /** Volta ao estado inicial para um novo envio. */
  startOver(): void {
    this.sentState.set(false);
    this.failureState.set(null);
  }
}
