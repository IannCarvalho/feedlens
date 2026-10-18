import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { APP_CONFIG } from './core/app-config';
import { DemoPanel } from './features/demo/demo-panel';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, DemoPanel],
  template: `
    <a class="skip-link" href="#conteudo">Ir para o conteúdo</a>
    <header class="top">
      <div class="top-inner">
        <svg class="mark" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
          <circle cx="15" cy="16" r="11" fill="none" stroke="currentColor" stroke-width="2.5" />
          <circle cx="24" cy="12" r="3.5" fill="var(--amber)" />
        </svg>
        <span class="brand">Seu feedback</span>
      </div>
    </header>
    <main id="conteudo" class="page" tabindex="-1">
      <router-outlet />
      @if (config.useMock) {
        <app-demo-panel />
      }
    </main>
  `,
  styles: `
    .skip-link {
      position: absolute;
      left: 0.5rem;
      top: -4rem;
      padding: 0.5rem 0.75rem;
      background: var(--ink);
      color: #fff;
      border-radius: 0.375rem;
      z-index: 10;
    }

    .skip-link:focus {
      top: 0.5rem;
    }

    .top {
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }

    .top-inner,
    .page {
      width: min(100% - 2rem, 42rem);
      margin-inline: auto;
    }

    .top-inner {
      display: flex;
      align-items: center;
      gap: 0.625rem;
      padding-block: 0.875rem;
    }

    .brand {
      font-weight: 700;
      font-size: 1.0625rem;
    }

    .page {
      padding-block: 2rem 3rem;
    }

    .page:focus {
      outline: none;
    }
  `,
})
export class App {
  protected readonly config = inject(APP_CONFIG);
}
