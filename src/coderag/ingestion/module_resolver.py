"""Shared helpers for resolving module paths during semantic extraction."""

from __future__ import annotations

import json
import posixpath
import re

from coderag.core.models import ScannedFile, SymbolChunk


_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")
_JS_EXPORT_FUNCTION_OR_CLASS_PATTERN = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_EXPORT_INTERFACE_PATTERN = re.compile(
    r"^\s*export\s+interface\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_EXPORT_VARIABLE_PATTERN = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_EXPORT_LIST_PATTERN = re.compile(r"^\s*export\s*\{([^}]*)\}")
_JS_REEXPORT_PATTERN = re.compile(
    r"^\s*export\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]"
)
_JS_EXPORT_STAR_PATTERN = re.compile(
    r"^\s*export\s*\*\s*from\s*['\"]([^'\"]+)['\"]"
)
_JS_EXPORT_DEFAULT_PATTERN = re.compile(r"^\s*export\s+default\b")


def _normalize_repo_path(path: str) -> str:
    """Normalize repository-relative paths to a POSIX-like form."""
    normalized = path.replace("\\", "/")
    return posixpath.normpath(normalized).lstrip("./")


def derive_python_module_name(path: str) -> str:
    """Convert a Python file path into its dotted module name."""
    normalized = _normalize_repo_path(path)
    if not normalized.endswith(".py"):
        return ""

    module_path = normalized[:-3]
    if module_path.endswith("/__init__"):
        module_path = module_path[: -len("/__init__")]
    return module_path.replace("/", ".")


def build_python_module_index(
    scanned_files: list[ScannedFile],
) -> dict[str, str]:
    """Build a mapping from Python file paths to dotted module names."""
    module_index: dict[str, str] = {}
    for file_obj in scanned_files:
        if file_obj.language != "python":
            continue
        module_index[file_obj.path] = derive_python_module_name(file_obj.path)
    return module_index


def build_python_qualified_name_index(
    symbols: list[SymbolChunk],
    module_index: dict[str, str],
) -> dict[str, str]:
    """Build a mapping from qualified Python symbol names to ids."""
    qualified_names: dict[str, str] = {}
    for symbol in symbols:
        if symbol.language != "python":
            continue
        module_name = module_index.get(symbol.path, "")
        qualified_name = (
            f"{module_name}.{symbol.symbol_name}"
            if module_name
            else symbol.symbol_name
        )
        qualified_names[qualified_name] = symbol.id
    return qualified_names


def resolve_python_relative_import(
    source_path: str,
    level: int,
    module: str | None,
    name: str,
) -> str | None:
    """Resolve a Python relative import into an absolute dotted name."""
    source_module = derive_python_module_name(source_path)
    if not source_module:
        return None

    normalized_path = _normalize_repo_path(source_path)
    package_parts = source_module.split(".") if source_module else []
    if not normalized_path.endswith("/__init__.py") and package_parts:
        package_parts = package_parts[:-1]

    ascend = max(level - 1, 0)
    if ascend > len(package_parts):
        return None

    base_parts = package_parts[: len(package_parts) - ascend]
    module_parts = module.split(".") if module else []
    name_parts = [name] if name else []
    target_parts = [part for part in [*base_parts, *module_parts, *name_parts] if part]
    if not target_parts:
        return None
    return ".".join(target_parts)


def normalize_js_import_path(
    source_path: str,
    raw_import: str,
    scanned_paths: set[str],
    *,
    tsconfig_paths: dict[str, str] | None = None,
    tsconfig_base_url: str | None = None,
) -> str | None:
    """Resolve a relative JS or TS import path to a scanned repository file."""
    candidate_bases: list[str] = []

    if raw_import.startswith("."):
        source_dir = posixpath.dirname(_normalize_repo_path(source_path))
        candidate_bases.append(
            _normalize_repo_path(posixpath.join(source_dir, raw_import))
        )
    else:
        alias_target = _resolve_tsconfig_alias(raw_import, tsconfig_paths or {})
        if alias_target:
            candidate_bases.append(alias_target)
        if tsconfig_base_url:
            candidate_bases.append(
                _normalize_repo_path(posixpath.join(tsconfig_base_url, raw_import))
            )

    for candidate_base in candidate_bases:
        resolved_candidate = _probe_js_candidate(candidate_base, scanned_paths)
        if resolved_candidate:
            return resolved_candidate

    return None


def load_tsconfig_paths(
    scanned_files: list[ScannedFile],
) -> tuple[str | None, dict[str, str]]:
    """Load baseUrl and path aliases from the first tsconfig or jsconfig file."""
    config_file = next(
        (
            file_obj
            for file_obj in scanned_files
            if posixpath.basename(_normalize_repo_path(file_obj.path))
            in {"tsconfig.json", "jsconfig.json"}
        ),
        None,
    )
    if config_file is None:
        return None, {}

    try:
        payload = json.loads(config_file.content)
    except json.JSONDecodeError:
        return None, {}

    compiler_options = payload.get("compilerOptions") or {}
    config_dir = posixpath.dirname(_normalize_repo_path(config_file.path))
    raw_base_url = str(compiler_options.get("baseUrl") or "").strip()
    base_url = (
        _normalize_repo_path(posixpath.join(config_dir, raw_base_url))
        if raw_base_url
        else None
    )

    alias_paths: dict[str, str] = {}
    for alias_pattern, targets in (compiler_options.get("paths") or {}).items():
        if not isinstance(targets, list) or not targets:
            continue
        first_target = str(targets[0] or "").strip()
        if not first_target:
            continue
        alias_paths[str(alias_pattern).strip()] = _normalize_repo_path(
            posixpath.join(config_dir, first_target)
        )
    return base_url, alias_paths


def _resolve_tsconfig_alias(
    raw_import: str,
    tsconfig_paths: dict[str, str],
) -> str | None:
    """Resolve a bare import through tsconfig path aliases."""
    for alias_pattern, target_pattern in tsconfig_paths.items():
        if "*" not in alias_pattern:
            if raw_import == alias_pattern:
                return target_pattern
            continue

        alias_prefix, alias_suffix = alias_pattern.split("*", maxsplit=1)
        if not raw_import.startswith(alias_prefix):
            continue
        if alias_suffix and not raw_import.endswith(alias_suffix):
            continue

        suffix_length = len(alias_suffix)
        raw_middle = raw_import[len(alias_prefix) :]
        if suffix_length:
            raw_middle = raw_middle[:-suffix_length]
        return _normalize_repo_path(target_pattern.replace("*", raw_middle, 1))
    return None


def _probe_js_candidate(candidate_base: str, scanned_paths: set[str]) -> str | None:
    """Probe a JS or TS candidate path with extension and index fallbacks."""
    normalized_import = _normalize_repo_path(candidate_base)
    if normalized_import in scanned_paths:
        return normalized_import

    if posixpath.splitext(normalized_import)[1] in _JS_EXTENSIONS:
        return normalized_import if normalized_import in scanned_paths else None

    for extension in _JS_EXTENSIONS:
        candidate = f"{normalized_import}{extension}"
        if candidate in scanned_paths:
            return candidate

    for extension in _JS_EXTENSIONS:
        candidate = posixpath.join(normalized_import, f"index{extension}")
        if candidate in scanned_paths:
            return candidate

    return None


def build_js_export_index(
    scanned_files: list[ScannedFile],
    symbols: list[SymbolChunk],
    *,
    languages: set[str] | None = None,
    tsconfig_paths: dict[str, str] | None = None,
    tsconfig_base_url: str | None = None,
) -> dict[str, dict[str, str]]:
    """Build an export surface mapping for JS/TS files in the repo."""
    active_languages = languages or {"javascript", "js", "typescript", "ts"}
    file_symbols: dict[str, list[SymbolChunk]] = {}
    scanned_path_set = {
        _normalize_repo_path(item.path)
        for item in scanned_files
        if item.language in active_languages
    }
    path_aliases = {
        _normalize_repo_path(item.path): item.path
        for item in scanned_files
        if item.language in active_languages
    }

    for symbol in symbols:
        if symbol.language not in active_languages:
            continue
        file_symbols.setdefault(symbol.path, []).append(symbol)

    export_index: dict[str, dict[str, str]] = {}
    pending_named_reexports: list[tuple[str, str, str]] = []
    pending_star_reexports: list[tuple[str, str]] = []

    for file_obj in scanned_files:
        if file_obj.language not in active_languages:
            continue

        symbols_by_name = {
            symbol.symbol_name: symbol.id
            for symbol in file_symbols.get(file_obj.path, [])
        }
        symbols_by_line = {
            symbol.start_line: symbol.id
            for symbol in file_symbols.get(file_obj.path, [])
        }
        file_exports: dict[str, str] = {}

        for line_number, line in enumerate(file_obj.content.splitlines(), start=1):
            named_export_match = _JS_EXPORT_FUNCTION_OR_CLASS_PATTERN.match(line)
            if named_export_match:
                export_name = named_export_match.group(1)
                symbol_id = symbols_by_name.get(export_name)
                if symbol_id:
                    file_exports[export_name] = symbol_id
                if line.strip().startswith("export default") and symbol_id:
                    file_exports["default"] = symbol_id
                continue

            variable_export_match = _JS_EXPORT_VARIABLE_PATTERN.match(line)
            if variable_export_match:
                export_name = variable_export_match.group(1)
                symbol_id = symbols_by_name.get(export_name)
                if symbol_id:
                    file_exports[export_name] = symbol_id
                continue

            interface_export_match = _JS_EXPORT_INTERFACE_PATTERN.match(line)
            if interface_export_match:
                export_name = interface_export_match.group(1)
                symbol_id = symbols_by_name.get(export_name)
                if symbol_id:
                    file_exports[export_name] = symbol_id
                continue

            reexport_match = _JS_REEXPORT_PATTERN.match(line)
            if reexport_match:
                raw_names = reexport_match.group(1)
                raw_target = reexport_match.group(2)
                resolved_target = normalize_js_import_path(
                    file_obj.path,
                    raw_target,
                    scanned_path_set,
                    tsconfig_paths=tsconfig_paths,
                    tsconfig_base_url=tsconfig_base_url,
                )
                if not resolved_target:
                    continue
                for item in raw_names.split(","):
                    spec = item.strip()
                    if not spec:
                        continue
                    if " as " in spec:
                        source_name, export_name = [
                            part.strip() for part in spec.split(" as ", maxsplit=1)
                        ]
                    else:
                        source_name = spec
                        export_name = spec
                    pending_named_reexports.append(
                        (file_obj.path, resolved_target, source_name, export_name)
                    )
                continue

            export_star_match = _JS_EXPORT_STAR_PATTERN.match(line)
            if export_star_match:
                resolved_target = normalize_js_import_path(
                    file_obj.path,
                    export_star_match.group(1),
                    scanned_path_set,
                    tsconfig_paths=tsconfig_paths,
                    tsconfig_base_url=tsconfig_base_url,
                )
                if resolved_target:
                    pending_star_reexports.append((file_obj.path, resolved_target))
                continue

            export_list_match = _JS_EXPORT_LIST_PATTERN.match(line)
            if export_list_match:
                for item in export_list_match.group(1).split(","):
                    spec = item.strip()
                    if not spec:
                        continue
                    if " as " in spec:
                        source_name, export_name = [
                            part.strip() for part in spec.split(" as ", maxsplit=1)
                        ]
                    else:
                        source_name = spec
                        export_name = spec
                    symbol_id = symbols_by_name.get(source_name)
                    if symbol_id:
                        file_exports[export_name] = symbol_id
                continue

            if not _JS_EXPORT_DEFAULT_PATTERN.match(line):
                continue
            symbol_id = symbols_by_line.get(line_number)
            if not symbol_id and file_symbols.get(file_obj.path):
                symbol_id = file_symbols[file_obj.path][0].id
            if symbol_id:
                file_exports["default"] = symbol_id

        export_index[file_obj.path] = file_exports

    for owner_path, target_path, source_name, export_name in pending_named_reexports:
        target_exports = export_index.get(path_aliases.get(target_path, target_path), {})
        symbol_id = target_exports.get(source_name)
        if symbol_id:
            export_index.setdefault(owner_path, {})[export_name] = symbol_id

    for owner_path, target_path in pending_star_reexports:
        target_exports = export_index.get(path_aliases.get(target_path, target_path), {})
        for export_name, symbol_id in target_exports.items():
            if export_name == "default":
                continue
            export_index.setdefault(owner_path, {}).setdefault(export_name, symbol_id)

    return export_index