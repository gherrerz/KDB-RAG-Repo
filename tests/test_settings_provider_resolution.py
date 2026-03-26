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


def test_semantic_graph_java_flag_defaults_to_false() -> None:
    """Mantiene deshabilitada la extracción semántica Java por defecto."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_java_enabled is False


def test_semantic_graph_typescript_flag_defaults_to_false() -> None:
    """Mantiene deshabilitada la extracción semántica TypeScript por defecto."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_typescript_enabled is False


def test_semantic_graph_query_flags_defaults() -> None:
    """Configura por defecto la expansión semántica de query desactivada."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_query_enabled is False
    assert settings.semantic_graph_query_max_edges == 400
    assert settings.semantic_graph_query_max_nodes == 200
    assert settings.semantic_graph_query_max_ms == 120.0
    assert settings.semantic_graph_query_fallback_to_structural is True


def test_resolve_semantic_relation_types_filters_invalid_and_duplicates() -> None:
    """Normaliza tipos válidos y elimina entradas inválidas/duplicadas."""
    settings = Settings(
        SEMANTIC_RELATION_TYPES="calls,IMPORTS,foo,implements,calls",
        _env_file=None,
    )

    assert settings.resolve_semantic_relation_types() == [
        "CALLS",
        "IMPORTS",
        "IMPLEMENTS",
    ]


def test_resolve_semantic_relation_weights_parses_and_falls_back() -> None:
    """Acepta pesos válidos y conserva defaults ante entradas inválidas."""
    settings = Settings(
        SEMANTIC_RELATION_WEIGHTS="CALLS:1.4,IMPORTS:abc,EXTENDS:1.2,foo:3,IMPLEMENTS:-1",
        _env_file=None,
    )

    weights = settings.resolve_semantic_relation_weights()

    assert weights["CALLS"] == 1.4
    assert weights["EXTENDS"] == 1.2
    assert weights["IMPORTS"] == 0.7
    assert weights["IMPLEMENTS"] == 1.0
