"""Tests for offline RAGAS collector and scorer scripts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECT_MODULE_PATH = REPO_ROOT / "scripts" / "benchmark_code_ragas_collect.py"
COLLECT_SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_ragas_collect",
    COLLECT_MODULE_PATH,
)
assert COLLECT_SPEC is not None and COLLECT_SPEC.loader is not None
COLLECT_MODULE = importlib.util.module_from_spec(COLLECT_SPEC)
sys.modules[COLLECT_SPEC.name] = COLLECT_MODULE
COLLECT_SPEC.loader.exec_module(COLLECT_MODULE)

SCORE_MODULE_PATH = REPO_ROOT / "scripts" / "benchmark_code_ragas_score.py"
SCORE_SPEC = importlib.util.spec_from_file_location(
    "benchmark_code_ragas_score",
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


def test_collect_ragas_queries_freezes_answer_citations_and_contexts(
    tmp_path: Path,
) -> None:
    sample_file = tmp_path / "src" / "sample.py"
    sample_file.parent.mkdir(parents=True)
    sample_file.write_text(
        "def target_symbol():\n"
        "    return 'value'\n",
        encoding="utf-8",
    )
    query = COLLECT_MODULE.MaterializedRagasQuery(
        query_id="q1",
        query="donde esta target_symbol",
        cohort="exact_symbol",
        gate_candidate=True,
        repo_id="repo1",
        top_n=60,
        top_k=20,
        ragas_reference={
            "reference_answer": "target_symbol esta en src/sample.py y retorna value.",
            "requires_citations": True,
            "reference_entities": ["target_symbol", "src/sample.py"],
        },
        materialized_expected={
            "path": "src/sample.py",
            "start_line": 1,
            "end_line": 2,
            "snippet_preview": "def target_symbol():\n    return 'value'",
        },
        materialized_alternatives=[],
        eligibility={"valid": True, "ragas_enabled": True},
    )

    def fake_post(url: str, *, json: dict, timeout: float) -> FakeResponse:
        assert url.endswith("/query")
        assert json["top_n"] == 60
        assert json["top_k"] == 20
        assert timeout == 30.0
        return FakeResponse(
            200,
            {
                "answer": "target_symbol esta en src/sample.py y retorna value.",
                "citations": [
                    {
                        "path": "src/sample.py",
                        "start_line": 1,
                        "end_line": 2,
                        "score": 0.99,
                        "reason": "hybrid_rag_match",
                    }
                ],
                "diagnostics": {
                    "fallback_used": False,
                    "fallback_reason": None,
                    "stage_timings_ms": {"total_ms": 18.0},
                },
            },
        )

    metadata, json_rows, csv_rows = COLLECT_MODULE.collect_ragas_queries(
        base_url="http://127.0.0.1:8000",
        repo_id=None,
        materialized_queries=[query],
        workspace_root=tmp_path,
        top_n_override=None,
        top_k_override=None,
        timeout_seconds=30.0,
        request_fn=fake_post,
    )

    assert metadata["successful_queries"] == 1
    assert metadata["score_eligible_queries"] == 1
    assert json_rows[0]["answer_text"].startswith("target_symbol")
    assert json_rows[0]["retrieved_contexts"][0]["source"] == "workspace_citation_span"
    assert "return 'value'" in json_rows[0]["retrieved_contexts"][0]["text"]
    assert csv_rows[0].score_eligible is True


def test_score_row_computes_ragas_style_metrics_for_evaluable_row() -> None:
    row = {
        "query_id": "q1",
        "query": "donde esta target_symbol",
        "cohort": "exact_symbol",
        "gate_candidate": True,
        "ok": True,
        "score_eligible": True,
        "score_skip_reason": None,
        "fallback_used": False,
        "citations_count": 1,
        "retrieved_contexts_count": 1,
        "answer_text": "target_symbol esta en src/sample.py y retorna value.",
        "ragas_reference": {
            "reference_answer": "target_symbol esta en src/sample.py y retorna value.",
            "reference_entities": ["target_symbol", "src/sample.py"],
        },
        "materialized_expected": {
            "snippet_preview": "def target_symbol():\n    return 'value'",
        },
        "materialized_alternatives": [],
        "retrieved_contexts": [
            {
                "path": "src/sample.py",
                "start_line": 1,
                "end_line": 2,
                "text": "def target_symbol():\n    return 'value'",
            }
        ],
    }

    score = SCORE_MODULE.score_row(row)

    assert score.score_eligible is True
    assert score.answer_relevancy is not None and score.answer_relevancy > 0.5
    assert score.answer_correctness is not None and score.answer_correctness > 0.7
    assert score.faithfulness is not None and score.faithfulness > 0.4
    assert score.context_entity_recall == 1.0


def test_score_row_does_not_overpenalize_verbose_grounded_answer() -> None:
    row = {
        "query_id": "q_verbose",
        "query": "donde esta target_symbol",
        "cohort": "exact_symbol",
        "gate_candidate": True,
        "ok": True,
        "score_eligible": True,
        "score_skip_reason": None,
        "fallback_used": False,
        "citations_count": 3,
        "retrieved_contexts_count": 3,
        "answer_text": (
            "target_symbol esta en src/sample.py y retorna value. "
            "Ademas el modulo incluye helpers, validaciones, comentarios y "
            "detalles adicionales que expanden la explicacion para el usuario."
        ),
        "ragas_reference": {
            "reference_answer": "target_symbol esta en src/sample.py y retorna value.",
            "reference_entities": ["target_symbol", "src/sample.py"],
            "reference_claims": [
                "target_symbol esta en src/sample.py",
                "target_symbol retorna value",
            ],
        },
        "materialized_expected": {
            "snippet_preview": "def target_symbol():\n    return 'value'",
        },
        "materialized_alternatives": [],
        "retrieved_contexts": [
            {
                "path": "src/sample.py",
                "start_line": 1,
                "end_line": 2,
                "text": "def target_symbol():\n    return 'value'",
            },
            {
                "path": "src/extra.py",
                "start_line": 10,
                "end_line": 12,
                "text": "def helper():\n    return True",
            },
            {
                "path": "src/notes.py",
                "start_line": 1,
                "end_line": 2,
                "text": "NOT_RELEVANT = True",
            },
        ],
    }

    score = SCORE_MODULE.score_row(row)

    assert score.answer_correctness is not None and score.answer_correctness > 0.55
    assert score.context_precision is not None and score.context_precision > 0.35


def test_score_row_skips_non_evaluable_row() -> None:
    row = {
        "query_id": "q2",
        "query": "donde esta missing_symbol",
        "cohort": "exact_symbol",
        "gate_candidate": True,
        "ok": True,
        "score_eligible": False,
        "score_skip_reason": "missing_citations",
        "fallback_used": False,
        "citations_count": 0,
        "retrieved_contexts_count": 0,
        "answer_text": "",
        "ragas_reference": {
            "reference_answer": "missing_symbol esta en src/missing.py.",
        },
        "materialized_expected": {},
        "materialized_alternatives": [],
        "retrieved_contexts": [],
    }

    score = SCORE_MODULE.score_row(row)

    assert score.score_eligible is False
    assert score.skip_reason == "missing_citations"
    assert score.answer_correctness is None


def test_build_gate_fails_when_scored_rate_is_too_low() -> None:
    gate = SCORE_MODULE.build_gate(
        overall={
            "gate_candidate": {
                "queries_count": 10,
                "answer_relevancy": 0.80,
                "answer_correctness": 0.81,
                "faithfulness": 0.79,
                "context_entity_recall": 0.90,
                "scored_rate": 0.40,
                "context_precision": 0.60,
                "context_recall": 0.65,
            }
        },
        hard_thresholds=SCORE_MODULE.HARD_THRESHOLDS,
        soft_thresholds=SCORE_MODULE.SOFT_THRESHOLDS,
    )

    assert gate["status"] == "fail"
    assert "scored_rate" in gate["failed_hard_metrics"]


def test_score_collected_report_with_engine_auto_falls_back_to_proxy_when_unconfigured(
    monkeypatch,
) -> None:
    for env_name in (
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "VERTEXAI_PROJECT",
    ):
        monkeypatch.delenv(env_name, raising=False)

    collected_report = {
        "rows": [
            {
                "query_id": "q1",
                "query": "donde esta target_symbol",
                "cohort": "exact_symbol",
                "gate_candidate": True,
                "ok": True,
                "score_eligible": True,
                "score_skip_reason": None,
                "fallback_used": False,
                "citations_count": 1,
                "retrieved_contexts_count": 1,
                "answer_text": "target_symbol esta en src/sample.py y retorna value.",
                "ragas_reference": {
                    "reference_answer": "target_symbol esta en src/sample.py y retorna value.",
                    "reference_entities": ["target_symbol", "src/sample.py"],
                    "reference_claims": [
                        "target_symbol esta en src/sample.py",
                        "target_symbol retorna value",
                    ],
                },
                "materialized_expected": {
                    "snippet_preview": "def target_symbol():\n    return 'value'",
                },
                "materialized_alternatives": [],
                "retrieved_contexts": [
                    {
                        "path": "src/sample.py",
                        "start_line": 1,
                        "end_line": 2,
                        "text": "def target_symbol():\n    return 'value'",
                    }
                ],
            }
        ]
    }

    _, rows, _, _, _, scoring_meta = SCORE_MODULE.score_collected_report_with_engine(
        collected_report,
        scoring_engine=SCORE_MODULE.AUTO_SCORING_ENGINE,
        ragas_provider=None,
        ragas_llm_model=None,
        ragas_embedding_model=None,
        ragas_batch_size=2,
    )

    assert rows[0].answer_correctness is not None
    assert scoring_meta["scoring_engine"] == SCORE_MODULE.PROXY_SCORING_ENGINE
    assert any(
        "ragas_fallback:RuntimeError:ragas_provider_not_configured" in note
        for note in scoring_meta["engine_notes"]
    )