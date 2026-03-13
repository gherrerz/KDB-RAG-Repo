"""Recuperación híbrida que combina similitud de vectores y puntuaciones de BM25."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from coderag.core.models import RetrievalChunk
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex


VECTOR_COLLECTIONS = ["code_symbols", "code_files", "code_modules"]


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


def hybrid_search(repo_id: str, query: str, top_n: int = 50) -> list[RetrievalChunk]:
    """Busque datos de repositorios indexados con vector y fusión BM25."""
    embedder = EmbeddingClient()
    vector_results: list[dict] = []
    if embedder.client is not None:
        chroma = ChromaIndex()
        query_embedding = embedder.embed_texts([query])[0]

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
                    {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]},
                )
                for collection_name in VECTOR_COLLECTIONS
            ]
        except Exception:
            # Fallback secuencial para preservar funcionalidad si el runtime
            # no permite concurrencia segura del cliente Chroma.
            for collection_name in VECTOR_COLLECTIONS:
                result = chroma.query(
                    collection_name=collection_name,
                    query_embedding=query_embedding,
                    top_n=top_n,
                    where={"repo_id": repo_id},
                )
                vector_results.append(result)

    fused: dict[str, RetrievalChunk] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for vector_result in vector_results:
        ids = vector_result.get("ids", [[]])[0]
        docs = vector_result.get("documents", [[]])[0]
        metas = vector_result.get("metadatas", [[]])[0]
        distances = vector_result.get("distances", [[]])[0]

        for item_id, doc, meta, distance in zip(ids, docs, metas, distances):
            score = 1.0 / (1.0 + float(distance))
            if meta.get("language") == "module":
                score *= 1.2
            scores[item_id] += score
            fused[item_id] = RetrievalChunk(
                id=item_id,
                text=doc,
                score=score,
                metadata=meta,
            )

    bm25_results = GLOBAL_BM25.query(repo_id=repo_id, text=query, top_n=top_n)
    for item in bm25_results:
        item_id = str(item["id"])
        bm25_score = float(item["score"])
        scores[item_id] += bm25_score
        fused[item_id] = RetrievalChunk(
            id=item_id,
            text=item["text"],
            score=bm25_score,
            metadata=item["metadata"],
        )

    ranked = sorted(fused.values(), key=lambda item: scores[item.id], reverse=True)
    for chunk in ranked:
        chunk.score = scores[chunk.id]
    return ranked[:top_n]
