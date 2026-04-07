"""JavaScript/TypeScript extractor using brace balancing."""

import re

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.brace_aware import (
    BraceAwareSpanResolver,
    CommentStyle,
)


_FUNCTION_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
)

_CLASS_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
)

_ARROW_FUNCTION_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?::[^=]+)?=>"
)

_METHOD_PATTERN = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?"
    r"(?:readonly\s+)?(?:static\s+)?(?:async\s+)?"
    r"(?:get\s+|set\s+)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^;]*\)\s*"
    r"(?::\s*[^\{]+)?\s*(?:\{.*)?$"
)

_CONTROL_FLOW_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
}


class JavaScriptBraceExtractor:
    """Extract JS/TS symbols and resolve full declaration spans."""

    def __init__(self) -> None:
        """Initialize JS/TS comment-aware brace resolver."""
        self._resolver = BraceAwareSpanResolver(
            CommentStyle(
                line_comment="//",
                block_start="/*",
                block_end="*/",
            )
        )

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Detect function/class/method declarations in JS/TS source."""
        detections: list[SymbolDetection] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            function_match = _FUNCTION_PATTERN.match(line)
            if function_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=function_match.group(1),
                        symbol_type="function",
                        start_line=line_number,
                    )
                )
                continue

            class_match = _CLASS_PATTERN.match(line)
            if class_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=class_match.group(1),
                        symbol_type="class",
                        start_line=line_number,
                    )
                )
                continue

            arrow_match = _ARROW_FUNCTION_PATTERN.match(line)
            if arrow_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=arrow_match.group(1),
                        symbol_type="function",
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
        """Resolve JS/TS symbol span using balanced braces."""
        lines = content.splitlines()
        return self._resolver.resolve_from_start(
            lines=lines,
            start_line=detection.start_line,
            search_window=12,
        )
