"""Unit tests for query diagnostics helper payload construction."""

from coderag.api.query_diagnostics import build_query_diagnostics


class _SettingsWithCapabilities:
    """Test double exposing provider capability resolvers."""

    @staticmethod
    def llm_provider_capabilities(provider: str) -> dict[str, object]:
        return {"provider": provider, "configured": True}

    @staticmethod
    def embedding_provider_capabilities(provider: str) -> dict[str, object]:
        return {"provider": provider, "dimension": 1536}


class _SettingsWithoutCapabilities:
    """Test double without capability resolver methods."""


def test_build_query_diagnostics_includes_optional_llm_error() -> None:
    """Build full diagnostics payload and keep optional llm_error when present."""
    diagnostics = build_query_diagnostics(
        settings=_SettingsWithCapabilities(),
        retrieved_count=2,
        reranked_count=1,
        graph_nodes_count=3,
        context_chars=120,
        raw_citations_count=2,
        filtered_citations_count=1,
        returned_citations_count=1,
        context_sufficient=True,
        llm_enabled=True,
        llm_provider="openai",
        llm_answer_model="gpt-5-mini",
        llm_verifier_model="gpt-5-nano",
        verify_enabled=True,
        embedding_provider="vertex",
        embedding_model="textembedding-gecko",
        discovered_modules=["api", "core"],
        fallback_reason="generation_error",
        verify_valid=None,
        verify_skipped=False,
        budget_seconds=8.0,
        budget_exhausted=False,
        stage_timings={"total_ms": 123.4},
        inventory_intent=True,
        inventory_target=None,
        llm_error="timeout",
        semantic_diagnostics={
            "semantic_query_enabled": True,
            "semantic_edges_used": 4,
        },
    )

    assert diagnostics["retrieved"] == 2
    assert diagnostics["low_signal_retrieval"] is True
    assert diagnostics["inventory_route"] == "fallback_to_general"
    assert diagnostics["llm_capabilities"] == {
        "provider": "openai",
        "configured": True,
    }
    assert diagnostics["embedding_capabilities"] == {
        "provider": "vertex",
        "dimension": 1536,
    }
    assert diagnostics["llm_error"] == "timeout"
    assert diagnostics["semantic_query_enabled"] is True
    assert diagnostics["semantic_edges_used"] == 4


def test_build_query_diagnostics_defaults_capabilities_to_empty() -> None:
    """Default capabilities to empty maps when settings do not expose resolvers."""
    diagnostics = build_query_diagnostics(
        settings=_SettingsWithoutCapabilities(),
        retrieved_count=5,
        reranked_count=3,
        graph_nodes_count=2,
        context_chars=500,
        raw_citations_count=3,
        filtered_citations_count=3,
        returned_citations_count=3,
        context_sufficient=False,
        llm_enabled=False,
        llm_provider="anthropic",
        llm_answer_model="claude-3-5-haiku",
        llm_verifier_model="claude-3-5-haiku",
        verify_enabled=False,
        embedding_provider="anthropic",
        embedding_model="none",
        discovered_modules=[],
        fallback_reason="not_configured",
        verify_valid=None,
        verify_skipped=True,
        budget_seconds=12.0,
        budget_exhausted=True,
        stage_timings={"total_ms": 50.0},
        inventory_intent=False,
        inventory_target=None,
        llm_error=None,
    )

    assert diagnostics["inventory_route"] is None
    assert diagnostics["llm_capabilities"] == {}
    assert diagnostics["embedding_capabilities"] == {}
    assert "llm_error" not in diagnostics


def test_build_retrieval_diagnostics_merges_semantic_payload() -> None:
    """Incluye diagnostics semánticos en el payload retrieval-only."""
    from coderag.api.query_diagnostics import build_retrieval_diagnostics

    diagnostics = build_retrieval_diagnostics(
        settings=_SettingsWithoutCapabilities(),
        retrieved_count=2,
        reranked_count=1,
        graph_nodes_count=1,
        context_chars=0,
        raw_citations_count=1,
        filtered_citations_count=1,
        returned_citations_count=1,
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        budget_seconds=10.0,
        budget_exhausted=False,
        stage_timings={"graph_expand_ms": 5.0},
        fallback_reason=None,
        semantic_diagnostics={
            "semantic_query_enabled": True,
            "semantic_nodes_used": 3,
        },
    )

    assert diagnostics["semantic_query_enabled"] is True
    assert diagnostics["semantic_nodes_used"] == 3
