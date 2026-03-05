"""End-to-end query orchestration for Hybrid RAG + GraphRAG."""

from pathlib import Path
import re

from coderag.core.models import Citation, QueryResponse
from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.llm.openai_client import AnswerClient
from coderag.retrieval.context_assembler import assemble_context
from coderag.retrieval.graph_expand import expand_with_graph
from coderag.retrieval.hybrid_search import hybrid_search
from coderag.retrieval.reranker import rerank


def _build_extractive_fallback(citations: list[Citation]) -> str:
    """Build a local evidence-only answer when LLM is unavailable."""
    if not citations:
        return "No se encontró información en el repositorio."

    lines = [
        "OpenAI no está configurado; mostrando evidencias relevantes encontradas:",
    ]
    for index, citation in enumerate(citations[:5], start=1):
        lines.append(
            (
                f"{index}. {citation.path} "
                f"(líneas {citation.start_line}-{citation.end_line}, "
                f"score {citation.score:.4f})"
            )
        )
    return "\n".join(lines)


def _is_module_query(query: str) -> bool:
    """Return whether user asks about repository modules/services."""
    normalized = query.lower()
    return any(
        token in normalized
        for token in ["modulo", "módulo", "module", "modulos", "módulos"]
    )


def _discover_repo_modules(repo_id: str) -> list[str]:
    """Discover top-level module folders from locally cloned repository."""
    settings = get_settings()
    repo_path = settings.workspace_path / repo_id
    if not repo_path.exists() or not repo_path.is_dir():
        return []

    modules: list[str] = []
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            continue
        if (child / "pom.xml").exists() or name.startswith("mall-"):
            modules.append(name)
    return modules


def _is_inventory_query(query: str) -> bool:
    """Return whether query asks for an exhaustive list of entities."""
    normalized = query.lower()
    has_all_word = any(
        token in normalized
        for token in ["todos", "todas", "all", "lista", "listar", "cuales son"]
    )
    return has_all_word


def _extract_module_name(query: str) -> str | None:
    """Extract mall-* module token from query text when present."""
    match = re.search(r"(mall-[a-z0-9-]+)", query.lower())
    if match is None:
        return None
    return match.group(1)


def _extract_inventory_target(query: str) -> str | None:
    """Extract target entity token from inventory-style natural language queries."""
    normalized = query.lower()

    match_es = re.search(r"todos?\s+(?:los|las)?\s*([a-z0-9_-]+)", normalized)
    if match_es:
        token = match_es.group(1)
        return token[:-1] if token.endswith("s") else token

    match_en = re.search(r"all\s+([a-z0-9_-]+)", normalized)
    if match_en:
        token = match_en.group(1)
        return token[:-1] if token.endswith("s") else token

    return None


def _query_inventory_entities(
    repo_id: str,
    target_term: str,
    module_name: str | None,
) -> list[dict]:
    """Query inventory entities from graph using generic target term."""
    graph = GraphBuilder()
    try:
        return graph.query_inventory(
            repo_id=repo_id,
            target_term=target_term,
            module_name=module_name,
            limit=800,
        )
    except Exception:
        return []
    finally:
        graph.close()


def _is_noisy_path(path: str) -> bool:
    """Return whether citation path is likely non-informative noise."""
    normalized = path.strip().lower()
    if not normalized:
        return True
    if normalized in {".", "..", "document", "docs"}:
        return True
    if normalized.startswith("document/"):
        return True
    return False


def _citation_priority(citation: Citation) -> tuple[int, float]:
    """Assign sorting priority: code/module paths first, then score."""
    path = citation.path.lower()
    if path.endswith("pom.xml"):
        rank = 0
    elif "src/main/" in path or path.endswith(".java") or path.endswith(".py"):
        rank = 1
    elif "/" not in path and path.startswith("mall-"):
        rank = 2
    else:
        rank = 3
    return (rank, -citation.score)


def run_query(repo_id: str, query: str, top_n: int, top_k: int) -> QueryResponse:
    """Run full query pipeline and return answer with citations."""
    settings = get_settings()
    initial = hybrid_search(repo_id=repo_id, query=query, top_n=top_n)
    reranked = rerank(chunks=initial, top_k=top_k)
    graph_context = expand_with_graph(chunks=reranked)
    discovered_modules = _discover_repo_modules(repo_id) if _is_module_query(query) else []
    module_name = _extract_module_name(query)
    inventory_target = _extract_inventory_target(query) if _is_inventory_query(query) else None
    discovered_inventory: list[dict] = []
    if inventory_target:
        discovered_inventory = _query_inventory_entities(
            repo_id=repo_id,
            target_term=inventory_target,
            module_name=module_name,
        )

    context = assemble_context(
        chunks=reranked,
        graph_records=graph_context,
        max_tokens=settings.max_context_tokens,
    )
    if discovered_modules:
        module_block = "\n".join(
            [
                "MODULE_INVENTORY:",
                *[f"- {module}" for module in discovered_modules],
            ]
        )
        context = f"{module_block}\n\n{context}"
    if discovered_inventory:
        inventory_lines = [
            f"- {item.get('label')} | {item.get('path')} | "
            f"{item.get('start_line')}-{item.get('end_line')}"
            for item in discovered_inventory
        ]
        service_block = "\n".join(
            [
                f"INVENTORY[{inventory_target}]",
                *inventory_lines,
            ]
        )
        context = f"{service_block}\n\n{context}"

    raw_citations = [
        Citation(
            path=item.metadata.get("path", "unknown"),
            start_line=int(item.metadata.get("start_line", 0)),
            end_line=int(item.metadata.get("end_line", 0)),
            score=float(item.score),
            reason="hybrid_rag_match",
        )
        for item in reranked
    ]

    filtered_citations = [
        item for item in raw_citations if not _is_noisy_path(item.path)
    ]
    citations = sorted(filtered_citations, key=_citation_priority)
    if discovered_inventory:
        inventory_citations = [
            Citation(
                path=str(item.get("path", "unknown")),
                start_line=int(item.get("start_line", 1)),
                end_line=int(item.get("end_line", 1)),
                score=1.0,
                reason="inventory_graph_match",
            )
            for item in discovered_inventory
            if not _is_noisy_path(str(item.get("path", "")))
        ]
        citations = inventory_citations + citations

    client = AnswerClient()
    if client.enabled:
        answer = client.answer(query=query, context=context)
        valid = client.verify(answer=answer, context=context)
        if not valid:
            answer = "No se encontró información en el repositorio."
    else:
        answer = _build_extractive_fallback(citations)

    diagnostics = {
        "retrieved": len(initial),
        "reranked": len(reranked),
        "graph_nodes": len(graph_context),
        "openai_enabled": client.enabled,
        "discovered_modules": discovered_modules,
        "inventory_target": inventory_target,
        "inventory_count": len(discovered_inventory),
    }
    return QueryResponse(answer=answer, citations=citations, diagnostics=diagnostics)
