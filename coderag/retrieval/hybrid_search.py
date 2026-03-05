"""Hybrid retrieval combining vector similarity and BM25 scores."""

from collections import defaultdict

from coderag.core.models import RetrievalChunk
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex


def hybrid_search(repo_id: str, query: str, top_n: int = 50) -> list[RetrievalChunk]:
    """Search indexed repository data with vector and BM25 fusion."""
    embedder = EmbeddingClient()
    vector_results: list[dict] = []
    if embedder.client is not None:
        chroma = ChromaIndex()
        query_embedding = embedder.embed_texts([query])[0]
        for collection_name in ["code_symbols", "code_files", "code_modules"]:
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
