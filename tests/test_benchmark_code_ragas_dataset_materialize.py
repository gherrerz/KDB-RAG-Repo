"""Tests for the code RAGAS dataset materializer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT / "scripts" / "benchmark_code_ragas_dataset_materialize.py"
)
SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_ragas_dataset_materialize",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_materialize_ragas_dataset_builds_enabled_reference_row() -> None:
    gold_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "queries": [
            {
                "id": "q1",
                "query": "donde esta target_symbol",
                "cohort": "exact_symbol",
                "expected_path": "src/sample.py",
                "expected_kind": "function",
                "symbol_name": "target_symbol",
                "reference_answer": "target_symbol esta en src/sample.py y retorna value.",
            }
        ],
    }
    materialized_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "defaults": {"top_n": 60, "top_k": 20},
        "workspace_root": "C:/repo",
        "queries": [
            {
                "id": "q1",
                "valid": True,
                "validation_errors": [],
                "materialized_expected": {
                    "path": "src/sample.py",
                    "start_line": 4,
                    "end_line": 6,
                    "symbol_name": "target_symbol",
                    "kind": "function",
                },
                "materialized_alternatives": [],
            }
        ],
    }

    dataset = MODULE.materialize_ragas_dataset(gold_data, materialized_data)

    assert dataset["dataset_name"] == "code_ragas_dataset_gold_v1"
    assert dataset["ragas_enabled_queries"] == 1
    row = dataset["queries"][0]
    assert row["retrieval_defaults"] == {"top_n": 60, "top_k": 20}
    assert row["ragas_reference"]["reference_answer"].startswith("target_symbol")
    assert row["ragas_reference"]["answer_type"] == "explanatory"
    assert row["ragas_reference"]["eval_mode"] == "retrieval_grounded"
    assert row["eligibility"]["ragas_enabled"] is True
    assert row["ragas_reference"]["reference_entities"] == [
        "target_symbol",
        "src/sample.py",
    ]
    assert {hint["value"] for hint in row["ragas_reference"]["reference_context_hints"]} == {
        "src/sample.py",
        "target_symbol",
        "function",
    }


def test_materialize_ragas_dataset_disables_literal_file_by_default() -> None:
    gold_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "queries": [
            {
                "id": "q2",
                "query": "dame el archivo completo README.md",
                "cohort": "literal_file",
                "expected_path": "README.md",
                "expected_kind": "file",
                "reference_answer": "README.md contiene la guia principal del proyecto.",
            }
        ],
    }
    materialized_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "defaults": {"top_n": 60, "top_k": 20},
        "workspace_root": "C:/repo",
        "queries": [
            {
                "id": "q2",
                "valid": True,
                "validation_errors": [],
                "materialized_expected": {
                    "path": "README.md",
                    "start_line": None,
                    "end_line": None,
                    "symbol_name": None,
                    "kind": "file",
                },
                "materialized_alternatives": [],
            }
        ],
    }

    dataset = MODULE.materialize_ragas_dataset(gold_data, materialized_data)

    assert dataset["ragas_enabled_queries"] == 0
    row = dataset["queries"][0]
    assert row["eligibility"]["valid"] is True
    assert row["eligibility"]["ragas_enabled"] is False
    assert row["eligibility"]["disabled_reasons"] == ["cohort_disabled_by_default"]


def test_materialize_ragas_dataset_marks_missing_materialized_query_invalid() -> None:
    gold_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "queries": [
            {
                "id": "q3",
                "query": "donde esta missing_symbol",
                "cohort": "exact_symbol",
                "expected_path": "src/missing.py",
                "reference_answer": "missing_symbol esta en src/missing.py.",
            }
        ],
    }
    materialized_data = {
        "dataset_name": "code_retrieval_gold_v1",
        "repo_id": "repo1",
        "defaults": {"top_n": 60, "top_k": 20},
        "workspace_root": "C:/repo",
        "queries": [],
    }

    dataset = MODULE.materialize_ragas_dataset(gold_data, materialized_data)

    assert dataset["invalid_queries"] == 1
    row = dataset["queries"][0]
    assert row["eligibility"]["valid"] is False
    assert row["eligibility"]["ragas_enabled"] is False
    assert row["validation_errors"] == ["missing_materialized_query"]