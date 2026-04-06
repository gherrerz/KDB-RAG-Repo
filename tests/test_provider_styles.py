"""Pruebas del bloque CSS compartido para feedback de providers."""

from src.coderag.ui.provider_styles import PROVIDER_FEEDBACK_STYLES


def test_provider_feedback_styles_contains_expected_selectors() -> None:
    """Verifica que el bloque compartido incluye selectores clave de feedback."""
    css = PROVIDER_FEEDBACK_STYLES

    assert "QLabel#providerWarning" in css
    assert "QLabel#providerStatusChip" in css
    assert "QCheckBox#forceFallbackCheck" in css
    assert "QLabel#actionHint" in css
