"""Pruebas de helpers CSS compartidos para cards y chips de estado."""

from coderag.ui.card_styles import (
    frame_card_styles,
    status_chip_styles,
    title_subtitle_styles,
    top_card_styles,
)


def test_frame_card_styles_includes_all_selectors() -> None:
    """Genera selector combinado para todos los cards enviados."""
    css = frame_card_styles("one", "two")

    assert "QFrame#one" in css
    assert "QFrame#two" in css
    assert "border-radius: 12px" in css


def test_top_card_styles_sets_highlight_background() -> None:
    """Aplica fondo destacado para card superior."""
    css = top_card_styles("topCard")

    assert "QFrame#topCard" in css
    assert "#15243E" in css


def test_title_subtitle_styles_target_expected_labels() -> None:
    """Genera reglas para labels de titulo y subtitulo."""
    css = title_subtitle_styles("title", "subtitle")

    assert "QLabel#title" in css
    assert "QLabel#subtitle" in css
    assert "letter-spacing" in css


def test_status_chip_styles_with_center_alignment() -> None:
    """Incluye estados del chip y alineación opcional en centro."""
    css = status_chip_styles("status", center=True)

    assert "QLabel#status" in css
    assert "state=\"running\"" in css
    assert "qproperty-alignment: AlignCenter" in css
