"""Benchmark de latencia contra API real para escenarios de consulta RAG."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import statistics
from time import perf_counter, sleep
from typing import Any

import requests


@dataclass(frozen=True)
class Scenario:
    """Define un escenario de benchmark HTTP."""

    name: str
    endpoint: str
    payload: dict[str, Any]


def percentile(values: list[float], q: float) -> float:
    """Calcula percentil q en rango [0, 1] con interpolación lineal simple."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float]) -> dict[str, float]:
    """Resume lista de latencias con estadísticas estándar."""
    if not values:
        return {
            "count": 0.0,
            "mean_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
        }

    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 2),
        "min_ms": round(min(values), 2),
        "max_ms": round(max(values), 2),
        "p50_ms": round(percentile(values, 0.50), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
        "p99_ms": round(percentile(values, 0.99), 2),
    }


def call_endpoint(
    *,
    base_url: str,
    scenario: Scenario,
    timeout_seconds: float,
) -> tuple[float, dict[str, Any]]:
    """Ejecuta una llamada HTTP y devuelve latencia y JSON de respuesta."""
    url = f"{base_url.rstrip('/')}{scenario.endpoint}"
    started = perf_counter()
    response = requests.post(
        url,
        json=scenario.payload,
        timeout=timeout_seconds,
    )
    elapsed_ms = (perf_counter() - started) * 1000.0

    if response.status_code != 200:
        raise RuntimeError(
            f"Escenario '{scenario.name}' fallo con HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    payload = response.json()
    return elapsed_ms, payload


def build_scenarios(args: argparse.Namespace) -> list[Scenario]:
    """Construye escenarios de benchmark con base en argumentos CLI."""
    query_payload = {
        "repo_id": args.repo_id,
        "top_n": args.top_n,
        "top_k": args.top_k,
    }

    inventory_payload = {
        "repo_id": args.repo_id,
        "page": 1,
        "page_size": args.inventory_page_size,
    }

    return [
        Scenario(
            name="query_general",
            endpoint="/query",
            payload={
                **query_payload,
                "query": args.query_general,
            },
        ),
        Scenario(
            name="query_module",
            endpoint="/query",
            payload={
                **query_payload,
                "query": args.query_module,
            },
        ),
        Scenario(
            name="inventory_query",
            endpoint="/inventory/query",
            payload={
                **inventory_payload,
                "query": args.query_inventory,
            },
        ),
        Scenario(
            name="inventory_explain",
            endpoint="/inventory/query",
            payload={
                **inventory_payload,
                "query": args.query_inventory_explain,
            },
        ),
    ]


def write_reports(
    *,
    output_dir: Path,
    raw_results: dict[str, Any],
    summary_rows: list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Escribe reporte JSON y CSV en el directorio de salida."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"benchmark_live_{timestamp}.json"
    csv_path = output_dir / f"benchmark_live_{timestamp}.csv"

    json_path.write_text(
        json.dumps(raw_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = [
        "scenario",
        "count",
        "mean_ms",
        "min_ms",
        "max_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "diagnostic_total_mean_ms",
        "diagnostic_hybrid_mean_ms",
        "diagnostic_rerank_mean_ms",
        "diagnostic_graph_mean_ms",
        "diagnostic_context_mean_ms",
        "diagnostic_answer_mean_ms",
        "diagnostic_verify_mean_ms",
        "errors",
    ]

    lines = [",".join(headers)]
    for row in summary_rows:
        values = [str(row.get(header, "")) for header in headers]
        lines.append(",".join(values))

    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, csv_path


def mean_stage(stage_values: list[float]) -> float:
    """Promedio redondeado de latencias de una etapa de diagnóstico."""
    if not stage_values:
        return 0.0
    return round(statistics.fmean(stage_values), 2)


def parse_args() -> argparse.Namespace:
    """Procesa argumentos de línea de comandos para benchmark live."""
    parser = argparse.ArgumentParser(
        description="Benchmark live de latencia para endpoints /query y /inventory/query",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--sleep-between-ms", type=float, default=30.0)
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--inventory-page-size", type=int, default=80)
    parser.add_argument(
        "--query-general",
        default="explica la arquitectura de autenticacion",
    )
    parser.add_argument(
        "--query-module",
        default="cuales son los modulos del repositorio",
    )
    parser.add_argument(
        "--query-inventory",
        default="cuales son todos los controllers del modulo api",
    )
    parser.add_argument(
        "--query-inventory-explain",
        default=(
            "cuales son los componentes del modulo core y que funcion cumple cada uno"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark_reports",
    )
    return parser.parse_args()


def main() -> int:
    """Ejecuta benchmark live, resume métricas y escribe artefactos de reporte."""
    args = parse_args()
    if args.iterations <= 0:
        raise ValueError("iterations debe ser mayor a 0")
    if args.warmup < 0:
        raise ValueError("warmup no puede ser negativo")

    scenarios = build_scenarios(args)
    all_results: dict[str, Any] = {
        "meta": {
            "base_url": args.base_url,
            "repo_id": args.repo_id,
            "iterations": args.iterations,
            "warmup": args.warmup,
            "timeout_seconds": args.timeout_seconds,
            "top_n": args.top_n,
            "top_k": args.top_k,
            "inventory_page_size": args.inventory_page_size,
        },
        "scenarios": {},
    }

    summary_rows: list[dict[str, Any]] = []

    for scenario in scenarios:
        roundtrip_ms: list[float] = []
        errors: list[str] = []

        stage_total: list[float] = []
        stage_hybrid: list[float] = []
        stage_rerank: list[float] = []
        stage_graph: list[float] = []
        stage_context: list[float] = []
        stage_answer: list[float] = []
        stage_verify: list[float] = []

        total_runs = args.warmup + args.iterations
        for index in range(total_runs):
            try:
                elapsed_ms, payload = call_endpoint(
                    base_url=args.base_url,
                    scenario=scenario,
                    timeout_seconds=args.timeout_seconds,
                )
                diagnostics = payload.get("diagnostics", {})
                timings = diagnostics.get("stage_timings_ms", {})

                is_warmup = index < args.warmup
                if is_warmup:
                    continue

                roundtrip_ms.append(elapsed_ms)
                if isinstance(timings, dict):
                    if "total_ms" in timings:
                        stage_total.append(float(timings.get("total_ms", 0.0)))
                    if "hybrid_search_ms" in timings:
                        stage_hybrid.append(float(timings.get("hybrid_search_ms", 0.0)))
                    if "rerank_ms" in timings:
                        stage_rerank.append(float(timings.get("rerank_ms", 0.0)))
                    if "graph_expand_ms" in timings:
                        stage_graph.append(float(timings.get("graph_expand_ms", 0.0)))
                    if "context_assembly_ms" in timings:
                        stage_context.append(float(timings.get("context_assembly_ms", 0.0)))
                    if "llm_answer_ms" in timings:
                        stage_answer.append(float(timings.get("llm_answer_ms", 0.0)))
                    if "llm_verify_ms" in timings:
                        stage_verify.append(float(timings.get("llm_verify_ms", 0.0)))
            except Exception as exc:
                if index >= args.warmup:
                    errors.append(str(exc).replace("\n", " "))

            if args.sleep_between_ms > 0:
                sleep(args.sleep_between_ms / 1000.0)

        stats = summarize(roundtrip_ms)
        scenario_report = {
            "roundtrip_ms": roundtrip_ms,
            "stats": stats,
            "diagnostics_stage_ms": {
                "total_mean_ms": mean_stage(stage_total),
                "hybrid_mean_ms": mean_stage(stage_hybrid),
                "rerank_mean_ms": mean_stage(stage_rerank),
                "graph_mean_ms": mean_stage(stage_graph),
                "context_mean_ms": mean_stage(stage_context),
                "answer_mean_ms": mean_stage(stage_answer),
                "verify_mean_ms": mean_stage(stage_verify),
            },
            "errors": errors,
        }
        all_results["scenarios"][scenario.name] = scenario_report

        summary_rows.append(
            {
                "scenario": scenario.name,
                "count": int(stats["count"]),
                "mean_ms": stats["mean_ms"],
                "min_ms": stats["min_ms"],
                "max_ms": stats["max_ms"],
                "p50_ms": stats["p50_ms"],
                "p95_ms": stats["p95_ms"],
                "p99_ms": stats["p99_ms"],
                "diagnostic_total_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["total_mean_ms"],
                "diagnostic_hybrid_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["hybrid_mean_ms"],
                "diagnostic_rerank_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["rerank_mean_ms"],
                "diagnostic_graph_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["graph_mean_ms"],
                "diagnostic_context_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["context_mean_ms"],
                "diagnostic_answer_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["answer_mean_ms"],
                "diagnostic_verify_mean_ms": scenario_report[
                    "diagnostics_stage_ms"
                ]["verify_mean_ms"],
                "errors": len(errors),
            }
        )

    output_dir = Path(args.output_dir)
    json_path, csv_path = write_reports(
        output_dir=output_dir,
        raw_results=all_results,
        summary_rows=summary_rows,
    )

    print("Benchmark live completado")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    for row in summary_rows:
        print(
            f"- {row['scenario']}: mean={row['mean_ms']} ms, "
            f"p95={row['p95_ms']} ms, p99={row['p99_ms']} ms, "
            f"errors={row['errors']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
