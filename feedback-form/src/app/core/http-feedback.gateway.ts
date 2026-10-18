import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { APP_CONFIG } from './app-config';
import { FeedbackGateway, SendFailure, SendOutcome } from './feedback.gateway';

/** Envio real: POST /api/feedbacks da API de ingestão existente. */
@Injectable()
export class HttpFeedbackGateway extends FeedbackGateway {
  private readonly http = inject(HttpClient);
  private readonly config = inject(APP_CONFIG);
  private warmed = false;

  override async send(id: string, text: string): Promise<SendOutcome> {
    try {
      await firstValueFrom(
        this.http.post(`${this.config.apiBaseUrl}/api/feedbacks`, { id, feedback: text }),
      );
      return { ok: true };
    } catch (error) {
      return classify(error);
    }
  }

  /**
   * `no-cors`: a resposta é opaca, mas o pedido sai e o serviço acorda enquanto
   * a pessoa escreve. Não depende de CORS e não precisa de credencial.
   */
  override warmUp(): void {
    if (this.warmed) {
      return;
    }
    this.warmed = true;
    void fetch(`${this.config.apiBaseUrl}/health`, { mode: 'no-cors' }).catch(() => undefined);
  }
}

/** Traduz a resposta da API para a linguagem da tela. */
export function classify(error: unknown): SendOutcome {
  if (!(error instanceof HttpErrorResponse)) {
    return { ok: false, reason: 'unavailable' };
  }
  // 409: o mesmo id já foi recebido. Para quem enviou, deu certo.
  if (error.status === 409) {
    return { ok: true };
  }
  const reason: SendFailure =
    error.status === 0
      ? 'offline'
      : error.status === 400 || error.status === 413 || error.status === 422
        ? 'invalid'
        : error.status === 429
          ? 'busy'
          : 'unavailable';
  return { ok: false, reason };
}
