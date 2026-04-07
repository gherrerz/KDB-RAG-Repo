"""Estrategia de reclasificación para candidatos de recuperación."""

from coderag.core.models import RetrievalChunk


def rerank(chunks: list[RetrievalChunk], top_k: int = 10) -> list[RetrievalChunk]:
    """Aplique una reclasificación determinista y mantenga los elementos más fuertes."""
    ranked = sorted(chunks, key=lambda item: item.score, reverse=True)
    return ranked[:top_k]
