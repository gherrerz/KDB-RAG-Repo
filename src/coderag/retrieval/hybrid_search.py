"""Recuperación híbrida que combina similitud de vectores y puntuaciones de BM25."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import logging
import unicodedata

from src.coderag.core.models import RetrievalChunk
from src.coderag.ingestion.embedding import EmbeddingClient
from src.coderag.ingestion.index_bm25 import GLOBAL_BM25
from src.coderag.ingestion.index_chroma import ChromaIndex


VECTOR_COLLECTIONS = ["code_symbols", "code_files", "code_modules"]
LOGGER = logging.getLogger(__name__)
VECTOR_WEIGHT = 0.55
BM25_WEIGHT = 0.45


def _normalize_query(query: str) -> str:
    """Normaliza consultas para reducir ruido ortográfico y de espacios."""
    lowered = query.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_marks.split())


def _empty_result() -> dict:
    """Devuelve un resultado vacío con shape compatible de Chroma."""
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def _query_collection(
    chroma: ChromaIndex,
    collection_name: str,
    query_embedding: list[float],
    repo_id: str,
    top_n: int,
) -> tuple[str, dict]:
    """Consulta una colección de Chroma y devuelve su nombre junto al resultado."""
    result = chroma.query(
        collection_name=collection_name,
        query_embedding=query_embedding,
        top_n=top_n,
        where={"repo_id": repo_id},
    )
    return collection_name, result


def hybrid_search(
    repo_id: str,
    query: str,
    top_n: int = 50,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
) -> list[RetrievalChunk]:
    """Busque datos de repositorios indexados con vector y fusión BM25."""
    embedder = EmbeddingClient(
        provider=embedding_provider,
        model=embedding_model,
    )
    normalized_query = _normalize_query(query)
    vector_results: list[dict] = []
    query_embedding: list[float] | None = None
    try:
        embeddings = embedder.embed_texts([normalized_query])
        if embeddings:
            query_embedding = embeddings[0]
    except Exception as exc:
        LOGGER.warning(
            "No se pudo generar embedding de consulta para repo=%s: %s",
            repo_id,
            exc,
        )

    if query_embedding is not None:
        chroma = ChromaIndex()

        try:
            with ThreadPoolExecutor(max_workers=len(VECTOR_COLLECTIONS)) as executor:
                futures = [
                    executor.submit(
                        _query_collection,
                        chroma,
                        collection_name,
                        query_embedding,
                        repo_id,
                        top_n,
                    )
                    for collection_name in VECTOR_COLLECTIONS
                ]
                results_by_collection = {
                    collection_name: result
                    for collection_name, result in [
                        future.result() for future in futures
                    ]
                }
            vector_results = [
                results_by_collection.get(
                    collection_name,
                    _empty_result(),
                )
                for collection_name in VECTOR_COLLECTIONS
            ]
        except Exception as exc:
            LOGGER.warning(
                "Fallo recuperación vectorial concurrente para repo=%s; "
                "usando fallback secuencial. error=%s",
                repo_id,
                exc,
            )
            # Fallback secuencial para preservar funcionalidad si el runtime
            # no permite concurrencia segura del cliente Chroma.
            for collection_name in VECTOR_COLLECTIONS:
                try:
                    result = chroma.query(
                        collection_name=collection_name,
                        query_embedding=query_embedding,
                        top_n=top_n,
                        where={"repo_id": repo_id},
                    )
                    vector_results.append(result)
                except Exception as inner_exc:
                    LOGGER.warning(
                        "Fallo recuperación vectorial en colección=%s repo=%s: %s",
                        collection_name,
                        repo_id,
                        inner_exc,
                    )
                    vector_results.append(_empty_result())
    else:
        LOGGER.warning(
            "Consulta sin embedding utilizable para repo=%s; "
            "se priorizará BM25.",
            repo_id,
        )

    fused: dict[str, RetrievalChunk] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for vector_result in vector_results:
        ids = vector_result.get("ids", [[]])[0]
        docs = vector_result.get("documents", [[]])[0]
        metas = vector_result.get("metadatas", [[]])[0]
        distances = vector_result.get("distances", [[]])[0]

        for item_id, doc, meta, distance in zip(ids, docs, metas, distances):
            score = 1.0 / (1.0 + float(distance))
            weighted_score = score * VECTOR_WEIGHT
            scores[item_id] += weighted_score
            fused[item_id] = RetrievalChunk(
                id=item_id,
                text=doc,
                score=weighted_score,
                metadata=meta,
            )

    GLOBAL_BM25.ensure_repo_loaded(repo_id)
    bm25_results = GLOBAL_BM25.query(repo_id=repo_id, text=normalized_query, top_n=top_n)
    max_bm25_score = max((float(item["score"]) for item in bm25_results), default=0.0)
    for item in bm25_results:
        item_id = str(item["id"])
        bm25_score = float(item["score"])
        normalized_bm25 = 0.0
        if max_bm25_score > 0:
            normalized_bm25 = bm25_score / max_bm25_score
        weighted_bm25 = normalized_bm25 * BM25_WEIGHT
        scores[item_id] += weighted_bm25
        fused[item_id] = RetrievalChunk(
            id=item_id,
            text=item["text"],
            score=weighted_bm25,
            metadata=item["metadata"],
        )

    ranked = sorted(fused.values(), key=lambda item: scores[item.id], reverse=True)
    for chunk in ranked:
        chunk.score = scores[chunk.id]
    return ranked[:top_n]
