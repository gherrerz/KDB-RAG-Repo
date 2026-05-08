"""Python extractor backed by AST node positions."""

import ast

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan


class PythonAstExtractor:
    """Extracts Python classes/functions and their exact spans using AST."""

    class _DetectionVisitor(ast.NodeVisitor):
        """Collect Python symbol detections while tracking class nesting."""

        def __init__(self) -> None:
            """Initialize the mutable detection state."""
            self.class_stack: list[str] = []
            self.detections: list[SymbolDetection] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            """Record a class and recurse into its body."""
            self.detections.append(
                SymbolDetection(
                    symbol_name=node.name,
                    symbol_type="class",
                    start_line=node.lineno,
                )
            )
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            """Record functions and methods with distinct symbol types."""
            self.detections.append(
                SymbolDetection(
                    symbol_name=node.name,
                    symbol_type=("method" if self.class_stack else "function"),
                    start_line=node.lineno,
                )
            )
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            """Record async functions and methods with distinct symbol types."""
            self.detections.append(
                SymbolDetection(
                    symbol_name=node.name,
                    symbol_type=("method" if self.class_stack else "function"),
                    start_line=node.lineno,
                )
            )
            self.generic_visit(node)

    def detect_symbols(
        self,
        content: str,
        path: str | None = None,
    ) -> list[SymbolDetection]:
        """Return Python symbols discovered in the module AST."""
        del path
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return []

        visitor = self._DetectionVisitor()
        visitor.visit(tree)
        detections = visitor.detections

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
