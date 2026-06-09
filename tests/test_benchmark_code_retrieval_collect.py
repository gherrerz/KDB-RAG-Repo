"""Tests for code retrieval collector and IR scorer scripts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECT_MODULE_PATH = REPO_ROOT / "scripts" / "benchmark_code_retrieval_collect.py"
COLLECT_SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_retrieval_collect",
    COLLECT_MODULE_PATH,
)
assert COLLECT_SPEC is not None and COLLECT_SPEC.loader is not None
COLLECT_MODULE = importlib.util.module_from_spec(COLLECT_SPEC)
sys.modules[COLLECT_SPEC.name] = COLLECT_MODULE
COLLECT_SPEC.loader.exec_module(COLLECT_MODULE)

SCORE_MODULE_PATH = REPO_ROOT / "scripts" / "benchmark_code_ir_score.py"
SCORE_SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_ir_score",
    SCORE_MODULE_PATH,
)
assert SCORE_SPEC is not None and SCORE_SPEC.loader is not None
SCORE_MODULE = importlib.util.module_from_spec(SCORE_SPEC)
sys.modules[SCORE_SPEC.name] = SCORE_MODULE
SCORE_SPEC.loader.exec_module(SCORE_MODULE)


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> dict:
        return self._body


def test_collect_retrieval_queries_builds_raw_and_summary_rows() -> None:
    query = COLLECT_MODULE.MaterializedQuery(
        query_id="q1",
        query="donde esta run_query",
        cohort="exact_symbol",
        gate_candidate=True,
        preferred_endpoint="query/retrieval",
        expected_path="src/coderag/api/query_service.py",
        expected_kind="function",
        materialized_expected={
            "path": "src/coderag/api/query_service.py",
            "start_line": 1111,
            "end_line": 1287,
            "symbol_name": "run_query",
            "kind": "function",
        },
        materialized_alternatives=[],
    )

    def fake_post(url: str, *, json: dict, timeout: float) -> FakeResponse:
        assert url.endswith("/query/retrieval")
        assert json["top_n"] == 60
        assert json["top_k"] == 20
        assert timeout == 30.0
        return FakeResponse(
            200,
            {
                "chunks": [
                    {
                        "path": "src/coderag/api/query_service.py",
                        "start_line": 1111,
                        "end_line": 1287,
                        "kind": "function",
                        "metadata": {"symbol_name": "run_query"},
                    }
                ],
                "citations": [
                    {
                        "path": "src/coderag/api/query_service.py",
                        "start_line": 1111,
                        "end_line": 1287,
                        "score": 1.0,
                        "reason": "hybrid_rag_match",
                    }
                ],
                "diagnostics": {
                    "fallback_reason": None,
                    "fallback_used": False,
                    "stage_timings_ms": {"total_ms": 12.5},
                },
            },
        )

    metadata, json_rows, csv_rows = COLLECT_MODULE.collect_retrieval_queries(
        base_url="http://127.0.0.1:8000",
        repo_id="kdb-rag-repo",
        materialized_queries=[query],
        top_n=60,
        top_k=20,
        timeout_seconds=30.0,
        include_context=False,
        request_fn=fake_post,
    )

    assert metadata["successful_queries"] == 1
    assert len(json_rows) == 1
    assert len(csv_rows) == 1
    assert csv_rows[0].top1_path == "src/coderag/api/query_service.py"
    assert json_rows[0]["response_body"]["chunks"][0]["metadata"]["symbol_name"] == "run_query"


def test_score_row_computes_exact_hits_and_ranking_metrics() -> None:
    row = {
        "query_id": "q1",
        "query": "donde esta run_query",
        "cohort": "exact_symbol",
        "gate_candidate": True,
        "ok": True,
        "materialized_expected": {
            "path": "src/coderag/api/query_service.py",
            "start_line": 1111,
            "end_line": 1287,
            "symbol_name": "run_query",
            "kind": "function",
        },
        "materialized_alternatives": [],
        "response_body": {
            "chunks": [
                {
                    "path": "src/coderag/api/query_service.py",
                    "start_line": 1111,
                    "end_line": 1287,
                    "metadata": {"symbol_name": "run_query"},
                },
                {
                    "path": "src/coderag/api/server.py",
                    "start_line": 492,
                    "end_line": 515,
                    "metadata": {"symbol_name": "run_query"},
                },
            ],
            "citations": [
                {
                    "path": "src/coderag/api/query_service.py",
                    "start_line": 1111,
                    "end_line": 1287,
                }
            ],
            "diagnostics": {"fallback_used": False},
        },
    }

    score = SCORE_MODULE.score_row(row)

    assert score.exact_path_hit_at_1 == 1.0
    assert score.exact_path_hit_at_3 == 1.0
    assert score.exact_line_hit_at_1 == 1.0
    assert score.exact_symbol_hit_at_1 == 1.0
    assert score.line_span_iou_at_1 == 1.0
    assert score.reciprocal_rank == 1.0
    assert score.citation_path_precision == 1.0
    assert score.citation_span_recall == 1.0


def test_build_gate_fails_on_hard_threshold_miss() -> None:
    gate = SCORE_MODULE.build_gate(
        overall={
            "gate_candidate": {
                "queries_count": 26,
                "exact_path_hit_at_1": 0.75,
                "exact_line_hit_at_1": 0.72,
                "mrr": 0.90,
                "fallback_rate": 0.0,
                "exact_path_hit_at_3": 0.95,
                "ndcg_5": 0.91,
                "citation_path_precision_mean": 0.90,
            }
        },
        hard_thresholds=SCORE_MODULE.HARD_THRESHOLDS,
        soft_thresholds=SCORE_MODULE.SOFT_THRESHOLDS,
    )

    assert gate["status"] == "fail"
    assert "exact_path_hit_at_1" in gate["failed_hard_metrics"]


def test_build_gate_passes_hard_and_warns_on_soft_threshold_miss() -> None:
    gate = SCORE_MODULE.build_gate(
        overall={
            "gate_candidate": {
                "queries_count": 26,
                "exact_path_hit_at_1": 0.81,
                "exact_line_hit_at_1": 0.71,
                "mrr": 0.87,
                "fallback_rate": 0.01,
                "exact_path_hit_at_3": 0.93,
                "ndcg_5": 0.91,
                "citation_path_precision_mean": 0.20,
            }
        },
        hard_thresholds=SCORE_MODULE.HARD_THRESHOLDS,
        soft_thresholds=SCORE_MODULE.SOFT_THRESHOLDS,
    )

    assert gate["status"] == "pass_with_warnings"
    assert gate["failed_hard_metrics"] == []
    assert gate["failed_soft_metrics"] == ["citation_path_precision_mean"]