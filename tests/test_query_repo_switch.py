"""Pruebas de limpieza de chat al cambiar repositorio en la vista de consulta."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

from coderag.ui.main_window import MainWindow


class _FakeResponse:
    """Respuesta HTTP simulada para endpoints de catálogo de repos."""

    def __init__(self, payload: dict) -> None:
        """Guarda payload JSON para consumo en pruebas."""
        self._payload = payload

    def raise_for_status(self) -> None:
        """No-op para simular respuestas exitosas."""

    def json(self) -> dict:
        """Devuelve payload JSON configurado para la prueba."""
        return self._payload


@pytest.fixture
def qapp() -> QApplication:
    """Asegura una instancia de QApplication para widgets Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> MainWindow:
    """Crea ventana principal con endpoint /repos simulado."""
    state = {"repo_ids": ["repo-a", "repo-b"]}

    def _fake_get(url: str, timeout: int) -> _FakeResponse:  # noqa: ARG001
        return _FakeResponse({"repo_ids": list(state["repo_ids"])})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "get", _fake_get)
    created = MainWindow()
    created._test_repo_state = state  # type: ignore[attr-defined]
    return created


def _add_history_and_evidence(created: MainWindow) -> None:
    """Agrega contenido previo para verificar que se limpia al cambiar repo."""
    created.query_view.append_user_message("pregunta previa")
    created.query_view.append_assistant_message("respuesta previa")
    created.query_view.query_input.setText("texto pendiente")
    created.evidence_view.set_citations(
        [
            {
                "path": "a.py",
                "start_line": 1,
                "end_line": 2,
                "score": 0.9,
                "reason": "test",
            }
        ]
    )


def test_manual_repo_switch_clears_chat_and_evidence(window: MainWindow) -> None:
    """Al cambiar repo manualmente se vacía historial, input y tabla de evidencia."""
    _add_history_and_evidence(window)

    assert window.query_view.history_output.toPlainText().strip()
    assert window.evidence_view.table.rowCount() == 1

    window.query_view.repo_id.setCurrentText("repo-b")

    assert window.query_view.history_output.toPlainText() == ""
    assert window.query_view.get_question_text() == ""
    assert window.evidence_view.table.rowCount() == 0



def test_refresh_repo_switch_clears_chat_and_evidence(window: MainWindow) -> None:
    """Si refresh cambia selección de repo también se reinicia la conversación."""
    _add_history_and_evidence(window)

    state = window._test_repo_state  # type: ignore[attr-defined]
    state["repo_ids"] = ["repo-b"]

    window._refresh_repo_ids(log_on_error=True)

    assert window.query_view.get_repo_id_text() == "repo-b"
    assert window.query_view.history_output.toPlainText() == ""
    assert window.evidence_view.table.rowCount() == 0
