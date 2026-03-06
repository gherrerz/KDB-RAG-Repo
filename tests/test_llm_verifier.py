"""Tests for verifier result parsing robustness."""

from coderag.llm.openai_client import _is_verifier_result_valid


def test_verifier_accepts_accented_and_punctuated_valid_tokens() -> None:
    """Parses VALIDO verdict even with accents and punctuation noise."""
    assert _is_verifier_result_valid("VÁLIDO")
    assert _is_verifier_result_valid("VALIDO.")
    assert _is_verifier_result_valid("Resultado: válido")


def test_verifier_rejects_invalid_markers() -> None:
    """Rejects responses that contain invalid verdict markers."""
    assert not _is_verifier_result_valid("INVALIDO")
    assert not _is_verifier_result_valid("invalid")
    assert not _is_verifier_result_valid("hallucination detected")