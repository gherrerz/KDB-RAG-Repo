"""Tests for Python semantic relation extraction."""

from src.coderag.core.models import ScannedFile
from src.coderag.ingestion.chunker import extract_symbol_chunks
from src.coderag.ingestion.semantic_python import extract_python_semantic_relations


def test_extract_python_semantic_relations_intrafile_resolution() -> None:
    """Extracts CALLS/EXTENDS/IMPORTS and resolves intra-file targets."""
    content = (
        "class Base:\n"
        "    pass\n\n"
        "class Child(Base):\n"
        "    pass\n\n"
        "def helper():\n"
        "    return 1\n\n"
        "def use_helper():\n"
        "    import json\n"
        "    helper()\n"
    )
    scanned_files = [
        ScannedFile(
            path="pkg/mod.py",
            language="python",
            content=content,
        )
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "EXTENDS"
        and item.target_ref == "Base"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helper"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "json"
        and item.target_symbol_id is None
        for item in relations
    )


def test_extract_python_semantic_relations_ignores_syntax_errors() -> None:
    """Skips malformed Python files without failing extraction."""
    scanned_files = [
        ScannedFile(
            path="pkg/broken.py",
            language="python",
            content="def broken(:\n    pass\n",
        )
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert relations == []
