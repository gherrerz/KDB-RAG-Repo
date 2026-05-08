"""Extracción semántica inicial para Python basada en AST."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable

from coderag.core.models import (
    FileImportRelation,
    ScannedFile,
    SemanticRelation,
    SymbolChunk,
)
from coderag.ingestion.module_resolver import (
    build_python_module_index,
    build_python_qualified_name_index,
    resolve_python_relative_import,
)


def _build_python_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[
    dict[tuple[str, str, int], str],
    dict[str, dict[str, str]],
    dict[str, list[str]],
]:
    """Genera índices para resolver símbolos Python por ubicación y nombre."""
    by_location: dict[tuple[str, str, int], str] = {}
    by_file_name: dict[str, dict[str, str]] = {}
    global_by_name: dict[str, list[str]] = {}

    for symbol in symbols:
        if symbol.language != "python":
            continue
        by_location[(symbol.path, symbol.symbol_name, symbol.start_line)] = symbol.id
        by_file_name.setdefault(symbol.path, {})[symbol.symbol_name] = symbol.id
        global_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)

    return by_location, by_file_name, global_by_name


def _target_ref_from_expr(node: ast.expr) -> str | None:
    """Resuelve una referencia legible para llamadas o herencia."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent_ref = _target_ref_from_expr(node.value)
        if parent_ref:
            return f"{parent_ref}.{node.attr}"
        return node.attr
    if isinstance(node, ast.Call):
        return _target_ref_from_expr(node.func)
    if isinstance(node, ast.Subscript):
        return _target_ref_from_expr(node.value)
    return None


def _build_initial_import_bindings(
    tree: ast.AST,
    path: str,
) -> dict[str, str]:
    """Build initial top-level Python import bindings for a file."""
    bindings: dict[str, str] = {}
    module_body = getattr(tree, "body", [])
    for node in module_body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                bindings[local_name] = alias.name
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            if node.level > 0:
                target_ref = resolve_python_relative_import(
                    source_path=path,
                    level=node.level,
                    module=node.module,
                    name=alias.name,
                )
            else:
                module_name = node.module or ""
                target_ref = (
                    f"{module_name}.{alias.name}" if module_name else alias.name
                )
            if not target_ref:
                continue
            bindings[alias.asname or alias.name] = target_ref
    return bindings


def _build_module_to_paths(module_index: dict[str, str]) -> dict[str, list[str]]:
    """Build a reverse map from dotted module names to file paths."""
    module_to_paths: dict[str, list[str]] = {}
    for path, module_name in module_index.items():
        if not module_name:
            continue
        module_to_paths.setdefault(module_name, []).append(path)
    return module_to_paths


def _resolve_file_import_target(
    target_ref: str,
    *,
    qualified_name_index: dict[str, str],
    symbol_path_by_id: dict[str, str],
    module_to_paths: dict[str, list[str]],
) -> tuple[str | None, str]:
    """Resolve a top-level Python import to a repository file when possible."""
    symbol_id = qualified_name_index.get(target_ref)
    if symbol_id:
        target_path = symbol_path_by_id.get(symbol_id)
        if target_path:
            return target_path, "qualified"

    direct_paths = module_to_paths.get(target_ref) or []
    if len(direct_paths) == 1:
        return direct_paths[0], "module"

    if "." in target_ref:
        module_target = target_ref.rsplit(".", maxsplit=1)[0]
        module_paths = module_to_paths.get(module_target) or []
        if len(module_paths) == 1:
            return module_paths[0], "module"

    return None, "unresolved"


def _build_top_level_file_import_relations(
    repo_id: str,
    file_obj: ScannedFile,
    tree: ast.AST,
    *,
    qualified_name_index: dict[str, str],
    symbol_path_by_id: dict[str, str],
    module_to_paths: dict[str, list[str]],
) -> list[FileImportRelation]:
    """Extract top-level Python imports as file-scoped dependency relations."""
    relations: list[FileImportRelation] = []
    seen: set[tuple[str, str, int]] = set()

    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target_ref = alias.name
                target_path, resolution_method = _resolve_file_import_target(
                    target_ref,
                    qualified_name_index=qualified_name_index,
                    symbol_path_by_id=symbol_path_by_id,
                    module_to_paths=module_to_paths,
                )
                dedup_key = (
                    file_obj.path,
                    target_path or target_ref,
                    int(getattr(node, "lineno", 1)),
                )
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                relations.append(
                    FileImportRelation(
                        repo_id=repo_id,
                        source_path=file_obj.path,
                        target_path=target_path,
                        target_ref=target_ref,
                        target_kind="file" if target_path else "external",
                        path=file_obj.path,
                        line=int(getattr(node, "lineno", 1)),
                        language="python",
                        resolution_method=resolution_method,
                    )
                )
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        for alias in node.names:
            if alias.name == "*":
                continue
            if node.level > 0:
                target_ref = resolve_python_relative_import(
                    source_path=file_obj.path,
                    level=node.level,
                    module=node.module,
                    name=alias.name,
                )
            else:
                module_name = node.module or ""
                target_ref = (
                    f"{module_name}.{alias.name}" if module_name else alias.name
                )
            if not target_ref:
                continue
            target_path, resolution_method = _resolve_file_import_target(
                target_ref,
                qualified_name_index=qualified_name_index,
                symbol_path_by_id=symbol_path_by_id,
                module_to_paths=module_to_paths,
            )
            dedup_key = (
                file_obj.path,
                target_path or target_ref,
                int(getattr(node, "lineno", 1)),
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            relations.append(
                FileImportRelation(
                    repo_id=repo_id,
                    source_path=file_obj.path,
                    target_path=target_path,
                    target_ref=target_ref,
                    target_kind="file" if target_path else "external",
                    path=file_obj.path,
                    line=int(getattr(node, "lineno", 1)),
                    language="python",
                    resolution_method=resolution_method,
                )
            )

    relations.sort(key=lambda item: (item.source_path, item.line, item.target_ref))
    return relations


class _PythonSemanticVisitor(ast.NodeVisitor):
    """Recorre AST de Python y emite relaciones semánticas por símbolo."""

    def __init__(
        self,
        repo_id: str,
        path: str,
        by_location: dict[tuple[str, str, int], str],
        by_file_name: dict[str, dict[str, str]],
        qualified_name_index: dict[str, str],
        global_by_name: dict[str, list[str]],
        import_bindings: dict[str, str] | None = None,
        resolution_source_counts: Counter[str] | None = None,
    ) -> None:
        """Inicializa estado mutable y estructuras para deduplicación."""
        self.repo_id = repo_id
        self.path = path
        self.by_location = by_location
        self.by_file_name = by_file_name
        self.qualified_name_index = qualified_name_index
        self.global_by_name = global_by_name
        self._source_stack: list[str] = []
        self._binding_stack: list[dict[str, str]] = [dict(import_bindings or {})]
        self._seen: set[tuple[str, str, str, int]] = set()
        self.relations: list[SemanticRelation] = []
        self._resolution_source_counts = resolution_source_counts

    def _resolve_source(self, name: str, line: int) -> str | None:
        """Resuelve el símbolo fuente actual por nombre y línea de inicio."""
        return self.by_location.get((self.path, name, line))

    def _current_bindings(self) -> dict[str, str]:
        """Return the active binding table for the current scope."""
        return self._binding_stack[-1]

    def _resolve_bound_reference(self, target_ref: str) -> str | None:
        """Expand a dotted reference using the active import bindings."""
        parts = target_ref.split(".")
        root_name = parts[0]
        binding = self._current_bindings().get(root_name)
        if not binding:
            return None
        suffix = ".".join(parts[1:])
        return f"{binding}.{suffix}" if suffix else binding

    def _resolve_target(self, target_ref: str) -> tuple[str | None, str]:
        """Resuelve referencias locales, importadas y cross-file."""
        if not target_ref:
            return None, "unresolved"

        if "." not in target_ref:
            local_target = self.by_file_name.get(self.path, {}).get(target_ref)
            if local_target:
                return local_target, "local"

        bound_target = self._resolve_bound_reference(target_ref)
        if bound_target:
            alias_target = self.qualified_name_index.get(bound_target)
            if alias_target:
                return alias_target, "alias"

        qualified_target = self.qualified_name_index.get(target_ref)
        if qualified_target:
            return qualified_target, "qualified"

        simple_name = target_ref.split(".")[-1]
        global_targets = self.global_by_name.get(simple_name) or []
        if len(global_targets) == 1:
            return global_targets[0], "global_unique"
        return None, "unresolved"

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
        target_symbol_id, resolution_source = self._resolve_target(target_ref)
        dedup_key = (
            source_symbol_id,
            relation_type,
            target_symbol_id or target_ref,
            line,
        )
        if dedup_key in self._seen:
            return
        self._seen.add(dedup_key)
        if self._resolution_source_counts is not None:
            self._resolution_source_counts[resolution_source] += 1

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
                resolution_method=resolution_source,
            )
        )

    def _visit_symbol_scope(self, node: ast.AST, name: str, line: int) -> None:
        """Gestiona entrada/salida de un ámbito de símbolo."""
        symbol_id = self._resolve_source(name, line)
        self._binding_stack.append(dict(self._current_bindings()))
        if symbol_id:
            self._source_stack.append(symbol_id)
        self.generic_visit(node)
        if symbol_id:
            self._source_stack.pop()
        self._binding_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Extrae EXTENDS para clases y visita el cuerpo."""
        source_symbol_id = self._resolve_source(node.name, int(node.lineno))
        self._binding_stack.append(dict(self._current_bindings()))
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
            self._binding_stack.pop()
            return

        self.generic_visit(node)
        self._binding_stack.pop()

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
            self._current_bindings()[alias.asname or alias.name.split(".")[0]] = (
                alias.name
            )
            self._append_relation(
                relation_type="IMPORTS",
                target_ref=alias.name,
                line=int(getattr(node, "lineno", 1)),
                confidence=0.8,
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Extrae relaciones IMPORTS para from ... import ... en símbolos."""
        for alias in node.names:
            imported_name = alias.name or ""
            if imported_name == "*":
                continue
            if node.level > 0:
                target_ref = resolve_python_relative_import(
                    source_path=self.path,
                    level=node.level,
                    module=node.module,
                    name=imported_name,
                )
            else:
                module_name = node.module or ""
                target_ref = (
                    f"{module_name}.{imported_name}"
                    if module_name and imported_name
                    else module_name or imported_name
                )
            if not target_ref:
                continue
            self._current_bindings()[alias.asname or imported_name] = target_ref
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
    resolution_stats_sink: dict[str, int] | None = None,
    file_imports_sink: list[FileImportRelation] | None = None,
) -> list[SemanticRelation]:
    """Extrae relaciones semánticas Python (CALLS, IMPORTS y EXTENDS)."""
    by_location, by_file_name, global_by_name = _build_python_symbol_indexes(symbols)
    module_index = build_python_module_index(scanned_files)
    module_to_paths = _build_module_to_paths(module_index)
    qualified_name_index = build_python_qualified_name_index(symbols, module_index)
    symbol_path_by_id = {symbol.id: symbol.path for symbol in symbols}
    relations: list[SemanticRelation] = []
    file_import_relations: list[FileImportRelation] = []
    resolution_source_counts: Counter[str] = Counter()

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
            qualified_name_index=qualified_name_index,
            global_by_name=global_by_name,
            import_bindings=_build_initial_import_bindings(tree, file_obj.path),
            resolution_source_counts=resolution_source_counts,
        )
        visitor.visit(tree)
        relations.extend(visitor.relations)
        file_import_relations.extend(
            _build_top_level_file_import_relations(
                repo_id,
                file_obj,
                tree,
                qualified_name_index=qualified_name_index,
                symbol_path_by_id=symbol_path_by_id,
                module_to_paths=module_to_paths,
            )
        )

    if resolution_stats_sink is not None:
        resolution_stats_sink.clear()
        resolution_stats_sink.update(dict(resolution_source_counts))
    if file_imports_sink is not None:
        file_imports_sink.clear()
        file_imports_sink.extend(file_import_relations)

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
