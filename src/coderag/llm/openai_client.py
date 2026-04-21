"""Envoltorio de cliente OpenAI para generación y validación de respuestas."""

import re
import logging
from threading import Lock
import unicodedata
from uuid import uuid4

from openai import OpenAI
import requests

from coderag.core.provider_model_catalog import (
    default_llm_model,
    llm_models_for_provider,
    normalize_provider_name,
)
from coderag.core.settings import ProviderName, get_settings
from coderag.core.vertex_ai import (
    build_vertex_api_url,
    build_vertex_labels,
    resolve_vertex_auth_context,
)
from coderag.llm.prompts import (
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_verify_prompt,
)


UNAVAILABLE_ANSWER_TEXT = "No se encontró información en el repositorio."
LOGGER = logging.getLogger(__name__)


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
    content_payload: dict[str, object] = {
        "parts": [{"text": prompt}],
    }
    if vertex:
        # Vertex con modelos Gemini 2.5 requiere role explícito en contents.
        content_payload["role"] = "user"
    return {
        system_key: {
            "parts": [{"text": SYSTEM_PROMPT}],
        },
        "contents": [
            content_payload
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


def _extract_openai_responses_text(data: dict) -> str:
    """Extrae texto de OpenAI Responses API en formatos output_text u output[]."""
    output_text = str(data.get("output_text") or "").strip()
    if output_text:
        return output_text

    output_items = data.get("output") or []
    if not isinstance(output_items, list):
        return ""

    parts: list[str] = []
    for item in output_items:
        if not isinstance(item, dict):
            continue
        content_items = item.get("content") or []
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if not isinstance(content, dict):
                continue
            text = str(content.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    """Detecta errores del provider cuando el modelo no permite temperature=0."""
    message = str(exc).lower()
    return (
        "temperature" in message
        and "unsupported" in message
        and ("does not support 0" in message or "unsupported_value" in message)
    )


def _is_openai_model_selection_error(exc: Exception) -> bool:
    """Detecta errores por modelo inválido/no soportado/sin acceso."""
    message = str(exc).lower()
    model_related = (
        "model" in message
        and (
            "does not exist" in message
            or "not found" in message
            or "not available" in message
            or "access" in message
            or "permission" in message
            or "unsupported" in message
        )
    )
    endpoint_related = (
        ("not supported" in message or "only supported" in message)
        and ("endpoint" in message or "responses" in message or "chat" in message)
    )
    return model_related or endpoint_related


def _is_openai_responses_payload_error(exc: Exception) -> bool:
    """Detecta errores de formato payload en Responses API para probar otra variante."""
    message = str(exc).lower()
    return (
        "input" in message
        and (
            "role" in message
            or "content" in message
            or "invalid type" in message
            or "unsupported" in message
        )
    )


def _is_vertex_model_selection_error(exc: Exception) -> bool:
    """Detecta errores de Vertex por modelo no disponible/no encontrado."""
    message = str(exc).lower()
    if "404" in message and "models/" in message:
        return True
    return (
        "model" in message
        and (
            "not found" in message
            or "not available" in message
            or "does not exist" in message
            or "unsupported" in message
            or "not enabled" in message
        )
    )


def _openai_model_candidates(primary_model: str) -> list[str]:
    """Construye candidatos de modelo con fallback determinista y sin duplicados."""
    candidates: list[str] = []
    for item in [
        primary_model,
        default_llm_model("openai"),
        *llm_models_for_provider("openai"),
    ]:
        value = item.strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _vertex_model_candidates(primary_model: str) -> list[str]:
    """Construye candidatos de modelo Vertex con fallback determinista."""
    candidates: list[str] = []
    resilient_fallbacks = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    for item in [
        primary_model,
        default_llm_model("vertex"),
        *llm_models_for_provider("vertex"),
        *resilient_fallbacks,
    ]:
        value = item.strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


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
            self.provider = "vertex"
        self.provider = normalize_provider_name(self.provider)

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
            self.answer_model = "gemini-2.0-flash"

        if hasattr(settings, "resolve_verifier_model"):
            self.verifier_model = settings.resolve_verifier_model(
                self.provider,
                verifier_model,
            )
        elif verifier_model and verifier_model.strip():
            self.verifier_model = verifier_model.strip()
        else:
            self.verifier_model = "gemini-2.0-flash"

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
        *,
        labels: dict[str, str] | None = None,
        use_case_id: str | None = None,
    ) -> str:
        """Ejecute la llamada API del provider activo y devuelva texto plano."""
        if not self.enabled:
            return UNAVAILABLE_ANSWER_TEXT

        if self.provider == "openai":
            return self._call_openai(model, prompt, timeout_seconds=timeout_seconds)
        if self.provider == "gemini":
            return self._call_gemini(model, prompt, timeout_seconds=timeout_seconds)
        if self.provider == "vertex":
            return self._call_vertex_ai(
                model,
                prompt,
                timeout_seconds=timeout_seconds,
                labels=labels,
                use_case_id=use_case_id,
            )
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
        rest_timeout = float(request_kwargs.get("timeout", 20.0))

        last_error: Exception | None = None
        for candidate_model in _openai_model_candidates(model):
            # Soporta modelos responses-only incluso con SDK antiguo sin client.responses.
            try:
                response = requests.post(
                    "https://api.openai.com/v1/responses",
                    json={
                        "model": candidate_model,
                        "instructions": SYSTEM_PROMPT,
                        "input": prompt,
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=rest_timeout,
                )
                response.raise_for_status()
                text = _extract_openai_responses_text(response.json())
                if text:
                    return text
            except Exception as exc:
                last_error = exc

            if hasattr(self.client, "responses"):
                try:
                    response = self.client.responses.create(
                        model=candidate_model,
                        instructions=SYSTEM_PROMPT,
                        input=prompt,
                        **request_kwargs,
                    )
                    return (response.output_text or "").strip()
                except Exception as exc:
                    last_error = exc
                    if _is_openai_responses_payload_error(exc):
                        try:
                            response = self.client.responses.create(
                                model=candidate_model,
                                input=messages,
                                **request_kwargs,
                            )
                            return (response.output_text or "").strip()
                        except Exception as exc_legacy:
                            last_error = exc_legacy
                    # Si falla por incompatibilidad de endpoint/modelo, intenta chat
                    # para el mismo modelo antes de pasar al siguiente fallback.
                    if not _is_openai_model_selection_error(last_error):
                        raise

            try:
                completion = self.client.chat.completions.create(
                    model=candidate_model,
                    messages=messages,
                    temperature=0,
                    **request_kwargs,
                )
            except Exception as exc:
                last_error = exc
                if _is_unsupported_temperature_error(exc):
                    completion = self.client.chat.completions.create(
                        model=candidate_model,
                        messages=messages,
                        **request_kwargs,
                    )
                    content = completion.choices[0].message.content
                    return (content or "").strip()
                if _is_openai_model_selection_error(exc):
                    continue
                raise
            content = completion.choices[0].message.content
            return (content or "").strip()

        if last_error is not None:
            raise last_error
        return ""

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
        *,
        labels: dict[str, str] | None = None,
        use_case_id: str | None = None,
    ) -> str:
        """Llama a Vertex AI generateContent usando Service Account."""
        settings = get_settings()
        project = (
            settings.resolve_vertex_project_id()
            if hasattr(settings, "resolve_vertex_project_id")
            else getattr(settings, "vertex_ai_project_id", "")
        )
        location = (
            settings.resolve_vertex_location()
            if hasattr(settings, "resolve_vertex_location")
            else getattr(settings, "vertex_ai_location", "us-central1")
        )
        base_url = (
            settings.resolve_vertex_api_base_url()
            if hasattr(settings, "resolve_vertex_api_base_url")
            else f"https://{location}-aiplatform.googleapis.com"
        )
        api_version = str(getattr(settings, "vertex_api_version", "v1"))
        generate_path_template = str(
            getattr(
                settings,
                "vertex_generate_content_path_template",
                (
                    "/projects/{project}/locations/{location}/publishers/google/"
                    "models/{model}:generateContent"
                ),
            )
        )
        token_url = str(getattr(settings, "vertex_auth_token_url", "")).strip()
        if hasattr(settings, "resolve_vertex_credentials_reference"):
            credentials_source = str(
                settings.resolve_vertex_credentials_reference()
            ).strip()
        else:
            credentials_source = str(
                getattr(settings, "vertex_ai_service_account_json_b64", "")
            ).strip()
        if not project:
            return ""
        if hasattr(settings, "is_vertex_ai_configured") and not settings.is_vertex_ai_configured():
            return ""
        if not credentials_source:
            return ""

        if token_url:
            auth_context = resolve_vertex_auth_context(
                credentials_source,
                token_url=token_url,
            )
        else:
            auth_context = resolve_vertex_auth_context(credentials_source)

        timeout_value = _timeout_value(timeout_seconds)
        correlation_id = None
        if bool(getattr(settings, "vertex_ai_correlation_id_enabled", False)):
            correlation_id = str(uuid4())

        headers = {
            "Authorization": f"Bearer {auth_context.access_token}",
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers["x-correlation-id"] = correlation_id

        last_error: Exception | None = None
        for candidate_model in _vertex_model_candidates(model):
            model_name = _vertex_model_name(candidate_model)
            url = build_vertex_api_url(
                base_url=base_url,
                api_version=api_version,
                path_template=generate_path_template,
                project_id=project,
                model_name=model_name,
                location=location,
            )
            if not url:
                return ""
            payload = _build_generate_content_payload(prompt, vertex=True)
            resolved_use_case = (use_case_id or settings.vertex_ai_label_use_case_id).strip()
            request_labels = build_vertex_labels(
                enabled=bool(settings.vertex_ai_labels_enabled),
                service=str(settings.vertex_ai_label_service),
                use_case_id=resolved_use_case,
                model_name=model_name,
                service_account_email=auth_context.service_account_email,
                service_account_label=str(
                    getattr(settings, "vertex_ai_label_service_account", "")
                ),
                overrides=labels,
            )
            if request_labels:
                payload["labels"] = request_labels

            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout_value,
                )
                response.raise_for_status()
                if correlation_id:
                    LOGGER.debug(
                        "Vertex request completed correlation_id=%s model=%s",
                        correlation_id,
                        candidate_model,
                    )
                return _extract_generative_text(response.json())
            except Exception as exc:
                last_error = exc
                if _is_vertex_model_selection_error(exc):
                    LOGGER.warning(
                        "Vertex model unavailable model=%s; trying next fallback candidate.",
                        candidate_model,
                    )
                    continue
                raise

        if last_error is not None:
            raise last_error
        return ""

    def answer(
        self,
        query: str,
        context: str,
        timeout_seconds: float | None = None,
        *,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Genere una respuesta basada en el contexto para una pregunta de un usuario."""
        prompt = build_answer_prompt(query=query, context=context)
        return self._call(
            self.answer_model,
            prompt,
            timeout_seconds=timeout_seconds,
            labels=labels,
            use_case_id="query_answer",
        )

    @property
    def enabled(self) -> bool:
        """Devuelve si la generación del provider activo está habilitada."""
        if self.provider == "openai":
            return self.client is not None
        if self.provider == "gemini":
            return bool(self.api_key)
        if self.provider == "vertex":
            settings = get_settings()
            if hasattr(settings, "is_vertex_ai_configured"):
                return bool(settings.is_vertex_ai_configured())
            credentials_source = (
                settings.resolve_vertex_credentials_reference()
                if hasattr(settings, "resolve_vertex_credentials_reference")
                else getattr(settings, "vertex_ai_service_account_json_b64", "")
            )
            project_id = (
                settings.resolve_vertex_project_id()
                if hasattr(settings, "resolve_vertex_project_id")
                else getattr(settings, "vertex_ai_project_id", "")
            )
            return bool(str(credentials_source).strip() and str(project_id).strip())
        return False

    def verify(
        self,
        answer: str,
        context: str,
        timeout_seconds: float | None = None,
        *,
        labels: dict[str, str] | None = None,
    ) -> bool:
        """Valida si la respuesta está sustentada en el contexto proporcionado."""
        if not self.enabled:
            return True

        prompt = build_verify_prompt(answer=answer, context=context)
        result = self._call(
            self.verifier_model,
            prompt,
            timeout_seconds=timeout_seconds,
            labels=labels,
            use_case_id="query_verify",
        )
        return _is_verifier_result_valid(result)
