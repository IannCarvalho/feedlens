import { InjectionToken } from '@angular/core';

/**
 * Configuração lida de `config.json` em tempo de execução (não entra no bundle).
 * Assim o mesmo artefato serve teste e produção: só o arquivo muda.
 */
export interface AppConfig {
  /** Origem da API do FeedLens, sem barra final. Ex.: https://api.exemplo.com */
  readonly apiBaseUrl: string;
  /** Usa o gateway de demonstração (dados mockados) em vez da API real. */
  readonly useMock: boolean;
  /** Nome do produto citado nos textos, na voz do cliente final. */
  readonly productName: string;
}

export const DEFAULT_CONFIG: AppConfig = {
  apiBaseUrl: '',
  useMock: true,
  productName: 'nosso produto',
};

export const APP_CONFIG = new InjectionToken<AppConfig>('APP_CONFIG', {
  providedIn: 'root',
  factory: () => DEFAULT_CONFIG,
});

/** Limites do contrato de ingestão (MAX_FEEDBACK_CHARS=5000 no backend). */
export const FEEDBACK_MAX_CHARS = 5000;
export const FEEDBACK_MIN_CHARS = 5;

/**
 * Lê `config.json`. Qualquer falha cai no padrão (modo demonstração), para o
 * app nunca ficar em branco por causa de um arquivo ausente.
 */
export async function loadConfig(url = 'config.json'): Promise<AppConfig> {
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return DEFAULT_CONFIG;
    }
    const raw = (await response.json()) as Partial<AppConfig>;
    return {
      apiBaseUrl: (raw.apiBaseUrl ?? DEFAULT_CONFIG.apiBaseUrl).replace(/\/+$/, ''),
      useMock: raw.useMock ?? DEFAULT_CONFIG.useMock,
      productName: raw.productName?.trim() || DEFAULT_CONFIG.productName,
    };
  } catch {
    return DEFAULT_CONFIG;
  }
}
