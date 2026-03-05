"""Tests for ingestion pipeline orchestration behavior."""

from pathlib import Path

import pytest

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion import pipeline


def test_ingest_repository_continues_on_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pipeline logs warning and completes even when Neo4j indexing fails."""
    scanned = [ScannedFile(path="a.py", language="python", content="def a():\n pass")]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="r1",
            path="a.py",
            language="python",
            symbol_name="a",
            symbol_type="function",
            start_line=1,
            end_line=2,
            snippet="def a():\n pass",
        )
    ]

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit: ("r1", tmp_path),
    )
    monkeypatch.setattr(pipeline, "scan_repository", lambda repo_path: scanned)
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(pipeline, "_index_vectors", lambda repo_id, s, c: None)
    monkeypatch.setattr(
        pipeline,
        "_index_bm25",
        lambda repo_id, scanned_files, chunks: None,
    )

    def fail_graph(
        repo_id: str,
        scanned_files: list[ScannedFile],
        chunks: list[SymbolChunk],
    ) -> None:
        raise RuntimeError("neo4j auth")

    monkeypatch.setattr(pipeline, "_index_graph", fail_graph)

    logs: list[str] = []
    repo_id = pipeline.ingest_repository(
        repo_url="https://example.com/repo.git",
        branch="main",
        commit=None,
        logger=logs.append,
    )

    assert repo_id == "r1"
    assert any("Advertencia: grafo Neo4j no disponible" in item for item in logs)
    assert logs[-1] == "Ingesta finalizada"
