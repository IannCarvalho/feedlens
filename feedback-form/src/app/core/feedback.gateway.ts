/**
 * Contrato entre a tela e o envio. A tela só conhece estes tipos: nada de
 * status HTTP, cabeçalhos ou formato da API chega até ela.
 */
export type SendFailure =
  /** O texto não foi aceito (vazio ou grande demais). */
  | 'invalid'
  /** Muitos envios em pouco tempo; tentar de novo daqui a pouco. */
  | 'busy'
  /** Sem conexão com o serviço (rede caiu, serviço fora do ar). */
  | 'offline'
  /** Qualquer outra falha do serviço. */
  | 'unavailable';

export type SendOutcome =
  { readonly ok: true } | { readonly ok: false; readonly reason: SendFailure };

export abstract class FeedbackGateway {
  /**
   * Envia um feedback. `id` é gerado pela tela e reaproveitado nas
   * tentativas: reenviar o mesmo `id` é seguro (o serviço trata como repetido).
   * Nunca rejeita: toda falha vira `{ ok: false }`.
   */
  abstract send(id: string, text: string): Promise<SendOutcome>;

  /** Acorda o serviço antes do envio (opcional, sem efeito colateral). */
  warmUp(): void {
    // Sem efeito por padrão; só o gateway HTTP tem o que acordar.
  }
}
