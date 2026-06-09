"""Colecta respuestas retrieval-only para el gold set materializado."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

import requests


RequestFn = Callable[..., requests.Response]


@dataclass(frozen=True)
class MaterializedQuery:
    """Entrada del gold set materializado usada por el collector."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    preferred_endpoint: str
    expected_path: str
    expected_kind: str | None
    materialized_expected: dict[str, Any]
    materialized_alternatives: list[dict[str, Any]]


@dataclass(frozen=True)
class CollectionRow:
    """Resumen por consulta de la corrida retrieval-only."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    http_status: int
    ok: bool
    error_code: str | None
    chunks_count: int
    citations_count: int
    fallback_reason: str | None
    fallback_used: bool
    top1_path: str | None
    top1_start_line: int | None
    top1_end_line: int | None
    top1_kind: str | None
    total_ms: float | None


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for retrieval collection."""
    repo_root = Path(__file__).resolve().parents[1]
    default_materialized = repo_root / "benchmark_reports" / "code_gold_materialized.json"
    parser = argparse.ArgumentParser(
        description="Colecta artefactos brutos de /query/retrieval para code retrieval eval.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", default=None)
    parser.add_argument("--materialized-file", type=Path, default=default_materialized)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--include-context", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "benchmark_reports")
    return parser.parse_args()


def load_materialized_queries(materialized_file: Path) -> tuple[dict[str, Any], list[MaterializedQuery]]:
    """Load valid queries from a materialized gold set artifact."""
    if not materialized_file.exists() or not materialized_file.is_file():
        raise ValueError(f"materialized_file no existe: {materialized_file}")

    payload = json.loads(materialized_file.read_text(encoding="utf-8"))
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise ValueError("materialized_file debe incluir lista no vacia en 'queries'")

    queries: list[MaterializedQuery] = []
    for item in raw_queries:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("valid", False)):
            continue
        query_id = str(item.get("id") or "").strip()
        query = str(item.get("query") or "").strip()
        preferred_endpoint = str(item.get("preferred_endpoint") or "query/retrieval").strip()
        expected_path = str(item.get("expected_path") or "").strip()
        materialized_expected = item.get("materialized_expected")
        materialized_alternatives = item.get("materialized_alternatives")
        if not query_id or not query or preferred_endpoint != "query/retrieval":
            continue
        if not isinstance(materialized_expected, dict):
            continue
        if not isinstance(materialized_alternatives, list):
            materialized_alternatives = []

        queries.append(
            MaterializedQuery(
                query_id=query_id,
                query=query,
                cohort=str(item.get("cohort") or "unknown"),
                gate_candidate=bool(item.get("gate_candidate", False)),
                preferred_endpoint=preferred_endpoint,
                expected_path=expected_path,
                expected_kind=(
                    str(item.get("expected_kind"))
                    if item.get("expected_kind") is not None
                    else None
                ),
                materialized_expected=materialized_expected,
                materialized_alternatives=[
                    candidate
                    for candidate in materialized_alternatives
                    if isinstance(candidate, dict)
                ],
            )
        )

    if not queries:
        raise ValueError("materialized_file no contiene queries validas para query/retrieval")
    return payload, queries


def _extract_error_code(response_body: Any) -> str | None:
    """Extract a short machine-friendly error code from a non-200 response."""
    if not isinstance(response_body, dict):
        return None
    detail = response_body.get("detail")
    if isinstance(detail, dict):
        for key in ("code", "reason", "error"):
            value = detail.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return None


def _safe_json(response: requests.Response) -> Any:
    """Return JSON body when possible, falling back to a compact text wrapper."""
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text[:2000]}


def collect_retrieval_queries(
    *,
    base_url: str,
    repo_id: str,
    materialized_queries: list[MaterializedQuery],
    top_n: int,
    top_k: int,
    timeout_seconds: float,
    include_context: bool,
    request_fn: RequestFn = requests.post,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[CollectionRow]]:
    """Collect retrieval-only responses and keep the raw artifact per query."""
    json_rows: list[dict[str, Any]] = []
    csv_rows: list[CollectionRow] = []

    for entry in materialized_queries:
        payload = {
            "repo_id": repo_id,
            "query": entry.query,
            "top_n": top_n,
            "top_k": top_k,
            "include_context": include_context,
        }
        response: requests.Response | None = None
        response_body: Any = None
        error_code: str | None = None

        try:
            response = request_fn(
                f"{base_url.rstrip('/')}/query/retrieval",
                json=payload,
                timeout=timeout_seconds,
            )
            response_body = _safe_json(response)
            error_code = _extract_error_code(response_body) if response.status_code != 200 else None
        except requests.RequestException as exc:
            response_body = {"request_error": str(exc)}
            error_code = exc.__class__.__name__

        status_code = response.status_code if response is not None else 0
        ok = status_code == 200 and isinstance(response_body, dict)
        body = response_body if isinstance(response_body, dict) else {}
        chunks = body.get("chunks") if ok and isinstance(body.get("chunks"), list) else []
        citations = body.get("citations") if ok and isinstance(body.get("citations"), list) else []
        diagnostics = body.get("diagnostics") if ok and isinstance(body.get("diagnostics"), dict) else {}
        top_chunk = chunks[0] if chunks else {}
        if not isinstance(top_chunk, dict):
            top_chunk = {}
        stage_timings = diagnostics.get("stage_timings_ms")
        total_ms = None
        if isinstance(stage_timings, dict) and stage_timings.get("total_ms") is not None:
            total_ms = float(stage_timings["total_ms"])

        summary = CollectionRow(
            query_id=entry.query_id,
            query=entry.query,
            cohort=entry.cohort,
            gate_candidate=entry.gate_candidate,
            http_status=status_code,
            ok=ok,
            error_code=error_code,
            chunks_count=len(chunks),
            citations_count=len(citations),
            fallback_reason=(
                str(diagnostics.get("fallback_reason"))
                if diagnostics.get("fallback_reason") is not None
                else None
            ),
            fallback_used=bool(diagnostics.get("fallback_used", False)),
            top1_path=(str(top_chunk.get("path")) if top_chunk.get("path") is not None else None),
            top1_start_line=(
                int(top_chunk.get("start_line"))
                if top_chunk.get("start_line") is not None
                else None
            ),
            top1_end_line=(
                int(top_chunk.get("end_line"))
                if top_chunk.get("end_line") is not None
                else None
            ),
            top1_kind=(str(top_chunk.get("kind")) if top_chunk.get("kind") is not None else None),
            total_ms=total_ms,
        )
        csv_rows.append(summary)
        json_rows.append(
            {
                **summary.__dict__,
                "payload": payload,
                "materialized_expected": entry.materialized_expected,
                "materialized_alternatives": entry.materialized_alternatives,
                "response_body": response_body,
            }
        )

    ok_rows = sum(1 for row in csv_rows if row.ok)
    metadata = {
        "base_url": base_url,
        "repo_id": repo_id,
        "queries_count": len(csv_rows),
        "successful_queries": ok_rows,
        "failed_queries": len(csv_rows) - ok_rows,
        "top_n": top_n,
        "top_k": top_k,
        "include_context": include_context,
        "timeout_seconds": timeout_seconds,
    }
    return metadata, json_rows, csv_rows


def write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    json_rows: list[dict[str, Any]],
    csv_rows: list[CollectionRow],
) -> tuple[Path, Path]:
    """Write JSON raw artifact and CSV summary for a retrieval collection run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"code_retrieval_collect_{timestamp}.json"
    csv_path = output_dir / f"code_retrieval_collect_{timestamp}.csv"

    json_payload = {
        "meta": metadata,
        "rows": json_rows,
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(CollectionRow.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row.__dict__)

    return json_path, csv_path


def main() -> int:
    """CLI entrypoint for retrieval collection."""
    args = parse_args()
    materialized_payload, materialized_queries = load_materialized_queries(args.materialized_file)
    defaults = materialized_payload.get("defaults", {})
    repo_id = args.repo_id or str(materialized_payload.get("repo_id") or "").strip()
    if not repo_id:
        raise ValueError("repo_id es requerido via --repo-id o materialized_file.repo_id")
    top_n = args.top_n if args.top_n is not None else int(defaults.get("top_n", 60))
    top_k = args.top_k if args.top_k is not None else int(defaults.get("top_k", 20))

    metadata, json_rows, csv_rows = collect_retrieval_queries(
        base_url=args.base_url,
        repo_id=repo_id,
        materialized_queries=materialized_queries,
        top_n=top_n,
        top_k=top_k,
        timeout_seconds=args.timeout_seconds,
        include_context=args.include_context,
    )
    metadata["materialized_file"] = str(args.materialized_file)
    json_path, csv_path = write_reports(
        output_dir=args.output_dir,
        metadata=metadata,
        json_rows=json_rows,
        csv_rows=csv_rows,
    )

    print("Code retrieval collection completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        f"successful_queries={metadata['successful_queries']} "
        f"failed_queries={metadata['failed_queries']}"
    )
    return 0 if metadata["failed_queries"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())