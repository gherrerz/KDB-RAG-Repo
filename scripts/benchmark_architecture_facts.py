"""Evalua cobertura factual arquitectonica contra un gold set fijo."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

import requests


@dataclass(frozen=True)
class FactQuery:
    """Consulta con lista de hechos esperados para scoring factual."""

    query: str
    expected_facts: tuple[str, ...]


@dataclass(frozen=True)
class FactEvalRow:
    """Resultado factual por consulta en benchmark ON/OFF."""

    query: str
    matched_facts: int
    total_facts: int
    fact_coverage_score: float
    missed_facts: str
    citations: int
    fallback_reason: str | None
    semantic_query_enabled: bool | None
    semantic_edges_used: int | None


def parse_args() -> argparse.Namespace:
    """Parsea argumentos CLI para benchmark factual de arquitectura."""
    parser = argparse.ArgumentParser(
        description=(
            "Evalua fact_coverage_score de consultas arquitectonicas usando "
            "gold set de expected facts."
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--gold-file", default="scripts/benchmark_data/architecture_facts_gold.json")
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output-dir", default="benchmark_reports")
    return parser.parse_args()


def _normalize(value: str) -> str:
    """Normaliza texto para matching robusto sin diferencias de formato."""
    lowered = value.lower().replace("-", "_")
    return re.sub(r"\s+", " ", lowered)


def load_gold_queries(gold_file: Path) -> list[FactQuery]:
    """Carga consultas/facts esperados desde archivo JSON de gold set."""
    if not gold_file.exists() or not gold_file.is_file():
        raise ValueError(f"gold_file no existe: {gold_file}")

    payload = json.loads(gold_file.read_text(encoding="utf-8"))
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise ValueError("gold_file debe incluir lista no vacia en 'queries'")

    queries: list[FactQuery] = []
    for item in raw_queries:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        facts_raw = item.get("expected_facts")
        if not query or not isinstance(facts_raw, list):
            continue
        facts = tuple(str(f).strip() for f in facts_raw if str(f).strip())
        if not facts:
            continue
        queries.append(FactQuery(query=query, expected_facts=facts))

    if not queries:
        raise ValueError("gold_file no contiene entradas validas")
    return queries


def _build_match_corpus(answer: str, diagnostics: dict[str, Any], citations: list[dict[str, Any]]) -> str:
    """Construye corpus de matching con answer, diagnostics y paths citados."""
    citation_paths = " ".join(
        str(item.get("path") or "")
        for item in citations
        if isinstance(item, dict)
    )
    diagnostic_keys = " ".join(str(key) for key in diagnostics.keys())
    combined = f"{answer} {citation_paths} {diagnostic_keys}"
    return _normalize(combined)


def evaluate_fact_queries(
    *,
    base_url: str,
    repo_id: str,
    fact_queries: list[FactQuery],
    top_n: int,
    top_k: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], list[FactEvalRow]]:
    """Ejecuta benchmark factual por consulta y calcula score agregado."""
    rows: list[FactEvalRow] = []

    for fact_query in fact_queries:
        payload = {
            "repo_id": repo_id,
            "query": fact_query.query,
            "top_n": top_n,
            "top_k": top_k,
        }
        response = requests.post(
            f"{base_url.rstrip('/')}/query",
            json=payload,
            timeout=timeout_seconds,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Consulta fallo con HTTP {response.status_code}: {response.text[:500]}"
            )

        body = response.json()
        diagnostics = body.get("diagnostics", {})
        if not isinstance(diagnostics, dict):
            diagnostics = {}

        citations_raw = body.get("citations")
        citations = citations_raw if isinstance(citations_raw, list) else []

        answer = str(body.get("answer") or "")
        corpus = _build_match_corpus(answer, diagnostics, citations)

        matched: list[str] = []
        missed: list[str] = []
        for fact in fact_query.expected_facts:
            normalized_fact = _normalize(fact)
            if normalized_fact in corpus:
                matched.append(fact)
            else:
                missed.append(fact)

        total_facts = len(fact_query.expected_facts)
        coverage = len(matched) / total_facts if total_facts else 0.0
        semantic_edges_used = diagnostics.get("semantic_edges_used")

        rows.append(
            FactEvalRow(
                query=fact_query.query,
                matched_facts=len(matched),
                total_facts=total_facts,
                fact_coverage_score=round(coverage, 4),
                missed_facts="|".join(missed),
                citations=len(citations),
                fallback_reason=diagnostics.get("fallback_reason"),
                semantic_query_enabled=diagnostics.get("semantic_query_enabled"),
                semantic_edges_used=(
                    int(semantic_edges_used)
                    if semantic_edges_used is not None
                    else None
                ),
            )
        )

    mean_fact_score = sum(item.fact_coverage_score for item in rows) / len(rows)
    mean_edges_used = sum((item.semantic_edges_used or 0) for item in rows) / len(rows)
    meta = {
        "base_url": base_url,
        "repo_id": repo_id,
        "queries_count": len(rows),
        "architecture_fact_coverage_score": round(mean_fact_score, 4),
        "semantic_edges_used_mean": round(mean_edges_used, 2),
    }
    return meta, rows


def write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    rows: list[FactEvalRow],
) -> tuple[Path, Path]:
    """Escribe resultados JSON/CSV del benchmark factual."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"architecture_facts_eval_{timestamp}.json"
    csv_path = output_dir / f"architecture_facts_eval_{timestamp}.csv"

    json_payload = {
        "meta": metadata,
        "rows": [row.__dict__ for row in rows],
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(FactEvalRow.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    return json_path, csv_path


def main() -> int:
    """Ejecuta benchmark factual y reporta score de cobertura de hechos."""
    args = parse_args()
    fact_queries = load_gold_queries(Path(args.gold_file))
    metadata, rows = evaluate_fact_queries(
        base_url=args.base_url,
        repo_id=args.repo_id,
        fact_queries=fact_queries,
        top_n=args.top_n,
        top_k=args.top_k,
        timeout_seconds=args.timeout_seconds,
    )
    json_path, csv_path = write_reports(
        output_dir=Path(args.output_dir),
        metadata=metadata,
        rows=rows,
    )

    print("Architecture facts evaluation completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        "architecture_fact_coverage_score="
        f"{metadata['architecture_fact_coverage_score']:.4f}"
    )
    print(f"semantic_edges_used_mean={metadata['semantic_edges_used_mean']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
