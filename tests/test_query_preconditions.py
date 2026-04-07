"""Pruebas unitarias para validación local de precondiciones de consulta."""

from coderag.ui.query_preconditions import evaluate_local_query_preconditions


def test_query_preconditions_blocks_provider_not_ready() -> None:
    """Bloquea cuando providers no están listos y no hay force fallback."""
    result = evaluate_local_query_preconditions(
        repo_id="repo-a",
        question="hola",
        has_repo_in_catalog=True,
        job_poll_enabled=False,
        embedding_ready=False,
        embedding_reason="missing_gemini_api_key",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert result.allowed is False
    assert "forzar fallback" in result.message.lower()
    assert "embeddings=missing_gemini_api_key" in result.message


def test_query_preconditions_blocks_missing_repo() -> None:
    """Bloquea cuando no hay repo seleccionado."""
    result = evaluate_local_query_preconditions(
        repo_id="",
        question="hola",
        has_repo_in_catalog=False,
        job_poll_enabled=False,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert result.allowed is False
    assert "id de repositorio" in result.message.lower()


def test_query_preconditions_blocks_ingest_in_progress() -> None:
    """Bloquea cuando hay ingesta activa."""
    result = evaluate_local_query_preconditions(
        repo_id="repo-a",
        question="hola",
        has_repo_in_catalog=True,
        job_poll_enabled=True,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert result.allowed is False
    assert "ingesta" in result.message.lower()


def test_query_preconditions_blocks_repo_not_in_catalog() -> None:
    """Bloquea cuando repo no existe en catálogo local."""
    result = evaluate_local_query_preconditions(
        repo_id="repo-x",
        question="hola",
        has_repo_in_catalog=False,
        job_poll_enabled=False,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert result.allowed is False
    assert "no existe" in result.message.lower()


def test_query_preconditions_allow_when_all_ready() -> None:
    """Permite consulta cuando todas las condiciones locales se cumplen."""
    result = evaluate_local_query_preconditions(
        repo_id="repo-a",
        question="hola",
        has_repo_in_catalog=True,
        job_poll_enabled=False,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=True,
        llm_reason="ok",
        force_fallback=False,
    )

    assert result.allowed is True
    assert result.message == ""


def test_query_preconditions_retrieval_only_allows_missing_llm() -> None:
    """En modo retrieval-only no bloquea cuando llm no está listo."""
    result = evaluate_local_query_preconditions(
        repo_id="repo-a",
        question="hola",
        has_repo_in_catalog=True,
        job_poll_enabled=False,
        embedding_ready=True,
        embedding_reason="ok",
        llm_ready=False,
        llm_reason="missing_anthropic_api_key",
        force_fallback=False,
        retrieval_only_mode=True,
    )

    assert result.allowed is True
    assert result.message == ""
