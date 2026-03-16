"""Pruebas para el comportamiento de orquestación de la canalización de ingesta."""

from pathlib import Path

import pytest

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion import pipeline


def test_ingest_repository_continues_on_graph_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """La canalización registra una advertencia y se completa incluso cuando falla la indexación de Neo4j."""
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
        scan_max_file_size_bytes = 12345
        scan_excluded_dirs = ".git,node_modules"
        scan_excluded_extensions = ".png,.zip"
        scan_excluded_files = ".gitignore,.env"

    received_scan_args: dict[str, object] = {}

    def _fake_scan_repository_with_stats(
        repo_path: Path,
        max_file_size: int = 200_000,
        excluded_dirs: set[str] | None = None,
        excluded_extensions: set[str] | None = None,
        excluded_files: set[str] | None = None,
    ) -> tuple[list[ScannedFile], dict[str, int]]:
        received_scan_args["repo_path"] = repo_path
        received_scan_args["max_file_size"] = max_file_size
        received_scan_args["excluded_dirs"] = excluded_dirs or set()
        received_scan_args["excluded_extensions"] = excluded_extensions or set()
        received_scan_args["excluded_files"] = excluded_files or set()
        return scanned, {
            "visited": 1,
            "scanned": 1,
            "excluded_dir": 0,
            "excluded_extension": 0,
            "excluded_file": 0,
            "excluded_size": 0,
            "excluded_decode": 0,
        }

    monkeypatch.setattr(pipeline, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        pipeline,
        "clone_repository",
        lambda repo_url, destination_root, branch, commit: ("r1", tmp_path),
    )
    monkeypatch.setattr(
        pipeline,
        "scan_repository_with_stats",
        _fake_scan_repository_with_stats,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_symbol_chunks",
        lambda repo_id, scanned_files: symbols,
    )
    monkeypatch.setattr(
        pipeline,
        "_index_vectors",
        lambda repo_id, s, c, **kwargs: None,
    )
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
    assert received_scan_args["repo_path"] == tmp_path
    assert received_scan_args["max_file_size"] == 12345
    assert ".git" in received_scan_args["excluded_dirs"]
    assert "node_modules" in received_scan_args["excluded_dirs"]
    assert ".png" in received_scan_args["excluded_extensions"]
    assert ".zip" in received_scan_args["excluded_extensions"]
    assert ".gitignore" in received_scan_args["excluded_files"]
    assert ".env" in received_scan_args["excluded_files"]
    assert any("Advertencia: grafo Neo4j no disponible" in item for item in logs)
    assert logs[-1] == "Ingesta finalizada"
