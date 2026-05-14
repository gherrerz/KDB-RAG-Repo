"""Orquestación de consultas de un extremo a otro para Hybrid RAG + GraphRAG."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from time import monotonic

from coderag.core.models import (
    Citation,
    InventoryItem,
    InventoryQueryResponse,
    QueryResponse,
    RetrievedChunk,
    RetrievalQueryResponse,
    RetrievalStatistics,
    RetrievalChunk,
)
from coderag.core.settings import get_settings
from coderag.api import citation_presentation as citation_presentation_service
from coderag.api import external_imports as external_imports_service
from coderag.api import internal_importers as internal_importers_service
from coderag.api import inventory_graph_first as inventory_graph_first_service
from coderag.api import inventory_helpers as inventory_helpers_service
from coderag.api import inventory_purpose as inventory_purpose_service
from coderag.api import query_answer_resolution as query_answer_resolution_service
from coderag.api import query_hybrid_pipeline as query_hybrid_pipeline_service
from coderag.api import query_signals as query_signals_service
from coderag.api import inventory_query_flow as inventory_query_flow_service
from coderag.api import literal_mode as literal_mode_service
from coderag.api.citation_filters import build_inventory_citations, is_noisy_path
from coderag.api.query_diagnostics import (
    build_inventory_diagnostics,
    build_inventory_missing_target_diagnostics,
    build_query_diagnostics,
    build_retrieval_diagnostics,
)
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.llm.openai_client import AnswerClient
from coderag.retrieval.context_assembler import assemble_context
from coderag.retrieval.graph_expand import expand_with_graph, expand_with_graph_with_diagnostics
from coderag.retrieval.hybrid_search import hybrid_search
from coderag.retrieval.reranker import rerank


_fallback_header = citation_presentation_service.fallback_header


def _build_extractive_fallback(
    citations: list[Citation],
    inventory_mode: bool = False,
    inventory_target: str | None = None,
    query: str = "",
    fallback_reason: str = "not_configured",
    component_purposes: list[tuple[str, str]] | None = None,
) -> str:
    """Cree una respuesta local basada únicamente en evidencia cuando el LLM no esté disponible."""
    return citation_presentation_service.build_extractive_fallback(
        citations=citations,
        inventory_mode=inventory_mode,
        inventory_target=inventory_target,
        query=query,
        fallback_reason=fallback_reason,
        component_purposes=component_purposes,
    )


_is_module_query = query_signals_service.is_module_query


def _discover_repo_modules(repo_id: str) -> list[str]:
    """Descubra módulos persistidos en Neo4j sin depender del workspace local."""
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

    graph = GraphBuilder()
    try:
        modules = graph.query_repo_modules(repo_id)
    finally:
        graph.close()

    return [
        module_name
        for module_name in modules
        if module_name and not module_name.startswith(".")
        and module_name.lower() not in excluded_names
    ]


_is_inventory_query = query_signals_service.is_inventory_query
_is_external_import_query = query_signals_service.is_external_import_query
_normalize_query_signal_text = query_signals_service.normalize_query_signal_text
_extract_file_reference_candidates = (
    query_signals_service.extract_file_reference_candidates
)
_is_reverse_file_import_query = (
    query_signals_service.is_reverse_file_import_query
)


_extract_external_import_candidates = (
    external_imports_service.extract_external_import_candidates
)


def _resolve_external_import_source_paths(repo_id: str, query: str) -> dict[str, int]:
    """Busca archivos conectados a imports externos relevantes para la query."""
    return external_imports_service.resolve_external_import_source_paths(
        repo_id=repo_id,
        query=query,
        hooks=_external_import_hooks(),
    )


def _apply_external_import_seed_boost(
    repo_id: str,
    query: str,
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int, dict[str, int]]:
    """Da prioridad previa al rerank a archivos respaldados por IMPORTS_EXTERNAL_FILE."""
    return external_imports_service.apply_external_import_seed_boost(
        repo_id=repo_id,
        query=query,
        chunks=chunks,
        hooks=_external_import_hooks(),
    )


def _build_external_import_seed_chunks(
    repo_id: str,
    matched_paths: dict[str, int],
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int]:
    """Crea seeds sintéticos mínimos para graph expansion cuando falta el archivo importador."""
    return external_imports_service.build_external_import_seed_chunks(
        repo_id=repo_id,
        matched_paths=matched_paths,
        chunks=chunks,
    )


def _external_import_hooks(
) -> external_imports_service.ExternalImportHooks:
    """Build external import hooks from current query_service symbols."""
    return external_imports_service.ExternalImportHooks(
        graph_builder_factory=GraphBuilder,
        is_external_import_query=_is_external_import_query,
    )


def _resolve_reverse_file_target_paths(
    repo_id: str,
    query: str,
) -> tuple[list[str], int, tuple[str, ...]]:
    """Resuelve archivos objetivo mencionados en queries inversas de imports."""
    return inventory_graph_first_service.resolve_reverse_file_target_paths(
        repo_id=repo_id,
        query=query,
        hooks=_inventory_graph_first_hooks(),
    )


def _resolve_internal_file_importer_paths(
    repo_id: str,
    query: str,
) -> tuple[dict[str, int], list[str], tuple[str, ...]]:
    """Busca archivos que importan directamente al archivo objetivo citado."""
    return internal_importers_service.resolve_internal_file_importer_paths(
        repo_id=repo_id,
        query=query,
        hooks=_internal_importer_hooks(),
    )


def _apply_internal_file_importer_seed_boost(
    repo_id: str,
    query: str,
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int, dict[str, int], list[str]]:
    """Prioriza paths respaldados por IMPORTS_FILE inverso para queries de importadores."""
    return internal_importers_service.apply_internal_file_importer_seed_boost(
        repo_id=repo_id,
        query=query,
        chunks=chunks,
        hooks=_internal_importer_hooks(),
    )


def _build_internal_file_importer_seed_chunks(
    repo_id: str,
    matched_paths: dict[str, int],
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int]:
    """Crea seeds sintéticos para importadores internos ausentes del retrieval inicial."""
    return internal_importers_service.build_internal_file_importer_seed_chunks(
        repo_id=repo_id,
        matched_paths=matched_paths,
        chunks=chunks,
    )


def _internal_importer_hooks(
) -> internal_importers_service.InternalImporterHooks:
    """Build internal importer hooks from current query_service symbols."""
    return internal_importers_service.InternalImporterHooks(
        graph_builder_factory=GraphBuilder,
        resolve_reverse_file_target_paths=_resolve_reverse_file_target_paths,
    )


_build_reverse_file_import_answer = (
    inventory_graph_first_service.build_reverse_file_import_answer
)


def _run_reverse_file_import_query(
    repo_id: str,
    query: str,
    page: int,
    page_size: int,
) -> InventoryQueryResponse | None:
    """Ejecuta un lookup graph-first para preguntas de importadores directos."""
    return inventory_graph_first_service.run_reverse_file_import_query(
        repo_id=repo_id,
        query=query,
        page=page,
        page_size=page_size,
        hooks=_inventory_graph_first_hooks(),
    )


def _inventory_graph_first_hooks(
) -> inventory_graph_first_service.InventoryGraphFirstHooks:
    """Build graph-first inventory hooks from current query_service symbols."""
    return inventory_graph_first_service.InventoryGraphFirstHooks(
        get_settings=get_settings,
        graph_builder_factory=GraphBuilder,
        is_reverse_file_import_query=_is_reverse_file_import_query,
        extract_file_reference_candidates=_extract_file_reference_candidates,
        sanitize_inventory_pagination=_sanitize_inventory_pagination,
        build_inventory_citations=build_inventory_citations,
        is_inventory_query=_is_inventory_query,
        extract_inventory_target=_extract_inventory_target,
        run_inventory_query=run_inventory_query,
    )


def _literal_mode_hooks() -> literal_mode_service.LiteralModeHooks:
    """Build literal mode hooks from current query_service collaborators."""
    return literal_mode_service.LiteralModeHooks(
        get_settings=get_settings,
        resolve_repo_file_path=_resolve_repo_file_path,
        has_local_repo_workspace=_has_local_repo_workspace,
    )


_is_literal_code_query = literal_mode_service.is_literal_code_query
_extract_literal_file_candidates = literal_mode_service.extract_literal_file_candidates
_extract_literal_symbol_candidates = (
    literal_mode_service.extract_literal_symbol_candidates
)


def _resolve_repo_root(repo_id: str) -> Path | None:
    """Resuelve la ruta raíz del repositorio local en workspace."""
    return literal_mode_service.resolve_repo_root(
        repo_id,
        hooks=_literal_mode_hooks(),
    )


def _resolve_literal_file_match(
    repo_id: str,
    query: str,
) -> tuple[Path | None, str | None, str]:
    """Resuelve un archivo para modo literal con política estricta de coincidencia."""
    return literal_mode_service.resolve_literal_file_match(
        repo_id=repo_id,
        query=query,
        hooks=_literal_mode_hooks(),
    )


_python_symbol_spans = literal_mode_service.python_symbol_spans
_brace_block_end = literal_mode_service.brace_block_end
_generic_symbol_spans = literal_mode_service.generic_symbol_spans


def _resolve_literal_symbol_match(
    repo_id: str,
    query: str,
) -> tuple[Path | None, str | None, int | None, int | None, str | None, str]:
    """Resuelve símbolo exacto único en archivos del repositorio."""
    return literal_mode_service.resolve_literal_symbol_match(
        repo_id=repo_id,
        query=query,
        hooks=_literal_mode_hooks(),
    )


_slice_lines = literal_mode_service.slice_lines


def _build_literal_code_response(repo_id: str, query: str) -> QueryResponse:
    """Construye respuesta determinística en modo código literal sin síntesis LLM."""
    return literal_mode_service.build_literal_code_response(
        repo_id=repo_id,
        query=query,
        hooks=_literal_mode_hooks(),
    )


def _build_literal_retrieval_response(
    repo_id: str,
    query: str,
    include_context: bool,
) -> RetrievalQueryResponse:
    """Construye respuesta retrieval-only determinística para solicitudes de código literal."""
    return literal_mode_service.build_literal_retrieval_response(
        repo_id=repo_id,
        query=query,
        include_context=include_context,
        hooks=_literal_mode_hooks(),
    )


_extract_module_name = inventory_helpers_service.extract_module_name
_normalize_inventory_token = inventory_helpers_service.normalize_inventory_token
_inventory_base_forms = inventory_helpers_service.inventory_base_forms
_canonical_inventory_term = inventory_helpers_service.canonical_inventory_term
_plural_variants = inventory_helpers_service.plural_variants
_deduplicate_citations = citation_presentation_service.deduplicate_citations
_deduplicate_citations_by_path = (
    citation_presentation_service.deduplicate_citations_by_path
)
_extract_inventory_target = inventory_helpers_service.extract_inventory_target
_is_inventory_explain_query = inventory_helpers_service.is_inventory_explain_query


def _resolve_repo_file_path(repo_id: str, relative_path: str) -> Path | None:
    """Resuelva y valide la ruta relativa al repositorio a un archivo local existente."""
    normalized = relative_path.strip().replace("\\", "/").strip("/")
    if not normalized:
        return None

    settings = get_settings()
    repo_root = (settings.workspace_path / repo_id).resolve()
    candidate = (repo_root / normalized).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None

    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _has_local_repo_workspace(repo_id: str) -> bool:
    """Indica si el repositorio conserva workspace local para modo literal."""
    repo_root = get_settings().workspace_path / repo_id
    return repo_root.exists() and repo_root.is_dir()


def _first_sentence(text: str) -> str:
    """Devuelve el primer fragmento similar a una oración sin puntuación final."""
    return inventory_purpose_service.first_sentence(text)


def _purpose_from_filename(file_path: Path) -> str | None:
    """Inferir sugerencias de propósito a partir de la raíz del nombre de archivo utilizando heurísticas ligeras."""
    return inventory_purpose_service.purpose_from_filename(file_path)


def _build_purpose_from_source(file_path: Path) -> str | None:
    """Inferir el propósito conciso del componente a partir de la primera declaración de fuente identificable."""
    return inventory_purpose_service.build_purpose_from_source(file_path)


def _describe_inventory_components(
    repo_id: str,
    citations: list[Citation],
    pipeline_started_at: float,
    budget_seconds: float,
    query: str | None = None,
) -> list[tuple[str, str]]:
    """Cree sugerencias de propósito por componente usando metadata persistida."""
    return inventory_purpose_service.describe_inventory_components(
        repo_id=repo_id,
        citations=citations,
        pipeline_started_at=pipeline_started_at,
        budget_seconds=budget_seconds,
        query=query,
        hooks=_inventory_purpose_hooks(),
    )


_inventory_term_aliases = inventory_helpers_service.inventory_term_aliases


def _query_inventory_entities(
    repo_id: str,
    target_term: str,
    module_name: str | None,
) -> list[dict]:
    """Consulta entidades de inventario desde un gráfico utilizando un término objetivo genérico."""
    return inventory_helpers_service.query_inventory_entities(
        repo_id=repo_id,
        target_term=target_term,
        module_name=module_name,
        hooks=_inventory_helper_hooks(),
    )


def _resolve_module_scope(repo_id: str, module_name: str | None) -> str | None:
    """Resuelva el token del módulo de usuario usando metadata persistida del grafo."""
    return inventory_helpers_service.resolve_module_scope(
        repo_id=repo_id,
        module_name=module_name,
        hooks=_inventory_helper_hooks(),
    )


def _inventory_helper_hooks(
) -> inventory_helpers_service.InventoryHelperHooks:
    """Build inventory helper hooks from current query_service symbols."""
    return inventory_helpers_service.InventoryHelperHooks(
        get_settings=get_settings,
        graph_builder_factory=GraphBuilder,
    )


def _inventory_purpose_hooks(
) -> inventory_purpose_service.InventoryPurposeHooks:
    """Build inventory purpose hooks from current query_service symbols."""
    return inventory_purpose_service.InventoryPurposeHooks(
        graph_builder_factory=GraphBuilder,
        remaining_budget_seconds=_remaining_budget_seconds,
        normalize_inventory_token=_normalize_inventory_token,
    )


def _sanitize_inventory_pagination(page: int, page_size: int) -> tuple[int, int]:
    """Normalice los argumentos de paginación de inventario frente a los límites configurados."""
    settings = get_settings()
    safe_page = max(1, int(page))
    default_size = max(1, int(getattr(settings, "inventory_page_size", 80)))
    requested_size = int(page_size) if int(page_size) > 0 else default_size
    max_page_size = max(
        default_size,
        int(getattr(settings, "inventory_max_page_size", 300)),
    )
    safe_page_size = min(max(1, requested_size), max_page_size)
    return safe_page, safe_page_size


def _remaining_budget_seconds(started_at: float, budget_seconds: float) -> float:
    """Devuelve el presupuesto restante (segundos) para una canalización de consultas en ejecución."""
    elapsed = monotonic() - started_at
    return max(0.0, budget_seconds - elapsed)


def _elapsed_milliseconds(started_at: float) -> float:
    """Devuelve los milisegundos transcurridos redondeados para facilitar la lectura del diagnóstico."""
    return round((monotonic() - started_at) * 1000, 2)


_citation_priority = citation_presentation_service.citation_priority


def _graph_context_paths(graph_records: list[dict]) -> tuple[set[str], set[str]]:
    """Resume paths internos y fuentes externas presentes en graph_context."""
    file_dependency_paths: set[str] = set()
    external_source_paths: set[str] = set()
    for record in graph_records:
        labels = {str(label) for label in (record.get("labels") or [])}
        relation_types = {
            str(item) for item in (record.get("relation_types") or []) if str(item)
        }
        props = record.get("props") or {}
        if "File" in labels and "IMPORTS_FILE" in relation_types:
            path = str(props.get("path", "") or "").strip()
            if path:
                file_dependency_paths.add(path)
        if "ExternalSymbol" in labels and "IMPORTS_EXTERNAL_FILE" in relation_types:
            source_path = str(
                record.get("source_path") or props.get("source_path") or ""
            ).strip()
            if source_path:
                external_source_paths.add(source_path)
    return file_dependency_paths, external_source_paths


def _apply_graph_context_chunk_boost(
    chunks: list[RetrievalChunk],
    graph_records: list[dict],
) -> tuple[list[RetrievalChunk], int]:
    """Aplica un boost liviano a chunks respaldados por aristas de archivo."""
    if not chunks or not graph_records:
        return chunks, 0

    file_dependency_paths, external_source_paths = _graph_context_paths(graph_records)
    boosted_count = 0
    rescored: list[tuple[float, RetrievalChunk]] = []
    for chunk in chunks:
        path = str(chunk.metadata.get("path", "") or "")
        boost = 0.0
        if path in file_dependency_paths:
            boost += 0.28
        if path in external_source_paths:
            boost += 0.12
        if boost > 0:
            boosted_count += 1
            chunk.score = float(chunk.score) + boost
        rescored.append((float(chunk.score), chunk))

    rescored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in rescored], boosted_count


def _build_graph_context_citations(graph_records: list[dict]) -> list[Citation]:
    """Construye citas derivadas desde aristas File -> File y externas."""
    citations: list[Citation] = []
    seen: set[tuple[str, int, int, str]] = set()
    for record in graph_records:
        labels = {str(label) for label in (record.get("labels") or [])}
        relation_types = {
            str(item) for item in (record.get("relation_types") or []) if str(item)
        }
        props = record.get("props") or {}

        path = ""
        line = 1
        score = 0.7
        reason = ""
        if "File" in labels and "IMPORTS_FILE" in relation_types:
            path = str(props.get("path", "") or "").strip()
            reason = "graph_file_dependency_match"
            score = 0.75
        elif "ExternalSymbol" in labels and "IMPORTS_EXTERNAL_FILE" in relation_types:
            path = str(
                record.get("source_path") or props.get("source_path") or ""
            ).strip()
            line = int(record.get("line", 1) or 1)
            reason = "graph_external_dependency_source"
            score = 0.7

        if not path:
            continue
        key = (path, line, line, reason)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            Citation(
                path=path,
                start_line=line,
                end_line=line,
                score=score,
                reason=reason,
            )
        )
    return citations


def _safe_discover_repo_modules(repo_id: str, query: str) -> list[str]:
    """Descubre módulos solo cuando la consulta contiene intención de módulo."""
    if not _is_module_query(query):
        return []
    try:
        return _discover_repo_modules(repo_id)
    except Exception:
        return []


def _timed_graph_expand(
    chunks: list[RetrievalChunk],
    query: str | None = None,
) -> tuple[list[dict], float, dict[str, object]]:
    """Ejecuta expansión de grafo y devuelve resultado/latencia/diagnostics."""
    started_at = monotonic()
    result, semantic_diagnostics = expand_with_graph_with_diagnostics(
        chunks=chunks,
        query=query,
    )
    return result, _elapsed_milliseconds(started_at), semantic_diagnostics


def _timed_module_discovery(repo_id: str, query: str) -> tuple[list[str], float]:
    """Ejecuta descubrimiento de módulos y devuelve resultado junto con latencia en ms."""
    started_at = monotonic()
    result = _safe_discover_repo_modules(repo_id=repo_id, query=query)
    return result, _elapsed_milliseconds(started_at)


def _is_context_sufficient(context: str, reranked_count: int) -> bool:
    """Evalúa si el contexto tiene señal mínima para responder con LLM."""
    if reranked_count <= 0:
        return False
    if not context.strip():
        return False
    return len(context.strip()) >= 80


def _build_retrieval_answer(chunks: list[RetrievedChunk], query: str) -> str:
    """Construye salida textual diferenciada para modo retrieval-only."""
    if not chunks:
        return "Modo retrieval-only (sin LLM): no se encontró evidencia relevante."

    lines = [
        "Modo retrieval-only (sin LLM):",
        f"Se recuperaron {len(chunks)} fragmentos relevantes para: {query.strip()}",
        "",
        "Evidencia principal:",
    ]
    for index, chunk in enumerate(chunks[:5], start=1):
        lines.append(
            (
                f"{index}. {chunk.path} "
                f"(líneas {chunk.start_line}-{chunk.end_line}, score {chunk.score:.4f})"
            )
        )
    return "\n".join(lines)


def _build_retrieval_inventory_response(
    *,
    inventory_response: InventoryQueryResponse,
    include_context: bool,
) -> RetrievalQueryResponse:
    """Adapta una respuesta de inventario al contrato retrieval-only."""
    chunks: list[RetrievedChunk] = []
    for item in inventory_response.items:
        chunk_id = f"inventory:{item.path}:{item.start_line}:{item.end_line}"
        chunks.append(
            RetrievedChunk(
                id=chunk_id,
                text=item.label,
                score=1.0,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                kind=item.kind,
                metadata={
                    "path": item.path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "kind": item.kind,
                    "inventory_label": item.label,
                },
            )
        )

    diagnostics = dict(inventory_response.diagnostics)
    diagnostics.update(
        {
            "mode": "retrieval_only",
            "inventory_route": "graph_first_retrieval",
            "inventory_page": inventory_response.page,
            "inventory_page_size": inventory_response.page_size,
            "inventory_total": inventory_response.total,
            "retrieved": inventory_response.total,
            "reranked": len(chunks),
            "graph_nodes": 0,
            "context_chars": 0,
            "raw_citations": len(inventory_response.citations),
            "filtered_citations": len(inventory_response.citations),
            "returned_citations": len(inventory_response.citations),
        }
    )

    context: str | None = None
    if include_context and chunks:
        context_lines = [
            "INVENTORY_CONTEXT:",
            *[
                (
                    f"- {chunk.path} "
                    f"(líneas {chunk.start_line}-{chunk.end_line}) "
                    f"=> {chunk.text}"
                )
                for chunk in chunks
            ],
        ]
        context = "\n".join(context_lines)
        diagnostics["context_chars"] = len(context)

    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer=inventory_response.answer,
        chunks=chunks,
        citations=inventory_response.citations,
        statistics=RetrievalStatistics(
            total_before_rerank=inventory_response.total,
            total_after_rerank=len(chunks),
            graph_nodes_count=0,
        ),
        diagnostics=diagnostics,
        context=context,
    )


@dataclass(frozen=True)
class _ResolvedEmbeddingRuntime:
    """Representa el runtime efectivo de embeddings para diagnostics."""

    provider: str
    model: str


def _resolve_embedding_runtime(
    settings: object,
    embedding_provider: str | None,
    embedding_model: str | None,
) -> _ResolvedEmbeddingRuntime:
    """Resuelve provider y modelo efectivos de embeddings para diagnostics."""
    provider = (
        settings.resolve_embedding_provider(embedding_provider)
        if hasattr(settings, "resolve_embedding_provider")
        else (embedding_provider or "vertex")
    )
    model = (
        settings.resolve_embedding_model(provider, embedding_model)
        if hasattr(settings, "resolve_embedding_model")
        else (embedding_model or "text-embedding-005")
    )
    return _ResolvedEmbeddingRuntime(provider=provider, model=model)


def _hybrid_seed_preparation_hooks(
) -> query_hybrid_pipeline_service.HybridSeedPreparationHooks:
    """Build hybrid seed preparation hooks from current query_service symbols."""
    return query_hybrid_pipeline_service.HybridSeedPreparationHooks(
        hybrid_search=hybrid_search,
        elapsed_milliseconds=_elapsed_milliseconds,
        apply_internal_file_importer_seed_boost=(
            _apply_internal_file_importer_seed_boost
        ),
        apply_external_import_seed_boost=_apply_external_import_seed_boost,
        rerank=rerank,
        build_internal_file_importer_seed_chunks=(
            _build_internal_file_importer_seed_chunks
        ),
        build_external_import_seed_chunks=_build_external_import_seed_chunks,
    )


def _graph_enrichment_hooks(
) -> query_hybrid_pipeline_service.GraphEnrichmentHooks:
    """Build graph enrichment hooks from current query_service symbols."""
    return query_hybrid_pipeline_service.GraphEnrichmentHooks(
        apply_graph_context_chunk_boost=_apply_graph_context_chunk_boost,
        build_graph_context_citations=_build_graph_context_citations,
        citation_priority=_citation_priority,
    )


def _query_answer_resolution_hooks(
) -> query_answer_resolution_service.QueryAnswerResolutionHooks:
    """Build query answer resolution hooks from current query_service symbols."""
    return query_answer_resolution_service.QueryAnswerResolutionHooks(
        is_context_sufficient=_is_context_sufficient,
        build_extractive_fallback=_build_extractive_fallback,
        remaining_budget_seconds=_remaining_budget_seconds,
        elapsed_milliseconds=_elapsed_milliseconds,
    )


def _build_common_hybrid_diagnostics_args(
    *,
    settings: object,
    retrieved_count: int,
    reranked_count: int,
    graph_nodes_count: int,
    context_chars: int,
    raw_citations_count: int,
    filtered_citations_count: int,
    returned_citations_count: int,
    embedding_runtime: _ResolvedEmbeddingRuntime,
    budget_seconds: float,
    budget_exhausted: bool,
    stage_timings: dict[str, float],
    semantic_diagnostics: dict[str, object],
) -> dict[str, object]:
    """Construye el paquete común de diagnostics híbridos."""
    return {
        "settings": settings,
        "retrieved_count": retrieved_count,
        "reranked_count": reranked_count,
        "graph_nodes_count": graph_nodes_count,
        "context_chars": context_chars,
        "raw_citations_count": raw_citations_count,
        "filtered_citations_count": filtered_citations_count,
        "returned_citations_count": returned_citations_count,
        "embedding_provider": embedding_runtime.provider,
        "embedding_model": embedding_runtime.model,
        "budget_seconds": budget_seconds,
        "budget_exhausted": budget_exhausted,
        "stage_timings": stage_timings,
        "semantic_diagnostics": semantic_diagnostics,
    }


def _resolve_query_answer(
    *,
    client: query_answer_resolution_service.QueryAnswerClient,
    settings: query_answer_resolution_service.QueryAnswerSettings,
    query: str,
    citations: list[Citation],
    context: str,
    reranked_count: int,
    verify_enabled: bool,
    pipeline_started_at: float,
    budget_seconds: float,
    stage_timings: dict[str, float],
) -> query_answer_resolution_service.QueryAnswerResolution:
    """Resuelve la respuesta final para query entre LLM y fallback extractivo."""
    return query_answer_resolution_service.resolve_query_answer(
        client=client,
        settings=settings,
        query=query,
        citations=citations,
        context=context,
        reranked_count=reranked_count,
        verify_enabled=verify_enabled,
        pipeline_started_at=pipeline_started_at,
        budget_seconds=budget_seconds,
        stage_timings=stage_timings,
        hooks=_query_answer_resolution_hooks(),
    )


def _prepare_hybrid_graph_seed_input(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None,
    embedding_model: str | None,
) -> query_hybrid_pipeline_service.HybridGraphSeedInput:
    """Ejecuta la preparación híbrida común hasta el input de expansión."""
    return query_hybrid_pipeline_service.prepare_hybrid_graph_seed_input(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        top_k=top_k,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        hooks=_hybrid_seed_preparation_hooks(),
    )


def _finalize_graph_enrichment(
    reranked: list[RetrievalChunk],
    graph_context: list[dict],
    semantic_expand_diagnostics: dict[str, object],
    reverse_import_seed_boosted_count: int,
    reverse_import_seed_chunks_added_count: int,
    reverse_import_target_paths: list[str],
    external_import_seed_boosted_count: int,
    external_import_seed_chunks_added_count: int,
) -> query_hybrid_pipeline_service.GraphEnrichmentResult:
    """Aplica enriquecimiento final común de grafo, citas y diagnostics."""
    return query_hybrid_pipeline_service.finalize_graph_enrichment(
        reranked,
        graph_context,
        semantic_expand_diagnostics,
        reverse_import_seed_boosted_count,
        reverse_import_seed_chunks_added_count,
        reverse_import_target_paths,
        external_import_seed_boosted_count,
        external_import_seed_chunks_added_count,
        hooks=_graph_enrichment_hooks(),
    )


def run_inventory_query(
    repo_id: str,
    query: str,
    page: int,
    page_size: int,
) -> InventoryQueryResponse:
    """Ejecute una consulta de inventario basada en gráficos con paginación y presupuesto de tiempo."""
    return inventory_query_flow_service.run_inventory_query(
        repo_id=repo_id,
        query=query,
        page=page,
        page_size=page_size,
        hooks=_inventory_query_hooks(),
    )


def _inventory_query_hooks() -> inventory_query_flow_service.InventoryQueryHooks:
    """Build inventory query hooks from current query_service symbols."""
    return inventory_query_flow_service.InventoryQueryHooks(
        get_settings=get_settings,
        extract_inventory_target=_extract_inventory_target,
        is_inventory_explain_query=_is_inventory_explain_query,
        extract_module_name=_extract_module_name,
        resolve_module_scope=_resolve_module_scope,
        inventory_term_aliases=_inventory_term_aliases,
        sanitize_inventory_pagination=_sanitize_inventory_pagination,
        elapsed_milliseconds=_elapsed_milliseconds,
        build_inventory_missing_target_diagnostics=(
            build_inventory_missing_target_diagnostics
        ),
        normalize_inventory_token=_normalize_inventory_token,
        dependency_inventory_terms=tuple(
            inventory_helpers_service.DEPENDENCY_INVENTORY_TERMS
        ),
        remaining_budget_seconds=_remaining_budget_seconds,
        query_inventory_entities=_query_inventory_entities,
        build_inventory_citations=build_inventory_citations,
        describe_inventory_components=_describe_inventory_components,
        build_extractive_fallback=_build_extractive_fallback,
        build_inventory_diagnostics=build_inventory_diagnostics,
    )


def _resolve_graph_first_inventory_route(
    repo_id: str,
    query: str,
    page_size: int,
) -> tuple[InventoryQueryResponse | None, bool, str | None, bool]:
    """Resuelve short-circuits graph-first compartidos antes del flujo general."""
    return inventory_graph_first_service.resolve_graph_first_inventory_route(
        repo_id=repo_id,
        query=query,
        page_size=page_size,
        hooks=_inventory_graph_first_hooks(),
    )


def run_retrieval_query(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    include_context: bool = False,
) -> RetrievalQueryResponse:
    """Ejecuta retrieval híbrido sin síntesis LLM y retorna evidencia estructurada."""
    settings = get_settings()
    inventory_page_size = int(getattr(settings, "inventory_page_size", 80))
    inventory_response, _, _, _ = _resolve_graph_first_inventory_route(
        repo_id=repo_id,
        query=query,
        page_size=inventory_page_size,
    )
    if inventory_response is not None:
        return _build_retrieval_inventory_response(
            inventory_response=inventory_response,
            include_context=include_context,
        )

    if _is_literal_code_query(query):
        return _build_literal_retrieval_response(
            repo_id=repo_id,
            query=query,
            include_context=include_context,
        )

    budget_seconds = max(1.0, float(settings.query_max_seconds))
    embedding_runtime = _resolve_embedding_runtime(
        settings=settings,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )

    pipeline_started_at = monotonic()
    hybrid_pipeline = _prepare_hybrid_graph_seed_input(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        top_k=top_k,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    stage_timings = dict(hybrid_pipeline.stage_timings)

    graph_started_at = monotonic()
    graph_context, semantic_expand_diagnostics = expand_with_graph_with_diagnostics(
        chunks=hybrid_pipeline.graph_seed_input,
        query=query,
    )
    stage_timings["graph_expand_ms"] = _elapsed_milliseconds(graph_started_at)
    graph_enrichment = _finalize_graph_enrichment(
        reranked=hybrid_pipeline.reranked,
        graph_context=graph_context,
        semantic_expand_diagnostics=semantic_expand_diagnostics,
        reverse_import_seed_boosted_count=(
            hybrid_pipeline.reverse_import_seed_boosted_count
        ),
        reverse_import_seed_chunks_added_count=(
            hybrid_pipeline.reverse_import_seed_chunks_added_count
        ),
        reverse_import_target_paths=hybrid_pipeline.reverse_import_target_paths,
        external_import_seed_boosted_count=(
            hybrid_pipeline.external_import_seed_boosted_count
        ),
        external_import_seed_chunks_added_count=(
            hybrid_pipeline.external_import_seed_chunks_added_count
        ),
    )
    reranked = graph_enrichment.reranked
    semantic_expand_diagnostics = graph_enrichment.semantic_expand_diagnostics

    context: str | None = None
    context_chars = 0
    if include_context:
        context_started_at = monotonic()
        context = assemble_context(
            chunks=reranked,
            graph_records=graph_context,
            max_tokens=settings.max_context_tokens,
        )
        stage_timings["context_assembly_ms"] = _elapsed_milliseconds(context_started_at)
        context_chars = len(context)

    raw_citations = graph_enrichment.raw_citations
    filtered_citations = graph_enrichment.filtered_citations
    citations = graph_enrichment.citations

    chunks: list[RetrievedChunk] = []
    for item in reranked:
        metadata = dict(item.metadata)
        chunks.append(
            RetrievedChunk(
                id=item.id,
                text=item.text,
                score=float(item.score),
                path=str(metadata.get("path", "unknown")),
                start_line=int(metadata.get("start_line", 0)),
                end_line=int(metadata.get("end_line", 0)),
                kind=str(metadata.get("kind", "code_chunk")),
                metadata=metadata,
            )
        )

    answer = _build_retrieval_answer(chunks=chunks, query=query)
    stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
    common_diagnostics_args = _build_common_hybrid_diagnostics_args(
        settings=settings,
        retrieved_count=len(hybrid_pipeline.initial),
        reranked_count=len(reranked),
        graph_nodes_count=len(graph_context),
        context_chars=context_chars,
        raw_citations_count=len(raw_citations),
        filtered_citations_count=len(filtered_citations),
        returned_citations_count=len(citations),
        embedding_runtime=embedding_runtime,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        semantic_diagnostics=semantic_expand_diagnostics,
    )
    diagnostics = build_retrieval_diagnostics(
        **common_diagnostics_args,
        fallback_reason=None,
    )

    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer=answer,
        chunks=chunks,
        citations=citations,
        statistics=RetrievalStatistics(
            total_before_rerank=len(hybrid_pipeline.initial),
            total_after_rerank=len(reranked),
            graph_nodes_count=len(graph_context),
        ),
        diagnostics=diagnostics,
        context=context,
    )


def run_query(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    llm_provider: str | None = None,
    answer_model: str | None = None,
    verifier_model: str | None = None,
) -> QueryResponse:
    """Ejecute el proceso de consulta completo y devuelva la respuesta con citas."""
    settings = get_settings()
    inventory_page_size = int(getattr(settings, "inventory_page_size", 80))
    inventory_response, inventory_intent, inventory_target, is_reverse_import = (
        _resolve_graph_first_inventory_route(
            repo_id=repo_id,
            query=query,
            page_size=inventory_page_size,
        )
    )
    if inventory_response is not None:
        diagnostics = dict(inventory_response.diagnostics)
        diagnostics.update(
            {
                "inventory_route": (
                    "graph_reverse_import"
                    if is_reverse_import
                    else "graph_first"
                ),
                "inventory_page": inventory_response.page,
                "inventory_page_size": inventory_response.page_size,
                "inventory_total": inventory_response.total,
            }
        )
        return QueryResponse(
            answer=inventory_response.answer,
            citations=inventory_response.citations,
            diagnostics=diagnostics,
        )

    if _is_literal_code_query(query):
        return _build_literal_code_response(repo_id=repo_id, query=query)

    budget_seconds = max(1.0, float(settings.query_max_seconds))
    verify_enabled = (
        settings.is_verify_enabled()
        if hasattr(settings, "is_verify_enabled")
        else True
    )
    embedding_runtime = _resolve_embedding_runtime(
        settings=settings,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    pipeline_started_at = monotonic()
    hybrid_pipeline = _prepare_hybrid_graph_seed_input(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        top_k=top_k,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    stage_timings = dict(hybrid_pipeline.stage_timings)

    parallel_started_at = monotonic()
    with ThreadPoolExecutor(max_workers=2) as executor:
        graph_future = executor.submit(
            _timed_graph_expand,
            hybrid_pipeline.graph_seed_input,
            query,
        )
        modules_future = executor.submit(_timed_module_discovery, repo_id, query)
        graph_context, graph_ms, semantic_expand_diagnostics = graph_future.result()
        discovered_modules, module_ms = modules_future.result()
    stage_timings["graph_expand_ms"] = graph_ms
    stage_timings["module_discovery_ms"] = module_ms
    stage_timings["post_rerank_parallel_ms"] = _elapsed_milliseconds(parallel_started_at)
    graph_enrichment = _finalize_graph_enrichment(
        reranked=hybrid_pipeline.reranked,
        graph_context=graph_context,
        semantic_expand_diagnostics=semantic_expand_diagnostics,
        reverse_import_seed_boosted_count=(
            hybrid_pipeline.reverse_import_seed_boosted_count
        ),
        reverse_import_seed_chunks_added_count=(
            hybrid_pipeline.reverse_import_seed_chunks_added_count
        ),
        reverse_import_target_paths=hybrid_pipeline.reverse_import_target_paths,
        external_import_seed_boosted_count=(
            hybrid_pipeline.external_import_seed_boosted_count
        ),
        external_import_seed_chunks_added_count=(
            hybrid_pipeline.external_import_seed_chunks_added_count
        ),
    )
    reranked = graph_enrichment.reranked
    semantic_expand_diagnostics = graph_enrichment.semantic_expand_diagnostics

    context_started_at = monotonic()
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
    stage_timings["context_assembly_ms"] = _elapsed_milliseconds(context_started_at)

    raw_citations = graph_enrichment.raw_citations
    filtered_citations = graph_enrichment.filtered_citations
    citations = graph_enrichment.citations

    client = AnswerClient(
        provider=llm_provider,
        answer_model=answer_model,
        verifier_model=verifier_model,
    )
    answer_resolution = _resolve_query_answer(
        client=client,
        settings=settings,
        query=query,
        citations=citations,
        context=context,
        reranked_count=len(reranked),
        verify_enabled=verify_enabled,
        pipeline_started_at=pipeline_started_at,
        budget_seconds=budget_seconds,
        stage_timings=stage_timings,
    )
    answer = answer_resolution.answer

    stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
    common_diagnostics_args = _build_common_hybrid_diagnostics_args(
        settings=settings,
        retrieved_count=len(hybrid_pipeline.initial),
        reranked_count=len(reranked),
        graph_nodes_count=len(graph_context),
        context_chars=len(context),
        raw_citations_count=len(raw_citations),
        filtered_citations_count=len(filtered_citations),
        returned_citations_count=len(citations),
        embedding_runtime=embedding_runtime,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        semantic_diagnostics=semantic_expand_diagnostics,
    )
    diagnostics = build_query_diagnostics(
        **common_diagnostics_args,
        context_sufficient=answer_resolution.context_sufficient,
        llm_enabled=client.enabled,
        llm_provider=client.provider,
        llm_answer_model=client.answer_model,
        llm_verifier_model=client.verifier_model,
        verify_enabled=verify_enabled,
        discovered_modules=discovered_modules,
        fallback_reason=answer_resolution.fallback_reason,
        verify_valid=answer_resolution.verify_valid,
        verify_skipped=answer_resolution.verify_skipped,
        inventory_intent=inventory_intent,
        inventory_target=inventory_target,
        llm_error=answer_resolution.llm_error,
    )
    return QueryResponse(answer=answer, citations=citations, diagnostics=diagnostics)
