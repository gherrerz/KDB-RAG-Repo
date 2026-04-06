"""Pruebas del bloque CSS base compartido para inputs y botones."""

from src.coderag.ui.base_styles import (
    BASE_BUTTON_STYLES,
    BASE_INPUT_STYLES,
    BASE_INPUT_STYLES_WITH_TEXTEDIT,
    BASE_WIDGET_TEXT_STYLES,
)


def test_base_widget_text_styles_contains_widget_selector() -> None:
    """Incluye estilo base de tipografía y color global de widgets."""
    assert "QWidget" in BASE_WIDGET_TEXT_STYLES
    assert "font-size" in BASE_WIDGET_TEXT_STYLES


def test_base_input_styles_contains_line_edit_and_combo() -> None:
    """Incluye reglas base para QLineEdit y QComboBox."""
    assert "QLineEdit, QComboBox" in BASE_INPUT_STYLES
    assert "QComboBox::down-arrow" in BASE_INPUT_STYLES


def test_base_input_styles_with_text_edit_contains_qtextedit() -> None:
    """Incluye variante con QTextEdit para vistas que la usan."""
    assert "QTextEdit" in BASE_INPUT_STYLES_WITH_TEXTEDIT


def test_base_button_styles_contains_common_button_states() -> None:
    """Incluye estado base, hover, flash y disabled de botones."""
    css = BASE_BUTTON_STYLES
    assert "QPushButton" in css
    assert "QPushButton:hover" in css
    assert "QPushButton[flash=\"true\"]" in css
    assert "QPushButton:disabled" in css
