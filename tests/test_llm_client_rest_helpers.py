"""Pruebas unitarias para helpers REST internos del cliente LLM."""

from coderag.llm.openai_client import (
    AnswerClient,
    _build_generate_content_payload,
    _extract_generative_text,
    _extract_openai_responses_text,
    _is_openai_model_selection_error,
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


def test_extract_openai_responses_text_from_output_text() -> None:
    """Prioriza output_text cuando está disponible."""
    payload = {"output_text": "respuesta directa"}
    assert _extract_openai_responses_text(payload) == "respuesta directa"


def test_extract_openai_responses_text_from_output_items() -> None:
    """Extrae texto cuando output_text no viene poblado."""
    payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "linea uno"},
                    {"type": "output_text", "text": "linea dos"},
                ]
            }
        ]
    }
    assert _extract_openai_responses_text(payload) == "linea uno\nlinea dos"


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


def test_is_openai_model_selection_error_detects_model_access_errors() -> None:
    """Detecta mensajes típicos de modelo sin acceso/no disponible."""
    exc = Exception("The model `gpt-5-pro` does not exist or you do not have access")
    assert _is_openai_model_selection_error(exc) is True


def test_is_openai_model_selection_error_detects_responses_only_message() -> None:
    """Reconoce error cuando el modelo solo soporta v1/responses."""
    exc = Exception(
        "This model is only supported in v1/responses and not in v1/chat/completions."
    )
    assert _is_openai_model_selection_error(exc) is True


def test_call_openai_falls_back_to_safe_model_when_selected_model_fails(monkeypatch) -> None:
    """Si el modelo elegido falla por acceso, usa fallback de catálogo."""

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
            return (override or "gpt-5-pro").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gpt-5-pro").strip()

    class _Response:
        output_text = "respuesta fallback ok"

    class _Responses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("model") == "gpt-5-pro":
                raise Exception("The model `gpt-5-pro` does not exist or you do not have access")
            return _Response()

    class _ChatCompletions:
        def create(self, **kwargs):
            if kwargs.get("model") == "gpt-5-pro":
                raise Exception("The model `gpt-5-pro` does not exist or you do not have access")
            raise Exception("Unexpected chat call for fallback model")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _ChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _Responses()
            self.chat = _Chat()

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())

    client = AnswerClient(provider="openai", answer_model="gpt-5-pro")
    client.client = _FakeClient()

    result = client._call_openai("gpt-5-pro", "hola", timeout_seconds=5)

    assert result == "respuesta fallback ok"
    attempted_models = [
        call.get("model") for call in client.client.responses.calls
    ]
    assert attempted_models[0] == "gpt-5-pro"
    assert "gpt-4.1-mini" in attempted_models


def test_call_openai_uses_responses_text_input_first(monkeypatch) -> None:
    """Prioriza payload de Responses con instructions + input textual."""

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
            return (override or "gpt-5-pro").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gpt-5-pro").strip()

    class _Response:
        output_text = "respuesta responses ok"

    class _Responses:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Response()

    class _ChatCompletions:
        def create(self, **kwargs):  # noqa: ARG002
            raise Exception("Chat should not be used when responses succeeds")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _ChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _Responses()
            self.chat = _Chat()

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())

    client = AnswerClient(provider="openai", answer_model="gpt-5-pro")
    client.client = _FakeClient()

    result = client._call_openai("gpt-5-pro", "hola", timeout_seconds=5)

    assert result == "respuesta responses ok"
    first_call = client.client.responses.calls[0]
    assert first_call.get("input") == "hola"
    assert first_call.get("instructions")


def test_call_openai_uses_rest_responses_when_sdk_has_no_responses(monkeypatch) -> None:
    """Si el SDK no expone responses, usa REST /v1/responses."""

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
            return (override or "gpt-5-pro").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gpt-5-pro").strip()

    class _FakeRestResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"output_text": "respuesta rest ok"}

    class _ChatCompletions:
        def create(self, **kwargs):  # noqa: ARG002
            raise Exception("Chat should not be used when REST responses succeeds")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _ChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = _Chat()

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        "coderag.llm.openai_client.requests.post",
        lambda *args, **kwargs: _FakeRestResponse(),
    )

    client = AnswerClient(provider="openai", answer_model="gpt-5-pro")
    client.client = _FakeClient()

    result = client._call_openai("gpt-5-pro", "hola", timeout_seconds=5)
    assert result == "respuesta rest ok"


def test_call_vertex_ai_returns_text_from_candidates(monkeypatch) -> None:
    """Vertex extrae contenido de candidates/content/parts correctamente."""

    class _Settings:
        vertex_ai_api_key = "vertex-token"
        vertex_ai_project_id = "test-project"
        vertex_ai_location = "us-central1"

        @staticmethod
        def resolve_llm_provider(provider: str | None = None) -> str:
            return (provider or "vertex_ai").strip().lower()

        @staticmethod
        def resolve_api_key(provider: str) -> str:
            _ = provider
            return "vertex-token"

        @staticmethod
        def resolve_answer_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gemini-2.0-flash").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gemini-2.0-flash").strip()

    class _FakeResponse:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "respuesta vertex ok"}],
                        }
                    }
                ]
            }

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        "coderag.llm.openai_client.requests.post",
        lambda *args, **kwargs: _FakeResponse(),
    )

    client = AnswerClient(provider="vertex_ai", answer_model="gemini-2.0-flash")
    result = client._call_vertex_ai("gemini-2.0-flash", "hola", timeout_seconds=5)

    assert result == "respuesta vertex ok"


def test_call_vertex_ai_returns_empty_when_project_not_configured(monkeypatch) -> None:
    """Vertex devuelve vacío sin proyecto configurado y evita llamada remota."""

    class _Settings:
        vertex_ai_api_key = "vertex-token"
        vertex_ai_project_id = ""
        vertex_ai_location = "us-central1"

        @staticmethod
        def resolve_llm_provider(provider: str | None = None) -> str:
            return (provider or "vertex_ai").strip().lower()

        @staticmethod
        def resolve_api_key(provider: str) -> str:
            _ = provider
            return "vertex-token"

        @staticmethod
        def resolve_answer_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gemini-2.0-flash").strip()

        @staticmethod
        def resolve_verifier_model(provider: str, override: str | None = None) -> str:
            _ = provider
            return (override or "gemini-2.0-flash").strip()

    monkeypatch.setattr("coderag.llm.openai_client.get_settings", lambda: _Settings())

    def _unexpected_post(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("requests.post should not be called without project")

    monkeypatch.setattr("coderag.llm.openai_client.requests.post", _unexpected_post)

    client = AnswerClient(provider="vertex_ai", answer_model="gemini-2.0-flash")
    result = client._call_vertex_ai("gemini-2.0-flash", "hola", timeout_seconds=5)

    assert result == ""
