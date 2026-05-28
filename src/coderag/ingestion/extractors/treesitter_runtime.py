"""Shared helpers for Tree-sitter-backed language extractors."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable


class TreeSitterUnavailableError(RuntimeError):
    """Raised when a requested Tree-sitter runtime or grammar is unavailable."""


TreeSitterLanguage = Any
TreeSitterParser = Any
TreeSitterTree = Any

_TREE_SITTER_IMPORT_ERROR: Exception | None = None

try:
    from tree_sitter import Language as TreeSitterLanguage
    from tree_sitter import Parser as TreeSitterParser
    from tree_sitter import Tree as TreeSitterTree
except ImportError as exc:  # pragma: no cover - exercised in lean envs.
    _TREE_SITTER_IMPORT_ERROR = exc

try:
    import tree_sitter_kotlin
except ImportError:  # pragma: no cover - exercised in lean envs.
    tree_sitter_kotlin = None

try:
    import tree_sitter_swift
except ImportError:  # pragma: no cover - exercised in lean envs.
    tree_sitter_swift = None


_LANGUAGE_FACTORIES: dict[str, Callable[[], object]] = {}
if tree_sitter_kotlin is not None:
    _LANGUAGE_FACTORIES["kotlin"] = tree_sitter_kotlin.language
if tree_sitter_swift is not None:
    _LANGUAGE_FACTORIES["swift"] = tree_sitter_swift.language


def _normalize_language(language: str) -> str:
    """Normalize a language token for Tree-sitter lookups."""
    return language.strip().lower()


def is_language_available(language: str) -> bool:
    """Report whether a Tree-sitter grammar is available for a language."""
    return _normalize_language(language) in _LANGUAGE_FACTORIES


@lru_cache(maxsize=None)
def get_language(language: str) -> TreeSitterLanguage:
    """Return a cached Tree-sitter language object for a language key."""
    normalized = _normalize_language(language)
    if _TREE_SITTER_IMPORT_ERROR is not None:
        raise TreeSitterUnavailableError(
            "Tree-sitter Python runtime is not installed."
        ) from _TREE_SITTER_IMPORT_ERROR

    factory = _LANGUAGE_FACTORIES.get(normalized)
    if factory is None:
        raise TreeSitterUnavailableError(
            f"Tree-sitter grammar is not available for '{normalized}'."
        )

    return TreeSitterLanguage(factory())


def build_parser(language: str) -> TreeSitterParser:
    """Build a parser instance for the requested language."""
    return TreeSitterParser(get_language(language))


def parse_source(language: str, content: str) -> TreeSitterTree:
    """Parse UTF-8 source content with the requested Tree-sitter grammar."""
    return build_parser(language).parse(content.encode("utf-8"))


def node_line_range(node: Any) -> tuple[int, int]:
    """Return an inclusive 1-indexed line range for a Tree-sitter node."""
    start_line = int(node.start_point[0]) + 1
    end_line = max(start_line, int(node.end_point[0]) + 1)
    return start_line, end_line