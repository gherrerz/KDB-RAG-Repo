"""Pruebas de la ingesta incremental: diff de commits, decisión de modo y persistencia."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coderag.ingestion.git_client import (
    _parse_name_status_z,
    diff_changed_files,
    resolve_head_commit,
)
from coderag.ingestion.pipeline import _resolve_ingest_mode
from coderag.storage.metadata_store import MetadataStore


# ---------------------------------------------------------------------------
# Parser de `git diff --name-status -z`
# ---------------------------------------------------------------------------


def test_parse_name_status_z_classifies_modify_add_delete() -> None:
    """Modificados y añadidos van a changed; borrados a deleted."""
    raw = "M\0src/a.py\0A\0src/b.py\0D\0src/c.py\0"
    changed, deleted = _parse_name_status_z(raw)

    assert changed == ["src/a.py", "src/b.py"]
    assert deleted == ["src/c.py"]


def test_parse_name_status_z_handles_rename_and_copy() -> None:
    """Rename borra el origen y marca el destino; copy solo marca el destino."""
    raw = "R100\0old/a.py\0new/a.py\0C100\0base.py\0copy.py\0"
    changed, deleted = _parse_name_status_z(raw)

    assert "new/a.py" in changed
    assert "copy.py" in changed
    assert deleted == ["old/a.py"]
    assert "base.py" not in deleted


def test_parse_name_status_z_empty_output() -> None:
    """Salida vacía produce listas vacías."""
    assert _parse_name_status_z("") == ([], [])


# ---------------------------------------------------------------------------
# diff_changed_files / resolve_head_commit sobre un repo git real
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    """Ejecuta un comando git en cwd abortando si falla."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Crea un repo git con dos commits para diffear."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "keep.py").write_text("print('keep')\n", encoding="utf-8")
    (repo / "remove.py").write_text("print('remove')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_resolve_head_commit_returns_sha(git_repo: Path) -> None:
    """resolve_head_commit devuelve un SHA de 40 hex en un repo válido."""
    head = resolve_head_commit(git_repo)
    assert head is not None
    assert len(head) == 40


def test_resolve_head_commit_returns_none_outside_repo(tmp_path: Path) -> None:
    """Fuera de un repo git devuelve None en vez de propagar error."""
    assert resolve_head_commit(tmp_path) is None


def test_diff_changed_files_detects_modify_add_delete(git_repo: Path) -> None:
    """El diff entre base y HEAD clasifica modificados, añadidos y borrados."""
    base = resolve_head_commit(git_repo)
    assert base is not None

    (git_repo / "keep.py").write_text("print('changed')\n", encoding="utf-8")
    (git_repo / "new.py").write_text("print('new')\n", encoding="utf-8")
    (git_repo / "remove.py").unlink()
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "next")
    head = resolve_head_commit(git_repo)
    assert head is not None

    diff = diff_changed_files(git_repo, base, head)
    assert diff is not None
    changed, deleted = diff
    assert set(changed) == {"keep.py", "new.py"}
    assert deleted == ["remove.py"]


def test_diff_changed_files_unknown_base_returns_none(git_repo: Path) -> None:
    """Un commit base inexistente devuelve None para forzar fallback a full."""
    head = resolve_head_commit(git_repo)
    assert head is not None
    assert diff_changed_files(git_repo, "0" * 40, head) is None


# ---------------------------------------------------------------------------
# Decisión de modo de ingesta
# ---------------------------------------------------------------------------


def _noop_logger(_message: str) -> None:
    """Logger silencioso para las pruebas de decisión de modo."""


def test_resolve_mode_full_when_no_existing_data(tmp_path: Path) -> None:
    """Sin data previa siempre se hace reindex completo."""
    plan = _resolve_ingest_mode(
        repo_id="r",
        repo_path=tmp_path,
        has_existing_data=False,
        last_indexed_commit="abc",
        head_commit="def",
        changed_files=None,
        logger=_noop_logger,
    )
    assert plan.mode == "full"
    assert plan.reason == "no_existing_data"


def test_resolve_mode_incremental_with_explicit_changed_files(tmp_path: Path) -> None:
    """Una lista explícita de cambios activa incremental sin tocar git."""
    plan = _resolve_ingest_mode(
        repo_id="r",
        repo_path=tmp_path,
        has_existing_data=True,
        last_indexed_commit=None,
        head_commit=None,
        changed_files=["src/a.py", "  ", "src/b.py"],
        logger=_noop_logger,
    )
    assert plan.mode == "incremental"
    assert plan.changed_paths == {"src/a.py", "src/b.py"}


def test_resolve_mode_full_when_base_missing(tmp_path: Path) -> None:
    """Con data previa pero sin commit base no se puede diffear: full."""
    plan = _resolve_ingest_mode(
        repo_id="r",
        repo_path=tmp_path,
        has_existing_data=True,
        last_indexed_commit=None,
        head_commit="def",
        changed_files=None,
        logger=_noop_logger,
    )
    assert plan.mode == "full"
    assert plan.reason == "missing_base_or_head_commit"


def test_resolve_mode_incremental_via_git_diff(git_repo: Path) -> None:
    """Con base + HEAD distintos y diff disponible se decide incremental."""
    base = resolve_head_commit(git_repo)
    assert base is not None
    (git_repo / "keep.py").write_text("print('x')\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "next")
    head = resolve_head_commit(git_repo)

    plan = _resolve_ingest_mode(
        repo_id="r",
        repo_path=git_repo,
        has_existing_data=True,
        last_indexed_commit=base,
        head_commit=head,
        changed_files=None,
        logger=_noop_logger,
    )
    assert plan.mode == "incremental"
    assert plan.reason == "git_diff"
    assert "keep.py" in plan.changed_paths


# ---------------------------------------------------------------------------
# Persistencia de last_indexed_commit (SQLite)
# ---------------------------------------------------------------------------


def _upsert(store: MetadataStore, commit: str | None) -> None:
    """Helper para upsertar runtime con un commit indexado dado."""
    store.upsert_repo_runtime(
        repo_id="repo-1",
        organization="org",
        repo_url="https://example.com/org/repo.git",
        branch="main",
        local_path="/tmp/repo-1",
        embedding_provider="vertex",
        embedding_model="text-embedding-005",
        last_indexed_commit=commit,
    )


def test_last_indexed_commit_round_trip(tmp_path: Path) -> None:
    """El commit indexado se persiste y se recupera vía get_repo_runtime."""
    store = MetadataStore(tmp_path / "metadata.db")
    _upsert(store, "commit-aaa")

    runtime = store.get_repo_runtime("repo-1")
    assert runtime is not None
    assert runtime["last_indexed_commit"] == "commit-aaa"


def test_last_indexed_commit_preserved_when_upsert_none(tmp_path: Path) -> None:
    """Un upsert posterior con commit None conserva la base previa (COALESCE)."""
    store = MetadataStore(tmp_path / "metadata.db")
    _upsert(store, "commit-aaa")
    _upsert(store, None)

    runtime = store.get_repo_runtime("repo-1")
    assert runtime is not None
    assert runtime["last_indexed_commit"] == "commit-aaa"


def test_last_indexed_commit_advances_on_new_value(tmp_path: Path) -> None:
    """Un upsert con un commit nuevo avanza la base persistida."""
    store = MetadataStore(tmp_path / "metadata.db")
    _upsert(store, "commit-aaa")
    _upsert(store, "commit-bbb")

    runtime = store.get_repo_runtime("repo-1")
    assert runtime is not None
    assert runtime["last_indexed_commit"] == "commit-bbb"
