"""Embedding generation helpers using OpenAI Responses stack."""

import hashlib

from openai import OpenAI

from coderag.core.settings import get_settings


def _fallback_embedding(text: str, dimension: int = 256) -> list[float]:
    """Generate deterministic fallback vectors for offline operation."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest) * (dimension // len(digest) + 1)
    return [float(item) / 255.0 for item in values[:dimension]]


class EmbeddingClient:
    """Client abstraction to produce vectors for indexing and search."""

    def __init__(self) -> None:
        """Initialize client from environment settings."""
        settings = get_settings()
        self.model = settings.openai_embedding_model
        self.api_key = settings.openai_api_key
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.max_chars_per_text = 12000
        self.batch_size = 64

    def _sanitize_text(self, text: str) -> str:
        """Trim long input strings to keep embedding requests within limits."""
        if len(text) <= self.max_chars_per_text:
            return text
        return text[: self.max_chars_per_text]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed list of strings using OpenAI API or deterministic fallback."""
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
