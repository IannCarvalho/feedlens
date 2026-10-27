"""Chamada à LLM via LiteLLM, percorrendo a cadeia de credenciais.

Cada credencial é tentada em ordem (com o timeout e os retries do próprio
LiteLLM); a primeira resposta não vazia vence. Toda falha vira uma
``Attempt`` com a chave removida da mensagem, e se nenhuma credencial
responde a cadeia inteira levanta ``LLMRequestError``.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.llm.credentials import Credential
from app.llm.errors import Attempt, LLMNotConfiguredError, LLMRequestError

logger = logging.getLogger(__name__)

_litellm = None
_import_lock = threading.Lock()


@dataclass(frozen=True)
class Completion:
    content: str
    model_ref: str          # 'provedor/modelo' que respondeu
    source: str             # 'server' | 'stored' | 'request'
    latency_ms: int
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


def get_litellm():
    """Importa o LiteLLM sob demanda: ~200 MB e segundos de import que boot,
    CLI e testes não precisam pagar."""
    global _litellm
    with _import_lock:
        if _litellm is None:
            # Usa o mapa de modelos/preços embutido no pacote, em vez de
            # baixá-lo do GitHub a cada import (lento e falha sem internet).
            os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
            import litellm

            # Sem o banner "Give Feedback / Get Help" a cada exceção.
            litellm.suppress_debug_info = True
            # Em DEBUG o LiteLLM loga o payload, que é texto de usuário.
            logging.getLogger('LiteLLM').setLevel(logging.WARNING)
            _litellm = litellm
    return _litellm


def warm_up() -> None:
    """Pré-carrega o LiteLLM (o servidor chama numa thread ao subir)."""
    try:
        get_litellm()
    except Exception as e:  # noqa: BLE001 - aquecer é otimização, nunca erro
        logger.warning('LiteLLM warm-up failed: %s', e)


def _classify(litellm: Any, error: Exception) -> str:
    # Ordem importa: Timeout herda de APIConnectionError no SDK da OpenAI e
    # ContextWindowExceededError herda de BadRequestError.
    checks = (
        ('auth', (litellm.AuthenticationError, litellm.PermissionDeniedError)),
        ('rate_limit', (litellm.RateLimitError,)),
        ('timeout', (litellm.Timeout,)),
        ('not_found', (litellm.NotFoundError,)),
        ('bad_request', (litellm.BadRequestError, litellm.UnprocessableEntityError)),
        ('connection', (litellm.APIConnectionError, litellm.ServiceUnavailableError,
                        litellm.InternalServerError)),
    )
    for kind, types in checks:
        if isinstance(error, types):
            return kind
    return 'error'


def _redact(text: str, secret: str) -> str:
    text = text.replace(secret, '***') if secret else text
    return text[:500]


def _cost(litellm: Any, response: Any) -> Optional[float]:
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:  # noqa: BLE001 - modelo sem preço no mapa: custo desconhecido
        return None


def _notify_failure(hook: Callable[[Credential, Attempt], None],
                    credential: Credential, attempt: Attempt) -> None:
    """Avisa quem observa a falha sem poder quebrá-la.

    O gancho existe para a cadeia registrar no banco a chave que o provedor
    recusou (``credential_store.record_permanent_failure``). Contabilidade nunca
    pode derrubar a análise: qualquer erro do gancho morre aqui.
    """
    try:
        hook(credential, attempt)
    except Exception:  # noqa: BLE001 - observador, jamais o caminho crítico
        logger.exception('LLM failure hook raised; the chain continues')


def complete(
    messages: list[dict],
    chain: list[Credential],
    *,
    temperature: float = 0.0,
    json_mode: bool = False,
    timeout: int = 30,
    max_retries: int = 2,
    max_tokens: Optional[int] = None,
    on_failure: Optional[Callable[[Credential, 'Attempt'], None]] = None,
    validate_content: Optional[Callable[[str], bool]] = None,
) -> Completion:
    """Executa a conversa no primeiro provedor da cadeia que responder.

    ``on_failure`` é chamado a cada tentativa que falha, antes de passar para a
    próxima credencial — inclusive quando uma reserva acaba respondendo. É por
    ali que a chave recusada por autenticação é marcada no banco, para não ser
    tentada de novo em todo feedback.

    ``validate_content`` diz se a resposta serve para quem chamou (o analisador
    passa o parser de JSON). Uma resposta que chega mas não serve conta como
    falha daquela credencial e a cadeia segue: um provedor que responde prosa
    não pode deixar o feedback sem análise enquanto a reserva responderia.
    """
    if not chain:
        raise LLMNotConfiguredError()

    litellm = get_litellm()
    attempts: list[Attempt] = []

    def fail(credential: Credential, kind: str, detail: str) -> None:
        attempt = Attempt(credential.model_ref, kind, detail)
        attempts.append(attempt)
        if on_failure is not None:
            _notify_failure(on_failure, credential, attempt)

    for credential in chain:
        started = time.perf_counter()
        options: dict[str, Any] = {}
        if max_tokens is not None:
            options['max_tokens'] = max_tokens
        if json_mode:
            # JSON nativo na OpenAI e no Gemini; na Anthropic o LiteLLM ignora
            # (sem schema) e o prompt + o parser tolerante cobrem.
            options['response_format'] = {'type': 'json_object'}
        try:
            response = litellm.completion(
                model=credential.model_ref,
                messages=messages,
                api_key=credential.api_key,
                temperature=temperature,
                timeout=timeout,
                num_retries=max_retries,
                # Parâmetro não suportado pelo modelo (ex.: temperature nos
                # modelos de raciocínio) é descartado em vez de virar erro 400.
                drop_params=True,
                **options,
            )
        except Exception as e:  # noqa: BLE001 - toda falha segue para o próximo provedor
            kind = _classify(litellm, e)
            detail = _redact(str(e), credential.api_key)
            logger.warning('LLM call to %s failed (%s): %s',
                           credential.model_ref, kind, detail[:200])
            fail(credential, kind, detail)
            continue

        content = (response.choices[0].message.content or '').strip()
        if not content:
            logger.warning('LLM %s returned an empty response', credential.model_ref)
            fail(credential, 'empty', 'resposta vazia')
            continue

        if validate_content is not None:
            try:
                usable = bool(validate_content(content))
            except Exception:  # noqa: BLE001 - validador quebrado não derruba a análise
                logger.exception('validate_content raised; response treated as unusable')
                usable = False
            if not usable:
                # A resposta chegou, mas não serve. Tentar a próxima credencial
                # custa uma chamada; não tentar custa o feedback, que ficaria
                # "não analisado" mesmo com uma reserva capaz de responder.
                logger.warning('LLM %s returned an unusable response '
                               '(invalid_response); trying the next credential',
                               credential.model_ref)
                fail(credential, 'invalid_response', 'resposta não serve ao chamador')
                continue

        usage = getattr(response, 'usage', None)
        completion = Completion(
            content=content,
            model_ref=credential.model_ref,
            source=credential.source,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=getattr(usage, 'prompt_tokens', None),
            completion_tokens=getattr(usage, 'completion_tokens', None),
            cost_usd=_cost(litellm, response),
        )
        logger.info('LLM %s answered in %dms (tokens in=%s out=%s, cost_usd=%s)',
                    completion.model_ref, completion.latency_ms, completion.prompt_tokens,
                    completion.completion_tokens, completion.cost_usd)
        return completion

    raise LLMRequestError(attempts)
