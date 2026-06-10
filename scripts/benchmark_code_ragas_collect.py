"""Colecta respuestas completas de /query para evaluación RAGAS offline."""

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
class MaterializedRagasQuery:
    """Entrada materializada apta para la colección RAGAS."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    repo_id: str
    top_n: int
    top_k: int
    ragas_reference: dict[str, Any]
    materialized_expected: dict[str, Any]
    materialized_alternatives: list[dict[str, Any]]
    eligibility: dict[str, Any]


@dataclass(frozen=True)
class CollectionRow:
    """Resumen operacional por query para la colección RAGAS."""

    query_id: str
    query: str
    cohort: str
    gate_candidate: bool
    http_status: int
    ok: bool
    error_code: str | None
    fallback_used: bool
    fallback_reason: str | None
    citations_count: int
    retrieved_contexts_count: int
    answer_chars: int
    total_ms: float | None
    score_eligible: bool
    score_skip_reason: str | None


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for RAGAS collection."""
    repo_root = Path(__file__).resolve().parents[1]
    default_dataset = (
        repo_root / "benchmark_reports" / "code_ragas_dataset_materialized.json"
    )
    parser = argparse.ArgumentParser(
        description="Colecta artefactos brutos de /query para evaluación RAGAS.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", default=None)
    parser.add_argument("--dataset-file", type=Path, default=default_dataset)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "benchmark_reports",
    )
    return parser.parse_args()


def _load_json(file_path: Path) -> dict[str, Any]:
    return json.loads(file_path.read_text(encoding="utf-8"))


def _extract_error_code(response_body: Any) -> str | None:
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
    if isinstance(detail, str):
        text = detail.strip()
        if text:
            return text
    return None


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text[:2000]}


def load_materialized_ragas_queries(
    dataset_file: Path,
) -> tuple[dict[str, Any], Path | None, list[MaterializedRagasQuery]]:
    """Load only valid and RAGAS-enabled rows from the materialized dataset."""
    if not dataset_file.exists() or not dataset_file.is_file():
        raise ValueError(f"dataset_file no existe: {dataset_file}")

    payload = _load_json(dataset_file)
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise ValueError("dataset_file debe incluir lista no vacia en 'queries'")

    workspace_root_raw = payload.get("workspace_root")
    workspace_root = Path(workspace_root_raw) if workspace_root_raw else None
    defaults = dict(payload.get("defaults") or {})
    queries: list[MaterializedRagasQuery] = []
    for item in raw_queries:
        if not isinstance(item, dict):
            continue
        eligibility = item.get("eligibility")
        if not isinstance(eligibility, dict):
            continue
        if not bool(eligibility.get("valid", False)):
            continue
        if not bool(eligibility.get("ragas_enabled", False)):
            continue

        query_id = str(item.get("id") or item.get("query_id") or "").strip()
        query = str(item.get("query") or "").strip()
        repo_id = str(item.get("repo_id") or payload.get("repo_id") or "").strip()
        ragas_reference = item.get("ragas_reference")
        materialized_expected = item.get("materialized_expected")
        materialized_alternatives = item.get("materialized_alternatives")
        if not isinstance(ragas_reference, dict):
            continue
        if not isinstance(materialized_expected, dict):
            continue
        if not isinstance(materialized_alternatives, list):
            materialized_alternatives = []
        retrieval_defaults = item.get("retrieval_defaults")
        if not isinstance(retrieval_defaults, dict):
            retrieval_defaults = defaults
        if not query_id or not query or not repo_id:
            continue

        queries.append(
            MaterializedRagasQuery(
                query_id=query_id,
                query=query,
                cohort=str(item.get("cohort") or "unknown"),
                gate_candidate=bool(item.get("gate_candidate", False)),
                repo_id=repo_id,
                top_n=int(retrieval_defaults.get("top_n", defaults.get("top_n", 60))),
                top_k=int(retrieval_defaults.get("top_k", defaults.get("top_k", 20))),
                ragas_reference=ragas_reference,
                materialized_expected=materialized_expected,
                materialized_alternatives=[
                    candidate
                    for candidate in materialized_alternatives
                    if isinstance(candidate, dict)
                ],
                eligibility=eligibility,
            )
        )

    if not queries:
        raise ValueError("dataset_file no contiene queries RAGAS habilitadas")
    return payload, workspace_root, queries


def _resolve_repo_file_path(
    workspace_root: Path | None,
    relative_path: str,
) -> Path | None:
    if workspace_root is None:
        return None
    normalized = relative_path.replace("\\", "/").lstrip("/")
    resolved = workspace_root / normalized
    return resolved if resolved.exists() and resolved.is_file() else None


def _extract_lines(
    file_path: Path,
    start_line: int | None,
    end_line: int | None,
) -> str | None:
    lines = file_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    if start_line is None or end_line is None:
        return "\n".join(lines[:200]).strip() or None
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), end_line)
    if start_index >= len(lines) or start_index >= end_index:
        return None
    text = "\n".join(lines[start_index:end_index]).strip()
    return text or None


def _fallback_context_text(
    citation: dict[str, Any],
    query: MaterializedRagasQuery,
) -> tuple[str | None, str | None]:
    citation_path = str(citation.get("path") or "").strip()
    citation_start = citation.get("start_line")
    citation_end = citation.get("end_line")
    for target in [query.materialized_expected, *query.materialized_alternatives]:
        if not isinstance(target, dict):
            continue
        if str(target.get("path") or "").strip() != citation_path:
            continue
        if citation_start != target.get("start_line"):
            continue
        if citation_end != target.get("end_line"):
            continue
        snippet = str(
            target.get("snippet_preview") or target.get("snippet") or ""
        ).strip()
        if snippet:
            return snippet, "materialized_target"
    return None, None


def _build_retrieved_contexts(
    *,
    citations: list[dict[str, Any]],
    workspace_root: Path | None,
    query: MaterializedRagasQuery,
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for citation in citations:
        path = str(citation.get("path") or "").strip()
        start_line = (
            int(citation.get("start_line"))
            if citation.get("start_line") is not None
            else None
        )
        end_line = (
            int(citation.get("end_line"))
            if citation.get("end_line") is not None
            else None
        )
        key = (path, start_line, end_line)
        if not path or key in seen:
            continue
        seen.add(key)

        context_text: str | None = None
        context_source: str | None = None
        file_path = _resolve_repo_file_path(workspace_root, path)
        if file_path is not None:
            context_text = _extract_lines(file_path, start_line, end_line)
            if context_text is not None:
                context_source = "workspace_citation_span"
        if context_text is None:
            context_text, context_source = _fallback_context_text(citation, query)
        if context_text is None:
            continue

        contexts.append(
            {
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "score": citation.get("score"),
                "reason": citation.get("reason"),
                "text": context_text,
                "context_chars": len(context_text),
                "source": context_source,
            }
        )
    return contexts


def _build_score_eligibility(
    *,
    ok: bool,
    answer_text: str,
    citations: list[dict[str, Any]],
    retrieved_contexts: list[dict[str, Any]],
    ragas_reference: dict[str, Any],
    diagnostics: dict[str, Any],
) -> tuple[bool, str | None]:
    if not ok:
        return False, "http_error"
    if not answer_text.strip():
        return False, "empty_answer"
    if bool(diagnostics.get("fallback_used", False)):
        return False, "fallback_used"
    if bool(ragas_reference.get("requires_citations", True)) and not citations:
        return False, "missing_citations"
    if not retrieved_contexts:
        return False, "missing_retrieved_contexts"
    return True, None


def collect_ragas_queries(
    *,
    base_url: str,
    repo_id: str | None,
    materialized_queries: list[MaterializedRagasQuery],
    workspace_root: Path | None,
    top_n_override: int | None,
    top_k_override: int | None,
    timeout_seconds: float,
    request_fn: RequestFn = requests.post,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[CollectionRow]]:
    """Collect /query responses and freeze evidence for later scoring."""
    json_rows: list[dict[str, Any]] = []
    csv_rows: list[CollectionRow] = []

    for query in materialized_queries:
        effective_repo_id = repo_id or query.repo_id
        effective_top_n = top_n_override if top_n_override is not None else query.top_n
        effective_top_k = top_k_override if top_k_override is not None else query.top_k
        payload = {
            "repo_id": effective_repo_id,
            "query": query.query,
            "top_n": effective_top_n,
            "top_k": effective_top_k,
        }
        response: requests.Response | None = None
        response_body: Any = None
        error_code: str | None = None

        try:
            response = request_fn(
                f"{base_url.rstrip('/')}/query",
                json=payload,
                timeout=timeout_seconds,
            )
            response_body = _safe_json(response)
            if response.status_code != 200:
                error_code = _extract_error_code(response_body)
        except requests.RequestException as exc:
            response_body = {"request_error": str(exc)}
            error_code = exc.__class__.__name__

        status_code = response.status_code if response is not None else 0
        ok = status_code == 200 and isinstance(response_body, dict)
        body = response_body if isinstance(response_body, dict) else {}
        answer_text = str(body.get("answer") or "") if ok else ""
        citations = body.get("citations") if ok and isinstance(body.get("citations"), list) else []
        citations = [item for item in citations if isinstance(item, dict)]
        diagnostics = body.get("diagnostics") if ok and isinstance(body.get("diagnostics"), dict) else {}
        retrieved_contexts = _build_retrieved_contexts(
            citations=citations,
            workspace_root=workspace_root,
            query=query,
        )
        score_eligible, skip_reason = _build_score_eligibility(
            ok=ok,
            answer_text=answer_text,
            citations=citations,
            retrieved_contexts=retrieved_contexts,
            ragas_reference=query.ragas_reference,
            diagnostics=diagnostics,
        )
        stage_timings = diagnostics.get("stage_timings_ms")
        total_ms = None
        if isinstance(stage_timings, dict) and stage_timings.get("total_ms") is not None:
            total_ms = float(stage_timings["total_ms"])

        summary = CollectionRow(
            query_id=query.query_id,
            query=query.query,
            cohort=query.cohort,
            gate_candidate=query.gate_candidate,
            http_status=status_code,
            ok=ok,
            error_code=error_code,
            fallback_used=bool(diagnostics.get("fallback_used", False)),
            fallback_reason=(
                str(diagnostics.get("fallback_reason"))
                if diagnostics.get("fallback_reason") is not None
                else None
            ),
            citations_count=len(citations),
            retrieved_contexts_count=len(retrieved_contexts),
            answer_chars=len(answer_text),
            total_ms=total_ms,
            score_eligible=score_eligible,
            score_skip_reason=skip_reason,
        )
        csv_rows.append(summary)
        json_rows.append(
            {
                **summary.__dict__,
                "payload": payload,
                "ragas_reference": query.ragas_reference,
                "materialized_expected": query.materialized_expected,
                "materialized_alternatives": query.materialized_alternatives,
                "eligibility": query.eligibility,
                "response_body": response_body,
                "answer_text": answer_text,
                "retrieved_contexts": retrieved_contexts,
            }
        )

    ok_rows = sum(1 for row in csv_rows if row.ok)
    score_eligible_rows = sum(1 for row in csv_rows if row.score_eligible)
    metadata = {
        "base_url": base_url,
        "repo_id": repo_id,
        "queries_count": len(csv_rows),
        "successful_queries": ok_rows,
        "failed_queries": len(csv_rows) - ok_rows,
        "score_eligible_queries": score_eligible_rows,
        "score_skipped_queries": len(csv_rows) - score_eligible_rows,
        "timeout_seconds": timeout_seconds,
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
    }
    return metadata, json_rows, csv_rows


def write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    json_rows: list[dict[str, Any]],
    csv_rows: list[CollectionRow],
) -> tuple[Path, Path]:
    """Write JSON and CSV artifacts for the RAGAS collection run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"code_ragas_collect_{timestamp}.json"
    csv_path = output_dir / f"code_ragas_collect_{timestamp}.csv"

    json_path.write_text(
        json.dumps(
            {"meta": metadata, "rows": json_rows},
            indent=2,
            ensure_ascii=False,
        ),
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
    """CLI entrypoint for the RAGAS collector."""
    args = parse_args()
    dataset_payload, workspace_root, queries = load_materialized_ragas_queries(
        args.dataset_file,
    )
    metadata, json_rows, csv_rows = collect_ragas_queries(
        base_url=args.base_url,
        repo_id=args.repo_id or str(dataset_payload.get("repo_id") or "").strip(),
        materialized_queries=queries,
        workspace_root=workspace_root,
        top_n_override=args.top_n,
        top_k_override=args.top_k,
        timeout_seconds=args.timeout_seconds,
    )
    metadata["dataset_file"] = str(args.dataset_file)
    metadata["dataset_name"] = dataset_payload.get("dataset_name")
    json_path, csv_path = write_reports(
        output_dir=args.output_dir,
        metadata=metadata,
        json_rows=json_rows,
        csv_rows=csv_rows,
    )

    print("Code RAGAS collection completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        f"successful_queries={metadata['successful_queries']} "
        f"score_eligible_queries={metadata['score_eligible_queries']}"
    )
    return 0 if metadata["failed_queries"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())