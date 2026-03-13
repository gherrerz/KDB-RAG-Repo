"""Utilidades de generación de embeddings con OpenAI Responses."""

import hashlib
import logging
from threading import Lock

from openai import OpenAI

from coderag.core.settings import get_settings

LOGGER = logging.getLogger(__name__)

MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


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

    def __init__(self) -> None:
        """Inicialice el cliente desde la configuración del entorno."""
        settings = get_settings()
        self.model = settings.openai_embedding_model
        self.api_key = settings.openai_api_key
        self.client = self._resolve_client(api_key=self.api_key)
        self.max_chars_per_text = 12000
        self.batch_size = 64

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

    def _default_dimension(self) -> int:
        """Devuelva una dimensión de respaldo estable para el modelo activo."""
        return MODEL_DIMENSIONS.get(self.model, 1536)

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

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Incruste una lista de cadenas utilizando la API OpenAI o un respaldo determinista."""
        if not texts:
            return []

        normalized = [self._sanitize_text(text) for text in texts]
        target_dimension: int | None = None

        if self.client is None:
            target_dimension = self._default_dimension()
            LOGGER.warning(
                "OpenAI no configurado; usando fallback determinista "
                "para embeddings (dim=%s).",
                target_dimension,
            )
            return [
                _fallback_embedding(text, dimension=target_dimension)
                for text in normalized
            ]

        vectors: list[list[float]] = []
        for index in range(0, len(normalized), self.batch_size):
            batch = normalized[index : index + self.batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
                batch_vectors = [item.embedding for item in response.data]
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
                    "Fallo al solicitar embeddings en OpenAI; se usa fallback "
                    "determinista para el lote (dim=%s, model=%s, error=%s).",
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
        return vectors
