"""Smoke tests for the shared Tree-sitter runtime helpers."""

from coderag.ingestion.extractors.treesitter_runtime import (
    is_language_available,
    parse_source,
)


def test_runtime_reports_kotlin_and_swift_as_available() -> None:
    """The runtime should expose the grammars wired into Fase 1."""
    assert is_language_available("kotlin") is True
    assert is_language_available("swift") is True


def test_runtime_parses_kotlin_and_swift_samples() -> None:
    """The runtime should parse minimal Kotlin and Swift samples."""
    kotlin_tree = parse_source("kotlin", "class Demo { fun run() {} }")
    swift_tree = parse_source("swift", "class Demo { func run() {} }")

    assert kotlin_tree.root_node.type == "source_file"
    assert swift_tree.root_node.type == "source_file"