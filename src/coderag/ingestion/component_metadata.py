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


def _frontend_purpose_from_path(path: str) -> tuple[str | None, str | None]:
    """Infiere propósito frontend a partir de convenciones de path y archivo."""
    pure_path = PurePosixPath(path)
    stem = pure_path.stem.lower()
    filename = pure_path.name.lower()
    parent_tokens = {part.lower() for part in pure_path.parts[:-1]}

    if filename == "page.tsx":
        return (
            "Define la página principal de la ruta y compone la UI servida al usuario.",
            "next_filename_heuristic",
        )
    if filename == "layout.tsx":
        return (
            "Define el layout compartido de la ruta y envuelve el contenido anidado.",
            "next_filename_heuristic",
        )
    if filename == "loading.tsx":
        return (
            "Define la interfaz de carga mostrada mientras la ruta resuelve sus datos.",
            "next_filename_heuristic",
        )
    if filename in {"error.tsx", "global-error.tsx"}:
        return (
            "Define la interfaz de error para recuperar o reportar fallos de la ruta.",
            "next_filename_heuristic",
        )
    if filename == "not-found.tsx":
        return (
            "Define la interfaz mostrada cuando la ruta solicitada no existe.",
            "next_filename_heuristic",
        )
    if filename == "middleware.ts":
        return (
            "Define middleware de Next.js para interceptar y controlar requests antes del enrutado.",
            "next_filename_heuristic",
        )
    if filename == "route.ts":
        return (
            "Define handlers HTTP del endpoint y coordina la respuesta de la ruta API.",
            "next_filename_heuristic",
        )
    if stem.startswith("use") and len(stem) > 3:
        return (
            "Define un hook reutilizable para encapsular estado, efectos o acceso a datos del frontend.",
            "frontend_filename_heuristic",
        )
    if stem.endswith("provider") or "providers" in parent_tokens:
        return (
            "Define un provider frontend para exponer contexto compartido, estado o dependencias reutilizables.",
            "frontend_filename_heuristic",
        )
    return None, None


def _purpose_from_frontend_symbol(name: str) -> tuple[str | None, str | None]:
    """Infiere propósito frontend a partir del símbolo principal detectado."""
    lowered = name.lower()
    if lowered.startswith("use") and len(name) > 3:
        return (
            f"Define el hook `{name}` para encapsular estado, efectos o acceso a datos reutilizable.",
            "frontend_symbol_name",
        )
    if lowered.endswith("provider"):
        return (
            f"Define `{name}` para proveer contexto compartido a componentes descendientes.",
            "frontend_symbol_name",
        )
    return None, None


def _purpose_from_route_handler(name: str) -> tuple[str | None, str | None]:
    """Infiere propósito a partir de handlers HTTP exportados."""
    if name not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return None, None
    return (
        f"Define el handler HTTP `{name}` para procesar requests del endpoint de la ruta.",
        "route_handler_name",
    )


def infer_component_purpose(path: str, content: str) -> tuple[str | None, str | None]:
    """Infiere un resumen corto de propósito para persistirlo durante ingesta."""
    fallback_hint = _purpose_from_filename(path)
    frontend_hint, frontend_hint_source = _frontend_purpose_from_path(path)
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
                    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
            (
                re.compile(
                    r"^\s*const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
                ),
                "function",
            ),
        ],
        ".ts": [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
            (
                re.compile(
                    r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
                ),
                "function",
            ),
        ],
        ".jsx": [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
            (
                re.compile(
                    r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
                ),
                "function",
            ),
        ],
        ".tsx": [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (
                re.compile(
                    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)"
                ),
                "function",
            ),
            (
                re.compile(
                    r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>"
                ),
                "function",
            ),
        ],
    }

    if frontend_hint is not None:
        return frontend_hint, frontend_hint_source

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
            route_purpose, route_source = _purpose_from_route_handler(name)
            if route_purpose is not None:
                return route_purpose, route_source

            frontend_purpose, frontend_source = _purpose_from_frontend_symbol(name)
            if frontend_purpose is not None:
                return frontend_purpose, frontend_source

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