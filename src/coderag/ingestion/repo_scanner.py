"""Escáner de repositorio para seleccionar archivos relevantes para la indexación."""

from fnmatch import fnmatch
import os
from pathlib import Path

from coderag.core.models import ScannedFile

LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".kt": "kotlin",
    ".java": "java",
    ".go": "go",
    ".md": "markdown",
    ".swift": "swift",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

def detect_language(path: Path) -> str:
    """Detecta una etiqueta de lenguaje lógico a partir de una extensión de archivo."""
    return LANG_MAP.get(path.suffix.lower(), "text")


def scan_repository(
    repo_path: Path,
    max_file_size: int,
    excluded_dirs: set[str] | None = None,
    excluded_extensions: set[str] | None = None,
    excluded_files: set[str] | None = None,
    excluded_patterns: set[str] | None = None,
) -> list[ScannedFile]:
    """Recopila archivos de código, configuración y documentación con filtros."""
    scanned, _stats = scan_repository_with_stats(
        repo_path=repo_path,
        max_file_size=max_file_size,
        excluded_dirs=excluded_dirs,
        excluded_extensions=excluded_extensions,
        excluded_files=excluded_files,
        excluded_patterns=excluded_patterns,
    )
    return scanned


def scan_repository_with_stats(
    repo_path: Path,
    max_file_size: int,
    excluded_dirs: set[str] | None = None,
    excluded_extensions: set[str] | None = None,
    excluded_files: set[str] | None = None,
    excluded_patterns: set[str] | None = None,
) -> tuple[list[ScannedFile], dict[str, int]]:
    """Recopila archivos y devuelve estadísticas agregadas de exclusión/cobertura."""
    scanned: list[ScannedFile] = []
    stats = {
        "visited": 0,
        "visited_dirs": 0,
        "scanned": 0,
        "excluded_dir": 0,
        "excluded_extension": 0,
        "excluded_file": 0,
        "excluded_pattern": 0,
        "excluded_size": 0,
        "excluded_decode": 0,
        "pruned_dirs": 0,
    }
    excluded_dir_names = {item.lower() for item in (excluded_dirs or set())}
    excluded_file_extensions = {
        item.lower() for item in (excluded_extensions or set())
    }
    excluded_file_entries = {item.lower() for item in (excluded_files or set())}
    excluded_path_patterns = {
        item.lower().replace("\\", "/") for item in (excluded_patterns or set())
    }

    for root, dir_names, file_names in os.walk(repo_path, topdown=True):
        stats["visited_dirs"] += 1
        kept_dir_names: list[str] = []
        pruned_here = 0
        for dir_name in dir_names:
            if dir_name.lower() in excluded_dir_names:
                pruned_here += 1
                continue
            kept_dir_names.append(dir_name)
        if pruned_here > 0:
            stats["excluded_dir"] += pruned_here
            stats["pruned_dirs"] += pruned_here
        dir_names[:] = kept_dir_names

        root_path = Path(root)
        for file_name in file_names:
            file_path = root_path / file_name
            stats["visited"] += 1

            if file_path.suffix.lower() in excluded_file_extensions:
                stats["excluded_extension"] += 1
                continue

            rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
            rel_path_normalized = rel_path.lower()

            if file_path.name.lower() in excluded_file_entries:
                stats["excluded_file"] += 1
                continue

            if rel_path_normalized in excluded_file_entries:
                stats["excluded_file"] += 1
                continue

            if any(fnmatch(rel_path_normalized, pattern) for pattern in excluded_path_patterns):
                stats["excluded_pattern"] += 1
                continue

            if file_path.stat().st_size > max_file_size:
                stats["excluded_size"] += 1
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                stats["excluded_decode"] += 1
                continue

            scanned.append(
                ScannedFile(
                    path=rel_path,
                    language=detect_language(file_path),
                    content=content,
                )
            )
            stats["scanned"] += 1
    return scanned, stats
