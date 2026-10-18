import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { FeedbackSession } from '../../core/feedback-session';
import { FeedbackGateway, SendOutcome } from '../../core/feedback.gateway';
import { SendPage } from './send.page';

@Component({ template: '' })
class ThanksStub {}

class FakeGateway extends FeedbackGateway {
  outcome: SendOutcome = { ok: true };
  readonly calls: { id: string; text: string }[] = [];
  warmed = 0;

  override async send(id: string, text: string): Promise<SendOutcome> {
    this.calls.push({ id, text });
    return this.outcome;
  }

  override warmUp(): void {
    this.warmed += 1;
  }
}

describe('SendPage', () => {
  let fixture: ComponentFixture<SendPage>;
  let gateway: FakeGateway;
  let router: Router;

  const el = () => fixture.nativeElement as HTMLElement;
  const textarea = () => el().querySelector('textarea') as HTMLTextAreaElement;
  const button = () => el().querySelector('button[type="submit"]') as HTMLButtonElement;

  async function type(text: string): Promise<void> {
    textarea().value = text;
    textarea().dispatchEvent(new Event('input'));
    await fixture.whenStable();
  }

  async function submit(): Promise<void> {
    el()
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { cancelable: true }));
    await fixture.whenStable();
  }

  beforeEach(async () => {
    gateway = new FakeGateway();
    await TestBed.configureTestingModule({
      imports: [SendPage],
      providers: [
        { provide: FeedbackGateway, useValue: gateway },
        provideRouter([{ path: 'obrigado', component: ThanksStub }]),
      ],
    }).compileComponents();
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(SendPage);
    await fixture.whenStable();
  });

  it('mostra o campo, o botão e nenhum termo técnico', () => {
    const text = el().textContent ?? '';
    expect(el().querySelector('label[for="feedback-text"]')?.textContent).toContain('Seu feedback');
    expect(button().textContent).toContain('Enviar feedback');
    for (const term of ['POST', 'API', 'endpoint', 'JSON', 'HTTP', 'UUID']) {
      expect(text).not.toContain(term);
    }
  });

  it('acorda o serviço ao focar no campo', () => {
    textarea().dispatchEvent(new Event('focus'));
    expect(gateway.warmed).toBeGreaterThan(0);
  });

  it('não envia vazio e explica o que fazer', async () => {
    await submit();
    expect(gateway.calls).toHaveLength(0);
    const error = el().querySelector('#feedback-error');
    expect(error?.textContent).toContain('Escreva seu feedback antes de enviar.');
    expect(textarea().getAttribute('aria-invalid')).toBe('true');
  });

  it('não envia texto curto demais', async () => {
    await type('oi');
    await submit();
    expect(gateway.calls).toHaveLength(0);
    expect(el().querySelector('#feedback-error')?.textContent).toContain('Conte um pouco mais');
  });

  it('envia e vai para a página de agradecimento', async () => {
    await type('  O app ficou muito mais rápido!  ');
    await submit();
    await fixture.whenStable();

    expect(gateway.calls).toHaveLength(1);
    expect(gateway.calls[0].text).toBe('O app ficou muito mais rápido!');
    expect(router.url).toBe('/obrigado');
    expect(TestBed.inject(FeedbackSession).sent()).toBe(true);
  });

  it('em falha mantém o texto, avisa em linguagem simples e oferece tentar de novo', async () => {
    gateway.outcome = { ok: false, reason: 'offline' };
    await type('Meu feedback completo');
    await submit();
    fixture.detectChanges();

    expect(textarea().value).toBe('Meu feedback completo');
    expect(router.url).not.toBe('/obrigado');
    const alert = el().querySelector('.alert');
    expect(alert?.textContent).toContain('sem conexão');
    expect(alert?.textContent).toContain('Seu texto continua aqui');
    expect(button().textContent).toContain('Tentar de novo');
  });

  it('o reenvio depois da falha usa o mesmo id', async () => {
    gateway.outcome = { ok: false, reason: 'unavailable' };
    await type('Meu feedback completo');
    await submit();
    gateway.outcome = { ok: true };
    await submit();
    await fixture.whenStable();

    expect(gateway.calls).toHaveLength(2);
    expect(gateway.calls[1].id).toBe(gateway.calls[0].id);
  });

  it('mostra o contador de caracteres', async () => {
    await type('abcde');
    fixture.detectChanges();
    expect(el().querySelector('.count')?.textContent).toContain('5 / 5000');
  });
});
