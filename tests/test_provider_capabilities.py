"""Pruebas de capacidades por provider para embeddings y LLM."""

from coderag.core.settings import Settings


def test_embedding_capabilities_vertex_requires_project() -> None:
    """Vertex embeddings queda no configurado sin service account Base64 ni fallback legacy."""
    settings = Settings(
        VERTEX_SERVICE_ACCOUNT_JSON_B64="",
        VERTEX_API_BASE_URL="https://us-central1-aiplatform.googleapis.com",
        VERTEX_AI_PROJECT_ID="",
        _env_file=None,
    )
    info = settings.embedding_provider_capabilities("vertex_ai")
    assert info["supported"] is True
    assert info["configured"] is False
    assert info["reason"] == "missing_vertex_ai_api_key_or_project"


def test_llm_capabilities_openai_missing_key() -> None:
    """OpenAI reporta no configurado cuando falta API key."""
    settings = Settings(OPENAI_API_KEY="")
    info = settings.llm_provider_capabilities("openai")
    assert info["supported"] is True
    assert info["configured"] is False
    assert info["reason"] == "missing_openai_api_key"


def test_embedding_capabilities_anthropic_not_supported() -> None:
    """Anthropic en embeddings se marca explícitamente como no soportado."""
    settings = Settings(ANTHROPIC_API_KEY="a")
    info = settings.embedding_provider_capabilities("anthropic")
    assert info["supported"] is False
    assert info["configured"] is False
    assert info["reason"] == "provider_without_embedding_backend"
