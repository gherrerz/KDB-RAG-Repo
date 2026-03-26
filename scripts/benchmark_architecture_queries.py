"""Evalua consultas de arquitectura sobre /query y calcula success rate."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import requests


DEFAULT_QUERIES = [
    "explica la arquitectura general del sistema y sus capas principales",
    "describe como fluye una consulta desde la API hasta la generacion de respuesta",
    "que modulos participan en ingestion, retrieval y llm y como se conectan",
    "describe la relacion entre query_service, hybrid_search y graph_expand",
    "como se integra el pipeline de inventory con el resto de la arquitectura",
    "explica como se gestionan providers y modelos en la arquitectura",
    "que componentes sostienen trazabilidad y citas en respuestas de query",
    "describe el manejo de fallback cuando falla la verificacion o el modelo",
    "como se controla presupuesto de tiempo y diagnosticos por etapa en query",
    "explica el rol de settings y feature flags semanticos en tiempo de ejecucion",
]

DEFAULT_KEYWORDS = {
    "api",
    "architecture",
    "arquitectura",
    "citation",
    "cita",
    "diagnostic",
    "diagnostico",
    "fallback",
    "graph",
    "grafo",
    "ingesta",
    "ingestion",
    "model",
    "modelo",
    "modulo",
    "pipeline",
    "provider",
    "query",
    "retrieval",
    "semantic",
}


@dataclass(frozen=True)
class SuccessCriteria:
    """Define los umbrales para marcar una consulta como exitosa."""

    min_citations: int
    min_answer_chars: int
    min_keyword_hits: int


@dataclass(frozen=True)
class QueryRunResult:
    """Resultado estructurado para una consulta de arquitectura."""

    query: str
    success: bool
    fallback_reason: str | None
    citations: int
    answer_chars: int
    keyword_hits: int
    verify_skipped: bool
    semantic_query_enabled: bool | None
    semantic_edges_used: int | None
    semantic_nodes_used: int | None
    semantic_noise_ratio: float | None
    stage_total_ms: float | None


def parse_args() -> argparse.Namespace:
    """Parsea argumentos CLI para la evaluacion de arquitectura."""
    parser = argparse.ArgumentParser(
        description="Evalua architecture_query_success_rate con set fijo de consultas.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--min-citations", type=int, default=5)
    parser.add_argument("--min-answer-chars", type=int, default=500)
    parser.add_argument("--min-keyword-hits", type=int, default=3)
    parser.add_argument(
        "--queries-file",
        default="",
        help="Archivo .txt opcional (1 consulta por linea).",
    )
    parser.add_argument("--output-dir", default="benchmark_reports")
    return parser.parse_args()


def load_queries(queries_file: str) -> list[str]:
    """Carga consultas desde archivo o usa set fijo por defecto."""
    if not queries_file:
        return list(DEFAULT_QUERIES)

    path = Path(queries_file)
    if not path.exists() or not path.is_file():
        raise ValueError(f"queries_file no existe: {queries_file}")

    queries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    queries = [query for query in queries if query]
    if not queries:
        raise ValueError("queries_file no contiene consultas validas")
    return queries


def _keyword_hits(answer_text: str, keywords: set[str]) -> int:
    """Cuenta cuantas palabras clave aparecen en la respuesta normalizada."""
    normalized = answer_text.lower()
    return sum(1 for keyword in keywords if keyword in normalized)


def evaluate_query(
    *,
    base_url: str,
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    timeout_seconds: float,
    criteria: SuccessCriteria,
) -> QueryRunResult:
    """Ejecuta una consulta y evalua si cumple el criterio de exito."""
    payload = {
        "repo_id": repo_id,
        "query": query,
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

    data = response.json()
    diagnostics = data.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}

    answer = str(data.get("answer") or "")
    citations_raw = data.get("citations")
    citations = len(citations_raw) if isinstance(citations_raw, list) else 0
    keyword_hits = _keyword_hits(answer_text=answer, keywords=DEFAULT_KEYWORDS)

    fallback_reason = diagnostics.get("fallback_reason")
    answer_chars = len(answer)
    success = (
        fallback_reason is None
        and citations >= criteria.min_citations
        and answer_chars >= criteria.min_answer_chars
        and keyword_hits >= criteria.min_keyword_hits
    )

    timings = diagnostics.get("stage_timings_ms")
    stage_total_ms = None
    if isinstance(timings, dict) and "total_ms" in timings:
        stage_total_ms = float(timings.get("total_ms") or 0.0)

    semantic_edges_used = diagnostics.get("semantic_edges_used")
    semantic_nodes_used = diagnostics.get("semantic_nodes_used")

    return QueryRunResult(
        query=query,
        success=success,
        fallback_reason=fallback_reason,
        citations=citations,
        answer_chars=answer_chars,
        keyword_hits=keyword_hits,
        verify_skipped=bool(diagnostics.get("verify_skipped")),
        semantic_query_enabled=diagnostics.get("semantic_query_enabled"),
        semantic_edges_used=(
            int(semantic_edges_used) if semantic_edges_used is not None else None
        ),
        semantic_nodes_used=(
            int(semantic_nodes_used) if semantic_nodes_used is not None else None
        ),
        semantic_noise_ratio=(
            float(diagnostics.get("semantic_noise_ratio"))
            if diagnostics.get("semantic_noise_ratio") is not None
            else None
        ),
        stage_total_ms=stage_total_ms,
    )


def write_reports(
    *,
    output_dir: Path,
    metadata: dict[str, Any],
    rows: list[QueryRunResult],
) -> tuple[Path, Path]:
    """Persiste reporte JSON/CSV con detalle por consulta y agregados."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"architecture_query_eval_{timestamp}.json"
    csv_path = output_dir / f"architecture_query_eval_{timestamp}.csv"

    json_payload = {
        "meta": metadata,
        "rows": [row.__dict__ for row in rows],
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(QueryRunResult.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    return json_path, csv_path


def main() -> int:
    """Ejecuta evaluacion y emite success rate de arquitectura."""
    args = parse_args()
    if args.min_citations <= 0:
        raise ValueError("min_citations debe ser > 0")
    if args.min_answer_chars <= 0:
        raise ValueError("min_answer_chars debe ser > 0")
    if args.min_keyword_hits <= 0:
        raise ValueError("min_keyword_hits debe ser > 0")

    queries = load_queries(args.queries_file)
    criteria = SuccessCriteria(
        min_citations=args.min_citations,
        min_answer_chars=args.min_answer_chars,
        min_keyword_hits=args.min_keyword_hits,
    )

    results = [
        evaluate_query(
            base_url=args.base_url,
            repo_id=args.repo_id,
            query=query,
            top_n=args.top_n,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
            criteria=criteria,
        )
        for query in queries
    ]

    success_count = sum(1 for item in results if item.success)
    success_rate = success_count / len(results)

    metadata = {
        "base_url": args.base_url,
        "repo_id": args.repo_id,
        "queries_count": len(results),
        "success_count": success_count,
        "architecture_query_success_rate": round(success_rate, 4),
        "criteria": {
            "fallback_reason": None,
            "min_citations": criteria.min_citations,
            "min_answer_chars": criteria.min_answer_chars,
            "min_keyword_hits": criteria.min_keyword_hits,
        },
    }

    json_path, csv_path = write_reports(
        output_dir=Path(args.output_dir),
        metadata=metadata,
        rows=results,
    )

    print("Architecture evaluation completed")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(
        "architecture_query_success_rate="
        f"{success_rate:.4f} ({success_count}/{len(results)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
