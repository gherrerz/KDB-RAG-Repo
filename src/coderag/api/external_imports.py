"""External import graph helpers extracted from query service."""

from collections.abc import Callable
from dataclasses import dataclass
import re

from coderag.core.models import RetrievalChunk


@dataclass(frozen=True)
class ExternalImportHooks:
    """Injected collaborators required by external import helpers."""

    graph_builder_factory: Callable[[], object]
    is_external_import_query: Callable[[str], bool]


def extract_external_import_candidates(query: str) -> tuple[str, ...]:
    """Extract candidate external import references from a user query."""
    excluded = {
        "where",
        "is",
        "the",
        "a",
        "an",
        "in",
        "of",
        "from",
        "to",
        "used",
        "by",
        "import",
        "imported",
        "imports",
        "dependency",
        "dependencies",
        "dependencia",
        "dependencias",
        "donde",
        "dónde",
        "esta",
        "está",
        "se",
        "que",
        "qué",
        "el",
        "la",
        "los",
        "las",
        "en",
        "archivo",
        "file",
    }
    candidates: list[str] = []
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_.-]{1,}", query):
        token = match.group(0).strip().lower().strip("._-")
        if len(token) < 3 or token in excluded:
            continue
        candidates.append(token)
        if "." in token:
            head = token.split(".", maxsplit=1)[0].strip("._-")
            if len(head) >= 3 and head not in excluded:
                candidates.append(head)
    return tuple(dict.fromkeys(candidates))


def resolve_external_import_source_paths(
    repo_id: str,
    query: str,
    *,
    hooks: ExternalImportHooks,
) -> dict[str, int]:
    """Resolve source files connected to external imports relevant to the query."""
    if not hooks.is_external_import_query(query):
        return {}

    candidates = extract_external_import_candidates(query)
    if not candidates:
        return {}

    graph = hooks.graph_builder_factory()
    try:
        rows = graph.query_external_import_source_paths(
            repo_id=repo_id,
            candidates=list(candidates),
            limit=100,
        )
    except Exception:
        return {}
    finally:
        graph.close()

    results: dict[str, int] = {}
    for row in rows:
        source_path = str(row.get("source_path", "") or "").strip()
        match_score = int(row.get("match_score", 0) or 0)
        if source_path:
            results[source_path] = max(match_score, results.get(source_path, 0))
    return results


def apply_external_import_seed_boost(
    repo_id: str,
    query: str,
    chunks: list[RetrievalChunk],
    *,
    hooks: ExternalImportHooks,
) -> tuple[list[RetrievalChunk], int, dict[str, int]]:
    """Boost chunks backed by IMPORTS_EXTERNAL_FILE before reranking."""
    if not chunks:
        return chunks, 0, {}

    matched_paths = resolve_external_import_source_paths(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    if not matched_paths:
        return chunks, 0, {}

    candidates = extract_external_import_candidates(query)
    boosted_count = 0
    rescored: list[tuple[float, RetrievalChunk]] = []
    for chunk in chunks:
        path = str(chunk.metadata.get("path", "") or "").strip()
        boost = 0.0
        if path in matched_paths:
            boost += 0.28 + min(0.18, 0.06 * max(0, matched_paths[path] - 1))
            haystack = " ".join(
                [
                    path.lower(),
                    str(chunk.metadata.get("symbol_name", "") or "").lower(),
                    chunk.text.lower(),
                ]
            )
            if any(candidate in haystack for candidate in candidates):
                boost += 0.12
        if boost > 0:
            boosted_count += 1
            chunk.score = float(chunk.score) + boost
        rescored.append((float(chunk.score), chunk))

    rescored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in rescored], boosted_count, matched_paths


def build_external_import_seed_chunks(
    repo_id: str,
    matched_paths: dict[str, int],
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int]:
    """Create synthetic graph seeds for missing external-import source files."""
    if not matched_paths:
        return [], 0

    existing_paths = {
        str(chunk.metadata.get("path", "") or "").strip() for chunk in chunks
    }
    seed_chunks: list[RetrievalChunk] = []
    for path, match_score in sorted(
        matched_paths.items(),
        key=lambda item: (-int(item[1]), item[0]),
    ):
        if not path or path in existing_paths:
            continue
        seed_chunks.append(
            RetrievalChunk(
                id=f"external-seed:{repo_id}:{path}",
                text=f"Graph-backed external import seed for {path}",
                score=0.45 + min(0.20, 0.08 * max(0, match_score - 1)),
                metadata={
                    "repo_id": repo_id,
                    "path": path,
                    "start_line": 1,
                    "end_line": 1,
                    "kind": "graph_external_seed",
                },
            )
        )
    return seed_chunks, len(seed_chunks)