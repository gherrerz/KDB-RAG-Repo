"""Utilidades de resumen para archivos y módulos."""

from collections import defaultdict

from src.coderag.core.models import ScannedFile


def summarize_file(file_obj: ScannedFile) -> str:
    """Produzca un resumen determinista compacto a partir del contenido del archivo."""
    lines = file_obj.content.splitlines()
    head = "\n".join(lines[:20])
    return (
        f"Archivo: {file_obj.path}\n"
        f"Lenguaje: {file_obj.language}\n"
        f"Lineas: {len(lines)}\n"
        f"Extracto:\n{head}"
    )


def summarize_modules(scanned_files: list[ScannedFile]) -> dict[str, str]:
    """Cree resúmenes de módulos agrupados por carpeta de nivel superior."""
    grouped: dict[str, list[ScannedFile]] = defaultdict(list)
    for file_obj in scanned_files:
        root = file_obj.path.split("/", 1)[0] if "/" in file_obj.path else "."
        grouped[root].append(file_obj)

    summaries: dict[str, str] = {}
    for module_name, files in grouped.items():
        languages = sorted({item.language for item in files})
        summaries[module_name] = (
            f"Modulo: {module_name}\n"
            f"Archivos: {len(files)}\n"
            f"Lenguajes: {', '.join(languages)}"
        )
    return summaries
