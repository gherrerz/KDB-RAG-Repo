"""Tree-sitter-backed symbol extractor for Kotlin Fase 1 declarations."""

from __future__ import annotations

from typing import Any

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.treesitter_runtime import (
    TreeSitterUnavailableError,
    node_line_range,
    parse_source,
)


class KotlinTreeSitterExtractor:
    """Extract Kotlin symbols supported in the current Fase 1 scope."""

    def detect_symbols(
        self,
        content: str,
        path: str | None = None,
    ) -> list[SymbolDetection]:
        """Return Kotlin classes, interfaces, enums, methods, and functions."""
        del path
        try:
            tree = parse_source("kotlin", content)
        except TreeSitterUnavailableError:
            return []

        detections: list[SymbolDetection] = []
        for node in tree.root_node.named_children:
            if node.type == "class_declaration":
                detections.extend(self._detect_class_symbols(node))
                continue
            if node.type == "function_declaration":
                function_name = _identifier_text(node)
                if function_name is None:
                    continue
                start_line, _end_line = node_line_range(node)
                detections.append(
                    SymbolDetection(
                        symbol_name=function_name,
                        symbol_type="function",
                        start_line=start_line,
                    )
                )

        detections.sort(key=lambda item: (item.start_line, item.symbol_name))
        return detections

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Resolve the line span for a previously detected Kotlin symbol."""
        try:
            tree = parse_source("kotlin", content)
        except TreeSitterUnavailableError:
            return SymbolSpan(
                start_line=detection.start_line,
                end_line=detection.start_line,
            )

        for node in _iter_kotlin_symbol_nodes(tree.root_node):
            start_line, end_line = node_line_range(node)
            if start_line != detection.start_line:
                continue
            if _symbol_type_for_node(node) != detection.symbol_type:
                continue
            if _symbol_name_for_node(node) != detection.symbol_name:
                continue
            return SymbolSpan(start_line=start_line, end_line=end_line)

        return SymbolSpan(
            start_line=detection.start_line,
            end_line=detection.start_line,
        )

    def _detect_class_symbols(self, node: Any) -> list[SymbolDetection]:
        """Collect Fase 1 symbols from a Kotlin class-like declaration."""
        class_name = _identifier_text(node)
        if class_name is None:
            return []

        detections: list[SymbolDetection] = []
        start_line, _end_line = node_line_range(node)
        detections.append(
            SymbolDetection(
                symbol_name=class_name,
                symbol_type=_symbol_type_for_node(node),
                start_line=start_line,
            )
        )

        body_node = _class_body_node(node)
        if body_node is None:
            return detections

        for child in body_node.named_children:
            child_start, _child_end = node_line_range(child)
            if child.type == "secondary_constructor":
                detections.append(
                    SymbolDetection(
                        symbol_name=class_name,
                        symbol_type="constructor",
                        start_line=child_start,
                    )
                )
                continue
            if child.type != "function_declaration":
                continue

            method_name = _identifier_text(child)
            if method_name is None:
                continue
            detections.append(
                SymbolDetection(
                    symbol_name=method_name,
                    symbol_type="method",
                    start_line=child_start,
                )
            )

        return detections


def _class_body_node(node: Any) -> Any | None:
    """Return the Kotlin body node for a class-like declaration, if present."""
    for child in node.named_children:
        if child.type in {"class_body", "enum_class_body"}:
            return child
    return None


def _identifier_text(node: Any) -> str | None:
    """Return the first identifier text found among named children."""
    for child in node.named_children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _symbol_name_for_node(node: Any) -> str | None:
    """Resolve the symbol name represented by a Kotlin declaration node."""
    if node.type == "secondary_constructor":
        return node.parent.parent.named_children[0].text.decode("utf-8")
    return _identifier_text(node)


def _symbol_type_for_node(node: Any) -> str:
    """Map a Kotlin declaration node to the public symbol type contract."""
    if node.type == "function_declaration":
        if node.parent is not None and node.parent.type in {
            "class_body",
            "enum_class_body",
        }:
            return "method"
        return "function"
    if node.type == "secondary_constructor":
        return "constructor"

    node_text = node.text.decode("utf-8")
    if node_text.startswith("interface "):
        return "interface"
    modifiers = _modifier_tokens(node)
    if "enum" in modifiers:
        return "enum"
    return "class"


def _modifier_tokens(node: Any) -> set[str]:
    """Return normalized modifier tokens from a Kotlin declaration node."""
    tokens: set[str] = set()
    for child in node.named_children:
        if child.type != "modifiers":
            continue
        for raw_token in child.text.decode("utf-8").split():
            cleaned = raw_token.strip()
            if cleaned:
                tokens.add(cleaned)
    return tokens


def _iter_kotlin_symbol_nodes(root: Any) -> list[Any]:
    """Return Kotlin AST nodes that correspond to supported symbol types."""
    nodes: list[Any] = []
    for child in root.named_children:
        if child.type == "class_declaration":
            nodes.append(child)
            body_node = _class_body_node(child)
            if body_node is None:
                continue
            for body_child in body_node.named_children:
                if body_child.type in {"secondary_constructor", "function_declaration"}:
                    nodes.append(body_child)
            continue
        if child.type == "function_declaration":
            nodes.append(child)
    return nodes