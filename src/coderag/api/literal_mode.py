"""Literal code mode helpers extracted from query service orchestration."""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from coderag.api.inventory_helpers import (
    INVENTORY_EQUIVALENT_GROUPS,
    MODULE_NAME_STOPWORDS,
    canonical_inventory_term,
    extract_module_name,
)
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
    get_symbol_definitions: Callable[..., list[dict]]
    get_symbol_snippet: Callable[[str, str, str], object]
    get_file_snippet: Callable[[str, str], object]


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


def _current_checkout_root() -> Path:
    """Return the repository root of the running checkout."""
    return Path(__file__).resolve().parents[3]


def _normalize_repo_locator(value: str) -> str:
    """Normalize repo identifiers and folder names for loose matching."""
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _repo_id_matches_current_checkout(repo_id: str) -> bool:
    """Return whether the requested repo id plausibly refers to this checkout."""
    normalized_repo_id = _normalize_repo_locator(repo_id)
    normalized_checkout = _normalize_repo_locator(_current_checkout_root().name)
    if not normalized_repo_id or not normalized_checkout:
        return False
    return (
        normalized_checkout in normalized_repo_id
        or normalized_repo_id in normalized_checkout
    )


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


_COMPONENT_TYPE_TOKENS = sorted(
    {token for group in INVENTORY_EQUIVALENT_GROUPS for token in group},
    key=len,
    reverse=True,
)
_COMPONENT_TYPE_ALTERNATION = "|".join(
    re.escape(token) for token in _COMPONENT_TYPE_TOKENS
)

_COMPONENT_REQUEST_OBJECTS = (
    r"codigo|c[oó]digo|implementacion|implementaci[oó]n|source|fuente|"
    r"cuerpo|definicion|definici[oó]n"
)

_NATURAL_COMPONENT_INTENT_PATTERNS = [
    # Any mention of "código"/"implementación"/"source"/etc. is enough on its
    # own to signal a code request; no verb is required (e.g. "el código del
    # controlador BarController" has no verb).
    re.compile(rf"\b(?:{_COMPONENT_REQUEST_OBJECTS})\b", re.IGNORECASE),
    re.compile(r"\bc[oó]mo\s+est[aá]\s+implementad[oa]\b", re.IGNORECASE),
    re.compile(r"\bhow\s+is\b.{0,60}\bimplemented\b", re.IGNORECASE),
]

_COMPONENT_TYPE_THEN_NAME = re.compile(
    rf"\b({_COMPONENT_TYPE_ALTERNATION})\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_COMPONENT_NAME_THEN_TYPE = re.compile(
    rf"\b([A-Za-z_][A-Za-z0-9_]*)\s+({_COMPONENT_TYPE_ALTERNATION})\b",
    re.IGNORECASE,
)
# Bare PascalCase identifiers (e.g. "UserRepository", "FooService") used as
# component names without an explicit type qualifier nearby, as in
# "cómo está implementado UserRepository".
_BARE_PASCAL_CASE_IDENTIFIER = re.compile(
    r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b"
)


@dataclass(frozen=True)
class ComponentRef:
    """A component name mentioned in a query, with an optional type hint."""

    name: str
    type_hint: str | None


def is_component_code_query(query: str) -> bool:
    """Detect requests (fixed phrases or natural language) for a component's code."""
    if is_literal_code_query(query):
        return True
    if not any(
        pattern.search(query) for pattern in _NATURAL_COMPONENT_INTENT_PATTERNS
    ):
        return False
    return bool(
        extract_literal_file_candidates(query)
        or extract_literal_symbol_candidates(query)
        or extract_component_candidates(query)
    )


def extract_component_candidates(query: str) -> list[ComponentRef]:
    """Extract component name + type-hint pairs like 'servicio FooService'."""
    candidates: list[ComponentRef] = []
    for pattern, type_group, name_group in (
        (_COMPONENT_TYPE_THEN_NAME, 1, 2),
        (_COMPONENT_NAME_THEN_TYPE, 2, 1),
    ):
        for match in pattern.finditer(query):
            name = match.group(name_group)
            if not name or name.lower() in MODULE_NAME_STOPWORDS:
                continue
            type_token = match.group(type_group)
            type_hint = canonical_inventory_term(type_token)
            candidates.append(ComponentRef(name=name, type_hint=type_hint))

    for match in _BARE_PASCAL_CASE_IDENTIFIER.finditer(query):
        name = match.group(1)
        if name.lower() in MODULE_NAME_STOPWORDS:
            continue
        candidates.append(ComponentRef(name=name, type_hint=None))

    deduped: list[ComponentRef] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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

    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", query):
        if token.count("_") >= 1:
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


_TEST_PATH_MARKERS = ("/test/", "/tests/", "test_", "_test.", ".spec.", "/spec/")


def _looks_like_test_path(path: str) -> bool:
    """Heuristic check for test/fixture paths, used only to break ties."""
    lowered = f"/{path.lower()}"
    return any(marker in lowered for marker in _TEST_PATH_MARKERS)


def _select_unambiguous_candidate(
    candidates: list[dict],
) -> tuple[dict | None, list[dict]]:
    """Auto-select when a single non-test candidate stands out; else ambiguous.

    Deliberately simple: prefers a lone non-test/non-fixture match over
    genuine ambiguity between multiple production candidates, rather than
    guessing which one the user meant.
    """
    if len(candidates) == 1:
        return candidates[0], []
    non_test = [
        candidate
        for candidate in candidates
        if not _looks_like_test_path(str(candidate.get("path", "")))
    ]
    if len(non_test) == 1:
        return non_test[0], []
    return None, candidates


@dataclass(frozen=True)
class _ComponentResolvedTarget:
    """Resolved component target read from the persisted index (no filesystem)."""

    path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    symbol_type: str | None
    match_type: str
    literal_source: str
    target_content: str


def resolve_component_target(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> tuple[_ComponentResolvedTarget | None, str, list[dict]]:
    """Resolve the exact component/file/symbol target requested from the index.

    Reads exclusively from the persisted index (Neo4j for symbol lookup,
    Chroma/Postgres for snippet content) so extraction works even when the
    repository has no local workspace (distributed worker, purged clone).

    Returns ``(resolved_target_or_None, status, ambiguous_candidates)``.
    ``status`` is one of: "resolved", "ambiguous_component",
    "component_not_found", "file_not_found".
    """
    file_candidates = extract_literal_file_candidates(query)
    for candidate in file_candidates:
        normalized = candidate.strip("/")
        try:
            snippet = hooks.get_file_snippet(repo_id, normalized)
        except Exception:
            snippet = None
        if snippet is not None:
            return (
                _ComponentResolvedTarget(
                    path=snippet.path,
                    start_line=snippet.start_line,
                    end_line=snippet.end_line,
                    symbol_name=None,
                    symbol_type=None,
                    match_type="exact_path",
                    literal_source=snippet.source,
                    target_content=snippet.text,
                ),
                "resolved",
                [],
            )

    component_refs = list(extract_component_candidates(query))
    seen_names = {ref.name.lower() for ref in component_refs}
    for name in extract_literal_symbol_candidates(query):
        if name.lower() not in seen_names:
            component_refs.append(ComponentRef(name=name, type_hint=None))
            seen_names.add(name.lower())

    if not component_refs:
        return None, ("file_not_found" if file_candidates else "component_not_found"), []

    module_name = extract_module_name(query)
    for ref in component_refs:
        try:
            matches = hooks.get_symbol_definitions(
                repo_id,
                ref.name,
                symbol_type=ref.type_hint,
                module_name=module_name,
            )
        except Exception:
            matches = []
        if not matches:
            continue

        selected, ambiguous = _select_unambiguous_candidate(matches)
        if selected is None:
            return None, "ambiguous_component", ambiguous

        path = str(selected.get("path", ""))
        start_line = int(selected.get("start_line", 1) or 1)
        end_line = int(selected.get("end_line", 1) or 1)
        symbol_name = str(selected.get("label") or ref.name)
        symbol_type = selected.get("kind")

        try:
            snippet = hooks.get_symbol_snippet(repo_id, path, symbol_name)
        except Exception:
            snippet = None
        if snippet is None:
            continue

        return (
            _ComponentResolvedTarget(
                path=snippet.path or path,
                start_line=snippet.start_line or start_line,
                end_line=snippet.end_line or end_line,
                symbol_name=symbol_name,
                symbol_type=symbol_type,
                match_type="graph_symbol_match",
                literal_source=snippet.source,
                target_content=snippet.text,
            ),
            "resolved",
            [],
        )

    return None, "component_not_found", []


def resolve_repo_root(repo_id: str, *, hooks: LiteralModeHooks) -> Path | None:
    """Resolve the local workspace root for a repository."""
    settings = hooks.get_settings()
    candidates = [(settings.workspace_path / repo_id).resolve()]
    if _repo_id_matches_current_checkout(repo_id):
        candidates.append(_current_checkout_root())
    for candidate in candidates:
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


def _component_failure_answer(status: str, ambiguous: list[dict]) -> str:
    """Build the human-readable message for a failed component resolution."""
    if status == "ambiguous_component":
        options = "\n".join(
            "- {path} ({kind} {label})".format(
                path=candidate.get("path", ""),
                kind=candidate.get("kind", "") or "",
                label=candidate.get("label", "") or "",
            ).strip()
            for candidate in ambiguous
        )
        return (
            "Hay múltiples componentes que coinciden con ese nombre. "
            "Especifica la ruta o el módulo para desambiguar:\n" + options
        )
    if status == "file_not_found":
        return (
            "No encontré ese archivo en el índice del repositorio ingestado. "
            "Verifica la ruta exacta dentro del repositorio."
        )
    return (
        "No pude identificar el componente solicitado en el repositorio "
        "ingestado. Indica el nombre exacto de la clase, función o "
        "servicio que necesitas."
    )


def _component_candidates_diagnostics(ambiguous: list[dict]) -> list[dict]:
    """Shape ambiguous graph matches into diagnostics-friendly candidates."""
    return [
        {
            "path": candidate.get("path"),
            "symbol": candidate.get("label"),
            "kind": candidate.get("kind"),
        }
        for candidate in ambiguous
    ]


def build_component_code_response(
    repo_id: str,
    query: str,
    *,
    hooks: LiteralModeHooks,
) -> QueryResponse:
    """Build the deterministic query response for component code mode.

    Resolves and extracts exclusively from the persisted index (Neo4j +
    Chroma/Postgres), so it works without a local repository workspace.
    """
    resolved, status, ambiguous = resolve_component_target(
        repo_id, query, hooks=hooks
    )
    if resolved is None:
        return QueryResponse(
            answer=_component_failure_answer(status, ambiguous),
            citations=[],
            diagnostics={
                "component_mode": True,
                "component_name": None,
                "component_type": None,
                "resolution_path": None,
                "literal_source": None,
                "component_candidates": _component_candidates_diagnostics(ambiguous),
                "literal_failure_reason": status,
                "fallback_reason": "component_not_resolved",
                "inventory_intent": False,
                "inventory_route": None,
            },
        )

    suffix = PurePosixPath(resolved.path).suffix.lstrip(".") or "text"
    answer_lines = ["Modo código de componente (sin síntesis LLM).", f"Archivo: {resolved.path}"]
    if resolved.symbol_name:
        label = f"{resolved.symbol_type or ''} {resolved.symbol_name}".strip()
        answer_lines.append(f"Componente: {label}")
    answer_lines.append(f"Fuente: índice persistido ({resolved.literal_source})")
    answer_lines.extend(["", f"```{suffix}", resolved.target_content, "```"])

    citations = [
        Citation(
            path=resolved.path,
            start_line=resolved.start_line,
            end_line=resolved.end_line,
            score=1.0,
            reason=(
                "component_graph_symbol_match"
                if resolved.symbol_name
                else "component_persisted_snippet"
            ),
        )
    ]
    diagnostics = {
        "component_mode": True,
        "component_name": resolved.symbol_name,
        "component_type": resolved.symbol_type,
        "resolution_path": resolved.match_type,
        "literal_source": resolved.literal_source,
        "retrieved": 1,
        "reranked": 1,
        "raw_citations": 1,
        "filtered_citations": 1,
        "returned_citations": 1,
        "fallback_reason": None,
        "inventory_intent": False,
        "inventory_route": None,
    }
    return QueryResponse(
        answer="\n".join(answer_lines),
        citations=citations,
        diagnostics=diagnostics,
    )


def build_component_retrieval_response(
    repo_id: str,
    query: str,
    include_context: bool,
    *,
    hooks: LiteralModeHooks,
) -> RetrievalQueryResponse:
    """Build the deterministic retrieval-only response for component code mode.

    Resolves and extracts exclusively from the persisted index, mirroring
    ``build_component_code_response`` for the retrieval-only contract.
    """
    resolved, status, ambiguous = resolve_component_target(
        repo_id, query, hooks=hooks
    )
    if resolved is None:
        return RetrievalQueryResponse(
            mode="retrieval_only",
            answer=_component_failure_answer(status, ambiguous),
            chunks=[],
            citations=[],
            statistics=RetrievalStatistics(
                total_before_rerank=0,
                total_after_rerank=0,
                graph_nodes_count=0,
            ),
            diagnostics={
                "mode": "retrieval_only",
                "component_mode": True,
                "component_name": None,
                "component_type": None,
                "resolution_path": None,
                "literal_source": None,
                "component_candidates": _component_candidates_diagnostics(ambiguous),
                "literal_failure_reason": status,
                "fallback_reason": "component_not_resolved",
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

    kind = "component_symbol" if resolved.symbol_name else "component_file"
    chunk = RetrievedChunk(
        id=f"component:{resolved.path}:{resolved.start_line}:{resolved.end_line}",
        text=resolved.target_content,
        score=1.0,
        path=resolved.path,
        start_line=resolved.start_line,
        end_line=resolved.end_line,
        kind=kind,
        metadata={
            "path": resolved.path,
            "start_line": resolved.start_line,
            "end_line": resolved.end_line,
            "kind": kind,
            "component_mode": True,
            "symbol_name": resolved.symbol_name,
            "symbol_type": resolved.symbol_type,
        },
    )
    citation = Citation(
        path=resolved.path,
        start_line=resolved.start_line,
        end_line=resolved.end_line,
        score=1.0,
        reason=(
            "component_graph_symbol_match"
            if resolved.symbol_name
            else "component_persisted_snippet"
        ),
    )
    answer_lines = [
        "Modo retrieval-only (sin LLM): código de componente.",
        f"Archivo: {resolved.path}",
    ]
    if resolved.symbol_name:
        label = f"{resolved.symbol_type or ''} {resolved.symbol_name}".strip()
        answer_lines.append(f"Componente: {label}")
    answer_lines.extend(["", resolved.target_content])
    context = resolved.target_content if include_context else None
    return RetrievalQueryResponse(
        mode="retrieval_only",
        answer="\n".join(answer_lines),
        chunks=[chunk],
        citations=[citation],
        statistics=RetrievalStatistics(
            total_before_rerank=1,
            total_after_rerank=1,
            graph_nodes_count=0,
        ),
        diagnostics={
            "mode": "retrieval_only",
            "component_mode": True,
            "component_name": resolved.symbol_name,
            "component_type": resolved.symbol_type,
            "resolution_path": resolved.match_type,
            "literal_source": resolved.literal_source,
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