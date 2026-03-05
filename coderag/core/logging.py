"""Logging setup helpers for the application."""

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure global logging with a concise formatter."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
