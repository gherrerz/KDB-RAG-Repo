"""Simula rollback semantico reiniciando API y mide tiempos reales."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import urllib.request

import requests


@dataclass(frozen=True)
class SimulationResult:
    """Resultado de tiempos observados en una simulacion de rollback."""

    startup_semantic_on_seconds: float
    rollback_to_semantic_off_seconds: float
    post_rollback_health_seconds: float
    post_rollback_smoke_query_seconds: float
    post_rollback_semantic_query_enabled: bool | None
    post_rollback_fallback_reason: str | None


def parse_args() -> argparse.Namespace:
    """Parsea argumentos CLI para ejecutar la simulacion."""
    parser = argparse.ArgumentParser(
        description=(
            "Simula rollback semantico: arranca API con semantic on, "
            "reinicia con semantic off y mide recuperacion."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--python-exe", default=r".\.venv\Scripts\python.exe")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--query-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=15)
    parser.add_argument("--output-dir", default="benchmark_reports")
    return parser.parse_args()


def wait_for_health(base_url: str, timeout_seconds: float) -> bool:
    """Espera hasta que /health/storage responda 200 o expire timeout."""
    deadline = time.time() + timeout_seconds
    url = f"{base_url}/health/storage"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def start_api_process(
    *,
    python_exe: str,
    host: str,
    port: int,
    semantic_query_enabled: bool,
) -> subprocess.Popen[Any]:
    """Arranca uvicorn en segundo plano con flags controladas por entorno."""
    env = os.environ.copy()
    env["HEALTH_CHECK_OPENAI"] = "false"
    env["SEMANTIC_GRAPH_QUERY_ENABLED"] = (
        "true" if semantic_query_enabled else "false"
    )
    return subprocess.Popen(
        [
            python_exe,
            "-m",
            "uvicorn",
            "src.coderag.api.server:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_api_process(process: subprocess.Popen[Any]) -> None:
    """Detiene proceso uvicorn de forma segura evitando huella residual."""
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def run_smoke_query(
    *,
    base_url: str,
    repo_id: str,
    query_timeout_seconds: float,
    top_n: int,
    top_k: int,
) -> tuple[float, bool | None, str | None]:
    """Ejecuta consulta smoke post-rollback y retorna tiempo y diagnostics."""
    payload = {
        "repo_id": repo_id,
        "query": "explica la arquitectura general del sistema y sus capas principales",
        "top_n": top_n,
        "top_k": top_k,
    }
    started_at = time.time()
    response = requests.post(
        f"{base_url}/query",
        json=payload,
        timeout=query_timeout_seconds,
    )
    elapsed_seconds = time.time() - started_at
    if response.status_code != 200:
        raise RuntimeError(
            f"Smoke query fallo con HTTP {response.status_code}: {response.text[:500]}"
        )

    diagnostics = response.json().get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    return (
        elapsed_seconds,
        diagnostics.get("semantic_query_enabled"),
        diagnostics.get("fallback_reason"),
    )


def write_report(
    *,
    output_dir: Path,
    host: str,
    port: int,
    result: SimulationResult,
) -> Path:
    """Escribe artefacto JSON con resultados de simulacion."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"rollback_simulation_{timestamp}.json"
    payload = {
        "meta": {
            "simulation_date": datetime.now().strftime("%Y-%m-%d"),
            "method": "semantic_on_to_off_restart_same_port",
            "host": host,
            "port": port,
        },
        "results": {
            "startup_semantic_on_seconds": result.startup_semantic_on_seconds,
            "rollback_to_semantic_off_seconds": (
                result.rollback_to_semantic_off_seconds
            ),
            "post_rollback_health_seconds": result.post_rollback_health_seconds,
            "post_rollback_smoke_query_seconds": (
                result.post_rollback_smoke_query_seconds
            ),
            "post_rollback_semantic_query_enabled": (
                result.post_rollback_semantic_query_enabled
            ),
            "post_rollback_fallback_reason": result.post_rollback_fallback_reason,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    """Ejecuta simulacion completa de rollback con medicion de tiempos."""
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"

    process_on = start_api_process(
        python_exe=args.python_exe,
        host=args.host,
        port=args.port,
        semantic_query_enabled=True,
    )
    try:
        started_on = time.time()
        if not wait_for_health(base_url, args.timeout_seconds):
            raise RuntimeError("No se obtuvo health 200 para instancia semantic-on")
        startup_on_seconds = round(time.time() - started_on, 3)

        rollback_started = time.time()
        stop_api_process(process_on)

        process_off = start_api_process(
            python_exe=args.python_exe,
            host=args.host,
            port=args.port,
            semantic_query_enabled=False,
        )
        try:
            health_started = time.time()
            if not wait_for_health(base_url, args.timeout_seconds):
                raise RuntimeError(
                    "No se obtuvo health 200 para instancia semantic-off"
                )
            post_rollback_health_seconds = round(time.time() - health_started, 3)

            (
                smoke_query_seconds,
                post_semantic_enabled,
                post_fallback_reason,
            ) = run_smoke_query(
                base_url=base_url,
                repo_id=args.repo_id,
                query_timeout_seconds=args.query_timeout_seconds,
                top_n=args.top_n,
                top_k=args.top_k,
            )

            result = SimulationResult(
                startup_semantic_on_seconds=startup_on_seconds,
                rollback_to_semantic_off_seconds=round(
                    time.time() - rollback_started,
                    3,
                ),
                post_rollback_health_seconds=post_rollback_health_seconds,
                post_rollback_smoke_query_seconds=round(smoke_query_seconds, 3),
                post_rollback_semantic_query_enabled=post_semantic_enabled,
                post_rollback_fallback_reason=post_fallback_reason,
            )
            out_path = write_report(
                output_dir=Path(args.output_dir),
                host=args.host,
                port=args.port,
                result=result,
            )

            print("Rollback simulation completed")
            print(f"JSON: {out_path}")
            print(
                "rollback_to_semantic_off_seconds="
                f"{result.rollback_to_semantic_off_seconds}"
            )
            print(
                "post_rollback_health_seconds="
                f"{result.post_rollback_health_seconds}"
            )
            print(
                "post_rollback_smoke_query_seconds="
                f"{result.post_rollback_smoke_query_seconds}"
            )
            print(
                "post_rollback_semantic_query_enabled="
                f"{result.post_rollback_semantic_query_enabled}"
            )
            return 0
        finally:
            stop_api_process(process_off)
    finally:
        # If semantic-on process is still alive (for failure paths), stop it.
        if process_on.poll() is None:
            stop_api_process(process_on)


if __name__ == "__main__":
    raise SystemExit(main())
