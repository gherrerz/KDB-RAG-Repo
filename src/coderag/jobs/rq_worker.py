"""Proceso worker RQ para ejecutar jobs de ingesta desacoplados de la API."""

import argparse

from redis import Redis
from rq import SimpleWorker
from rq.timeouts import TimerDeathPenalty

from src.coderag.core.logging import configure_logging
from src.coderag.core.settings import get_settings


class CoderagSimpleWorker(SimpleWorker):
    """Worker simple con death penalty compatible con plataforma."""

    death_penalty_class = TimerDeathPenalty


def run_worker(*, queue_name: str | None = None, burst: bool = False) -> None:
    """Ejecuta un worker RQ ligado a la cola de ingesta configurada."""
    configure_logging()
    settings = get_settings()

    target_queue = queue_name or settings.ingestion_queue_name
    redis_conn = Redis.from_url(settings.redis_url)

    worker = CoderagSimpleWorker(
        [target_queue],
        connection=redis_conn,
    )
    worker.work(burst=burst, with_scheduler=False, logging_level="INFO")


def _build_parser() -> argparse.ArgumentParser:
    """Construye parser CLI para ejecutar worker desde módulo Python."""
    parser = argparse.ArgumentParser(description="Worker RQ para ingesta coderag")
    parser.add_argument(
        "--queue",
        default=None,
        help="Nombre de cola RQ a consumir (default: INGESTION_QUEUE_NAME).",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Procesa jobs pendientes y termina.",
    )
    return parser


def main() -> None:
    """Punto de entrada CLI del worker RQ."""
    parser = _build_parser()
    args = parser.parse_args()
    run_worker(queue_name=args.queue, burst=args.burst)


if __name__ == "__main__":
    main()
