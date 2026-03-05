"""Reranking strategy for retrieval candidates."""

from coderag.core.models import RetrievalChunk


def rerank(chunks: list[RetrievalChunk], top_k: int = 10) -> list[RetrievalChunk]:
    """Apply deterministic reranking and keep strongest items."""
    ranked = sorted(chunks, key=lambda item: item.score, reverse=True)
    return ranked[:top_k]
