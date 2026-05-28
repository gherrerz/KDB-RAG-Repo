"""Tree-sitter-backed symbol extractor for Swift Fase 1 declarations."""

from __future__ import annotations

from typing import Any

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.treesitter_runtime import (
    TreeSitterUnavailableError,
    node_line_range,
    parse_source,
)


class SwiftTreeSitterExtractor:
    """Extract Swift symbols supported in the current Fase 1 scope."""

    def detect_symbols(
        self,
        content: str,
        path: str | None = None,
    ) -> list[SymbolDetection]:
        """Return Swift class-like declarations and supported callables."""
        del path
        try:
            tree = parse_source("swift", content)
        except TreeSitterUnavailableError:
            return []

        detections: list[SymbolDetection] = []
        for node in tree.root_node.named_children:
            if node.type in {"class_declaration", "protocol_declaration"}:
                detections.extend(self._detect_type_symbols(node))
                continue
            if node.type == "function_declaration":
                function_name = _symbol_name_for_node(node)
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
        """Resolve the line span for a previously detected Swift symbol."""
        try:
            tree = parse_source("swift", content)
        except TreeSitterUnavailableError:
            return SymbolSpan(
                start_line=detection.start_line,
                end_line=detection.start_line,
            )

        for node in _iter_swift_symbol_nodes(tree.root_node):
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

    def _detect_type_symbols(self, node: Any) -> list[SymbolDetection]:
        """Collect Fase 1 symbols from Swift type and protocol declarations."""
        type_name = _symbol_name_for_node(node)
        if type_name is None:
            return []

        detections: list[SymbolDetection] = []
        start_line, _end_line = node_line_range(node)
        detections.append(
            SymbolDetection(
                symbol_name=type_name,
                symbol_type=_symbol_type_for_node(node),
                start_line=start_line,
            )
        )

        body_node = _body_node(node)
        if body_node is None:
            return detections

        for child in body_node.named_children:
            child_start, _child_end = node_line_range(child)
            if child.type == "init_declaration":
                detections.append(
                    SymbolDetection(
                        symbol_name=type_name,
                        symbol_type="constructor",
                        start_line=child_start,
                    )
                )
                continue
            if child.type not in {
                "function_declaration",
                "protocol_function_declaration",
            }:
                continue

            method_name = _symbol_name_for_node(child)
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


def _body_node(node: Any) -> Any | None:
    """Return the body node for a Swift type declaration, if present."""
    for child in node.named_children:
        if child.type in {"class_body", "enum_class_body", "protocol_body"}:
            return child
    return None


def _symbol_name_for_node(node: Any) -> str | None:
    """Resolve the public symbol name represented by a Swift node."""
    if node.type == "init_declaration":
        parent = node.parent
        if parent is None or parent.parent is None:
            return None
        return _type_identifier_text(parent.parent)
    if node.type in {"function_declaration", "protocol_function_declaration"}:
        return _function_identifier_text(node)
    return _declaration_type_name(node)


def _type_identifier_text(node: Any) -> str | None:
    """Return a Swift type identifier from a declaration node."""
    for child in node.named_children:
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
    return None


def _declaration_type_name(node: Any) -> str | None:
    """Return the declared type name from Swift type-like declarations."""
    type_name = _type_identifier_text(node)
    if type_name is not None:
        return type_name

    for child in node.named_children:
        if child.type == "user_type":
            nested_type = _type_identifier_text(child)
            if nested_type is not None:
                return nested_type
    return None


def _function_identifier_text(node: Any) -> str | None:
    """Return a Swift function identifier from a declaration node."""
    for child in node.named_children:
        if child.type == "simple_identifier":
            return child.text.decode("utf-8")
    return None


def _symbol_type_for_node(node: Any) -> str:
    """Map a Swift declaration node to the public symbol type contract."""
    if node.type == "protocol_declaration":
        return "protocol"
    if node.type == "init_declaration":
        return "constructor"
    if node.type in {"function_declaration", "protocol_function_declaration"}:
        if node.parent is not None and node.parent.type in {
            "class_body",
            "enum_class_body",
            "protocol_body",
        }:
            return "method"
        return "function"
    if node.children:
        keyword_type = node.children[0].type
        if keyword_type == "extension":
            return "extension"
        if keyword_type == "struct":
            return "struct"
        if keyword_type == "enum":
            return "enum"
    return "class"


def _iter_swift_symbol_nodes(root: Any) -> list[Any]:
    """Return Swift AST nodes that correspond to supported symbol types."""
    nodes: list[Any] = []
    for child in root.named_children:
        if child.type in {"class_declaration", "protocol_declaration"}:
            nodes.append(child)
            body_node = _body_node(child)
            if body_node is None:
                continue
            for body_child in body_node.named_children:
                if body_child.type in {
                    "init_declaration",
                    "function_declaration",
                    "protocol_function_declaration",
                }:
                    nodes.append(body_child)
            continue
        if child.type == "function_declaration":
            nodes.append(child)
    return nodes