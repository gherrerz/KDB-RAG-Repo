"""Tests for Kotlin semantic relation extraction phase 1."""

from coderag.core.models import ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_kotlin import extract_kotlin_semantic_relations


def test_extract_kotlin_semantic_relations_core_types() -> None:
    """Extract IMPORTS, EXTENDS, IMPLEMENTS and CALLS for Kotlin basics."""
    content = (
        "package demo\n\n"
        "import demo.base.Base\n"
        "import demo.api.Service\n\n"
        "interface Service\n\n"
        "open class Base {\n"
        "    fun helper() {}\n"
        "}\n\n"
        "class Impl: Base(), Service {\n"
        "    fun run() {\n"
        "        helper()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [ScannedFile(path="src/Impl.kt", language="kotlin", content=content)]
    symbols = extract_symbol_chunks(repo_id="repo-kotlin", scanned_files=scanned_files)

    relations = extract_kotlin_semantic_relations(
        repo_id="repo-kotlin",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(item.relation_type == "IMPORTS" for item in relations)
    assert any(
        item.relation_type == "EXTENDS" and item.target_ref == "Base"
        for item in relations
    )
    assert any(
        item.relation_type == "IMPLEMENTS" and item.target_ref == "Service"
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS" and item.target_ref == "helper"
        for item in relations
    )


def test_extract_kotlin_semantic_relations_resolves_cross_file_targets() -> None:
    """Resolve Kotlin imports, supertypes and calls across repository files."""
    api_content = "package com.acme.api\n\ninterface Service\n"
    base_content = (
        "package com.acme.impl\n\n"
        "open class Base {\n"
        "    fun helper() {}\n"
        "}\n"
    )
    impl_content = (
        "package com.acme.impl\n\n"
        "import com.acme.api.Service\n\n"
        "class Impl: Base(), Service {\n"
        "    fun run() {\n"
        "        helper()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/com/acme/api/Service.kt",
            language="kotlin",
            content=api_content,
        ),
        ScannedFile(
            path="src/com/acme/impl/Base.kt",
            language="kotlin",
            content=base_content,
        ),
        ScannedFile(
            path="src/com/acme/impl/Impl.kt",
            language="kotlin",
            content=impl_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-kotlin", scanned_files=scanned_files)

    service_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Service.kt") and item.symbol_name == "Service"
    )
    base_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Base.kt") and item.symbol_name == "Base"
    )
    helper_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Base.kt") and item.symbol_name == "helper"
    )

    relations = extract_kotlin_semantic_relations(
        repo_id="repo-kotlin",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "com.acme.api.Service"
        and item.target_symbol_id == service_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "EXTENDS"
        and item.target_ref == "Base"
        and item.target_symbol_id == base_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "IMPLEMENTS"
        and item.target_ref == "Service"
        and item.target_symbol_id == service_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helper"
        and item.target_symbol_id == helper_symbol_id
        for item in relations
    )