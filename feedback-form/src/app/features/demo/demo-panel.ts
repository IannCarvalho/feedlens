import { Component, computed, inject } from '@angular/core';
import { SAMPLE_FEEDBACKS, MOCK_SCENARIOS, MockScenarioId } from '../../core/mock-data';
import { FeedbackSession } from '../../core/feedback-session';
import { MockFeedbackGateway } from '../../core/mock-feedback.gateway';

/**
 * Painel só do modo demonstração (`useMock: true`). Existe para quem avalia a
 * tela: troca o cenário, preenche exemplos e mostra o que seria enviado. Usa
 * vocabulário técnico de propósito, e nunca é montado para o cliente final.
 */
@Component({
  selector: 'app-demo-panel',
  template: `
    <details class="demo">
      <summary>Modo demonstração</summary>
      <div class="body">
        <p class="note">
          Dados de exemplo, sem servidor. Este painel não aparece para o cliente final.
        </p>

        <label for="demo-scenario">Cenário do envio</label>
        <select id="demo-scenario" [value]="gateway.scenario()" (change)="pick($event)">
          @for (scenario of scenarios; track scenario.id) {
            <option [value]="scenario.id" [selected]="scenario.id === gateway.scenario()">
              {{ scenario.label }}
            </option>
          }
        </select>

        <label class="check">
          <input type="checkbox" [checked]="instant()" (change)="toggleInstant($event)" />
          Sem espera simulada
        </label>

        <button class="button button-secondary" type="button" (click)="fillSample()">
          Preencher com um exemplo
        </button>

        <h2>Último envio</h2>
        @if (payload(); as body) {
          <pre>{{ body }}</pre>
        } @else {
          <p class="note">Nada enviado ainda.</p>
        }

        <h2>Recebidos nesta demonstração ({{ gateway.received().length }})</h2>
        <ul>
          @for (item of gateway.received(); track item.id) {
            <li>{{ item.feedback }}</li>
          }
        </ul>
      </div>
    </details>
  `,
  styles: `
    .demo {
      margin-top: 2rem;
      background: var(--surface);
      border: 1px dashed var(--field-border);
      border-radius: 0.75rem;
      padding: 0.75rem 1rem;
      font-size: 0.9375rem;
    }

    summary {
      cursor: pointer;
      font-weight: 700;
    }

    .body {
      display: grid;
      gap: 0.625rem;
      margin-top: 0.75rem;
    }

    .note {
      margin: 0;
      color: var(--muted);
    }

    select {
      padding: 0.5rem;
      border: 1px solid var(--field-border);
      border-radius: 0.375rem;
      background: var(--surface);
      color: var(--ink);
      font: inherit;
    }

    .check {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    h2 {
      margin: 0.5rem 0 0;
      font-size: 1rem;
    }

    pre {
      margin: 0;
      padding: 0.75rem;
      overflow-x: auto;
      border-radius: 0.375rem;
      background: var(--bg);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    ul {
      margin: 0;
      padding-left: 1.25rem;
    }
  `,
})
export class DemoPanel {
  protected readonly gateway = inject(MockFeedbackGateway);
  private readonly session = inject(FeedbackSession);

  protected readonly scenarios = MOCK_SCENARIOS;
  protected readonly instant = computed(() => this.gateway.delayScale() === 0);
  protected readonly payload = computed(() => {
    const last = this.gateway.lastPayload();
    return last ? JSON.stringify(last, null, 2) : null;
  });

  private sampleIndex = 0;

  protected pick(event: Event): void {
    this.gateway.scenario.set((event.target as HTMLSelectElement).value as MockScenarioId);
  }

  protected toggleInstant(event: Event): void {
    this.gateway.delayScale.set((event.target as HTMLInputElement).checked ? 0 : 1);
  }

  protected fillSample(): void {
    const text = SAMPLE_FEEDBACKS[this.sampleIndex % SAMPLE_FEEDBACKS.length];
    this.sampleIndex += 1;
    this.session.draft.set({ text });
  }
}
