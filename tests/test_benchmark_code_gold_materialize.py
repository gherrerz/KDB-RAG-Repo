"""Tests for the code retrieval gold materializer."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "benchmark_code_gold_materialize.py"
SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_gold_materialize",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_materialize_dataset_resolves_python_symbol_span(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    source_dir = repo_root / "src"
    source_dir.mkdir(parents=True)
    file_path = source_dir / "sample.py"
    file_path.write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def target_symbol():\n"
        "    value = 2\n"
        "    return value\n",
        encoding="utf-8",
    )

    gold_data = {
        "dataset_name": "test",
        "repo_id": "repo",
        "queries": [
            {
                "id": "q1",
                "query": "donde esta target_symbol",
                "expected_path": "src/sample.py",
                "expected_start_line": 4,
                "expected_end_line": None,
                "symbol_name": "target_symbol",
                "expected_kind": "function",
                "acceptable_alternatives": [],
            }
        ],
    }

    materialized = MODULE.materialize_dataset(gold_data, repo_root)

    assert materialized["valid_queries"] == 1
    query = materialized["queries"][0]
    assert query["materialized_expected"]["start_line"] == 4
    assert query["materialized_expected"]["end_line"] == 6
    assert "return value" in query["materialized_expected"]["snippet"]


def test_materialize_dataset_uses_preview_for_whole_file_targets(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(parents=True)
    file_path = docs_dir / "big.txt"
    file_path.write_text(
        "\n".join(f"line {index}" for index in range(1, 101)),
        encoding="utf-8",
    )

    gold_data = {
        "dataset_name": "test",
        "repo_id": "repo",
        "queries": [
            {
                "id": "q2",
                "query": "dame el archivo completo big.txt",
                "expected_path": "docs/big.txt",
                "expected_start_line": None,
                "expected_end_line": None,
                "symbol_name": None,
                "expected_kind": "file",
                "acceptable_alternatives": [],
            }
        ],
    }

    materialized = MODULE.materialize_dataset(gold_data, repo_root)

    assert materialized["valid_queries"] == 1
    query = materialized["queries"][0]
    expected = query["materialized_expected"]
    assert expected["content_strategy"] == "whole_file"
    assert expected["snippet"] is None
    assert expected["preview_end_line"] == MODULE.WHOLE_FILE_PREVIEW_LINES
    assert "line 1" in expected["snippet_preview"]
    assert "line 40" in expected["snippet_preview"]
    assert "line 41" not in expected["snippet_preview"]