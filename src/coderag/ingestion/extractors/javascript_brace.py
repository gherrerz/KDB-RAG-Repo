"""JavaScript/TypeScript extractor using brace balancing."""

from pathlib import PurePosixPath
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

_ANONYMOUS_DEFAULT_FUNCTION_PATTERN = re.compile(
    r"^\s*export\s+default\s+(?:async\s+)?function\s*\("
)

_CLASS_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
)

_INTERFACE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)

_ANONYMOUS_DEFAULT_CLASS_PATTERN = re.compile(
    r"^\s*export\s+default\s+class\s*(?:extends\s+[A-Za-z_$][A-Za-z0-9_$.]*)?\s*\{?"
)

_ARROW_FUNCTION_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?::[^=]+)?=>"
)

_ANONYMOUS_DEFAULT_ARROW_PATTERN = re.compile(
    r"^\s*export\s+default\s+(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
)

_STYLED_COMPONENT_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*styled(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\s*\()"
)

_DEFAULT_HOC_PATTERN = re.compile(
    r"^\s*export\s+default\s+(?:memo|withRouter)\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)"
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

    def detect_symbols(
        self,
        content: str,
        path: str | None = None,
    ) -> list[SymbolDetection]:
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

            if _ANONYMOUS_DEFAULT_FUNCTION_PATTERN.match(line):
                detections.append(
                    SymbolDetection(
                        symbol_name=_default_symbol_name(path),
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

            interface_match = _INTERFACE_PATTERN.match(line)
            if interface_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=interface_match.group(1),
                        symbol_type="interface",
                        start_line=line_number,
                    )
                )
                continue

            if _ANONYMOUS_DEFAULT_CLASS_PATTERN.match(line):
                detections.append(
                    SymbolDetection(
                        symbol_name=_default_symbol_name(path),
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

            if _ANONYMOUS_DEFAULT_ARROW_PATTERN.match(line):
                detections.append(
                    SymbolDetection(
                        symbol_name=_default_symbol_name(path),
                        symbol_type="function",
                        start_line=line_number,
                    )
                )
                continue

            styled_match = _STYLED_COMPONENT_PATTERN.match(line)
            if styled_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=styled_match.group(1),
                        symbol_type="function",
                        start_line=line_number,
                    )
                )
                continue

            hoc_match = _DEFAULT_HOC_PATTERN.match(line)
            if hoc_match:
                detections.append(
                    SymbolDetection(
                        symbol_name=hoc_match.group(1),
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


def _default_symbol_name(path: str | None) -> str:
    """Derive a stable symbol name for anonymous default exports."""
    if not path:
        return "DefaultExport"

    filename = PurePosixPath(path).name
    stem = PurePosixPath(path).stem
    normalized = stem.lower()
    special_names = {
        "page": "Page",
        "layout": "Layout",
        "template": "Template",
        "loading": "Loading",
        "error": "Error",
        "not-found": "NotFound",
        "middleware": "Middleware",
        "_app": "_App",
        "_document": "_Document",
    }
    if normalized in special_names:
        return special_names[normalized]
    words = re.split(r"[^A-Za-z0-9]+", filename.rsplit(".", maxsplit=1)[0])
    parts = [word[:1].upper() + word[1:] for word in words if word]
    return "".join(parts) or "DefaultExport"
