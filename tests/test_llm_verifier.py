"""Pruebas de robustez del análisis de resultados del verificador."""

from coderag.llm.openai_client import _is_verifier_result_valid


def test_verifier_accepts_accented_and_punctuated_valid_tokens() -> None:
    """Analiza el veredicto VALIDO incluso con acentos y ruido de puntuación."""
    assert _is_verifier_result_valid("VÁLIDO")
    assert _is_verifier_result_valid("VALIDO.")
    assert _is_verifier_result_valid("Resultado: válido")


def test_verifier_rejects_invalid_markers() -> None:
    """Rechaza respuestas que contienen marcadores de veredicto inválidos."""
    assert not _is_verifier_result_valid("INVALIDO")
    assert not _is_verifier_result_valid("invalid")
    assert not _is_verifier_result_valid("hallucination detected")


def test_verifier_accepts_negated_invalid_statement() -> None:
    """Acepta veredictos que niegan explícitamente la invalidez."""
    assert _is_verifier_result_valid("No hay evidencia de contenido inválido.")


def test_verifier_accepts_grounded_detailed_statement() -> None:
    """Acepta dictámenes detallados sin token exacto 'valid' cuando hay soporte claro."""
    value = (
        "La respuesta esta sustentada por evidencia suficiente y es "
        "consistent con el contexto provisto."
    )
    assert _is_verifier_result_valid(value)