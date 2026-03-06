"""Escáner de repositorio para seleccionar archivos relevantes para la indexación."""

from pathlib import Path

from coderag.core.models import ScannedFile

LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".go": "go",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "venv",
    ".venv",
    "__pycache__",
}


def detect_language(path: Path) -> str:
    """Detecta una etiqueta de lenguaje lógico a partir de una extensión de archivo."""
    return LANG_MAP.get(path.suffix.lower(), "text")


def scan_repository(repo_path: Path, max_file_size: int = 200_000) -> list[ScannedFile]:
    """Recopile archivos fuente y de documentación del repositorio."""
    scanned: list[ScannedFile] = []
    for file_path in repo_path.rglob("*"):
        if not file_path.is_file():
            continue

        if any(part in EXCLUDED_DIRS for part in file_path.parts):
            continue

        if file_path.stat().st_size > max_file_size:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
        scanned.append(
            ScannedFile(
                path=rel_path,
                language=detect_language(file_path),
                content=content,
            )
        )
    return scanned
