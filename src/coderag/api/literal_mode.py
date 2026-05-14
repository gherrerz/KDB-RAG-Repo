"""Literal code mode helpers extracted from query service orchestration."""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from coderag.core.models import (
    Citation,
    QueryResponse,
    RetrievedChunk,
    RetrievalQueryResponse,
    RetrievalStatistics,
)


@dataclass(frozen=True)
class LiteralModeHooks:
    """Injected collaborators required by literal mode operations."""

    get_settings: Callable[[], object]
    resolve_repo_file_path: Callable[[str, str], Path | None]
    has_local_repo_workspace: Callable[[str], bool]


@dataclass(frozen=True)
class _LiteralResolvedTarget:
    """Resolved exact literal target from a repository workspace."""

    file_path: Path
    relative_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    match_type: str
    target_content: str


def is_literal_code_query(query: str) -> bool:
    """Detect requests that explicitly ask for literal code output."""
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
        extract_literal_file_candidates(query)
        or extract_literal_symbol_candidates(query)
    )


def extract_literal_file_candidates(query: str) -> list[str]:
    """Extract candidate file paths or filenames for literal mode."""
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


def extract_literal_symbol_candidates(query: str) -> list[str]:
    """Extract candidate symbol names for literal mode."""
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


def resolve_repo_root(repo_id: str, *, hooks: LiteralModeHooks) -> Path | None:
    """Resolve the local workspace root for a repository."""
    settings = hooks.get_settings()
    candidate = (settings.workspace_path / repo_id).resolve()
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def resolve_literal_file_match(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> tuple[Path | None, str | None, str]:
    """Resolve an exact file match for literal mode."""
    candidates = extract_literal_file_candidates(query)
    if not candidates:
        return None, None, "missing_file_hint"

    repo_root = resolve_repo_root(repo_id, hooks=hooks)

    for candidate in candidates:
        if "/" not in candidate:
            continue
        resolved_path = hooks.resolve_repo_file_path(
            repo_id=repo_id,
            relative_path=candidate,
        )
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
        matches = [item for item in repo_root.rglob(candidate) if item.is_file()]
        if len(matches) == 1:
            relative = PurePosixPath(matches[0].relative_to(repo_root))
            return matches[0], str(relative), "exact_filename_unique"
        if len(matches) > 1:
            return None, None, "ambiguous_filename"

    return None, None, "exact_match_not_found"


def python_symbol_spans(content: str, symbol: str) -> list[tuple[int, int]]:
    """Resolve exact Python symbol spans using the AST."""
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


def brace_block_end(lines: list[str], start_index: int) -> int:
    """Resolve a block end for brace-based languages."""
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


def generic_symbol_spans(content: str, symbol: str) -> list[tuple[int, int]]:
    """Resolve approximate symbol spans for non-Python languages."""
    escaped = re.escape(symbol)
    patterns = [
        re.compile(rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{escaped}\b"),
        re.compile(rf"^\s*class\s+{escaped}\b"),
        re.compile(rf"^\s*(?:const|let|var)\s+{escaped}\s*=\s*(?:async\s*)?.*=>"),
        re.compile(
            rf"^\s*(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp|\s)+"
            rf"(?:[A-Za-z0-9_<>,\[\]\.\?]+\s+)+{escaped}\s*\([^;]*\)\s*(?:\{{)?\s*$"
        ),
    ]
    lines = content.splitlines()
    spans: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if not any(pattern.match(line) for pattern in patterns):
            continue
        end_line = index + 1
        if "{" in line or any("{" in next_line for next_line in lines[index:index + 2]):
            end_line = brace_block_end(lines, index)
        spans.append((index + 1, max(index + 1, end_line)))
    return spans


def resolve_literal_symbol_match(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> tuple[Path | None, str | None, int | None, int | None, str | None, str]:
    """Resolve an exact unique symbol match inside repository files."""
    candidates = extract_literal_symbol_candidates(query)
    if not candidates:
        return None, None, None, None, None, "missing_symbol_hint"

    repo_root = resolve_repo_root(repo_id, hooks=hooks)
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
                spans = python_symbol_spans(content, symbol)
            else:
                spans = generic_symbol_spans(content, symbol)
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


def slice_lines(content: str, start_line: int, end_line: int) -> str:
    """Extract an inclusive line range from file content."""
    lines = content.splitlines()
    if not lines:
        return ""
    safe_start = max(1, start_line)
    safe_end = max(safe_start, min(end_line, len(lines)))
    return "\n".join(lines[safe_start - 1:safe_end])


def _resolve_literal_target(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> tuple[_LiteralResolvedTarget | None, str]:
    """Resolve and read the exact literal target requested by the user."""
    file_path, relative_path, match_type = resolve_literal_file_match(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    start_line = 1
    end_line: int | None = None
    symbol_name: str | None = None
    target_content: str | None = None
    if (
        (file_path is None or relative_path is None)
        and match_type == "missing_file_hint"
    ):
        (
            file_path,
            relative_path,
            symbol_start,
            symbol_end,
            symbol_name,
            match_type,
        ) = resolve_literal_symbol_match(
            repo_id=repo_id,
            query=query,
            hooks=hooks,
        )
        if symbol_start is not None and symbol_end is not None:
            start_line = symbol_start
            end_line = symbol_end

    if file_path is None or relative_path is None:
        return None, match_type

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, "file_read_error"

    if end_line is None:
        lines = content.splitlines()
        end_line = max(1, len(lines))
    else:
        target_content = slice_lines(content, start_line, end_line)

    if target_content is None:
        target_content = content

    return (
        _LiteralResolvedTarget(
            file_path=file_path,
            relative_path=relative_path,
            start_line=start_line,
            end_line=end_line,
            symbol_name=symbol_name,
            match_type=match_type,
            target_content=target_content,
        ),
        match_type,
    )


def _build_query_failure_response(
    *,
    answer: str,
    failure_reason: str,
    fallback_reason: str,
    match_type: str | None,
) -> QueryResponse:
    """Build a failure response for query literal mode."""
    return QueryResponse(
        answer=answer,
        citations=[],
        diagnostics={
            "literal_mode": True,
            "literal_exact_match": False,
            "literal_match_type": match_type,
            "literal_failure_reason": failure_reason,
            "fallback_reason": fallback_reason,
            "inventory_intent": False,
            "inventory_route": None,
        },
    )


def _build_retrieval_failure_response(
    *,
    answer: str,
    failure_reason: str,
    fallback_reason: str,
    match_type: str | None,
) -> RetrievalQueryResponse:
    """Build a failure response for retrieval literal mode."""
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
            "literal_match_type": match_type,
            "literal_failure_reason": failure_reason,
            "fallback_reason": fallback_reason,
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


def build_literal_code_response(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> QueryResponse:
    """Build the deterministic query response for literal code mode."""
    if not hooks.has_local_repo_workspace(repo_id):
        return _build_query_failure_response(
            answer=(
                "No puedo devolver código literal porque este repositorio no "
                "tiene workspace local disponible. Reingesta el repositorio o "
                "usa consulta semántica/retrieval en lugar de modo literal."
            ),
            failure_reason="workspace_unavailable",
            fallback_reason="literal_workspace_required",
            match_type=None,
        )

    resolved_target, failure_reason = _resolve_literal_target(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    if resolved_target is None:
        if failure_reason == "file_read_error":
            return _build_query_failure_response(
                answer=(
                    "No pude leer el archivo solicitado desde el workspace local. "
                    "Reintenta después de verificar que el archivo exista y sea accesible."
                ),
                failure_reason=failure_reason,
                fallback_reason="literal_not_exact_match",
                match_type="exact_symbol_unique",
            )
        return _build_query_failure_response(
            answer=(
                "No puedo devolver código literal con precisión en esta consulta. "
                "Indica la ruta exacta del archivo dentro del repositorio o un "
                "nombre de archivo único."
            ),
            failure_reason=failure_reason,
            fallback_reason="literal_not_exact_match",
            match_type=None,
        )

    suffix = resolved_target.file_path.suffix.lower().lstrip(".") or "text"
    answer = "\n".join(
        [
            "Modo código literal (sin síntesis LLM).",
            f"Archivo: {resolved_target.relative_path}",
            (
                f"Símbolo: {resolved_target.symbol_name}"
                if resolved_target.symbol_name
                else ""
            ),
            "",
            f"```{suffix}",
            resolved_target.target_content,
            "```",
        ]
    )
    citations = [
        Citation(
            path=resolved_target.relative_path,
            start_line=resolved_target.start_line,
            end_line=resolved_target.end_line,
            score=1.0,
            reason=(
                "literal_symbol_exact_match"
                if resolved_target.symbol_name
                else "literal_file_exact_match"
            ),
        )
    ]
    diagnostics = {
        "literal_mode": True,
        "literal_exact_match": True,
        "literal_match_type": resolved_target.match_type,
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


def build_literal_retrieval_response(
    repo_id: str,
    query: str,
    include_context: bool,
    *,
    hooks: LiteralModeHooks,
) -> RetrievalQueryResponse:
    """Build the deterministic retrieval-only response for literal code mode."""
    if not hooks.has_local_repo_workspace(repo_id):
        return _build_retrieval_failure_response(
            answer=(
                "Modo retrieval-only (sin LLM): no puedo devolver código "
                "literal porque este repositorio no tiene workspace local "
                "disponible. Reingesta el repositorio o usa retrieval "
                "semántico."
            ),
            failure_reason="workspace_unavailable",
            fallback_reason="literal_workspace_required",
            match_type=None,
        )

    resolved_target, failure_reason = _resolve_literal_target(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    if resolved_target is None:
        if failure_reason == "file_read_error":
            return _build_retrieval_failure_response(
                answer=(
                    "Modo retrieval-only (sin LLM): no pude leer el archivo "
                    "solicitado desde el workspace local."
                ),
                failure_reason=failure_reason,
                fallback_reason="literal_not_exact_match",
                match_type="exact_symbol_unique",
            )
        return _build_retrieval_failure_response(
            answer=(
                "Modo retrieval-only (sin LLM): no puedo devolver código literal "
                "con precisión en esta consulta. Indica la ruta exacta del archivo "
                "dentro del repositorio o un nombre de archivo único."
            ),
            failure_reason=failure_reason,
            fallback_reason="literal_not_exact_match",
            match_type=None,
        )

    chunk = RetrievedChunk(
        id=(
            f"literal:{resolved_target.relative_path}:"
            f"{resolved_target.start_line}:{resolved_target.end_line}"
        ),
        text=resolved_target.target_content,
        score=1.0,
        path=resolved_target.relative_path,
        start_line=resolved_target.start_line,
        end_line=resolved_target.end_line,
        kind=(
            "literal_symbol" if resolved_target.symbol_name else "literal_file"
        ),
        metadata={
            "path": resolved_target.relative_path,
            "start_line": resolved_target.start_line,
            "end_line": resolved_target.end_line,
            "kind": (
                "literal_symbol" if resolved_target.symbol_name else "literal_file"
            ),
            "literal_mode": True,
            "symbol_name": resolved_target.symbol_name,
        },
    )
    citation = Citation(
        path=resolved_target.relative_path,
        start_line=resolved_target.start_line,
        end_line=resolved_target.end_line,
        score=1.0,
        reason=(
            "literal_symbol_exact_match"
            if resolved_target.symbol_name
            else "literal_file_exact_match"
        ),
    )
    answer = "\n".join(
        [
            "Modo retrieval-only (sin LLM): código literal exacto.",
            f"Archivo: {resolved_target.relative_path}",
            (
                f"Símbolo: {resolved_target.symbol_name}"
                if resolved_target.symbol_name
                else ""
            ),
            "",
            resolved_target.target_content,
        ]
    )
    context = resolved_target.target_content if include_context else None
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
            "literal_match_type": resolved_target.match_type,
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