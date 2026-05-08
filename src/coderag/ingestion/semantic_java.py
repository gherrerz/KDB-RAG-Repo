"""Extracción semántica fase 1 para Java basada en patrones."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk

_IMPORT_PATTERN = re.compile(
    r"^\s*import\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_\.\*]*)\s*;"
)
_CLASS_PATTERN = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?"
    r"(?:abstract\s+|final\s+|sealed\s+|non-sealed\s+|static\s+)*"
    r"(class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+([^\{]+?))?"
    r"(?:\s+implements\s+([^\{]+?))?"
    r"\s*(?:\{|$)"
)
_METHOD_PATTERN = re.compile(
    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_<>\[\]?]*\s+)+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:throws\s+[^{]+)?"
    r"(?:\{.*)?$"
)
_CONSTRUCTOR_PATTERN = re.compile(
    r"^\s*(?:public|private|protected)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:throws\s+[^{]+)?"
    r"(?:\{.*)?$"
)
_CALL_PATTERN = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_CONTROL_FLOW_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "throw",
    "synchronized",
    "super",
    "this",
}

_PACKAGE_PATTERN = re.compile(
    r"^\s*package\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*;"
)
_STATIC_IMPORT_PREFIX_PATTERN = re.compile(r"^\s*import\s+static\s+")


def _java_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filtra archivos Java del escaneo."""
    return [item for item in scanned_files if item.language == "java"]


def _clean_type_name(raw: str) -> str:
    """Normaliza referencias de tipo Java removiendo genéricos y espacios."""
    value = re.sub(r"<[^>]*>", "", raw or "")
    value = value.replace("?", "").strip()
    if "." in value:
        value = value.split(".")[-1]
    return value


def _build_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[
    dict[str, list[SymbolChunk]],
    dict[str, dict[str, str]],
    dict[str, list[str]],
]:
    """Construye índices de símbolos Java por archivo y nombre."""
    by_file: dict[str, list[SymbolChunk]] = {}
    by_file_name: dict[str, dict[str, str]] = {}
    global_types_by_name: dict[str, list[str]] = {}

    for symbol in symbols:
        if symbol.language != "java":
            continue
        by_file.setdefault(symbol.path, []).append(symbol)
        by_file_name.setdefault(symbol.path, {})[symbol.symbol_name] = symbol.id
        if symbol.symbol_type in {"class", "interface", "enum", "record"}:
            global_types_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)

    return by_file, by_file_name, global_types_by_name


def _file_package(content: str) -> str | None:
    """Extrae package Java declarado en el archivo si existe."""
    for line in content.splitlines()[:80]:
        match = _PACKAGE_PATTERN.match(line)
        if match:
            return match.group(1)
    return None


def _build_path_to_package(scanned_files: list[ScannedFile]) -> dict[str, str | None]:
    """Construye mapping path -> package para archivos Java del repositorio."""
    mapping: dict[str, str | None] = {}
    for file_obj in _java_files(scanned_files):
        mapping[file_obj.path] = _file_package(file_obj.content)
    return mapping


def _build_fqcn_index(
    symbols: list[SymbolChunk],
    path_to_package: dict[str, str | None],
) -> dict[str, str]:
    """Construye índice fqcn -> symbol_id para tipos Java declarados."""
    fqcn_index: dict[str, str] = {}
    for symbol in symbols:
        if symbol.language != "java":
            continue
        if symbol.symbol_type not in {"class", "interface", "enum", "record"}:
            continue
        package_name = path_to_package.get(symbol.path)
        if package_name:
            fqcn = f"{package_name}.{symbol.symbol_name}"
            fqcn_index[fqcn] = symbol.id
    return fqcn_index


def _build_java_target_resolver(
    *,
    file_obj: ScannedFile,
    file_symbols: list[SymbolChunk],
    by_file_name: dict[str, dict[str, str]],
    global_types_by_name: dict[str, list[str]],
    path_to_package: dict[str, str | None],
    fqcn_index: dict[str, str],
) -> Callable[[str], tuple[str | None, str]]:
    """Crea resolvedor de targets Java con contexto de imports/package."""
    local_name_index = by_file_name.get(file_obj.path, {})
    package_name = path_to_package.get(file_obj.path)

    imported_fqcn_by_simple_name: dict[str, str] = {}
    wildcard_import_packages: list[str] = []
    static_member_owner_by_name: dict[str, str] = {}
    static_wildcard_owner_types: list[str] = []
    for line in file_obj.content.splitlines():
        match = _IMPORT_PATTERN.match(line)
        if not match:
            continue
        imported_ref = match.group(1).strip()
        is_static = bool(_STATIC_IMPORT_PREFIX_PATTERN.match(line))
        if is_static and imported_ref.endswith(".*"):
            static_wildcard_owner_types.append(imported_ref[:-2])
            continue
        if is_static and "." in imported_ref:
            owner_fqcn, member_name = imported_ref.rsplit(".", maxsplit=1)
            if member_name:
                static_member_owner_by_name[member_name] = owner_fqcn
            continue
        if imported_ref.endswith(".*"):
            wildcard_import_packages.append(imported_ref[:-2])
            continue
        simple_name = imported_ref.split(".")[-1]
        imported_fqcn_by_simple_name[simple_name] = imported_ref

    file_type_ids_by_name: dict[str, str] = {}
    for symbol in file_symbols:
        if symbol.symbol_type in {"class", "interface", "enum", "record"}:
            file_type_ids_by_name[symbol.symbol_name] = symbol.id

    def _resolve_target(target_ref: str) -> tuple[str | None, str]:
        """Resuelve target_ref con prioridad local -> import -> package -> global."""
        if not target_ref:
            return None, "unresolved"

        if "." in target_ref:
            direct = fqcn_index.get(target_ref)
            if direct:
                return direct, "fqcn"
            simple_name = target_ref.split(".")[-1]
        else:
            simple_name = target_ref

        local = local_name_index.get(simple_name)
        if local:
            return local, "local"

        local_type = file_type_ids_by_name.get(simple_name)
        if local_type:
            return local_type, "local_type"

        imported_fqcn = imported_fqcn_by_simple_name.get(simple_name)
        if imported_fqcn:
            imported = fqcn_index.get(imported_fqcn)
            if imported:
                return imported, "import"

        static_owner_fqcn = static_member_owner_by_name.get(simple_name)
        if static_owner_fqcn:
            static_owner = fqcn_index.get(static_owner_fqcn)
            if static_owner:
                return static_owner, "static_import_member"

        for static_owner_fqcn in static_wildcard_owner_types:
            static_owner = fqcn_index.get(static_owner_fqcn)
            if static_owner:
                return static_owner, "static_import_wildcard"

        for wildcard_package in wildcard_import_packages:
            wildcard_fqcn = f"{wildcard_package}.{simple_name}"
            wildcard_target = fqcn_index.get(wildcard_fqcn)
            if wildcard_target:
                return wildcard_target, "import_wildcard"

        if package_name:
            package_fqcn = f"{package_name}.{simple_name}"
            package_target = fqcn_index.get(package_fqcn)
            if package_target:
                return package_target, "same_package"

        global_ids = global_types_by_name.get(simple_name) or []
        if len(global_ids) == 1:
            return global_ids[0], "global_unique"
        return None, "unresolved"

    return _resolve_target


def _resolve_source_symbol_id(line: int, file_symbols: list[SymbolChunk]) -> str | None:
    """Resuelve el símbolo fuente más interno que contiene la línea dada."""
    candidates = [
        item
        for item in file_symbols
        if item.start_line <= line <= item.end_line
        and item.symbol_type in {
            "class",
            "interface",
            "enum",
            "record",
            "method",
            "constructor",
        }
    ]
    if not candidates:
        type_symbols = [
            item
            for item in file_symbols
            if item.symbol_type in {"class", "interface", "enum", "record"}
        ]
        if not type_symbols:
            return None
        type_symbols.sort(key=lambda item: item.start_line)
        return type_symbols[0].id
    candidates.sort(key=lambda item: (item.end_line - item.start_line, item.start_line))
    return candidates[0].id


def _is_declaration_line(line: str) -> bool:
    """Indica si la línea es una declaración de tipo o método Java."""
    return bool(
        _CLASS_PATTERN.match(line)
        or _METHOD_PATTERN.match(line)
        or _CONSTRUCTOR_PATTERN.match(line)
    )


def _append_relation(
    relations: list[SemanticRelation],
    seen: set[tuple[str, str, str, int]],
    *,
    repo_id: str,
    path: str,
    source_symbol_id: str | None,
    relation_type: str,
    target_ref: str,
    line: int,
    confidence: float,
    resolve_target: Callable[[str], tuple[str | None, str]],
) -> str | None:
    """Agrega una relación semántica Java con deduplicación."""
    if not source_symbol_id or not target_ref:
        return None

    target_symbol_id, resolution_source = resolve_target(target_ref)
    dedup_key = (
        source_symbol_id,
        relation_type,
        target_symbol_id or target_ref,
        line,
    )
    if dedup_key in seen:
        return None
    seen.add(dedup_key)

    relations.append(
        SemanticRelation(
            repo_id=repo_id,
            source_symbol_id=source_symbol_id,
            relation_type=relation_type,
            target_symbol_id=target_symbol_id,
            target_ref=target_ref,
            target_kind="symbol" if target_symbol_id else "external",
            path=path,
            line=max(1, line),
            confidence=confidence,
            language="java",
            resolution_method=resolution_source,
        )
    )
    return resolution_source


def extract_java_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int] | None = None,
) -> list[SemanticRelation]:
    """Extrae relaciones Java fase 1: IMPORTS, EXTENDS/IMPLEMENTS y CALLS."""
    by_file_symbols, by_file_name, global_types_by_name = _build_symbol_indexes(
        symbols
    )
    path_to_package = _build_path_to_package(scanned_files)
    fqcn_index = _build_fqcn_index(symbols, path_to_package)
    relations: list[SemanticRelation] = []
    resolution_source_counts: Counter[str] = Counter()

    for file_obj in _java_files(scanned_files):
        file_symbols = by_file_symbols.get(file_obj.path, [])
        seen: set[tuple[str, str, str, int]] = set()
        resolve_target = _build_java_target_resolver(
            file_obj=file_obj,
            file_symbols=file_symbols,
            by_file_name=by_file_name,
            global_types_by_name=global_types_by_name,
            path_to_package=path_to_package,
            fqcn_index=fqcn_index,
        )

        for line_number, line in enumerate(file_obj.content.splitlines(), start=1):
            source_symbol_id = _resolve_source_symbol_id(line_number, file_symbols)

            import_match = _IMPORT_PATTERN.match(line)
            if import_match:
                target_ref = import_match.group(1).strip()
                source = _append_relation(
                    relations,
                    seen,
                    repo_id=repo_id,
                    path=file_obj.path,
                    source_symbol_id=source_symbol_id,
                    relation_type="IMPORTS",
                    target_ref=target_ref,
                    line=line_number,
                    confidence=0.8,
                    resolve_target=resolve_target,
                )
                if source:
                    resolution_source_counts[source] += 1

            class_match = _CLASS_PATTERN.match(line)
            if class_match:
                class_kind = class_match.group(1)
                extends_part = class_match.group(3) or ""
                implements_part = class_match.group(4) or ""

                if extends_part:
                    for item in extends_part.split(","):
                        target_ref = _clean_type_name(item)
                        if not target_ref:
                            continue
                        source = _append_relation(
                            relations,
                            seen,
                            repo_id=repo_id,
                            path=file_obj.path,
                            source_symbol_id=source_symbol_id,
                            relation_type="EXTENDS",
                            target_ref=target_ref,
                            line=line_number,
                            confidence=0.95,
                            resolve_target=resolve_target,
                        )
                        if source:
                            resolution_source_counts[source] += 1

                if implements_part and class_kind == "class":
                    for item in implements_part.split(","):
                        target_ref = _clean_type_name(item)
                        if not target_ref:
                            continue
                        source = _append_relation(
                            relations,
                            seen,
                            repo_id=repo_id,
                            path=file_obj.path,
                            source_symbol_id=source_symbol_id,
                            relation_type="IMPLEMENTS",
                            target_ref=target_ref,
                            line=line_number,
                            confidence=0.95,
                            resolve_target=resolve_target,
                        )
                        if source:
                            resolution_source_counts[source] += 1
                elif class_kind == "interface" and extends_part:
                    # Interfaces use EXTENDS; no IMPLEMENTS edges are emitted.
                    pass

            if _is_declaration_line(line):
                continue

            for match in _CALL_PATTERN.finditer(line):
                target_ref = match.group(1)
                if not target_ref or target_ref.lower() in _CONTROL_FLOW_NAMES:
                    continue
                source = _append_relation(
                    relations,
                    seen,
                    repo_id=repo_id,
                    path=file_obj.path,
                    source_symbol_id=source_symbol_id,
                    relation_type="CALLS",
                    target_ref=target_ref,
                    line=line_number,
                    confidence=0.75,
                    resolve_target=resolve_target,
                )
                if source:
                    resolution_source_counts[source] += 1

    relations.sort(
        key=lambda item: (
            item.path,
            item.line,
            item.source_symbol_id,
            item.relation_type,
            item.target_ref,
        )
    )
    if resolution_stats_sink is not None:
        resolution_stats_sink.clear()
        resolution_stats_sink.update(dict(resolution_source_counts))
    return relations
