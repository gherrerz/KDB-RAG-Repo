"""Extracción semántica fase 1 para JavaScript basada en patrones."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from coderag.core.settings import get_settings
from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.ingestion.module_resolver import (
    build_js_export_index,
    load_tsconfig_paths,
    normalize_js_import_path,
)

_IMPORT_PATTERN = re.compile(
    r"^\s*import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]"
)
_CLASS_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s+extends\s+([A-Za-z_$][A-Za-z0-9_$]*))?"
)
_CALL_PATTERN = re.compile(
    r"([A-Za-z_$][A-Za-z0-9_$]*(?:\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)?)\s*\("
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
    "function",
}


def _javascript_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filtra archivos JavaScript del escaneo."""
    return [item for item in scanned_files if item.language in {"javascript", "js"}]


def _build_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[dict[str, list[SymbolChunk]], dict[str, list[str]]]:
    """Construye índices de símbolos JavaScript por archivo y nombre."""
    by_file: dict[str, list[SymbolChunk]] = {}
    global_by_name: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol.language not in {"javascript", "js"}:
            continue
        by_file.setdefault(symbol.path, []).append(symbol)
        global_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)
    return by_file, global_by_name


def _parse_import_bindings(
    file_obj: ScannedFile,
    export_index: dict[str, dict[str, str]],
    scanned_paths: set[str],
    *,
    tsconfig_paths: dict[str, str],
    tsconfig_base_url: str | None,
) -> dict[str, str | dict[str, str]]:
    """Parse JS imports into local bindings that point to exported symbols."""
    bindings: dict[str, str | dict[str, str]] = {}

    for line in file_obj.content.splitlines():
        import_match = _IMPORT_PATTERN.match(line)
        if not import_match:
            continue
        raw_target = import_match.group(1).strip()
        resolved_path = normalize_js_import_path(
            file_obj.path,
            raw_target,
            scanned_paths,
            tsconfig_paths=tsconfig_paths,
            tsconfig_base_url=tsconfig_base_url,
        )
        if not resolved_path:
            continue
        target_exports = export_index.get(resolved_path, {})
        import_clause = line.split("from", maxsplit=1)[0]
        import_clause = import_clause.replace("import", "", 1).strip()
        if not import_clause:
            continue

        namespace_match = re.match(
            r"^\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)$",
            import_clause,
        )
        if namespace_match:
            bindings[namespace_match.group(1)] = dict(target_exports)
            continue

        named_match = re.search(r"\{([^}]*)\}", import_clause)
        if named_match:
            for item in named_match.group(1).split(","):
                spec = item.strip()
                if not spec:
                    continue
                if " as " in spec:
                    export_name, local_name = [
                        part.strip() for part in spec.split(" as ", maxsplit=1)
                    ]
                else:
                    export_name = spec
                    local_name = spec
                symbol_id = target_exports.get(export_name)
                if symbol_id:
                    bindings[local_name] = symbol_id

        default_clause = import_clause.split(",", maxsplit=1)[0].strip()
        if default_clause and not default_clause.startswith("{"):
            symbol_id = target_exports.get("default")
            if symbol_id:
                bindings[default_clause] = symbol_id

    return bindings


def _resolve_source_symbol_id(line: int, file_symbols: list[SymbolChunk]) -> str | None:
    """Resuelve el símbolo fuente más interno que contiene la línea."""
    candidates = [
        item
        for item in file_symbols
        if item.start_line <= line <= item.end_line
        and item.symbol_type in {"class", "method", "function"}
    ]
    if not candidates:
        class_candidates = [item for item in file_symbols if item.symbol_type == "class"]
        if not class_candidates:
            return None
        class_candidates.sort(key=lambda item: item.start_line)
        return class_candidates[0].id
    candidates.sort(key=lambda item: (item.end_line - item.start_line, item.start_line))
    return candidates[0].id


def _resolve_target_symbol_id(
    *,
    target_ref: str,
    file_symbols: list[SymbolChunk],
    global_by_name: dict[str, list[str]],
    import_bindings: dict[str, str | dict[str, str]],
) -> tuple[str | None, str]:
    """Resuelve target por nombre local y fallback global único."""
    if not target_ref:
        return None, "unresolved"

    if "." in target_ref:
        root_name, member_name = target_ref.split(".", maxsplit=1)
        namespace_binding = import_bindings.get(root_name)
        if isinstance(namespace_binding, dict):
            namespace_target = namespace_binding.get(member_name)
            if namespace_target:
                return namespace_target, "namespace_import"

    bound_target = import_bindings.get(target_ref)
    if isinstance(bound_target, str):
        return bound_target, "import_binding"

    local_matches = [item.id for item in file_symbols if item.symbol_name == target_ref]
    if local_matches:
        return local_matches[0], "local"

    global_matches = global_by_name.get(target_ref, [])
    if len(global_matches) == 1:
        return global_matches[0], "global_unique"
    return None, "unresolved"


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
    file_symbols: list[SymbolChunk],
    global_by_name: dict[str, list[str]],
    import_bindings: dict[str, str | dict[str, str]],
) -> str | None:
    """Agrega relación JavaScript con deduplicación y retorna origen resolución."""
    if not source_symbol_id or not target_ref:
        return None

    target_symbol_id, resolution_source = _resolve_target_symbol_id(
        target_ref=target_ref,
        file_symbols=file_symbols,
        global_by_name=global_by_name,
        import_bindings=import_bindings,
    )
    if resolution_source == "global_unique":
        confidence = min(confidence, 0.55)

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
            language="javascript",
            resolution_method=resolution_source,
        )
    )
    return resolution_source


def extract_javascript_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int] | None = None,
) -> list[SemanticRelation]:
    """Extrae relaciones JavaScript fase 1: IMPORTS, EXTENDS y CALLS."""
    by_file_symbols, global_by_name = _build_symbol_indexes(symbols)
    settings = get_settings()
    tsconfig_base_url: str | None = None
    tsconfig_paths: dict[str, str] = {}
    if bool(getattr(settings, "semantic_tsconfig_resolution_enabled", False)):
        tsconfig_base_url, tsconfig_paths = load_tsconfig_paths(scanned_files)
    export_index = build_js_export_index(
        scanned_files,
        symbols,
        languages={"javascript", "js"},
        tsconfig_paths=tsconfig_paths,
        tsconfig_base_url=tsconfig_base_url,
    )
    scanned_paths = {item.path for item in _javascript_files(scanned_files)}
    relations: list[SemanticRelation] = []
    resolution_source_counts: Counter[str] = Counter()

    for file_obj in _javascript_files(scanned_files):
        file_symbols = by_file_symbols.get(file_obj.path, [])
        seen: set[tuple[str, str, str, int]] = set()
        import_bindings = _parse_import_bindings(
            file_obj,
            export_index,
            scanned_paths,
            tsconfig_paths=tsconfig_paths,
            tsconfig_base_url=tsconfig_base_url,
        )

        for line_number, line in enumerate(file_obj.content.splitlines(), start=1):
            source_symbol_id = _resolve_source_symbol_id(line_number, file_symbols)

            import_match = _IMPORT_PATTERN.match(line)
            if import_match:
                raw_target = import_match.group(1).strip()
                target_ref = normalize_js_import_path(
                    file_obj.path,
                    raw_target,
                    scanned_paths,
                    tsconfig_paths=tsconfig_paths,
                    tsconfig_base_url=tsconfig_base_url,
                ) or raw_target
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
                    file_symbols=file_symbols,
                    global_by_name=global_by_name,
                    import_bindings=import_bindings,
                )
                if source:
                    resolution_source_counts[source] += 1

            class_match = _CLASS_PATTERN.match(line)
            if class_match:
                extends_target = (class_match.group(2) or "").strip()
                if extends_target:
                    source = _append_relation(
                        relations,
                        seen,
                        repo_id=repo_id,
                        path=file_obj.path,
                        source_symbol_id=source_symbol_id,
                        relation_type="EXTENDS",
                        target_ref=extends_target,
                        line=line_number,
                        confidence=0.95,
                        file_symbols=file_symbols,
                        global_by_name=global_by_name,
                        import_bindings=import_bindings,
                    )
                    if source:
                        resolution_source_counts[source] += 1

            for call_match in _CALL_PATTERN.finditer(line):
                target_ref = re.sub(r"\s+", "", call_match.group(1))
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
                    file_symbols=file_symbols,
                    global_by_name=global_by_name,
                    import_bindings=import_bindings,
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