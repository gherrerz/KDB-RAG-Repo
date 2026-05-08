"""Tests para prompts de respuesta y verificación LLM."""

from coderag.llm.prompts import SYSTEM_PROMPT, build_answer_prompt


def test_system_prompt_guides_import_queries_with_evidence_priority() -> None:
    """El prompt del sistema debe priorizar imports y distinguir mención vs import directo."""
    assert "prioriza evidencia explícita de imports" in SYSTEM_PROMPT
    assert "distingue entre import directo" in SYSTEM_PROMPT


def test_build_answer_prompt_mentions_graph_dependency_blocks_for_import_questions() -> None:
    """El prompt de respuesta debe instruir cómo usar file-context derivado del grafo."""
    prompt = build_answer_prompt(
        query="where is neo4j imported",
        context=(
            "GRAPH_FILE_DEPENDENCY\nPATH: src/coderag/core/storage_health.py\n\n"
            "GRAPH_EXTERNAL_DEPENDENCY\nREF: neo4j\nSOURCE_PATH: src/coderag/ingestion/semantic_python.py"
        ),
    )

    assert "GRAPH_FILE_DEPENDENCY" in prompt
    assert "GRAPH_EXTERNAL_DEPENDENCY" in prompt
    assert "No confundas una mención textual del término con un import directo" in prompt
    assert "Si solo hay dependencias relacionadas o fuentes externas derivadas del grafo" in prompt