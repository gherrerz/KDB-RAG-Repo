"""Shared contracts for language-specific symbol extractors."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SymbolDetection:
    """Describes a detected symbol declaration in source code."""

    symbol_name: str
    symbol_type: str
    start_line: int


@dataclass(frozen=True)
class SymbolSpan:
    """Represents the inclusive line span for a symbol definition."""

    start_line: int
    end_line: int


class SymbolExtractor(Protocol):
    """Contract implemented by all language extractors."""

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Return symbols declared in the provided source content."""

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Resolve the inclusive symbol span for a detected declaration."""
