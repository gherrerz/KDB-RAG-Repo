"""Pruebas de integración unitaria para flujos REST de Vertex AI."""

from types import SimpleNamespace

import pytest

from coderag.ingestion.embedding import EmbeddingClient
from coderag.llm.openai_client import AnswerClient


def test_embedding_vertex_uses_predict_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vertex embeddings llama endpoint predict y parsea vectors."""

    class _Settings:
        openai_embedding_model = "text-embedding-3-small"
        openai_api_key = ""
        openai_timeout_seconds = 5.0
        vertex_ai_api_key = "oauth-token"
        vertex_ai_project_id = "demo-proj"
        vertex_ai_location = "us-central1"

        def resolve_embedding_provider(self, override: str | None = None) -> str:
            return override or "openai"

        def resolve_embedding_model(self, provider: str, override: str | None = None) -> str:
            return override or "text-embedding-005"

        def resolve_api_key(self, provider: str) -> str:
            if provider == "vertex_ai":
                return self.vertex_ai_api_key
            return self.openai_api_key

    captured = {"url": ""}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "predictions": [
                    {"embeddings": {"values": [0.1, 0.2, 0.3]}}
                ]
            },
        )

    import coderag.ingestion.embedding as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(module.requests, "post", fake_post)

    client = EmbeddingClient(provider="vertex_ai", model="text-embedding-005")
    vectors = client.embed_texts(["hola"])  # noqa: S101 - test

    assert "aiplatform.googleapis.com" in captured["url"]
    assert vectors == [[0.1, 0.2, 0.3]]


def test_answer_client_vertex_enabled_requires_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vertex LLM queda deshabilitado si falta project id."""

    class _Settings:
        openai_api_key = ""
        vertex_ai_api_key = "token"
        vertex_ai_project_id = ""
        vertex_ai_location = "us-central1"

        def resolve_llm_provider(self, override: str | None = None) -> str:
            return override or "openai"

        def resolve_api_key(self, provider: str) -> str:
            if provider == "vertex_ai":
                return self.vertex_ai_api_key
            return self.openai_api_key

        def resolve_answer_model(self, provider: str, override: str | None = None) -> str:
            return override or "gemini-2.0-flash"

        def resolve_verifier_model(self, provider: str, override: str | None = None) -> str:
            return override or "gemini-2.0-flash"

        def is_vertex_ai_configured(self) -> bool:
            return bool(self.vertex_ai_api_key and self.vertex_ai_project_id)

    import coderag.llm.openai_client as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    client = AnswerClient(provider="vertex_ai")
    assert client.enabled is False
