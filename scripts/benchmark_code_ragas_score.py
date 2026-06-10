"""Calcula métricas offline de respuesta sobre el collector RAGAS."""

from __future__ import annotations

import argparse
import copy
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
import types
from typing import Any


HARD_THRESHOLDS: dict[str, float] = {
    "answer_relevancy": 0.55,
    "answer_correctness": 0.55,
    "faithfulness": 0.45,
    "context_entity_recall": 0.70,
    "scored_rate": 0.80,
}

SOFT_THRESHOLDS: dict[str, float] = {
    "context_precision": 0.35,
    "context_recall": 0.45,
}

PROXY_SCORING_ENGINE = "offline_lexical_proxy"
RAGAS_SCORING_ENGINE = "ragas"
AUTO_SCORING_ENGINE = "auto"

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")
STOPWORDS = {
    "a",
    "al",
    "como",
    "con",
    "de",
    "del",
    "donde",
    "el",
    "en",
    "esta",
    "la",
    "las",
    "lo",
    "los",
    "muestrame",
    "para",
    "por",
    "que",
    "se",
    "su",
    "un",
    "una",
    "y",
}


@dataclass(frozen=True)
class RowScore:
    """Métricas por query para el scorer RAGAS offline."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    ok: bool
    score_eligible: bool
    skip_reason: str | None
    fallback_used: bool
    citations_count: int
    retrieved_contexts_count: int
    answer_chars: int
    answer_relevancy: float | None
    answer_correctness: float | None
    faithfulness: float | None
    context_precision: float | None
    context_recall: float | None
    context_entity_recall: float | None


@dataclass(frozen=True)
class ResolvedRagasRuntime:
    """Configuración resuelta del runtime para ejecutar scoring real."""

    provider: str
    llm_model: str
    embedding_model: str
    openai_api_key: str
    vertex_project_id: str
    vertex_location: str
    vertex_api_base_url: str
    vertex_api_version: str
    vertex_token_url: str
    vertex_credentials_b64: str
    engine_notes: list[str]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for RAGAS scoring."""
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Calcula métricas offline sobre el collector RAGAS.",
    )
    parser.add_argument("--collected-report", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "benchmark_reports",
    )
    parser.add_argument(
        "--hard-answer-relevancy",
        type=float,
        default=HARD_THRESHOLDS["answer_relevancy"],
    )
    parser.add_argument(
        "--hard-answer-correctness",
        type=float,
        default=HARD_THRESHOLDS["answer_correctness"],
    )
    parser.add_argument(
        "--hard-faithfulness",
        type=float,
        default=HARD_THRESHOLDS["faithfulness"],
    )
    parser.add_argument(
        "--hard-context-entity-recall",
        type=float,
        default=HARD_THRESHOLDS["context_entity_recall"],
    )
    parser.add_argument(
        "--min-scored-rate",
        type=float,
        default=HARD_THRESHOLDS["scored_rate"],
    )
    parser.add_argument(
        "--soft-context-precision",
        type=float,
        default=SOFT_THRESHOLDS["context_precision"],
    )
    parser.add_argument(
        "--soft-context-recall",
        type=float,
        default=SOFT_THRESHOLDS["context_recall"],
    )
    parser.add_argument(
        "--scoring-engine",
        choices=[AUTO_SCORING_ENGINE, "proxy", RAGAS_SCORING_ENGINE],
        default=AUTO_SCORING_ENGINE,
    )
    parser.add_argument(
        "--ragas-provider",
        choices=["openai", "vertexai"],
        default=None,
    )
    parser.add_argument("--ragas-llm-model", default=None)
    parser.add_argument("--ragas-embedding-model", default=None)
    parser.add_argument("--ragas-batch-size", type=int, default=4)
    return parser.parse_args()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]
    return [token for token in tokens if token not in STOPWORDS and len(token) > 1]


def _token_set(text: str) -> set[str]:
    return set(_tokenize(text))


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _f1_score(reference: set[str], answer: set[str]) -> float:
    if not reference and not answer:
        return 1.0
    if not reference or not answer:
        return 0.0
    overlap = len(reference & answer)
    precision = _safe_divide(overlap, len(answer))
    recall = _safe_divide(overlap, len(reference))
    if precision == 0.0 and recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _mean(values: list[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _contains_any(text: str, values: list[str]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(value) in normalized for value in values if value)


def _overlap_recall(reference: set[str], answer: set[str]) -> float:
    if not reference:
        return 1.0
    return _safe_divide(len(reference & answer), len(reference))


def _claim_coverage(claims: list[str], answer_text: str) -> float:
    if not claims:
        return 1.0
    answer_tokens = _token_set(answer_text)
    claim_scores: list[float] = []
    for claim in claims:
        claim_tokens = _token_set(claim)
        if not claim_tokens:
            continue
        claim_scores.append(_overlap_recall(claim_tokens, answer_tokens))
    return _mean(claim_scores) or 0.0


def _weighted_rank_precision(contexts: list[str], match_terms: list[str]) -> float:
    if not contexts:
        return 0.0
    weighted_hits = 0.0
    total_weight = 0.0
    for index, context in enumerate(contexts, start=1):
        weight = 1.0 / index
        total_weight += weight
        if _contains_any(context, match_terms):
            weighted_hits += weight
    return _safe_divide(weighted_hits, total_weight)


def _build_reference_contexts(row: dict[str, Any]) -> list[str]:
    contexts: list[str] = []
    raw_alternatives = row.get("materialized_alternatives")
    if not isinstance(raw_alternatives, list):
        raw_alternatives = []
    for target in [row.get("materialized_expected"), *raw_alternatives]:
        if not isinstance(target, dict):
            continue
        snippet = str(
            target.get("snippet_preview") or target.get("snippet") or ""
        ).strip()
        if snippet and snippet not in contexts:
            contexts.append(snippet)
    return contexts


def _build_row_sample(row: dict[str, Any]) -> dict[str, Any]:
    ragas_reference = row.get("ragas_reference")
    if not isinstance(ragas_reference, dict):
        ragas_reference = {}
    retrieved_contexts = row.get("retrieved_contexts")
    if not isinstance(retrieved_contexts, list):
        retrieved_contexts = []

    normalized_contexts: list[str] = []
    for item in retrieved_contexts:
        if not isinstance(item, dict):
            continue
        context_text = str(item.get("text") or "").strip()
        context_path = str(item.get("path") or "").strip()
        combined_parts = [part for part in [context_path, context_text] if part]
        combined_text = "\n".join(combined_parts).strip()
        if combined_text:
            normalized_contexts.append(combined_text)

    return {
        "query_id": str(row.get("query_id") or row.get("id") or ""),
        "query": str(row.get("query") or ""),
        "cohort": str(row.get("cohort") or "unknown"),
        "gate_candidate": bool(row.get("gate_candidate", False)),
        "ok": bool(row.get("ok", False)),
        "score_eligible": bool(row.get("score_eligible", False)),
        "skip_reason": row.get("score_skip_reason"),
        "fallback_used": bool(row.get("fallback_used", False)),
        "citations_count": int(row.get("citations_count") or 0),
        "retrieved_contexts_count": int(row.get("retrieved_contexts_count") or 0),
        "answer_text": str(row.get("answer_text") or ""),
        "reference_answer": str(ragas_reference.get("reference_answer") or ""),
        "reference_entities": [
            str(item).strip()
            for item in (ragas_reference.get("reference_entities") or [])
            if str(item).strip()
        ],
        "reference_claims": [
            str(item).strip()
            for item in (ragas_reference.get("reference_claims") or [])
            if str(item).strip()
        ],
        "retrieved_contexts": normalized_contexts,
        "reference_contexts": _build_reference_contexts(row),
    }


def _build_skipped_row_score(sample: dict[str, Any]) -> RowScore:
    return RowScore(
        query_id=sample["query_id"],
        query=sample["query"],
        cohort=sample["cohort"],
        gate_candidate=sample["gate_candidate"],
        ok=sample["ok"],
        score_eligible=False,
        skip_reason=(
            str(sample["skip_reason"])
            if sample["skip_reason"] is not None
            else "not_eligible"
        ),
        fallback_used=sample["fallback_used"],
        citations_count=sample["citations_count"],
        retrieved_contexts_count=sample["retrieved_contexts_count"],
        answer_chars=len(sample["answer_text"]),
        answer_relevancy=None,
        answer_correctness=None,
        faithfulness=None,
        context_precision=None,
        context_recall=None,
        context_entity_recall=None,
    )


def score_row(row: dict[str, Any]) -> RowScore:
    """Compute row-level proxy metrics for a collected RAGAS row."""
    sample = _build_row_sample(row)
    if not sample["score_eligible"]:
        return _build_skipped_row_score(sample)

    query_tokens = _token_set(sample["query"])
    answer_tokens = _token_set(sample["answer_text"])
    reference_tokens = _token_set(sample["reference_answer"])
    retrieved_context_text = "\n".join(sample["retrieved_contexts"])
    retrieved_context_tokens = _token_set(retrieved_context_text)
    reference_context_tokens = _token_set("\n".join(sample["reference_contexts"]))

    answer_relevancy = _safe_divide(
        len(query_tokens & answer_tokens),
        max(1, len(query_tokens)),
    )
    entity_hits_in_answer = sum(
        1
        for entity in sample["reference_entities"]
        if _contains_any(sample["answer_text"], [entity])
    )
    entity_recall_in_answer = _safe_divide(
        entity_hits_in_answer,
        max(1, len(sample["reference_entities"])),
    )
    reference_recall = _overlap_recall(reference_tokens, answer_tokens)
    claim_coverage = _claim_coverage(
        sample["reference_claims"],
        sample["answer_text"],
    )
    answer_correctness = (
        0.15 * _f1_score(reference_tokens, answer_tokens)
        + 0.35 * reference_recall
        + 0.25 * claim_coverage
        + 0.25 * entity_recall_in_answer
    )
    answer_support = _safe_divide(
        len(answer_tokens & retrieved_context_tokens),
        max(1, len(answer_tokens)),
    )
    entity_support_in_context = _safe_divide(
        sum(
            1
            for entity in sample["reference_entities"]
            if _contains_any(retrieved_context_text, [entity])
        ),
        max(1, len(sample["reference_entities"])),
    )
    faithfulness = 0.6 * answer_support + 0.4 * entity_support_in_context

    context_hits = 0
    context_match_terms = sample["reference_entities"] or list(reference_tokens)
    for context in sample["retrieved_contexts"]:
        if _contains_any(context, context_match_terms):
            context_hits += 1
    context_precision = (
        0.4
        * _safe_divide(
            context_hits,
            max(1, len(sample["retrieved_contexts"])),
        )
        + 0.6
        * _weighted_rank_precision(
            sample["retrieved_contexts"],
            context_match_terms,
        )
    )
    context_recall = _safe_divide(
        len((reference_tokens | reference_context_tokens) & retrieved_context_tokens),
        max(1, len(reference_tokens | reference_context_tokens)),
    )
    context_entity_recall = _safe_divide(
        sum(
            1
            for entity in sample["reference_entities"]
            if _contains_any(retrieved_context_text, [entity])
        ),
        max(1, len(sample["reference_entities"])),
    )

    return RowScore(
        query_id=sample["query_id"],
        query=sample["query"],
        cohort=sample["cohort"],
        gate_candidate=sample["gate_candidate"],
        ok=sample["ok"],
        score_eligible=True,
        skip_reason=None,
        fallback_used=sample["fallback_used"],
        citations_count=sample["citations_count"],
        retrieved_contexts_count=sample["retrieved_contexts_count"],
        answer_chars=len(sample["answer_text"]),
        answer_relevancy=answer_relevancy,
        answer_correctness=answer_correctness,
        faithfulness=faithfulness,
        context_precision=context_precision,
        context_recall=context_recall,
        context_entity_recall=context_entity_recall,
    )


def _aggregate_rows(rows: list[RowScore]) -> dict[str, Any]:
    scored_rows = [row for row in rows if row.score_eligible]
    skipped_rows = [row for row in rows if not row.score_eligible]
    return {
        "queries_count": len(rows),
        "scored_queries": len(scored_rows),
        "skipped_queries": len(skipped_rows),
        "scored_rate": _safe_divide(len(scored_rows), max(1, len(rows))),
        "answer_relevancy": _mean([row.answer_relevancy for row in scored_rows]),
        "answer_correctness": _mean([row.answer_correctness for row in scored_rows]),
        "faithfulness": _mean([row.faithfulness for row in scored_rows]),
        "context_precision": _mean([row.context_precision for row in scored_rows]),
        "context_recall": _mean([row.context_recall for row in scored_rows]),
        "context_entity_recall": _mean(
            [row.context_entity_recall for row in scored_rows]
        ),
        "fallback_rate": _mean([1.0 if row.fallback_used else 0.0 for row in rows]),
    }


def _compare_metric(actual: float | None, threshold: float) -> bool:
    return actual is not None and actual >= threshold


def _register_vertexai_chat_shim() -> None:
    if "langchain_community.chat_models.vertexai" in sys.modules:
        return
    try:
        from langchain_google_vertexai import ChatVertexAI
    except ImportError:
        return

    shim = types.ModuleType("langchain_community.chat_models.vertexai")
    shim.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = shim


def _ensure_repo_src_on_path() -> None:
    repo_src = Path(__file__).resolve().parents[1] / "src"
    repo_src_str = str(repo_src)
    if repo_src_str not in sys.path:
        sys.path.insert(0, repo_src_str)


def get_settings() -> Any:
    """Carga Settings del runtime con una frontera parcheable para tests."""
    _ensure_repo_src_on_path()

    from coderag.core.settings import get_settings as runtime_get_settings

    return runtime_get_settings()


def _normalize_ragas_provider(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"vertexai", "vertex_ai"}:
        return "vertex"
    return normalized


def _resolve_ragas_runtime_settings(
    requested_provider: str | None,
    llm_model: str | None,
    embedding_model: str | None,
) -> ResolvedRagasRuntime:
    settings = get_settings()
    preferred_provider = _normalize_ragas_provider(requested_provider)
    if not preferred_provider:
        resolved_provider = _normalize_ragas_provider(settings.resolve_llm_provider())
        if resolved_provider == "vertex" and not settings.is_vertex_ai_configured():
            resolved_provider = ""
        if resolved_provider == "openai" and not settings.resolve_api_key("openai"):
            resolved_provider = ""
        if not resolved_provider:
            if settings.is_vertex_ai_configured():
                preferred_provider = "vertex"
            elif settings.resolve_api_key("openai"):
                preferred_provider = "openai"
            else:
                raise RuntimeError("ragas_provider_not_configured")
        else:
            preferred_provider = resolved_provider

    if preferred_provider == "openai":
        openai_api_key = settings.resolve_api_key("openai")
        if not openai_api_key:
            raise RuntimeError("ragas_provider_not_configured")
        return ResolvedRagasRuntime(
            provider="openai",
            llm_model=settings.resolve_answer_model("openai", override=llm_model),
            embedding_model=settings.resolve_embedding_model(
                "openai",
                override=embedding_model,
            ),
            openai_api_key=openai_api_key,
            vertex_project_id="",
            vertex_location="",
            vertex_api_base_url="",
            vertex_api_version="",
            vertex_token_url="",
            vertex_credentials_b64="",
            engine_notes=[],
        )

    if preferred_provider != "vertex":
        raise RuntimeError(f"ragas_provider_unsupported:{preferred_provider}")

    if not settings.is_vertex_ai_configured():
        raise RuntimeError(
            "ragas_vertex_not_configured:"
            f"{settings.vertex_ai_missing_reason()}"
        )

    return ResolvedRagasRuntime(
        provider="vertex",
        llm_model=settings.resolve_answer_model("vertex", override=llm_model),
        embedding_model=settings.resolve_embedding_model(
            "vertex",
            override=embedding_model,
        ),
        openai_api_key="",
        vertex_project_id=settings.resolve_vertex_project_id(),
        vertex_location=settings.resolve_vertex_location(),
        vertex_api_base_url=settings.resolve_vertex_api_base_url(),
        vertex_api_version=str(getattr(settings, "vertex_api_version", "v1")).strip()
        or "v1",
        vertex_token_url=str(getattr(settings, "vertex_auth_token_url", "")).strip(),
        vertex_credentials_b64=settings.resolve_vertex_credentials_reference(),
        engine_notes=["ragas_vertex_runtime_config"],
    )


def _build_ragas_vertex_backends(
    runtime: ResolvedRagasRuntime,
) -> tuple[Any, Any, list[str]]:
    _ensure_repo_src_on_path()

    from coderag.core.vertex_ai import build_vertex_service_account_credentials
    from google.genai import Client
    from langchain_google_vertexai import VertexAIEmbeddings
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms import llm_factory

    credentials = build_vertex_service_account_credentials(
        runtime.vertex_credentials_b64,
        token_url=runtime.vertex_token_url or None,
    )
    os.environ["GOOGLE_CLOUD_PROJECT"] = runtime.vertex_project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = runtime.vertex_location
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    google_client = Client(
        vertexai=True,
        credentials=credentials,
        project=runtime.vertex_project_id,
        location=runtime.vertex_location,
    )
    llm = llm_factory(
        model=runtime.llm_model,
        provider="google",
        client=google_client,
    )
    embeddings = LangchainEmbeddingsWrapper(
        VertexAIEmbeddings(
            model=runtime.embedding_model,
            project=runtime.vertex_project_id,
            location=runtime.vertex_location,
            credentials=credentials,
        )
    )
    return (
        llm,
        embeddings,
        [
            "ragas_vertex_runtime_adapter",
            "ragas_vertex_google_genai_client",
            "ragas_vertex_langchain_embeddings_wrapper",
        ],
    )


def _build_ragas_backends(
    runtime: ResolvedRagasRuntime,
) -> tuple[Any, Any, list[str]]:
    if runtime.provider == "vertex":
        return _build_ragas_vertex_backends(runtime)

    from openai import OpenAI
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory

    client = OpenAI(api_key=runtime.openai_api_key)
    llm = llm_factory(model=runtime.llm_model, provider=runtime.provider, client=client)
    embeddings = embedding_factory(
        provider=runtime.provider,
        model=runtime.embedding_model,
        client=client,
    )
    return llm, embeddings, []


def _coerce_metric_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_with_ragas(
    raw_rows: list[dict[str, Any]],
    *,
    provider: str,
    llm_model: str,
    embedding_model: str,
    batch_size: int,
) -> tuple[
    dict[str, Any],
    list[RowScore],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    _register_vertexai_chat_shim()

    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import (
        answer_correctness,
        answer_relevancy,
        context_entity_recall,
        context_precision,
        context_recall,
        faithfulness,
    )

    runtime = _resolve_ragas_runtime_settings(
        provider,
        llm_model,
        embedding_model,
    )
    llm, embeddings, backend_notes = _build_ragas_backends(runtime)

    metrics = []
    for metric_template in (
        answer_relevancy,
        answer_correctness,
        faithfulness,
        context_precision,
        context_recall,
        context_entity_recall,
    ):
        metric = copy.deepcopy(metric_template)
        if hasattr(metric, "llm"):
            metric.llm = llm
        if hasattr(metric, "embeddings"):
            metric.embeddings = embeddings
        metrics.append(metric)

    eligible_raw_rows: list[dict[str, Any]] = []
    skipped_rows: list[RowScore] = []
    samples: list[SingleTurnSample] = []
    for row in raw_rows:
        sample = _build_row_sample(row)
        if not sample["score_eligible"]:
            skipped_rows.append(_build_skipped_row_score(sample))
            continue
        eligible_raw_rows.append(row)
        samples.append(
            SingleTurnSample(
                user_input=sample["query"],
                retrieved_contexts=sample["retrieved_contexts"],
                reference_contexts=sample["reference_contexts"],
                response=sample["answer_text"],
                reference=sample["reference_answer"],
            )
        )

    if not samples:
        rows = skipped_rows
        overall = _aggregate_rows(rows)
        by_cohort: dict[str, Any] = {}
        metric_coverage = {
            "answer_relevancy": 0,
            "answer_correctness": 0,
            "faithfulness": 0,
            "context_precision": 0,
            "context_recall": 0,
            "context_entity_recall": 0,
        }
        return overall, rows, by_cohort, [], metric_coverage, {
            "scoring_engine": RAGAS_SCORING_ENGINE,
            "engine_notes": runtime.engine_notes + backend_notes + ["no_score_eligible_rows"],
            "ragas_provider": runtime.provider,
            "ragas_llm_model": runtime.llm_model,
            "ragas_embedding_model": runtime.embedding_model,
        }

    result = evaluate(
        dataset=EvaluationDataset(samples=samples, name="code_ragas_eval"),
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        show_progress=False,
        batch_size=batch_size,
        raise_exceptions=False,
    )
    dataframe = result.to_pandas()
    scored_rows: list[RowScore] = []
    ragas_records = dataframe.to_dict(orient="records")
    for source_row, record in zip(eligible_raw_rows, ragas_records, strict=True):
        scored_rows.append(
            RowScore(
                query_id=str(source_row.get("query_id") or source_row.get("id") or ""),
                query=str(source_row.get("query") or ""),
                cohort=str(source_row.get("cohort") or "unknown"),
                gate_candidate=bool(source_row.get("gate_candidate", False)),
                ok=bool(source_row.get("ok", False)),
                score_eligible=True,
                skip_reason=None,
                fallback_used=bool(source_row.get("fallback_used", False)),
                citations_count=int(source_row.get("citations_count") or 0),
                retrieved_contexts_count=int(source_row.get("retrieved_contexts_count") or 0),
                answer_chars=len(str(source_row.get("answer_text") or "")),
                answer_relevancy=_coerce_metric_value(record.get("answer_relevancy")),
                answer_correctness=_coerce_metric_value(record.get("answer_correctness")),
                faithfulness=_coerce_metric_value(record.get("faithfulness")),
                context_precision=_coerce_metric_value(record.get("context_precision")),
                context_recall=_coerce_metric_value(record.get("context_recall")),
                context_entity_recall=_coerce_metric_value(record.get("context_entity_recall")),
            )
        )

    rows = [*scored_rows, *skipped_rows]
    overall = _aggregate_rows(rows)
    gate_rows = [row for row in rows if row.gate_candidate]
    if gate_rows:
        overall["gate_candidate"] = _aggregate_rows(gate_rows)

    by_cohort: dict[str, Any] = {}
    for cohort in sorted({row.cohort for row in rows}):
        cohort_rows = [row for row in rows if row.cohort == cohort]
        by_cohort[cohort] = _aggregate_rows(cohort_rows)

    skipped_summary = [
        {
            "query_id": row.query_id,
            "query": row.query,
            "cohort": row.cohort,
            "skip_reason": row.skip_reason,
        }
        for row in rows
        if not row.score_eligible
    ]
    metric_coverage = {
        "answer_relevancy": overall["scored_queries"],
        "answer_correctness": overall["scored_queries"],
        "faithfulness": overall["scored_queries"],
        "context_precision": overall["scored_queries"],
        "context_recall": overall["scored_queries"],
        "context_entity_recall": overall["scored_queries"],
    }
    scoring_meta = {
        "scoring_engine": RAGAS_SCORING_ENGINE,
        "engine_notes": runtime.engine_notes + backend_notes,
        "ragas_provider": runtime.provider,
        "ragas_llm_model": runtime.llm_model,
        "ragas_embedding_model": runtime.embedding_model,
    }
    return overall, rows, by_cohort, skipped_summary, metric_coverage, scoring_meta


def build_gate(
    *,
    overall: dict[str, Any],
    hard_thresholds: dict[str, float],
    soft_thresholds: dict[str, float],
) -> dict[str, Any]:
    scope_metrics_raw = overall.get("gate_candidate")
    scope_name = "gate_candidate"
    if not isinstance(scope_metrics_raw, dict):
        scope_metrics_raw = overall
        scope_name = "overall"

    hard_results: dict[str, Any] = {}
    failed_hard: list[str] = []
    for metric_name, threshold in hard_thresholds.items():
        actual = scope_metrics_raw.get(metric_name)
        passed = _compare_metric(actual, threshold)
        hard_results[metric_name] = {
            "actual": actual,
            "threshold": threshold,
            "operator": ">=",
            "passed": passed,
        }
        if not passed:
            failed_hard.append(metric_name)

    soft_results: dict[str, Any] = {}
    failed_soft: list[str] = []
    for metric_name, threshold in soft_thresholds.items():
        actual = scope_metrics_raw.get(metric_name)
        passed = _compare_metric(actual, threshold)
        soft_results[metric_name] = {
            "actual": actual,
            "threshold": threshold,
            "operator": ">=",
            "passed": passed,
        }
        if not passed:
            failed_soft.append(metric_name)

    if failed_hard:
        status = "fail"
    elif failed_soft:
        status = "pass_with_warnings"
    else:
        status = "pass"
    return {
        "status": status,
        "scope": scope_name,
        "queries_count": scope_metrics_raw.get("queries_count"),
        "hard_thresholds": hard_results,
        "soft_thresholds": soft_results,
        "failed_hard_metrics": failed_hard,
        "failed_soft_metrics": failed_soft,
    }


def score_collected_report(
    collected_report: dict[str, Any],
) -> tuple[dict[str, Any], list[RowScore], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Score a collected RAGAS report with the local proxy scorer."""
    raw_rows = collected_report.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("collected_report debe incluir lista no vacia en 'rows'")
    rows = [score_row(item) for item in raw_rows if isinstance(item, dict)]
    if not rows:
        raise ValueError("collected_report no contiene filas validas")

    overall = _aggregate_rows(rows)
    gate_rows = [row for row in rows if row.gate_candidate]
    if gate_rows:
        overall["gate_candidate"] = _aggregate_rows(gate_rows)

    by_cohort: dict[str, Any] = {}
    for cohort in sorted({row.cohort for row in rows}):
        cohort_rows = [row for row in rows if row.cohort == cohort]
        by_cohort[cohort] = _aggregate_rows(cohort_rows)

    skipped_rows = [
        {
            "query_id": row.query_id,
            "query": row.query,
            "cohort": row.cohort,
            "skip_reason": row.skip_reason,
        }
        for row in rows
        if not row.score_eligible
    ]
    metric_coverage = {
        "answer_relevancy": overall["scored_queries"],
        "answer_correctness": overall["scored_queries"],
        "faithfulness": overall["scored_queries"],
        "context_precision": overall["scored_queries"],
        "context_recall": overall["scored_queries"],
        "context_entity_recall": overall["scored_queries"],
    }
    return overall, rows, by_cohort, skipped_rows, metric_coverage


def score_collected_report_with_engine(
    collected_report: dict[str, Any],
    *,
    scoring_engine: str,
    ragas_provider: str | None,
    ragas_llm_model: str | None,
    ragas_embedding_model: str | None,
    ragas_batch_size: int,
) -> tuple[
    dict[str, Any],
    list[RowScore],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
]:
    raw_rows = collected_report.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("collected_report debe incluir lista no vacia en 'rows'")

    if scoring_engine == "proxy":
        overall, rows, by_cohort, skipped_rows, metric_coverage = (
            score_collected_report(collected_report)
        )
        return overall, rows, by_cohort, skipped_rows, metric_coverage, {
            "scoring_engine": PROXY_SCORING_ENGINE,
            "engine_notes": [],
        }

    if scoring_engine in {AUTO_SCORING_ENGINE, RAGAS_SCORING_ENGINE}:
        try:
            return _score_with_ragas(
                [row for row in raw_rows if isinstance(row, dict)],
                provider=ragas_provider or "",
                llm_model=ragas_llm_model or "",
                embedding_model=ragas_embedding_model or "",
                batch_size=ragas_batch_size,
            )
        except Exception as exc:
            if scoring_engine == RAGAS_SCORING_ENGINE:
                raise
            overall, rows, by_cohort, skipped_rows, metric_coverage = (
                score_collected_report(collected_report)
            )
            return overall, rows, by_cohort, skipped_rows, metric_coverage, {
                "scoring_engine": PROXY_SCORING_ENGINE,
                "engine_notes": [
                    f"ragas_fallback:{exc.__class__.__name__}:{exc}"
                ],
            }

    raise ValueError(f"scoring_engine desconocido: {scoring_engine}")


def write_reports(
    *,
    output_dir: Path,
    collected_report_path: Path,
    collected_meta: dict[str, Any],
    overall: dict[str, Any],
    rows: list[RowScore],
    by_cohort: dict[str, Any],
    skipped_rows: list[dict[str, Any]],
    metric_coverage: dict[str, Any],
    gate: dict[str, Any],
    scoring_meta: dict[str, Any],
) -> tuple[Path, Path]:
    """Write JSON and CSV artifacts for offline RAGAS scoring."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"code_ragas_eval_{timestamp}.json"
    csv_path = output_dir / f"code_ragas_eval_{timestamp}.csv"

    json_payload = {
        "meta": {
            "collected_report": str(collected_report_path),
            "source_meta": collected_meta,
            **scoring_meta,
        },
        "overall": overall,
        "gate": gate,
        "metric_coverage": metric_coverage,
        "by_cohort": by_cohort,
        "rows": [row.__dict__ for row in rows],
        "skipped_rows": skipped_rows,
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(RowScore.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
    return json_path, csv_path


def main() -> int:
    """CLI entrypoint for offline RAGAS scoring."""
    args = parse_args()
    collected_report = json.loads(args.collected_report.read_text(encoding="utf-8"))
    collected_meta = (
        collected_report.get("meta")
        if isinstance(collected_report.get("meta"), dict)
        else {}
    )
    overall, rows, by_cohort, skipped_rows, metric_coverage, scoring_meta = (
        score_collected_report_with_engine(
            collected_report,
            scoring_engine=args.scoring_engine,
            ragas_provider=args.ragas_provider,
            ragas_llm_model=args.ragas_llm_model,
            ragas_embedding_model=args.ragas_embedding_model,
            ragas_batch_size=args.ragas_batch_size,
        )
    )
    gate = build_gate(
        overall=overall,
        hard_thresholds={
            "answer_relevancy": args.hard_answer_relevancy,
            "answer_correctness": args.hard_answer_correctness,
            "faithfulness": args.hard_faithfulness,
            "context_entity_recall": args.hard_context_entity_recall,
            "scored_rate": args.min_scored_rate,
        },
        soft_thresholds={
            "context_precision": args.soft_context_precision,
            "context_recall": args.soft_context_recall,
        },
    )
    json_path, csv_path = write_reports(
        output_dir=args.output_dir,
        collected_report_path=args.collected_report,
        collected_meta=collected_meta,
        overall=overall,
        rows=rows,
        by_cohort=by_cohort,
        skipped_rows=skipped_rows,
        metric_coverage=metric_coverage,
        gate=gate,
        scoring_meta=scoring_meta,
    )

    print("Code RAGAS evaluation completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"scoring_engine={scoring_meta['scoring_engine']}")
    if overall["answer_correctness"] is not None:
        print(f"answer_correctness={overall['answer_correctness']:.4f}")
    if overall["faithfulness"] is not None:
        print(f"faithfulness={overall['faithfulness']:.4f}")
    print(f"gate_status={gate['status']}")
    return 0 if gate["status"] != "fail" else 3


if __name__ == "__main__":
    raise SystemExit(main())