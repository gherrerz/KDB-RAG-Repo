"""Constructores de fragmentos para símbolos, archivos y módulos."""

import hashlib
import json
import re

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.core.settings import get_settings
from coderag.ingestion.extractors import DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY


def _chunk_id(repo_id: str, path: str, name: str, start_line: int) -> str:
    """Cree una ID de fragmento determinista para fragmentos de símbolos."""
    value = f"{repo_id}:{path}:{name}:{start_line}".encode("utf-8")
    return hashlib.sha1(value, usedforsecurity=False).hexdigest()


def _slice_snippet(lines: list[str], start_line: int, end_line: int) -> str:
    """Return source text for an inclusive 1-indexed line range."""
    if not lines:
        return ""
    safe_start = max(1, min(start_line, len(lines)))
    safe_end = max(safe_start, min(end_line, len(lines)))
    return "\n".join(lines[safe_start - 1 : safe_end])


def _line_indent(line: str) -> int:
    """Return indentation width for a single line."""
    return len(line) - len(line.lstrip())


def _localized_block_end(
    lines: list[str],
    start_line: int,
    *,
    max_lines: int = 6,
) -> int:
    """Return a compact block span around a config key based on indentation."""
    if not lines:
        return start_line
    safe_start = max(1, min(start_line, len(lines)))
    base_indent = _line_indent(lines[safe_start - 1])
    end_line = safe_start
    scanned_lines = 1
    for index in range(safe_start, len(lines)):
        if scanned_lines >= max_lines:
            break
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            break
        current_indent = _line_indent(line)
        if current_indent <= base_indent:
            break
        end_line = index + 1
        scanned_lines += 1
    return end_line


def _json_value_span(lines: list[str], start_line: int) -> int:
    """Return a localized JSON span for the value attached to a key line."""
    if not lines:
        return start_line
    safe_start = max(1, min(start_line, len(lines)))
    current_line = lines[safe_start - 1]
    balance = 0
    in_string = False
    escaped = False
    seen_value_opener = False

    for index in range(safe_start - 1, len(lines)):
        line = lines[index]
        for char in line:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char in "[{":
                balance += 1
                seen_value_opener = True
            elif char in "]}":
                balance -= 1

        if index == safe_start - 1 and ":" not in current_line:
            return safe_start
        if not seen_value_opener:
            return safe_start
        if balance <= 0:
            return index + 1
    return min(len(lines), safe_start + 5)


def _extract_code_language_symbols(
    repo_id: str,
    file_obj: ScannedFile,
    lines: list[str],
) -> list[SymbolChunk]:
    """Extract symbols for a code-like language via registry strategies."""
    extractor = DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY.get(file_obj.language)
    detections = extractor.detect_symbols(file_obj.content, path=file_obj.path)
    chunks: list[SymbolChunk] = []

    for detection in detections:
        span = extractor.resolve_span(file_obj.content, detection)
        snippet = _slice_snippet(lines, span.start_line, span.end_line)
        chunks.append(
            SymbolChunk(
                id=_chunk_id(
                    repo_id,
                    file_obj.path,
                    detection.symbol_name,
                    detection.start_line,
                ),
                repo_id=repo_id,
                path=file_obj.path,
                language=file_obj.language,
                symbol_name=detection.symbol_name,
                symbol_type=detection.symbol_type,
                start_line=span.start_line,
                end_line=span.end_line,
                snippet=snippet,
            )
        )

    return chunks


def _extract_code_language_symbols_legacy(
    repo_id: str,
    file_obj: ScannedFile,
    lines: list[str],
) -> list[SymbolChunk]:
    """Extract symbols with the legacy fixed-window regex strategy."""
    chunks: list[SymbolChunk] = []
    for index, line in enumerate(lines):
        py_match = re.match(r"\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        js_match = re.match(r"\s*function\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        java_match = re.match(
            r"\s*(public|private|protected)?\s*(class|interface)\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)",
            line,
        )
        java_method_match = re.match(
            r"\s*(public|private|protected)?\s*(static\s+)?"
            r"([A-Za-z_][A-Za-z0-9_<>\[\]]*\s+)+"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(\{|$)",
            line,
        )
        java_constructor_match = re.match(
            r"\s*(public|private|protected)\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(\{|$)",
            line,
        )

        symbol_type = ""
        symbol_name = ""
        if py_match:
            symbol_type = "class" if py_match.group(1) == "class" else "function"
            symbol_name = py_match.group(2)
        elif js_match:
            symbol_type = "function"
            symbol_name = js_match.group(1)
        elif java_match:
            symbol_type = java_match.group(2)
            symbol_name = java_match.group(3)
        elif file_obj.language == "java" and java_constructor_match:
            symbol_type = "constructor"
            symbol_name = java_constructor_match.group(2)
        elif file_obj.language == "java" and java_method_match:
            symbol_type = "method"
            symbol_name = java_method_match.group(4)
        else:
            continue

        start_line = index + 1
        end_line = min(start_line + 30, len(lines))
        chunks.append(
            SymbolChunk(
                id=_chunk_id(repo_id, file_obj.path, symbol_name, start_line),
                repo_id=repo_id,
                path=file_obj.path,
                language=file_obj.language,
                symbol_name=symbol_name,
                symbol_type=symbol_type,
                start_line=start_line,
                end_line=end_line,
                snippet=_slice_snippet(lines, start_line, end_line),
            )
        )

    return chunks


def _is_symbol_extractor_v2_enabled() -> bool:
    """Return whether modular symbol extraction is enabled in runtime settings."""
    settings = get_settings()
    return bool(getattr(settings, "symbol_extractor_v2_enabled", True))


def extract_symbol_chunks(repo_id: str, scanned_files: list[ScannedFile]) -> list[SymbolChunk]:
    """Extract symbol-level chunks using language-specific extractor strategies."""
    chunks: list[SymbolChunk] = []
    use_modular = _is_symbol_extractor_v2_enabled()

    for file_obj in scanned_files:
        lines = file_obj.content.splitlines()
        if use_modular:
            code_chunks = _extract_code_language_symbols(repo_id, file_obj, lines)
        else:
            code_chunks = _extract_code_language_symbols_legacy(
                repo_id,
                file_obj,
                lines,
            )
        chunks.extend(code_chunks)
        if code_chunks:
            continue

        if file_obj.language == "markdown":
            for index, line in enumerate(lines):
                heading_match = re.match(r"\s{0,3}#{1,6}\s+(.+)", line)
                if not heading_match:
                    continue
                heading = heading_match.group(1).strip()
                if not heading:
                    continue
                start_line = index + 1
                end_line = min(start_line + 20, len(lines))
                snippet = _slice_snippet(lines, start_line, end_line)
                chunks.append(
                    SymbolChunk(
                        id=_chunk_id(repo_id, file_obj.path, heading, start_line),
                        repo_id=repo_id,
                        path=file_obj.path,
                        language=file_obj.language,
                        symbol_name=heading,
                        symbol_type="section",
                        start_line=start_line,
                        end_line=end_line,
                        snippet=snippet,
                    )
                )
            continue

        if file_obj.language in {"yaml", "toml"}:
            pattern = r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]"
            for index, line in enumerate(lines):
                key_match = re.match(pattern, line)
                if not key_match:
                    continue
                key_name = key_match.group(1)
                start_line = index + 1
                end_line = _localized_block_end(lines, start_line)
                snippet = _slice_snippet(lines, start_line, end_line)
                chunks.append(
                    SymbolChunk(
                        id=_chunk_id(repo_id, file_obj.path, key_name, start_line),
                        repo_id=repo_id,
                        path=file_obj.path,
                        language=file_obj.language,
                        symbol_name=key_name,
                        symbol_type="config_key",
                        start_line=start_line,
                        end_line=end_line,
                        snippet=snippet,
                    )
                )
            continue

        if file_obj.language == "json":
            try:
                payload = json.loads(file_obj.content)
            except Exception:
                payload = None

            if isinstance(payload, dict):
                for key_name in list(payload.keys())[:40]:
                    key_pattern = rf'"{re.escape(str(key_name))}"\s*:'
                    start_line = 1
                    for index, line in enumerate(lines):
                        if re.search(key_pattern, line):
                            start_line = index + 1
                            break
                    end_line = _json_value_span(lines, start_line)
                    snippet = _slice_snippet(lines, start_line, end_line)
                    chunks.append(
                        SymbolChunk(
                            id=_chunk_id(
                                repo_id,
                                file_obj.path,
                                str(key_name),
                                start_line,
                            ),
                            repo_id=repo_id,
                            path=file_obj.path,
                            language=file_obj.language,
                            symbol_name=str(key_name),
                            symbol_type="config_key",
                            start_line=start_line,
                            end_line=end_line,
                            snippet=snippet,
                        )
                    )
    return chunks
