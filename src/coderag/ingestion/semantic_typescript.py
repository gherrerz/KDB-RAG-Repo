"""Extracción semántica fase 1 para TypeScript basada en patrones."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk

_IMPORT_PATTERN = re.compile(
    r"^\s*import\s+(?:type\s+)?(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]"
)
_CLASS_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s+extends\s+([A-Za-z_$][A-Za-z0-9_$]*))?"
    r"(?:\s+implements\s+([^\{]+))?"
)
_INTERFACE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?interface\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s+extends\s+([^\{]+))?"
)
_CALL_PATTERN = re.compile(
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
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


def _typescript_files(scanned_files: Iterable[ScannedFile]) -> list[ScannedFile]:
    """Filtra archivos TypeScript del escaneo."""
    return [item for item in scanned_files if item.language in {"typescript", "ts"}]


def _build_symbol_indexes(
    symbols: list[SymbolChunk],
) -> tuple[dict[str, list[SymbolChunk]], dict[str, list[str]]]:
    """Construye índices de símbolos TypeScript por archivo y nombre."""
    by_file: dict[str, list[SymbolChunk]] = {}
    global_by_name: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol.language not in {"typescript", "ts"}:
            continue
        by_file.setdefault(symbol.path, []).append(symbol)
        global_by_name.setdefault(symbol.symbol_name, []).append(symbol.id)
    return by_file, global_by_name


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
) -> str | None:
    """Resuelve target por nombre local y fallback global único."""
    if not target_ref:
        return None

    local_matches = [
        item.id for item in file_symbols if item.symbol_name == target_ref
    ]
    if local_matches:
        return local_matches[0]

    global_matches = global_by_name.get(target_ref, [])
    if len(global_matches) == 1:
        return global_matches[0]
    return None


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
) -> str | None:
    """Agrega relación TypeScript con deduplicación y retorna origen resolución."""
    if not source_symbol_id or not target_ref:
        return None

    target_symbol_id = _resolve_target_symbol_id(
        target_ref=target_ref,
        file_symbols=file_symbols,
        global_by_name=global_by_name,
    )
    resolution_source = "local" if target_symbol_id else "unresolved"

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
            language="typescript",
        )
    )
    return resolution_source


def extract_typescript_semantic_relations(
    repo_id: str,
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    resolution_stats_sink: dict[str, int] | None = None,
) -> list[SemanticRelation]:
    """Extrae relaciones TypeScript fase 1: IMPORTS, EXTENDS/IMPLEMENTS y CALLS."""
    by_file_symbols, global_by_name = _build_symbol_indexes(symbols)
    relations: list[SemanticRelation] = []
    resolution_source_counts: Counter[str] = Counter()

    for file_obj in _typescript_files(scanned_files):
        file_symbols = by_file_symbols.get(file_obj.path, [])
        seen: set[tuple[str, str, str, int]] = set()

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
                    file_symbols=file_symbols,
                    global_by_name=global_by_name,
                )
                if source:
                    resolution_source_counts[source] += 1

            class_match = _CLASS_PATTERN.match(line)
            if class_match:
                extends_target = (class_match.group(2) or "").strip()
                implements_targets = (class_match.group(3) or "").strip()

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
                    )
                    if source:
                        resolution_source_counts[source] += 1

                if implements_targets:
                    for raw_target in implements_targets.split(","):
                        target_ref = raw_target.strip()
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
                            file_symbols=file_symbols,
                            global_by_name=global_by_name,
                        )
                        if source:
                            resolution_source_counts[source] += 1

            interface_match = _INTERFACE_PATTERN.match(line)
            if interface_match:
                extends_targets = (interface_match.group(2) or "").strip()
                if extends_targets:
                    for raw_target in extends_targets.split(","):
                        target_ref = raw_target.strip()
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
                            file_symbols=file_symbols,
                            global_by_name=global_by_name,
                        )
                        if source:
                            resolution_source_counts[source] += 1

            for call_match in _CALL_PATTERN.finditer(line):
                target_ref = call_match.group(1)
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
