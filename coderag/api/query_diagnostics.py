"""Helper utilities to build query diagnostics payloads."""

from typing import Any


def _provider_capabilities(
    *,
    settings: Any,
    attr_name: str,
    provider: str,
) -> dict[str, Any]:
    """Return provider capabilities map when settings expose the API."""
    capability_resolver = getattr(settings, attr_name, None)
    if not callable(capability_resolver):
        return {}
    capabilities = capability_resolver(provider)
    if isinstance(capabilities, dict):
        return capabilities
    return {}


def build_query_diagnostics(
    *,
    settings: Any,
    retrieved_count: int,
    reranked_count: int,
    graph_nodes_count: int,
    context_chars: int,
    raw_citations_count: int,
    filtered_citations_count: int,
    returned_citations_count: int,
    context_sufficient: bool,
    llm_enabled: bool,
    llm_provider: str,
    llm_answer_model: str,
    llm_verifier_model: str,
    verify_enabled: bool,
    embedding_provider: str,
    embedding_model: str,
    discovered_modules: list[str],
    fallback_reason: str | None,
    verify_valid: bool | None,
    verify_skipped: bool,
    budget_seconds: float,
    budget_exhausted: bool,
    stage_timings: dict[str, float],
    inventory_intent: bool,
    inventory_target: str | None,
    llm_error: str | None,
    semantic_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the diagnostics payload for query responses."""
    diagnostics: dict[str, Any] = {
        "retrieved": retrieved_count,
        "reranked": reranked_count,
        "graph_nodes": graph_nodes_count,
        "context_chars": context_chars,
        "raw_citations": raw_citations_count,
        "filtered_citations": filtered_citations_count,
        "returned_citations": returned_citations_count,
        "low_signal_retrieval": retrieved_count < 3,
        "context_sufficient": context_sufficient,
        "llm_enabled": llm_enabled,
        "llm_provider": llm_provider,
        "llm_answer_model": llm_answer_model,
        "llm_verifier_model": llm_verifier_model,
        "llm_verify_enabled": verify_enabled,
        "llm_capabilities": _provider_capabilities(
            settings=settings,
            attr_name="llm_provider_capabilities",
            provider=llm_provider,
        ),
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_capabilities": _provider_capabilities(
            settings=settings,
            attr_name="embedding_provider_capabilities",
            provider=embedding_provider,
        ),
        "discovered_modules": discovered_modules,
        "inventory_target": None,
        "inventory_terms": [],
        "inventory_count": 0,
        "fallback_reason": fallback_reason,
        "verify_valid": verify_valid,
        "verify_skipped": verify_skipped,
        "query_budget_seconds": budget_seconds,
        "budget_exhausted": budget_exhausted,
        "stage_timings_ms": stage_timings,
        "inventory_intent": inventory_intent,
        "inventory_route": (
            "fallback_to_general"
            if inventory_intent and not inventory_target
            else None
        ),
    }
    if llm_error is not None:
        diagnostics["llm_error"] = llm_error
    if semantic_diagnostics:
        diagnostics.update(semantic_diagnostics)
    return diagnostics


def build_inventory_missing_target_diagnostics(
    *,
    explain_inventory: bool,
    module_name_raw: str | None,
    module_name: str | None,
    budget_seconds: float,
    stage_timings: dict[str, float],
) -> dict[str, Any]:
    """Build diagnostics payload when inventory target extraction fails."""
    return {
        "inventory_target": None,
        "inventory_terms": [],
        "inventory_count": 0,
        "inventory_explain": explain_inventory,
        "module_name_raw": module_name_raw,
        "module_name_resolved": module_name,
        "query_budget_seconds": budget_seconds,
        "budget_exhausted": False,
        "stage_timings_ms": stage_timings,
        "fallback_reason": "inventory_target_missing",
    }


def build_inventory_diagnostics(
    *,
    inventory_target: str,
    inventory_terms: list[str],
    inventory_count: int,
    explain_inventory: bool,
    inventory_purpose_count: int,
    module_name_raw: str | None,
    module_name: str | None,
    budget_seconds: float,
    budget_exhausted: bool,
    stage_timings: dict[str, float],
    fallback_reason: str | None,
) -> dict[str, Any]:
    """Build diagnostics payload for successful inventory query execution."""
    return {
        "inventory_target": inventory_target,
        "inventory_terms": inventory_terms,
        "inventory_count": inventory_count,
        "inventory_explain": explain_inventory,
        "inventory_purpose_count": inventory_purpose_count,
        "module_name_raw": module_name_raw,
        "module_name_resolved": module_name,
        "query_budget_seconds": budget_seconds,
        "budget_exhausted": budget_exhausted,
        "stage_timings_ms": stage_timings,
        "fallback_reason": fallback_reason,
    }


def build_retrieval_diagnostics(
    *,
    settings: Any,
    retrieved_count: int,
    reranked_count: int,
    graph_nodes_count: int,
    context_chars: int,
    raw_citations_count: int,
    filtered_citations_count: int,
    returned_citations_count: int,
    embedding_provider: str,
    embedding_model: str,
    budget_seconds: float,
    budget_exhausted: bool,
    stage_timings: dict[str, float],
    fallback_reason: str | None,
    semantic_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build diagnostics payload for retrieval-only query execution."""
    diagnostics = {
        "retrieved": retrieved_count,
        "reranked": reranked_count,
        "graph_nodes": graph_nodes_count,
        "context_chars": context_chars,
        "raw_citations": raw_citations_count,
        "filtered_citations": filtered_citations_count,
        "returned_citations": returned_citations_count,
        "low_signal_retrieval": retrieved_count < 3,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_capabilities": _provider_capabilities(
            settings=settings,
            attr_name="embedding_provider_capabilities",
            provider=embedding_provider,
        ),
        "query_budget_seconds": budget_seconds,
        "budget_exhausted": budget_exhausted,
        "stage_timings_ms": stage_timings,
        "fallback_reason": fallback_reason,
        "mode": "retrieval_only",
    }
    if semantic_diagnostics:
        diagnostics.update(semantic_diagnostics)
    return diagnostics