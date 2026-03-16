"""Pruebas unitarias para catálogo centralizado de mensajes de UI."""

from coderag.ui.provider_messages import (
    embedding_warning_not_configured,
    embedding_warning_unsupported,
    ingest_provider_not_ready_message,
    query_provider_not_ready_message,
)


def test_ingest_provider_not_ready_message_contains_reason() -> None:
    """Incluye reason y acción de fallback para ingesta."""
    message = ingest_provider_not_ready_message("missing_gemini_api_key")

    assert "missing_gemini_api_key" in message
    assert "forzar fallback" in message.lower()


def test_query_provider_not_ready_message_contains_details() -> None:
    """Incluye detalles compuestos y acción de fallback para consulta."""
    details = "embeddings=missing_key, llm=missing_key"
    message = query_provider_not_ready_message(details)

    assert details in message
    assert "forzar fallback" in message.lower()


def test_embedding_warning_by_context() -> None:
    """Mantiene variante de warning para ingestion y query."""
    ingestion_message = embedding_warning_unsupported("ingestion")
    query_message = embedding_warning_unsupported("query")

    assert "proveedor" in ingestion_message.lower()
    assert "embeddings:" in query_message.lower()


def test_embedding_warning_not_configured_by_context() -> None:
    """Mantiene formato de no configurado por contexto."""
    reason = "missing_vertex_ai_api_key_or_project"

    ingestion_message = embedding_warning_not_configured("ingestion", reason)
    query_message = embedding_warning_not_configured("query", reason)

    assert reason in ingestion_message
    assert reason in query_message
    assert ingestion_message.startswith("Provider")
    assert query_message.startswith("Embeddings")
