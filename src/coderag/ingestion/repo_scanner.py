"""Escáner de repositorio para seleccionar archivos relevantes para la indexación."""

from pathlib import Path

from coderag.core.models import ScannedFile

LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".md": "markdown",
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
) -> list[ScannedFile]:
    """Recopila archivos de código, configuración y documentación con filtros."""
    scanned, _stats = scan_repository_with_stats(
        repo_path=repo_path,
        max_file_size=max_file_size,
        excluded_dirs=excluded_dirs,
        excluded_extensions=excluded_extensions,
        excluded_files=excluded_files,
    )
    return scanned


def scan_repository_with_stats(
    repo_path: Path,
    max_file_size: int,
    excluded_dirs: set[str] | None = None,
    excluded_extensions: set[str] | None = None,
    excluded_files: set[str] | None = None,
) -> tuple[list[ScannedFile], dict[str, int]]:
    """Recopila archivos y devuelve estadísticas agregadas de exclusión/cobertura."""
    scanned: list[ScannedFile] = []
    stats = {
        "visited": 0,
        "scanned": 0,
        "excluded_dir": 0,
        "excluded_extension": 0,
        "excluded_file": 0,
        "excluded_size": 0,
        "excluded_decode": 0,
    }
    excluded_dir_names = {item.lower() for item in (excluded_dirs or set())}
    excluded_file_extensions = {
        item.lower() for item in (excluded_extensions or set())
    }
    excluded_file_entries = {item.lower() for item in (excluded_files or set())}

    for file_path in repo_path.rglob("*"):
        if not file_path.is_file():
            continue
        stats["visited"] += 1

        if any(part.lower() in excluded_dir_names for part in file_path.parts):
            stats["excluded_dir"] += 1
            continue

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
