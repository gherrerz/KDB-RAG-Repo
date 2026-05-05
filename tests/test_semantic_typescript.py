"""Tests for TypeScript semantic relation extraction phase 1."""

from coderag.core.models import ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_typescript import extract_typescript_semantic_relations


def test_extract_typescript_semantic_relations_core_types() -> None:
    """Extracts IMPORTS, EXTENDS/IMPLEMENTS and CALLS from TypeScript."""
    content = (
        "import { Base } from './base';\n"
        "import type { Contract } from './contract';\n\n"
        "export class Service extends Base implements Contract {\n"
        "  public run(): void {\n"
        "    helper();\n"
        "  }\n"
        "}\n\n"
        "function helper(): void {}\n"
    )
    scanned_files = [
        ScannedFile(path="src/service.ts", language="typescript", content=content)
    ]
    symbols = extract_symbol_chunks(repo_id="repo-ts", scanned_files=scanned_files)

    relations = extract_typescript_semantic_relations(
        repo_id="repo-ts",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(item.relation_type == "IMPORTS" for item in relations)
    assert any(
        item.relation_type == "EXTENDS" and item.target_ref == "Base"
        for item in relations
    )
    assert any(
        item.relation_type == "IMPLEMENTS" and item.target_ref == "Contract"
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS" and item.target_ref == "helper"
        for item in relations
    )


def test_extract_typescript_semantic_relations_reports_resolution_stats() -> None:
    """Exposes resolution source counts for TypeScript diagnostics."""
    content = (
        "export class Service {\n"
        "  public run(): void {\n"
        "    helper();\n"
        "  }\n"
        "}\n\n"
        "function helper(): void {}\n"
    )
    scanned_files = [
        ScannedFile(path="src/service.ts", language="typescript", content=content)
    ]
    symbols = extract_symbol_chunks(repo_id="repo-ts", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    extract_typescript_semantic_relations(
        repo_id="repo-ts",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    assert stats.get("local", 0) >= 1


def test_extract_typescript_semantic_relations_detects_tsx_component_usage() -> None:
    """Extracts CALLS relations from JSX component usage in TSX files."""
    button_content = (
        "export function Button(): JSX.Element {\n"
        "  return <button>ok</button>;\n"
        "}\n"
    )
    page_content = (
        "import { Button } from './button';\n\n"
        "export default function Page(): JSX.Element {\n"
        "  return <main><Button /></main>;\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="app/button.tsx", language="typescript", content=button_content),
        ScannedFile(path="app/page.tsx", language="typescript", content=page_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-tsx", scanned_files=scanned_files)

    relations = extract_typescript_semantic_relations(
        repo_id="repo-tsx",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    component_calls = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "Button"
    ]

    assert len(component_calls) == 1
    assert component_calls[0].target_symbol_id is not None
    assert not any(
        item.relation_type == "CALLS" and item.target_ref == "Page"
        for item in relations
    )
