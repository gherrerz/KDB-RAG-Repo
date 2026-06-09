"""Calcula métricas IR sobre el artefacto de code retrieval colectado."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any


HARD_THRESHOLDS: dict[str, float] = {
    "exact_path_hit_at_1": 0.80,
    "exact_line_hit_at_1": 0.70,
    "mrr": 0.86,
    "fallback_rate": 0.05,
}

SOFT_THRESHOLDS: dict[str, float] = {
    "exact_path_hit_at_3": 0.92,
    "ndcg_5": 0.90,
    "citation_path_precision_mean": 0.85,
}


@dataclass(frozen=True)
class Target:
    """Target aceptable del gold set para una consulta."""

    path: str
    start_line: int | None
    end_line: int | None
    symbol_name: str | None
    kind: str | None
    whole_file: bool


@dataclass(frozen=True)
class RowScore:
    """Métricas IR por consulta retrieval-only."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    ok: bool
    chunks_count: int
    citations_count: int
    exact_path_hit_at_1: float
    exact_path_hit_at_3: float
    exact_line_hit_at_1: float | None
    exact_symbol_hit_at_1: float | None
    line_span_iou_at_1: float | None
    reciprocal_rank: float
    ndcg_5: float
    citation_path_precision: float
    citation_span_recall: float | None
    fallback_used: float


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for IR scoring."""
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Calcula métricas IR sobre el collector de code retrieval.",
    )
    parser.add_argument("--collected-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=repo_root / "benchmark_reports")
    parser.add_argument("--hard-exact-path-hit-at-1", type=float, default=HARD_THRESHOLDS["exact_path_hit_at_1"])
    parser.add_argument("--hard-exact-line-hit-at-1", type=float, default=HARD_THRESHOLDS["exact_line_hit_at_1"])
    parser.add_argument("--hard-mrr", type=float, default=HARD_THRESHOLDS["mrr"])
    parser.add_argument("--max-fallback-rate", type=float, default=HARD_THRESHOLDS["fallback_rate"])
    parser.add_argument("--soft-exact-path-hit-at-3", type=float, default=SOFT_THRESHOLDS["exact_path_hit_at_3"])
    parser.add_argument("--soft-ndcg-5", type=float, default=SOFT_THRESHOLDS["ndcg_5"])
    parser.add_argument(
        "--soft-citation-path-precision-mean",
        type=float,
        default=SOFT_THRESHOLDS["citation_path_precision_mean"],
    )
    return parser.parse_args()


def _normalize_path(value: str | None) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def _build_targets(row: dict[str, Any]) -> list[Target]:
    targets: list[Target] = []
    raw_targets = [row.get("materialized_expected")] + list(row.get("materialized_alternatives") or [])
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        start_line = item.get("start_line")
        end_line = item.get("end_line")
        targets.append(
            Target(
                path=path,
                start_line=int(start_line) if start_line is not None else None,
                end_line=int(end_line) if end_line is not None else None,
                symbol_name=(
                    str(item.get("symbol_name"))
                    if item.get("symbol_name") is not None
                    else None
                ),
                kind=(str(item.get("kind")) if item.get("kind") is not None else None),
                whole_file=(start_line is None and end_line is None),
            )
        )
    return targets


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b) + 1)


def _span_iou(chunk: dict[str, Any], target: Target) -> float | None:
    if target.whole_file:
        return None
    chunk_start = chunk.get("start_line")
    chunk_end = chunk.get("end_line")
    if chunk_start is None or chunk_end is None or target.start_line is None or target.end_line is None:
        return None
    chunk_start = int(chunk_start)
    chunk_end = int(chunk_end)
    overlap = _overlap(chunk_start, chunk_end, target.start_line, target.end_line)
    if overlap <= 0:
        return 0.0
    union = (chunk_end - chunk_start + 1) + (target.end_line - target.start_line + 1) - overlap
    return overlap / union if union > 0 else 0.0


def _path_match(chunk_path: str | None, target: Target) -> bool:
    return _normalize_path(chunk_path) == _normalize_path(target.path)


def _line_match(chunk: dict[str, Any], target: Target) -> bool:
    if target.whole_file:
        return _path_match(chunk.get("path"), target)
    chunk_start = chunk.get("start_line")
    chunk_end = chunk.get("end_line")
    if chunk_start is None or chunk_end is None or target.start_line is None or target.end_line is None:
        return False
    return _path_match(chunk.get("path"), target) and _overlap(
        int(chunk_start),
        int(chunk_end),
        target.start_line,
        target.end_line,
    ) > 0


def _symbol_match(chunk: dict[str, Any], target: Target) -> bool:
    if not target.symbol_name or not _path_match(chunk.get("path"), target):
        return False
    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    symbol_name = metadata.get("symbol_name")
    return str(symbol_name or "").strip() == target.symbol_name


def _chunk_relevance(chunk: dict[str, Any], targets: list[Target]) -> float:
    best = 0.0
    for target in targets:
        if not _path_match(chunk.get("path"), target):
            continue
        if target.whole_file:
            best = max(best, 2.0)
            continue
        iou = _span_iou(chunk, target)
        if iou is None:
            best = max(best, 1.0)
        elif iou > 0:
            best = max(best, 2.0)
        else:
            best = max(best, 1.0)
    return best


def _reciprocal_rank(chunks: list[dict[str, Any]], targets: list[Target]) -> float:
    for index, chunk in enumerate(chunks, start=1):
        if _chunk_relevance(chunk, targets) > 0:
            return 1.0 / index
    return 0.0


def _ndcg_at_5(chunks: list[dict[str, Any]], targets: list[Target]) -> float:
    gains = [_chunk_relevance(chunk, targets) for chunk in chunks[:5]]
    if not gains:
        return 0.0
    dcg = sum(((2**gain) - 1.0) / math.log2(index + 2) for index, gain in enumerate(gains))
    relevant_targets = min(5, max(1, len(targets)))
    ideal_gains = [2.0] * relevant_targets + [0.0] * max(0, 5 - relevant_targets)
    idcg = sum(((2**gain) - 1.0) / math.log2(index + 2) for index, gain in enumerate(ideal_gains))
    return dcg / idcg if idcg > 0 else 0.0


def _citation_path_precision(citations: list[dict[str, Any]], targets: list[Target]) -> float:
    if not citations:
        return 0.0
    matched = 0
    for citation in citations:
        if any(_path_match(citation.get("path"), target) for target in targets):
            matched += 1
    return matched / len(citations)


def _citation_span_recall(citations: list[dict[str, Any]], targets: list[Target]) -> float | None:
    span_targets = [target for target in targets if not target.whole_file]
    if not span_targets:
        return None
    matched_targets = 0
    for target in span_targets:
        if any(_line_match(citation, target) for citation in citations):
            matched_targets += 1
    return matched_targets / len(span_targets)


def score_row(row: dict[str, Any]) -> RowScore:
    """Compute row-level IR metrics from a collected retrieval result."""
    response_body = row.get("response_body")
    if not isinstance(response_body, dict):
        response_body = {}
    chunks = response_body.get("chunks") if isinstance(response_body.get("chunks"), list) else []
    citations = response_body.get("citations") if isinstance(response_body.get("citations"), list) else []
    diagnostics = response_body.get("diagnostics") if isinstance(response_body.get("diagnostics"), dict) else {}
    targets = _build_targets(row)
    top1 = chunks[0] if chunks else {}
    if not isinstance(top1, dict):
        top1 = {}

    exact_path_hit_at_1 = 1.0 if any(_path_match(top1.get("path"), target) for target in targets) else 0.0
    exact_path_hit_at_3 = 1.0 if any(
        any(_path_match(chunk.get("path"), target) for target in targets)
        for chunk in chunks[:3]
        if isinstance(chunk, dict)
    ) else 0.0

    line_targets = [target for target in targets if not target.whole_file]
    symbol_targets = [target for target in targets if target.symbol_name]
    exact_line_hit_at_1 = (
        1.0 if any(_line_match(top1, target) for target in line_targets) else 0.0
    ) if line_targets else None
    exact_symbol_hit_at_1 = (
        1.0 if any(_symbol_match(top1, target) for target in symbol_targets) else 0.0
    ) if symbol_targets else None
    line_span_iou_at_1 = None
    if line_targets:
        line_span_iou_at_1 = max((_span_iou(top1, target) or 0.0) for target in line_targets)

    citation_path_precision = _citation_path_precision(
        [item for item in citations if isinstance(item, dict)],
        targets,
    )
    citation_span_recall = _citation_span_recall(
        [item for item in citations if isinstance(item, dict)],
        targets,
    )

    return RowScore(
        query_id=str(row.get("query_id") or row.get("id") or ""),
        query=str(row.get("query") or ""),
        cohort=str(row.get("cohort") or "unknown"),
        gate_candidate=bool(row.get("gate_candidate", False)),
        ok=bool(row.get("ok", False)),
        chunks_count=len(chunks),
        citations_count=len(citations),
        exact_path_hit_at_1=exact_path_hit_at_1,
        exact_path_hit_at_3=exact_path_hit_at_3,
        exact_line_hit_at_1=exact_line_hit_at_1,
        exact_symbol_hit_at_1=exact_symbol_hit_at_1,
        line_span_iou_at_1=line_span_iou_at_1,
        reciprocal_rank=_reciprocal_rank([item for item in chunks if isinstance(item, dict)], targets),
        ndcg_5=_ndcg_at_5([item for item in chunks if isinstance(item, dict)], targets),
        citation_path_precision=citation_path_precision,
        citation_span_recall=citation_span_recall,
        fallback_used=1.0 if bool(diagnostics.get("fallback_used", False)) else 0.0,
    )


def _mean(values: list[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _aggregate_rows(rows: list[RowScore]) -> dict[str, Any]:
    return {
        "queries_count": len(rows),
        "exact_path_hit_at_1": _mean([row.exact_path_hit_at_1 for row in rows]),
        "exact_path_hit_at_3": _mean([row.exact_path_hit_at_3 for row in rows]),
        "exact_line_hit_at_1": _mean([row.exact_line_hit_at_1 for row in rows]),
        "exact_symbol_hit_at_1": _mean([row.exact_symbol_hit_at_1 for row in rows]),
        "line_span_iou_at_1": _mean([row.line_span_iou_at_1 for row in rows]),
        "mrr": _mean([row.reciprocal_rank for row in rows]),
        "ndcg_5": _mean([row.ndcg_5 for row in rows]),
        "citation_path_precision_mean": _mean([row.citation_path_precision for row in rows]),
        "citation_span_recall_mean": _mean([row.citation_span_recall for row in rows]),
        "fallback_rate": _mean([row.fallback_used for row in rows]),
    }


def _compare_metric(metric_name: str, actual: float | None, threshold: float) -> bool:
    """Compare a metric against its threshold, using <= only for fallback_rate."""
    if actual is None:
        return False
    if metric_name == "fallback_rate":
        return actual <= threshold
    return actual >= threshold


def build_gate(
    *,
    overall: dict[str, Any],
    hard_thresholds: dict[str, float],
    soft_thresholds: dict[str, float],
) -> dict[str, Any]:
    """Evaluate hard and soft IR thresholds over the gate_candidate slice."""
    scope_metrics_raw = overall.get("gate_candidate")
    scope_name = "gate_candidate"
    if not isinstance(scope_metrics_raw, dict):
        scope_metrics_raw = overall
        scope_name = "overall"

    hard_results: dict[str, Any] = {}
    failed_hard: list[str] = []
    for metric_name, threshold in hard_thresholds.items():
        actual = scope_metrics_raw.get(metric_name)
        passed = _compare_metric(metric_name, actual, threshold)
        hard_results[metric_name] = {
            "actual": actual,
            "threshold": threshold,
            "operator": "<=" if metric_name == "fallback_rate" else ">=",
            "passed": passed,
        }
        if not passed:
            failed_hard.append(metric_name)

    soft_results: dict[str, Any] = {}
    failed_soft: list[str] = []
    for metric_name, threshold in soft_thresholds.items():
        actual = scope_metrics_raw.get(metric_name)
        passed = _compare_metric(metric_name, actual, threshold)
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


def score_collected_report(collected_report: dict[str, Any]) -> tuple[dict[str, Any], list[RowScore], dict[str, Any]]:
    """Score a collected retrieval report and return overall plus cohort metrics."""
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
    return overall, rows, by_cohort


def write_reports(
    *,
    output_dir: Path,
    collected_report_path: Path,
    collected_meta: dict[str, Any],
    overall: dict[str, Any],
    rows: list[RowScore],
    by_cohort: dict[str, Any],
    gate: dict[str, Any],
) -> tuple[Path, Path]:
    """Write JSON and CSV artifacts for IR scoring."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"code_ir_eval_{timestamp}.json"
    csv_path = output_dir / f"code_ir_eval_{timestamp}.csv"

    json_payload = {
        "meta": {
            "collected_report": str(collected_report_path),
            "source_meta": collected_meta,
        },
        "overall": overall,
        "gate": gate,
        "by_cohort": by_cohort,
        "rows": [row.__dict__ for row in rows],
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
    """CLI entrypoint for IR scoring."""
    args = parse_args()
    collected_report = json.loads(args.collected_report.read_text(encoding="utf-8"))
    collected_meta = collected_report.get("meta") if isinstance(collected_report.get("meta"), dict) else {}
    overall, rows, by_cohort = score_collected_report(collected_report)
    gate = build_gate(
        overall=overall,
        hard_thresholds={
            "exact_path_hit_at_1": args.hard_exact_path_hit_at_1,
            "exact_line_hit_at_1": args.hard_exact_line_hit_at_1,
            "mrr": args.hard_mrr,
            "fallback_rate": args.max_fallback_rate,
        },
        soft_thresholds={
            "exact_path_hit_at_3": args.soft_exact_path_hit_at_3,
            "ndcg_5": args.soft_ndcg_5,
            "citation_path_precision_mean": args.soft_citation_path_precision_mean,
        },
    )
    json_path, csv_path = write_reports(
        output_dir=args.output_dir,
        collected_report_path=args.collected_report,
        collected_meta=collected_meta,
        overall=overall,
        rows=rows,
        by_cohort=by_cohort,
        gate=gate,
    )

    print("Code IR evaluation completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"exact_path_hit_at_1={overall['exact_path_hit_at_1']:.4f}")
    print(f"mrr={overall['mrr']:.4f}")
    print(f"gate_status={gate['status']}")
    if gate["status"] == "fail":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())