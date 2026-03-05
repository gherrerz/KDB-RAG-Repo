"""High-level ingestion pipeline orchestrator."""

from typing import Callable

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.core.settings import get_settings
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.git_client import clone_repository
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex
from coderag.ingestion.repo_scanner import scan_repository
from coderag.ingestion.summarizer import summarize_file, summarize_modules

LoggerFn = Callable[[str], None]


def ingest_repository(
    repo_url: str,
    branch: str,
    commit: str | None,
    logger: LoggerFn,
) -> str:
    """Run full repository ingestion and return repository identifier."""
    settings = get_settings()
    logger("Clonando repositorio...")
    repo_id, repo_path = clone_repository(
        repo_url=repo_url,
        destination_root=settings.workspace_path,
        branch=branch,
        commit=commit,
    )

    logger("Escaneando archivos...")
    scanned_files = scan_repository(repo_path)

    logger("Extrayendo símbolos...")
    symbol_chunks = extract_symbol_chunks(repo_id=repo_id, scanned_files=scanned_files)

    logger("Generando embeddings...")
    _index_vectors(repo_id, scanned_files, symbol_chunks)

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
) -> None:
    """Generate and persist vectors for symbols/files/modules."""
    chroma = ChromaIndex()
    embedder = EmbeddingClient()

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
    """Build BM25 index from symbols, files, and module summaries."""
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


def _index_graph(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
) -> None:
    """Populate Neo4j graph store with file-symbol relationships."""
    graph = GraphBuilder()
    try:
        graph.upsert_repo_graph(
            repo_id=repo_id,
            scanned_files=scanned_files,
            symbols=symbols,
        )
    finally:
        graph.close()
