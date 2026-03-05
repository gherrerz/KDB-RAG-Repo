"""Tests for module discovery support in query service."""

from pathlib import Path

import pytest

import coderag.api.query_service as query_service


def test_is_module_query_detects_spanish_and_english_terms() -> None:
    """Identifies module-related query intents in common variants."""
    assert query_service._is_module_query("Cuales son los modulos?")
    assert query_service._is_module_query("list repository modules")
    assert not query_service._is_module_query("donde se define auth")


def test_discover_repo_modules_reads_top_level_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Returns matching top-level module folders from local repository."""
    repo_id = "repo1"
    repo_dir = tmp_path / repo_id
    (repo_dir / "mall-admin").mkdir(parents=True)
    (repo_dir / "mall-admin" / "pom.xml").write_text("<project/>")
    (repo_dir / "mall-common").mkdir(parents=True)
    (repo_dir / "docs").mkdir(parents=True)

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    modules = query_service._discover_repo_modules(repo_id)
    assert "mall-admin" in modules
    assert "mall-common" in modules
    assert "docs" not in modules


def test_is_inventory_query_detection() -> None:
    """Detects generic inventory intents in natural language queries."""
    assert query_service._is_inventory_query(
        "cuales son todos los service del modulo mall-admin"
    )
    assert query_service._is_inventory_query("list all controllers in module")
    assert not query_service._is_inventory_query("que hace autenticacion")


def test_extract_inventory_target_for_es_and_en() -> None:
    """Extracts normalized inventory target token from user query."""
    assert query_service._extract_inventory_target("todos los services del modulo") == "service"
    assert query_service._extract_inventory_target("all controllers in mall-admin") == "controller"
