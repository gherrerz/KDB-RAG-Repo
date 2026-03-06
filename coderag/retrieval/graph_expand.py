"""Módulo de expansión GraphRAG que utiliza vecinos Neo4j."""

from coderag.core.models import RetrievalChunk
from coderag.core.settings import get_settings
from coderag.ingestion.graph_builder import GraphBuilder


def expand_with_graph(chunks: list[RetrievalChunk]) -> list[dict]:
    """Amplíe el contexto atravesando los vecinos del gráfico desde los símbolos recuperados."""
    symbol_ids = [item.id for item in chunks]
    if not symbol_ids:
        return []

    settings = get_settings()
    graph = GraphBuilder()
    try:
        return graph.expand_symbols(symbol_ids=symbol_ids, hops=settings.graph_hops)
    except Exception:
        return []
    finally:
        graph.close()
