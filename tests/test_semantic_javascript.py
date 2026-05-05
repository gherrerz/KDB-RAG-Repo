"""Tests for JavaScript semantic relation extraction phase 1."""

from coderag.core.models import ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_javascript import extract_javascript_semantic_relations


def test_extract_javascript_semantic_relations_core_types() -> None:
    """Extracts IMPORTS, EXTENDS and CALLS from JavaScript."""
    content = (
        "import Base from './base';\n\n"
        "export class Service extends Base {\n"
        "  run() {\n"
        "    helper();\n"
        "  }\n"
        "}\n\n"
        "function helper() {}\n"
    )
    scanned_files = [
        ScannedFile(path="src/service.js", language="javascript", content=content)
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)

    relations = extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(item.relation_type == "IMPORTS" for item in relations)
    assert any(
        item.relation_type == "EXTENDS" and item.target_ref == "Base"
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS" and item.target_ref == "helper"
        for item in relations
    )


def test_extract_javascript_semantic_relations_reports_resolution_stats() -> None:
    """Exposes resolution source counts for JavaScript diagnostics."""
    content = (
        "export class Service {\n"
        "  run() {\n"
        "    helper();\n"
        "  }\n"
        "}\n\n"
        "function helper() {}\n"
    )
    scanned_files = [
        ScannedFile(path="src/service.js", language="javascript", content=content)
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    assert stats.get("local", 0) >= 1