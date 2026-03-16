"""Mensajes compartidos para feedback operativo de providers en UI."""


def ingest_ready_message() -> str:
    """Mensaje cuando ingesta esta habilitada y lista para ejecutar."""
    return "Listo para ingestar con la configuracion seleccionada."


def ingest_provider_not_ready_message(reason: str) -> str:
    """Mensaje cuando ingesta esta bloqueada por provider no listo."""
    return (
        "Provider de embeddings no listo "
        f"({reason}). Activa 'Forzar fallback' para habilitar Ingestar."
    )


def ingest_requires_repo_url_message() -> str:
    """Mensaje cuando falta la URL del repositorio para ingestar."""
    return "Repo URL es obligatorio"


def query_blocked_by_ingestion_message() -> str:
    """Mensaje cuando consulta esta bloqueada por ingesta en curso."""
    return "Consulta bloqueada mientras la ingesta esta en progreso."


def query_requires_repo_message() -> str:
    """Mensaje cuando no hay repositorio seleccionado para consultar."""
    return "Selecciona un repositorio para habilitar la consulta."


def query_requires_question_message() -> str:
    """Mensaje cuando no hay pregunta para consultar."""
    return "Escribe una pregunta para habilitar la consulta."


def query_ready_message() -> str:
    """Mensaje cuando consulta esta habilitada y lista para ejecutar."""
    return "Listo para consultar en el repositorio activo."


def query_provider_not_ready_message(details: str) -> str:
    """Mensaje cuando consulta esta bloqueada por providers no listos."""
    return (
        "Provider no listo "
        f"({details}). Activa 'Forzar fallback' para habilitar Consultar."
    )


def embedding_warning_unsupported(context: str) -> str:
    """Warning cuando provider de embeddings no tiene backend."""
    if context == "ingestion":
        return "Proveedor sin backend de embeddings; se usara fallback determinista."
    return "Embeddings: provider sin backend; se usara fallback determinista."


def embedding_warning_not_configured(context: str, reason: str) -> str:
    """Warning cuando provider de embeddings no esta configurado."""
    if context == "ingestion":
        return f"Provider no configurado ({reason})."
    return f"Embeddings: provider no configurado ({reason})."


def llm_warning_unsupported() -> str:
    """Warning cuando provider LLM no es soportado."""
    return "LLM: provider no soportado."


def llm_warning_not_configured(reason: str) -> str:
    """Warning cuando provider LLM no esta configurado."""
    return f"LLM: provider no configurado ({reason})."