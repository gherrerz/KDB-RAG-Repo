"""Java extractor based on declaration patterns + brace balancing."""

import re

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.brace_aware import (
    BraceAwareSpanResolver,
    CommentStyle,
)


_CLASS_PATTERN = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?"
    r"(?:abstract\s+|final\s+)?(class|interface|enum|record)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)

_METHOD_PATTERN = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_<>\[\]?]*\s+)+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:throws\s+[^{]+)?"
    r"(?:\{.*)?$"
)

_CONSTRUCTOR_PATTERN = re.compile(
    r"^\s*(?:public|private|protected)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:throws\s+[^{]+)?"
    r"(?:\{.*)?$"
)

_CONTROL_FLOW_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "throw",
    "synchronized",
}


class JavaBraceExtractor:
    """Extract Java symbols and resolve spans via balanced braces."""

    def __init__(self) -> None:
        """Initialize Java comment/braces resolver."""
        self._resolver = BraceAwareSpanResolver(
            CommentStyle(
                line_comment="//",
                block_start="/*",
                block_end="*/",
            )
        )

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Detect Java classes, interfaces, methods and constructors."""
        detections: list[SymbolDetection] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            class_match = _CLASS_PATTERN.match(line)
            if class_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=class_match.group(2),
                        symbol_type=class_match.group(1),
                        start_line=line_number,
                    )
                )
                continue

            constructor_match = _CONSTRUCTOR_PATTERN.match(line)
            if constructor_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=constructor_match.group(1),
                        symbol_type="constructor",
                        start_line=line_number,
                    )
                )
                continue

            method_match = _METHOD_PATTERN.match(line)
            if not method_match:
                continue

            method_name = method_match.group(1)
            if method_name.lower() in _CONTROL_FLOW_NAMES:
                continue
            detections.append(
                SymbolDetection(
                    symbol_name=method_name,
                    symbol_type="method",
                    start_line=line_number,
                )
            )

        return detections

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Resolve Java declaration span with brace-aware scanning."""
        lines = content.splitlines()
        return self._resolver.resolve_from_start(
            lines=lines,
            start_line=detection.start_line,
            search_window=12,
        )
