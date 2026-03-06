"""End-to-end query orchestration for Hybrid RAG + GraphRAG."""

from pathlib import Path
import re
import unicodedata

from coderag.core.models import Citation, QueryResponse
from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.llm.openai_client import AnswerClient
from coderag.retrieval.context_assembler import assemble_context
from coderag.retrieval.graph_expand import expand_with_graph
from coderag.retrieval.hybrid_search import hybrid_search
from coderag.retrieval.reranker import rerank


INVENTORY_EQUIVALENT_GROUPS = [
    {"service", "servicio"},
    {"controller", "controlador"},
    {"repository", "repositorio", "repo"},
    {"handler", "manejador"},
    {"model", "modelo"},
    {"entity", "entidad"},
    {"client", "cliente"},
    {"adapter", "adaptador"},
    {"gateway", "pasarela"},
    {"dao", "dataaccess", "data-access"},
    {"config", "configuration", "configuracion", "configuración"},
    {"implementation", "implementacion", "implementación", "impl"},
    {"manager", "gestor"},
    {"factory", "fabrica", "fábrica"},
    {"helper", "util", "utils", "utilidad"},
]


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

    excluded_names = {
        ".git",
        ".github",
        ".vscode",
        "docs",
        "doc",
        "test",
        "tests",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        "scripts",
    }

    modules: list[str] = []
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            continue
        if name.lower() in excluded_names:
            continue
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
    """Extract module or package token from natural language query."""
    normalized = query.lower()

    quoted = re.search(r"['\"]([a-z0-9_./-]+)['\"]", normalized)
    if quoted:
        return quoted.group(1)

    patterns = [
        r"(?:modulo|módulo|module|package|servicio|service)\s+([a-z0-9_./-]+)",
        r"(?:in|en|de|del|of|for)\s+([a-z0-9_./-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            token = match.group(1).strip(".,;:!?()[]{}")
            if token and token not in {"el", "la", "los", "las", "the", "a", "an"}:
                return token

    module_like = re.search(r"\b([a-z0-9]+(?:[-_/][a-z0-9]+)+)\b", normalized)
    if module_like:
        return module_like.group(1)
    return None


def _normalize_inventory_token(token: str) -> str:
    """Normalize inventory token by lowercasing and removing accents/punctuation."""
    lowered = token.lower().strip(".,;:!?()[]{}")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _inventory_base_forms(token: str) -> set[str]:
    """Build candidate base forms from plural/singular variants."""
    normalized = _normalize_inventory_token(token)
    forms = {normalized}

    if normalized.endswith("ies") and len(normalized) > 3:
        forms.add(normalized[:-3] + "y")

    if normalized.endswith("es") and len(normalized) > 3:
        es_root = normalized[:-2]
        if normalized.endswith(
            (
                "ses",
                "xes",
                "zes",
                "ches",
                "shes",
                "ores",
                "dores",
                "tores",
                "ciones",
                "siones",
                "ades",
                "udes",
            )
        ):
            forms.add(es_root)

    if normalized.endswith("s") and len(normalized) > 2:
        forms.add(normalized[:-1])

    return {form for form in forms if form}


def _canonical_inventory_term(token: str) -> str:
    """Return canonical inventory term from available base forms."""
    forms = _inventory_base_forms(token)
    known_terms = {
        term
        for group in INVENTORY_EQUIVALENT_GROUPS
        for term in group
    }
    for form in sorted(forms, key=lambda item: (len(item), item)):
        if form in known_terms:
            return form
    return _normalize_inventory_token(token)


def _plural_variants(token: str) -> set[str]:
    """Generate plural/surface variants for a normalized inventory term."""
    variants = {token}
    if not token:
        return variants

    if token.endswith(("s", "x", "z", "ch", "sh", "or", "ion", "dad", "dor")):
        variants.add(f"{token}es")
    else:
        variants.add(f"{token}s")
    if token.endswith("y") and len(token) > 1:
        variants.add(f"{token[:-1]}ies")
    return variants


def _deduplicate_citations(citations: list[Citation]) -> list[Citation]:
    """Deduplicate citations keeping first occurrence order."""
    seen: set[tuple[str, int, int]] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = (
            citation.path,
            citation.start_line,
            citation.end_line,
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(citation)
    return deduplicated


def _deduplicate_citations_by_path(citations: list[Citation]) -> list[Citation]:
    """Deduplicate citations by path keeping first occurrence order."""
    seen_paths: set[str] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = citation.path.strip().lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduplicated.append(citation)
    return deduplicated


def _extract_inventory_target(query: str) -> str | None:
    """Extract target entity token from inventory-style natural language queries."""
    normalized = query.lower()

    match_es = re.search(r"todos?\s+(?:los|las)?\s*([a-z0-9_-]+)", normalized)
    if match_es:
        return _canonical_inventory_term(match_es.group(1))

    match_en = re.search(r"all\s+([a-z0-9_-]+)", normalized)
    if match_en:
        return _canonical_inventory_term(match_en.group(1))

    match_which = re.search(r"which\s+([a-z0-9_-]+)", normalized)
    if match_which:
        return _canonical_inventory_term(match_which.group(1))

    return None


def _inventory_term_aliases(target_term: str) -> list[str]:
    """Expand inventory target with plural and cross-language aliases."""
    base_forms = _inventory_base_forms(target_term)
    aliases: set[str] = set()
    for form in base_forms:
        aliases.update(_plural_variants(form))

    for group in INVENTORY_EQUIVALENT_GROUPS:
        if base_forms.intersection(group):
            for token in group:
                normalized = _normalize_inventory_token(token)
                aliases.update(_plural_variants(normalized))

    return sorted(aliases)


def _query_inventory_entities(
    repo_id: str,
    target_term: str,
    module_name: str | None,
) -> list[dict]:
    """Query inventory entities from graph using generic target term."""
    graph = GraphBuilder()
    try:
        entities_by_key: dict[tuple[str, int, int], dict] = {}
        for alias in _inventory_term_aliases(target_term):
            entities = graph.query_inventory(
                repo_id=repo_id,
                target_term=alias,
                module_name=module_name,
                limit=800,
            )
            for item in entities:
                path = str(item.get("path", ""))
                start_line = int(item.get("start_line", 1))
                end_line = int(item.get("end_line", 1))
                key = (path, start_line, end_line)
                if key not in entities_by_key:
                    entities_by_key[key] = item
        return sorted(entities_by_key.values(), key=lambda item: item.get("path", ""))
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
    """Assign sorting priority using generic path quality signals."""
    path = citation.path.strip().lower()
    suffix = Path(path).suffix
    code_like_suffixes = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go",
        ".rs", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".php",
        ".rb", ".swift", ".scala", ".sql", ".sh", ".ps1", ".yaml",
        ".yml", ".json", ".toml", ".md", ".xml",
    }
    if suffix in code_like_suffixes:
        rank = 0
    elif "/" in path or "\\" in path:
        rank = 1
    elif path:
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
    inventory_terms = _inventory_term_aliases(inventory_target) if inventory_target else []
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
    if inventory_target and module_name:
        module_prefix = f"{module_name.strip('/')}/"
        module_scoped = [
            item for item in citations
            if item.path.strip().lower().startswith(module_prefix.lower())
        ]
        alias_scoped = [
            item for item in module_scoped
            if any(alias in item.path.strip().lower() for alias in inventory_terms)
        ]
        if alias_scoped:
            citations = alias_scoped
        elif module_scoped:
            citations = module_scoped
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
        citations = _deduplicate_citations(inventory_citations + citations)
        citations = _deduplicate_citations_by_path(citations)

    client = AnswerClient()
    if client.enabled:
        answer = client.answer(query=query, context=context)
        valid = client.verify(answer=answer, context=context)
        if not valid:
            answer = _build_extractive_fallback(citations)
    else:
        answer = _build_extractive_fallback(citations)

    diagnostics = {
        "retrieved": len(initial),
        "reranked": len(reranked),
        "graph_nodes": len(graph_context),
        "openai_enabled": client.enabled,
        "discovered_modules": discovered_modules,
        "inventory_target": inventory_target,
        "inventory_terms": inventory_terms,
        "inventory_count": len(discovered_inventory),
    }
    return QueryResponse(answer=answer, citations=citations, diagnostics=diagnostics)
