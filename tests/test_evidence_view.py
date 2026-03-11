"""Pruebas de microinteracciones en panel de evidencia."""

import sys

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from coderag.ui.evidence_view import EvidenceView


@pytest.fixture
def qapp() -> QApplication:
    """Asegura una instancia de QApplication para widgets Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _citation(path: str, start: int, end: int, score: float = 0.9) -> dict:
    """Construye una cita de prueba con forma esperada por la vista."""
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "score": score,
        "reason": "test",
    }


def test_set_citations_triggers_panel_pulse(qapp: QApplication) -> None:
    """Al refrescar citas se activa pulso temporal en título y card."""
    view = EvidenceView()
    view.set_citations([_citation("a.py", 1, 3)])

    assert view.title_label.property("pulse") == "true"
    assert view.card.property("pulse") == "true"

    QTest.qWait(230)

    assert view.title_label.property("pulse") == "false"
    assert view.card.property("pulse") == "false"


def test_only_new_rows_get_temporary_highlight(qapp: QApplication) -> None:
    """En updates sucesivos solo las filas nuevas se resaltan temporalmente."""
    view = EvidenceView()

    first = [_citation("a.py", 1, 3)]
    view.set_citations(first)
    QTest.qWait(360)

    second = [_citation("a.py", 1, 3), _citation("b.py", 10, 12)]
    view.set_citations(second)

    existing_color = view.table.item(0, 0).background().color()
    new_color = view.table.item(1, 0).background().color()

    assert existing_color != new_color

    QTest.qWait(360)

    cleared_existing = view.table.item(0, 0).background().color()
    cleared_new = view.table.item(1, 0).background().color()
    assert cleared_existing == cleared_new
