"""Generic fallback extractor for unsupported languages."""

import re

from src.coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan


class GenericFallbackExtractor:
    """Fallback extractor that preserves legacy windowed behavior."""

    def __init__(self, window_lines: int = 30) -> None:
        """Create fallback extractor with configurable window size."""
        self._window_lines = max(1, int(window_lines))

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Detect common symbol signatures using basic regex heuristics."""
        detections: list[SymbolDetection] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            py_match = re.match(r"\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            js_match = re.match(r"\s*function\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if py_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=py_match.group(2),
                        symbol_type=("class" if py_match.group(1) == "class" else "function"),
                        start_line=line_number,
                    )
                )
                continue
            if js_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=js_match.group(1),
                        symbol_type="function",
                        start_line=line_number,
                    )
                )

        return detections

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Resolve fallback span using a fixed line window."""
        line_count = len(content.splitlines())
        end_line = min(line_count, detection.start_line + self._window_lines)
        return SymbolSpan(start_line=detection.start_line, end_line=max(detection.start_line, end_line))
