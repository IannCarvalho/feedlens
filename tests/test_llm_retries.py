"""Os retries do LiteLLM (``num_retries``) dependem do ``tenacity``.

Sem ele, QUALQUER falha de uma chamada (timeout, cota, 5xx) virava a exceção
genérica "tenacity import failed": o retry nunca acontecia, o erro real ficava
escondido e o feedback era gravado com o resultado seguro padrão (INCONCLUSIVO,
sem spam), como se a IA tivesse analisado e não soubesse dizer.
"""
import importlib.util

from app.llm.client import get_litellm


def test_tenacity_is_declared_and_installed():
    assert importlib.util.find_spec("tenacity") is not None


def test_a_failing_call_with_retries_surfaces_the_real_error():
    litellm = get_litellm()
    try:
        litellm.completion(
            model="gemini/gemini-2.0-flash",
            messages=[{"role": "user", "content": "oi"}],
            num_retries=1,
            mock_response=litellm.Timeout(
                message="simulado", model="gemini-2.0-flash", llm_provider="gemini"),
        )
    except Exception as error:  # noqa: BLE001 - o tipo é justamente o que se verifica
        assert isinstance(error, litellm.Timeout), repr(error)
        assert "tenacity" not in str(error)
    else:  # pragma: no cover
        raise AssertionError("a chamada deveria falhar")
