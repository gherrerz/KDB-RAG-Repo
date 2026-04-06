"""Asistentes de configuración de registro para la aplicación."""

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure el registro global con un formateador conciso."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
