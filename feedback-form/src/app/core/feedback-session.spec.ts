import { TestBed } from '@angular/core/testing';
import { FeedbackSession } from './feedback-session';
import { FeedbackGateway, SendOutcome } from './feedback.gateway';

class FakeGateway extends FeedbackGateway {
  readonly calls: { id: string; text: string }[] = [];
  outcomes: SendOutcome[] = [{ ok: true }];

  override async send(id: string, text: string): Promise<SendOutcome> {
    this.calls.push({ id, text });
    return this.outcomes.shift() ?? { ok: true };
  }
}

describe('FeedbackSession', () => {
  let gateway: FakeGateway;
  let session: FeedbackSession;

  beforeEach(() => {
    gateway = new FakeGateway();
    TestBed.configureTestingModule({
      providers: [{ provide: FeedbackGateway, useValue: gateway }],
    });
    session = TestBed.inject(FeedbackSession);
  });

  it('envia o texto sem espaços nas pontas e limpa o rascunho no sucesso', async () => {
    session.draft.set({ text: '  Ótimo atendimento  ' });
    expect(await session.submit()).toBe(true);
    expect(gateway.calls[0].text).toBe('Ótimo atendimento');
    expect(session.draft().text).toBe('');
    expect(session.sent()).toBe(true);
  });

  it('falha: mantém o texto, expõe o motivo e reaproveita o mesmo id na nova tentativa', async () => {
    gateway.outcomes = [{ ok: false, reason: 'offline' }, { ok: true }];
    session.draft.set({ text: 'Texto longo o bastante' });

    expect(await session.submit()).toBe(false);
    expect(session.failure()).toBe('offline');
    expect(session.draft().text).toBe('Texto longo o bastante');
    expect(session.sent()).toBe(false);

    expect(await session.submit()).toBe(true);
    expect(session.failure()).toBeNull();
    expect(gateway.calls[1].id).toBe(gateway.calls[0].id);
  });

  it('gera um id novo depois de um envio bem-sucedido', async () => {
    session.draft.set({ text: 'Primeiro feedback' });
    await session.submit();
    session.draft.set({ text: 'Segundo feedback' });
    await session.submit();
    expect(gateway.calls[1].id).not.toBe(gateway.calls[0].id);
  });

  it('ignora um segundo envio enquanto o primeiro está em andamento', async () => {
    session.draft.set({ text: 'Feedback único' });
    const first = session.submit();
    const second = await session.submit();
    await first;
    expect(second).toBe(false);
    expect(gateway.calls).toHaveLength(1);
  });

  it('startOver limpa o estado de envio', async () => {
    gateway.outcomes = [{ ok: false, reason: 'busy' }];
    session.draft.set({ text: 'Algo escrito aqui' });
    await session.submit();
    session.startOver();
    expect(session.failure()).toBeNull();
    expect(session.sent()).toBe(false);
  });
});
