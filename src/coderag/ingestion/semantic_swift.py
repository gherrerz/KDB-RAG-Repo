"""Extracción semántica fase 1 para Swift."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath
from typing import Any

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.ingestion.extractors.treesitter_runtime import (
    TreeSitterUnavailableError,
    parse_source,
)

_TRAILING_IDENTIFIER_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)$")
_COLLECTION_WRAPPER_CALL_PATTERN = re.compile(
    r"\.(dropFirst|dropLast|drop|prefix|suffix|reversed|sorted|shuffled|filter)"
    r"\([^()]*\)$"
)
_COLLECTION_WRAPPER_BLOCK_PATTERN = re.compile(r"\.(filter)\s*\{.*\}$")
_CONTROL_FLOW_NAMES = {"if", "for", "while", "switch", "guard", "return", "self", "super"}

ReceiverTypeHints = dict[str, tuple[str, ...]]
AssociatedTypeBindings = dict[str, tuple[str, ...]]
TypeReceiverRecord = tuple[
    str,
    ReceiverTypeHints,
    tuple[tuple[str, ...], ...],
    frozenset[str],
    AssociatedTypeBindings,
]
TypeReceiverHintsIndex = dict[str, list[TypeReceiverRecord]]


def extract_swift_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int] | None = None,
) -> list[SemanticRelation]:
    """Extract Swift IMPORTS, EXTENDS, IMPLEMENTS and CALLS relations."""
    swift_files = _swift_files(scanned_files)
    parsed_roots = _parse_swift_roots(swift_files)
    (
        by_file,
        by_file_name,
        global_by_name,
        symbol_by_id,
        owner_symbol_by_member_id,
    ) = _build_symbol_indexes(symbols)
    type_receiver_hints_index = _build_type_receiver_hints_index(
        swift_files=swift_files,
        parsed_roots=parsed_roots,
    )
    resolution_counts: Counter[str] = Counter()
    relations: list[SemanticRelation] = []

    for file_obj in swift_files:
        file_symbols = by_file.get(file_obj.path, [])
        if not file_symbols:
            continue

        root = parsed_roots.get(file_obj.path)
        if root is None:
            continue

        resolve_target = _build_swift_target_resolver(
            file_obj=file_obj,
            by_file_name=by_file_name,
            global_by_name=global_by_name,
            symbol_by_id=symbol_by_id,
            owner_symbol_by_member_id=owner_symbol_by_member_id,
        )
        relations.extend(
            _extract_import_relations(
                repo_id=repo_id,
                file_obj=file_obj,
                file_symbols=file_symbols,
                root=root,
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
                symbol_by_id=symbol_by_id,
                root=root,
                resolve_target=resolve_target,
                resolution_counts=resolution_counts,
            )
        )
        relations.extend(
            _extract_call_relations(
                repo_id=repo_id,
                file_obj=file_obj,
                file_symbols=file_symbols,
                root=root,
                resolve_target=resolve_target,
                resolution_counts=resolution_counts,
                type_receiver_hints_index=type_receiver_hints_index,
            )
        )

    if resolution_stats_sink is not None:
        resolution_stats_sink.clear()
        resolution_stats_sink.update(dict(resolution_counts))
    return relations


def _swift_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filter Swift files from the repository scan."""
    return [item for item in scanned_files if item.language == "swift"]


def _parse_swift_roots(scanned_files: Iterable[ScannedFile]) -> dict[str, Any]:
    """Parse Swift files once and return their syntax-tree roots by path."""
    parsed_roots: dict[str, Any] = {}
    for file_obj in scanned_files:
        try:
            tree = parse_source("swift", file_obj.content)
        except TreeSitterUnavailableError:
            continue
        parsed_roots[file_obj.path] = tree.root_node
    return parsed_roots


def _build_type_receiver_hints_index(
    *,
    swift_files: list[ScannedFile],
    parsed_roots: dict[str, Any],
) -> TypeReceiverHintsIndex:
    """Index typed Swift properties by declared type name across files."""
    index: TypeReceiverHintsIndex = {}
    path_to_imported_modules = {
        file_obj.path: frozenset(_imported_module_names(file_obj.content))
        for file_obj in swift_files
    }
    for path, root in parsed_roots.items():
        for node in root.named_children:
            if node.type not in {"class_declaration", "protocol_declaration"}:
                continue
            type_name = _declaration_type_name(node)
            if type_name is None:
                continue
            body_node = _declaration_body_node(node)
            if body_node is None:
                continue
            property_hints = _build_property_receiver_type_hints(body_node)
            inherited_type_refs = tuple(
                type_parts
                for specifier in _inheritance_specifiers(node)
                if (type_parts := _type_ref_parts(_inheritance_target_ref(specifier)))
                is not None
            )
            associated_type_bindings = _associated_type_bindings_for_declaration(node)
            index.setdefault(type_name, []).append(
                (
                    path,
                    property_hints,
                    inherited_type_refs,
                    path_to_imported_modules.get(path, frozenset()),
                    associated_type_bindings,
                )
            )
    return index


def _build_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[
    dict[str, list[SymbolChunk]],
    dict[str, dict[str, str]],
    dict[str, list[str]],
    dict[str, SymbolChunk],
    dict[str, SymbolChunk],
]:
    """Build Swift symbol indexes by file, name, and id."""
    by_file: dict[str, list[SymbolChunk]] = {}
    by_file_name: dict[str, dict[str, str]] = {}
    global_by_name: dict[str, list[str]] = {}
    symbol_by_id: dict[str, SymbolChunk] = {}

    for symbol in symbols:
        if symbol.language != "swift":
            continue
        by_file.setdefault(symbol.path, []).append(symbol)
        by_file_name.setdefault(symbol.path, {})[symbol.symbol_name] = symbol.id
        global_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)
        symbol_by_id[symbol.id] = symbol

    owner_symbol_by_member_id: dict[str, SymbolChunk] = {}
    for file_symbols in by_file.values():
        owner_symbol_by_member_id.update(_owner_symbols_by_member_id(file_symbols))

    return (
        by_file,
        by_file_name,
        global_by_name,
        symbol_by_id,
        owner_symbol_by_member_id,
    )


def _build_swift_target_resolver(
    *,
    file_obj: ScannedFile,
    by_file_name: dict[str, dict[str, str]],
    global_by_name: dict[str, list[str]],
    symbol_by_id: dict[str, SymbolChunk],
    owner_symbol_by_member_id: dict[str, SymbolChunk],
) -> Any:
    """Create a Swift target resolver using local and global symbol names."""
    local_name_index = by_file_name.get(file_obj.path, {})
    imported_modules = _imported_module_names(file_obj.content)

    def _resolve_target(
        target_ref: str,
        owner_hint: tuple[str, ...] | None = None,
    ) -> tuple[str | None, str]:
        if not target_ref:
            return None, "unresolved"

        local = local_name_index.get(target_ref)
        if local:
            return local, "local"

        global_ids = global_by_name.get(target_ref) or []
        if len(global_ids) == 1:
            return global_ids[0], "global_unique"
        if owner_hint:
            owner_matches = _resolve_symbol_by_owner_hint(
                candidate_ids=global_ids,
                owner_hint=owner_hint,
                symbol_by_id=symbol_by_id,
                owner_symbol_by_member_id=owner_symbol_by_member_id,
                imported_modules=imported_modules,
            )
            if owner_matches is not None:
                return owner_matches
        if imported_modules:
            import_module_matches = [
                symbol_id
                for symbol_id in global_ids
                if _symbol_matches_imported_module(
                    symbol=symbol_by_id.get(symbol_id),
                    imported_modules=imported_modules,
                )
            ]
            if len(import_module_matches) == 1:
                return import_module_matches[0], "import_module_path"
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
    """Extract Swift IMPORTS relations from import declarations."""
    relations: list[SemanticRelation] = []
    for node in root.named_children:
        if node.type != "import_declaration":
            continue
        target_ref = _import_target_name(node)
        if not target_ref:
            continue
        line = node.start_point[0] + 1
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
                relation_type="IMPORTS",
                target_symbol_id=target_symbol_id,
                target_ref=target_ref,
                target_kind=("internal" if target_symbol_id else "external"),
                path=file_obj.path,
                line=line,
                confidence=0.95,
                language="swift",
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
    symbol_by_id: dict[str, SymbolChunk],
    root: Any,
    resolve_target: Any,
    resolution_counts: Counter[str],
) -> list[SemanticRelation]:
    """Extract Swift EXTENDS and IMPLEMENTS relations from type headers."""
    relations: list[SemanticRelation] = []
    del by_file_name

    for node in root.named_children:
        if node.type not in {"class_declaration", "protocol_declaration"}:
            continue
        source_name = _declaration_type_name(node)
        if source_name is None:
            continue
        source_symbol_id = _resolve_declared_symbol_id(node, file_symbols)
        if source_symbol_id is None:
            continue
        source_symbol_type = next(
            (item.symbol_type for item in file_symbols if item.id == source_symbol_id),
            "class",
        )
        for specifier in _inheritance_specifiers(node):
            target_ref = _inheritance_target_ref(specifier)
            if not target_ref:
                continue
            line = specifier.start_point[0] + 1
            target_symbol_id, resolution_method = resolve_target(target_ref)
            relation_type = _inheritance_relation_type(
                source_symbol_type,
                target_symbol_id,
                symbol_by_id,
            )
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
                    language="swift",
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
    type_receiver_hints_index: TypeReceiverHintsIndex,
) -> list[SemanticRelation]:
    """Extract Swift CALLS relations from function bodies."""
    relations: list[SemanticRelation] = []
    imported_modules = _imported_module_names(file_obj.content)
    for function_node in _iter_descendants(root):
        if function_node.type != "function_declaration":
            continue
        receiver_type_hints = _build_declared_type_receiver_type_hints(
            function_node,
            file_obj.path,
            imported_modules,
            type_receiver_hints_index,
        )
        receiver_type_hints.update(
            _build_enclosing_type_receiver_type_hints(function_node)
        )
        receiver_type_hints.update(
            _build_function_receiver_type_hints(function_node, receiver_type_hints)
        )
        for call_node in _iter_descendants(function_node):
            if call_node.type != "call_expression":
                continue
            target_ref, owner_hint, receiver_name = _call_target_parts(call_node)
            if not target_ref or target_ref in _CONTROL_FLOW_NAMES:
                continue
            if owner_hint is None and receiver_name is not None:
                owner_hint = receiver_type_hints.get(receiver_name)
            line = call_node.start_point[0] + 1
            source_symbol_id = _resolve_source_symbol_id(line, file_symbols)
            if source_symbol_id is None:
                continue
            target_symbol_id, resolution_method = resolve_target(
                target_ref,
                owner_hint,
            )
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
                    language="swift",
                    resolution_method=resolution_method,
                )
            )
    return relations


def _resolve_source_symbol_id(
    line: int,
    file_symbols: list[SymbolChunk],
) -> str | None:
    """Resolve the narrowest Swift source symbol containing the line."""
    candidates = [
        item
        for item in file_symbols
        if item.start_line <= line <= item.end_line
        and item.symbol_type in {
            "class",
            "extension",
            "struct",
            "protocol",
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
                "extension",
                "struct",
                "protocol",
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


def _import_target_name(node: Any) -> str | None:
    """Return the imported module or symbol name from a Swift import node."""
    for child in node.named_children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _imported_module_names(content: str) -> set[str]:
    """Return imported Swift module names from simple import declarations."""
    modules: set[str] = set()
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("import "):
            continue
        imported_ref = stripped[len("import ") :].strip()
        if not imported_ref:
            continue
        parts = imported_ref.split()
        if len(parts) >= 2 and parts[0] in {
            "typealias",
            "struct",
            "class",
            "enum",
            "protocol",
            "let",
            "var",
            "func",
        }:
            imported_ref = parts[1]
        module_name = imported_ref.split(".", maxsplit=1)[0].strip()
        if module_name:
            modules.add(module_name)
    return modules


def _symbol_matches_imported_module(
    *,
    symbol: SymbolChunk | None,
    imported_modules: set[str],
) -> bool:
    """Check whether a symbol path appears to belong to an imported module."""
    if symbol is None:
        return False
    path_parts = {part for part in PurePosixPath(symbol.path).parts if part}
    return any(module_name in path_parts for module_name in imported_modules)


def _owner_symbols_by_member_id(
    file_symbols: list[SymbolChunk],
) -> dict[str, SymbolChunk]:
    """Map Swift methods and constructors to their nearest enclosing type."""
    owner_symbols = [
        item
        for item in file_symbols
        if item.symbol_type in {"class", "extension", "struct", "protocol", "enum"}
    ]
    mapping: dict[str, SymbolChunk] = {}
    for symbol in file_symbols:
        if symbol.symbol_type not in {"method", "constructor"}:
            continue
        candidates = [
            item
            for item in owner_symbols
            if item.start_line <= symbol.start_line <= symbol.end_line <= item.end_line
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item.end_line - item.start_line, item.start_line))
        mapping[symbol.id] = candidates[0]
    return mapping


def _resolve_symbol_by_owner_hint(
    *,
    candidate_ids: list[str],
    owner_hint: tuple[str, ...],
    symbol_by_id: dict[str, SymbolChunk],
    owner_symbol_by_member_id: dict[str, SymbolChunk],
    imported_modules: set[str],
) -> tuple[str, str] | None:
    """Resolve Swift symbols using owner/module hints from qualified calls."""
    if not owner_hint:
        return None

    owner_name = owner_hint[-1]
    module_hints = owner_hint[:-1]
    owner_matches: list[str] = []
    import_owner_matches: list[str] = []
    for candidate_id in candidate_ids:
        candidate_symbol = symbol_by_id.get(candidate_id)
        if candidate_symbol is None:
            continue
        candidate_owner = owner_symbol_by_member_id.get(candidate_id)
        candidate_owner_name = (
            candidate_owner.symbol_name if candidate_owner is not None else None
        )
        if candidate_owner_name != owner_name:
            continue
        if module_hints and _symbol_matches_module_hint(candidate_symbol, module_hints):
            owner_matches.append(candidate_id)
            continue
        if imported_modules and _symbol_matches_imported_module(
            symbol=candidate_symbol,
            imported_modules=imported_modules,
        ):
            import_owner_matches.append(candidate_id)

    if len(owner_matches) == 1:
        return owner_matches[0], "owner_path"
    if len(import_owner_matches) == 1:
        return import_owner_matches[0], "owner_import_module_path"
    return None


def _symbol_matches_module_hint(
    symbol: SymbolChunk,
    module_hints: tuple[str, ...],
) -> bool:
    """Check whether a symbol path contains all explicit module/type hints."""
    path_parts = {part for part in PurePosixPath(symbol.path).parts if part}
    return all(part in path_parts for part in module_hints)


def _type_identifier_text(node: Any) -> str | None:
    """Return the Swift type identifier from a declaration node."""
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


def _resolve_declared_symbol_id(node: Any, file_symbols: list[SymbolChunk]) -> str | None:
    """Resolve the symbol id for a Swift declaration node by line and type."""
    start_line = node.start_point[0] + 1
    source_name = _declaration_type_name(node)
    if source_name is None:
        return None

    expected_type = _declaration_symbol_type(node)
    candidates = [
        item
        for item in file_symbols
        if item.start_line == start_line
        and item.symbol_name == source_name
        and item.symbol_type == expected_type
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.id)
    return candidates[0].id


def _declaration_symbol_type(node: Any) -> str:
    """Map Swift declaration nodes to their top-level symbol type."""
    if node.type == "protocol_declaration":
        return "protocol"
    if node.children:
        keyword_type = node.children[0].type
        if keyword_type == "extension":
            return "extension"
        if keyword_type == "struct":
            return "struct"
        if keyword_type == "enum":
            return "enum"
    return "class"


def _inheritance_specifiers(node: Any) -> list[Any]:
    """Return Swift inheritance specifiers from a declaration."""
    return [child for child in node.named_children if child.type == "inheritance_specifier"]


def _inheritance_target_ref(node: Any) -> str | None:
    """Return the referenced type name from a Swift inheritance specifier."""
    for child in node.named_children:
        if child.type == "user_type":
            return child.text.decode("utf-8")
    return node.text.decode("utf-8").strip() or None


def _type_ref_parts(type_ref: str | None) -> tuple[str, ...] | None:
    """Return normalized type path parts from a Swift type reference."""
    if not type_ref:
        return None
    parts = tuple(part.strip() for part in type_ref.split(".") if part.strip())
    if not parts:
        return None
    return parts


def _inheritance_relation_type(
    source_symbol_type: str,
    target_symbol_id: str | None,
    symbol_by_id: dict[str, SymbolChunk],
) -> str:
    """Map Swift inheritance clauses to EXTENDS or IMPLEMENTS relations."""
    if source_symbol_type == "protocol":
        return "EXTENDS"
    if source_symbol_type in {"struct", "enum"}:
        return "IMPLEMENTS"
    if target_symbol_id is not None:
        target_symbol = symbol_by_id.get(target_symbol_id)
        if target_symbol is not None and target_symbol.symbol_type == "class":
            return "EXTENDS"
    return "IMPLEMENTS"


def _build_function_receiver_type_hints(
    function_node: Any,
    seed_hints: ReceiverTypeHints | None = None,
) -> dict[str, tuple[str, ...]]:
    """Infer local Swift receiver types from parameters and simple bindings."""
    hints: dict[str, tuple[str, ...]] = dict(seed_hints or {})

    for child in function_node.named_children:
        if child.type == "parameter":
            parameter_name = _parameter_local_name(child)
            parameter_type = _type_parts_from_node(child)
            if parameter_name is not None and parameter_type is not None:
                hints[parameter_name] = parameter_type
            continue
        if child.type != "function_body":
            continue
        for statement in _iter_descendants(child):
            if statement.type == "property_declaration":
                binding_name = _binding_name(statement)
                if binding_name is None:
                    continue
                explicit_type = _type_parts_from_node(statement)
                if explicit_type is not None:
                    hints[binding_name] = explicit_type
                    continue
                initializer_name = _binding_initializer_identifier(statement)
                if initializer_name is None:
                    continue
                inferred_type = hints.get(initializer_name)
                if inferred_type is not None:
                    hints[binding_name] = inferred_type
                continue
            if statement.type not in {"if_statement", "guard_statement"}:
                continue
            conditional_binding = _conditional_binding_alias(statement)
            if conditional_binding is None:
                continue
            binding_name, initializer_name = conditional_binding
            inferred_type = hints.get(initializer_name)
            if inferred_type is not None:
                hints[binding_name] = inferred_type

    return hints


def _build_enclosing_type_receiver_type_hints(
    function_node: Any,
) -> dict[str, tuple[str, ...]]:
    """Infer receiver types from the nearest enclosing Swift type body."""
    body_node = function_node.parent
    if body_node is None or body_node.type not in {
        "class_body",
        "enum_class_body",
        "protocol_body",
    }:
        return {}

    return _build_property_receiver_type_hints(body_node)


def _build_declared_type_receiver_type_hints(
    function_node: Any,
    file_path: str,
    imported_modules: set[str],
    type_receiver_hints_index: TypeReceiverHintsIndex,
) -> ReceiverTypeHints:
    """Infer receiver types from the declared Swift type across sibling files."""
    declaration_node = _enclosing_type_declaration(function_node)
    if declaration_node is None:
        return {}
    type_name = _declaration_type_name(declaration_node)
    if type_name is None:
        return {}
    associated_type_bindings = _associated_type_bindings_for_declaration(
        declaration_node
    )
    return _resolve_type_receiver_hints(
        type_name,
        file_path,
        imported_modules,
        type_receiver_hints_index,
        visited_type_names=set(),
        explicit_module_hint=None,
        associated_type_bindings=associated_type_bindings,
    )


def _resolve_type_receiver_hints(
    type_name: str,
    file_path: str,
    imported_modules: set[str],
    type_receiver_hints_index: TypeReceiverHintsIndex,
    visited_type_names: set[str],
    explicit_module_hint: tuple[str, ...] | None,
    associated_type_bindings: AssociatedTypeBindings,
) -> ReceiverTypeHints:
    """Resolve typed properties for a Swift type and its direct ancestors."""
    if type_name in visited_type_names:
        return {}

    selected_records = _select_type_receiver_records(
        type_name,
        file_path,
        imported_modules,
        type_receiver_hints_index,
        explicit_module_hint,
    )
    if not selected_records:
        return {}

    resolved_hints: ReceiverTypeHints = {}
    next_visited = set(visited_type_names)
    next_visited.add(type_name)

    for (
        candidate_path,
        property_hints,
        inherited_type_refs,
        candidate_imports,
        candidate_associated_type_bindings,
    ) in selected_records:
        candidate_bindings = dict(associated_type_bindings)
        candidate_bindings.update(candidate_associated_type_bindings)
        for inherited_type_ref in inherited_type_refs:
            inherited_name = inherited_type_ref[-1]
            resolved_hints.update(
                _resolve_type_receiver_hints(
                    inherited_name,
                    candidate_path,
                    set(candidate_imports),
                    type_receiver_hints_index,
                    next_visited,
                    inherited_type_ref[:-1] or None,
                    associated_type_bindings=candidate_bindings,
                )
            )
        resolved_hints.update(
            {
                binding_name: _qualify_receiver_type_hint(
                    _substitute_associated_type_hint(
                        type_parts,
                        candidate_bindings,
                    ),
                    candidate_path,
                    set(candidate_imports),
                    type_receiver_hints_index,
                )
                for binding_name, type_parts in property_hints.items()
            }
        )

    return resolved_hints


def _select_type_receiver_records(
    type_name: str,
    file_path: str,
    imported_modules: set[str],
    type_receiver_hints_index: TypeReceiverHintsIndex,
    explicit_module_hint: tuple[str, ...] | None,
) -> list[TypeReceiverRecord]:
    """Select the closest Swift type receiver records for a given type name."""
    candidates = type_receiver_hints_index.get(type_name, [])
    if not candidates:
        return []

    if explicit_module_hint:
        explicit_matches = [
            record
            for record in candidates
            if _path_matches_module_hint(record[0], explicit_module_hint)
        ]
        if explicit_matches:
            return explicit_matches

    current_parent = PurePosixPath(file_path).parent
    sibling_matches = [
        record
        for record in candidates
        if PurePosixPath(record[0]).parent == current_parent
    ]
    if sibling_matches:
        return sibling_matches

    if imported_modules:
        import_module_matches = [
            record
            for record in candidates
            if _path_matches_imported_module(record[0], imported_modules)
        ]
        if len(import_module_matches) == 1:
            return import_module_matches
    if len(candidates) == 1:
        return candidates
    return []


def _path_matches_imported_module(path: str, imported_modules: set[str]) -> bool:
    """Check whether a path appears to belong to an imported Swift module."""
    path_parts = {part for part in PurePosixPath(path).parts if part}
    return any(module_name in path_parts for module_name in imported_modules)


def _path_matches_module_hint(path: str, module_hints: tuple[str, ...]) -> bool:
    """Check whether a path contains all explicit module/type hints."""
    path_parts = {part for part in PurePosixPath(path).parts if part}
    return all(part in path_parts for part in module_hints)


def _qualify_receiver_type_hint(
    type_parts: tuple[str, ...],
    file_path: str,
    imported_modules: set[str],
    type_receiver_hints_index: TypeReceiverHintsIndex,
) -> tuple[str, ...]:
    """Qualify a Swift receiver type using the declaring file imports when possible."""
    if len(type_parts) != 1:
        return type_parts

    candidate_type_name = type_parts[0]
    selected_records = _select_type_receiver_records(
        candidate_type_name,
        file_path,
        imported_modules,
        type_receiver_hints_index,
        None,
    )
    if len(selected_records) != 1:
        return type_parts

    selected_path = selected_records[0][0]
    matching_modules = tuple(
        module_name
        for module_name in imported_modules
        if module_name in PurePosixPath(selected_path).parts
    )
    if len(matching_modules) != 1:
        return type_parts
    return (matching_modules[0], candidate_type_name)


def _substitute_associated_type_hint(
    type_parts: tuple[str, ...],
    associated_type_bindings: AssociatedTypeBindings,
) -> tuple[str, ...]:
    """Replace a Swift associated type placeholder with its constrained type."""
    if len(type_parts) != 1:
        return type_parts
    return associated_type_bindings.get(type_parts[0], type_parts)


def _associated_type_bindings_for_declaration(
    node: Any,
) -> AssociatedTypeBindings:
    """Collect Swift associated type bindings from declarations and where clauses."""
    bindings: AssociatedTypeBindings = {}
    body_node = _declaration_body_node(node)
    if body_node is not None:
        for child in body_node.named_children:
            if child.type != "associatedtype_declaration":
                continue
            binding = _associated_type_binding_from_declaration(child)
            if binding is None:
                continue
            bindings[binding[0]] = binding[1]

    for child in node.named_children:
        if child.type != "type_constraints":
            continue
        bindings.update(_associated_type_bindings_from_constraints(child))

    return bindings


def _associated_type_binding_from_declaration(
    node: Any,
) -> tuple[str, tuple[str, ...]] | None:
    """Return a constrained associated type binding from one declaration."""
    binding_name: str | None = None
    binding_type: tuple[str, ...] | None = None
    for child in node.named_children:
        if child.type == "type_identifier" and binding_name is None:
            binding_name = child.text.decode("utf-8")
            continue
        if child.type == "user_type":
            binding_type = _type_parts_from_node(child)
    if binding_name is None or binding_type is None:
        return None
    return binding_name, binding_type


def _associated_type_bindings_from_constraints(
    node: Any,
) -> AssociatedTypeBindings:
    """Return associated type bindings expressed in a Swift where clause."""
    bindings: AssociatedTypeBindings = {}
    for child in node.named_children:
        if child.type != "type_constraint":
            continue
        binding = _associated_type_binding_from_constraint(child)
        if binding is None:
            continue
        bindings[binding[0]] = binding[1]
    return bindings


def _associated_type_binding_from_constraint(
    node: Any,
) -> tuple[str, tuple[str, ...]] | None:
    """Return one associated type binding from a Swift constraint node."""
    constraint_node = node.named_children[0] if node.named_children else node
    if constraint_node.type not in {"inheritance_constraint", "equality_constraint"}:
        return None

    binding_name: str | None = None
    binding_type: tuple[str, ...] | None = None
    for child in constraint_node.named_children:
        if child.type == "identifier" and binding_name is None:
            binding_name = child.text.decode("utf-8")
            continue
        if child.type == "user_type":
            binding_type = _type_parts_from_node(child)
    if binding_name is None or binding_type is None:
        return None
    return binding_name, binding_type


def _build_property_receiver_type_hints(
    body_node: Any,
) -> ReceiverTypeHints:
    """Infer receiver types from Swift property declarations in a body node."""

    hints: ReceiverTypeHints = {}
    for child in body_node.named_children:
        if child.type not in {
            "property_declaration",
            "protocol_property_declaration",
        }:
            continue
        binding_name = _binding_name(child)
        if binding_name is None:
            continue
        explicit_type = _type_parts_from_node(child)
        if explicit_type is not None:
            hints[binding_name] = explicit_type
            continue
        initializer_name = _binding_initializer_identifier(child)
        if initializer_name is None:
            continue
        inferred_type = hints.get(initializer_name)
        if inferred_type is not None:
            hints[binding_name] = inferred_type

    return hints


def _enclosing_type_declaration(function_node: Any) -> Any | None:
    """Return the nearest enclosing Swift type or extension declaration."""
    body_node = function_node.parent
    if body_node is None:
        return None
    declaration_node = body_node.parent
    if declaration_node is None:
        return None
    if declaration_node.type not in {"class_declaration", "protocol_declaration"}:
        return None
    return declaration_node


def _declaration_body_node(node: Any) -> Any | None:
    """Return the body node for a Swift type-like declaration."""
    for child in node.named_children:
        if child.type in {"class_body", "enum_class_body", "protocol_body"}:
            return child
    return None


def _parameter_local_name(node: Any) -> str | None:
    """Return the effective local parameter name from a Swift parameter node."""
    identifiers = [
        child.text.decode("utf-8")
        for child in node.named_children
        if child.type == "simple_identifier"
    ]
    if not identifiers:
        return None
    return identifiers[-1]


def _binding_name(node: Any) -> str | None:
    """Return the local binding name from a Swift property declaration."""
    for child in node.named_children:
        if child.type != "pattern":
            continue
        for descendant in _iter_descendants(child):
            if descendant.type == "simple_identifier":
                return descendant.text.decode("utf-8")
        pattern_text = child.text.decode("utf-8").strip()
        if pattern_text:
            return pattern_text.split()[-1]
    return None


def _binding_initializer_identifier(node: Any) -> str | None:
    """Return a simple identifier initializer from a Swift binding."""
    seen_pattern = False
    for child in node.named_children:
        if child.type == "pattern":
            seen_pattern = True
            continue
        if not seen_pattern:
            continue
        initializer_name = _initializer_identifier_text(child)
        if initializer_name is not None:
            return initializer_name
    return None


def _conditional_binding_alias(node: Any) -> tuple[str, str] | None:
    """Return a local alias introduced by a Swift if/guard value binding."""
    saw_value_binding = False
    binding_name: str | None = None
    for child in node.named_children:
        if child.type == "value_binding_pattern":
            saw_value_binding = True
            continue
        if not saw_value_binding:
            continue
        if binding_name is None and child.type == "simple_identifier":
            binding_name = child.text.decode("utf-8")
            continue
        initializer_name = _initializer_identifier_text(child)
        if binding_name is not None and initializer_name is not None:
            return binding_name, initializer_name
    return None


def _initializer_identifier_text(node: Any) -> str | None:
    """Return a simple local identifier from a Swift initializer expression."""
    if node.type in {"simple_identifier", "navigation_expression", "call_expression"}:
        return _receiver_identifier_text(node.text.decode("utf-8"))
    return None


def _type_parts_from_node(node: Any) -> tuple[str, ...] | None:
    """Return normalized Swift type parts from a node containing a type."""
    if node.type == "optional_type":
        for child in node.named_children:
            type_parts = _type_parts_from_node(child)
            if type_parts is not None:
                return type_parts
        return None
    if node.type == "array_type":
        for child in node.named_children:
            type_parts = _type_parts_from_node(child)
            if type_parts is not None:
                return type_parts
        return None
    if node.type == "type_arguments":
        for child in node.named_children:
            type_parts = _type_parts_from_node(child)
            if type_parts is not None:
                return type_parts
        return None

    for child in node.named_children:
        if child.type in {
            "user_type",
            "type_annotation",
            "optional_type",
            "array_type",
            "type_arguments",
        }:
            type_parts = _type_parts_from_node(child)
            if type_parts is not None:
                return type_parts
        if child.type == "type_identifier":
            if node.type == "user_type":
                type_name = child.text.decode("utf-8")
                if type_name in {"Array", "Optional"}:
                    for grandchild in node.named_children:
                        if grandchild.type == "type_arguments":
                            wrapped_type = _type_parts_from_node(grandchild)
                            if wrapped_type is not None:
                                return wrapped_type
            parts = tuple(
                grandchild.text.decode("utf-8")
                for grandchild in node.named_children
                if grandchild.type == "type_identifier"
            )
            if parts:
                return parts
    return None


def _call_target_parts(
    node: Any,
) -> tuple[str | None, tuple[str, ...] | None, str | None]:
    """Return the callee name and owner hint from a Swift call expression."""
    if not node.named_children:
        return None, None, None
    callee = node.named_children[0]
    callee_text = callee.text.decode("utf-8").strip()
    if callee.type == "simple_identifier":
        return callee_text, None, None
    if callee.type == "navigation_expression":
        parts = tuple(part.strip() for part in callee_text.split(".") if part.strip())
        if len(parts) >= 2:
            owner_hint = parts[:-1]
            target_ref = parts[-1]
            if owner_hint and any(part[:1].isupper() for part in owner_hint):
                return target_ref, owner_hint, _receiver_identifier_text(owner_hint[-1])
            return target_ref, None, _receiver_identifier_text(owner_hint[-1])
    match = _TRAILING_IDENTIFIER_PATTERN.search(callee_text)
    if not match:
        return None, None, None
    return match.group(1), None, None


def _receiver_identifier_text(text: str) -> str | None:
    """Return the base local receiver identifier from a Swift expression string."""
    normalized = text.strip()
    if not normalized:
        return None
    if normalized.startswith("self."):
        normalized = normalized[len("self.") :]

    while normalized:
        updated = normalized.rstrip("?!")
        if "[" in updated:
            updated = updated.split("[", maxsplit=1)[0].strip()
        updated = updated.rstrip("?!")

        for accessor in (".first", ".last", ".lazy"):
            if updated.endswith(accessor):
                updated = updated[: -len(accessor)].rstrip("?!")
                break
        else:
            wrapper_match = _COLLECTION_WRAPPER_CALL_PATTERN.search(updated)
            if wrapper_match is not None:
                updated = updated[: wrapper_match.start()].rstrip("?!")
            else:
                wrapper_match = _COLLECTION_WRAPPER_BLOCK_PATTERN.search(updated)
                if wrapper_match is not None:
                    updated = updated[: wrapper_match.start()].rstrip("?!")

        if updated == normalized:
            break
        normalized = updated

    match = _TRAILING_IDENTIFIER_PATTERN.search(normalized)
    if not match:
        return None
    return match.group(1)


def _iter_descendants(node: Any) -> Iterator[Any]:
    """Yield the node and all named descendants depth-first."""
    yield node
    for child in node.named_children:
        yield from _iter_descendants(child)