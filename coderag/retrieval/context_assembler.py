"""Context assembly for final LLM prompt input."""

from coderag.core.models import RetrievalChunk


def assemble_context(
    chunks: list[RetrievalChunk],
    graph_records: list[dict],
    max_tokens: int,
) -> str:
    """Build bounded context payload with snippets and graph evidence."""
    sections: list[str] = []
    for chunk in chunks:
        metadata = chunk.metadata
        sections.append(
            "\n".join(
                [
                    f"PATH: {metadata.get('path', 'unknown')}",
                    (
                        "LINES: "
                        f"{metadata.get('start_line', 0)}-"
                        f"{metadata.get('end_line', 0)}"
                    ),
                    f"SCORE: {chunk.score:.4f}",
                    chunk.text,
                ]
            )
        )

    if graph_records:
        sections.append("GRAPH_CONTEXT:")
        for record in graph_records[:50]:
            sections.append(str(record))

    context = "\n\n---\n\n".join(sections)
    token_estimate = len(context) // 4
    if token_estimate <= max_tokens:
        return context

    max_chars = max_tokens * 4
    return context[:max_chars]
