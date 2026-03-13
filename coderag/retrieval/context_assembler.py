"""Ensamblado de contexto para la entrada del mensaje final de LLM."""

from coderag.core.models import RetrievalChunk


SECTION_SEPARATOR = "\n\n---\n\n"


def assemble_context(
    chunks: list[RetrievalChunk],
    graph_records: list[dict],
    max_tokens: int,
) -> str:
    """Cree una carga útil de contexto limitada con fragmentos y evidencia gráfica."""
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

    context = SECTION_SEPARATOR.join(sections)
    max_chars = max(0, int(max_tokens) * 4)
    if len(context) <= max_chars:
        return context

    if max_chars == 0:
        return ""

    kept_sections: list[str] = []
    current_length = 0
    for section in sections:
        section_length = len(section)
        projected = section_length
        if kept_sections:
            projected += len(SECTION_SEPARATOR)

        if current_length + projected > max_chars:
            break

        kept_sections.append(section)
        current_length += projected

    if kept_sections:
        return SECTION_SEPARATOR.join(kept_sections)

    # Si el primer bloque excede el presupuesto, mantén solo su prefijo.
    return sections[0][:max_chars]
