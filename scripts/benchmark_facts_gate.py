"""Evalua gate factual ON/OFF y revisión humana para staging."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parsea argumentos de entrada para decisión de gate factual."""
    parser = argparse.ArgumentParser(
        description="Decision de gate factual para rollout de staging.",
    )
    parser.add_argument("--on-report", required=True)
    parser.add_argument("--off-report", required=True)
    parser.add_argument("--review-csv", default="")
    parser.add_argument("--min-uplift", type=float, default=0.15)
    parser.add_argument("--min-reviewed-ratio", type=float, default=0.90)
    parser.add_argument("--min-correct-ratio", type=float, default=0.85)
    parser.add_argument("--output-dir", default="benchmark_reports")
    return parser.parse_args()


def load_score(report_path: Path) -> tuple[float, dict[str, Any]]:
    """Carga score factual desde reporte JSON de benchmark."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise ValueError(f"Reporte invalido (meta): {report_path}")
    score = float(meta.get("architecture_fact_coverage_score") or 0.0)
    return score, payload


def load_review_metrics(review_csv: Path) -> dict[str, float] | None:
    """Carga métricas de revisión humana desde CSV, si fue provisto."""
    if not review_csv.exists() or not review_csv.is_file():
        return None

    rows = list(csv.DictReader(review_csv.read_text(encoding="utf-8").splitlines()))
    if not rows:
        return None

    reviewed = [r for r in rows if str(r.get("is_correct") or "").strip()]
    reviewed_ratio = len(reviewed) / len(rows)

    true_values = {"true", "1", "yes", "si", "sí"}
    correct = [
        r
        for r in reviewed
        if str(r.get("is_correct") or "").strip().lower() in true_values
    ]
    correct_ratio = (len(correct) / len(reviewed)) if reviewed else 0.0

    return {
        "rows_total": float(len(rows)),
        "rows_reviewed": float(len(reviewed)),
        "reviewed_ratio": round(reviewed_ratio, 4),
        "correct_ratio": round(correct_ratio, 4),
    }


def build_decision(
    *,
    on_score: float,
    off_score: float,
    review_metrics: dict[str, float] | None,
    min_uplift: float,
    min_reviewed_ratio: float,
    min_correct_ratio: float,
) -> tuple[str, dict[str, Any]]:
    """Calcula estado pass/pending_review/fail según thresholds configurados."""
    uplift_abs = on_score - off_score
    uplift_rel = (uplift_abs / off_score) if off_score > 0 else 0.0
    details: dict[str, Any] = {
        "on_score": round(on_score, 4),
        "off_score": round(off_score, 4),
        "uplift_absolute": round(uplift_abs, 4),
        "uplift_relative": round(uplift_rel, 4),
        "min_uplift_relative": round(min_uplift, 4),
        "review": review_metrics,
        "min_reviewed_ratio": round(min_reviewed_ratio, 4),
        "min_correct_ratio": round(min_correct_ratio, 4),
    }

    if uplift_rel < min_uplift:
        return "fail", details

    if review_metrics is None:
        return "pending_review", details

    if review_metrics["reviewed_ratio"] < min_reviewed_ratio:
        return "pending_review", details

    if review_metrics["correct_ratio"] < min_correct_ratio:
        return "fail", details

    return "pass", details


def write_decision(*, output_dir: Path, status: str, details: dict[str, Any]) -> Path:
    """Escribe artefacto JSON con la decisión de gate factual."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"facts_gate_decision_{timestamp}.json"
    payload = {
        "status": status,
        "details": details,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    """Ejecuta la evaluación de gate factual y retorna código de salida."""
    args = parse_args()
    on_score, _on_payload = load_score(Path(args.on_report))
    off_score, _off_payload = load_score(Path(args.off_report))
    review_metrics = (
        load_review_metrics(Path(args.review_csv)) if args.review_csv else None
    )

    status, details = build_decision(
        on_score=on_score,
        off_score=off_score,
        review_metrics=review_metrics,
        min_uplift=args.min_uplift,
        min_reviewed_ratio=args.min_reviewed_ratio,
        min_correct_ratio=args.min_correct_ratio,
    )
    out_path = write_decision(
        output_dir=Path(args.output_dir),
        status=status,
        details=details,
    )

    print("Facts gate decision completed")
    print(f"JSON: {out_path}")
    print(f"status={status}")
    print(f"uplift_relative={details['uplift_relative']:.4f}")

    if status == "pass":
        return 0
    if status == "pending_review":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
