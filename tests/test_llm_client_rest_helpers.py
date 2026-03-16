"""Pruebas unitarias para helpers REST internos del cliente LLM."""

from coderag.llm.openai_client import (
    AnswerClient,
    _build_generate_content_payload,
    _extract_generative_text,
    _is_unsupported_temperature_error,
    _model_path,
    _timeout_value,
    _vertex_model_name,
)


def test_timeout_value_enforces_minimum() -> None:
    """Normaliza timeout con piso de 1 segundo."""
    assert _timeout_value(None) == 20.0
    assert _timeout_value(0.1) == 1.0
    assert _timeout_value(5.0) == 5.0


def test_model_path_adds_prefix_when_missing() -> None:
    """Agrega prefijo models/ solo cuando hace falta."""
    assert _model_path("gemini-2.0-flash") == "models/gemini-2.0-flash"
    assert _model_path("models/gemini-2.0-flash") == "models/gemini-2.0-flash"


def test_vertex_model_name_strips_models_prefix() -> None:
    """El nombre de modelo para Vertex no conserva el prefijo models/."""
    assert _vertex_model_name("models/gemini-2.0-flash") == "gemini-2.0-flash"
    assert _vertex_model_name("gemini-2.0-flash") == "gemini-2.0-flash"


def test_build_generate_content_payload_uses_provider_specific_system_key() -> None:
    """Gemini usa system_instruction y Vertex usa systemInstruction."""
    gemini_payload = _build_generate_content_payload("hola", vertex=False)
    vertex_payload = _build_generate_content_payload("hola", vertex=True)

    assert "system_instruction" in gemini_payload
    assert "systemInstruction" not in gemini_payload
    assert "systemInstruction" in vertex_payload
    assert "system_instruction" not in vertex_payload
    assert gemini_payload["generationConfig"]["temperature"] == 0
    assert vertex_payload["generationConfig"]["temperature"] == 0


def test_extract_generative_text_handles_empty_candidates() -> None:
    """Devuelve vacío cuando no hay candidates."""
    assert _extract_generative_text({}) == ""
    assert _extract_generative_text({"candidates": []}) == ""


def test_extract_generative_text_joins_parts() -> None:
    """Concatena partes textuales del primer candidate."""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "linea 1"},
                        {"text": "linea 2"},
                    ]
                }
            }
        ]
    }

    assert _extract_generative_text(payload) == "linea 1\nlinea 2"


def test_is_unsupported_temperature_error_detects_openai_message() -> None:
    """Identifica errores típicos de OpenAI cuando no acepta temperature=0."""
    exc = Exception(
        "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' "
        "does not support 0 with this model. Only the default (1) value is "
        "supported.\", 'type': 'invalid_request_error', 'param': 'temperature', "
        "'code': 'unsupported_value'}}"
    )
    assert _is_unsupported_temperature_error(exc) is True


def test_call_openai_retries_without_temperature(monkeypatch) -> None:
    """Si el modelo rechaza temperature=0, reintenta sin ese parámetro."""

    class _Settings:
        openai_api_key = "key"

        @staticmethod
        def resolve_llm_provider(provider: str | None = None) -> str:
            return (provider or "openai").strip().lower()

        @staticmethod
        def resolve_api_key(provider: str) -> str:
            _ = provider
            return "key"

        @staticmethod
        def resolve_answer_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gpt-5-mini").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gpt-5-mini").strip()

    class _Message:
        content = "respuesta ok"

    class _Choice:
        message = _Message()

    class _Completion:
        choices = [_Choice()]

    class _ChatCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise Exception(
                    "Unsupported value: 'temperature' does not support 0 with this "
                    "model. Only the default (1) value is supported."
                )
            return _Completion()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _ChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())

    client = AnswerClient(provider="openai", answer_model="gpt-5-mini")
    client.client = _FakeClient()

    result = client._call_openai("gpt-5-mini", "hola", timeout_seconds=5)

    assert result == "respuesta ok"
    calls = client.client.chat.completions.calls
    assert len(calls) == 2
    assert calls[0].get("temperature") == 0
    assert "temperature" not in calls[1]
