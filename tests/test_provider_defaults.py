"""Pruebas unitarias para catálogo compartido de modelos por defecto."""

from coderag.ui.provider_defaults import (
    default_embedding_model,
    default_llm_model,
    embedding_models_for_provider,
    llm_models_for_provider,
)


def test_default_embedding_models_catalog() -> None:
    """Resuelve modelos por defecto esperados para embeddings."""
    assert default_embedding_model("openai") == "text-embedding-3-small"
    assert default_embedding_model("gemini") == "text-embedding-004"
    assert default_embedding_model("vertex_ai") == "text-embedding-005"
    assert default_embedding_model("vertex") == "text-embedding-005"


def test_default_llm_models_catalog() -> None:
    """Resuelve modelos por defecto esperados para LLM."""
    assert default_llm_model("openai") == "gpt-4.1-mini"
    assert default_llm_model("gemini") == "gemini-2.0-flash"
    assert default_llm_model("vertex_ai") == "gemini-2.0-flash"
    assert default_llm_model("vertex") == "gemini-2.0-flash"


def test_unknown_provider_returns_empty_string() -> None:
    """Devuelve string vacío cuando no existe default para el provider."""
    assert default_embedding_model("unknown") == ""
    assert default_llm_model("unknown") == ""


def test_embedding_models_for_provider_catalog() -> None:
    """Expone listas predefinidas de embeddings por provider."""
    openai_models = embedding_models_for_provider("openai")
    gemini_models = embedding_models_for_provider("gemini")

    assert "text-embedding-3-small" in openai_models
    assert "text-embedding-3-large" in openai_models
    assert gemini_models == ["text-embedding-004"]


def test_llm_models_for_provider_catalog() -> None:
    """Expone listas predefinidas de modelos LLM por provider."""
    gemini_models = llm_models_for_provider("gemini")
    vertex_models = llm_models_for_provider("vertex_ai")

    assert "gemini-2.0-flash" in gemini_models
    assert "gemini-2.0-flash-lite" in gemini_models
    assert "gemini-2.0-flash" in vertex_models
