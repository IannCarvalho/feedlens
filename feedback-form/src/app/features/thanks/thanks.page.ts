import { Component, ElementRef, afterNextRender, inject, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';
import { APP_CONFIG } from '../../core/app-config';

@Component({
  selector: 'app-thanks-page',
  imports: [RouterLink],
  template: `
    <section class="card" aria-labelledby="thanks-title">
      <span class="check" aria-hidden="true">✓</span>
      <h1 #title id="thanks-title" tabindex="-1">Obrigado!</h1>
      <p class="lead">Recebemos seu feedback. Ele ajuda a melhorar {{ config.productName }}.</p>
      <a class="button" routerLink="/">Enviar outro feedback</a>
    </section>
  `,
  styles: `
    .card {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 0.75rem;
      padding: 2rem 1.5rem;
      text-align: center;
      box-shadow: 0 1px 2px rgb(20 23 31 / 6%);
    }

    .check {
      display: inline-grid;
      place-items: center;
      width: 3rem;
      height: 3rem;
      margin-bottom: 0.75rem;
      border-radius: 50%;
      background: var(--ok);
      color: #fff;
      font-size: 1.5rem;
      font-weight: 700;
    }

    h1:focus {
      outline: none;
    }

    .lead {
      color: var(--muted);
      margin: 0.5rem auto 1.5rem;
      max-width: 30rem;
    }
  `,
})
export class ThanksPage {
  protected readonly config = inject(APP_CONFIG);
  private readonly title = viewChild.required<ElementRef<HTMLElement>>('title');

  constructor() {
    // Leitores de tela anunciam a confirmação ao receber o foco no título.
    afterNextRender(() => this.title().nativeElement.focus());
  }
}
