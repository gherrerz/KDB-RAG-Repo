"""Punto de entrada para iniciar la API HTTP del proyecto."""

from __future__ import annotations

import argparse

import uvicorn

from src.coderag.api.server import app


def build_parser() -> argparse.ArgumentParser:
    """Construye parser CLI para ejecutar la API con uvicorn."""
    parser = argparse.ArgumentParser(
        description=(
            "Inicia la API HTTP de Coderag usando la aplicación FastAPI "
            "publicada en src.main:app."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Activa autoreload para desarrollo local.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Ejecuta el servidor uvicorn con opciones de línea de comando."""
    args = build_parser().parse_args(argv)
    uvicorn.run(
        "src.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
