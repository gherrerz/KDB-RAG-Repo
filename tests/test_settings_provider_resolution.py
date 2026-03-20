"""Pruebas de prioridad de resolución provider/modelo en Settings."""

import pytest

from coderag.core.settings import Settings


def test_embedding_resolution_priority_override_over_env_and_legacy() -> None:
    """Aplica prioridad override > env nuevo > legado OPENAI_* para embeddings."""
    settings = Settings(
        EMBEDDING_PROVIDER="gemini",
        EMBEDDING_MODEL="env-embed-model",
        OPENAI_EMBEDDING_MODEL="legacy-openai-embed",
    )

    provider = settings.resolve_embedding_provider("vertex_ai")
    model = settings.resolve_embedding_model(provider, "override-embed-model")

    assert provider == "vertex_ai"
    assert model == "override-embed-model"


def test_embedding_resolution_uses_new_env_before_legacy() -> None:
    """Sin override, usa env nuevo antes del fallback OPENAI_* legado."""
    settings = Settings(
        EMBEDDING_PROVIDER="gemini",
        EMBEDDING_MODEL="env-embed-model",
        OPENAI_EMBEDDING_MODEL="legacy-openai-embed",
    )

    provider = settings.resolve_embedding_provider(None)
    model = settings.resolve_embedding_model(provider, None)

    assert provider == "gemini"
    assert model == "env-embed-model"


def test_llm_resolution_priority_override_over_env_and_legacy() -> None:
    """Aplica prioridad override > env nuevo > legado OPENAI_* para LLM."""
    settings = Settings(
        LLM_PROVIDER="anthropic",
        LLM_ANSWER_MODEL="env-answer-model",
        LLM_VERIFIER_MODEL="env-verifier-model",
        OPENAI_ANSWER_MODEL="legacy-answer",
        OPENAI_VERIFIER_MODEL="legacy-verifier",
    )

    provider = settings.resolve_llm_provider("gemini")
    answer_model = settings.resolve_answer_model(provider, "override-answer")
    verifier_model = settings.resolve_verifier_model(provider, "override-verifier")

    assert provider == "gemini"
    assert answer_model == "override-answer"
    assert verifier_model == "override-verifier"


def test_llm_resolution_uses_new_env_before_legacy() -> None:
    """Sin override, usa modelos LLM de env nuevo antes de OPENAI_* legado."""
    settings = Settings(
        LLM_PROVIDER="anthropic",
        LLM_ANSWER_MODEL="env-answer-model",
        LLM_VERIFIER_MODEL="env-verifier-model",
        OPENAI_ANSWER_MODEL="legacy-answer",
        OPENAI_VERIFIER_MODEL="legacy-verifier",
    )

    provider = settings.resolve_llm_provider(None)
    answer_model = settings.resolve_answer_model(provider, None)
    verifier_model = settings.resolve_verifier_model(provider, None)

    assert provider == "anthropic"
    assert answer_model == "env-answer-model"
    assert verifier_model == "env-verifier-model"


def test_chroma_hnsw_space_defaults_to_cosine() -> None:
    """Usa cosine como espacio HNSW por defecto cuando no hay override."""
    settings = Settings(_env_file=None)

    assert settings.chroma_hnsw_space == "cosine"
    assert settings.resolve_chroma_hnsw_space() == "cosine"


def test_chroma_hnsw_space_accepts_l2_and_cosine() -> None:
    """Acepta solo valores soportados para CHROMA_HNSW_SPACE."""
    settings_l2 = Settings(CHROMA_HNSW_SPACE="l2", _env_file=None)
    settings_cos = Settings(CHROMA_HNSW_SPACE="cosine", _env_file=None)

    assert settings_l2.resolve_chroma_hnsw_space() == "l2"
    assert settings_cos.resolve_chroma_hnsw_space() == "cosine"


def test_chroma_hnsw_space_rejects_invalid_values() -> None:
    """Rechaza valores no soportados para CHROMA_HNSW_SPACE."""
    with pytest.raises(ValueError):
        Settings(CHROMA_HNSW_SPACE="ip", _env_file=None)
