"""Orquestador de canalización de ingesta de alto nivel."""

from typing import Callable

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.core.settings import get_settings
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.git_client import clone_repository
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex
from coderag.ingestion.repo_scanner import scan_repository_with_stats
from coderag.ingestion.summarizer import summarize_file, summarize_modules

LoggerFn = Callable[[str], None]


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
) -> tuple[int, set[str], set[str], set[str]]:
    """Lee y valida filtros de escaneo definidos en variables de entorno."""
    max_file_size = getattr(settings, "scan_max_file_size_bytes", None)
    excluded_dirs_raw = str(getattr(settings, "scan_excluded_dirs", "") or "").strip()
    excluded_extensions_raw = str(
        getattr(settings, "scan_excluded_extensions", "") or ""
    ).strip()
    excluded_files_raw = str(getattr(settings, "scan_excluded_files", "") or "").strip()

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
    return int(max_file_size), excluded_dirs, excluded_extensions, excluded_files


def _repo_has_existing_index_data(repo_id: str, logger: LoggerFn) -> bool:
    """Determina si existe data indexada previa para el repositorio."""
    chroma = ChromaIndex()
    chroma_total = 0
    for collection_name in ("code_symbols", "code_files", "code_modules"):
        chroma_total += chroma.count_by_repo_id(
            collection_name=collection_name,
            repo_id=repo_id,
        )

    bm25_exists = GLOBAL_BM25.has_repo(repo_id) or GLOBAL_BM25.has_repo_snapshot(repo_id)

    graph_exists = False
    graph = GraphBuilder()
    try:
        graph_exists = graph.has_repo_data(repo_id)
    except Exception as exc:
        logger(
            "Advertencia: no se pudo verificar estado previo en Neo4j "
            f"para repo '{repo_id}' ({exc})"
        )
    finally:
        graph.close()

    return chroma_total > 0 or bm25_exists or graph_exists


def _purge_repo_indices(repo_id: str, logger: LoggerFn) -> None:
    """Purga datos indexados previos por repo_id en Chroma, BM25 y Neo4j."""
    chroma = ChromaIndex()
    chroma_deleted = chroma.delete_by_repo_id(repo_id=repo_id)

    bm25_deleted = GLOBAL_BM25.delete_repo(repo_id)

    graph = GraphBuilder()
    try:
        graph_deleted = graph.delete_repo_subgraph(repo_id)
    finally:
        graph.close()

    logger(
        "Purge por repo_id completado: "
        f"chroma_total={chroma_deleted['total']}, "
        f"bm25_docs={bm25_deleted['docs_removed']}, "
        f"bm25_snapshot={bm25_deleted['snapshot_removed']}, "
        f"neo4j_nodes={graph_deleted}"
    )


def ingest_repository(
    repo_url: str,
    branch: str,
    commit: str | None,
    logger: LoggerFn,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
) -> str:
    """Ejecute la ingesta completa del repositorio y devuelva el identificador del repositorio."""
    settings = get_settings()
    logger("Clonando repositorio...")
    repo_id, repo_path = clone_repository(
        repo_url=repo_url,
        destination_root=settings.workspace_path,
        branch=branch,
        commit=commit,
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
    ) = _read_scan_filters_from_settings(settings)

    logger("Escaneando archivos...")
    scanned_files, scan_stats = scan_repository_with_stats(
        repo_path,
        max_file_size=max_file_size,
        excluded_dirs=excluded_dirs,
        excluded_extensions=excluded_extensions,
        excluded_files=excluded_files,
    )
    logger(
        "Escaneo: visitados={visited}, indexados={scanned}, excluidos_dir={excluded_dir}, "
        "excluidos_ext={excluded_extension}, excluidos_archivo={excluded_file}, "
        "excluidos_size={excluded_size}, excluidos_decode={excluded_decode}".format(
            **scan_stats
        )
    )

    logger("Extrayendo símbolos...")
    symbol_chunks = extract_symbol_chunks(repo_id=repo_id, scanned_files=scanned_files)
    language_counts: dict[str, int] = {}
    for item in scanned_files:
        language_counts[item.language] = language_counts.get(item.language, 0) + 1
    logger(
        f"Cobertura: archivos={len(scanned_files)}, chunks={len(symbol_chunks)}, "
        f"lenguajes={language_counts}"
    )

    logger("Generando embeddings...")
    _index_vectors(
        repo_id,
        scanned_files,
        symbol_chunks,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )

    logger("Construyendo BM25...")
    _index_bm25(repo_id, scanned_files, symbol_chunks)

    logger("Construyendo grafo Neo4j...")
    try:
        _index_graph(repo_id, scanned_files, symbol_chunks)
    except Exception as exc:
        logger(f"Advertencia: grafo Neo4j no disponible ({exc})")

    logger("Ingesta finalizada")
    return repo_id


def _index_vectors(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
) -> None:
    """Generar y conservar vectores para símbolos/archivos/módulos."""
    chroma = ChromaIndex()
    embedder = EmbeddingClient(
        provider=embedding_provider,
        model=embedding_model,
    )

    symbol_texts = [chunk.snippet for chunk in symbols]
    symbol_embeddings = embedder.embed_texts(symbol_texts)
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
    chroma.upsert(
        collection_name="code_symbols",
        ids=[chunk.id for chunk in symbols],
        documents=symbol_texts,
        embeddings=symbol_embeddings,
        metadatas=symbol_meta,
    )

    file_ids = [f"{repo_id}:{item.path}" for item in scanned_files]
    file_docs = [summarize_file(item) for item in scanned_files]
    file_embeddings = embedder.embed_texts(file_docs)
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
    chroma.upsert(
        collection_name="code_files",
        ids=file_ids,
        documents=file_docs,
        embeddings=file_embeddings,
        metadatas=file_meta,
    )

    module_summaries = summarize_modules(scanned_files)
    module_names = list(module_summaries.keys())
    module_docs = list(module_summaries.values())
    module_embeddings = embedder.embed_texts(module_docs)
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
    chroma.upsert(
        collection_name="code_modules",
        ids=module_ids,
        documents=module_docs,
        embeddings=module_embeddings,
        metadatas=module_meta,
    )


def _index_bm25(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
) -> None:
    """Cree un índice BM25 a partir de símbolos, archivos y resúmenes de módulos."""
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

    GLOBAL_BM25.build(repo_id=repo_id, docs=docs, metadatas=metadatas)
    GLOBAL_BM25.persist_repo(repo_id)


def _index_graph(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
) -> None:
    """Llene el almacén de gráficos Neo4j con relaciones archivo-símbolo."""
    graph = GraphBuilder()
    try:
        graph.upsert_repo_graph(
            repo_id=repo_id,
            scanned_files=scanned_files,
            symbols=symbols,
        )
    finally:
        graph.close()
