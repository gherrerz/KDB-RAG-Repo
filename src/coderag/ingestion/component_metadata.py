"""Heurísticas de metadata derivada para archivos y componentes."""

from __future__ import annotations

import ast
from pathlib import PurePosixPath
import re


def _first_sentence(text: str) -> str:
    """Devuelve el primer fragmento tipo oración sin puntuación final."""
    first = re.split(r"[\.\n\r]", text, maxsplit=1)[0].strip()
    return first.rstrip(" \t\"'`.,;:!?¡¿")


def _purpose_from_filename(path: str) -> str | None:
    """Infiere propósito liviano a partir del nombre del archivo."""
    pure_path = PurePosixPath(path)
    stem = pure_path.stem.lower()
    filename = pure_path.name.lower()

    if filename == "requirements.txt":
        return "Declara dependencias Python del proyecto para instalación y despliegue."
    if filename in {"pyproject.toml", "poetry.lock"}:
        return (
            "Define metadata del proyecto y dependencias Python gestionadas "
            "por herramientas modernas."
        )
    if filename in {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }:
        return (
            "Declara dependencias JavaScript/TypeScript y scripts de "
            "construcción del proyecto."
        )
    if filename in {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
    }:
        return (
            "Configura dependencias y build del ecosistema JVM para el "
            "proyecto."
        )

    if any(token in stem for token in ("settings", "config", "configuration")):
        return "Centraliza configuración y parámetros del módulo."
    if any(token in stem for token in ("model", "entity", "schema", "dto")):
        return "Define estructuras de datos y contratos del dominio."
    if any(token in stem for token in ("log", "logger", "logging")):
        return "Configura y encapsula el comportamiento de logging."
    if stem in {"__init__", "index"}:
        return "Define inicialización y exportaciones del módulo."
    return None


def infer_component_purpose(path: str, content: str) -> tuple[str | None, str | None]:
    """Infiere un resumen corto de propósito para persistirlo durante ingesta."""
    fallback_hint = _purpose_from_filename(path)
    pure_path = PurePosixPath(path)
    suffix = pure_path.suffix.lower()
    lines = content.splitlines()[:240]

    if suffix == ".py":
        try:
            module_ast = ast.parse(content)
        except (SyntaxError, ValueError):
            module_ast = None

        if module_ast is not None:
            module_doc = ast.get_docstring(module_ast)
            if module_doc:
                summary = _first_sentence(module_doc)
                if len(summary) >= 20:
                    return f"{summary}.", "module_docstring"

            for node in module_ast.body:
                if isinstance(node, ast.ClassDef):
                    name = node.name
                    normalized = name.lower()
                    if any(token in normalized for token in ("settings", "config")):
                        return (
                            f"Declara la clase `{name}` para centralizar "
                            f"configuración del componente.",
                            "python_class_name",
                        )
                    if "service" in normalized:
                        return (
                            f"Declara la clase `{name}` para implementar lógica "
                            f"de servicio del componente.",
                            "python_class_name",
                        )
                    return (
                        f"Declara la clase `{name}` y centraliza "
                        f"responsabilidades del componente.",
                        "python_class_name",
                    )

                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    normalized = name.lower()
                    has_setup = any(
                        token in normalized
                        for token in ("configure", "setup", "init")
                    )
                    has_logging = any(
                        token in normalized for token in ("logging", "log")
                    )
                    if has_setup and has_logging:
                        return (
                            f"Define `{name}` para configurar el logging del "
                            f"componente.",
                            "python_function_name",
                        )
                    return (
                        f"Define la función `{name}` y encapsula comportamiento "
                        f"reutilizable.",
                        "python_function_name",
                    )

    patterns_by_suffix: dict[str, list[tuple[re.Pattern[str], str]]] = {
        ".java": [
            (
                re.compile(
                    r"^\s*(?:public\s+|private\s+|protected\s+)?"
                    r"(?:abstract\s+|final\s+)?"
                    r"(class|interface|enum|record)\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "java_type",
            ),
        ],
        ".js": [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
        ],
        ".ts": [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
        ],
    }

    patterns = patterns_by_suffix.get(suffix, [])
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*")):
            continue
        for pattern, kind in patterns:
            match = pattern.match(line)
            if not match:
                continue
            if kind == "java_type":
                java_kind = match.group(1)
                name = match.group(2)
                lowered = name.lower()
                if "controller" in lowered:
                    return (
                        f"Declara el {java_kind} `{name}` para gestionar "
                        f"entradas y coordinación del componente.",
                        "java_type_name",
                    )
                if "service" in lowered:
                    return (
                        f"Declara el {java_kind} `{name}` para implementar "
                        f"lógica de negocio del componente.",
                        "java_type_name",
                    )
                if "repository" in lowered:
                    return (
                        f"Declara el {java_kind} `{name}` para encapsular "
                        f"acceso a datos del componente.",
                        "java_type_name",
                    )
                return (
                    f"Declara el {java_kind} `{name}` y concentra lógica "
                    f"principal del componente.",
                    "java_type_name",
                )

            name = match.group(1)
            lowered = name.lower()
            if kind == "class":
                if "controller" in lowered:
                    return (
                        f"Declara la clase `{name}` para gestionar entradas y "
                        f"coordinación del componente.",
                        "class_name",
                    )
                if "service" in lowered:
                    return (
                        f"Declara la clase `{name}` para implementar lógica "
                        f"de servicio del componente.",
                        "class_name",
                    )
                if "repository" in lowered:
                    return (
                        f"Declara la clase `{name}` para encapsular acceso a "
                        f"datos del componente.",
                        "class_name",
                    )
                return (
                    f"Declara la clase `{name}` y centraliza "
                    f"responsabilidades del componente.",
                    "class_name",
                )
            return (
                f"Define la función `{name}` y encapsula comportamiento "
                f"reutilizable.",
                "function_name",
            )

    if fallback_hint:
        return fallback_hint, "filename_heuristic"
    return None, None