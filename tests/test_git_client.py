"""Pruebas de resiliencia a la clonación de repositorios."""

import shlex
import subprocess
from pathlib import Path

import pytest

from coderag.core.models import RepoAuthConfig
from coderag.ingestion.git_client import (
    build_repo_id,
    clone_repository,
    extract_repo_organization,
)


def test_build_repo_id_uses_url_tail_for_https_url() -> None:
    """Deriva repo_id compuesto con organización, repositorio y rama."""
    repo_id = build_repo_id("https://github.com/macrozheng/mall.git", "main")
    assert repo_id == "macrozheng-mall-main"


def test_build_repo_id_uses_url_tail_for_ssh_url() -> None:
    """Admite URL SSH y compone el repo_id con organización, repo y rama."""
    repo_id = build_repo_id("git@github.com:macrozheng/mall.git", "develop")
    assert repo_id == "macrozheng-mall-develop"


def test_extract_repo_organization_returns_owner_for_https_url() -> None:
    """Deriva owner para URLs HTTPS comunes de GitHub y similares."""
    organization = extract_repo_organization(
        "https://github.com/macrozheng/mall.git"
    )
    assert organization == "macrozheng"


def test_extract_repo_organization_returns_last_parent_for_nested_gitlab_url() -> None:
    """Devuelve solo el último segmento padre para URLs GitLab anidadas."""
    organization = extract_repo_organization(
        "https://gitlab.com/group/subgroup/project.git"
    )
    assert organization == "subgroup"


def test_extract_repo_organization_returns_workspace_for_ssh_url() -> None:
    """Deriva workspace u owner desde URLs SSH de estilo git@host:path."""
    organization = extract_repo_organization(
        "git@bitbucket.org:workspace/proyecto.git"
    )
    assert organization == "workspace"


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


def test_clone_repository_uses_bitbucket_http_basic_for_https_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configura askpass genérico para Bitbucket HTTPS con usuario y secreto."""
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
        repo_url="https://bitbucket.org/acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        auth=RepoAuthConfig(
            deployment="cloud",
            transport="https",
            method="http_basic",
            username="acme-user",
            secret="app-password",
        ),
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_env["CODERAG_GIT_HTTP_USERNAME"] == "acme-user"
    assert captured_env["CODERAG_GIT_HTTP_SECRET"] == "app-password"
    assert captured_env["GIT_ASKPASS"].endswith("askpass.sh")
    assert "GIT_SSH_COMMAND" not in captured_env


def test_clone_repository_uses_ssh_content_for_ssh_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configura GIT_SSH_COMMAND con key y known_hosts desde variables en modo estricto."""
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
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        ssh_key_content="PRIVATE KEY",
        ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
        ssh_strict_host_key_checking="yes",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert "StrictHostKeyChecking=yes" in captured_env["GIT_SSH_COMMAND"]
    assert "UserKnownHostsFile=" in captured_env["GIT_SSH_COMMAND"]
    assert " -i " in captured_env["GIT_SSH_COMMAND"]
def test_clone_repository_uses_ssh_content_from_env_for_bitbucket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Prioriza contenido SSH desde variables para Bitbucket sin depender de archivos."""
    captured_env: dict[str, str] = {}
    materialized_key_path: Path | None = None
    materialized_known_hosts_path: Path | None = None

    def fake_run(*args, **kwargs):
        nonlocal materialized_key_path, materialized_known_hosts_path
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        ssh_parts = shlex.split(captured_env["GIT_SSH_COMMAND"])
        materialized_key_path = Path(ssh_parts[ssh_parts.index("-i") + 1])
        known_hosts_option = next(
            part for part in ssh_parts if part.startswith("UserKnownHostsFile=")
        )
        materialized_known_hosts_path = Path(
            known_hosts_option.split("=", maxsplit=1)[1]
        )

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_repository(
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        ssh_key_content="PRIVATE KEY FROM ENV",
        ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
        ssh_strict_host_key_checking="yes",
    )

    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert materialized_key_path is not None
    assert materialized_known_hosts_path is not None
    assert not materialized_key_path.exists()
    assert not materialized_known_hosts_path.exists()


def test_clone_repository_accepts_base64_ssh_content_for_bitbucket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Decodifica contenido base64 para SSH de Bitbucket cuando no hay archivos."""
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
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        ssh_key_content_b64="UFJJVkFURSBLRVkgRlJPTSBFTlY=",
        ssh_known_hosts_content_b64="Yml0YnVja2V0LmV4YW1wbGUgc3NoLWVkMjU1MTkgQUFBQQ==",
        ssh_strict_host_key_checking="yes",
    )

    assert "UserKnownHostsFile=" in captured_env["GIT_SSH_COMMAND"]
    assert " -i " in captured_env["GIT_SSH_COMMAND"]


def test_clone_repository_prefers_raw_ssh_content_over_base64(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Usa contenido raw antes que la variante base64 cuando ambas están presentes."""
    captured_env: dict[str, str] = {}
    materialized_key_path: Path | None = None
    materialized_known_hosts_path: Path | None = None

    def fake_run(*args, **kwargs):
        nonlocal materialized_key_path, materialized_known_hosts_path
        command = args[0]
        env = kwargs.get("env") or {}
        captured_env.update({k: str(v) for k, v in env.items()})

        ssh_parts = shlex.split(captured_env["GIT_SSH_COMMAND"])
        materialized_key_path = Path(ssh_parts[ssh_parts.index("-i") + 1])
        materialized_known_hosts_path = Path(
            next(
                part.split("=", maxsplit=1)[1]
                for part in ssh_parts
                if part.startswith("UserKnownHostsFile=")
            )
        )

        destination = Path(command[-1])
        destination.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    clone_repository(
        repo_url="git@bitbucket.example:acme/private-repo.git",
        destination_root=tmp_path,
        branch="main",
        commit=None,
        provider="bitbucket",
        ssh_key_content="PRIVATE KEY FROM ENV",
        ssh_key_content_b64="UFJJVkFURSBLRVkgRlJPTSBCRTY0",
        ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
        ssh_known_hosts_content_b64="Yml0YnVja2V0LmV4YW1wbGUgc3NoLWVkMjU1MTkgQkJCQg==",
        ssh_strict_host_key_checking="yes",
    )

    assert materialized_key_path is not None
    assert materialized_known_hosts_path is not None
    assert not materialized_key_path.exists()
    assert not materialized_known_hosts_path.exists()


def test_clone_repository_rejects_invalid_base64_ssh_content(
    tmp_path: Path,
) -> None:
    """Falla con error descriptivo cuando el secreto SSH base64 es inválido."""
    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="git@bitbucket.example:acme/private-repo.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
            provider="bitbucket",
            ssh_key_content_b64="***not-base64***",
            ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
            ssh_strict_host_key_checking="yes",
        )

    assert "GIT_SSH_KEY_CONTENT_B64" in str(exc.value)


def test_clone_repository_requires_known_hosts_when_strict_mode_is_enabled(
    tmp_path: Path,
) -> None:
    """Falla temprano si strict=yes y known_hosts no está presente."""
    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="git@bitbucket.example:acme/private-repo.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
            ssh_key_content="PRIVATE KEY",
            ssh_strict_host_key_checking="yes",
        )

    assert "known_hosts" in str(exc.value)


def test_clone_repository_requires_key_content_when_missing(
    tmp_path: Path,
) -> None:
    """Falla temprano si no se configuró contenido de clave SSH."""
    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="git@bitbucket.example:acme/private-repo.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
            provider="bitbucket",
            ssh_known_hosts_content="bitbucket.example ssh-ed25519 AAAA",
            ssh_strict_host_key_checking="yes",
        )

    assert "GIT_SSH_KEY_CONTENT" in str(exc.value)


def test_clone_repository_requires_username_for_bitbucket_https_basic(
    tmp_path: Path,
) -> None:
    """Falla temprano cuando Bitbucket HTTPS no recibe usuario explícito."""
    with pytest.raises(RuntimeError) as exc:
        clone_repository(
            repo_url="https://bitbucket.example/scm/acme/private-repo.git",
            destination_root=tmp_path,
            branch="main",
            commit=None,
            provider="bitbucket",
            auth=RepoAuthConfig(
                deployment="server",
                transport="https",
                method="http_basic",
                secret="server-password",
            ),
        )

    assert "auth.username" in str(exc.value)
