"""Evalua calidad arquitectonica por cobertura de componentes esperados."""

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
class QuerySpec:
    """Define consulta objetivo y componentes esperados en la respuesta."""

    query: str
    expected_components: tuple[str, ...]


QUERY_SPECS: tuple[QuerySpec, ...] = (
    QuerySpec(
        query="explica la arquitectura general del sistema y sus capas principales",
        expected_components=(
            "api",
            "ingestion",
            "retrieval",
            "storage",
            "llm",
        ),
    ),
    QuerySpec(
        query="describe como fluye una consulta desde la API hasta la generacion de respuesta",
        expected_components=(
            "query_service",
            "hybrid_search",
            "graph_expand",
            "assemble_context",
            "answerclient",
        ),
    ),
    QuerySpec(
        query="que modulos participan en ingestion, retrieval y llm y como se conectan",
        expected_components=(
            "pipeline",
            "graph_builder",
            "hybrid_search",
            "rerank",
            "openai_client",
        ),
    ),
    QuerySpec(
        query="describe la relacion entre query_service, hybrid_search y graph_expand",
        expected_components=(
            "query_service",
            "hybrid_search",
            "graph_expand",
            "rerank",
            "context",
        ),
    ),
    QuerySpec(
        query="como se integra el pipeline de inventory con el resto de la arquitectura",
        expected_components=(
            "inventory",
            "query_service",
            "graph_builder",
            "diagnostics",
            "citation",
        ),
    ),
    QuerySpec(
        query="explica como se gestionan providers y modelos en la arquitectura",
        expected_components=(
            "settings",
            "provider",
            "model",
            "capabilities",
            "resolve",
        ),
    ),
    QuerySpec(
        query="que componentes sostienen trazabilidad y citas en respuestas de query",
        expected_components=(
            "citation",
            "diagnostics",
            "path",
            "line",
            "evidence",
        ),
    ),
    QuerySpec(
        query="describe el manejo de fallback cuando falla la verificacion o el modelo",
        expected_components=(
            "fallback",
            "verification",
            "generation_error",
            "time_budget",
            "not_configured",
        ),
    ),
    QuerySpec(
        query="como se controla presupuesto de tiempo y diagnosticos por etapa en query",
        expected_components=(
            "query_budget",
            "stage_timings",
            "graph_expand",
            "hybrid_search",
            "llm_answer",
        ),
    ),
    QuerySpec(
        query="explica el rol de settings y feature flags semanticos en tiempo de ejecucion",
        expected_components=(
            "settings",
            "semantic_graph_query_enabled",
            "semantic_relation_types",
            "max_edges",
            "fallback",
        ),
    ),
)


@dataclass(frozen=True)
class QualityRow:
    """Resultado de calidad por consulta para comparativa ON/OFF."""

    query: str
    matched_components: int
    total_components: int
    component_coverage_score: float
    citations: int
    unique_citation_paths: int
    fallback_reason: str | None
    semantic_query_enabled: bool | None
    semantic_edges_used: int | None


def parse_args() -> argparse.Namespace:
    """Parsea argumentos de ejecucion para benchmark de calidad."""
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark de calidad arquitectonica por cobertura de componentes "
            "esperados en respuestas /query."
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output-dir", default="benchmark_reports")
    return parser.parse_args()


def _normalize_text(value: str) -> str:
    """Normaliza texto para matching estable sin ruido de formato."""
    lowered = value.lower()
    return re.sub(r"\s+", " ", lowered)


def _component_matches(
    *,
    answer_text: str,
    citation_paths: list[str],
    expected_components: tuple[str, ...],
) -> int:
    """Cuenta componentes esperados presentes en respuesta o rutas citadas."""
    corpus = _normalize_text(answer_text)
    if citation_paths:
        corpus = f"{corpus} " + _normalize_text(" ".join(citation_paths))

    matches = 0
    for component in expected_components:
        token = _normalize_text(component)
        if token in corpus:
            matches += 1
    return matches


def evaluate_quality(
    *,
    base_url: str,
    repo_id: str,
    top_n: int,
    top_k: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], list[QualityRow]]:
    """Ejecuta set fijo y calcula score de cobertura por consulta."""
    rows: list[QualityRow] = []
    for spec in QUERY_SPECS:
        payload = {
            "repo_id": repo_id,
            "query": spec.query,
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
        citation_paths = [
            str(item.get("path") or "")
            for item in citations
            if isinstance(item, dict) and item.get("path")
        ]
        unique_paths = len(set(citation_paths))

        matched = _component_matches(
            answer_text=str(body.get("answer") or ""),
            citation_paths=citation_paths,
            expected_components=spec.expected_components,
        )
        total_components = len(spec.expected_components)
        coverage_score = matched / total_components if total_components else 0.0

        semantic_edges_used = diagnostics.get("semantic_edges_used")
        rows.append(
            QualityRow(
                query=spec.query,
                matched_components=matched,
                total_components=total_components,
                component_coverage_score=round(coverage_score, 4),
                citations=len(citations),
                unique_citation_paths=unique_paths,
                fallback_reason=diagnostics.get("fallback_reason"),
                semantic_query_enabled=diagnostics.get("semantic_query_enabled"),
                semantic_edges_used=(
                    int(semantic_edges_used)
                    if semantic_edges_used is not None
                    else None
                ),
            )
        )

    avg_coverage = sum(item.component_coverage_score for item in rows) / len(rows)
    avg_paths = sum(item.unique_citation_paths for item in rows) / len(rows)
    meta = {
        "base_url": base_url,
        "repo_id": repo_id,
        "queries_count": len(rows),
        "architecture_component_coverage_score": round(avg_coverage, 4),
        "avg_unique_citation_paths": round(avg_paths, 2),
    }
    return meta, rows


def write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    rows: list[QualityRow],
) -> tuple[Path, Path]:
    """Escribe reporte JSON/CSV con score de calidad por consulta."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"architecture_quality_eval_{timestamp}.json"
    csv_path = output_dir / f"architecture_quality_eval_{timestamp}.csv"

    json_payload = {
        "meta": metadata,
        "rows": [item.__dict__ for item in rows],
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(QualityRow.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    return json_path, csv_path


def main() -> int:
    """Ejecuta benchmark de calidad arquitectonica y publica resultados."""
    args = parse_args()
    meta, rows = evaluate_quality(
        base_url=args.base_url,
        repo_id=args.repo_id,
        top_n=args.top_n,
        top_k=args.top_k,
        timeout_seconds=args.timeout_seconds,
    )
    json_path, csv_path = write_reports(
        output_dir=Path(args.output_dir),
        metadata=meta,
        rows=rows,
    )

    print("Architecture quality evaluation completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        "architecture_component_coverage_score="
        f"{meta['architecture_component_coverage_score']:.4f}"
    )
    print(f"avg_unique_citation_paths={meta['avg_unique_citation_paths']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
