"""Pruebas de guardrails preventivos con opción Forzar fallback."""

import sys

import pytest
import requests
from PySide6.QtWidgets import QApplication

from coderag.ui.main_window import MainWindow


class _FakeResponse:
    """Respuesta HTTP simulada simple."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def qapp() -> QApplication:
    """Asegura una instancia de QApplication para pruebas UI."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _build_window(monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """Crea ventana con requests.get controlado para catálogo y readiness."""

    def _fake_get(url: str, timeout: int):  # noqa: ARG001
        if url.endswith("/repos"):
            return _FakeResponse({"repo_ids": ["repo-a"]})
        if "/repos/repo-a/status" in url:
            return _FakeResponse({"query_ready": True, "warnings": []})
        return _FakeResponse({})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "get", _fake_get)
    return MainWindow()


def test_query_blocks_when_provider_not_ready_without_force(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Si provider no está listo y no se activa fallback, bloquea consulta."""
    window = _build_window(monkeypatch)

    monkeypatch.setattr(
        window.query_view,
        "is_embedding_provider_ready",
        lambda: (False, "missing_gemini_api_key"),
    )
    monkeypatch.setattr(
        window.query_view,
        "is_llm_provider_ready",
        lambda: (True, "ok"),
    )

    called = {"post": False}

    def _fake_post(*args, **kwargs):  # noqa: ANN002, ANN003
        called["post"] = True
        return _FakeResponse({"answer": "ok", "citations": [], "diagnostics": {}})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("hola")
    window.query_view.force_fallback.setChecked(False)
    window._on_query()

    assert called["post"] is False
    assert "forzar fallback" in window.query_view.history_output.toPlainText().lower()


def test_query_allows_when_force_fallback_enabled(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Con forzar fallback activo, permite consultar aun con provider no listo."""
    window = _build_window(monkeypatch)

    monkeypatch.setattr(
        window.query_view,
        "is_embedding_provider_ready",
        lambda: (False, "missing_gemini_api_key"),
    )
    monkeypatch.setattr(
        window.query_view,
        "is_llm_provider_ready",
        lambda: (False, "missing_anthropic_api_key"),
    )

    called = {"post": False}

    def _fake_post(*args, **kwargs):  # noqa: ANN002, ANN003
        called["post"] = True
        return _FakeResponse({"answer": "ok", "citations": [], "diagnostics": {}})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("hola")
    window.query_view.force_fallback.setChecked(True)
    window._on_query()

    assert called["post"] is True


def test_query_button_is_disabled_with_contextual_tooltip_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Deshabilita Consultar y muestra causa hasta que se active fallback."""
    window = _build_window(monkeypatch)

    monkeypatch.setattr(
        window.query_view,
        "is_embedding_provider_ready",
        lambda: (False, "missing_vertex_ai_api_key_or_project"),
    )
    monkeypatch.setattr(
        window.query_view,
        "is_llm_provider_ready",
        lambda: (True, "ok"),
    )

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("consulta")
    window.query_view.force_fallback.setChecked(False)
    window._update_query_action_state()

    assert window.query_view.query_button.isEnabled() is False
    tooltip = window.query_view.query_button.toolTip().lower()
    assert "forzar fallback" in tooltip
    assert "embeddings=" in tooltip
    hint = window.query_view.query_action_hint.text().lower()
    assert "forzar fallback" in hint
    assert "embeddings=" in hint

    window.query_view.force_fallback.setChecked(True)
    window._update_query_action_state()

    assert window.query_view.query_button.isEnabled() is True
    assert "listo para consultar" in window.query_view.query_action_hint.text().lower()


def test_ingest_button_is_disabled_with_contextual_tooltip_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Deshabilita Ingestar y explica accion correctiva cuando no hay readiness."""
    window = _build_window(monkeypatch)

    monkeypatch.setattr(
        window.ingestion_view,
        "is_embedding_provider_ready",
        lambda: (False, "provider_without_embedding_backend"),
    )

    window.ingestion_view.force_fallback.setChecked(False)
    window._update_ingest_action_state()

    assert window.ingestion_view.ingest_button.isEnabled() is False
    tooltip = window.ingestion_view.ingest_button.toolTip().lower()
    assert "forzar fallback" in tooltip
    assert "provider_without_embedding_backend" in tooltip
    hint = window.ingestion_view.ingest_action_hint.text().lower()
    assert "forzar fallback" in hint
    assert "provider_without_embedding_backend" in hint

    window.ingestion_view.force_fallback.setChecked(True)
    window._update_ingest_action_state()

    assert window.ingestion_view.ingest_button.isEnabled() is True
    assert "listo para ingestar" in window.ingestion_view.ingest_action_hint.text().lower()


def test_query_profile_profundo_uses_expanded_payload_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Perfil profundo amplía alcance de retrieval y timeout del request."""
    window = _build_window(monkeypatch)

    captured: dict[str, object] = {"json": None, "timeout": None}

    def _fake_post(url: str, json: dict, timeout: float):  # noqa: ANN001
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse({"answer": "ok", "citations": [], "diagnostics": {}})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("explica el repo")
    window.query_view.query_profile.setCurrentText("profundo")

    window._on_query()

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["top_n"] == 120
    assert payload["top_k"] == 30
    assert float(captured["timeout"]) >= 120.0


def test_query_profile_change_resets_top_n_top_k_inputs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Cambiar perfil restablece top-n/top-k a defaults del perfil seleccionado."""
    window = _build_window(monkeypatch)

    window.query_view.query_profile.setCurrentText("rapido")
    assert window.query_view.get_top_n() == 40
    assert window.query_view.get_top_k() == 10

    window.query_view.set_query_limits(top_n=61, top_k=17)
    assert window.query_view.get_top_n() == 61
    assert window.query_view.get_top_k() == 17

    window.query_view.query_profile.setCurrentText("profundo")
    assert window.query_view.get_top_n() == 120
    assert window.query_view.get_top_k() == 30


def test_query_payload_uses_top_n_top_k_values_edited_in_ui(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """La consulta usa top-n/top-k editados en pantalla y no solo defaults."""
    window = _build_window(monkeypatch)

    captured: dict[str, object] = {"json": None}

    def _fake_post(url: str, json: dict, timeout: float):  # noqa: ANN001
        captured["json"] = json
        return _FakeResponse({"answer": "ok", "citations": [], "diagnostics": {}})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("resumen")
    window.query_view.query_profile.setCurrentText("balanceado")
    window.query_view.set_query_limits(top_n=33, top_k=7)

    window._on_query()

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["top_n"] == 33
    assert payload["top_k"] == 7


def test_query_profile_rapido_timeout_retries_then_reports_error(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Perfil rapido aplica reintento y luego informa error si persiste timeout."""
    window = _build_window(monkeypatch)

    called = {"count": 0}

    def _fake_post(*args, **kwargs):  # noqa: ANN002, ANN003
        called["count"] += 1
        raise requests.Timeout("simulated timeout")

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("hola")
    window.query_view.query_profile.setCurrentText("rapido")

    window._on_query()

    assert called["count"] == 2
    assert "tras timeout inicial" in window.query_view.history_output.toPlainText().lower()


def test_query_profile_balanceado_retries_with_reduced_scope(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Perfil balanceado reintenta con menos candidatos tras timeout inicial."""
    window = _build_window(monkeypatch)

    calls: list[dict[str, object]] = []

    def _fake_post(url: str, json: dict, timeout: float):  # noqa: ANN001
        calls.append({"json": dict(json), "timeout": timeout})
        if len(calls) == 1:
            raise requests.Timeout("first timeout")
        return _FakeResponse(
            {"answer": "ok", "citations": [], "diagnostics": {}}
        )

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("hola")
    window.query_view.query_profile.setCurrentText("balanceado")

    window._on_query()

    assert len(calls) == 2
    first_payload = calls[0]["json"]
    second_payload = calls[1]["json"]
    assert isinstance(first_payload, dict)
    assert isinstance(second_payload, dict)
    assert first_payload["top_n"] == 80
    assert first_payload["top_k"] == 20
    assert second_payload["top_n"] == 40
    assert second_payload["top_k"] == 10
    assert "reintento automatico" in window.query_view.history_output.toPlainText().lower()


def test_query_status_preflight_sends_selected_embedding_configuration(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Incluye provider/model de embeddings seleccionados al validar status previo a query."""
    captured_urls: list[str] = []

    def _fake_get(url: str, timeout: int):  # noqa: ARG001
        captured_urls.append(url)
        if url.endswith("/repos"):
            return _FakeResponse({"repo_ids": ["repo-a"]})
        if "/repos/repo-a/status" in url:
            return _FakeResponse({"query_ready": True, "warnings": []})
        return _FakeResponse({})

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "get", _fake_get)
    window = MainWindow()

    def _fake_post(*args, **kwargs):  # noqa: ANN002, ANN003
        return _FakeResponse({"answer": "ok", "citations": [], "diagnostics": {}})

    monkeypatch.setattr(module.requests, "post", _fake_post)
    monkeypatch.setattr(
        window.query_view,
        "is_embedding_provider_ready",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        window.query_view,
        "is_llm_provider_ready",
        lambda: (True, "ok"),
    )

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.embedding_provider.setCurrentText("vertex")
    window.query_view.embedding_model.setCurrentText("text-embedding-005")
    window.query_view.query_input.setText("hola")

    window._on_query()

    assert any(
        "requested_embedding_provider=vertex" in url
        and "requested_embedding_model=text-embedding-005" in url
        for url in captured_urls
        if "/repos/repo-a/status" in url
    )


def test_query_retrieval_mode_routes_to_retrieval_endpoint_with_context_flag(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Cuando retrieval-only está activo, usa /query/retrieval e incluye include_context."""
    window = _build_window(monkeypatch)

    captured: dict[str, object] = {"url": "", "json": None}

    def _fake_post(url: str, json: dict, timeout: float):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = dict(json)
        return _FakeResponse(
            {
                "mode": "retrieval_only",
                "answer": "Modo retrieval-only (sin LLM)",
                "chunks": [],
                "citations": [],
                "statistics": {
                    "total_before_rerank": 0,
                    "total_after_rerank": 0,
                    "graph_nodes_count": 0,
                },
                "diagnostics": {},
                "context": "PATH: src/a.py",
            }
        )

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("resumen")
    window.query_view.retrieval_only_mode.setChecked(True)
    window.query_view.include_context.setChecked(True)

    window._on_query()

    assert str(captured["url"]).endswith("/query/retrieval")
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["include_context"] is True
    assert "llm_provider" not in payload
    assert "answer_model" not in payload


def test_query_retrieval_mode_allows_missing_llm_without_force_fallback(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """En retrieval-only no bloquea consulta por llm no listo cuando embeddings sí están listos."""
    window = _build_window(monkeypatch)

    monkeypatch.setattr(
        window.query_view,
        "is_embedding_provider_ready",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        window.query_view,
        "is_llm_provider_ready",
        lambda: (False, "missing_anthropic_api_key"),
    )

    called = {"post": False, "url": ""}

    def _fake_post(url: str, json: dict, timeout: float):  # noqa: ANN001
        called["post"] = True
        called["url"] = url
        return _FakeResponse(
            {
                "mode": "retrieval_only",
                "answer": "ok",
                "chunks": [],
                "citations": [],
                "statistics": {
                    "total_before_rerank": 0,
                    "total_after_rerank": 0,
                    "graph_nodes_count": 0,
                },
                "diagnostics": {},
                "context": None,
            }
        )

    import coderag.ui.main_window as module

    monkeypatch.setattr(module.requests, "post", _fake_post)

    window.query_view.repo_id.setCurrentText("repo-a")
    window.query_view.query_input.setText("hola")
    window.query_view.force_fallback.setChecked(False)
    window.query_view.retrieval_only_mode.setChecked(True)

    window._on_query()

    assert called["post"] is True
    assert str(called["url"]).endswith("/query/retrieval")


def test_format_query_success_text_retrieval_inventory_summary() -> None:
    """Muestra encabezado y resumen cuando retrieval-only retorna inventario."""
    rendered = MainWindow._format_query_success_text(
        response_payload={
            "answer": "Inventario de modelos",
            "diagnostics": {
                "inventory_route": "graph_first_retrieval",
                "inventory_target": "modelo",
                "inventory_total": 12,
                "inventory_page": 2,
                "inventory_page_size": 5,
            },
            "context": None,
        },
        retrieval_only_mode=True,
    )

    assert "Modo: Retrieval-only inventario (sin LLM)" in rendered
    assert "Total: 12" in rendered
    assert "Página: 2" in rendered
    assert "Page size: 5" in rendered
    assert "Objetivo: modelo" in rendered
    assert "Inventario de modelos" in rendered


def test_format_query_success_text_retrieval_with_context_keeps_block() -> None:
    """Con retrieval-only sin inventario mantiene bloque de contexto ensamblado."""
    rendered = MainWindow._format_query_success_text(
        response_payload={
            "answer": "Modo retrieval-only",
            "diagnostics": {},
            "context": "PATH: src/a.py",
        },
        retrieval_only_mode=True,
    )

    assert "Modo retrieval-only" in rendered
    assert "Contexto ensamblado:" in rendered
    assert "PATH: src/a.py" in rendered
