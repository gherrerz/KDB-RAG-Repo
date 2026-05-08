"""Tests for JavaScript semantic relation extraction phase 1."""

import coderag.ingestion.semantic_javascript as semantic_javascript
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


def test_extract_javascript_semantic_relations_resolves_default_import_cross_file() -> None:
    """Resolves default imports to symbols exported by another file."""
    scanned_files = [
        ScannedFile(
            path="src/base.js",
            language="javascript",
            content="export default class Base {}\n",
        ),
        ScannedFile(
            path="src/service.js",
            language="javascript",
            content=(
                "import Base from './base';\n\n"
                "export class Service extends Base {\n"
                "  run() {}\n"
                "}\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)

    relations = extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "src/base.js"
        and item.target_symbol_id is None
        for item in relations
    )
    assert any(
        item.relation_type == "EXTENDS"
        and item.target_ref == "Base"
        and item.target_symbol_id is not None
        for item in relations
    )


def test_extract_javascript_semantic_relations_resolves_named_and_namespace_imports() -> None:
    """Resolves named and namespace imports through export bindings."""
    scanned_files = [
        ScannedFile(
            path="src/lib.js",
            language="javascript",
            content=(
                "export function helper() {}\n"
                "export function format() {}\n"
            ),
        ),
        ScannedFile(
            path="src/service.js",
            language="javascript",
            content=(
                "import { helper as localHelper } from './lib';\n"
                "import * as Lib from './lib';\n\n"
                "export function run() {\n"
                "  localHelper();\n"
                "  Lib.format();\n"
                "}\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    relations = extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "localHelper"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "Lib.format"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert stats.get("import_binding", 0) >= 1
    assert stats.get("namespace_import", 0) >= 1


def test_extract_javascript_semantic_relations_penalizes_global_unique_fallback() -> None:
    """Uses lower confidence when JS falls back to a global unique symbol."""
    scanned_files = [
        ScannedFile(
            path="src/shared.js",
            language="javascript",
            content="export function helper() {}\n",
        ),
        ScannedFile(
            path="src/service.js",
            language="javascript",
            content="export function run() { helper(); }\n",
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    relations = extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    helper_call = next(
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "helper"
    )

    assert helper_call.target_symbol_id is not None
    assert helper_call.confidence <= 0.55
    assert stats.get("global_unique", 0) >= 1


def test_extract_javascript_semantic_relations_resolves_tsconfig_alias_when_enabled(
    monkeypatch,
) -> None:
    """Resolves JS imports via tsconfig path aliases when the flag is enabled."""
    scanned_files = [
        ScannedFile(
            path="web/tsconfig.json",
            language="json",
            content=(
                '{"compilerOptions":{"baseUrl":"src","paths":{"@ui/*":["components/*"]}}}'
            ),
        ),
        ScannedFile(
            path="web/components/button.js",
            language="javascript",
            content="export function Button() {}\n",
        ),
        ScannedFile(
            path="web/src/page.js",
            language="javascript",
            content=(
                "import { Button } from '@ui/button';\n"
                "export function Page() {\n"
                "  Button();\n"
                "}\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-js", scanned_files=scanned_files)

    class _Settings:
        semantic_tsconfig_resolution_enabled = True

    monkeypatch.setattr(semantic_javascript, "get_settings", lambda: _Settings())

    relations = extract_javascript_semantic_relations(
        repo_id="repo-js",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "Button"
        and item.target_symbol_id is not None
        for item in relations
    )