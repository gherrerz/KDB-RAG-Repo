"""Pruebas unitarias para helper compartido de feedback de providers."""

from coderag.ui.provider_feedback import (
    embedding_feedback_from_capability,
    llm_feedback_from_capability,
)


def test_embedding_feedback_ingestion_unsupported() -> None:
    """Marca warning reutilizable cuando embeddings no tiene backend."""
    warning, chip_state, chip_text = embedding_feedback_from_capability(
        {
            "supported": False,
            "configured": False,
            "reason": "provider_without_embedding_backend",
        },
        context="ingestion",
    )

    assert "fallback" in warning.lower()
    assert chip_state == "warning"
    assert "fallback" in chip_text.lower()


def test_embedding_feedback_query_not_configured() -> None:
    """Marca bloqueo cuando embeddings esta soportado pero no configurado."""
    warning, chip_state, chip_text = embedding_feedback_from_capability(
        {
            "supported": True,
            "configured": False,
            "reason": "missing_vertex_ai_api_key_or_project",
        },
        context="query",
    )

    assert "no configurado" in warning.lower()
    assert chip_state == "blocked"
    assert chip_text == "Embeddings: No listo"


def test_llm_feedback_not_supported() -> None:
    """Marca bloqueo explicito cuando provider LLM no es soportado."""
    warning, chip_state, chip_text = llm_feedback_from_capability(
        {
            "supported": False,
            "configured": False,
            "reason": "unsupported_provider",
        }
    )

    assert "no soportado" in warning.lower()
    assert chip_state == "blocked"
    assert chip_text == "LLM: No listo"


def test_llm_feedback_ready() -> None:
    """Marca estado listo cuando provider LLM esta soportado y configurado."""
    warning, chip_state, chip_text = llm_feedback_from_capability(
        {
            "supported": True,
            "configured": True,
            "reason": "ok",
        }
    )

    assert warning == ""
    assert chip_state == "ready"
    assert chip_text == "LLM: Listo"
