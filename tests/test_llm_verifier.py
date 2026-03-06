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