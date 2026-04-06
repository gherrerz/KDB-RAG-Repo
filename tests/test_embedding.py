"""Pruebas para consistencia de dimensiones en generación de embeddings."""

from types import SimpleNamespace

import pytest

from src.coderag.ingestion.embedding import EmbeddingClient


class _FakeEmbeddingsAPI:
    """Simula respuestas mixtas de OpenAI para lotes consecutivos."""

    def __init__(self) -> None:
        """Inicializa contador interno de llamadas por lote."""
        self.calls = 0

    def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        """Devuelve éxito en la primera llamada y falla en la segunda."""
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("timeout")
        vector = [0.1] * 1536
        data = [SimpleNamespace(embedding=vector) for _ in input]
        return SimpleNamespace(data=data)


class _FakeOpenAIClient:
    """Cliente OpenAI mínimo para pruebas unitarias de embedding."""

    def __init__(self) -> None:
        """Expone el subcliente de embeddings simulado."""
        self.embeddings = _FakeEmbeddingsAPI()


def test_embed_texts_uses_model_dimension_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usa dimensión de modelo como fallback cuando OpenAI no está disponible."""

    class _Settings:
        openai_embedding_model = "text-embedding-3-small"
        openai_api_key = ""

    import src.coderag.ingestion.embedding as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    client = EmbeddingClient()
    vectors = client.embed_texts(["uno", "dos"])

    assert len(vectors) == 2
    assert all(len(vector) == 1536 for vector in vectors)


def test_embed_texts_keeps_dimension_on_mixed_api_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mantiene dimensión uniforme si una tanda cae en fallback por error transitorio."""

    class _Settings:
        openai_embedding_model = "text-embedding-3-small"
        openai_api_key = "test-key"

    import src.coderag.ingestion.embedding as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        module,
        "OpenAI",
        lambda *args, **kwargs: _FakeOpenAIClient(),
    )

    client = EmbeddingClient()
    client.batch_size = 1
    vectors = client.embed_texts(["a", "b"])

    assert len(vectors) == 2
    assert all(len(vector) == 1536 for vector in vectors)


def test_embed_texts_reports_progress_per_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emite progreso acumulado por lote para visibilidad en ingesta."""

    class _Settings:
        openai_embedding_model = "text-embedding-3-small"
        openai_api_key = "test-key"
        openai_timeout_seconds = 10.0

    import src.coderag.ingestion.embedding as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        module,
        "OpenAI",
        lambda *args, **kwargs: _FakeOpenAIClient(),
    )

    client = EmbeddingClient()
    client.batch_size = 1
    progress_events: list[tuple[int, int]] = []

    vectors = client.embed_texts(
        ["a", "b"],
        progress_callback=lambda processed, total: progress_events.append(
            (processed, total)
        ),
    )

    assert len(vectors) == 2
    assert progress_events == [(1, 2), (2, 2)]
