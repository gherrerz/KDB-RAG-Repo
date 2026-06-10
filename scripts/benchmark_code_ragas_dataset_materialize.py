"""Materializa el dataset base de RAGAS a partir del gold y su resolución local."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the RAGAS dataset materialization step."""
    repo_root = Path(__file__).resolve().parents[1]
    default_gold = repo_root / "scripts" / "benchmark_data" / "code_retrieval_gold.json"
    default_materialized = repo_root / "benchmark_reports" / "code_gold_materialized.json"
    default_output = (
        repo_root / "benchmark_reports" / "code_ragas_dataset_materialized.json"
    )
    parser = argparse.ArgumentParser(
        description="Materializa el dataset base para evaluación RAGAS offline.",
    )
    parser.add_argument(
        "--gold-file",
        type=Path,
        default=default_gold,
        help="Ruta al gold set fuente.",
    )
    parser.add_argument(
        "--materialized-file",
        type=Path,
        default=default_materialized,
        help="Ruta al gold set ya materializado contra el workspace.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Ruta del JSON RAGAS materializado.",
    )
    return parser.parse_args()


def _load_json(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_dataset_name(dataset_name: str) -> str:
    normalized = dataset_name.strip() or "code_retrieval_gold"
    if normalized.startswith("code_retrieval"):
        return normalized.replace("code_retrieval", "code_ragas_dataset", 1)
    return f"{normalized}_ragas"


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _default_answer_type(entry: dict[str, Any]) -> str:
    cohort = str(entry.get("cohort") or "").strip().lower()
    expected_kind = str(entry.get("expected_kind") or "").strip().lower()
    if cohort == "literal_file" or expected_kind == "file":
        return "extractive"
    if cohort in {"exact_symbol", "exact_config", "graph_first_small"}:
        return "explanatory"
    return "mixed"


def _default_eval_mode(entry: dict[str, Any]) -> str:
    cohort = str(entry.get("cohort") or "").strip().lower()
    if cohort in {"exact_symbol", "literal_file"}:
        return "retrieval_grounded"
    return "hybrid"


def _default_ragas_enabled(entry: dict[str, Any]) -> bool:
    cohort = str(entry.get("cohort") or "").strip().lower()
    if cohort == "literal_file":
        return False
    return bool(str(entry.get("reference_answer") or "").strip())


def _append_context_hint(
    hints: list[dict[str, str]],
    seen: set[tuple[str, str]],
    hint_type: str,
    value: str,
) -> None:
    normalized_value = value.strip()
    if not normalized_value:
        return
    key = (hint_type, normalized_value)
    if key in seen:
        return
    seen.add(key)
    hints.append({"type": hint_type, "value": normalized_value})


def _derive_reference_context_hints(
    entry: dict[str, Any],
    materialized_expected: dict[str, Any],
    materialized_alternatives: list[dict[str, Any]],
) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for target in [materialized_expected, *materialized_alternatives]:
        if not isinstance(target, dict):
            continue
        _append_context_hint(hints, seen, "path", str(target.get("path") or ""))
        _append_context_hint(
            hints,
            seen,
            "symbol",
            str(target.get("symbol_name") or ""),
        )
        _append_context_hint(hints, seen, "kind", str(target.get("kind") or ""))

    for hint in _coerce_str_list(entry.get("reference_context_hints")):
        _append_context_hint(hints, seen, "hint", hint)
    return hints


def _derive_reference_entities(
    entry: dict[str, Any],
    materialized_expected: dict[str, Any],
    materialized_alternatives: list[dict[str, Any]],
) -> list[str]:
    explicit_entities = _coerce_str_list(entry.get("reference_entities"))
    if explicit_entities:
        return explicit_entities

    entities: list[str] = []
    for candidate in [
        str(entry.get("symbol_name") or "").strip(),
        str(entry.get("expected_path") or "").strip(),
    ]:
        if candidate and candidate not in entities:
            entities.append(candidate)
    for target in [materialized_expected, *materialized_alternatives]:
        if not isinstance(target, dict):
            continue
        for key in ("symbol_name", "path"):
            value = str(target.get(key) or "").strip()
            if value and value not in entities:
                entities.append(value)
    return entities


def _normalize_reference_claims(entry: dict[str, Any]) -> list[str]:
    explicit_claims = _coerce_str_list(entry.get("reference_claims"))
    if explicit_claims:
        return explicit_claims

    reference_answer = str(entry.get("reference_answer") or "").strip()
    if not reference_answer:
        return []
    parts = [part.strip() for part in re.split(r"[.;]\s+", reference_answer) if part.strip()]
    return parts[:3]


def _build_ragas_reference(
    entry: dict[str, Any],
    materialized_expected: dict[str, Any],
    materialized_alternatives: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "reference_answer": str(entry.get("reference_answer") or "").strip(),
        "answer_type": str(entry.get("answer_type") or "").strip()
        or _default_answer_type(entry),
        "eval_mode": str(entry.get("eval_mode") or "").strip()
        or _default_eval_mode(entry),
        "requires_citations": bool(entry.get("requires_citations", True)),
        "reference_context_hints": _derive_reference_context_hints(
            entry,
            materialized_expected,
            materialized_alternatives,
        ),
        "reference_claims": _normalize_reference_claims(entry),
        "reference_entities": _derive_reference_entities(
            entry,
            materialized_expected,
            materialized_alternatives,
        ),
    }


def _build_eligibility(
    entry: dict[str, Any],
    materialized_entry: dict[str, Any],
    ragas_reference: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    if not bool(materialized_entry.get("valid", False)):
        reasons.append("materialized_query_invalid")
    if not ragas_reference["reference_answer"]:
        reasons.append("missing_reference_answer")
    if not _default_ragas_enabled(entry):
        reasons.append("cohort_disabled_by_default")

    return (
        {
            "valid": bool(materialized_entry.get("valid", False)),
            "ragas_enabled": not reasons,
            "disabled_reasons": reasons,
        },
        reasons,
    )


def materialize_ragas_dataset(
    gold_data: dict[str, Any],
    materialized_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge gold and materialized artifacts into a RAGAS-ready dataset."""
    raw_queries = gold_data.get("queries")
    materialized_queries = materialized_data.get("queries")
    if not isinstance(raw_queries, list) or not isinstance(materialized_queries, list):
        raise ValueError("gold_data y materialized_data deben incluir lista 'queries'")

    materialized_by_id = {
        str(item.get("id") or "").strip(): item
        for item in materialized_queries
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    rows: list[dict[str, Any]] = []
    for entry in raw_queries:
        if not isinstance(entry, dict):
            continue
        query_id = str(entry.get("id") or "").strip()
        materialized_entry = materialized_by_id.get(query_id)
        if materialized_entry is None:
            rows.append(
                {
                    **entry,
                    "retrieval_defaults": dict(materialized_data.get("defaults") or {}),
                    "materialized_expected": None,
                    "materialized_alternatives": [],
                    "ragas_reference": _build_ragas_reference(entry, {}, []),
                    "eligibility": {
                        "valid": False,
                        "ragas_enabled": False,
                        "disabled_reasons": ["missing_materialized_query"],
                    },
                    "validation_errors": ["missing_materialized_query"],
                }
            )
            continue

        materialized_expected = dict(materialized_entry.get("materialized_expected") or {})
        materialized_alternatives = [
            item
            for item in (materialized_entry.get("materialized_alternatives") or [])
            if isinstance(item, dict)
        ]
        ragas_reference = _build_ragas_reference(
            entry,
            materialized_expected,
            materialized_alternatives,
        )
        eligibility, extra_errors = _build_eligibility(
            entry,
            materialized_entry,
            ragas_reference,
        )
        validation_errors = [
            str(item)
            for item in (materialized_entry.get("validation_errors") or [])
            if str(item).strip()
        ]
        validation_errors.extend(extra_errors)
        rows.append(
            {
                **entry,
                "retrieval_defaults": dict(materialized_data.get("defaults") or {}),
                "materialized_expected": materialized_expected,
                "materialized_alternatives": materialized_alternatives,
                "ragas_reference": ragas_reference,
                "eligibility": eligibility,
                "validation_errors": validation_errors,
            }
        )

    valid_queries = sum(
        1 for row in rows if bool((row.get("eligibility") or {}).get("valid"))
    )
    ragas_enabled_queries = sum(
        1 for row in rows if bool((row.get("eligibility") or {}).get("ragas_enabled"))
    )
    return {
        "dataset_name": _normalize_dataset_name(
            str(gold_data.get("dataset_name") or "code_retrieval_gold")
        ),
        "repo_id": gold_data.get("repo_id") or materialized_data.get("repo_id"),
        "defaults": dict(materialized_data.get("defaults") or {}),
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace_root": materialized_data.get("workspace_root"),
        "source_artifacts": {
            "gold_dataset_name": gold_data.get("dataset_name"),
            "materialized_dataset_name": materialized_data.get("dataset_name"),
            "materialized_generated_at": materialized_data.get("generated_at"),
        },
        "total_queries": len(rows),
        "valid_queries": valid_queries,
        "invalid_queries": len(rows) - valid_queries,
        "ragas_enabled_queries": ragas_enabled_queries,
        "ragas_disabled_queries": len(rows) - ragas_enabled_queries,
        "queries": rows,
    }


def main() -> int:
    """CLI entrypoint for RAGAS dataset materialization."""
    args = parse_args()
    gold_data = _load_json(args.gold_file)
    materialized_data = _load_json(args.materialized_file)
    dataset = materialize_ragas_dataset(gold_data, materialized_data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "ragas_dataset_queries="
        f"{dataset['total_queries']} valid={dataset['valid_queries']} "
        f"enabled={dataset['ragas_enabled_queries']} output={args.output}"
    )
    return 0 if dataset["invalid_queries"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())