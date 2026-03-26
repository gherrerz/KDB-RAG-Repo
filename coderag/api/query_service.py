"""Orquestación de consultas de un extremo a otro para Hybrid RAG + GraphRAG."""

import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import re
from threading import Lock
from time import monotonic
import unicodedata

from coderag.core.models import (
    Citation,
    InventoryItem,
    InventoryQueryResponse,
    QueryResponse,
    RetrievedChunk,
    RetrievalQueryResponse,
    RetrievalStatistics,
    RetrievalChunk,
)
from coderag.core.settings import get_settings
from coderag.api.citation_filters import build_inventory_citations, is_noisy_path
from coderag.api.query_diagnostics import (
    build_inventory_diagnostics,
    build_inventory_missing_target_diagnostics,
    build_query_diagnostics,
    build_retrieval_diagnostics,
)
from coderag.ingestion.graph_builder import GraphBuilder
from coderag.llm.openai_client import AnswerClient
from coderag.retrieval.context_assembler import assemble_context
from coderag.retrieval.graph_expand import expand_with_graph, expand_with_graph_with_diagnostics
from coderag.retrieval.hybrid_search import hybrid_search
from coderag.retrieval.reranker import rerank


INVENTORY_EQUIVALENT_GROUPS = [
    {"class", "clase"},
    {"service", "servicio"},
    {"controller", "controlador"},
    {"repository", "repositorio", "repo"},
    {"handler", "manejador"},
    {"model", "modelo"},
    {"entity", "entidad"},
    {"client", "cliente"},
    {"adapter", "adaptador"},
    {"gateway", "pasarela"},
    {"dao", "dataaccess", "data-access"},
    {"config", "configuration", "configuracion", "configuración"},
    {"implementation", "implementacion", "implementación", "impl"},
    {"manager", "gestor"},
    {"factory", "fabrica", "fábrica"},
    {"helper", "util", "utils", "utilidad"},
    {
        "dependency",
        "dependencies",
        "dependencia",
        "dependencias",
        "requirement",
        "requirements",
        "requisito",
        "requisitos",
    },
    {"component", "componente", "element", "elemento"},
    {"file", "archivo", "fichero"},
]

BROAD_FILE_INVENTORY_TERMS = {
    "component",
    "componente",
    "element",
    "elemento",
    "file",
    "archivo",
    "fichero",
}

MODULE_NAME_STOPWORDS = {
    "el",
    "la",
    "los",
    "las",
    "the",
    "a",
    "an",
    "de",
    "del",
    "tipo",
    "type",
    "clase",
    "class",
    "proyecto",
    "project",
}

DEPENDENCY_INVENTORY_TERMS = {
    "dependency",
    "dependencies",
    "dependencia",
    "dependencias",
    "requirement",
    "requirements",
    "requisito",
    "requisitos",
}

INVENTORY_TARGET_STOPWORDS = {
    "todo",
    "todos",
    "toda",
    "todas",
    "los",
    "las",
    "all",
    "the",
}


_MODULE_SCOPE_CACHE: dict[tuple[str, str], str] = {}
_MODULE_SCOPE_CACHE_LOCK = Lock()


def _fallback_header(fallback_reason: str) -> str:
    """Devuelve un mensaje de encabezado alternativo según la causa raíz."""
    messages = {
        "not_configured": (
            "LLM no está configurado; respuesta extractiva basada en "
            "evidencia."
        ),
        "verification_failed": (
            "No se pudo validar completamente la respuesta generada; "
            "mostrando evidencia trazable."
        ),
        "generation_error": (
            "Ocurrió un error al generar respuesta con el modelo seleccionado; mostrando "
            "evidencia trazable."
        ),
        "time_budget_exhausted": (
            "Se alcanzó el presupuesto de tiempo de consulta; mostrando "
            "evidencia trazable disponible."
        ),
        "insufficient_context": (
            "No hubo contexto suficiente para una síntesis confiable; "
            "mostrando evidencia trazable disponible."
        ),
    }
    return messages.get(
        fallback_reason,
        "Mostrando evidencia trazable del repositorio.",
    )


def _build_extractive_fallback(
    citations: list[Citation],
    inventory_mode: bool = False,
    inventory_target: str | None = None,
    query: str = "",
    fallback_reason: str = "not_configured",
    component_purposes: list[tuple[str, str]] | None = None,
) -> str:
    """Cree una respuesta local basada únicamente en evidencia cuando el LLM no esté disponible."""
    if not citations:
        return "No se encontró información en el repositorio."

    if inventory_mode:
        unique_citations = _deduplicate_citations_by_path(citations)
        file_paths = [item.path for item in unique_citations]
        component_names = [PurePosixPath(path).name for path in file_paths]
        purposes_by_name = dict(component_purposes or [])

        folders = [
            str(PurePosixPath(path).parent)
            for path in file_paths
            if str(PurePosixPath(path).parent) not in {"", "."}
        ]
        folder_counter = Counter(folders)
        top_folders = [
            folder for folder, _count in folder_counter.most_common(3)
        ]

        target_label = inventory_target or "componentes"
        lines = [
            _fallback_header(fallback_reason),
            "1) Respuesta principal:",
            (
                f"Se identificaron {len(unique_citations)} elementos para "
                f"'{target_label}' en el repositorio consultado."
            ),
            "",
            "2) Componentes/archivos clave:",
        ]
        lines.extend(f"- {name}" for name in component_names)

        if purposes_by_name:
            lines.extend([
                "",
                "3) Función probable de cada componente:",
            ])
            for name in component_names:
                purpose = purposes_by_name.get(name)
                if purpose:
                    lines.append(f"- {name}: {purpose}")

        if top_folders:
            section_number = "4" if purposes_by_name else "3"
            lines.extend([
                "",
                f"{section_number}) Organización observada en el contexto:",
            ])
            lines.extend(f"- {folder}" for folder in top_folders)

        citations_section_number = "5" if purposes_by_name else "4"
        lines.extend([
            "",
            f"{citations_section_number}) Citas de archivos con líneas:",
        ])
        lines.extend(
            (
                f"- {citation.path} "
                f"(líneas {citation.start_line}-{citation.end_line}, "
                f"score {citation.score:.4f})"
            )
            for citation in unique_citations
        )

        if query.strip():
            lines.extend([
                "",
                f"Consulta original: {query.strip()}",
            ])
        return "\n".join(lines)

    lines = [
        _fallback_header(fallback_reason),
    ]
    limit = len(citations) if inventory_mode else 5
    for index, citation in enumerate(citations[:limit], start=1):
        lines.append(
            (
                f"{index}. {citation.path} "
                f"(líneas {citation.start_line}-{citation.end_line}, "
                f"score {citation.score:.4f})"
            )
        )
    return "\n".join(lines)


def _is_module_query(query: str) -> bool:
    """Devuelve si el usuario pregunta sobre los módulos/servicios del repositorio."""
    normalized = query.lower()
    return any(
        token in normalized
        for token in ["modulo", "módulo", "module", "modulos", "módulos"]
    )


def _discover_repo_modules(repo_id: str) -> list[str]:
    """Descubra las carpetas de módulos de nivel superior del repositorio clonado localmente."""
    settings = get_settings()
    repo_path = settings.workspace_path / repo_id
    if not repo_path.exists() or not repo_path.is_dir():
        return []

    excluded_names = {
        ".git",
        ".github",
        ".vscode",
        "docs",
        "doc",
        "test",
        "tests",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        "scripts",
    }

    modules: list[str] = []
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            continue
        if name.lower() in excluded_names:
            continue
        modules.append(name)
    return modules


def _is_inventory_query(query: str) -> bool:
    """Devuelve si la consulta pide inventario de forma explícita."""
    normalized = query.lower()
    inventory_tokens = ("inventario", "inventory")
    return any(token in normalized for token in inventory_tokens)


def _is_literal_code_query(query: str) -> bool:
    """Detecta solicitudes para devolver código literal de archivo completo."""
    normalized = query.lower()
    request_signals = (
        "codigo completo",
        "código completo",
        "archivo completo",
        "source code",
        "full code",
        "full source",
        "entire file",
        "complete file",
        "show me the code",
        "dame el codigo",
        "dame el código",
        "dame todo el codigo",
        "dame todo el código",
    )
    if not any(signal in normalized for signal in request_signals):
        return False
    return bool(
        _extract_literal_file_candidates(query)
        or _extract_literal_symbol_candidates(query)
    )


def _extract_literal_file_candidates(query: str) -> list[str]:
    """Extrae candidatos de ruta/archivo potenciales para modo literal."""
    candidates: list[str] = []

    quoted_matches = re.findall(r"['\"]([^'\"]+)['\"]", query)
    for value in quoted_matches:
        token = value.strip().strip(".,;:!?()[]{}")
        if "." in token:
            candidates.append(token)

    inline_matches = re.findall(r"\b[\w./\\-]+\.[A-Za-z0-9_+-]+\b", query)
    for value in inline_matches:
        token = value.strip().strip(".,;:!?()[]{}")
        if token:
            candidates.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.replace("\\", "/")
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _extract_literal_symbol_candidates(query: str) -> list[str]:
    """Extrae candidatos de símbolo potenciales para modo literal."""
    candidates: list[str] = []

    quoted_matches = re.findall(r"['\"]([^'\"]+)['\"]", query)
    for value in quoted_matches:
        token = value.strip().strip(".,;:!?()[]{}")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            candidates.append(token)

    symbol_patterns = [
        r"(?:funcion|función|function|method|metodo|método|class|clase|symbol|simbolo|símbolo)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\)",
    ]
    for pattern in symbol_patterns:
        for match in re.finditer(pattern, query, flags=re.IGNORECASE):
            token = match.group(1)
            if token:
                candidates.append(token)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _resolve_repo_root(repo_id: str) -> Path | None:
    """Resuelve la ruta raíz del repositorio local en workspace."""
    settings = get_settings()
    candidate = (settings.workspace_path / repo_id).resolve()
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _resolve_literal_file_match(
    repo_id: str,
    query: str,
) -> tuple[Path | None, str | None, str]:
    """Resuelve un archivo para modo literal con política estricta de coincidencia."""
    candidates = _extract_literal_file_candidates(query)
    if not candidates:
        return None, None, "missing_file_hint"

    repo_root = _resolve_repo_root(repo_id)

    for candidate in candidates:
        if "/" not in candidate:
            continue
        resolved_path = _resolve_repo_file_path(repo_id=repo_id, relative_path=candidate)
        if resolved_path is None:
            continue
        if repo_root is not None:
            relative_path = PurePosixPath(resolved_path.relative_to(repo_root))
        else:
            relative_path = PurePosixPath(candidate.strip("/"))
        return resolved_path, str(relative_path), "exact_path"

    if repo_root is None:
        return None, None, "repo_not_found"

    for candidate in candidates:
        if "/" in candidate:
            continue
        matches = [
            item
            for item in repo_root.rglob(candidate)
            if item.is_file()
        ]
        if len(matches) == 1:
            relative = PurePosixPath(matches[0].relative_to(repo_root))
            return matches[0], str(relative), "exact_filename_unique"
        if len(matches) > 1:
            return None, None, "ambiguous_filename"

    return None, None, "exact_match_not_found"


def _python_symbol_spans(content: str, symbol: str) -> list[tuple[int, int]]:
    """Obtiene spans exactos para símbolos Python usando AST."""
    try:
        module_ast = ast.parse(content)
    except (SyntaxError, ValueError):
        return []

    spans: list[tuple[int, int]] = []
    for node in ast.walk(module_ast):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name != symbol:
                continue
            start_line = int(node.lineno)
            end_line = int(getattr(node, "end_lineno", node.lineno))
            spans.append((start_line, end_line))
    return spans


def _brace_block_end(lines: list[str], start_index: int) -> int:
    """Resuelve fin de bloque por llaves a partir de una línea inicial."""
    balance = 0
    opened = False
    for index in range(start_index, len(lines)):
        line = lines[index]
        for char in line:
            if char == "{":
                balance += 1
                opened = True
            elif char == "}" and opened:
                balance -= 1
                if balance <= 0:
                    return index + 1
        if opened and balance <= 0:
            return index + 1
    return start_index + 1


def _generic_symbol_spans(content: str, symbol: str) -> list[tuple[int, int]]:
    """Obtiene spans aproximados para símbolos en lenguajes no Python."""
    escaped = re.escape(symbol)
    patterns = [
        re.compile(rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{escaped}\b"),
        re.compile(rf"^\s*class\s+{escaped}\b"),
        re.compile(rf"^\s*(?:const|let|var)\s+{escaped}\s*=\s*(?:async\s*)?.*=>"),
        re.compile(
            rf"^\s*(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp|\s)+"
            rf"(?:[A-Za-z0-9_<>,\[\]\.?]+\s+)+{escaped}\s*\([^;]*\)\s*(?:\{{)?\s*$"
        ),
    ]
    lines = content.splitlines()
    spans: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if not any(pattern.match(line) for pattern in patterns):
            continue
        end_line = index + 1
        if "{" in line or any("{" in next_line for next_line in lines[index:index + 2]):
            end_line = _brace_block_end(lines, index)
        spans.append((index + 1, max(index + 1, end_line)))
    return spans


def _resolve_literal_symbol_match(
    repo_id: str,
    query: str,
) -> tuple[Path | None, str | None, int | None, int | None, str | None, str]:
    """Resuelve símbolo exacto único en archivos del repositorio."""
    candidates = _extract_literal_symbol_candidates(query)
    if not candidates:
        return None, None, None, None, None, "missing_symbol_hint"

    repo_root = _resolve_repo_root(repo_id)
    if repo_root is None:
        return None, None, None, None, None, "repo_not_found"

    allowed_suffixes = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".cs"
    }
    matches: list[tuple[Path, str, int, int, str]] = []
    for symbol in candidates:
        for file_path in repo_root.rglob("*"):
            if not file_path.is_file() or file_path.suffix.lower() not in allowed_suffixes:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            if file_path.suffix.lower() == ".py":
                spans = _python_symbol_spans(content, symbol)
            else:
                spans = _generic_symbol_spans(content, symbol)
            if not spans:
                continue

            for start_line, end_line in spans:
                relative_path = str(PurePosixPath(file_path.relative_to(repo_root)))
                matches.append((file_path, relative_path, start_line, end_line, symbol))

    if not matches:
        return None, None, None, None, None, "symbol_exact_match_not_found"
    if len(matches) > 1:
        return None, None, None, None, None, "ambiguous_symbol"

    file_path, relative_path, start_line, end_line, symbol = matches[0]
    return file_path, relative_path, start_line, end_line, symbol, "exact_symbol_unique"


def _slice_lines(content: str, start_line: int, end_line: int) -> str:
    """Extrae un rango de líneas inclusivo desde contenido de archivo."""
    lines = content.splitlines()
    if not lines:
        return ""
    safe_start = max(1, start_line)
    safe_end = max(safe_start, min(end_line, len(lines)))
    return "\n".join(lines[safe_start - 1:safe_end])


def _build_literal_code_response(repo_id: str, query: str) -> QueryResponse:
    """Construye respuesta determinística en modo código literal sin síntesis LLM."""
    file_path, relative_path, match_type = _resolve_literal_file_match(
        repo_id=repo_id,
        query=query,
    )
    start_line = 1
    end_line: int | None = None
    symbol_name: str | None = None
    target_content: str | None = None
    if (file_path is None or relative_path is None) and match_type == "missing_file_hint":
        (
            file_path,
            relative_path,
            symbol_start,
            symbol_end,
            symbol_name,
            match_type,
        ) = _resolve_literal_symbol_match(repo_id=repo_id, query=query)
        if symbol_start is not None and symbol_end is not None:
            start_line = symbol_start
            end_line = symbol_end

    if file_path is None or relative_path is None:
        answer = (
            "No puedo devolver código literal con precisión en esta consulta. "
            "Indica la ruta exacta del archivo dentro del repositorio o un "
            "nombre de archivo único."
        )
        diagnostics = {
            "literal_mode": True,
            "literal_exact_match": False,
            "literal_match_type": None,
            "literal_failure_reason": match_type,
            "fallback_reason": "literal_not_exact_match",
            "inventory_intent": False,
            "inventory_route": None,
        }
        return QueryResponse(answer=answer, citations=[], diagnostics=diagnostics)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        diagnostics = {
            "literal_mode": True,
            "literal_exact_match": False,
            "literal_match_type": match_type,
            "literal_failure_reason": "file_read_error",
            "fallback_reason": "literal_not_exact_match",
            "inventory_intent": False,
            "inventory_route": None,
        }
        return QueryResponse(
            answer=(
                "No pude leer el archivo solicitado desde el workspace local. "
                "Reintenta después de verificar que el archivo exista y sea accesible."
            ),
            citations=[],
            diagnostics=diagnostics,
        )

    if end_line is None:
        lines = content.splitlines()
        end_line = max(1, len(lines))
    else:
        target_content = _slice_lines(content, start_line, end_line)

    if target_content is None:
        target_content = content

    suffix = file_path.suffix.lower().lstrip(".") or "text"
    answer = "\n".join(
        [
            "Modo código literal (sin síntesis LLM).",
            f"Archivo: {relative_path}",
            f"Símbolo: {symbol_name}" if symbol_name else "",
            "",
            f"```{suffix}",
            target_content,
            "```",
        ]
    )
    citations = [
        Citation(
            path=relative_path,
            start_line=start_line,
            end_line=end_line,
            score=1.0,
            reason=(
                "literal_symbol_exact_match"
                if symbol_name
                else "literal_file_exact_match"
            ),
        )
    ]
    diagnostics = {
        "literal_mode": True,
        "literal_exact_match": True,
        "literal_match_type": match_type,
        "literal_source": "live_file",
        "literal_file_exists": True,
        "retrieved": 1,
        "reranked": 1,
        "raw_citations": 1,
        "filtered_citations": 1,
        "returned_citations": 1,
        "fallback_reason": None,
        "inventory_intent": False,
        "inventory_route": None,
    }
    return QueryResponse(answer=answer, citations=citations, diagnostics=diagnostics)


def _build_literal_retrieval_response(
    repo_id: str,
    query: str,
    include_context: bool,
) -> RetrievalQueryResponse:
    """Construye respuesta retrieval-only determinística para solicitudes de código literal."""
    file_path, relative_path, match_type = _resolve_literal_file_match(
        repo_id=repo_id,
        query=query,
    )
    start_line = 1
    end_line: int | None = None
    symbol_name: str | None = None
    target_content: str | None = None
    if (file_path is None or relative_path is None) and match_type == "missing_file_hint":
        (
            file_path,
            relative_path,
            symbol_start,
            symbol_end,
            symbol_name,
            match_type,
        ) = _resolve_literal_symbol_match(repo_id=repo_id, query=query)
        if symbol_start is not None and symbol_end is not None:
            start_line = symbol_start
            end_line = symbol_end

    if file_path is None or relative_path is None:
        answer = (
            "Modo retrieval-only (sin LLM): no puedo devolver código literal "
            "con precisión en esta consulta. Indica la ruta exacta del archivo "
            "dentro del repositorio o un nombre de archivo único."
        )
        return RetrievalQueryResponse(
            mode="retrieval_only",
            answer=answer,
            chunks=[],
            citations=[],
            statistics=RetrievalStatistics(
                total_before_rerank=0,
                total_after_rerank=0,
                graph_nodes_count=0,
            ),
            diagnostics={
                "mode": "retrieval_only",
                "literal_mode": True,
                "literal_exact_match": False,
                "literal_match_type": None,
                "literal_failure_reason": match_type,
                "fallback_reason": "literal_not_exact_match",
                "retrieved": 0,
                "reranked": 0,
                "graph_nodes": 0,
                "context_chars": 0,
                "raw_citations": 0,
                "filtered_citations": 0,
                "returned_citations": 0,
            },
            context=None,
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return RetrievalQueryResponse(
            mode="retrieval_only",
            answer=(
                "Modo retrieval-only (sin LLM): no pude leer el archivo "
                "solicitado desde el workspace local."
            ),
            chunks=[],
            citations=[],
            statistics=RetrievalStatistics(
                total_before_rerank=0,
                total_after_rerank=0,
                graph_nodes_count=0,
            ),
            diagnostics={
                "mode": "retrieval_only",
                "literal_mode": True,
                "literal_exact_match": False,
                "literal_match_type": match_type,
                "literal_failure_reason": "file_read_error",
                "fallback_reason": "literal_not_exact_match",
                "retrieved": 0,
                "reranked": 0,
                "graph_nodes": 0,
                "context_chars": 0,
                "raw_citations": 0,
                "filtered_citations": 0,
                "returned_citations": 0,
            },
            context=None,
        )

    if end_line is None:
        lines = content.splitlines()
        end_line = max(1, len(lines))
    else:
        target_content = _slice_lines(content, start_line, end_line)

    if target_content is None:
        target_content = content

    chunk = RetrievedChunk(
        id=f"literal:{relative_path}:{start_line}:{end_line}",
        text=target_content,
        score=1.0,
        path=relative_path,
        start_line=start_line,
        end_line=end_line,
        kind="literal_symbol" if symbol_name else "literal_file",
        metadata={
            "path": relative_path,
            "start_line": start_line,
            "end_line": end_line,
            "kind": "literal_symbol" if symbol_name else "literal_file",
            "literal_mode": True,
            "symbol_name": symbol_name,
        },
    )
    citation = Citation(
        path=relative_path,
        start_line=start_line,
        end_line=end_line,
        score=1.0,
        reason=(
            "literal_symbol_exact_match"
            if symbol_name
            else "literal_file_exact_match"
        ),
    )
    answer = "\n".join(
        [
            "Modo retrieval-only (sin LLM): código literal exacto.",
            f"Archivo: {relative_path}",
            f"Símbolo: {symbol_name}" if symbol_name else "",
            "",
            target_content,
        ]
    )
    context = target_content if include_context else None
    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer=answer,
        chunks=[chunk],
        citations=[citation],
        statistics=RetrievalStatistics(
            total_before_rerank=1,
            total_after_rerank=1,
            graph_nodes_count=0,
        ),
        diagnostics={
            "mode": "retrieval_only",
            "literal_mode": True,
            "literal_exact_match": True,
            "literal_match_type": match_type,
            "literal_source": "live_file",
            "literal_file_exists": True,
            "retrieved": 1,
            "reranked": 1,
            "graph_nodes": 0,
            "context_chars": len(context or ""),
            "raw_citations": 1,
            "filtered_citations": 1,
            "returned_citations": 1,
            "fallback_reason": None,
        },
        context=context,
    )


def _extract_module_name(query: str) -> str | None:
    """Extraiga el token del módulo o del paquete de una consulta en lenguaje natural."""
    normalized = query.lower()

    quoted = re.search(r"['\"]([a-z0-9_./-]+)['\"]", normalized)
    if quoted:
        return quoted.group(1)

    anchored_patterns = [
        (
            r"(?:carpeta|folder|directorio|directory|"
            r"modulo|módulo|module|package)\s+"
            r"(?:del?\s+|de\s+la\s+|de\s+los\s+|de\s+las\s+)?"
            r"([a-z0-9_./-]+)"
        ),
        (
            r"(?:componentes?|elements?|archivos?|files?)\s+"
            r"(?:de|en|in|from|of)\s+"
            r"(?:la|el|los|las|the)?\s*"
            r"(?:carpeta|folder|directorio|directory|modulo|módulo|module|package)?\s*"
            r"([a-z0-9_./-]+)"
        ),
    ]
    for pattern in anchored_patterns:
        for match in re.finditer(pattern, normalized):
            token = match.group(1).strip(".,;:!?()[]{}")
            if token and token not in MODULE_NAME_STOPWORDS:
                return token

    patterns = [
        r"(?:modulo|módulo|module|package|servicio|service)\s+([a-z0-9_./-]+)",
        r"(?:in|en|de|del|of|for)\s+([a-z0-9_./-]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            token = match.group(1).strip(".,;:!?()[]{}")
            if token and token not in MODULE_NAME_STOPWORDS:
                return token

    module_like = re.search(r"\b([a-z0-9]+(?:[-_/][a-z0-9]+)+)\b", normalized)
    if module_like:
        return module_like.group(1)
    return None


def _normalize_inventory_token(token: str) -> str:
    """Normalice el token de inventario poniendo minúsculas y eliminando acentos/puntuación."""
    lowered = token.lower().strip(".,;:!?()[]{}")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _inventory_base_forms(token: str) -> set[str]:
    """Cree formularios base candidatos a partir de variantes plurales/singulares."""
    normalized = _normalize_inventory_token(token)
    forms = {normalized}

    if normalized.endswith("ies") and len(normalized) > 3:
        forms.add(normalized[:-3] + "y")

    if normalized.endswith("es") and len(normalized) > 3:
        es_root = normalized[:-2]
        if normalized.endswith(
            (
                "ses",
                "xes",
                "zes",
                "ches",
                "shes",
                "ores",
                "dores",
                "tores",
                "ciones",
                "siones",
                "ades",
                "udes",
            )
        ):
            forms.add(es_root)

    if normalized.endswith("s") and len(normalized) > 2:
        forms.add(normalized[:-1])

    return {form for form in forms if form}


def _canonical_inventory_term(token: str) -> str:
    """Devuelve el término de inventario canónico desde los formularios base disponibles."""
    forms = _inventory_base_forms(token)
    known_terms = {
        term
        for group in INVENTORY_EQUIVALENT_GROUPS
        for term in group
    }
    for form in sorted(forms, key=lambda item: (len(item), item)):
        if form in known_terms:
            return form
    return _normalize_inventory_token(token)


def _plural_variants(token: str) -> set[str]:
    """Genere variantes plurales/superficiales para un término de inventario normalizado."""
    variants = {token}
    if not token:
        return variants

    if token.endswith(("s", "x", "z", "ch", "sh", "or", "ion", "dad", "dor")):
        variants.add(f"{token}es")
    else:
        variants.add(f"{token}s")
    if token.endswith("y") and len(token) > 1:
        variants.add(f"{token[:-1]}ies")
    return variants


def _deduplicate_citations(citations: list[Citation]) -> list[Citation]:
    """Deduplicar citas manteniendo el orden de primera aparición."""
    seen: set[tuple[str, int, int]] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = (
            citation.path,
            citation.start_line,
            citation.end_line,
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(citation)
    return deduplicated


def _deduplicate_citations_by_path(citations: list[Citation]) -> list[Citation]:
    """Deduplica citas por ruta manteniendo el orden de primera aparición."""
    seen_paths: set[str] = set()
    deduplicated: list[Citation] = []
    for citation in citations:
        key = citation.path.strip().lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduplicated.append(citation)
    return deduplicated


def _extract_inventory_target(query: str) -> str | None:
    """Extraiga el token de la entidad de destino de consultas en lenguaje natural estilo inventario."""
    normalized = query.lower()

    # Explicit type specifiers: "de tipo X", "tipo X" (highest priority)
    # This catches queries like "componentes de tipo controller"
    match_type_spec = re.search(
        r"(?:de\s+)?tipo\s+(?:de\s+)?([a-z0-9_-]+)",
        normalized,
    )
    if match_type_spec:
        token = match_type_spec.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    # Component/element + type combinations: "componentes X", "elementos X" (second priority)
    # This catches queries like "componentes controller" or "elementos service"
    match_component_type = re.search(
        r"(?:componentes?|elements?|elementi?s)\s+([a-z0-9_-]+)",
        normalized,
    )
    if match_component_type:
        token = match_component_type.group(1)
        # Exclude prepositions that might have been captured
        if token not in INVENTORY_TARGET_STOPWORDS and token not in {"de", "del", "de la", "de los"}:
            return _canonical_inventory_term(token)

    # Generic patterns (lower priority)
    match_es = re.search(
        r"tod(?:os|as)?\s+(?:los|las)?\s*([a-z0-9_-]+)",
        normalized,
    )
    if match_es:
        token = match_es.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    match_cuales = re.search(
        r"cuales?\s+son\s+(?:tod(?:os|as)?\s+)?(?:los|las)?\s*([a-z0-9_-]+)",
        normalized,
    )
    if match_cuales:
        token = match_cuales.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    match_lista = re.search(r"(?:lista|listar)\s+(?:los|las)?\s*([a-z0-9_-]+)", normalized)
    if match_lista:
        token = match_lista.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    match_en = re.search(r"all\s+([a-z0-9_-]+)", normalized)
    if match_en:
        token = match_en.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    match_which = re.search(r"which\s+([a-z0-9_-]+)", normalized)
    if match_which:
        token = match_which.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return _canonical_inventory_term(token)

    return None


def _is_inventory_explain_query(query: str) -> bool:
    """Devuelve si la consulta solicita explicar el rol/función por componente listado."""
    normalized = _normalize_inventory_token(query)
    explanation_signals = [
        "que funcion",
        "que hace",
        "para que sirve",
        "funcion cumplen",
        "funcion de cada",
        "explain",
        "what each",
        "each one does",
        "what each one does",
        "function of each",
        "role of each",
        "what does",
        "what do",
        "purpose",
    ]
    return any(signal in normalized for signal in explanation_signals)


def _resolve_repo_file_path(repo_id: str, relative_path: str) -> Path | None:
    """Resuelva y valide la ruta relativa al repositorio a un archivo local existente."""
    normalized = relative_path.strip().replace("\\", "/").strip("/")
    if not normalized:
        return None

    settings = get_settings()
    repo_root = (settings.workspace_path / repo_id).resolve()
    candidate = (repo_root / normalized).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None

    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _first_sentence(text: str) -> str:
    """Devuelve el primer fragmento similar a una oración sin puntuación final."""
    first = re.split(r"[\.\n\r]", text, maxsplit=1)[0].strip()
    return first.rstrip(" \t\"'`.,;:!?¡¿")


def _purpose_from_filename(file_path: Path) -> str | None:
    """Inferir sugerencias de propósito a partir de la raíz del nombre de archivo utilizando heurísticas ligeras."""
    stem = file_path.stem.lower()
    filename = file_path.name.lower()

    if filename == "requirements.txt":
        return "Declara dependencias Python del proyecto para instalación y despliegue."
    if filename in {"pyproject.toml", "poetry.lock"}:
        return "Define metadata del proyecto y dependencias Python gestionadas por herramientas modernas."
    if filename in {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}:
        return "Declara dependencias JavaScript/TypeScript y scripts de construcción del proyecto."
    if filename in {"pom.xml", "build.gradle", "build.gradle.kts", "gradle.properties"}:
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


def _build_purpose_from_source(file_path: Path) -> str | None:
    """Inferir el propósito conciso del componente a partir de la primera declaración de fuente identificable."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    fallback_hint = _purpose_from_filename(file_path)
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
                summary = _first_sentence(module_doc)
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
                        token in normalized
                        for token in ("logging", "log")
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
                return f"Declara el {java_kind} `{name}` y concentra lógica principal del componente."
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
                return f"Declara la clase `{name}` y centraliza responsabilidades del componente."
            if kind == "function":
                return f"Define la función `{name}` y encapsula comportamiento reutilizable."

    if fallback_hint:
        return fallback_hint
    return "Contiene implementación de soporte del componente en este módulo."


def _describe_inventory_components(
    repo_id: str,
    citations: list[Citation],
    pipeline_started_at: float,
    budget_seconds: float,
    query: str | None = None,
) -> list[tuple[str, str]]:
    """Cree sugerencias de propósito por componente a partir de archivos fuente locales dentro del presupuesto."""
    descriptions: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    for citation in citations:
        if _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0:
            break
        path = citation.path.strip()
        component_name = PurePosixPath(path).name
        if not component_name or component_name in seen_names:
            continue
        file_path = _resolve_repo_file_path(repo_id=repo_id, relative_path=path)
        if file_path is None:
            continue
        purpose = _build_purpose_from_source(file_path)
        if purpose is None:
            continue
        seen_names.add(component_name)
        descriptions.append((component_name, purpose))

    if not descriptions:
        return descriptions

    if not query:
        return descriptions

    normalized_query = _normalize_inventory_token(query)
    query_tokens = set(re.findall(r"[a-z0-9]+", normalized_query))
    if not query_tokens:
        return descriptions

    def _score(item: tuple[str, str]) -> tuple[int, int, str]:
        name, purpose = item
        haystack = _normalize_inventory_token(f"{name} {purpose}")
        overlap = sum(1 for token in query_tokens if token in haystack)
        return (overlap, len(purpose), name.lower())

    return sorted(descriptions, key=_score, reverse=True)
    return descriptions


def _inventory_term_aliases(target_term: str) -> list[str]:
    """Amplíe el objetivo del inventario con alias en plural y en varios idiomas."""
    base_forms = _inventory_base_forms(target_term)
    aliases: set[str] = set()
    for form in base_forms:
        aliases.update(_plural_variants(form))

    for group in INVENTORY_EQUIVALENT_GROUPS:
        if base_forms.intersection(group):
            for token in group:
                normalized = _normalize_inventory_token(token)
                aliases.update(_plural_variants(normalized))

    return sorted(aliases)


def _query_inventory_entities(
    repo_id: str,
    target_term: str,
    module_name: str | None,
) -> list[dict]:
    """Consulta entidades de inventario desde un gráfico utilizando un término objetivo genérico."""
    settings = get_settings()
    graph = GraphBuilder()
    try:
        canonical_target = _canonical_inventory_term(target_term)
        if module_name and canonical_target in BROAD_FILE_INVENTORY_TERMS:
            module_files = graph.query_module_files(
                repo_id=repo_id,
                module_name=module_name,
                limit=settings.inventory_entity_limit,
            )
            return sorted(module_files, key=lambda item: item.get("path", ""))

        entities_by_key: dict[tuple[str, int, int], dict] = {}
        aliases = _inventory_term_aliases(target_term)[: settings.inventory_alias_limit]
        if not aliases:
            return []

        alias_results: dict[str, list[dict]] = {}
        if len(aliases) == 1:
            alias = aliases[0]
            alias_results[alias] = graph.query_inventory(
                repo_id=repo_id,
                target_term=alias,
                module_name=module_name,
                limit=settings.inventory_entity_limit,
            )
        else:
            max_workers = min(4, len(aliases))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    alias: executor.submit(
                        graph.query_inventory,
                        repo_id,
                        alias,
                        module_name,
                        settings.inventory_entity_limit,
                    )
                    for alias in aliases
                }
                alias_results = {
                    alias: future.result()
                    for alias, future in futures.items()
                }

        for alias in aliases:
            entities = alias_results.get(alias, [])
            for item in entities:
                path = str(item.get("path", ""))
                start_line = int(item.get("start_line", 1))
                end_line = int(item.get("end_line", 1))
                key = (path, start_line, end_line)
                if key not in entities_by_key:
                    entities_by_key[key] = item
        return sorted(entities_by_key.values(), key=lambda item: item.get("path", ""))
    except Exception:
        return []
    finally:
        graph.close()


def _resolve_module_scope(repo_id: str, module_name: str | None) -> str | None:
    """Resuelva el token del módulo de usuario en el alcance del directorio relativo al repositorio canónico."""
    if not module_name:
        return None

    cleaned = module_name.strip().strip("/\\").replace("\\", "/")
    if not cleaned:
        return None

    settings = get_settings()
    repo_path = settings.workspace_path / repo_id
    if not repo_path.exists() or not repo_path.is_dir():
        return cleaned

    cache_key = (str(repo_path.resolve()), cleaned.lower())
    with _MODULE_SCOPE_CACHE_LOCK:
        cached = _MODULE_SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    direct = (repo_path / cleaned)
    if direct.exists() and direct.is_dir():
        with _MODULE_SCOPE_CACHE_LOCK:
            _MODULE_SCOPE_CACHE[cache_key] = cleaned
        return cleaned

    lowered = cleaned.lower()
    matches: list[str] = []
    for directory in repo_path.rglob("*"):
        if not directory.is_dir():
            continue
        relative = directory.relative_to(repo_path).as_posix()
        rel_lower = relative.lower()
        if directory.name.lower() == lowered or rel_lower.endswith(f"/{lowered}"):
            matches.append(relative)

    if not matches:
        with _MODULE_SCOPE_CACHE_LOCK:
            _MODULE_SCOPE_CACHE[cache_key] = cleaned
        return cleaned

    matches.sort(key=lambda item: (item.count("/"), len(item), item))
    resolved = matches[0]
    with _MODULE_SCOPE_CACHE_LOCK:
        _MODULE_SCOPE_CACHE[cache_key] = resolved
    return resolved


def _sanitize_inventory_pagination(page: int, page_size: int) -> tuple[int, int]:
    """Normalice los argumentos de paginación de inventario frente a los límites configurados."""
    settings = get_settings()
    safe_page = max(1, int(page))
    default_size = max(1, settings.inventory_page_size)
    requested_size = int(page_size) if int(page_size) > 0 else default_size
    safe_page_size = min(max(1, requested_size), settings.inventory_max_page_size)
    return safe_page, safe_page_size


def _remaining_budget_seconds(started_at: float, budget_seconds: float) -> float:
    """Devuelve el presupuesto restante (segundos) para una canalización de consultas en ejecución."""
    elapsed = monotonic() - started_at
    return max(0.0, budget_seconds - elapsed)


def _elapsed_milliseconds(started_at: float) -> float:
    """Devuelve los milisegundos transcurridos redondeados para facilitar la lectura del diagnóstico."""
    return round((monotonic() - started_at) * 1000, 2)


def _citation_priority(citation: Citation) -> tuple[int, float]:
    """Asigne prioridad de clasificación utilizando señales genéricas de calidad de ruta."""
    path = citation.path.strip().lower()
    suffix = Path(path).suffix
    code_like_suffixes = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go",
        ".rs", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".php",
        ".rb", ".swift", ".scala", ".sql", ".sh", ".ps1", ".yaml",
        ".yml", ".json", ".toml", ".md", ".xml",
    }
    if suffix in code_like_suffixes:
        rank = 0
    elif "/" in path or "\\" in path:
        rank = 1
    elif path:
        rank = 2
    else:
        rank = 3
    return (rank, -citation.score)


def _safe_discover_repo_modules(repo_id: str, query: str) -> list[str]:
    """Descubre módulos solo cuando la consulta contiene intención de módulo."""
    if not _is_module_query(query):
        return []
    try:
        return _discover_repo_modules(repo_id)
    except Exception:
        return []


def _timed_graph_expand(
    chunks: list[RetrievalChunk],
) -> tuple[list[dict], float, dict[str, object]]:
    """Ejecuta expansión de grafo y devuelve resultado/latencia/diagnostics."""
    started_at = monotonic()
    result, semantic_diagnostics = expand_with_graph_with_diagnostics(chunks=chunks)
    return result, _elapsed_milliseconds(started_at), semantic_diagnostics


def _timed_module_discovery(repo_id: str, query: str) -> tuple[list[str], float]:
    """Ejecuta descubrimiento de módulos y devuelve resultado junto con latencia en ms."""
    started_at = monotonic()
    result = _safe_discover_repo_modules(repo_id=repo_id, query=query)
    return result, _elapsed_milliseconds(started_at)


def _is_context_sufficient(context: str, reranked_count: int) -> bool:
    """Evalúa si el contexto tiene señal mínima para responder con LLM."""
    if reranked_count <= 0:
        return False
    if not context.strip():
        return False
    return len(context.strip()) >= 80


def _build_retrieval_answer(chunks: list[RetrievedChunk], query: str) -> str:
    """Construye salida textual diferenciada para modo retrieval-only."""
    if not chunks:
        return "Modo retrieval-only (sin LLM): no se encontró evidencia relevante."

    lines = [
        "Modo retrieval-only (sin LLM):",
        f"Se recuperaron {len(chunks)} fragmentos relevantes para: {query.strip()}",
        "",
        "Evidencia principal:",
    ]
    for index, chunk in enumerate(chunks[:5], start=1):
        lines.append(
            (
                f"{index}. {chunk.path} "
                f"(líneas {chunk.start_line}-{chunk.end_line}, score {chunk.score:.4f})"
            )
        )
    return "\n".join(lines)


def _build_retrieval_inventory_response(
    *,
    inventory_response: InventoryQueryResponse,
    include_context: bool,
) -> RetrievalQueryResponse:
    """Adapta una respuesta de inventario al contrato retrieval-only."""
    chunks: list[RetrievedChunk] = []
    for item in inventory_response.items:
        chunk_id = f"inventory:{item.path}:{item.start_line}:{item.end_line}"
        chunks.append(
            RetrievedChunk(
                id=chunk_id,
                text=item.label,
                score=1.0,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                kind=item.kind,
                metadata={
                    "path": item.path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "kind": item.kind,
                    "inventory_label": item.label,
                },
            )
        )

    diagnostics = dict(inventory_response.diagnostics)
    diagnostics.update(
        {
            "mode": "retrieval_only",
            "inventory_route": "graph_first_retrieval",
            "inventory_page": inventory_response.page,
            "inventory_page_size": inventory_response.page_size,
            "inventory_total": inventory_response.total,
            "retrieved": inventory_response.total,
            "reranked": len(chunks),
            "graph_nodes": 0,
            "context_chars": 0,
            "raw_citations": len(inventory_response.citations),
            "filtered_citations": len(inventory_response.citations),
            "returned_citations": len(inventory_response.citations),
        }
    )

    context: str | None = None
    if include_context and chunks:
        context_lines = [
            "INVENTORY_CONTEXT:",
            *[
                (
                    f"- {chunk.path} "
                    f"(líneas {chunk.start_line}-{chunk.end_line}) "
                    f"=> {chunk.text}"
                )
                for chunk in chunks
            ],
        ]
        context = "\n".join(context_lines)
        diagnostics["context_chars"] = len(context)

    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer=inventory_response.answer,
        chunks=chunks,
        citations=inventory_response.citations,
        statistics=RetrievalStatistics(
            total_before_rerank=inventory_response.total,
            total_after_rerank=len(chunks),
            graph_nodes_count=0,
        ),
        diagnostics=diagnostics,
        context=context,
    )


def run_inventory_query(
    repo_id: str,
    query: str,
    page: int,
    page_size: int,
) -> InventoryQueryResponse:
    """Ejecute una consulta de inventario basada en gráficos con paginación y presupuesto de tiempo."""
    settings = get_settings()
    budget_seconds = max(1.0, float(settings.query_max_seconds))
    pipeline_started_at = monotonic()
    stage_timings: dict[str, float] = {}

    parse_started_at = monotonic()
    inventory_target = _extract_inventory_target(query)
    explain_inventory = _is_inventory_explain_query(query)
    module_name_raw = _extract_module_name(query)
    module_name = _resolve_module_scope(repo_id=repo_id, module_name=module_name_raw)
    inventory_terms = _inventory_term_aliases(inventory_target) if inventory_target else []
    safe_page, safe_page_size = _sanitize_inventory_pagination(page, page_size)
    stage_timings["parse_ms"] = _elapsed_milliseconds(parse_started_at)

    if not inventory_target:
        stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
        diagnostics = build_inventory_missing_target_diagnostics(
            explain_inventory=explain_inventory,
            module_name_raw=module_name_raw,
            module_name=module_name,
            budget_seconds=budget_seconds,
            stage_timings=stage_timings,
        )
        return InventoryQueryResponse(
            answer="No se detectó un objetivo de inventario en la consulta.",
            target=None,
            module_name=module_name,
            total=0,
            page=safe_page,
            page_size=safe_page_size,
            items=[],
            citations=[],
            diagnostics=diagnostics,
        )

    inventory_target_normalized = _normalize_inventory_token(inventory_target)
    auto_context_for_inventory = inventory_target_normalized in DEPENDENCY_INVENTORY_TERMS

    fallback_reason: str | None = None
    discovered_inventory: list[dict] = []

    if _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0:
        fallback_reason = "time_budget_exhausted"
    else:
        graph_started_at = monotonic()
        discovered_inventory = _query_inventory_entities(
            repo_id=repo_id,
            target_term=inventory_target,
            module_name=module_name,
        )
        stage_timings["graph_inventory_ms"] = _elapsed_milliseconds(graph_started_at)

    pagination_started_at = monotonic()
    total_items = len(discovered_inventory)
    offset = (safe_page - 1) * safe_page_size
    paged_inventory = discovered_inventory[offset:offset + safe_page_size]
    stage_timings["pagination_ms"] = _elapsed_milliseconds(pagination_started_at)

    items = [
        InventoryItem(
            label=str(item.get("label", "")),
            path=str(item.get("path", "unknown")),
            kind=str(item.get("kind", "file")),
            start_line=int(item.get("start_line", 1)),
            end_line=int(item.get("end_line", 1)),
        )
        for item in paged_inventory
    ]

    citations = build_inventory_citations(items)

    if (
        fallback_reason is None
        and _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
    ):
        fallback_reason = "time_budget_exhausted"

    purpose_started_at = monotonic()
    component_purposes: list[tuple[str, str]] = []
    if (explain_inventory or auto_context_for_inventory) and citations:
        component_purposes = _describe_inventory_components(
            repo_id=repo_id,
            citations=citations,
            pipeline_started_at=pipeline_started_at,
            budget_seconds=budget_seconds,
            query=query,
        )
    stage_timings["component_purpose_ms"] = _elapsed_milliseconds(purpose_started_at)

    answer = _build_extractive_fallback(
        citations,
        inventory_mode=True,
        inventory_target=inventory_target,
        query=query,
        fallback_reason=fallback_reason or "inventory_structured",
        component_purposes=component_purposes,
    )

    stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
    diagnostics = build_inventory_diagnostics(
        inventory_target=inventory_target,
        inventory_terms=inventory_terms,
        inventory_count=total_items,
        explain_inventory=explain_inventory,
        inventory_purpose_count=len(component_purposes),
        module_name_raw=module_name_raw,
        module_name=module_name,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        fallback_reason=fallback_reason,
    )

    return InventoryQueryResponse(
        answer=answer,
        target=inventory_target,
        module_name=module_name,
        total=total_items,
        page=safe_page,
        page_size=safe_page_size,
        items=items,
        citations=citations,
        diagnostics=diagnostics,
    )


def run_retrieval_query(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    include_context: bool = False,
) -> RetrievalQueryResponse:
    """Ejecuta retrieval híbrido sin síntesis LLM y retorna evidencia estructurada."""
    settings = get_settings()
    inventory_intent = _is_inventory_query(query)
    inventory_target = _extract_inventory_target(query) if inventory_intent else None
    if inventory_intent and inventory_target:
        inventory_response = run_inventory_query(
            repo_id=repo_id,
            query=query,
            page=1,
            page_size=settings.inventory_page_size,
        )
        return _build_retrieval_inventory_response(
            inventory_response=inventory_response,
            include_context=include_context,
        )

    if _is_literal_code_query(query):
        return _build_literal_retrieval_response(
            repo_id=repo_id,
            query=query,
            include_context=include_context,
        )

    budget_seconds = max(1.0, float(settings.query_max_seconds))
    resolved_embedding_provider = (
        settings.resolve_embedding_provider(embedding_provider)
        if hasattr(settings, "resolve_embedding_provider")
        else (embedding_provider or "openai")
    )
    resolved_embedding_model = (
        settings.resolve_embedding_model(
            resolved_embedding_provider,
            embedding_model,
        )
        if hasattr(settings, "resolve_embedding_model")
        else (
            embedding_model
            or getattr(settings, "openai_embedding_model", "text-embedding-3-small")
        )
    )

    pipeline_started_at = monotonic()
    stage_timings: dict[str, float] = {}

    retrieval_started_at = monotonic()
    initial = hybrid_search(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    stage_timings["hybrid_search_ms"] = _elapsed_milliseconds(retrieval_started_at)

    rerank_started_at = monotonic()
    reranked = rerank(chunks=initial, top_k=top_k)
    stage_timings["rerank_ms"] = _elapsed_milliseconds(rerank_started_at)

    graph_started_at = monotonic()
    graph_context, semantic_expand_diagnostics = expand_with_graph_with_diagnostics(
        chunks=reranked
    )
    stage_timings["graph_expand_ms"] = _elapsed_milliseconds(graph_started_at)

    context: str | None = None
    context_chars = 0
    if include_context:
        context_started_at = monotonic()
        context = assemble_context(
            chunks=reranked,
            graph_records=graph_context,
            max_tokens=settings.max_context_tokens,
        )
        stage_timings["context_assembly_ms"] = _elapsed_milliseconds(context_started_at)
        context_chars = len(context)

    raw_citations = [
        Citation(
            path=item.metadata.get("path", "unknown"),
            start_line=int(item.metadata.get("start_line", 0)),
            end_line=int(item.metadata.get("end_line", 0)),
            score=float(item.score),
            reason="hybrid_rag_match",
        )
        for item in reranked
    ]
    filtered_citations = [
        item for item in raw_citations if not is_noisy_path(item.path)
    ]
    citations_source = filtered_citations or raw_citations
    citations = sorted(citations_source, key=_citation_priority)

    chunks: list[RetrievedChunk] = []
    for item in reranked:
        metadata = dict(item.metadata)
        chunks.append(
            RetrievedChunk(
                id=item.id,
                text=item.text,
                score=float(item.score),
                path=str(metadata.get("path", "unknown")),
                start_line=int(metadata.get("start_line", 0)),
                end_line=int(metadata.get("end_line", 0)),
                kind=str(metadata.get("kind", "code_chunk")),
                metadata=metadata,
            )
        )

    answer = _build_retrieval_answer(chunks=chunks, query=query)
    stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
    diagnostics = build_retrieval_diagnostics(
        settings=settings,
        retrieved_count=len(initial),
        reranked_count=len(reranked),
        graph_nodes_count=len(graph_context),
        context_chars=context_chars,
        raw_citations_count=len(raw_citations),
        filtered_citations_count=len(filtered_citations),
        returned_citations_count=len(citations),
        embedding_provider=resolved_embedding_provider,
        embedding_model=resolved_embedding_model,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        fallback_reason=None,
        semantic_diagnostics=semantic_expand_diagnostics,
    )

    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer=answer,
        chunks=chunks,
        citations=citations,
        statistics=RetrievalStatistics(
            total_before_rerank=len(initial),
            total_after_rerank=len(reranked),
            graph_nodes_count=len(graph_context),
        ),
        diagnostics=diagnostics,
        context=context,
    )


def run_query(
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    llm_provider: str | None = None,
    answer_model: str | None = None,
    verifier_model: str | None = None,
) -> QueryResponse:
    """Ejecute el proceso de consulta completo y devuelva la respuesta con citas."""
    settings = get_settings()
    inventory_intent = _is_inventory_query(query)
    inventory_target = _extract_inventory_target(query) if inventory_intent else None
    if inventory_intent and inventory_target:
        inventory_response = run_inventory_query(
            repo_id=repo_id,
            query=query,
            page=1,
            page_size=settings.inventory_page_size,
        )
        diagnostics = dict(inventory_response.diagnostics)
        diagnostics.update(
            {
                "inventory_route": "graph_first",
                "inventory_page": inventory_response.page,
                "inventory_page_size": inventory_response.page_size,
                "inventory_total": inventory_response.total,
            }
        )
        return QueryResponse(
            answer=inventory_response.answer,
            citations=inventory_response.citations,
            diagnostics=diagnostics,
        )

    if _is_literal_code_query(query):
        return _build_literal_code_response(repo_id=repo_id, query=query)

    budget_seconds = max(1.0, float(settings.query_max_seconds))
    verify_enabled = (
        settings.is_verify_enabled()
        if hasattr(settings, "is_verify_enabled")
        else bool(getattr(settings, "openai_verify_enabled", True))
    )
    resolved_embedding_provider = (
        settings.resolve_embedding_provider(embedding_provider)
        if hasattr(settings, "resolve_embedding_provider")
        else (embedding_provider or "openai")
    )
    resolved_embedding_model = (
        settings.resolve_embedding_model(
            resolved_embedding_provider,
            embedding_model,
        )
        if hasattr(settings, "resolve_embedding_model")
        else (
            embedding_model
            or getattr(settings, "openai_embedding_model", "text-embedding-3-small")
        )
    )
    pipeline_started_at = monotonic()
    stage_timings: dict[str, float] = {}

    retrieval_started_at = monotonic()
    initial = hybrid_search(
        repo_id=repo_id,
        query=query,
        top_n=top_n,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
    )
    stage_timings["hybrid_search_ms"] = _elapsed_milliseconds(retrieval_started_at)

    rerank_started_at = monotonic()
    reranked = rerank(chunks=initial, top_k=top_k)
    stage_timings["rerank_ms"] = _elapsed_milliseconds(rerank_started_at)

    parallel_started_at = monotonic()
    with ThreadPoolExecutor(max_workers=2) as executor:
        graph_future = executor.submit(_timed_graph_expand, reranked)
        modules_future = executor.submit(_timed_module_discovery, repo_id, query)
        graph_context, graph_ms, semantic_expand_diagnostics = graph_future.result()
        discovered_modules, module_ms = modules_future.result()
    stage_timings["graph_expand_ms"] = graph_ms
    stage_timings["module_discovery_ms"] = module_ms
    stage_timings["post_rerank_parallel_ms"] = _elapsed_milliseconds(parallel_started_at)

    context_started_at = monotonic()
    context = assemble_context(
        chunks=reranked,
        graph_records=graph_context,
        max_tokens=settings.max_context_tokens,
    )
    if discovered_modules:
        module_block = "\n".join(
            [
                "MODULE_INVENTORY:",
                *[f"- {module}" for module in discovered_modules],
            ]
        )
        context = f"{module_block}\n\n{context}"
    stage_timings["context_assembly_ms"] = _elapsed_milliseconds(context_started_at)

    raw_citations = [
        Citation(
            path=item.metadata.get("path", "unknown"),
            start_line=int(item.metadata.get("start_line", 0)),
            end_line=int(item.metadata.get("end_line", 0)),
            score=float(item.score),
            reason="hybrid_rag_match",
        )
        for item in reranked
    ]

    filtered_citations = [
        item for item in raw_citations if not is_noisy_path(item.path)
    ]
    citations_source = filtered_citations
    if not citations_source and raw_citations:
        citations_source = raw_citations
    citations = sorted(citations_source, key=_citation_priority)

    client = AnswerClient(
        provider=llm_provider,
        answer_model=answer_model,
        verifier_model=verifier_model,
    )
    fallback_reason: str | None = None
    verify_valid: bool | None = None
    verify_skipped = False
    llm_error: str | None = None

    context_sufficient = _is_context_sufficient(context=context, reranked_count=len(reranked))

    if not context_sufficient:
        fallback_reason = "insufficient_context"
        answer = _build_extractive_fallback(
            citations,
            query=query,
            fallback_reason=fallback_reason,
        )
    elif client.enabled and _remaining_budget_seconds(pipeline_started_at, budget_seconds) > 0:
        try:
            answer_started_at = monotonic()
            answer_timeout = min(
                float(settings.openai_timeout_seconds),
                _remaining_budget_seconds(pipeline_started_at, budget_seconds),
            )
            if answer_timeout <= 0:
                fallback_reason = "time_budget_exhausted"
                answer = _build_extractive_fallback(
                    citations,
                    query=query,
                    fallback_reason=fallback_reason,
                )
            else:
                answer = client.answer(
                    query=query,
                    context=context,
                    timeout_seconds=answer_timeout,
                )
                stage_timings["llm_answer_ms"] = _elapsed_milliseconds(answer_started_at)

                if not verify_enabled:
                    verify_skipped = True
                else:
                    verify_timeout = min(
                        float(settings.openai_timeout_seconds),
                        _remaining_budget_seconds(pipeline_started_at, budget_seconds),
                    )
                    if verify_timeout <= 0:
                        verify_skipped = True
                    else:
                        verify_started_at = monotonic()
                        verify_valid = client.verify(
                            answer=answer,
                            context=context,
                            timeout_seconds=verify_timeout,
                        )
                        stage_timings["llm_verify_ms"] = _elapsed_milliseconds(verify_started_at)
                        if not verify_valid:
                            fallback_reason = "verification_failed"
                            answer = _build_extractive_fallback(
                                citations,
                                query=query,
                                fallback_reason=fallback_reason,
                            )
        except Exception as exc:
            fallback_reason = "generation_error"
            llm_error = str(exc)
            answer = _build_extractive_fallback(
                citations,
                query=query,
                fallback_reason=fallback_reason,
            )
    else:
        if not client.enabled:
            fallback_reason = "not_configured"
        else:
            fallback_reason = "time_budget_exhausted"
        answer = _build_extractive_fallback(
            citations,
            query=query,
            fallback_reason=fallback_reason,
        )

    stage_timings["total_ms"] = _elapsed_milliseconds(pipeline_started_at)
    diagnostics = build_query_diagnostics(
        settings=settings,
        retrieved_count=len(initial),
        reranked_count=len(reranked),
        graph_nodes_count=len(graph_context),
        context_chars=len(context),
        raw_citations_count=len(raw_citations),
        filtered_citations_count=len(filtered_citations),
        returned_citations_count=len(citations),
        context_sufficient=context_sufficient,
        llm_enabled=client.enabled,
        llm_provider=client.provider,
        llm_answer_model=client.answer_model,
        llm_verifier_model=client.verifier_model,
        verify_enabled=verify_enabled,
        embedding_provider=resolved_embedding_provider,
        embedding_model=resolved_embedding_model,
        discovered_modules=discovered_modules,
        fallback_reason=fallback_reason,
        verify_valid=verify_valid,
        verify_skipped=verify_skipped,
        budget_seconds=budget_seconds,
        budget_exhausted=(
            _remaining_budget_seconds(pipeline_started_at, budget_seconds) <= 0
        ),
        stage_timings=stage_timings,
        inventory_intent=inventory_intent,
        inventory_target=inventory_target,
        llm_error=llm_error,
        semantic_diagnostics=semantic_expand_diagnostics,
    )
    return QueryResponse(answer=answer, citations=citations, diagnostics=diagnostics)
