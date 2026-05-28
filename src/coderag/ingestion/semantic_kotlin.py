"""Extracción semántica fase 1 para Kotlin."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.ingestion.extractors.treesitter_runtime import (
    TreeSitterUnavailableError,
    parse_source,
)

_PACKAGE_PATTERN = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_\.]*)")
_TRAILING_IDENTIFIER_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)$")
_CONTROL_FLOW_NAMES = {"if", "for", "while", "when", "return", "super", "this"}


def extract_kotlin_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int] | None = None,
) -> list[SemanticRelation]:
    """Extract Kotlin IMPORTS, EXTENDS, IMPLEMENTS and CALLS relations."""
    kotlin_files = _kotlin_files(scanned_files)
    by_file, by_file_name, global_by_name = _build_symbol_indexes(symbols)
    path_to_package = _build_path_to_package(kotlin_files)
    fqname_index = _build_fqname_index(symbols, path_to_package)
    resolution_counts: Counter[str] = Counter()
    relations: list[SemanticRelation] = []

    for file_obj in kotlin_files:
        file_symbols = by_file.get(file_obj.path, [])
        if not file_symbols:
            continue

        try:
            tree = parse_source("kotlin", file_obj.content)
        except TreeSitterUnavailableError:
            continue

        resolve_target = _build_kotlin_target_resolver(
            file_obj=file_obj,
            by_file_name=by_file_name,
            global_by_name=global_by_name,
            path_to_package=path_to_package,
            fqname_index=fqname_index,
        )
        relations.extend(
            _extract_import_relations(
                repo_id=repo_id,
                file_obj=file_obj,
                file_symbols=file_symbols,
                root=tree.root_node,
                resolve_target=resolve_target,
                resolution_counts=resolution_counts,
            )
        )
        relations.extend(
            _extract_type_relations(
                repo_id=repo_id,
                file_obj=file_obj,
                file_symbols=file_symbols,
                by_file_name=by_file_name,
                root=tree.root_node,
                resolve_target=resolve_target,
                resolution_counts=resolution_counts,
            )
        )
        relations.extend(
            _extract_call_relations(
                repo_id=repo_id,
                file_obj=file_obj,
                file_symbols=file_symbols,
                root=tree.root_node,
                resolve_target=resolve_target,
                resolution_counts=resolution_counts,
            )
        )

    if resolution_stats_sink is not None:
        resolution_stats_sink.clear()
        resolution_stats_sink.update(dict(resolution_counts))
    return relations


def _kotlin_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filter Kotlin files from the repository scan."""
    return [item for item in scanned_files if item.language == "kotlin"]


def _build_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[
    dict[str, list[SymbolChunk]],
    dict[str, dict[str, str]],
    dict[str, list[str]],
]:
    """Build Kotlin symbol indexes by file and symbol name."""
    by_file: dict[str, list[SymbolChunk]] = {}
    by_file_name: dict[str, dict[str, str]] = {}
    global_by_name: dict[str, list[str]] = {}

    for symbol in symbols:
        if symbol.language != "kotlin":
            continue
        by_file.setdefault(symbol.path, []).append(symbol)
        by_file_name.setdefault(symbol.path, {})[symbol.symbol_name] = symbol.id
        global_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)

    return by_file, by_file_name, global_by_name


def _build_path_to_package(
    scanned_files: list[ScannedFile],
) -> dict[str, str | None]:
    """Build path to package mappings for Kotlin files."""
    mapping: dict[str, str | None] = {}
    for file_obj in scanned_files:
        mapping[file_obj.path] = _file_package(file_obj.content)
    return mapping


def _file_package(content: str) -> str | None:
    """Extract the declared Kotlin package from the file content."""
    for line in content.splitlines()[:40]:
        match = _PACKAGE_PATTERN.match(line)
        if match:
            return match.group(1)
    return None


def _build_fqname_index(
    symbols: list[SymbolChunk],
    path_to_package: dict[str, str | None],
) -> dict[str, str]:
    """Build package-qualified symbol ids for Kotlin types and functions."""
    fqname_index: dict[str, str] = {}
    for symbol in symbols:
        if symbol.language != "kotlin":
            continue
        if symbol.symbol_type not in {"class", "interface", "enum", "function"}:
            continue
        package_name = path_to_package.get(symbol.path)
        if not package_name:
            continue
        fqname_index[f"{package_name}.{symbol.symbol_name}"] = symbol.id
    return fqname_index


def _build_kotlin_target_resolver(
    *,
    file_obj: ScannedFile,
    by_file_name: dict[str, dict[str, str]],
    global_by_name: dict[str, list[str]],
    path_to_package: dict[str, str | None],
    fqname_index: dict[str, str],
) -> Any:
    """Create a Kotlin target resolver using file and import context."""
    local_name_index = by_file_name.get(file_obj.path, {})
    package_name = path_to_package.get(file_obj.path)
    imported_fqname_by_local_name: dict[str, str] = {}
    wildcard_import_packages: list[str] = []

    for raw_line in file_obj.content.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("import "):
            continue
        imported_ref = stripped[len("import ") :].strip()
        alias_name: str | None = None
        if " as " in imported_ref:
            imported_ref, alias_name = imported_ref.split(" as ", maxsplit=1)
            imported_ref = imported_ref.strip()
            alias_name = alias_name.strip()
        if imported_ref.endswith(".*"):
            wildcard_import_packages.append(imported_ref[:-2])
            continue
        local_name = alias_name or imported_ref.split(".")[-1]
        imported_fqname_by_local_name[local_name] = imported_ref

    def _resolve_target(target_ref: str) -> tuple[str | None, str]:
        if not target_ref:
            return None, "unresolved"

        if "." in target_ref:
            direct = fqname_index.get(target_ref)
            if direct:
                return direct, "import"
            simple_name = target_ref.split(".")[-1]
        else:
            simple_name = target_ref

        local = local_name_index.get(simple_name)
        if local:
            return local, "local"

        imported_fqname = imported_fqname_by_local_name.get(simple_name)
        if imported_fqname:
            imported = fqname_index.get(imported_fqname)
            if imported:
                return imported, "import"

        if package_name:
            package_target = fqname_index.get(f"{package_name}.{simple_name}")
            if package_target:
                return package_target, "same_package"

        for wildcard_package in wildcard_import_packages:
            wildcard_target = fqname_index.get(f"{wildcard_package}.{simple_name}")
            if wildcard_target:
                return wildcard_target, "import_wildcard"

        global_ids = global_by_name.get(simple_name) or []
        if len(global_ids) == 1:
            return global_ids[0], "global_unique"
        return None, "unresolved"

    return _resolve_target


def _extract_import_relations(
    *,
    repo_id: str,
    file_obj: ScannedFile,
    file_symbols: list[SymbolChunk],
    root: Any,
    resolve_target: Any,
    resolution_counts: Counter[str],
) -> list[SemanticRelation]:
    """Extract Kotlin IMPORTS relations from import declarations."""
    relations: list[SemanticRelation] = []
    for node in root.named_children:
        if node.type != "import":
            continue
        line = node.start_point[0] + 1
        source_symbol_id = _resolve_source_symbol_id(line, file_symbols)
        if source_symbol_id is None:
            continue

        import_ref = node.text.decode("utf-8")[len("import ") :].strip()
        if " as " in import_ref:
            import_ref = import_ref.split(" as ", maxsplit=1)[0].strip()
        target_symbol_id, resolution_method = resolve_target(import_ref)
        if target_symbol_id is not None:
            resolution_counts[resolution_method] += 1
        relations.append(
            SemanticRelation(
                repo_id=repo_id,
                source_symbol_id=source_symbol_id,
                relation_type="IMPORTS",
                target_symbol_id=target_symbol_id,
                target_ref=import_ref,
                target_kind=("internal" if target_symbol_id else "external"),
                path=file_obj.path,
                line=line,
                confidence=0.95,
                language="kotlin",
                resolution_method=resolution_method,
            )
        )
    return relations


def _extract_type_relations(
    *,
    repo_id: str,
    file_obj: ScannedFile,
    file_symbols: list[SymbolChunk],
    by_file_name: dict[str, dict[str, str]],
    root: Any,
    resolve_target: Any,
    resolution_counts: Counter[str],
) -> list[SemanticRelation]:
    """Extract Kotlin EXTENDS and IMPLEMENTS relations from type headers."""
    relations: list[SemanticRelation] = []
    file_name_index = by_file_name.get(file_obj.path, {})

    for node in root.named_children:
        if node.type != "class_declaration":
            continue
        class_name = _identifier_text(node)
        if class_name is None:
            continue
        source_symbol_id = file_name_index.get(class_name)
        if source_symbol_id is None:
            continue
        source_symbol_type = next(
            (item.symbol_type for item in file_symbols if item.id == source_symbol_id),
            "class",
        )
        specifiers = _delegation_specifiers(node)
        for specifier in specifiers:
            target_ref = _delegation_target_ref(specifier)
            if not target_ref:
                continue
            relation_type = _delegation_relation_type(source_symbol_type, specifier)
            line = specifier.start_point[0] + 1
            target_symbol_id, resolution_method = resolve_target(target_ref)
            if target_symbol_id is not None:
                resolution_counts[resolution_method] += 1
            relations.append(
                SemanticRelation(
                    repo_id=repo_id,
                    source_symbol_id=source_symbol_id,
                    relation_type=relation_type,
                    target_symbol_id=target_symbol_id,
                    target_ref=target_ref,
                    target_kind=("internal" if target_symbol_id else "external"),
                    path=file_obj.path,
                    line=line,
                    confidence=0.95,
                    language="kotlin",
                    resolution_method=resolution_method,
                )
            )
    return relations


def _extract_call_relations(
    *,
    repo_id: str,
    file_obj: ScannedFile,
    file_symbols: list[SymbolChunk],
    root: Any,
    resolve_target: Any,
    resolution_counts: Counter[str],
) -> list[SemanticRelation]:
    """Extract Kotlin CALLS relations from function bodies."""
    relations: list[SemanticRelation] = []
    for function_node in _iter_descendants(root):
        if function_node.type != "function_declaration":
            continue
        for call_node in _iter_descendants(function_node):
            if call_node.type != "call_expression":
                continue
            target_ref = _call_target_name(call_node)
            if not target_ref or target_ref in _CONTROL_FLOW_NAMES:
                continue
            line = call_node.start_point[0] + 1
            source_symbol_id = _resolve_source_symbol_id(line, file_symbols)
            if source_symbol_id is None:
                continue
            target_symbol_id, resolution_method = resolve_target(target_ref)
            if target_symbol_id is not None:
                resolution_counts[resolution_method] += 1
            relations.append(
                SemanticRelation(
                    repo_id=repo_id,
                    source_symbol_id=source_symbol_id,
                    relation_type="CALLS",
                    target_symbol_id=target_symbol_id,
                    target_ref=target_ref,
                    target_kind=("internal" if target_symbol_id else "external"),
                    path=file_obj.path,
                    line=line,
                    confidence=0.8,
                    language="kotlin",
                    resolution_method=resolution_method,
                )
            )
    return relations


def _resolve_source_symbol_id(
    line: int,
    file_symbols: list[SymbolChunk],
) -> str | None:
    """Resolve the narrowest Kotlin source symbol containing the line."""
    candidates = [
        item
        for item in file_symbols
        if item.start_line <= line <= item.end_line
        and item.symbol_type in {
            "class",
            "interface",
            "enum",
            "method",
            "constructor",
            "function",
        }
    ]
    if not candidates:
        fallback_candidates = [
            item
            for item in file_symbols
            if item.symbol_type in {
                "class",
                "interface",
                "enum",
                "function",
            }
        ]
        if not fallback_candidates:
            return None
        fallback_candidates.sort(key=lambda item: item.start_line)
        return fallback_candidates[0].id
    candidates.sort(key=lambda item: (item.end_line - item.start_line, item.start_line))
    return candidates[0].id


def _identifier_text(node: Any) -> str | None:
    """Return the Kotlin identifier from a declaration node."""
    for child in node.named_children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _delegation_specifiers(node: Any) -> list[Any]:
    """Return Kotlin delegation specifiers from a class declaration."""
    for child in node.named_children:
        if child.type == "delegation_specifiers":
            return list(child.named_children)
    return []


def _delegation_target_ref(node: Any) -> str | None:
    """Return the referenced type name from a Kotlin delegation specifier."""
    text = node.text.decode("utf-8").strip()
    if not text:
        return None
    if "(" in text:
        return text.split("(", maxsplit=1)[0].strip()
    return text


def _delegation_relation_type(source_symbol_type: str, specifier: Any) -> str:
    """Map Kotlin delegation specs to EXTENDS or IMPLEMENTS relations."""
    if source_symbol_type == "interface":
        return "EXTENDS"
    for child in specifier.named_children:
        if child.type == "constructor_invocation":
            return "EXTENDS"
    return "IMPLEMENTS"


def _call_target_name(node: Any) -> str | None:
    """Return the callee name from a Kotlin call expression."""
    if not node.named_children:
        return None
    callee = node.named_children[0]
    callee_text = callee.text.decode("utf-8").strip()
    if callee.type == "identifier":
        return callee_text
    match = _TRAILING_IDENTIFIER_PATTERN.search(callee_text)
    if not match:
        return None
    return match.group(1)


def _iter_descendants(node: Any) -> Iterator[Any]:
    """Yield the node and all named descendants depth-first."""
    yield node
    for child in node.named_children:
        yield from _iter_descendants(child)