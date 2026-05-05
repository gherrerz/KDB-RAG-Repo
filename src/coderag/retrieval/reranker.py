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
_IMPLEMENTATION_HINT_TOKENS = {
    "execute",
    "executed",
    "execution",
    "implement",
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


@dataclass(frozen=True)
class QueryProfile:
    """Señales derivadas de la consulta para ajustar el reranking."""

    raw_query: str
    normalized_query: str
    canonical_query: str
    tokens: tuple[str, ...]
    focus_identifiers: tuple[str, ...]
    exact_identifier_query: bool
    runtime_config_intent: bool
    code_intent: bool
    test_intent: bool
    natural_language_query: bool
    implementation_intent: bool


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
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", query)
            if len(token) >= 3
            and (
                _canonicalize_identifier(token).count("_") >= 1
                or "." in token
                or "-" in token
            )
        )
    )
    runtime_config_intent = bool(token_set & _RUNTIME_CONFIG_TOKENS)
    code_intent = bool(token_set & _CODE_TOKENS)
    test_intent = bool(token_set & _TEST_TOKENS)
    natural_language_query = not exact_identifier_query
    implementation_intent = bool(token_set & _IMPLEMENTATION_HINT_TOKENS)
    return QueryProfile(
        raw_query=query,
        normalized_query=normalized_query,
        canonical_query=_canonicalize_identifier(query),
        tokens=tokens,
        focus_identifiers=focus_identifiers,
        exact_identifier_query=exact_identifier_query,
        runtime_config_intent=runtime_config_intent,
        code_intent=code_intent,
        test_intent=test_intent,
        natural_language_query=natural_language_query,
        implementation_intent=implementation_intent,
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


def _is_noise_path(path: str) -> bool:
    """Marca rutas de bajo valor por defecto para queries funcionales."""
    normalized = path.strip().lower().replace("\\", "/")
    return any(segment in normalized for segment in _NOISE_PATH_SEGMENTS)


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


def _mentions_other_symbol_name(chunk: RetrievalChunk, symbol_name: str) -> bool:
    """Detecta wrappers cuyo texto depende de otro símbolo distinto al propio."""
    normalized_text = _canonicalize_identifier(chunk.text)
    normalized_symbol = _canonicalize_identifier(symbol_name)
    if not normalized_text or not normalized_symbol:
        return False
    matches = {
        candidate
        for candidate in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", normalized_text)
        if candidate != normalized_symbol and candidate in normalized_text
    }
    return bool(matches)


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
    test_path = _is_test_path(path)
    productive_path = _is_productive_implementation_path(path)
    strong_overlap = _strong_overlap(profile, chunk)
    wrapper_like_chunk = _mentions_other_symbol_name(chunk, symbol_name)

    if profile.runtime_config_intent and not profile.test_intent:
        if config_path:
            score += 0.40
        if symbol_type == "config_key":
            score += 0.30
        if test_path and not strong_overlap and not profile.test_intent:
            score -= 0.45

    if profile.code_intent:
        if symbol_type in {"function", "method", "class", "module"}:
            score += 0.30
        if productive_path and symbol_type in {"function", "method", "class", "module"}:
            score += 0.35
        if symbol_overlap_raw >= 0.4 and symbol_type in {"function", "method", "class"}:
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
        if config_path and not strong_overlap and not profile.runtime_config_intent:
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

    while remaining and len(selected) < top_k:
        best_index = 0
        best_score: float | None = None
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
