"""Envoltorio de cliente OpenAI para generación y validación de respuestas."""

import re
from threading import Lock
import unicodedata

from openai import OpenAI
import requests

from coderag.core.settings import ProviderName, get_settings
from coderag.llm.prompts import (
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_verify_prompt,
)


UNAVAILABLE_ANSWER_TEXT = "No se encontró información en el repositorio."


def _normalize_verifier_result(value: str) -> str:
    """Normalice el texto del verificador para un análisis sólido del veredicto."""
    lowered = value.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", without_marks)


def _is_verifier_result_valid(value: str) -> bool:
    """Interprete el veredicto del verificador a partir de la salida de texto libre normalizada."""
    normalized = _normalize_verifier_result(value)
    if not normalized:
        return False

    if re.search(
        r"\b(no|sin)\b(?:\s+\w+){0,8}\s+"
        r"(invalido|invalid|hallucination|hallucinated)\b",
        normalized,
    ):
        return True

    if re.search(r"\b(invalido|invalid|hallucination|hallucinated)\b", normalized):
        return False

    if re.search(r"\b(valido|valid)\b", normalized):
        return True

    positive_support_signals = (
        "sustent",
        "evidencia suficiente",
        "coincide con",
        "alinead",
        "grounded",
        "supported",
        "consistent",
    )
    if len(normalized) >= 40 and any(
        signal in normalized for signal in positive_support_signals
    ):
        return True

    return False


def _timeout_value(timeout_seconds: float | None) -> float:
    """Normaliza timeout de requests REST con mínimo seguro."""
    return max(1.0, float(timeout_seconds or 20.0))


def _model_path(model: str) -> str:
    """Normaliza ruta de modelo para APIs que requieren prefijo models/."""
    if model.startswith("models/"):
        return model
    return f"models/{model}"


def _vertex_model_name(model: str) -> str:
    """Normaliza nombre de modelo para rutas de publisher models en Vertex."""
    if model.startswith("models/"):
        return model.split("/", 1)[1]
    return model


def _build_generate_content_payload(prompt: str, *, vertex: bool) -> dict:
    """Construye payload generateContent para Gemini o Vertex."""
    system_key = "systemInstruction" if vertex else "system_instruction"
    return {
        system_key: {
            "parts": [{"text": SYSTEM_PROMPT}],
        },
        "contents": [
            {
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
        },
    }


def _extract_text_parts(parts: list[dict]) -> str:
    """Concatena partes de texto de payloads REST generativos."""
    text_parts = [item.get("text", "") for item in parts if isinstance(item, dict)]
    return "\n".join(part for part in text_parts if part).strip()


def _extract_generative_text(data: dict) -> str:
    """Extrae texto de respuesta en formato candidates/content/parts."""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) else []
    if not isinstance(parts, list):
        return ""
    return _extract_text_parts(parts)


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    """Detecta errores del provider cuando el modelo no permite temperature=0."""
    message = str(exc).lower()
    return (
        "temperature" in message
        and "unsupported" in message
        and ("does not support 0" in message or "unsupported_value" in message)
    )


class AnswerClient:
    """Servicio multi-provider para generación y validación de respuestas."""

    _shared_client: OpenAI | None = None
    _shared_api_key: str | None = None
    _client_lock: Lock = Lock()

    def __init__(
        self,
        provider: str | None = None,
        answer_model: str | None = None,
        verifier_model: str | None = None,
    ) -> None:
        """Inicialice el cliente con provider/modelos opcionales por operación."""
        settings = get_settings()
        if hasattr(settings, "resolve_llm_provider"):
            self.provider = settings.resolve_llm_provider(provider)
        else:
            self.provider = "openai"

        if hasattr(settings, "resolve_api_key"):
            self.api_key = settings.resolve_api_key(self.provider)
        else:
            self.api_key = getattr(settings, "openai_api_key", "")

        if hasattr(settings, "resolve_answer_model"):
            self.answer_model = settings.resolve_answer_model(
                self.provider,
                answer_model,
            )
        elif answer_model and answer_model.strip():
            self.answer_model = answer_model.strip()
        else:
            self.answer_model = getattr(settings, "openai_answer_model", "gpt-4.1-mini")

        if hasattr(settings, "resolve_verifier_model"):
            self.verifier_model = settings.resolve_verifier_model(
                self.provider,
                verifier_model,
            )
        elif verifier_model and verifier_model.strip():
            self.verifier_model = verifier_model.strip()
        else:
            self.verifier_model = getattr(
                settings,
                "openai_verifier_model",
                "gpt-4.1-mini",
            )

        self.client = self._resolve_client(
            provider=self.provider,
            api_key=self.api_key,
        )

    @classmethod
    def _resolve_client(
        cls,
        provider: ProviderName,
        api_key: str,
    ) -> OpenAI | None:
        """Reutiliza cliente OpenAI cuando aplique; otros providers usan REST."""
        if provider != "openai":
            return None
        if not api_key:
            return None
        with cls._client_lock:
            if cls._shared_client is None or cls._shared_api_key != api_key:
                cls._shared_client = OpenAI(api_key=api_key)
                cls._shared_api_key = api_key
            return cls._shared_client

    def _call(
        self,
        model: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Ejecute la llamada API del provider activo y devuelva texto plano."""
        if not self.enabled:
            return UNAVAILABLE_ANSWER_TEXT

        if self.provider == "openai":
            return self._call_openai(model, prompt, timeout_seconds=timeout_seconds)
        if self.provider == "anthropic":
            return self._call_anthropic(model, prompt, timeout_seconds=timeout_seconds)
        if self.provider == "gemini":
            return self._call_gemini(model, prompt, timeout_seconds=timeout_seconds)
        if self.provider == "vertex_ai":
            return self._call_vertex_ai(model, prompt, timeout_seconds=timeout_seconds)
        return UNAVAILABLE_ANSWER_TEXT

    def _call_openai(
        self,
        model: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Ejecuta llamada OpenAI Responses/Chat y retorna texto plano."""
        if self.client is None:
            return ""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        request_kwargs: dict[str, object] = {}
        if timeout_seconds is not None:
            request_kwargs["timeout"] = max(1.0, float(timeout_seconds))

        if hasattr(self.client, "responses"):
            response = self.client.responses.create(
                model=model,
                input=messages,
                **request_kwargs,
            )
            return (response.output_text or "").strip()

        try:
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                **request_kwargs,
            )
        except Exception as exc:
            if not _is_unsupported_temperature_error(exc):
                raise
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages,
                **request_kwargs,
            )
        content = completion.choices[0].message.content
        return (content or "").strip()

    def _call_anthropic(
        self,
        model: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Llama a Anthropic Messages API con prompt equivalente."""
        if not self.api_key:
            return ""
        payload = {
            "model": model,
            "max_tokens": 2048,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        timeout_value = _timeout_value(timeout_seconds)
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=timeout_value,
        )
        response.raise_for_status()
        data = response.json()
        chunks = data.get("content") or []
        text_parts = [
            item.get("text", "")
            for item in chunks
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in text_parts if part).strip()

    def _call_gemini(
        self,
        model: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Llama a Gemini API REST para generar respuesta textual."""
        if not self.api_key:
            return ""

        model_path = _model_path(model)

        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"{model_path}:generateContent"
        )
        payload = _build_generate_content_payload(prompt, vertex=False)
        timeout_value = _timeout_value(timeout_seconds)
        response = requests.post(
            url,
            params={"key": self.api_key},
            json=payload,
            timeout=timeout_value,
        )
        response.raise_for_status()
        return _extract_generative_text(response.json())

    def _call_vertex_ai(
        self,
        model: str,
        prompt: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Llama a Vertex AI generateContent usando token OAuth en VERTEX_AI_API_KEY."""
        settings = get_settings()
        project = getattr(settings, "vertex_ai_project_id", "")
        location = getattr(settings, "vertex_ai_location", "us-central1")
        if not self.api_key or not project:
            return ""

        model_name = _vertex_model_name(model)

        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/"
            f"locations/{location}/publishers/google/models/{model_name}:generateContent"
        )
        payload = _build_generate_content_payload(prompt, vertex=True)
        timeout_value = _timeout_value(timeout_seconds)
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_value,
        )
        response.raise_for_status()
        return _extract_generative_text(response.json())

    def answer(
        self,
        query: str,
        context: str,
        timeout_seconds: float | None = None,
    ) -> str:
        """Genere una respuesta basada en el contexto para una pregunta de un usuario."""
        prompt = build_answer_prompt(query=query, context=context)
        return self._call(
            self.answer_model,
            prompt,
            timeout_seconds=timeout_seconds,
        )

    @property
    def enabled(self) -> bool:
        """Devuelve si la generación del provider activo está habilitada."""
        if self.provider == "openai":
            return self.client is not None
        if self.provider in {"anthropic", "gemini"}:
            return bool(self.api_key)
        if self.provider == "vertex_ai":
            settings = get_settings()
            if hasattr(settings, "is_vertex_ai_configured"):
                return bool(settings.is_vertex_ai_configured())
            return bool(self.api_key and getattr(settings, "vertex_ai_project_id", ""))
        return False

    def verify(
        self,
        answer: str,
        context: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Valida si la respuesta está sustentada en el contexto proporcionado."""
        if not self.enabled:
            return True

        prompt = build_verify_prompt(answer=answer, context=context)
        result = self._call(
            self.verifier_model,
            prompt,
            timeout_seconds=timeout_seconds,
        )
        return _is_verifier_result_valid(result)
