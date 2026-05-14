"""Pruebas unitarias de la política de reintentos de ingesta."""

from coderag.jobs.ingest_error_policy import (
    is_non_retryable_ingest_error,
    is_retryable_ingest_error,
    is_transient_ingest_error,
    should_retry_failed_ingest_job,
)


def test_is_retryable_ingest_error_detects_transient_failures() -> None:
    """Marca como reintentables los fallos transitorios conocidos."""
    assert is_transient_ingest_error("Connection refused while cloning repo")
    assert is_retryable_ingest_error("Connection refused while cloning repo")


def test_is_retryable_ingest_error_rejects_permanent_failures() -> None:
    """No reintenta fallos permanentes de autenticación o acceso."""
    assert is_non_retryable_ingest_error("Authentication failed for origin")
    assert not is_retryable_ingest_error("Authentication failed for origin")


def test_is_retryable_ingest_error_prioritizes_permanent_markers() -> None:
    """Prioriza la señal permanente cuando el mensaje mezcla ambos tipos."""
    message = "Repository not found after timeout contacting remote"

    assert is_non_retryable_ingest_error(message)
    assert is_transient_ingest_error(message)
    assert not is_retryable_ingest_error(message)


def test_should_retry_failed_ingest_job_only_retries_transient_failures() -> None:
    """Con retry transitorio, solo relanza fallos marcados como reintentables."""
    assert should_retry_failed_ingest_job(
        retryable_error=True,
        retry_transient_only=True,
    )
    assert not should_retry_failed_ingest_job(
        retryable_error=False,
        retry_transient_only=True,
    )


def test_should_retry_failed_ingest_job_retries_all_when_configured() -> None:
    """Con retry-all habilitado, relanza cualquier fallo final de ingesta."""
    assert should_retry_failed_ingest_job(
        retryable_error=False,
        retry_transient_only=False,
    )