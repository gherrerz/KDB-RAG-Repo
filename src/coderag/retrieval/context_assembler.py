"""Ensamblado de contexto para la entrada del mensaje final de LLM."""

from coderag.core.models import RetrievalChunk


SECTION_SEPARATOR = "\n\n---\n\n"


def _format_graph_record(record: dict) -> str:
    """Convierte un nodo expandido del grafo en un bloque legible."""
    labels = [str(label) for label in (record.get("labels") or [])]
    props = record.get("props") or {}
    relation_types = [
        str(item) for item in (record.get("relation_types") or []) if str(item)
    ]

    header = "GRAPH_NODE"
    if "File" in labels:
        header = "GRAPH_FILE_DEPENDENCY"
    elif "ExternalSymbol" in labels:
        header = "GRAPH_EXTERNAL_DEPENDENCY"
    elif labels:
        header = f"GRAPH_{'_'.join(label.upper() for label in labels)}"

    lines = [header]
    seed = str(record.get("seed", "") or "").strip()
    if seed:
        lines.append(f"SEED: {seed}")

    if "File" in labels:
        path = str(props.get("path", "unknown") or "unknown")
        language = str(props.get("language", "") or "").strip()
        module_path = str(props.get("module_path", "") or "").strip()
        lines.append(f"PATH: {path}")
        if module_path:
            lines.append(f"MODULE: {module_path}")
        if language:
            lines.append(f"LANGUAGE: {language}")
    elif "ExternalSymbol" in labels:
        ref = str(props.get("ref", "unknown") or "unknown")
        language = str(props.get("language", "") or "").strip()
        source_path = str(
            record.get("source_path") or props.get("source_path") or ""
        ).strip()
        lines.append(f"REF: {ref}")
        if source_path:
            lines.append(f"SOURCE_PATH: {source_path}")
        if language:
            lines.append(f"LANGUAGE: {language}")
    else:
        identifier = str(
            props.get("id") or props.get("name") or props.get("path") or "unknown"
        )
        lines.append(f"ID: {identifier}")

    if relation_types:
        lines.append(f"RELATION_TYPES: {', '.join(relation_types)}")

    edge_count = int(record.get("edge_count", 1) or 1)
    lines.append(f"EDGE_COUNT: {edge_count}")
    return "\n".join(lines)


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
            sections.append(_format_graph_record(record))

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
