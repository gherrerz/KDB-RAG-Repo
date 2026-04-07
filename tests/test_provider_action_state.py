"""Pruebas del evaluador puro de habilitación de acciones UI."""

from coderag.ui.provider_action_state import (
    evaluate_ingest_action,
    evaluate_query_action,
)


def test_evaluate_ingest_action_ready() -> None:
    """Ingestar queda habilitado cuando provider esta listo."""
    state = evaluate_ingest_action(
        embedding_ready=True,
        embedding_reason="ok",
        force_fallback=False,
    )

    assert state.enabled is True
    assert "listo para ingestar" in state.message.lower()


def test_evaluate_ingest_action_not_ready_without_force() -> None:
    """Ingestar queda bloqueado cuando provider no esta listo sin fallback."""
    state = evaluate_ingest_action(
        embedding_ready=False,
        embedding_reason="provider_without_embedding_backend",
        force_fallback=False,
    )

    assert state.enabled is False
    assert "forzar fallback" in state.message.lower()


def test_evaluate_query_action_requires_repo() -> None:
    """Consultar exige repositorio seleccionado antes de habilitarse."""
    state = evaluate_query_action(
        controls_enabled=True,
        has_repo=False,
        has_question=True,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert state.enabled is False
    assert "repositorio" in state.message.lower()


def test_evaluate_query_action_not_ready_without_force() -> None:
    """Consultar queda bloqueado si embeddings/llm no estan listos sin fallback."""
    state = evaluate_query_action(
        controls_enabled=True,
        has_repo=True,
        has_question=True,
        embedding_ready=False,
        embedding_reason="missing_gemini_api_key",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert state.enabled is False
    assert "embeddings=missing_gemini_api_key" in state.message


def test_evaluate_query_action_ready_with_force() -> None:
    """Consultar se habilita con fallback forzado aunque falte readiness."""
    state = evaluate_query_action(
        controls_enabled=True,
        has_repo=True,
        has_question=True,
        embedding_ready=False,
        embedding_reason="missing_gemini_api_key",
        llm_ready=False,
        llm_reason="missing_anthropic_api_key",
        force_fallback=True,
    )

    assert state.enabled is True
    assert "listo para consultar" in state.message.lower()


def test_evaluate_query_action_blocked_by_ingestion() -> None:
    """Consultar queda bloqueado cuando controles no estan habilitados."""
    state = evaluate_query_action(
        controls_enabled=False,
        has_repo=True,
        has_question=True,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert state.enabled is False
    assert "ingesta" in state.message.lower()


def test_evaluate_query_action_retrieval_only_skips_llm_readiness() -> None:
    """En modo retrieval-only solo exige embeddings y permite llm no listo."""
    state = evaluate_query_action(
        controls_enabled=True,
        has_repo=True,
        has_question=True,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=False,
        llm_reason="missing_anthropic_api_key",
        force_fallback=False,
        retrieval_only_mode=True,
    )

    assert state.enabled is True
    assert "listo para consultar" in state.message.lower()
