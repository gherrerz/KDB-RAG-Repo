"""Pruebas de helpers de estado de acciones en MainWindow."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

from coderag.ui.main_window import MainWindow
from coderag.ui.provider_action_state import ActionState


class _FakeResponse:
    """Respuesta HTTP simulada para inicialización de ventana."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def qapp() -> QApplication:
    """Asegura instancia QApplication para tests UI."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _build_window(monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """Construye MainWindow con endpoints mínimos mockeados."""

    def _fake_get(url: str, timeout: int):  # noqa: ARG001
        if url.endswith("/repos"):
            return _FakeResponse({"repo_ids": ["repo-a"]})
        if url.endswith("/repos/repo-a/status"):
            return _FakeResponse({"query_ready": True, "warnings": []})
        return _FakeResponse({})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "get", _fake_get)
    return MainWindow()


def test_apply_ingest_action_state_sets_button_and_hint(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Helper aplica enabled/tooltip/hint de ingesta de forma consistente."""
    window = _build_window(monkeypatch)
    state = ActionState(enabled=False, message="bloqueado por readiness")

    window._apply_ingest_action_state(state)

    assert window.ingestion_view.ingest_button.isEnabled() is False
    assert window.ingestion_view.ingest_button.toolTip() == "bloqueado por readiness"
    assert window.ingestion_view.ingest_action_hint.text() == "bloqueado por readiness"


def test_apply_query_action_state_sets_button_and_hint(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Helper aplica enabled/tooltip/hint de consulta de forma consistente."""
    window = _build_window(monkeypatch)
    state = ActionState(enabled=True, message="listo")

    window._apply_query_action_state(state)

    assert window.query_view.query_button.isEnabled() is True
    assert window.query_view.query_button.toolTip() == "listo"
    assert window.query_view.query_action_hint.text() == "listo"


def test_finalize_job_poll_clears_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Helper de cierre de polling resetea flags internas."""
    window = _build_window(monkeypatch)
    window._job_poll_enabled = True
    window._active_job_id = "abc"

    window._finalize_job_poll()

    assert window._job_poll_enabled is False
    assert window._active_job_id is None
