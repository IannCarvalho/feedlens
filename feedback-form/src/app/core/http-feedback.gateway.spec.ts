import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { APP_CONFIG } from './app-config';
import { HttpFeedbackGateway, classify } from './http-feedback.gateway';

describe('HttpFeedbackGateway', () => {
  let gateway: HttpFeedbackGateway;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: APP_CONFIG,
          useValue: { apiBaseUrl: 'https://api.exemplo.com', useMock: false, productName: 'x' },
        },
        HttpFeedbackGateway,
      ],
    });
    gateway = TestBed.inject(HttpFeedbackGateway);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('envia só id e feedback para POST /api/feedbacks, sem credencial', async () => {
    const result = gateway.send('abc', 'Gostei');
    const request = http.expectOne('https://api.exemplo.com/api/feedbacks');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ id: 'abc', feedback: 'Gostei' });
    expect(request.request.withCredentials).toBe(false);
    expect(request.request.headers.has('X-API-Key')).toBe(false);
    request.flush({ id: 'abc' }, { status: 201, statusText: 'Created' });
    expect(await result).toEqual({ ok: true });
  });

  it('trata 409 (id repetido) como sucesso', async () => {
    const result = gateway.send('abc', 'Gostei');
    http
      .expectOne('https://api.exemplo.com/api/feedbacks')
      .flush({}, { status: 409, statusText: 'Conflict' });
    expect(await result).toEqual({ ok: true });
  });

  it('nunca rejeita: falha de servidor vira resultado', async () => {
    const result = gateway.send('abc', 'Gostei');
    http
      .expectOne('https://api.exemplo.com/api/feedbacks')
      .flush({}, { status: 500, statusText: 'Server Error' });
    expect(await result).toEqual({ ok: false, reason: 'unavailable' });
  });
});

describe('classify', () => {
  const failure = (status: number) => classify(new HttpErrorResponse({ status }));

  it.each([
    [0, 'offline'],
    [400, 'invalid'],
    [413, 'invalid'],
    [422, 'invalid'],
    [429, 'busy'],
    [401, 'unavailable'],
    [500, 'unavailable'],
    [503, 'unavailable'],
  ])('status %i vira %s', (status, reason) => {
    expect(failure(status)).toEqual({ ok: false, reason });
  });

  it('erro que não é HTTP vira indisponível', () => {
    expect(classify(new Error('boom'))).toEqual({ ok: false, reason: 'unavailable' });
  });
});
