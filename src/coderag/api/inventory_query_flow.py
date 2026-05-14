"""Inventory query flow extracted from query service orchestration."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic

from coderag.core.models import InventoryItem, InventoryQueryResponse


@dataclass(frozen=True)
class InventoryQueryHooks:
    """Injected collaborators required by the inventory query flow."""

    get_settings: Callable[[], object]
    extract_inventory_target: Callable[[str], str | None]
    is_inventory_explain_query: Callable[[str], bool]
    extract_module_name: Callable[[str], str | None]
    resolve_module_scope: Callable[[str, str | None], str | None]
    inventory_term_aliases: Callable[[str], list[str]]
    sanitize_inventory_pagination: Callable[[int, int], tuple[int, int]]
    elapsed_milliseconds: Callable[[float], float]
    build_inventory_missing_target_diagnostics: Callable[..., dict]
    normalize_inventory_token: Callable[[str], str]
    dependency_inventory_terms: Sequence[str]
    remaining_budget_seconds: Callable[[float, float], float]
    query_inventory_entities: Callable[..., list[dict]]
    build_inventory_citations: Callable[[list[InventoryItem]], list]
    describe_inventory_components: Callable[..., list[tuple[str, str]]]
    build_extractive_fallback: Callable[..., str]
    build_inventory_diagnostics: Callable[..., dict]


def run_inventory_query(
    repo_id: str,
    query: str,
    page: int,
    page_size: int,
    *,
    hooks: InventoryQueryHooks,
) -> InventoryQueryResponse:
    """Execute the graph-backed inventory query flow with pagination."""
    settings = hooks.get_settings()
    budget_seconds = max(1.0, float(settings.query_max_seconds))
    pipeline_started_at = monotonic()
    stage_timings: dict[str, float] = {}

    parse_started_at = monotonic()
    inventory_target = hooks.extract_inventory_target(query)
    explain_inventory = hooks.is_inventory_explain_query(query)
    module_name_raw = hooks.extract_module_name(query)
    module_name = hooks.resolve_module_scope(repo_id, module_name_raw)
    inventory_terms = (
        hooks.inventory_term_aliases(inventory_target)
        if inventory_target
        else []
    )
    safe_page, safe_page_size = hooks.sanitize_inventory_pagination(page, page_size)
    stage_timings["parse_ms"] = hooks.elapsed_milliseconds(parse_started_at)

    if not inventory_target:
        stage_timings["total_ms"] = hooks.elapsed_milliseconds(pipeline_started_at)
        diagnostics = hooks.build_inventory_missing_target_diagnostics(
            explain_inventory=explain_inventory,
            module_name_raw=module_name_raw,
            module_name=module_name,
            budget_seconds=budget_seconds,
            stage_timings=stage_timings,
        )
        return InventoryQueryResponse(
            answer="No se detectó un objetivo de inventario en la consulta.",
            target=None,
            module_name=module_name,
            total=0,
            page=safe_page,
            page_size=safe_page_size,
            items=[],
            citations=[],
            diagnostics=diagnostics,
        )

    inventory_target_normalized = hooks.normalize_inventory_token(inventory_target)
    auto_context_for_inventory = (
        inventory_target_normalized in hooks.dependency_inventory_terms
    )

    fallback_reason: str | None = None
    discovered_inventory: list[dict] = []

    if hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0:
        fallback_reason = "time_budget_exhausted"
    else:
        graph_started_at = monotonic()
        discovered_inventory = hooks.query_inventory_entities(
            repo_id=repo_id,
            target_term=inventory_target,
            module_name=module_name,
        )
        stage_timings["graph_inventory_ms"] = hooks.elapsed_milliseconds(
            graph_started_at
        )

    pagination_started_at = monotonic()
    total_items = len(discovered_inventory)
    offset = (safe_page - 1) * safe_page_size
    paged_inventory = discovered_inventory[offset:offset + safe_page_size]
    stage_timings["pagination_ms"] = hooks.elapsed_milliseconds(
        pagination_started_at
    )

    items = [
        InventoryItem(
            label=str(item.get("label", "")),
            path=str(item.get("path", "unknown")),
            kind=str(item.get("kind", "file")),
            start_line=int(item.get("start_line", 1)),
            end_line=int(item.get("end_line", 1)),
        )
        for item in paged_inventory
    ]

    citations = hooks.build_inventory_citations(items)

    if (
        fallback_reason is None
        and hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
    ):
        fallback_reason = "time_budget_exhausted"

    purpose_started_at = monotonic()
    component_purposes: list[tuple[str, str]] = []
    if (explain_inventory or auto_context_for_inventory) and citations:
        component_purposes = hooks.describe_inventory_components(
            repo_id=repo_id,
            citations=citations,
            pipeline_started_at=pipeline_started_at,
            budget_seconds=budget_seconds,
            query=query,
        )
    stage_timings["component_purpose_ms"] = hooks.elapsed_milliseconds(
        purpose_started_at
    )

    answer = hooks.build_extractive_fallback(
        citations,
        inventory_mode=True,
        inventory_target=inventory_target,
        query=query,
        fallback_reason=fallback_reason or "inventory_structured",
        component_purposes=component_purposes,
    )

    stage_timings["total_ms"] = hooks.elapsed_milliseconds(pipeline_started_at)
    diagnostics = hooks.build_inventory_diagnostics(
        inventory_target=inventory_target,
        inventory_terms=inventory_terms,
        inventory_count=total_items,
        explain_inventory=explain_inventory,
        inventory_purpose_count=len(component_purposes),
        module_name_raw=module_name_raw,
        module_name=module_name,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        fallback_reason=fallback_reason,
    )

    return InventoryQueryResponse(
        answer=answer,
        target=inventory_target,
        module_name=module_name,
        total=total_items,
        page=safe_page,
        page_size=safe_page_size,
        items=items,
        citations=citations,
        diagnostics=diagnostics,
    )