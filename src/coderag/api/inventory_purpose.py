"""Inventory purpose enrichment helpers extracted from query service."""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from coderag.core.models import Citation


@dataclass(frozen=True)
class InventoryPurposeHooks:
    """Injected collaborators required by inventory purpose enrichment."""

    graph_builder_factory: Callable[[], object]
    remaining_budget_seconds: Callable[[float, float], float]
    normalize_inventory_token: Callable[[str], str]


def first_sentence(text: str) -> str:
    """Return the first sentence-like fragment without trailing punctuation."""
    first = re.split(r"[\.\n\r]", text, maxsplit=1)[0].strip()
    return first.rstrip(" \t\"'`.,;:!?¡¿")


def purpose_from_filename(file_path: Path) -> str | None:
    """Infer a likely component purpose from the filename."""
    stem = file_path.stem.lower()
    filename = file_path.name.lower()

    if filename == "requirements.txt":
        return "Declara dependencias Python del proyecto para instalación y despliegue."
    if filename in {"pyproject.toml", "poetry.lock"}:
        return "Define metadata del proyecto y dependencias Python gestionadas por herramientas modernas."
    if filename in {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }:
        return "Declara dependencias JavaScript/TypeScript y scripts de construcción del proyecto."
    if filename in {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
    }:
        return "Configura dependencias y build del ecosistema JVM para el proyecto."

    if any(token in stem for token in ("settings", "config", "configuration")):
        return "Centraliza configuración y parámetros del módulo."
    if any(token in stem for token in ("model", "entity", "schema", "dto")):
        return "Define estructuras de datos y contratos del dominio."
    if any(token in stem for token in ("log", "logger", "logging")):
        return "Configura y encapsula el comportamiento de logging."
    if stem in {"__init__", "index"}:
        return "Define inicialización/exportaciones del módulo."
    return None


def build_purpose_from_source(file_path: Path) -> str | None:
    """Infer a concise component purpose from its source file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    fallback_hint = purpose_from_filename(file_path)
    lines = content.splitlines()[:240]
    suffix = file_path.suffix.lower()

    if suffix == ".py":
        try:
            module_ast = ast.parse(content)
        except (SyntaxError, ValueError):
            module_ast = None

        if module_ast is not None:
            module_doc = ast.get_docstring(module_ast)
            if module_doc:
                summary = first_sentence(module_doc)
                if len(summary) >= 20:
                    return f"{summary}."

            for node in module_ast.body:
                if isinstance(node, ast.ClassDef):
                    name = node.name
                    normalized = name.lower()
                    if any(token in normalized for token in ("settings", "config")):
                        return (
                            f"Declara la clase `{name}` para centralizar "
                            f"configuración del componente."
                        )
                    if "service" in normalized:
                        return (
                            f"Declara la clase `{name}` para implementar "
                            f"lógica de servicio del componente."
                        )
                    return (
                        f"Declara la clase `{name}` y centraliza "
                        f"responsabilidades del componente."
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
                            f"Define `{name}` para configurar el logging "
                            f"del componente."
                        )
                    return (
                        f"Define la función `{name}` y encapsula "
                        f"comportamiento reutilizable."
                    )

    patterns_by_suffix: dict[str, list[tuple[re.Pattern[str], str]]] = {
        ".java": [
            (
                re.compile(
                    r"^\s*(?:public\s+|private\s+|protected\s+)?"
                    r"(?:abstract\s+|final\s+)?"
                    r"(class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
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
                        f"entradas y coordinación del componente."
                    )
                if "service" in lowered:
                    return (
                        f"Declara el {java_kind} `{name}` para implementar "
                        f"lógica de negocio del componente."
                    )
                if "repository" in lowered:
                    return (
                        f"Declara el {java_kind} `{name}` para encapsular "
                        f"acceso a datos del componente."
                    )
                return (
                    f"Declara el {java_kind} `{name}` y concentra lógica "
                    f"principal del componente."
                )
            name = match.group(1)
            if kind == "class":
                lowered = name.lower()
                if "controller" in lowered:
                    return (
                        f"Declara la clase `{name}` para gestionar entradas "
                        f"y coordinación del componente."
                    )
                if "service" in lowered:
                    return (
                        f"Declara la clase `{name}` para implementar lógica "
                        f"de servicio del componente."
                    )
                if "repository" in lowered:
                    return (
                        f"Declara la clase `{name}` para encapsular acceso "
                        f"a datos del componente."
                    )
                return (
                    f"Declara la clase `{name}` y centraliza "
                    f"responsabilidades del componente."
                )
            if kind == "function":
                return (
                    f"Define la función `{name}` y encapsula comportamiento "
                    f"reutilizable."
                )

    if fallback_hint:
        return fallback_hint
    return "Contiene implementación de soporte del componente en este módulo."


def describe_inventory_components(
    repo_id: str,
    citations: list[Citation],
    pipeline_started_at: float,
    budget_seconds: float,
    query: str | None = None,
    *,
    hooks: InventoryPurposeHooks,
) -> list[tuple[str, str]]:
    """Build purpose hints for inventory components using persisted metadata."""
    if not citations:
        return []

    candidate_paths: list[str] = []
    seen_paths: set[str] = set()
    for citation in citations:
        if hooks.remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0:
            break
        path = citation.path.strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        candidate_paths.append(path)

    graph = hooks.graph_builder_factory()
    try:
        purpose_payloads = graph.query_file_purpose_summaries(
            repo_id=repo_id,
            paths=candidate_paths,
        )
    except Exception:
        purpose_payloads = {}
    finally:
        graph.close()

    descriptions: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for path in candidate_paths:
        component_name = PurePosixPath(path).name
        if not component_name or component_name in seen_names:
            continue
        payload = purpose_payloads.get(path)
        if not payload:
            continue
        purpose = str(payload.get("purpose_summary", "") or "").strip()
        if purpose is None:
            continue
        seen_names.add(component_name)
        descriptions.append((component_name, purpose))

    if not descriptions or not query:
        return descriptions

    normalized_query = hooks.normalize_inventory_token(query)
    query_tokens = set(re.findall(r"[a-z0-9]+", normalized_query))
    if not query_tokens:
        return descriptions

    def _score(item: tuple[str, str]) -> tuple[int, int, str]:
        name, purpose = item
        haystack = hooks.normalize_inventory_token(f"{name} {purpose}")
        overlap = sum(1 for token in query_tokens if token in haystack)
        return (overlap, len(purpose), name.lower())

    return sorted(descriptions, key=_score, reverse=True)