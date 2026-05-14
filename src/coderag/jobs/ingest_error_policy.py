"""Política de clasificación de errores de ingesta para reintentos."""

NON_RETRYABLE_INGEST_ERROR_MARKERS = (
    "authentication failed",
    "permission denied",
    "not found",
    "repository not found",
    "no se pudo clonar",
    "commit solicitado no está disponible",
    "invalid",
    "forbidden",
    "unauthorized",
)

TRANSIENT_INGEST_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "temporary failure",
    "connection refused",
    "connection reset",
    "service unavailable",
    "name resolution",
    "too many requests",
    "rate limit",
    "429",
    "deadlock",
    "database is locked",
    "connection aborted",
)


def _contains_any_marker(message: str, markers: tuple[str, ...]) -> bool:
    """Indica si el mensaje contiene algún marcador de la política."""
    normalized = (message or "").lower()
    return any(marker in normalized for marker in markers)


def is_non_retryable_ingest_error(message: str) -> bool:
    """Detecta errores permanentes donde reintentar no agrega valor."""
    return _contains_any_marker(message, NON_RETRYABLE_INGEST_ERROR_MARKERS)


def is_transient_ingest_error(message: str) -> bool:
    """Detecta errores transitorios típicos de red o infraestructura."""
    return _contains_any_marker(message, TRANSIENT_INGEST_ERROR_MARKERS)


def is_retryable_ingest_error(message: str) -> bool:
    """Clasifica si un error de ingesta amerita reintento automático."""
    if is_non_retryable_ingest_error(message):
        return False
    return is_transient_ingest_error(message)


def should_retry_failed_ingest_job(
    *,
    retryable_error: bool,
    retry_transient_only: bool,
) -> bool:
    """Decide si un fallo final de ingesta debe relanzarse en modo RQ."""
    return retryable_error or not retry_transient_only