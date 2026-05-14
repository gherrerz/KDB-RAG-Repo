"""Graph-first inventory routing helpers extracted from query service."""

from collections.abc import Callable
from dataclasses import dataclass

from coderag.core.models import InventoryItem, InventoryQueryResponse


@dataclass(frozen=True)
class InventoryGraphFirstHooks:
    """Injected collaborators required by graph-first inventory routing."""

    get_settings: Callable[[], object]
    graph_builder_factory: Callable[[], object]
    is_reverse_file_import_query: Callable[[str], bool]
    extract_file_reference_candidates: Callable[[str], tuple[str, ...]]
    sanitize_inventory_pagination: Callable[[int, int], tuple[int, int]]
    build_inventory_citations: Callable[[list[InventoryItem]], list]
    is_inventory_query: Callable[[str], bool]
    extract_inventory_target: Callable[[str], str | None]
    run_inventory_query: Callable[..., InventoryQueryResponse]


def resolve_reverse_file_target_paths(
    repo_id: str,
    query: str,
    *,
    hooks: InventoryGraphFirstHooks,
) -> tuple[list[str], int, tuple[str, ...]]:
    """Resolve target files mentioned in reverse import queries."""
    if not hooks.is_reverse_file_import_query(query):
        return [], 0, ()

    candidates = hooks.extract_file_reference_candidates(query)
    if not candidates:
        return [], 0, ()

    graph = hooks.graph_builder_factory()
    try:
        rows = graph.query_file_paths_by_suffix(
            repo_id=repo_id,
            candidates=list(candidates),
            limit=20,
        )
    except Exception:
        return [], 0, candidates
    finally:
        graph.close()

    if not rows:
        return [], 0, candidates

    best_score = max(int(row.get("match_score", 0) or 0) for row in rows)
    target_paths = [
        str(row.get("path", "") or "").strip()
        for row in rows
        if int(row.get("match_score", 0) or 0) == best_score
        and str(row.get("path", "") or "").strip()
    ]
    return list(dict.fromkeys(target_paths)), best_score, candidates


def build_reverse_file_import_answer(
    target_paths: list[str],
    items: list[InventoryItem],
) -> str:
    """Build an extractive answer for direct file importer lookups."""
    if not target_paths:
        return "No se pudo resolver el archivo objetivo dentro del repositorio."
    if len(target_paths) > 1:
        lines = [
            "La consulta coincide con múltiples archivos objetivo. Refina la ruta o nombre exacto.",
            "",
            "Candidatos:",
        ]
        lines.extend(f"- {path}" for path in target_paths[:10])
        return "\n".join(lines)
    if not items:
        return (
            f"No se encontraron archivos que importen directamente {target_paths[0]}."
        )

    lines = [
        f"Se encontraron {len(items)} archivos que importan directamente {target_paths[0]}:",
        "",
        "Importadores directos:",
    ]
    lines.extend(f"- {item.path}" for item in items[:20])
    return "\n".join(lines)


def run_reverse_file_import_query(
    repo_id: str,
    query: str,
    page: int,
    page_size: int,
    *,
    hooks: InventoryGraphFirstHooks,
) -> InventoryQueryResponse | None:
    """Execute the graph-first route for direct importer questions."""
    if not hooks.is_reverse_file_import_query(query):
        return None

    settings = hooks.get_settings()
    safe_page, safe_page_size = hooks.sanitize_inventory_pagination(page, page_size)
    target_paths, match_score, candidates = resolve_reverse_file_target_paths(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    target_ambiguous = len(target_paths) > 1

    discovered_importers: list[dict] = []
    if target_paths and not target_ambiguous:
        graph = hooks.graph_builder_factory()
        try:
            discovered_importers = graph.query_file_importers(
                repo_id=repo_id,
                target_paths=target_paths,
                limit=int(getattr(settings, "inventory_entity_limit", 500)),
            )
        except Exception:
            discovered_importers = []
        finally:
            graph.close()

    total_items = len(discovered_importers)
    offset = (safe_page - 1) * safe_page_size
    paged_importers = discovered_importers[offset:offset + safe_page_size]
    items = [
        InventoryItem(
            label=str(item.get("label", "")),
            path=str(item.get("path", "unknown")),
            kind=str(item.get("kind", "file_importer")),
            start_line=int(item.get("start_line", 1)),
            end_line=int(item.get("end_line", 1)),
        )
        for item in paged_importers
    ]
    citations = hooks.build_inventory_citations(items)
    diagnostics = {
        "reverse_import_lookup_used": True,
        "reverse_import_target_candidates": list(candidates),
        "reverse_import_target_paths": target_paths,
        "reverse_import_target_match_score": match_score,
        "reverse_import_target_ambiguous": target_ambiguous,
        "reverse_import_match_count": total_items,
        "inventory_route": "graph_reverse_import",
    }
    return InventoryQueryResponse(
        answer=build_reverse_file_import_answer(target_paths, items),
        target=target_paths[0] if len(target_paths) == 1 else None,
        module_name=None,
        total=total_items,
        page=safe_page,
        page_size=safe_page_size,
        items=items,
        citations=citations,
        diagnostics=diagnostics,
    )


def resolve_graph_first_inventory_route(
    repo_id: str,
    query: str,
    page_size: int,
    *,
    hooks: InventoryGraphFirstHooks,
) -> tuple[InventoryQueryResponse | None, bool, str | None, bool]:
    """Resolve shared graph-first short-circuits before general routing."""
    reverse_import_response = run_reverse_file_import_query(
        repo_id=repo_id,
        query=query,
        page=1,
        page_size=page_size,
        hooks=hooks,
    )
    if reverse_import_response is not None:
        return reverse_import_response, False, None, True

    inventory_intent = hooks.is_inventory_query(query)
    inventory_target = (
        hooks.extract_inventory_target(query) if inventory_intent else None
    )
    if inventory_intent and inventory_target:
        inventory_response = hooks.run_inventory_query(
            repo_id=repo_id,
            query=query,
            page=1,
            page_size=page_size,
        )
        return inventory_response, inventory_intent, inventory_target, False

    return None, inventory_intent, inventory_target, False