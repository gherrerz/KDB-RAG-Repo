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


def test_clone_repository_uses_github_token_for_https_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configura askpass para HTTPS privado en GitHub cuando se recibe token."""
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
        repo_url="https://github.com/acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="github",
        token="ghp_test_token",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_env["CODERAG_GITHUB_TOKEN"] == "ghp_test_token"
    assert captured_env["GIT_ASKPASS"].endswith("askpass.sh")
    assert "GIT_SSH_COMMAND" not in captured_env


def test_clone_repository_uses_ssh_key_file_for_ssh_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configura GIT_SSH_COMMAND con key file y known_hosts en modo estricto."""
    captured_env: dict[str, str] = {}
    key_path = tmp_path / "id_rsa"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("bitbucket.example ssh-ed25519 AAAA", encoding="utf-8")

    def fake_run(*args, **kwargs):
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_repository(
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        ssh_enable_agent=False,
        ssh_key_path=key_path,
        ssh_known_hosts_path=known_hosts,
        ssh_strict_host_key_checking="yes",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "StrictHostKeyChecking=yes" in captured_env["GIT_SSH_COMMAND"]
    assert "UserKnownHostsFile=" in captured_env["GIT_SSH_COMMAND"]
    assert " -i " in captured_env["GIT_SSH_COMMAND"]


def test_clone_repository_uses_ssh_agent_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prefiere SSH agent cuando está habilitado y SSH_AUTH_SOCK existe."""
    captured_env: dict[str, str] = {}
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("bitbucket.example ssh-ed25519 AAAA", encoding="utf-8")

    def fake_run(*args, **kwargs):
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")

    clone_repository(
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        ssh_enable_agent=True,
        ssh_key_path=tmp_path / "id_rsa_does_not_matter_with_agent",
        ssh_known_hosts_path=known_hosts,
        ssh_strict_host_key_checking="yes",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "StrictHostKeyChecking=yes" in captured_env["GIT_SSH_COMMAND"]
    assert " -i " not in captured_env["GIT_SSH_COMMAND"]


def test_clone_repository_requires_known_hosts_when_strict_mode_is_enabled(
    tmp_path: Path,
) -> None:
    """Falla temprano si strict=yes y known_hosts no está presente."""
    key_path = tmp_path / "id_rsa"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")
    missing_known_hosts = tmp_path / "missing_known_hosts"

    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="git@bitbucket.example:acme/private-repo.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
            ssh_enable_agent=False,
            ssh_key_path=key_path,
            ssh_known_hosts_path=missing_known_hosts,
            ssh_strict_host_key_checking="yes",
        )

    assert "known_hosts" in str(exc.value)
