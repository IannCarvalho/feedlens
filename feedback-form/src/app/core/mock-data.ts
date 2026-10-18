/**
 * Dados de demonstração. Só são usados com `useMock: true` (config.json):
 * servem para validar a tela sem backend, inclusive os caminhos de erro.
 */

export type MockScenarioId =
  'sucesso' | 'repetido' | 'lento' | 'muitos-envios' | 'instavel' | 'sem-conexao' | 'recusado';

export interface MockScenario {
  readonly id: MockScenarioId;
  readonly label: string;
  /** Tempo simulado até a resposta. */
  readonly delayMs: number;
}

export const MOCK_SCENARIOS: readonly MockScenario[] = [
  { id: 'sucesso', label: 'Envio normal', delayMs: 900 },
  { id: 'repetido', label: 'Feedback já recebido antes', delayMs: 700 },
  { id: 'lento', label: 'Resposta lenta (8 s)', delayMs: 8000 },
  { id: 'muitos-envios', label: 'Muitos envios seguidos', delayMs: 500 },
  { id: 'instavel', label: 'Serviço instável', delayMs: 900 },
  { id: 'sem-conexao', label: 'Sem conexão', delayMs: 600 },
  { id: 'recusado', label: 'Texto recusado', delayMs: 500 },
];

export const DEFAULT_MOCK_SCENARIO: MockScenarioId = 'sucesso';

export function isMockScenario(value: string | null): value is MockScenarioId {
  return MOCK_SCENARIOS.some((scenario) => scenario.id === value);
}

/** Exemplos para preencher o campo com um clique (modo demonstração). */
export const SAMPLE_FEEDBACKS: readonly string[] = [
  'Adorei o novo painel, ficou muito mais rápido de achar o que eu preciso. Só senti falta de poder exportar o relatório em PDF.',
  'O aplicativo travou duas vezes hoje quando tentei anexar uma foto. Perdi o que já tinha escrito.',
  'Atendimento excelente! A Carla resolveu meu problema de cobrança em poucos minutos.',
  'Sugiro um modo escuro. Uso à noite e a tela branca cansa bastante.',
  'Não consegui entender como cancelar a assinatura. O botão não está onde eu esperava.',
];

/** Feedbacks "já recebidos", para a demonstração mostrar o histórico da sessão. */
export interface MockReceivedFeedback {
  readonly id: string;
  readonly feedback: string;
  readonly receivedAt: string;
}

export const SEED_RECEIVED: readonly MockReceivedFeedback[] = [
  {
    id: '3b0b2a52-9d1e-4f17-8a8c-5a8f6a1d0001',
    feedback: 'Gostei muito da última atualização, o login ficou bem mais simples.',
    receivedAt: '2026-10-17T14:02:11-03:00',
  },
  {
    id: '3b0b2a52-9d1e-4f17-8a8c-5a8f6a1d0002',
    feedback: 'A entrega atrasou três dias e ninguém avisou.',
    receivedAt: '2026-10-17T16:45:30-03:00',
  },
];
