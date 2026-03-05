"""Tests for repository cloning resilience."""

import subprocess
from pathlib import Path

import pytest

from coderag.ingestion.git_client import clone_repository


def test_clone_repository_fallbacks_when_branch_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Falls back to default remote branch when requested branch fails."""
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = args[0]
        calls.append(command)
        if "--branch" in command:
            raise subprocess.CalledProcessError(
                returncode=128,
                cmd=command,
                stderr="Remote branch main not found",
            )

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    repo_id, destination = clone_repository(
        repo_url="https://example.com/org/repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
    )

    assert repo_id
    assert destination.exists()
    assert len(calls) == 2
    assert "--branch" in calls[0]
    assert "--branch" not in calls[1]


def test_clone_repository_raises_descriptive_error_on_total_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Raises helpful error with stderr and stdout details on clone failure."""

    def fake_run(*args, **kwargs):
        command = args[0]
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=command,
            stderr="Authentication failed",
            output="fatal: could not read Username",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="https://example.com/org/private.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
        )

    message = str(exc.value)
    assert "No se pudo clonar el repositorio" in message
    assert "Authentication failed" in message
