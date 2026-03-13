"""Envoltorio de cliente OpenAI para generación y validación de respuestas."""

import re
from threading import Lock
import unicodedata

from openai import OpenAI

from coderag.core.settings import get_settings
from coderag.llm.prompts import (
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_verify_prompt,
)


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

    if re.search(r"\b(invalido|invalid|hallucination|hallucinated)\b", normalized):
        return False

    if re.search(r"\b(valido|valid)\b", normalized):
        return True

    return False


class AnswerClient:
    """Servicio que llama a la API OpenAI Responses con respaldos seguros."""

    _shared_client: OpenAI | None = None
    _shared_api_key: str | None = None
    _client_lock: Lock = Lock()

    def __init__(self) -> None:
        """Inicialice el cliente OpenAI desde el entorno."""
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.answer_model = settings.openai_answer_model
        self.verifier_model = settings.openai_verifier_model
        self.client = self._resolve_client(api_key=self.api_key)

    @classmethod
    def _resolve_client(cls, api_key: str) -> OpenAI | None:
        """Reutiliza el cliente OpenAI mientras la API key no cambie."""
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
        """Ejecute la llamada API de Responses y devuelva resultados de texto sin formato."""
        if self.client is None:
            return "No se encontró información en el repositorio."

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

        completion = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            **request_kwargs,
        )
        content = completion.choices[0].message.content
        return (content or "").strip()

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
        """Devuelve si la generación respaldada por OpenAI está habilitada."""
        return self.client is not None

    def verify(
        self,
        answer: str,
        context: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Valida si la respuesta está sustentada en el contexto proporcionado."""
        if self.client is None:
            return True

        prompt = build_verify_prompt(answer=answer, context=context)
        result = self._call(
            self.verifier_model,
            prompt,
            timeout_seconds=timeout_seconds,
        )
        return _is_verifier_result_valid(result)
