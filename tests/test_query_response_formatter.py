"""Pruebas unitarias para formateadores de respuesta de consulta UI."""

from coderag.ui.query_response_formatter import (
    build_query_answer_text,
    build_repo_not_ready_message,
)


def test_build_query_answer_text_without_diagnostics() -> None:
    """Conserva respuesta base cuando no hay fallback_reason."""
    result = build_query_answer_text("respuesta", {"retrieved": 10})

    assert result == "respuesta"


def test_build_query_answer_text_with_fallback_reason() -> None:
    """Anexa bloque de diagnóstico cuando existe fallback_reason."""
    result = build_query_answer_text(
        "respuesta",
        {"fallback_reason": "verification_failed"},
    )

    assert "respuesta" in result
    assert "diagnóstico" in result
    assert "verification_failed" in result


def test_build_repo_not_ready_message_without_warnings() -> None:
    """Devuelve solo mensaje base cuando no hay advertencias."""
    result = build_repo_not_ready_message([])

    assert "no esta listo" in result.lower()
    assert "\n- " not in result


def test_build_repo_not_ready_message_limits_to_three_warnings() -> None:
    """Incluye hasta 3 advertencias para mantener legibilidad."""
    result = build_repo_not_ready_message(
        ["w1", "w2", "w3", "w4"],
    )

    assert "- w1" in result
    assert "- w2" in result
    assert "- w3" in result
    assert "- w4" not in result
