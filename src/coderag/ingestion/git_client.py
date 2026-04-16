"""Utilidades del cliente Git para clonar y preparar repositorios."""

import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


def build_repo_id(repo_url: str, branch: str) -> str:
    """Cree un identificador de repositorio desde la cola de la URL con respaldo determinista."""
    del branch  # Branch no longer contributes to public repo identifier.

    normalized = repo_url.strip()
    if not normalized:
        digest = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()
        return digest[:16]

    candidate = ""
    if normalized.startswith("git@"):
        if ":" in normalized:
            candidate = normalized.split(":", maxsplit=1)[-1]
    else:
        parsed = urlparse(normalized)
        candidate = parsed.path

    candidate = candidate.strip().strip("/")
    if "/" in candidate:
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]

    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-._")
    if sanitized:
        return sanitized.lower()

    digest = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()
    return digest[:16]


def _on_remove_error(func, path: str, exc_info) -> None:
    """Handle read-only files during directory removal on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _safe_remove_tree(path: Path, retries: int = 3) -> bool:
    """Elimine el directorio de forma recursiva con reintentos y manejo de solo lectura."""
    for _ in range(retries):
        try:
            shutil.rmtree(path, onerror=_on_remove_error)
            return True
        except PermissionError:
            time.sleep(0.4)
    return False


def _is_ssh_repo_url(repo_url: str) -> bool:
    """Retorna True para URLs Git SSH (git@host:path o ssh://)."""
    normalized = repo_url.strip().lower()
    return normalized.startswith("git@") or normalized.startswith("ssh://")


def _normalize_provider(provider: str | None) -> str:
    """Normaliza provider Git a un valor estable en minúsculas."""
    return (provider or "github").strip().lower()


def _resolve_github_token_env(token: str) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    """Construye entorno askpass para usar token GitHub sin exponerlo en args."""
    temp_dir = tempfile.TemporaryDirectory(prefix="coderag_git_askpass_")
    script_path = Path(temp_dir.name) / "askpass.sh"
    script_path.write_text(
        "#!/bin/sh\n"
        "prompt=$(printf '%s' \"$1\" | tr '[:upper:]' '[:lower:]')\n"
        "case \"$prompt\" in\n"
        "  *username*) printf '%s\\n' 'x-access-token' ;;\n"
        "  *) printf '%s\\n' \"$CODERAG_GITHUB_TOKEN\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script_path.chmod(0o700)
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(script_path),
        "CODERAG_GITHUB_TOKEN": token,
    }, temp_dir


def _resolve_runtime_auth_env(
    repo_url: str,
    *,
    provider: str,
    token: str | None,
    ssh_enable_agent: bool,
    ssh_key_path: Path,
    ssh_known_hosts_path: Path,
    ssh_strict_host_key_checking: str,
) -> tuple[dict[str, str] | None, tempfile.TemporaryDirectory[str] | None]:
    """Resuelve el entorno de autenticación según provider y tipo de URL."""
    normalized_provider = _normalize_provider(provider)
    is_ssh = _is_ssh_repo_url(repo_url)

    if normalized_provider == "bitbucket":
        return (
            _resolve_ssh_env(
                repo_url,
                ssh_enable_agent=ssh_enable_agent,
                ssh_key_path=ssh_key_path,
                ssh_known_hosts_path=ssh_known_hosts_path,
                ssh_strict_host_key_checking=ssh_strict_host_key_checking,
            ),
            None,
        )

    if normalized_provider == "github":
        cleaned_token = (token or "").strip()
        if cleaned_token and not is_ssh:
            return _resolve_github_token_env(cleaned_token)
        if is_ssh:
            return (
                _resolve_ssh_env(
                    repo_url,
                    ssh_enable_agent=ssh_enable_agent,
                    ssh_key_path=ssh_key_path,
                    ssh_known_hosts_path=ssh_known_hosts_path,
                    ssh_strict_host_key_checking=ssh_strict_host_key_checking,
                ),
                None,
            )
        return None, None

    # Compatibilidad: otros providers mantienen resolución por tipo de URL.
    if is_ssh:
        return (
            _resolve_ssh_env(
                repo_url,
                ssh_enable_agent=ssh_enable_agent,
                ssh_key_path=ssh_key_path,
                ssh_known_hosts_path=ssh_known_hosts_path,
                ssh_strict_host_key_checking=ssh_strict_host_key_checking,
            ),
            None,
        )

    cleaned_token = (token or "").strip()
    if cleaned_token:
        return _resolve_github_token_env(cleaned_token)
    return None, None


def _resolve_ssh_env(
    repo_url: str,
    *,
    ssh_enable_agent: bool,
    ssh_key_path: Path,
    ssh_known_hosts_path: Path,
    ssh_strict_host_key_checking: str,
) -> dict[str, str] | None:
    """Construye entorno SSH para clones privados sin exponer credenciales por request."""
    if not _is_ssh_repo_url(repo_url):
        return None

    strict_mode = str(ssh_strict_host_key_checking or "yes").strip().lower()
    if strict_mode not in {"yes", "accept-new", "no"}:
        raise RuntimeError(
            "GIT_SSH_STRICT_HOST_KEY_CHECKING debe ser uno de: yes, accept-new, no."
        )

    known_hosts = Path(ssh_known_hosts_path).expanduser()
    if strict_mode == "yes" and not known_hosts.exists():
        raise RuntimeError(
            "No se encontró archivo known_hosts requerido para SSH estricto. "
            f"Ruta esperada: {known_hosts}"
        )

    ssh_parts = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"StrictHostKeyChecking={strict_mode}",
    ]

    if known_hosts.exists():
        ssh_parts.extend(["-o", f"UserKnownHostsFile={known_hosts}"])

    use_agent = bool(ssh_enable_agent and os.environ.get("SSH_AUTH_SOCK"))
    if not use_agent:
        key_file = Path(ssh_key_path).expanduser()
        if not key_file.exists():
            raise RuntimeError(
                "No se encontró clave SSH privada para clonar repositorio. "
                f"Ruta esperada: {key_file}"
            )
        ssh_parts.extend(["-i", str(key_file), "-o", "IdentitiesOnly=yes"])

    command = " ".join(shlex.quote(part) for part in ssh_parts)
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": command,
    }


def _run_git_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    runtime_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando Git con entorno de runtime opcional."""
    env = None
    if runtime_env:
        env = os.environ.copy()
        env.update(runtime_env)

    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def clone_repository(
    repo_url: str,
    destination_root: Path,
    branch: str = "main",
    commit: str | None = None,
    provider: str = "github",
    token: str | None = None,
    ssh_enable_agent: bool = True,
    ssh_key_path: Path = Path("~/.ssh/id_rsa"),
    ssh_known_hosts_path: Path = Path("~/.ssh/known_hosts"),
    ssh_strict_host_key_checking: str = "yes",
) -> tuple[str, Path]:
    """Clona el repositorio en el espacio de trabajo y devuelve repo_id y la ruta local."""
    repo_id = build_repo_id(repo_url, branch)
    destination = destination_root / repo_id
    if destination.exists():
        removed = _safe_remove_tree(destination)
        if not removed:
            destination = destination_root / f"{repo_id}_{uuid4().hex[:8]}"

    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        repo_url,
        str(destination),
    ]
    runtime_env, askpass_temp_dir = _resolve_runtime_auth_env(
        repo_url,
        provider=provider,
        token=token,
        ssh_enable_agent=ssh_enable_agent,
        ssh_key_path=ssh_key_path,
        ssh_known_hosts_path=ssh_known_hosts_path,
        ssh_strict_host_key_checking=ssh_strict_host_key_checking,
    )
    try:
        try:
            _run_git_command(command, check=True, runtime_env=runtime_env)
        except subprocess.CalledProcessError as exc:
            if destination.exists():
                _safe_remove_tree(destination)

            fallback = [
                "git",
                "clone",
                "--depth",
                "1",
                repo_url,
                str(destination),
            ]
            try:
                _run_git_command(fallback, check=True, runtime_env=runtime_env)
            except subprocess.CalledProcessError as fallback_exc:
                stderr = (fallback_exc.stderr or "").strip()
                stdout = (fallback_exc.stdout or "").strip()
                message = (
                    "No se pudo clonar el repositorio. "
                    f"Rama solicitada: {branch}. "
                    f"stdout: {stdout} stderr: {stderr}"
                )
                raise RuntimeError(message) from fallback_exc

            if commit:
                checkout_result = _run_git_command(
                    ["git", "checkout", commit],
                    check=False,
                    cwd=destination,
                    runtime_env=runtime_env,
                )
                if checkout_result.returncode != 0:
                    stderr = (checkout_result.stderr or "").strip()
                    raise RuntimeError(
                        "El commit solicitado no está disponible en un clone "
                        f"depth=1. Commit: {commit}. stderr: {stderr}"
                    ) from exc
                return repo_id, destination

        if commit:
            _run_git_command(
                ["git", "checkout", commit],
                check=True,
                cwd=destination,
                runtime_env=runtime_env,
            )
    finally:
        if askpass_temp_dir is not None:
            askpass_temp_dir.cleanup()

    return repo_id, destination
