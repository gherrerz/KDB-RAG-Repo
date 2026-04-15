"""Pruebas de resiliencia a la clonación de repositorios."""

import subprocess
from pathlib import Path

import pytest

from coderag.ingestion.git_client import build_repo_id, clone_repository


def test_build_repo_id_uses_url_tail_for_https_url() -> None:
    """Deriva el identificador de repositorio público del segmento de ruta URL final."""
    repo_id = build_repo_id("https://github.com/macrozheng/mall.git", "main")
    assert repo_id == "mall"


def test_build_repo_id_uses_url_tail_for_ssh_url() -> None:
    """Admite URL de repositorio de estilo git@ SSH como fuente repo_id."""
    repo_id = build_repo_id("git@github.com:macrozheng/mall.git", "develop")
    assert repo_id == "mall"


def test_clone_repository_fallbacks_when_branch_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Usa una alternativa con la rama remota por defecto cuando falla la rama solicitada."""
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
    """Genera un error útil con detalles de stderr y stdout sobre falla de clonación."""

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


def test_clone_repository_uses_bitbucket_token_via_askpass_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configura GIT_ASKPASS y credenciales cuando se entrega token Bitbucket."""
    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        askpass = Path(captured_env["GIT_ASKPASS"])
        assert askpass.exists()

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_repository(
        repo_url="https://bitbucket.example/scm/acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        token="secret-token",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_env["CODERAG_GIT_USERNAME"] == "x-token-auth"
    assert captured_env["CODERAG_GIT_PASSWORD"] == "secret-token"


def test_clone_repository_supports_username_token_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Respeta formato user:token para escenarios Bitbucket Server/Data Center."""
    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_repository(
        repo_url="https://bitbucket.example/scm/acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        token="svc-ci:app-password",
    )

    assert captured_env["CODERAG_GIT_USERNAME"] == "svc-ci"
    assert captured_env["CODERAG_GIT_PASSWORD"] == "app-password"
