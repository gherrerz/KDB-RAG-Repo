"""Python extractor backed by AST node positions."""

import ast

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan


class PythonAstExtractor:
    """Extracts Python classes/functions and their exact spans using AST."""

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Return Python symbols discovered in the module AST."""
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return []

        detections: list[SymbolDetection] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                detections.append(
                    SymbolDetection(
                        symbol_name=node.name,
                        symbol_type="class",
                        start_line=node.lineno,
                    )
                )
                continue
            if isinstance(node, ast.AsyncFunctionDef):
                detections.append(
                    SymbolDetection(
                        symbol_name=node.name,
                        symbol_type="function",
                        start_line=node.lineno,
                    )
                )
                continue
            if isinstance(node, ast.FunctionDef):
                detections.append(
                    SymbolDetection(
                        symbol_name=node.name,
                        symbol_type="function",
                        start_line=node.lineno,
                    )
                )

        detections.sort(key=lambda item: (item.start_line, item.symbol_name))
        return detections

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Resolve exact span using AST node line metadata."""
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return SymbolSpan(
                start_line=detection.start_line,
                end_line=detection.start_line,
            )

        best_start = detection.start_line
        best_end = detection.start_line
        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            if getattr(node, "lineno", None) != detection.start_line:
                continue
            if getattr(node, "name", "") != detection.symbol_name:
                continue
            end_line = getattr(node, "end_lineno", None) or detection.start_line
            best_end = max(best_end, int(end_line))

        return SymbolSpan(start_line=best_start, end_line=best_end)
