"""Utilidades de generación de embeddings con OpenAI Responses."""

import hashlib

from openai import OpenAI

from coderag.core.settings import get_settings


def _fallback_embedding(text: str, dimension: int = 256) -> list[float]:
    """Genere vectores de respaldo deterministas para operaciones fuera de línea."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest) * (dimension // len(digest) + 1)
    return [float(item) / 255.0 for item in values[:dimension]]


class EmbeddingClient:
    """Abstracción del cliente para producir vectores para indexación y búsqueda."""

    def __init__(self) -> None:
        """Inicialice el cliente desde la configuración del entorno."""
        settings = get_settings()
        self.model = settings.openai_embedding_model
        self.api_key = settings.openai_api_key
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.max_chars_per_text = 12000
        self.batch_size = 64

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

        if self.client is None:
            return [_fallback_embedding(text) for text in normalized]

        vectors: list[list[float]] = []
        for index in range(0, len(normalized), self.batch_size):
            batch = normalized[index : index + self.batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
                vectors.extend([item.embedding for item in response.data])
            except Exception:
                vectors.extend([_fallback_embedding(text) for text in batch])
        return vectors
