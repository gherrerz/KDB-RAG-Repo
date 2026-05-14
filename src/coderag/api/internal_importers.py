"""Internal file importer graph helpers extracted from query service."""

from collections.abc import Callable
from dataclasses import dataclass

from coderag.core.models import RetrievalChunk


@dataclass(frozen=True)
class InternalImporterHooks:
    """Injected collaborators required by internal importer helpers."""

    graph_builder_factory: Callable[[], object]
    resolve_reverse_file_target_paths: Callable[
        [str, str], tuple[list[str], int, tuple[str, ...]]
    ]


def resolve_internal_file_importer_paths(
    repo_id: str,
    query: str,
    *,
    hooks: InternalImporterHooks,
) -> tuple[dict[str, int], list[str], tuple[str, ...]]:
    """Resolve files that directly import the file targeted by the query."""
    target_paths, match_score, candidates = hooks.resolve_reverse_file_target_paths(
        repo_id,
        query,
    )
    if not target_paths:
        return {}, [], candidates

    graph = hooks.graph_builder_factory()
    try:
        rows = graph.query_file_importers(
            repo_id=repo_id,
            target_paths=target_paths,
            limit=100,
        )
    except Exception:
        return {}, target_paths, candidates
    finally:
        graph.close()

    results: dict[str, int] = {}
    for row in rows:
        source_path = str(row.get("path", "") or "").strip()
        if source_path:
            results[source_path] = max(match_score, results.get(source_path, 0))
    return results, target_paths, candidates


def apply_internal_file_importer_seed_boost(
    repo_id: str,
    query: str,
    chunks: list[RetrievalChunk],
    *,
    hooks: InternalImporterHooks,
) -> tuple[list[RetrievalChunk], int, dict[str, int], list[str]]:
    """Boost chunks backed by reverse IMPORTS_FILE matches before reranking."""
    if not chunks:
        return chunks, 0, {}, []

    matched_paths, target_paths, candidates = resolve_internal_file_importer_paths(
        repo_id=repo_id,
        query=query,
        hooks=hooks,
    )
    if not matched_paths:
        return chunks, 0, {}, target_paths

    boosted_count = 0
    rescored: list[tuple[float, RetrievalChunk]] = []
    for chunk in chunks:
        path = str(chunk.metadata.get("path", "") or "").strip()
        boost = 0.0
        if path in matched_paths:
            boost += 0.24 + min(0.16, 0.05 * max(0, matched_paths[path] - 1))
            haystack = " ".join(
                [
                    path.lower(),
                    str(chunk.metadata.get("symbol_name", "") or "").lower(),
                    chunk.text.lower(),
                ]
            )
            if any(candidate in haystack for candidate in candidates):
                boost += 0.08
        if boost > 0:
            boosted_count += 1
            chunk.score = float(chunk.score) + boost
        rescored.append((float(chunk.score), chunk))

    rescored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in rescored], boosted_count, matched_paths, target_paths


def build_internal_file_importer_seed_chunks(
    repo_id: str,
    matched_paths: dict[str, int],
    chunks: list[RetrievalChunk],
) -> tuple[list[RetrievalChunk], int]:
    """Create synthetic graph seeds for missing internal importer files."""
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
                id=f"reverse-import-seed:{repo_id}:{path}",
                text=f"Graph-backed file importer seed for {path}",
                score=0.44 + min(0.18, 0.06 * max(0, match_score - 1)),
                metadata={
                    "repo_id": repo_id,
                    "path": path,
                    "start_line": 1,
                    "end_line": 1,
                    "kind": "graph_file_importer_seed",
                },
            )
        )
    return seed_chunks, len(seed_chunks)