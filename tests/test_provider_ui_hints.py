"""Pruebas UI para autocompletado de modelos y warnings por provider."""

import sys

import pytest
from PySide6.QtWidgets import QApplication

import coderag.ui.ingestion_view as ingestion_view_module
import coderag.ui.query_view as query_view_module
from coderag.ui.ingestion_view import IngestionView
from coderag.ui.model_catalog_client import UIModelCatalogResult
from coderag.ui.query_view import QueryView


@pytest.fixture
def qapp() -> QApplication:
    """Asegura instancia QApplication para widgets Qt."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_ingestion_provider_autofill_and_warning(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Cambia modelo por provider y muestra warning cuando no hay backend."""

    class _Settings:
        def embedding_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            if provider == "anthropic":
                return {
                    "provider": provider,
                    "supported": False,
                    "configured": False,
                    "reason": "provider_without_embedding_backend",
                }
            return {
                "provider": provider,
                "supported": True,
                "configured": True,
                "reason": "ok",
            }

    def _fake_fetch_models_for_provider(
        provider: str,
        kind: str,
        *,
        force_refresh: bool = False,
    ) -> UIModelCatalogResult:
        _ = force_refresh
        if provider == "gemini" and kind == "embedding":
            return UIModelCatalogResult(
                models=["text-embedding-004"],
                source="remote",
                warning=None,
            )
        if provider == "anthropic" and kind == "embedding":
            return UIModelCatalogResult(
                models=["text-embedding-3-small", "text-embedding-3-large"],
                source="fallback",
                warning="provider_without_embedding_backend",
            )
        return UIModelCatalogResult(
            models=["text-embedding-3-small"],
            source="fallback",
            warning="catalog_service_unavailable",
        )

    monkeypatch.setattr(ingestion_view_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        ingestion_view_module,
        "fetch_models_for_provider",
        _fake_fetch_models_for_provider,
    )
    view = IngestionView()

    view.embedding_provider.setCurrentText("gemini")
    assert view.embedding_model.currentText() == "text-embedding-004"
    assert view.embedding_model.count() >= 1
    assert view.embedding_model.itemText(0) == "text-embedding-004"
    assert view.embedding_warning.text() == ""
    assert view.embedding_status_chip.text() == "Embeddings: Listo"
    assert view.embedding_status_chip.property("state") == "ready"

    view.embedding_provider.setCurrentText("anthropic")
    assert "fallback" in view.embedding_warning.text().lower()
    assert "fallback" in view.embedding_status_chip.text().lower()
    assert view.embedding_status_chip.property("state") == "warning"


def test_query_provider_autofill_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Autocompleta modelos en query y muestra warnings de configuración."""

    class _Settings:
        def embedding_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            if provider == "vertex_ai":
                return {
                    "provider": provider,
                    "supported": True,
                    "configured": False,
                    "reason": "missing_vertex_ai_api_key_or_project",
                }
            return {
                "provider": provider,
                "supported": True,
                "configured": True,
                "reason": "ok",
            }

        def llm_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            if provider == "anthropic":
                return {
                    "provider": provider,
                    "supported": True,
                    "configured": False,
                    "answer": True,
                    "verify": True,
                    "reason": "missing_anthropic_api_key",
                }
            return {
                "provider": provider,
                "supported": True,
                "configured": True,
                "answer": True,
                "verify": True,
                "reason": "ok",
            }

    monkeypatch.setattr(query_view_module, "get_settings", lambda: _Settings())
    view = QueryView()

    view.embedding_provider.setCurrentText("vertex_ai")
    assert view.embedding_model.currentText() == "text-embedding-005"
    assert "no configurado" in view.embedding_warning.text().lower()
    assert "no listo" in view.embedding_status_chip.text().lower()
    assert view.embedding_status_chip.property("state") == "blocked"

    view.llm_provider.setCurrentText("anthropic")
    assert view.answer_model.currentText() == "claude-3-5-sonnet-20241022"
    assert view.verifier_model.currentText() == "claude-3-5-sonnet-20241022"
    assert view.answer_model.count() >= 2
    assert "claude-3-5-haiku-20241022" in [
        view.answer_model.itemText(i) for i in range(view.answer_model.count())
    ]
    assert "no configurado" in view.llm_warning.text().lower()
    assert "no listo" in view.llm_status_chip.text().lower()
    assert view.llm_status_chip.property("state") == "blocked"


def test_ingestion_vertex_refresh_keeps_catalog_behavior(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """Vertex carga su catálogo y el refresh vuelve a pedir el mismo provider."""

    class _Settings:
        def embedding_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            return {
                "provider": provider,
                "supported": True,
                "configured": False,
                "reason": "missing_vertex_ai_api_key_or_project",
            }

    calls: list[tuple[str, str, bool]] = []

    def _fake_fetch_models_for_provider(
        provider: str,
        kind: str,
        *,
        force_refresh: bool = False,
    ):
        calls.append((provider, kind, force_refresh))
        if provider == "vertex_ai" and kind == "embedding":
            return UIModelCatalogResult(
                models=[
                    "text-embedding-005",
                    "text-multilingual-embedding-002",
                ],
                source="fallback",
                warning="missing_vertex_ai_api_key_or_project",
            )
        return UIModelCatalogResult(
            models=["text-embedding-3-small"],
            source="fallback",
            warning="catalog_service_unavailable",
        )

    monkeypatch.setattr(ingestion_view_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        ingestion_view_module,
        "fetch_models_for_provider",
        _fake_fetch_models_for_provider,
    )

    view = IngestionView()
    calls.clear()
    view.embedding_provider.setCurrentText("vertex_ai")

    assert view.embedding_model.currentText() == "text-embedding-005"
    assert [
        view.embedding_model.itemText(i) for i in range(view.embedding_model.count())
    ] == ["text-embedding-005", "text-multilingual-embedding-002"]
    assert calls[-1] == ("vertex_ai", "embedding", False)

    view.refresh_embedding_models_button.click()

    assert calls[-1] == ("vertex_ai", "embedding", True)
    assert [
        view.embedding_model.itemText(i) for i in range(view.embedding_model.count())
    ] == ["text-embedding-005", "text-multilingual-embedding-002"]


def test_query_vertex_refresh_keeps_embedding_and_llm_catalogs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    """En query, refresh mantiene provider Vertex y recarga embeddings/LLM."""

    class _Settings:
        def embedding_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            return {
                "provider": provider,
                "supported": True,
                "configured": False,
                "reason": "missing_vertex_ai_api_key_or_project",
            }

        def llm_provider_capabilities(self, provider: str) -> dict[str, str | bool]:
            return {
                "provider": provider,
                "supported": True,
                "configured": False,
                "answer": True,
                "verify": True,
                "reason": "missing_vertex_ai_api_key_or_project",
            }

    calls: list[tuple[str, str, bool]] = []

    def _fake_fetch_models_for_provider(
        provider: str,
        kind: str,
        *,
        force_refresh: bool = False,
    ):
        calls.append((provider, kind, force_refresh))
        if provider == "vertex_ai" and kind == "embedding":
            return UIModelCatalogResult(
                models=["text-embedding-005", "text-multilingual-embedding-002"],
                source="fallback",
                warning="missing_vertex_ai_api_key_or_project",
            )
        if provider == "vertex_ai" and kind == "llm":
            return UIModelCatalogResult(
                models=["gemini-2.0-flash", "gemini-1.5-pro"],
                source="fallback",
                warning="missing_vertex_ai_api_key_or_project",
            )
        return UIModelCatalogResult(
            models=["gpt-4.1-mini"],
            source="fallback",
            warning="catalog_service_unavailable",
        )

    monkeypatch.setattr(query_view_module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        query_view_module,
        "fetch_models_for_provider",
        _fake_fetch_models_for_provider,
    )

    view = QueryView()
    calls.clear()
    view.embedding_provider.setCurrentText("vertex_ai")
    view.llm_provider.setCurrentText("vertex_ai")

    assert view.embedding_model.currentText() == "text-embedding-005"
    assert view.answer_model.currentText() == "gemini-2.0-flash"
    assert view.verifier_model.currentText() == "gemini-2.0-flash"
    assert calls[-1] == ("vertex_ai", "llm", False)

    view.refresh_models_button.click()

    assert ("vertex_ai", "embedding", True) in calls
    assert ("vertex_ai", "llm", True) in calls
    assert [
        view.embedding_model.itemText(i) for i in range(view.embedding_model.count())
    ] == ["text-embedding-005", "text-multilingual-embedding-002"]
    assert [view.answer_model.itemText(i) for i in range(view.answer_model.count())] == [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ]
    assert [
        view.verifier_model.itemText(i) for i in range(view.verifier_model.count())
    ] == ["gemini-2.0-flash", "gemini-1.5-pro"]
