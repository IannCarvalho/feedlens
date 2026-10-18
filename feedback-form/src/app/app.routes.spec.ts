import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { FeedbackSession } from './core/feedback-session';
import { FeedbackGateway } from './core/feedback.gateway';

describe('rotas', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        {
          provide: FeedbackGateway,
          useValue: { send: async () => ({ ok: true }), warmUp: () => undefined },
        },
      ],
    });
  });

  it('/obrigado sem ter enviado volta para o formulário', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/obrigado');
    expect(router.url).toBe('/');
  });

  it('/obrigado depois de enviar abre a confirmação', async () => {
    const session = TestBed.inject(FeedbackSession);
    session.draft.set({ text: 'Feedback válido' });
    await session.submit();
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/obrigado');
    expect(router.url).toBe('/obrigado');
  });

  it('rota desconhecida cai no formulário', async () => {
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/qualquer-coisa');
    expect(router.url).toBe('/');
  });
});
