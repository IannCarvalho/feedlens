import { TestBed } from '@angular/core/testing';
import { MockScenarioId, SEED_RECEIVED } from './mock-data';
import { MockFeedbackGateway } from './mock-feedback.gateway';

describe('MockFeedbackGateway', () => {
  let gateway: MockFeedbackGateway;

  const use = (scenario: MockScenarioId) => {
    gateway.scenario.set(scenario);
    gateway.delayScale.set(0);
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [MockFeedbackGateway] });
    gateway = TestBed.inject(MockFeedbackGateway);
  });

  it('começa com os feedbacks de exemplo', () => {
    expect(gateway.received()).toEqual(SEED_RECEIVED);
  });

  it('sucesso: guarda o feedback e registra o corpo enviado', async () => {
    use('sucesso');
    expect(await gateway.send('id-1', 'Muito bom')).toEqual({ ok: true });
    expect(gateway.received().at(-1)?.feedback).toBe('Muito bom');
    expect(gateway.lastPayload()).toEqual({ id: 'id-1', feedback: 'Muito bom' });
  });

  it('reenviar o mesmo id não duplica', async () => {
    use('sucesso');
    await gateway.send('id-1', 'Muito bom');
    await gateway.send('id-1', 'Muito bom');
    expect(gateway.received().filter((item) => item.id === 'id-1')).toHaveLength(1);
  });

  it('instável: falha na primeira tentativa do id e passa na segunda', async () => {
    use('instavel');
    expect(await gateway.send('id-2', 'x')).toEqual({ ok: false, reason: 'unavailable' });
    expect(await gateway.send('id-2', 'x')).toEqual({ ok: true });
  });

  it.each([
    ['sem-conexao', 'offline'],
    ['muitos-envios', 'busy'],
    ['recusado', 'invalid'],
  ] as const)('cenário %s devolve %s', async (scenario, reason) => {
    use(scenario);
    expect(await gateway.send('id-3', 'x')).toEqual({ ok: false, reason });
  });

  it('repetido: confirma sem guardar de novo', async () => {
    use('repetido');
    const before = gateway.received().length;
    expect(await gateway.send('id-4', 'x')).toEqual({ ok: true });
    expect(gateway.received()).toHaveLength(before);
  });
});
