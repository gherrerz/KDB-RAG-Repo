"""Pruebas de helpers de estado de acciones en MainWindow."""

import sys

import pytest
import requests
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
        if "/repos/repo-a/status" in url:
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


def test_repo_switch_syncs_embedding_runtime_defaults(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Al cambiar repo, sincroniza provider/model de query con última ingesta conocida."""

    def _fake_get(url: str, timeout: int):  # noqa: ARG001
        if url.endswith("/repos"):
            return _FakeResponse({"repo_ids": ["repo-a", "repo-b"]})
        if "/repos/repo-a/status" in url:
            return _FakeResponse(
                {
                    "query_ready": True,
                    "warnings": [],
                    "last_embedding_provider": "openai",
                    "last_embedding_model": "text-embedding-3-small",
                }
            )
        if "/repos/repo-b/status" in url:
            return _FakeResponse(
                {
                    "query_ready": True,
                    "warnings": [],
                    "last_embedding_provider": "vertex_ai",
                    "last_embedding_model": "text-embedding-005",
                }
            )
        return _FakeResponse({})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "get", _fake_get)
    window = MainWindow()

    window.query_view.repo_id.setCurrentText("repo-b")

    assert window.query_view.get_embedding_provider() == "vertex"
    assert window.query_view.get_embedding_model() == "text-embedding-005"


def test_set_query_controls_enabled_toggles_delete_button(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """El botón de eliminación sigue el estado habilitado global de consulta."""
    window = _build_window(monkeypatch)

    window._set_query_controls_enabled(False)
    assert window.query_view.delete_repo_button.isEnabled() is False

    window._set_query_controls_enabled(True)
    assert window.query_view.delete_repo_button.isEnabled() is True


def test_timer_event_throttles_repeated_polling_failures(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Reduce ruido de polling cuando se repite el mismo timeout de red."""
    window = _build_window(monkeypatch)
    window._job_poll_enabled = True
    window._active_job_id = "job-1"

    import coderag.ui.main_window as module

    def _fake_get(url: str, timeout: float):  # noqa: ARG001
        if "/jobs/" in url:
            raise requests.Timeout("Read timed out")
        if url.endswith("/repos"):
            return _FakeResponse({"repo_ids": ["repo-a"]})
        if "/repos/repo-a/status" in url:
            return _FakeResponse({"query_ready": True, "warnings": []})
        return _FakeResponse({})

    monkeypatch.setattr(module.requests, "get", _fake_get)

    class _FakeEvent:
        def __init__(self, timer_id: int) -> None:
            self._timer_id = timer_id

        def timerId(self) -> int:  # noqa: N802
            return self._timer_id

    event = _FakeEvent(window._poll_timer_id)

    for _ in range(6):
        window.timerEvent(event)

    logs_text = window.ingestion_view.logs.toPlainText()
    assert "Polling falló (intentos=1)" in logs_text
    assert "Polling falló (intentos=5)" in logs_text
    assert "Polling falló (intentos=2)" not in logs_text
