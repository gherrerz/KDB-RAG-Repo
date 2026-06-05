"""Orquestador de canalización de ingesta de alto nivel."""

from collections import Counter
from collections import defaultdict
from inspect import signature
from time import perf_counter
from typing import Callable

from coderag.core.lexical_index import (
    delete_active_repository_lexical_data,
    repository_has_active_lexical_data,
    repository_lexical_backend_label,
)
from coderag.core.vector_index import (
    build_managed_vector_index,
    count_repository_vector_documents,
    delete_repository_vector_documents,
)
from coderag.core.models import (
    FileImportRelation,
    RepoAuthConfig,
    ScannedFile,
    SemanticRelation,
    SymbolChunk,
)
from coderag.core.settings import get_settings, resolve_postgres_dsn
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.git_client import clone_repository
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_chroma import ChromaIndex
from coderag.ingestion.repo_scanner import scan_repository_with_stats
from coderag.ingestion.semantic_java import extract_java_semantic_relations
from coderag.ingestion.semantic_javascript import extract_javascript_semantic_relations
from coderag.ingestion.semantic_kotlin import extract_kotlin_semantic_relations
from coderag.ingestion.semantic_python import extract_python_semantic_relations
from coderag.ingestion.semantic_swift import extract_swift_semantic_relations
from coderag.ingestion.semantic_typescript import extract_typescript_semantic_relations
from coderag.ingestion.summarizer import summarize_file, summarize_modules

LoggerFn = Callable[[str], None]

_FILE_IMPORT_RESOLUTION_METHOD_ALIASES = {
    "import": "import_path",
    "import_from": "import_path",
    "path": "import_path",
    "fqcn": "import_path",
    "module": "module_path",
    "qualified": "symbol_path",
    "same_package": "same_package_path",
    "import_wildcard": "wildcard_path",
    "static_import_member": "static_owner_path",
    "static_import_wildcard": "wildcard_path",
    "import_module_path": "module_hint_path",
    "global_unique": "global_unique_path",
    "local": "local_path",
    "local_type": "local_path",
    "unresolved": "unresolved",
}


def _symbol_observability_summary(
    scanned_files: list[ScannedFile],
    symbol_chunks: list[SymbolChunk],
) -> str:
    """Build a compact observability summary for extracted symbols."""
    settings = get_settings()
    extractor_mode = (
        "v2" if getattr(settings, "symbol_extractor_v2_enabled", True) else "legacy"
    )

    files_by_language: dict[str, int] = defaultdict(int)
    for item in scanned_files:
        files_by_language[item.language] += 1

    chunks_by_language: dict[str, int] = defaultdict(int)
    span_lengths: list[int] = []
    for chunk in symbol_chunks:
        chunks_by_language[chunk.language] += 1
        span_lengths.append(max(1, chunk.end_line - chunk.start_line + 1))

    avg_span = 0.0
    p95_span = 0
    long_spans = 0
    if span_lengths:
        avg_span = round(sum(span_lengths) / len(span_lengths), 2)
        sorted_spans = sorted(span_lengths)
        p95_index = max(0, int((len(sorted_spans) - 1) * 0.95))
        p95_span = sorted_spans[p95_index]
        long_spans = sum(1 for value in span_lengths if value > 30)

    return (
        "Observabilidad símbolos: "
        f"modo={extractor_mode}, "
        f"archivos_por_lenguaje={dict(files_by_language)}, "
        f"chunks_por_lenguaje={dict(chunks_by_language)}, "
        f"span_promedio={avg_span}, "
        f"span_p95={p95_span}, "
        f"chunks_span_gt_30={long_spans}"
    )


def _summarize_file_import_relations(
    file_import_relations: list[FileImportRelation],
) -> tuple[int, int, int]:
    """Resume imports top-level a nivel archivo por destino interno/externo."""
    internal_count = sum(
        1 for item in file_import_relations if item.target_kind == "file"
    )
    external_count = sum(
        1 for item in file_import_relations if item.target_kind == "external"
    )
    return len(file_import_relations), internal_count, external_count


def _normalize_file_import_resolution_method(
    resolution_method: str | None,
) -> str | None:
    """Normalize file import resolution labels to a canonical vocabulary."""
    if resolution_method is None:
        return None
    normalized = resolution_method.strip().lower()
    if not normalized:
        return None
    return _FILE_IMPORT_RESOLUTION_METHOD_ALIASES.get(normalized, normalized)


def _normalize_file_import_relations(
    file_import_relations: list[FileImportRelation],
) -> None:
    """Normalize file import relations in place before diagnostics/persistence."""
    for relation in file_import_relations:
        relation.resolution_method = _normalize_file_import_resolution_method(
            relation.resolution_method
        )


def _summarize_file_import_resolution_counts(
    file_import_relations: list[FileImportRelation],
) -> dict[str, int]:
    """Summarize canonical file import resolution methods across languages."""
    return dict(
        Counter(
            relation.resolution_method
            for relation in file_import_relations
            if relation.resolution_method
        )
    )


def _summarize_file_import_resolution_counts_by_language(
    file_import_relations: list[FileImportRelation],
) -> dict[str, dict[str, int]]:
    """Summarize canonical file import resolution methods grouped by language."""
    by_language: dict[str, Counter[str]] = defaultdict(Counter)
    for relation in file_import_relations:
        if not relation.resolution_method:
            continue
        by_language[relation.language][relation.resolution_method] += 1
    return {
        language: dict(counts)
        for language, counts in sorted(by_language.items())
    }


def _summarize_file_import_counts_by_language(
    file_import_relations: list[FileImportRelation],
) -> dict[str, dict[str, int]]:
    """Summarize total/internal/external file import counts grouped by language."""
    by_language: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "internal": 0, "external": 0}
    )
    for relation in file_import_relations:
        counts = by_language[relation.language]
        counts["total"] += 1
        if relation.target_kind == "file":
            counts["internal"] += 1
        elif relation.target_kind == "external":
            counts["external"] += 1
    return {
        language: counts
        for language, counts in sorted(by_language.items())
    }


def _parse_csv_set(raw_value: str, prefix_dot: bool = False) -> set[str]:
    """Convierte una cadena CSV en un conjunto normalizado de tokens."""
    values: set[str] = set()
    for token in raw_value.split(","):
        cleaned = token.strip().lower()
        if not cleaned:
            continue
        if prefix_dot and not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        values.add(cleaned)
    return values


def _read_scan_filters_from_settings(
    settings: object,
) -> tuple[int, set[str], set[str], set[str], set[str]]:
    """Lee y valida filtros de escaneo definidos en variables de entorno."""
    max_file_size = getattr(settings, "scan_max_file_size_bytes", None)
    excluded_dirs_raw = str(getattr(settings, "scan_excluded_dirs", "") or "").strip()
    excluded_extensions_raw = str(
        getattr(settings, "scan_excluded_extensions", "") or ""
    ).strip()
    excluded_files_raw = str(getattr(settings, "scan_excluded_files", "") or "").strip()
    excluded_patterns_raw = str(
        getattr(settings, "scan_excluded_patterns", "") or ""
    ).strip()

    if max_file_size is None or int(max_file_size) <= 0:
        raise RuntimeError(
            "Falta configurar SCAN_MAX_FILE_SIZE_BYTES (>0) en variables de entorno."
        )
    if not excluded_dirs_raw:
        raise RuntimeError(
            "Falta configurar SCAN_EXCLUDED_DIRS en variables de entorno."
        )
    if not excluded_extensions_raw:
        raise RuntimeError(
            "Falta configurar SCAN_EXCLUDED_EXTENSIONS en variables de entorno."
        )

    excluded_dirs = _parse_csv_set(excluded_dirs_raw, prefix_dot=False)
    excluded_extensions = _parse_csv_set(excluded_extensions_raw, prefix_dot=True)
    excluded_files = _parse_csv_set(excluded_files_raw, prefix_dot=False)
    excluded_patterns = _parse_csv_set(excluded_patterns_raw, prefix_dot=False)
    return (
        int(max_file_size),
        excluded_dirs,
        excluded_extensions,
        excluded_files,
        excluded_patterns,
    )


def _run_semantic_extractor(
    extractor: Callable[..., list[SemanticRelation]],
    *,
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int],
    file_imports_sink: list[FileImportRelation],
) -> list[SemanticRelation]:
    """Run a semantic extractor, preserving compatibility with legacy stubs."""
    kwargs = {
        "repo_id": repo_id,
        "scanned_files": scanned_files,
        "symbols": symbols,
        "resolution_stats_sink": resolution_stats_sink,
    }
    parameters = signature(extractor).parameters
    if "file_imports_sink" in parameters:
        kwargs["file_imports_sink"] = file_imports_sink
    return extractor(**kwargs)


def _repo_has_existing_index_data(repo_id: str, logger: LoggerFn) -> bool:
    """Determina si existe data indexada previa para el repositorio."""
    chroma_total = count_repository_vector_documents(
        build_managed_vector_index(),
        repo_id=repo_id,
        collection_names=("code_symbols", "code_files", "code_modules"),
    )

    settings = get_settings()
    lexical_exists = repository_has_active_lexical_data(settings, repo_id)

    graph_exists = False
    graph: GraphBuilder | None = None
    try:
        graph = GraphBuilder()
        graph_exists = graph.has_repo_data(repo_id)
    except Exception as exc:
        logger(
            "Advertencia: no se pudo verificar estado previo en Neo4j "
            f"para repo '{repo_id}' ({exc})"
        )
    finally:
        if graph is not None:
            graph.close()

    return chroma_total > 0 or lexical_exists or graph_exists


def _purge_repo_indices(repo_id: str, logger: LoggerFn) -> None:
    """Purga datos indexados previos por repo_id en Chroma, Lexical y Neo4j."""
    chroma_deleted = delete_repository_vector_documents(
        build_managed_vector_index(),
        repo_id,
    )

    settings = get_settings()
    lexical_deleted = delete_active_repository_lexical_data(settings, repo_id)
    lexical_msg = f"lexical_docs={lexical_deleted['docs_removed']}"

    graph: GraphBuilder | None = None
    try:
        graph = GraphBuilder()
        graph_deleted = graph.delete_repo_subgraph(repo_id)
    finally:
        if graph is not None:
            graph.close()

    logger(
        "Purge por repo_id completado: "
        f"chroma_total={chroma_deleted['total']}, "
        f"{lexical_msg}, "
        f"neo4j_nodes={graph_deleted}"
    )


def ingest_repository(
    provider: str,
    repo_url: str,
    branch: str,
    commit: str | None,
    token: str | None,
    logger: LoggerFn,
    auth=None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    diagnostics_sink: dict[str, object] | None = None,
) -> str:
    """Ejecute la ingesta completa del repositorio y devuelva el identificador del repositorio."""
    settings = get_settings()
    ingestion_started_at = perf_counter()
    effective_auth = auth if auth is not None else RepoAuthConfig()
    logger("Clonando repositorio...")
    clone_started_at = perf_counter()
    repo_id, repo_path = clone_repository(
        repo_url=repo_url,
        destination_root=settings.workspace_path,
        branch=branch,
        commit=commit,
        provider=provider,
        token=token,
        auth=effective_auth,
        ssh_key_content=str(getattr(settings, "git_ssh_key_content", "") or ""),
        ssh_key_content_b64=str(
            getattr(settings, "git_ssh_key_content_b64", "") or ""
        ),
        ssh_known_hosts_content=str(
            getattr(settings, "git_ssh_known_hosts_content", "") or ""
        ),
        ssh_known_hosts_content_b64=str(
            getattr(settings, "git_ssh_known_hosts_content_b64", "") or ""
        ),
        ssh_strict_host_key_checking=str(
            getattr(settings, "git_ssh_strict_host_key_checking", "yes")
        ),
    )
    if diagnostics_sink is not None:
        diagnostics_sink["clone_ms"] = round(
            (perf_counter() - clone_started_at) * 1000.0,
            2,
        )

    if _repo_has_existing_index_data(repo_id=repo_id, logger=logger):
        logger(
            "Repositorio existente detectado; iniciando purge por repo_id "
            "antes de reindexar..."
        )
        try:
            _purge_repo_indices(repo_id=repo_id, logger=logger)
        except Exception as exc:
            raise RuntimeError(
                "No se pudo limpiar la data indexada previa del repositorio "
                f"'{repo_id}'. Se aborta la ingesta para evitar inconsistencias."
            ) from exc

    (
        max_file_size,
        excluded_dirs,
        excluded_extensions,
        excluded_files,
        excluded_patterns,
    ) = _read_scan_filters_from_settings(settings)

    logger("Escaneando archivos...")
    scan_started_at = perf_counter()
    scanned_files, scan_stats = scan_repository_with_stats(
        repo_path,
        max_file_size=max_file_size,
        excluded_dirs=excluded_dirs,
        excluded_extensions=excluded_extensions,
        excluded_files=excluded_files,
        excluded_patterns=excluded_patterns,
    )
    logger(
        "Escaneo: visitados={visited}, indexados={scanned}, excluidos_dir={excluded_dir}, "
        "excluidos_ext={excluded_extension}, excluidos_archivo={excluded_file}, "
        "excluidos_size={excluded_size}, excluidos_decode={excluded_decode}".format(
            **scan_stats
        )
    )
    if diagnostics_sink is not None:
        diagnostics_sink["scan_ms"] = round(
            (perf_counter() - scan_started_at) * 1000.0,
            2,
        )
        diagnostics_sink["scan_stats"] = dict(scan_stats)

    logger("Extrayendo símbolos...")
    chunk_started_at = perf_counter()
    symbol_chunks = extract_symbol_chunks(repo_id=repo_id, scanned_files=scanned_files)
    language_counts: dict[str, int] = {}
    for item in scanned_files:
        language_counts[item.language] = language_counts.get(item.language, 0) + 1
    logger(
        f"Cobertura: archivos={len(scanned_files)}, chunks={len(symbol_chunks)}, "
        f"lenguajes={language_counts}"
    )
    logger(_symbol_observability_summary(scanned_files, symbol_chunks))
    if diagnostics_sink is not None:
        diagnostics_sink["chunk_ms"] = round(
            (perf_counter() - chunk_started_at) * 1000.0,
            2,
        )
        diagnostics_sink["coverage"] = {
            "files": len(scanned_files),
            "chunks": len(symbol_chunks),
            "languages": dict(language_counts),
        }

    logger("Generando embeddings...")
    vector_started_at = perf_counter()
    _index_vectors(
        repo_id,
        scanned_files,
        symbol_chunks,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        logger=logger,
        diagnostics_sink=diagnostics_sink,
    )
    if diagnostics_sink is not None:
        diagnostics_sink["vector_total_ms"] = round(
            (perf_counter() - vector_started_at) * 1000.0,
            2,
        )

    logger("Construyendo indice lexico...")
    lexical_started_at = perf_counter()
    _index_lexical_backend(repo_id, scanned_files, symbol_chunks)
    if diagnostics_sink is not None:
        diagnostics_sink["lexical_ms"] = round(
            (perf_counter() - lexical_started_at) * 1000.0,
            2,
        )

    logger("Construyendo grafo Neo4j...")
    graph_started_at = perf_counter()
    try:
        _index_graph(
            repo_id,
            scanned_files,
            symbol_chunks,
            logger=logger,
            diagnostics_sink=diagnostics_sink,
        )
    except Exception as exc:
        logger(f"Advertencia: no se pudo indexar grafo Neo4j ({exc})")
    finally:
        if diagnostics_sink is not None:
            diagnostics_sink["graph_ms"] = round(
                (perf_counter() - graph_started_at) * 1000.0,
                2,
            )

    logger("Ingesta finalizada")
    if diagnostics_sink is not None:
        diagnostics_sink["ingestion_total_ms"] = round(
            (perf_counter() - ingestion_started_at) * 1000.0,
            2,
        )
    return repo_id


def _index_vectors(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    logger: LoggerFn | None = None,
    diagnostics_sink: dict[str, object] | None = None,
) -> None:
    """Generar y conservar vectores para símbolos/archivos/módulos."""
    chroma = ChromaIndex()
    embedder = EmbeddingClient(
        provider=embedding_provider,
        model=embedding_model,
    )
    vector_metrics: list[dict[str, int | str | None]] = []

    def _progress_logger(stage_name: str, total_items: int) -> LoggerFn | None:
        """Construye un logger de progreso por hitos de 10% para embeddings."""
        if logger is None or total_items <= 0:
            return None

        next_checkpoint = 10

        def _log(processed: int, total: int) -> None:
            nonlocal next_checkpoint
            if total <= 0:
                return
            percentage = int((processed * 100) / total)
            while percentage >= next_checkpoint and next_checkpoint <= 100:
                logger(
                    f"Embeddings {stage_name}: {processed}/{total} "
                    f"({next_checkpoint}%)"
                )
                next_checkpoint += 10

        return _log

    symbol_texts = [chunk.snippet for chunk in symbols]
    symbol_embeddings = embedder.embed_texts(
        symbol_texts,
        progress_callback=_progress_logger("símbolos", len(symbol_texts)),
    )
    symbol_meta = [
        {
            "id": chunk.id,
            "repo_id": repo_id,
            "path": chunk.path,
            "language": chunk.language,
            "symbol_name": chunk.symbol_name,
            "symbol_type": chunk.symbol_type,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
        }
        for chunk in symbols
    ]
    vector_metrics.append(chroma.upsert(
        collection_name="code_symbols",
        ids=[chunk.id for chunk in symbols],
        documents=symbol_texts,
        embeddings=symbol_embeddings,
        metadatas=symbol_meta,
    ))

    file_ids = [f"{repo_id}:{item.path}" for item in scanned_files]
    file_docs = [summarize_file(item) for item in scanned_files]
    file_embeddings = embedder.embed_texts(
        file_docs,
        progress_callback=_progress_logger("archivos", len(file_docs)),
    )
    file_meta = [
        {
            "id": file_ids[index],
            "repo_id": repo_id,
            "path": item.path,
            "language": item.language,
            "start_line": 1,
            "end_line": len(item.content.splitlines()),
        }
        for index, item in enumerate(scanned_files)
    ]
    vector_metrics.append(chroma.upsert(
        collection_name="code_files",
        ids=file_ids,
        documents=file_docs,
        embeddings=file_embeddings,
        metadatas=file_meta,
    ))

    module_summaries = summarize_modules(scanned_files)
    module_names = list(module_summaries.keys())
    module_docs = list(module_summaries.values())
    module_embeddings = embedder.embed_texts(
        module_docs,
        progress_callback=_progress_logger("módulos", len(module_docs)),
    )
    module_ids = [f"{repo_id}:module:{name}" for name in module_names]
    module_meta = [
        {
            "id": module_ids[index],
            "repo_id": repo_id,
            "path": module_names[index],
            "language": "module",
            "start_line": 1,
            "end_line": 1,
        }
        for index in range(len(module_ids))
    ]
    vector_metrics.append(chroma.upsert(
        collection_name="code_modules",
        ids=module_ids,
        documents=module_docs,
        embeddings=module_embeddings,
        metadatas=module_meta,
    ))

    if diagnostics_sink is not None:
        effective_batch_sizes = [
            int(item["effective_batch_size"])
            for item in vector_metrics
            if item.get("effective_batch_size") is not None
        ]
        diagnostics_sink["vector_index"] = {
            "collections_written": len(vector_metrics),
            "initial_batch_size": max(
                int(item["requested_batch_size"])
                for item in vector_metrics
            )
            if vector_metrics
            else 0,
            "effective_batch_size": min(effective_batch_sizes)
            if effective_batch_sizes
            else 0,
            "split_count": sum(int(item["split_count"] or 0) for item in vector_metrics),
            "recovered_retry_count": sum(
                int(item["recovered_retry_count"] or 0)
                for item in vector_metrics
            ),
            "payload_too_large_events": sum(
                int(item["payload_too_large_events"] or 0)
                for item in vector_metrics
            ),
            "proxy_reset_events": sum(
                int(item["proxy_reset_events"] or 0) for item in vector_metrics
            ),
            "upstream_restarting_events": sum(
                int(item["upstream_restarting_events"] or 0)
                for item in vector_metrics
            ),
            "documents_written": sum(
                int(item["documents_written"] or 0) for item in vector_metrics
            ),
            "collections": {
                str(item["collection_name"]): dict(item) for item in vector_metrics
            },
        }


def _index_lexical_backend(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
) -> None:
    """Indexa el backend léxico activo sobre Postgres."""
    docs: list[str] = [chunk.snippet for chunk in symbols]
    metadatas: list[dict] = [
        {
            "id": chunk.id,
            "repo_id": repo_id,
            "path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "symbol_name": chunk.symbol_name,
            "entity_type": "symbol",
        }
        for chunk in symbols
    ]

    file_docs = [summarize_file(item) for item in scanned_files]
    file_meta = [
        {
            "id": f"{repo_id}:{item.path}",
            "repo_id": repo_id,
            "path": item.path,
            "start_line": 1,
            "end_line": len(item.content.splitlines()),
            "symbol_name": "",
            "entity_type": "file",
        }
        for item in scanned_files
    ]
    docs.extend(file_docs)
    metadatas.extend(file_meta)

    module_summaries = summarize_modules(scanned_files)
    module_docs = list(module_summaries.values())
    module_names = list(module_summaries.keys())
    module_meta = [
        {
            "id": f"{repo_id}:module:{module_name}",
            "repo_id": repo_id,
            "path": module_name,
            "start_line": 1,
            "end_line": 1,
            "symbol_name": module_name,
            "entity_type": "module",
        }
        for module_name in module_names
    ]
    docs.extend(module_docs)
    metadatas.extend(module_meta)

    settings = get_settings()
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        raise RuntimeError(
            "LexicalStore Postgres es obligatorio para indexar el corpus "
            "lexico. Configure POSTGRES_* antes de ejecutar la ingesta."
        )

    from coderag.storage.lexical_store import LexicalStore
    from coderag.storage.postgres_session import PostgresSessionFactory

    LexicalStore(
        postgres_dsn,
        settings.lexical_fts_language,
        session_factory=PostgresSessionFactory.from_settings(settings),
    ).index_documents(
        repo_id=repo_id, docs=docs, metadatas=metadatas
    )


def _index_graph(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    logger: LoggerFn | None = None,
    diagnostics_sink: dict[str, object] | None = None,
) -> None:
    """Llene el almacén de gráficos Neo4j con relaciones archivo-símbolo."""
    settings = get_settings()
    semantic_enabled = bool(getattr(settings, "semantic_graph_enabled", False))
    java_semantic_enabled = bool(
        getattr(settings, "semantic_graph_java_enabled", False)
    )
    javascript_semantic_enabled = bool(
        getattr(settings, "semantic_graph_javascript_enabled", False)
    )
    typescript_semantic_enabled = bool(
        getattr(settings, "semantic_graph_typescript_enabled", False)
    )
    kotlin_semantic_enabled = bool(
        getattr(settings, "semantic_graph_kotlin_enabled", False)
    )
    swift_semantic_enabled = bool(
        getattr(settings, "semantic_graph_swift_enabled", False)
    )
    semantic_relations: list[SemanticRelation] = []
    file_import_relations: list[FileImportRelation] = []

    if semantic_enabled:
        started_at = perf_counter()
        extraction_failed = False
        extraction_error: str | None = None
        python_resolution_source_counts: dict[str, int] = {}
        java_resolution_source_counts: dict[str, int] = {}
        javascript_resolution_source_counts: dict[str, int] = {}
        typescript_resolution_source_counts: dict[str, int] = {}
        kotlin_resolution_source_counts: dict[str, int] = {}
        swift_resolution_source_counts: dict[str, int] = {}
        try:
            python_relations = _run_semantic_extractor(
                extract_python_semantic_relations,
                repo_id=repo_id,
                scanned_files=scanned_files,
                symbols=symbols,
                resolution_stats_sink=python_resolution_source_counts,
                file_imports_sink=file_import_relations,
            )
            semantic_relations.extend(python_relations)

            if java_semantic_enabled:
                java_relations = _run_semantic_extractor(
                    extract_java_semantic_relations,
                    repo_id=repo_id,
                    scanned_files=scanned_files,
                    symbols=symbols,
                    resolution_stats_sink=java_resolution_source_counts,
                    file_imports_sink=file_import_relations,
                )
                semantic_relations.extend(java_relations)
            if javascript_semantic_enabled:
                javascript_relations = _run_semantic_extractor(
                    extract_javascript_semantic_relations,
                    repo_id=repo_id,
                    scanned_files=scanned_files,
                    symbols=symbols,
                    resolution_stats_sink=javascript_resolution_source_counts,
                    file_imports_sink=file_import_relations,
                )
                semantic_relations.extend(javascript_relations)
            if typescript_semantic_enabled:
                typescript_relations = _run_semantic_extractor(
                    extract_typescript_semantic_relations,
                    repo_id=repo_id,
                    scanned_files=scanned_files,
                    symbols=symbols,
                    resolution_stats_sink=typescript_resolution_source_counts,
                    file_imports_sink=file_import_relations,
                )
                semantic_relations.extend(typescript_relations)
            if kotlin_semantic_enabled:
                kotlin_relations = _run_semantic_extractor(
                    extract_kotlin_semantic_relations,
                    repo_id=repo_id,
                    scanned_files=scanned_files,
                    symbols=symbols,
                    resolution_stats_sink=kotlin_resolution_source_counts,
                    file_imports_sink=file_import_relations,
                )
                semantic_relations.extend(kotlin_relations)
            if swift_semantic_enabled:
                swift_relations = _run_semantic_extractor(
                    extract_swift_semantic_relations,
                    repo_id=repo_id,
                    scanned_files=scanned_files,
                    symbols=symbols,
                    resolution_stats_sink=swift_resolution_source_counts,
                    file_imports_sink=file_import_relations,
                )
                semantic_relations.extend(swift_relations)
        except Exception as exc:
            extraction_failed = True
            extraction_error = str(exc)
            if logger is not None:
                logger(
                    "Advertencia: extracción semántica deshabilitada por error "
                    f"({exc})"
                )
            semantic_relations = []
        elapsed_ms = round((perf_counter() - started_at) * 1000.0, 2)
        relation_counts_by_type = dict(
            Counter(item.relation_type for item in semantic_relations)
        )
        symbol_path_by_id = {item.id: item.path for item in symbols}
        java_cross_file_relations = [
            item
            for item in semantic_relations
            if item.language == "java"
            and item.target_symbol_id is not None
            and symbol_path_by_id.get(item.target_symbol_id, item.path) != item.path
        ]
        java_cross_file_resolved_by_type = dict(
            Counter(item.relation_type for item in java_cross_file_relations)
        )
        java_cross_file_resolved_count = len(java_cross_file_relations)
        javascript_cross_file_relations = [
            item
            for item in semantic_relations
            if item.language == "javascript"
            and item.target_symbol_id is not None
            and symbol_path_by_id.get(item.target_symbol_id, item.path) != item.path
        ]
        javascript_cross_file_resolved_count = len(javascript_cross_file_relations)
        typescript_cross_file_relations = [
            item
            for item in semantic_relations
            if item.language == "typescript"
            and item.target_symbol_id is not None
            and symbol_path_by_id.get(item.target_symbol_id, item.path) != item.path
        ]
        typescript_cross_file_resolved_count = len(typescript_cross_file_relations)
        kotlin_cross_file_relations = [
            item
            for item in semantic_relations
            if item.language == "kotlin"
            and item.target_symbol_id is not None
            and symbol_path_by_id.get(item.target_symbol_id, item.path) != item.path
        ]
        kotlin_cross_file_resolved_count = len(kotlin_cross_file_relations)
        swift_cross_file_relations = [
            item
            for item in semantic_relations
            if item.language == "swift"
            and item.target_symbol_id is not None
            and symbol_path_by_id.get(item.target_symbol_id, item.path) != item.path
        ]
        swift_cross_file_resolved_count = len(swift_cross_file_relations)
        _normalize_file_import_relations(file_import_relations)
        (
            python_top_level_file_import_count,
            python_top_level_file_import_internal_count,
            python_top_level_file_import_external_count,
        ) = _summarize_file_import_relations(file_import_relations)
        file_import_resolution_counts = _summarize_file_import_resolution_counts(
            file_import_relations
        )
        file_import_resolution_counts_by_language = (
            _summarize_file_import_resolution_counts_by_language(
                file_import_relations
            )
        )
        file_import_counts_by_language = _summarize_file_import_counts_by_language(
            file_import_relations
        )
        unresolved_by_type = dict(
            Counter(
                item.relation_type
                for item in semantic_relations
                if item.target_symbol_id is None
            )
        )
        unresolved_count = sum(
            1 for item in semantic_relations if item.target_symbol_id is None
        )
        unresolved_ratio = (
            round(unresolved_count / len(semantic_relations), 4)
            if semantic_relations
            else 0.0
        )
        if logger is not None:
            logger(
                "Observabilidad semántica: "
                f"enabled=true, "
                f"relation_counts={len(semantic_relations)}, "
                f"relation_counts_by_type={relation_counts_by_type}, "
                f"java_cross_file_resolved_count={java_cross_file_resolved_count}, "
                f"java_cross_file_resolved_by_type={java_cross_file_resolved_by_type}, "
                f"javascript_cross_file_resolved_count={javascript_cross_file_resolved_count}, "
                f"typescript_cross_file_resolved_count={typescript_cross_file_resolved_count}, "
                f"kotlin_cross_file_resolved_count={kotlin_cross_file_resolved_count}, "
                f"swift_cross_file_resolved_count={swift_cross_file_resolved_count}, "
                f"python_resolution_source_counts={python_resolution_source_counts}, "
                f"python_top_level_file_import_count={python_top_level_file_import_count}, "
                "python_top_level_file_import_internal_count="
                f"{python_top_level_file_import_internal_count}, "
                "python_top_level_file_import_external_count="
                f"{python_top_level_file_import_external_count}, "
                f"java_resolution_source_counts={java_resolution_source_counts}, "
                f"javascript_resolution_source_counts={javascript_resolution_source_counts}, "
                f"typescript_resolution_source_counts={typescript_resolution_source_counts}, "
                f"kotlin_resolution_source_counts={kotlin_resolution_source_counts}, "
                f"swift_resolution_source_counts={swift_resolution_source_counts}, "
                f"file_import_resolution_counts={file_import_resolution_counts}, "
                "file_import_resolution_counts_by_language="
                f"{file_import_resolution_counts_by_language}, "
                f"file_import_counts_by_language={file_import_counts_by_language}, "
                f"unresolved_count={unresolved_count}, "
                f"unresolved_by_type={unresolved_by_type}, "
                f"unresolved_ratio={unresolved_ratio}, "
                f"semantic_extraction_ms={elapsed_ms}"
            )
        if diagnostics_sink is not None:
            semantic_payload: dict[str, object] = {
                "enabled": True,
                "status": "fallback" if extraction_failed else "ok",
                "relation_counts": len(semantic_relations),
                "relation_counts_by_type": relation_counts_by_type,
                "java_cross_file_resolved_count": java_cross_file_resolved_count,
                "java_cross_file_resolved_by_type": (
                    java_cross_file_resolved_by_type
                ),
                "javascript_cross_file_resolved_count": (
                    javascript_cross_file_resolved_count
                ),
                "typescript_cross_file_resolved_count": (
                    typescript_cross_file_resolved_count
                ),
                "kotlin_cross_file_resolved_count": (
                    kotlin_cross_file_resolved_count
                ),
                "swift_cross_file_resolved_count": (
                    swift_cross_file_resolved_count
                ),
                "python_resolution_source_counts": python_resolution_source_counts,
                "python_top_level_file_import_count": (
                    python_top_level_file_import_count
                ),
                "python_top_level_file_import_internal_count": (
                    python_top_level_file_import_internal_count
                ),
                "python_top_level_file_import_external_count": (
                    python_top_level_file_import_external_count
                ),
                "java_resolution_source_counts": java_resolution_source_counts,
                "javascript_resolution_source_counts": (
                    javascript_resolution_source_counts
                ),
                "typescript_resolution_source_counts": (
                    typescript_resolution_source_counts
                ),
                "kotlin_resolution_source_counts": kotlin_resolution_source_counts,
                "swift_resolution_source_counts": swift_resolution_source_counts,
                "file_import_resolution_counts": file_import_resolution_counts,
                "file_import_resolution_counts_by_language": (
                    file_import_resolution_counts_by_language
                ),
                "file_import_counts_by_language": file_import_counts_by_language,
                "unresolved_count": unresolved_count,
                "unresolved_by_type": unresolved_by_type,
                "unresolved_ratio": unresolved_ratio,
                "semantic_extraction_ms": elapsed_ms,
            }
            if extraction_error:
                semantic_payload["error"] = extraction_error
            diagnostics_sink["semantic_graph"] = semantic_payload
    elif diagnostics_sink is not None:
        diagnostics_sink["semantic_graph"] = {
            "enabled": False,
            "status": "disabled",
            "relation_counts": 0,
            "relation_counts_by_type": {},
            "java_cross_file_resolved_count": 0,
            "java_cross_file_resolved_by_type": {},
            "javascript_cross_file_resolved_count": 0,
            "typescript_cross_file_resolved_count": 0,
            "kotlin_cross_file_resolved_count": 0,
            "swift_cross_file_resolved_count": 0,
            "python_resolution_source_counts": {},
            "python_top_level_file_import_count": 0,
            "python_top_level_file_import_internal_count": 0,
            "python_top_level_file_import_external_count": 0,
            "java_resolution_source_counts": {},
            "javascript_resolution_source_counts": {},
            "typescript_resolution_source_counts": {},
            "kotlin_resolution_source_counts": {},
            "swift_resolution_source_counts": {},
            "file_import_resolution_counts": {},
            "file_import_resolution_counts_by_language": {},
            "file_import_counts_by_language": {},
            "unresolved_count": 0,
            "unresolved_by_type": {},
            "unresolved_ratio": 0.0,
            "semantic_extraction_ms": 0.0,
        }

    graph = GraphBuilder()
    try:
        graph.upsert_repo_graph(
            repo_id=repo_id,
            scanned_files=scanned_files,
            symbols=symbols,
            semantic_relations=semantic_relations,
            file_import_relations=file_import_relations,
        )
    finally:
        graph.close()
