"""Utilidades de generación de embeddings con OpenAI Responses."""

import hashlib
import logging
from threading import Lock
from collections.abc import Callable
from uuid import uuid4

from openai import OpenAI
import requests

from coderag.core.settings import ProviderName, get_settings
from coderag.core.vertex_ai import build_vertex_labels, resolve_vertex_auth_context

LOGGER = logging.getLogger(__name__)

MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "text-embedding-004": 768,
    "text-embedding-005": 768,
}


def _timeout_value(timeout_seconds: float | None) -> float:
    """Normaliza timeout con mínimo seguro para requests REST."""
    return max(1.0, float(timeout_seconds or 20.0))


def _model_path(model: str) -> str:
    """Normaliza identificador de modelo con prefijo models/."""
    if model.startswith("models/"):
        return model
    return f"models/{model}"


def _vertex_model_name(model: str) -> str:
    """Convierte model path al formato requerido por Vertex publisher models."""
    if model.startswith("models/"):
        return model.split("/", 1)[1]
    return model


def _extract_gemini_embeddings(data: dict) -> list[list[float]]:
    """Extrae embeddings desde payload REST de Gemini batchEmbedContents."""
    embeddings = data.get("embeddings") or []
    vectors: list[list[float]] = []
    for item in embeddings:
        values = item.get("values") if isinstance(item, dict) else []
        vectors.append([float(value) for value in (values or [])])
    return vectors


def _extract_vertex_embeddings(data: dict) -> list[list[float]]:
    """Extrae embeddings desde payload REST de Vertex predict."""
    predictions = data.get("predictions") or []
    vectors: list[list[float]] = []
    for item in predictions:
        embedding_obj = item.get("embeddings") if isinstance(item, dict) else None
        if isinstance(embedding_obj, dict):
            values = embedding_obj.get("values") or []
            vectors.append([float(value) for value in values])
            continue
        values = item.get("values") if isinstance(item, dict) else []
        vectors.append([float(value) for value in (values or [])])
    return vectors


def _fallback_embedding(text: str, dimension: int = 256) -> list[float]:
    """Genere vectores de respaldo deterministas para operaciones fuera de línea."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest) * (dimension // len(digest) + 1)
    return [float(item) / 255.0 for item in values[:dimension]]


class EmbeddingClient:
    """Abstracción del cliente para producir vectores para indexación y búsqueda."""

    _shared_client: OpenAI | None = None
    _shared_api_key: str | None = None
    _client_lock: Lock = Lock()

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """Inicialice el cliente con soporte multi-provider por operación."""
        settings = get_settings()
        if hasattr(settings, "resolve_embedding_provider"):
            self.provider = settings.resolve_embedding_provider(provider)
        else:
            self.provider = "vertex_ai"

        if hasattr(settings, "resolve_embedding_model"):
            self.model = settings.resolve_embedding_model(self.provider, model)
        elif model and model.strip():
            self.model = model.strip()
        else:
            self.model = "text-embedding-005"

        if hasattr(settings, "resolve_api_key"):
            self.api_key = settings.resolve_api_key(self.provider)
        else:
            self.api_key = getattr(settings, "openai_api_key", "")

        self.client = self._resolve_client(
            provider=self.provider,
            api_key=self.api_key,
        )
        self.max_chars_per_text = 12000
        self.batch_size = 64

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
                settings = get_settings()
                request_timeout = _timeout_value(
                    getattr(settings, "openai_timeout_seconds", 20.0)
                )
                max_retries = max(
                    0,
                    int(getattr(settings, "openai_max_retries", 2)),
                )
                cls._shared_client = OpenAI(
                    api_key=api_key,
                    timeout=request_timeout,
                    max_retries=max_retries,
                )
                cls._shared_api_key = api_key
            return cls._shared_client

    def _default_dimension(self) -> int:
        """Devuelva una dimensión de respaldo estable para el modelo activo."""
        return MODEL_DIMENSIONS.get(self.model, 768)

    def _fallback_reason(self) -> str:
        """Devuelve motivo compacto para trazabilidad de fallback."""
        if self.provider == "vertex_ai":
            settings = get_settings()
            if hasattr(settings, "is_vertex_ai_configured") and not settings.is_vertex_ai_configured():
                return "missing_vertex_ai_api_key_or_project"
            return "provider_runtime_error"
        if not self.api_key:
            return "missing_api_key"
        return "provider_runtime_error"

    @staticmethod
    def _validate_dimensions(vectors: list[list[float]], dimension: int) -> None:
        """Verifique que todos los vectores compartan la misma dimensión."""
        for vector in vectors:
            if len(vector) != dimension:
                raise RuntimeError(
                    "Se detectaron embeddings con dimensiones inconsistentes "
                    f"(esperada={dimension}, recibida={len(vector)})."
                )

    def _sanitize_text(self, text: str) -> str:
        """Recorta cadenas de entrada largas para mantener las solicitudes de embeddings dentro de los límites."""
        if len(text) <= self.max_chars_per_text:
            return text
        return text[: self.max_chars_per_text]

    def embed_texts(
        self,
        texts: list[str],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        labels: dict[str, str] | None = None,
        use_case_id: str | None = None,
    ) -> list[list[float]]:
        """Incruste textos con callback opcional de progreso por lotes."""
        if not texts:
            return []

        normalized = [self._sanitize_text(text) for text in texts]
        target_dimension: int | None = None

        has_provider_runtime = True
        if self.provider == "openai":
            has_provider_runtime = self.client is not None
        elif self.provider == "gemini":
            has_provider_runtime = bool(self.api_key)
        elif self.provider == "vertex_ai":
            settings = get_settings()
            if hasattr(settings, "is_vertex_ai_configured"):
                has_provider_runtime = bool(settings.is_vertex_ai_configured())
            else:
                has_provider_runtime = bool(getattr(settings, "vertex_ai_project_id", ""))
        else:
            has_provider_runtime = False

        if not has_provider_runtime:
            target_dimension = self._default_dimension()
            LOGGER.warning(
                "Embeddings provider=%s no disponible (%s); usando fallback "
                "determinista (dim=%s).",
                self.provider,
                self._fallback_reason(),
                target_dimension,
            )
            return [
                _fallback_embedding(text, dimension=target_dimension)
                for text in normalized
            ]

        vectors: list[list[float]] = []
        processed = 0
        for index in range(0, len(normalized), self.batch_size):
            batch = normalized[index : index + self.batch_size]
            try:
                if self.provider == "openai" and self.client is not None:
                    response = self.client.embeddings.create(
                        model=self.model,
                        input=batch,
                    )
                    batch_vectors = [item.embedding for item in response.data]
                elif self.provider == "gemini":
                    batch_vectors = self._embed_with_gemini_rest(batch)
                elif self.provider == "vertex_ai":
                    batch_vectors = self._embed_with_vertex_ai_rest(
                        batch,
                        labels=labels,
                        use_case_id=use_case_id,
                    )
                else:
                    batch_vectors = []

                if not batch_vectors:
                    continue

                if target_dimension is None:
                    target_dimension = len(batch_vectors[0])
                self._validate_dimensions(batch_vectors, target_dimension)
                vectors.extend(batch_vectors)
            except Exception as exc:
                if target_dimension is None:
                    target_dimension = self._default_dimension()
                LOGGER.warning(
                    "Fallo embeddings provider=%s; fallback determinista para "
                    "el lote (dim=%s, model=%s, error=%s).",
                    self.provider,
                    target_dimension,
                    self.model,
                    exc,
                )
                vectors.extend(
                    [
                        _fallback_embedding(text, dimension=target_dimension)
                        for text in batch
                    ]
                )
            finally:
                processed += len(batch)
                if progress_callback is not None:
                    progress_callback(processed, len(normalized))
        return vectors

    def _embed_with_gemini_rest(self, texts: list[str]) -> list[list[float]]:
        """Solicita embeddings a Gemini vía REST sin depender de SDK externo."""
        if not self.api_key:
            return []
        model_path = _model_path(self.model)

        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"{model_path}:batchEmbedContents"
        )
        payload = {
            "requests": [
                {
                    "model": model_path,
                    "content": {
                        "parts": [{"text": text}],
                    },
                }
                for text in texts
            ]
        }
        response = requests.post(
            url,
            params={"key": self.api_key},
            json=payload,
            timeout=_timeout_value(getattr(get_settings(), "openai_timeout_seconds", 20.0)),
        )
        response.raise_for_status()
        return _extract_gemini_embeddings(response.json())

    def _embed_with_vertex_ai_rest(
        self,
        texts: list[str],
        *,
        labels: dict[str, str] | None = None,
        use_case_id: str | None = None,
    ) -> list[list[float]]:
        """Solicita embeddings a Vertex AI usando Service Account."""
        settings = get_settings()
        project = getattr(settings, "vertex_ai_project_id", "")
        location = getattr(settings, "vertex_ai_location", "us-central1")
        if hasattr(settings, "resolve_vertex_credentials_reference"):
            credentials_source = str(
                settings.resolve_vertex_credentials_reference()
            ).strip()
        else:
            credentials_source = str(
                getattr(settings, "vertex_ai_service_account_json_b64", "")
            ).strip()
        if not project:
            return []
        if hasattr(settings, "is_vertex_ai_configured") and not settings.is_vertex_ai_configured():
            return []
        if not credentials_source:
            return []

        auth_context = resolve_vertex_auth_context(credentials_source)

        model_name = _vertex_model_name(self.model)

        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/"
            f"locations/{location}/publishers/google/models/{model_name}:predict"
        )
        resolved_use_case = (
            use_case_id or getattr(settings, "vertex_ai_label_use_case_id", "rag_embedding")
        ).strip()
        request_labels = build_vertex_labels(
            enabled=bool(getattr(settings, "vertex_ai_labels_enabled", True)),
            service=str(getattr(settings, "vertex_ai_label_service", "kdb-rag")),
            use_case_id=resolved_use_case,
            model_name=model_name,
            service_account_email=auth_context.service_account_email,
            service_account_label=str(
                getattr(settings, "vertex_ai_label_service_account", "")
            ),
            overrides=labels,
        )

        payload = {
            "instances": [{"content": text} for text in texts],
        }
        if request_labels:
            payload["labels"] = request_labels

        correlation_id = None
        if bool(getattr(settings, "vertex_ai_correlation_id_enabled", False)):
            correlation_id = str(uuid4())

        headers = {
            "Authorization": f"Bearer {auth_context.access_token}",
            "Content-Type": "application/json",
        }
        if correlation_id:
            headers["x-correlation-id"] = correlation_id

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_timeout_value(getattr(get_settings(), "openai_timeout_seconds", 20.0)),
        )
        response.raise_for_status()
        return _extract_vertex_embeddings(response.json())
