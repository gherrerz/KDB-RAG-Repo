"""Estrategia de reclasificación heurística para candidatos de recuperación."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from coderag.core.models import RetrievalChunk


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_RUNTIME_CONFIG_TOKENS = {
    "config",
    "configuration",
    "configuracion",
    "configuracion",
    "configured",
    "setting",
    "settings",
    "env",
    "environment",
    "variable",
    "variables",
    "runtime",
    "docker",
    "compose",
    "k8s",
    "kubernetes",
    "configmap",
    "helm",
    "deployment",
    "workspace",
    "retention",
}
_CODE_TOKENS = {
    "function",
    "functions",
    "method",
    "methods",
    "class",
    "classes",
    "implementation",
    "implemented",
    "executed",
    "execute",
    "call",
    "code",
    "symbol",
    "preflight",
}
_TEST_TOKENS = {
    "test",
    "tests",
    "fixture",
    "fixtures",
    "mock",
    "mocks",
    "spec",
    "specs",
}
_DEFINITION_LOOKUP_TOKENS = {
    "code",
    "codigo",
    "definition",
    "definicion",
    "defined",
    "function",
    "functions",
    "implement",
    "implementacion",
    "implemented",
    "implementation",
    "method",
    "methods",
    "source",
    "symbol",
}
_DOCUMENTATION_TOKENS = {
    "api",
    "documentacion",
    "documentado",
    "documentada",
    "documentar",
    "documentation",
    "documented",
    "docs",
    "guide",
    "guides",
    "readme",
    "reference",
}
_CONTEXT_OVERVIEW_TOKENS = {
    "context",
    "contexto",
    "overview",
    "resumen",
    "summary",
    "arquitectura",
    "sabes",
    "know",
}
_IMPLEMENTATION_HINT_TOKENS = {
    "execute",
    "executed",
    "execution",
    "implement",
    "implementacion",
    "implemented",
    "implementation",
    "run",
    "running",
    "flow",
    "path",
}
_NOISE_PATH_SEGMENTS = {
    "fixtures",
    "examples",
    "benchmark_reports",
}
_DOC_PATH_SEGMENTS = {
    "docs",
    "documentation",
    "guides",
}
_EXAMPLE_PATH_SEGMENTS = {
    "demo",
    "examples",
    "sample",
    "samples",
    "snippets",
}
_SOURCE_LIKE_SEGMENTS = {
    "src",
    "app",
    "lib",
    "pkg",
    "internal",
    "core",
    "api",
    "service",
    "services",
    "server",
    "client",
    "cmd",
}
_WRAPPER_PATH_SEGMENTS = {
    "api",
    "cli",
    "controller",
    "endpoint",
    "handler",
    "router",
    "server",
    "service",
    "services",
}
_ORCHESTRATION_PATH_TOKENS = {
    "admin",
    "cli",
    "controller",
    "endpoint",
    "flow",
    "handler",
    "router",
    "server",
    "ui",
    "view",
}
_CONFIG_FILE_SUFFIXES = (
    "settings.py",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    ".env",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
)
_DEFINITION_SYMBOL_TYPES = {
    "class",
    "function",
    "method",
    "module",
}


@dataclass(frozen=True)
class QueryProfile:
    """Señales derivadas de la consulta para ajustar el reranking."""

    raw_query: str
    normalized_query: str
    canonical_query: str
    tokens: tuple[str, ...]
    focus_identifiers: tuple[str, ...]
    target_identifier_candidates: tuple[str, ...]
    exact_identifier_query: bool
    runtime_config_intent: bool
    code_intent: bool
    test_intent: bool
    natural_language_query: bool
    implementation_intent: bool
    definition_lookup_intent: bool
    documentation_lookup_intent: bool
    operational_config_lookup_intent: bool
    context_overview_intent: bool
    prefers_symbol_definitions: bool
    prefers_docs: bool
    prefers_runtime_config: bool


def _normalize_text(value: str) -> str:
    """Normaliza texto para comparaciones robustas de query y paths."""
    lowered = value.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_marks.split())


def _canonicalize_identifier(value: str) -> str:
    """Convierte texto arbitrario a una forma comparable de identificador."""
    normalized = _normalize_text(value)
    canonical = re.sub(r"[^a-z0-9]+", "_", normalized)
    return re.sub(r"_+", "_", canonical).strip("_")


def _tokenize_query(value: str) -> tuple[str, ...]:
    """Extrae tokens estables desde la consulta normalizada."""
    normalized = _normalize_text(value)
    tokens = re.findall(r"[a-z0-9_]+", normalized)
    return tuple(token for token in tokens if len(token) >= 2)


def _is_exact_identifier_query(query: str) -> bool:
    """Determina si la consulta luce como un identificador exacto."""
    stripped = query.strip()
    if " " in stripped or len(stripped) < 3:
        return False
    if _IDENTIFIER_RE.fullmatch(stripped) is None:
        return False
    canonical = _canonicalize_identifier(stripped)
    return canonical.count("_") >= 1 or "." in stripped or "-" in stripped


def _build_query_profile(query: str) -> QueryProfile:
    """Construye un perfil ligero de intención para el reranker."""
    normalized_query = _normalize_text(query)
    tokens = _tokenize_query(query)
    token_set = set(tokens)
    exact_identifier_query = _is_exact_identifier_query(query)
    focus_identifiers = tuple(
        dict.fromkeys(
            _canonicalize_identifier(token)
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*", query)
            if len(token) >= 3
            and (
                _canonicalize_identifier(token).count("_") >= 1
                or "." in token
                or "-" in token
            )
        )
    )
    target_identifier_candidates = tuple(
        dict.fromkeys(
            candidate
            for candidate in (
                (_canonicalize_identifier(query),) if exact_identifier_query else ()
            )
            + focus_identifiers
            if candidate
        )
    )
    context_overview_intent = bool(token_set & _CONTEXT_OVERVIEW_TOKENS)
    documentation_lookup_intent = bool(token_set & _DOCUMENTATION_TOKENS)
    documentation_lookup_intent = (
        documentation_lookup_intent or context_overview_intent
    )
    operational_config_lookup_intent = bool(token_set & _RUNTIME_CONFIG_TOKENS)
    definition_lookup_intent = bool(token_set & _DEFINITION_LOOKUP_TOKENS) or (
        bool(target_identifier_candidates)
        and bool({"where", "donde", "location", "ubicacion"} & token_set)
        and not documentation_lookup_intent
        and not operational_config_lookup_intent
    )
    runtime_config_intent = (
        operational_config_lookup_intent
        and not documentation_lookup_intent
        and not context_overview_intent
    )
    code_intent = (
        bool(token_set & _CODE_TOKENS)
        or definition_lookup_intent
        or context_overview_intent
    )
    test_intent = bool(token_set & _TEST_TOKENS)
    natural_language_query = not exact_identifier_query
    implementation_intent = (
        bool(token_set & _IMPLEMENTATION_HINT_TOKENS)
        or definition_lookup_intent
        or exact_identifier_query
    )
    prefers_symbol_definitions = bool(target_identifier_candidates) and (
        definition_lookup_intent or exact_identifier_query
    )
    prefers_docs = documentation_lookup_intent
    prefers_runtime_config = runtime_config_intent
    return QueryProfile(
        raw_query=query,
        normalized_query=normalized_query,
        canonical_query=_canonicalize_identifier(query),
        tokens=tokens,
        focus_identifiers=focus_identifiers,
        target_identifier_candidates=target_identifier_candidates,
        exact_identifier_query=exact_identifier_query,
        runtime_config_intent=runtime_config_intent,
        code_intent=code_intent,
        test_intent=test_intent,
        natural_language_query=natural_language_query,
        implementation_intent=implementation_intent,
        definition_lookup_intent=definition_lookup_intent,
        documentation_lookup_intent=documentation_lookup_intent,
        operational_config_lookup_intent=operational_config_lookup_intent,
        context_overview_intent=context_overview_intent,
        prefers_symbol_definitions=prefers_symbol_definitions,
        prefers_docs=prefers_docs,
        prefers_runtime_config=prefers_runtime_config,
    )


def _is_test_path(path: str) -> bool:
    """Indica si una ruta pertenece a tests o artefactos equivalentes."""
    normalized = path.strip().lower().replace("\\", "/")
    return normalized.startswith("tests/") or "/tests/" in normalized


def _is_config_path(path: str) -> bool:
    """Detecta rutas que suelen representar configuración runtime."""
    normalized = path.strip().lower().replace("\\", "/")
    if not normalized:
        return False
    if normalized.startswith("k8s/") or "/k8s/" in normalized:
        return True
    return normalized.endswith(_CONFIG_FILE_SUFFIXES)


def _is_docs_path(path: str) -> bool:
    """Detecta rutas documentales frente a código o configuración."""
    normalized = path.strip().lower().replace("\\", "/")
    if not normalized:
        return False
    if normalized.startswith("docs/") or "/docs/" in normalized:
        return True
    filename = normalized.rsplit("/", maxsplit=1)[-1]
    stem = filename.rsplit(".", maxsplit=1)[0]
    if stem in {"readme", "configuration", "api_reference", "install"}:
        return True
    segments = [segment for segment in normalized.split("/") if segment]
    return any(segment in _DOC_PATH_SEGMENTS for segment in segments)


def _is_example_path(path: str) -> bool:
    """Detecta rutas de ejemplo que no deberían ganar por defecto."""
    normalized = path.strip().lower().replace("\\", "/")
    segments = [segment for segment in normalized.split("/") if segment]
    return any(segment in _EXAMPLE_PATH_SEGMENTS for segment in segments)


def _is_noise_path(path: str) -> bool:
    """Marca rutas de bajo valor por defecto para queries funcionales."""
    normalized = path.strip().lower().replace("\\", "/")
    return any(segment in normalized for segment in _NOISE_PATH_SEGMENTS)


def _is_orchestration_path(path: str) -> bool:
    """Detecta entrypoints y flujos auxiliares que no suelen ser el owner real."""
    normalized = path.strip().lower().replace("\\", "/")
    if not normalized:
        return False
    tokens: set[str] = set()
    for segment in normalized.split("/"):
        if not segment:
            continue
        stem = segment.rsplit(".", maxsplit=1)[0]
        tokens.update(token for token in re.findall(r"[a-z0-9]+", stem) if token)
    return bool(tokens & _ORCHESTRATION_PATH_TOKENS)


def _is_productive_implementation_path(path: str) -> bool:
    """Marca rutas que parecen implementación productiva y no soporte de pruebas."""
    normalized = path.strip().lower().replace("\\", "/")
    if not normalized or _is_test_path(normalized) or _is_noise_path(normalized):
        return False
    segments = [segment for segment in normalized.split("/") if segment]
    if any(segment in {"docs", "documentation"} for segment in segments):
        return False
    if any(segment in _SOURCE_LIKE_SEGMENTS for segment in segments):
        return True
    return normalized.endswith(
        (
            ".py",
            ".java",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".go",
            ".rb",
            ".cs",
            ".php",
            ".rs",
            ".cpp",
            ".c",
            ".h",
            ".hpp",
        )
    )


def _normalized_overlap(tokens: tuple[str, ...], haystack: str) -> float:
    """Calcula la cobertura normalizada de tokens sobre un texto dado."""
    if not tokens:
        return 0.0
    normalized_haystack = _canonicalize_identifier(haystack)
    if not normalized_haystack:
        return 0.0
    matches = sum(1 for token in tokens if token in normalized_haystack)
    return matches / len(tokens)


def _path_exact_match(path: str, canonical_query: str) -> bool:
    """Comprueba coincidencia exacta contra segmentos del path."""
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    for segment in normalized.split("/"):
        stem = segment.rsplit(".", maxsplit=1)[0]
        if _canonicalize_identifier(segment) == canonical_query:
            return True
        if _canonicalize_identifier(stem) == canonical_query:
            return True
    return False


def _symbol_type(metadata: dict) -> str:
    """Obtiene el tipo de símbolo disponible en metadata."""
    return str(metadata.get("symbol_type") or metadata.get("entity_type") or "")


def _strong_overlap(profile: QueryProfile, chunk: RetrievalChunk) -> bool:
    """Señal compacta para evitar castigar chunks con match léxico claro."""
    metadata = chunk.metadata
    return any(
        score >= 0.5
        for score in (
            _normalized_overlap(profile.tokens, str(metadata.get("symbol_name", ""))),
            _normalized_overlap(profile.tokens, str(metadata.get("path", ""))),
            _normalized_overlap(profile.tokens, chunk.text),
        )
    )


def _text_mentions_target(profile: QueryProfile, value: str) -> bool:
    """Marca si el texto menciona un identificador objetivo de la query."""
    normalized_value = _canonicalize_identifier(value)
    if not normalized_value:
        return False
    return any(
        candidate in normalized_value
        for candidate in profile.target_identifier_candidates
    )


def _exact_symbol_match(profile: QueryProfile, symbol_name: str) -> bool:
    """Comprueba si el símbolo coincide exactamente con el objetivo."""
    normalized_symbol = _normalize_text(symbol_name)
    trimmed_symbol = normalized_symbol.lstrip("_")
    if normalized_symbol.startswith("_") and trimmed_symbol:
        if trimmed_symbol in profile.target_identifier_candidates:
            return False
    canonical_symbol = _canonicalize_identifier(symbol_name)
    if not canonical_symbol:
        return False
    return canonical_symbol in profile.target_identifier_candidates


def _private_target_match(profile: QueryProfile, symbol_name: str) -> bool:
    """Detecta lookup exacto de símbolos privados con prefijo underscore."""
    normalized_symbol = _normalize_text(symbol_name)
    if not normalized_symbol.startswith("_"):
        return False
    stripped_symbol = normalized_symbol.lstrip("_")
    if not stripped_symbol:
        return False
    return _canonicalize_identifier(stripped_symbol) in (
        profile.target_identifier_candidates
    )


def _is_definition_like_chunk(chunk: RetrievalChunk) -> bool:
    """Indica si el chunk parece una definición canónica del símbolo."""
    path = str(chunk.metadata.get("path", ""))
    symbol_name = str(chunk.metadata.get("symbol_name", ""))
    symbol_type = _symbol_type(chunk.metadata).lower()
    if not symbol_name or symbol_type not in _DEFINITION_SYMBOL_TYPES:
        return False
    if _is_test_path(path) or _is_docs_path(path) or _is_example_path(path):
        return False
    return _is_productive_implementation_path(path)


def _is_prefixed_wrapper_symbol(
    profile: QueryProfile,
    symbol_name: str,
) -> bool:
    """Detecta wrappers sintéticos o privados que envuelven al target."""
    raw_symbol = _normalize_text(symbol_name)
    canonical_symbol = _canonicalize_identifier(symbol_name)
    if not raw_symbol or not canonical_symbol or not profile.target_identifier_candidates:
        return False
    if canonical_symbol in profile.target_identifier_candidates:
        return False
    if raw_symbol.startswith("_") and raw_symbol.lstrip("_") in (
        profile.target_identifier_candidates
    ):
        return True
    prefixes = ("fake_", "test_", "mock_", "stub_")
    return any(
        canonical_symbol.startswith(prefix)
        and canonical_symbol.endswith(candidate)
        for prefix in prefixes
        for candidate in profile.target_identifier_candidates
    )


def _mentions_other_symbol_name(chunk: RetrievalChunk, symbol_name: str) -> bool:
    """Detecta wrappers cuyo texto depende de otro símbolo distinto al propio."""
    normalized_symbol = _canonicalize_identifier(symbol_name)
    if not normalized_symbol:
        return False
    for candidate in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", chunk.text):
        canonical_candidate = _canonicalize_identifier(candidate)
        if not canonical_candidate or canonical_candidate == normalized_symbol:
            continue
        if canonical_candidate.count("_") >= 1:
            return True
    return False


def _is_wrapper_or_entrypoint_chunk(
    profile: QueryProfile,
    chunk: RetrievalChunk,
) -> bool:
    """Marca orquestadores que mencionan el target pero no lo definen."""
    path = str(chunk.metadata.get("path", "")).strip().lower().replace("\\", "/")
    symbol_name = str(chunk.metadata.get("symbol_name", ""))
    if not symbol_name:
        return False
    if _exact_symbol_match(profile, symbol_name):
        return False
    if not _text_mentions_target(profile, chunk.text):
        return False
    segments = {segment for segment in path.split("/") if segment}
    if segments & _WRAPPER_PATH_SEGMENTS:
        return True
    return _mentions_other_symbol_name(chunk, symbol_name)


def _is_preferred_definition_candidate(
    profile: QueryProfile,
    chunk: RetrievalChunk,
) -> bool:
    """Determina si el chunk merece promoción inicial como definición."""
    if not profile.prefers_symbol_definitions:
        return False
    symbol_name = str(chunk.metadata.get("symbol_name", ""))
    return _exact_symbol_match(profile, symbol_name) and _is_definition_like_chunk(
        chunk
    )


def _score_chunk(profile: QueryProfile, chunk: RetrievalChunk) -> float:
    """Calcula un score heurístico ajustado por intención y tipo de chunk."""
    metadata = chunk.metadata
    path = str(metadata.get("path", ""))
    symbol_name = str(metadata.get("symbol_name", ""))
    symbol_type = _symbol_type(metadata).lower()
    base_score = float(chunk.score)

    symbol_overlap_raw = _normalized_overlap(profile.tokens, symbol_name)
    path_overlap_raw = _normalized_overlap(profile.tokens, path)
    text_overlap_raw = _normalized_overlap(profile.tokens, chunk.text)

    symbol_overlap = symbol_overlap_raw * 0.55
    path_overlap = path_overlap_raw * 0.25
    text_overlap = text_overlap_raw * 0.15

    score = base_score + symbol_overlap + path_overlap + text_overlap

    if profile.exact_identifier_query:
        canonical_symbol = _canonicalize_identifier(symbol_name)
        if canonical_symbol == profile.canonical_query:
            score += 0.90
        if _path_exact_match(path, profile.canonical_query):
            score += 0.35

    if profile.focus_identifiers:
        canonical_symbol = _canonicalize_identifier(symbol_name)
        if canonical_symbol in profile.focus_identifiers:
            score += 0.75
        if any(
            _path_exact_match(path, focus_identifier)
            for focus_identifier in profile.focus_identifiers
        ):
            score += 0.30

    config_path = _is_config_path(path)
    docs_path = _is_docs_path(path)
    example_path = _is_example_path(path)
    test_path = _is_test_path(path)
    productive_path = _is_productive_implementation_path(path)
    strong_overlap = _strong_overlap(profile, chunk)
    exact_symbol_match = _exact_symbol_match(profile, symbol_name)
    private_target_match = _private_target_match(profile, symbol_name)
    definition_like_chunk = _is_definition_like_chunk(chunk)
    target_text_match = _text_mentions_target(profile, chunk.text)
    prefixed_wrapper_symbol = _is_prefixed_wrapper_symbol(profile, symbol_name)
    wrapper_like_chunk = _is_wrapper_or_entrypoint_chunk(profile, chunk)
    orchestration_path = _is_orchestration_path(path)

    if profile.prefers_symbol_definitions:
        if exact_symbol_match:
            score += 1.15
        if private_target_match and definition_like_chunk:
            score += 0.95
        if definition_like_chunk:
            score += 0.45
        if productive_path:
            score += 0.20
        if target_text_match and productive_path and not wrapper_like_chunk:
            score += 0.35
        if not symbol_name and target_text_match and productive_path:
            score += 0.35
        if wrapper_like_chunk:
            score -= 0.60
        if wrapper_like_chunk and orchestration_path:
            score -= 1.45
        if exact_symbol_match and orchestration_path:
            score -= 0.22
        if private_target_match and orchestration_path:
            score -= 0.22
        if not symbol_name and orchestration_path and not target_text_match:
            score -= 0.40
        if prefixed_wrapper_symbol:
            score -= 1.20
        if test_path and not exact_symbol_match:
            score -= 0.85
        if docs_path:
            score -= 0.45
        if example_path:
            score -= 0.30
        if config_path and not exact_symbol_match:
            score -= 0.35

    if profile.prefers_docs and not profile.test_intent:
        if docs_path:
            score += 0.85
        if docs_path and target_text_match:
            score += 4.10
        if docs_path and target_text_match and symbol_type == "section":
            score += 0.25
        if text_overlap_raw >= 0.3:
            score += 0.20
        if config_path:
            score -= 0.60
        if test_path:
            score -= 0.40

    if profile.prefers_runtime_config and not profile.test_intent:
        if config_path:
            score += 0.40
        if symbol_type == "config_key":
            score += 0.30
        if docs_path:
            score -= 0.30
        if test_path and not strong_overlap and not profile.test_intent:
            score -= 0.45

    if profile.context_overview_intent and not profile.test_intent:
        if docs_path:
            score += 0.55
        if productive_path and symbol_type in _DEFINITION_SYMBOL_TYPES:
            score += 0.55
        if productive_path and not docs_path and not config_path:
            score += 0.15
        if config_path:
            score -= 0.75
        if test_path:
            score -= 0.60
        if example_path:
            score -= 0.45

    if profile.code_intent and (
        not profile.prefers_docs or profile.context_overview_intent
    ):
        if symbol_type in _DEFINITION_SYMBOL_TYPES:
            score += 0.30
        if productive_path and symbol_type in _DEFINITION_SYMBOL_TYPES:
            score += 0.35
        if symbol_overlap_raw >= 0.4 and symbol_type in _DEFINITION_SYMBOL_TYPES:
            score += 0.40
        if profile.implementation_intent and symbol_overlap_raw >= 0.4:
            score += 0.25
        if (
            profile.implementation_intent
            and wrapper_like_chunk
            and symbol_overlap_raw < 0.3
            and text_overlap_raw >= 0.4
        ):
            score -= 0.30
        if prefixed_wrapper_symbol:
            score -= 0.60
        if config_path and not strong_overlap and not profile.runtime_config_intent:
            score -= 0.15
        if docs_path and not profile.prefers_docs:
            score -= 0.15
        if test_path and not profile.test_intent:
            score -= 0.55

    if profile.test_intent and test_path:
        score += 0.45

    if _is_noise_path(path) and not strong_overlap:
        score -= 0.20

    return score


def _diversity_penalty(path_count: int) -> float:
    """Limita la sobreconcentración de resultados en un mismo archivo."""
    if path_count <= 0:
        return 0.0
    if path_count == 1:
        return 0.50
    return 0.80


def rerank(
    query: str,
    chunks: list[RetrievalChunk],
    top_k: int = 10,
) -> list[RetrievalChunk]:
    """Reordena candidatos con heurísticas de intención y diversidad por path."""
    if not chunks or top_k <= 0:
        return []

    profile = _build_query_profile(query)
    scored: list[tuple[float, RetrievalChunk]] = [
        (_score_chunk(profile, chunk), chunk) for chunk in chunks
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    selected: list[RetrievalChunk] = []
    path_counts: dict[str, int] = {}
    remaining = list(scored)
    best_preliminary_score = scored[0][0]

    while remaining and len(selected) < top_k:
        best_index = 0
        best_score: float | None = None
        if not selected and profile.prefers_symbol_definitions:
            for index, (preliminary_score, chunk) in enumerate(remaining):
                symbol_name = str(chunk.metadata.get("symbol_name", ""))
                mentions_target = _text_mentions_target(profile, chunk.text)
                productive_path = _is_productive_implementation_path(
                    str(chunk.metadata.get("path", ""))
                )
                if _is_preferred_definition_candidate(profile, chunk):
                    best_index = index
                    best_score = preliminary_score
                    break
                if not symbol_name and mentions_target and productive_path:
                    best_index = index
                    best_score = preliminary_score
                    break
        for index, (preliminary_score, chunk) in enumerate(remaining):
            path = str(chunk.metadata.get("path", ""))
            adjusted = preliminary_score - _diversity_penalty(path_counts.get(path, 0))
            if best_score is None or adjusted > best_score:
                best_score = adjusted
                best_index = index

        preliminary_score, chosen = remaining.pop(best_index)
        path = str(chosen.metadata.get("path", ""))
        final_score = preliminary_score - _diversity_penalty(path_counts.get(path, 0))
        chosen.score = final_score
        selected.append(chosen)
        path_counts[path] = path_counts.get(path, 0) + 1

    return selected
