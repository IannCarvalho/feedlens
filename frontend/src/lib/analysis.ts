/*
 * Feedbacks que a IA não conseguiu analisar: o motivo, em linguagem simples.
 * Os códigos vêm do backend (`analysis_error`, o `kind` da última tentativa).
 */

const REASONS: Record<string, string> = {
  rate_limit: 'a cota do provedor de IA foi excedida',
  timeout: 'o provedor de IA demorou demais para responder',
  connection: 'não foi possível falar com o provedor de IA',
  auth: 'o provedor de IA recusou a chave',
  not_found: 'o modelo configurado não foi encontrado no provedor',
  bad_request: 'o provedor de IA recusou o pedido',
  empty: 'o provedor de IA devolveu uma resposta vazia',
  invalid_response: 'a IA devolveu uma resposta que o sistema não conseguiu ler',
}

export function analysisErrorLabel(kind: string | null | undefined): string {
  return (kind && REASONS[kind]) || 'o provedor de IA falhou'
}
