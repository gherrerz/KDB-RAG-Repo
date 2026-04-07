"""Pruebas de prioridad de espacio UI para los módulos de Consulta e Ingesta."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

import coderag.ui.ingestion_view as ingestion_view_module
import coderag.ui.query_view as query_view_module
from coderag.ui.ingestion_view import IngestionView
from coderag.ui.query_view import QueryView


@pytest.fixture
def qapp() -> QApplication:
    """Asegura instancia QApplication para widgets Qt en pruebas."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _prepare_query_view(view: QueryView, qapp: QApplication) -> None:
    """Inicializa tamaños del splitter de consulta sin mostrar ventana real."""
    _ = qapp
    view.query_splitter.resize(1000, 600)
    view._apply_query_splitter_sizes()


def _prepare_ingestion_view(view: IngestionView, qapp: QApplication) -> None:
    """Inicializa tamaños del splitter de ingesta sin mostrar ventana real."""
    _ = qapp
    view.ingestion_splitter.resize(1000, 600)
    view._apply_ingestion_splitter_sizes()


def test_query_layout_prioritizes_history_area(qapp: QApplication) -> None:
    """El splitter de consulta inicia favoreciendo el área de historial."""
    view = QueryView()
    _prepare_query_view(view, qapp)

    sizes = view.query_splitter.sizes()

    assert view.query_splitter.count() == 2
    assert view.history_output.minimumHeight() >= 260
    assert len(sizes) == 2
    assert sizes[1] >= sizes[0]


def test_query_config_section_can_collapse(qapp: QApplication) -> None:
    """La sección de configuración de consulta puede colapsarse y expandirse."""
    view = QueryView()
    _prepare_query_view(view, qapp)

    view.repo_toggle_button.setChecked(False)
    qapp.processEvents()

    assert view.repo_scroll.isHidden()
    assert view.repo_section.maximumHeight() < 120

    view.repo_toggle_button.setChecked(True)
    qapp.processEvents()

    assert not view.repo_scroll.isHidden()
    assert view.repo_section.maximumHeight() >= 1000


def test_ingestion_layout_prioritizes_logs_area(qapp: QApplication) -> None:
    """El splitter de ingesta inicia favoreciendo el panel de logs."""
    view = IngestionView()
    _prepare_ingestion_view(view, qapp)

    sizes = view.ingestion_splitter.sizes()

    assert view.ingestion_splitter.count() == 2
    assert view.logs.minimumHeight() >= 280
    assert len(sizes) == 2
    assert sizes[1] >= sizes[0]


def test_ingestion_form_section_can_collapse(qapp: QApplication) -> None:
    """La sección de formulario de ingesta puede colapsarse y expandirse."""
    view = IngestionView()
    _prepare_ingestion_view(view, qapp)

    view.form_toggle_button.setChecked(False)
    qapp.processEvents()

    assert view.form_scroll.isHidden()
    assert view.form_section.maximumHeight() < 120

    view.form_toggle_button.setChecked(True)
    qapp.processEvents()

    assert not view.form_scroll.isHidden()
    assert view.form_section.maximumHeight() >= 1000


def test_query_layout_preferences_persist(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    """Consulta guarda y restaura estado de sección y tamaños de splitter."""

    class _FakeSettings:
        _store: dict[str, object] = {}

        def __init__(self, org: str, app: str) -> None:
            _ = (org, app)

        def value(self, key: str, default=None, type=None):  # noqa: A002
            _ = type
            return self._store.get(key, default)

        def setValue(self, key: str, value) -> None:  # noqa: ANN001
            self._store[key] = value

    monkeypatch.setattr(query_view_module, "QSettings", _FakeSettings)

    first = QueryView()
    _prepare_query_view(first, qapp)
    first.query_splitter.setSizes([210, 790])
    first.repo_toggle_button.setChecked(False)
    first._persist_layout_preferences()

    second = QueryView()
    _prepare_query_view(second, qapp)

    assert second.repo_toggle_button.isChecked() is False
    assert second.repo_scroll.isHidden()
    assert second._saved_query_splitter_sizes is not None
    assert len(second._saved_query_splitter_sizes) == 2
    assert second._saved_query_splitter_sizes[0] == 210
    assert second._saved_query_splitter_sizes[1] > 0


def test_ingestion_layout_preferences_persist(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Ingesta guarda y restaura estado de sección y tamaños de splitter."""

    class _FakeSettings:
        _store: dict[str, object] = {}

        def __init__(self, org: str, app: str) -> None:
            _ = (org, app)

        def value(self, key: str, default=None, type=None):  # noqa: A002
            _ = type
            return self._store.get(key, default)

        def setValue(self, key: str, value) -> None:  # noqa: ANN001
            self._store[key] = value

    monkeypatch.setattr(ingestion_view_module, "QSettings", _FakeSettings)

    first = IngestionView()
    _prepare_ingestion_view(first, qapp)
    first.ingestion_splitter.setSizes([220, 780])
    first.form_toggle_button.setChecked(False)
    first._persist_layout_preferences()

    second = IngestionView()
    _prepare_ingestion_view(second, qapp)

    assert second.form_toggle_button.isChecked() is False
    assert second.form_scroll.isHidden()
    assert second._saved_ingestion_splitter_sizes is not None
    assert len(second._saved_ingestion_splitter_sizes) == 2
    assert second._saved_ingestion_splitter_sizes[0] == 220
    assert second._saved_ingestion_splitter_sizes[1] > 0
