import { Component, ElementRef, afterNextRender, computed, inject, viewChild } from '@angular/core';
import { Router } from '@angular/router';
import { FormField, FormRoot, form, maxLength, validate } from '@angular/forms/signals';
import { APP_CONFIG, FEEDBACK_MAX_CHARS, FEEDBACK_MIN_CHARS } from '../../core/app-config';
import { FeedbackSession } from '../../core/feedback-session';
import { SendFailure } from '../../core/feedback.gateway';

/** Mensagens na voz do cliente: sem status, sem "API", e sempre dizendo o que fazer. */
const FAILURE_TEXT: Record<SendFailure, string> = {
  invalid:
    'Não conseguimos aceitar esse texto. Confira se ele não está vazio nem muito longo e tente de novo.',
  busy: 'Recebemos muitos envios em pouco tempo. Espere cerca de um minuto e tente de novo. Seu texto continua aqui.',
  offline:
    'Parece que você está sem conexão. Verifique sua internet e tente de novo. Seu texto continua aqui.',
  unavailable:
    'Algo deu errado do nosso lado. Tente de novo em instantes. Seu texto continua aqui.',
};

@Component({
  selector: 'app-send-page',
  imports: [FormRoot, FormField],
  templateUrl: './send.page.html',
  styleUrl: './send.page.css',
})
export class SendPage {
  protected readonly session = inject(FeedbackSession);
  protected readonly config = inject(APP_CONFIG);
  private readonly router = inject(Router);

  protected readonly maxChars = FEEDBACK_MAX_CHARS;
  private readonly textArea = viewChild.required<ElementRef<HTMLTextAreaElement>>('textArea');

  protected readonly feedbackForm = form(
    this.session.draft,
    (path) => {
      validate(path.text, ({ value }) =>
        value().trim().length < FEEDBACK_MIN_CHARS
          ? {
              kind: 'tooShort',
              message:
                value().trim().length === 0
                  ? 'Escreva seu feedback antes de enviar.'
                  : 'Conte um pouco mais: escreva pelo menos algumas palavras.',
            }
          : undefined,
      );
      maxLength(path.text, FEEDBACK_MAX_CHARS, {
        message: `O texto passou do limite de ${FEEDBACK_MAX_CHARS} caracteres. Encurte um pouco.`,
      });
    },
    {
      submission: {
        action: async () => {
          if (await this.session.submit()) {
            await this.router.navigateByUrl('/obrigado');
          }
          return undefined;
        },
      },
    },
  );

  protected readonly length = computed(() => this.session.draft().text.length);
  protected readonly failureMessage = computed(() => {
    const failure = this.session.failure();
    return failure ? FAILURE_TEXT[failure] : null;
  });

  constructor() {
    this.session.startOver();
    afterNextRender(() => this.textArea().nativeElement.focus());
  }

  /** Primeiro foco no campo: acorda o serviço enquanto a pessoa escreve. */
  protected onFocus(): void {
    this.session.warmUp();
  }

  protected showError(): boolean {
    const field = this.feedbackForm.text();
    return field.touched() && field.invalid();
  }
}
