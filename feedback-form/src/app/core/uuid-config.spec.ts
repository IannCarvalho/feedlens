import { newId } from './uuid';
import { loadConfig, DEFAULT_CONFIG } from './app-config';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('newId', () => {
  it('gera UUID v4 únicos', () => {
    const ids = new Set(Array.from({ length: 50 }, () => newId()));
    expect(ids.size).toBe(50);
    ids.forEach((id) => expect(id).toMatch(UUID_V4));
  });

  it('gera UUID v4 válido mesmo sem crypto.randomUUID (contexto não seguro)', () => {
    const original = globalThis.crypto.randomUUID;
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      value: undefined,
      configurable: true,
    });
    try {
      expect(newId()).toMatch(UUID_V4);
    } finally {
      Object.defineProperty(globalThis.crypto, 'randomUUID', {
        value: original,
        configurable: true,
      });
    }
  });
});

describe('loadConfig', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('lê config.json e tira a barra final da URL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          apiBaseUrl: 'https://api.exemplo.com//',
          useMock: false,
          productName: ' Loja ',
        }),
      }),
    );
    expect(await loadConfig()).toEqual({
      apiBaseUrl: 'https://api.exemplo.com',
      useMock: false,
      productName: 'Loja',
    });
  });

  it('cai no modo demonstração se o arquivo não existe ou falha', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    expect(await loadConfig()).toEqual(DEFAULT_CONFIG);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('rede')));
    expect(await loadConfig()).toEqual(DEFAULT_CONFIG);
  });
});
