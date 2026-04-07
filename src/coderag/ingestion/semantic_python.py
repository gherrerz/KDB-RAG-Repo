"""Extracción semántica inicial para Python basada en AST."""

from __future__ import annotations

import ast
from collections.abc import Iterable

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk


def _build_symbol_lookup(
    symbols: list[SymbolChunk],
) -> tuple[dict[tuple[str, str, int], str], dict[str, dict[str, str]]]:
    """Genera índices para resolver símbolos Python por ubicación y nombre."""
    by_location: dict[tuple[str, str, int], str] = {}
    by_file_name: dict[str, dict[str, str]] = {}

    for symbol in symbols:
        if symbol.language != "python":
            continue
        by_location[(symbol.path, symbol.symbol_name, symbol.start_line)] = symbol.id
        by_file_name.setdefault(symbol.path, {})[symbol.symbol_name] = symbol.id

    return by_location, by_file_name


def _target_ref_from_expr(node: ast.expr) -> str | None:
    """Resuelve una referencia legible para llamadas o herencia."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _target_ref_from_expr(node.func)
    if isinstance(node, ast.Subscript):
        return _target_ref_from_expr(node.value)
    return None


class _PythonSemanticVisitor(ast.NodeVisitor):
    """Recorre AST de Python y emite relaciones semánticas por símbolo."""

    def __init__(
        self,
        repo_id: str,
        path: str,
        by_location: dict[tuple[str, str, int], str],
        by_file_name: dict[str, dict[str, str]],
    ) -> None:
        """Inicializa estado mutable y estructuras para deduplicación."""
        self.repo_id = repo_id
        self.path = path
        self.by_location = by_location
        self.by_file_name = by_file_name
        self._source_stack: list[str] = []
        self._seen: set[tuple[str, str, str, int]] = set()
        self.relations: list[SemanticRelation] = []

    def _resolve_source(self, name: str, line: int) -> str | None:
        """Resuelve el símbolo fuente actual por nombre y línea de inicio."""
        return self.by_location.get((self.path, name, line))

    def _resolve_target(self, target_ref: str) -> str | None:
        """Resuelve referencias intraarchivo por nombre simple."""
        return self.by_file_name.get(self.path, {}).get(target_ref)

    def _append_relation(
        self,
        relation_type: str,
        target_ref: str,
        line: int,
        confidence: float,
    ) -> None:
        """Agrega una relación si existe símbolo fuente activo."""
        if not self._source_stack:
            return

        source_symbol_id = self._source_stack[-1]
        target_symbol_id = self._resolve_target(target_ref)
        dedup_key = (
            source_symbol_id,
            relation_type,
            target_symbol_id or target_ref,
            line,
        )
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)

        self.relations.append(
            SemanticRelation(
                repo_id=self.repo_id,
                source_symbol_id=source_symbol_id,
                relation_type=relation_type,
                target_symbol_id=target_symbol_id,
                target_ref=target_ref,
                target_kind="symbol" if target_symbol_id else "external",
                path=self.path,
                line=max(1, line),
                confidence=confidence,
                language="python",
            )
        )

    def _visit_symbol_scope(self, node: ast.AST, name: str, line: int) -> None:
        """Gestiona entrada/salida de un ámbito de símbolo."""
        symbol_id = self._resolve_source(name, line)
        if symbol_id:
            self._source_stack.append(symbol_id)
        self.generic_visit(node)
        if symbol_id:
            self._source_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Extrae EXTENDS para clases y visita el cuerpo."""
        source_symbol_id = self._resolve_source(node.name, int(node.lineno))
        if source_symbol_id:
            self._source_stack.append(source_symbol_id)
            for base in node.bases:
                base_ref = _target_ref_from_expr(base)
                if not base_ref:
                    continue
                self._append_relation(
                    relation_type="EXTENDS",
                    target_ref=base_ref,
                    line=int(getattr(base, "lineno", node.lineno)),
                    confidence=0.95,
                )
            self.generic_visit(node)
            self._source_stack.pop()
            return

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Recorre funciones para registrar CALLS e IMPORTS locales."""
        self._visit_symbol_scope(node, node.name, int(node.lineno))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Recorre funciones async para registrar CALLS e IMPORTS locales."""
        self._visit_symbol_scope(node, node.name, int(node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        """Extrae relaciones CALLS desde el símbolo activo."""
        target_ref = _target_ref_from_expr(node.func)
        if target_ref:
            self._append_relation(
                relation_type="CALLS",
                target_ref=target_ref,
                line=int(getattr(node, "lineno", 1)),
                confidence=0.9,
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Extrae relaciones IMPORTS para imports dentro de símbolos."""
        for alias in node.names:
            if not alias.name:
                continue
            self._append_relation(
                relation_type="IMPORTS",
                target_ref=alias.name,
                line=int(getattr(node, "lineno", 1)),
                confidence=0.8,
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Extrae relaciones IMPORTS para from ... import ... en símbolos."""
        module = node.module or ""
        for alias in node.names:
            imported_name = alias.name or ""
            if module and imported_name:
                target_ref = f"{module}.{imported_name}"
            elif module:
                target_ref = module
            else:
                target_ref = imported_name
            if not target_ref:
                continue
            self._append_relation(
                relation_type="IMPORTS",
                target_ref=target_ref,
                line=int(getattr(node, "lineno", 1)),
                confidence=0.8,
            )
        self.generic_visit(node)


def _python_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filtra archivos Python de la lista escaneada."""
    return [item for item in scanned_files if item.language == "python"]


def extract_python_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
) -> list[SemanticRelation]:
    """Extrae relaciones semánticas Python (CALLS, IMPORTS y EXTENDS)."""
    by_location, by_file_name = _build_symbol_lookup(symbols)
    relations: list[SemanticRelation] = []

    for file_obj in _python_files(scanned_files):
        try:
            tree = ast.parse(file_obj.content)
        except (SyntaxError, ValueError):
            continue

        visitor = _PythonSemanticVisitor(
            repo_id=repo_id,
            path=file_obj.path,
            by_location=by_location,
            by_file_name=by_file_name,
        )
        visitor.visit(tree)
        relations.extend(visitor.relations)

    relations.sort(
        key=lambda item: (
            item.path,
            item.line,
            item.source_symbol_id,
            item.relation_type,
            item.target_ref,
        )
    )
    return relations
