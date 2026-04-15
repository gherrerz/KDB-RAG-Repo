"""Utilidades del cliente Git para clonar y preparar repositorios."""

from contextlib import contextmanager
import hashlib
import os
import re
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


def _default_git_username(provider: str | None) -> str:
    """Resuelve un usuario HTTP por defecto para autenticación con token."""
    normalized = (provider or "").strip().lower()
    if normalized == "github":
        return "x-access-token"
    if normalized == "gitlab":
        return "oauth2"
    return "x-token-auth"


def _resolve_git_credentials(
    token: str,
    provider: str | None,
) -> tuple[str, str]:
    """Normaliza credenciales desde token simple o formato user:token."""
    raw_token = token.strip()
    if ":" in raw_token:
        username, password = raw_token.split(":", maxsplit=1)
        if password:
            resolved_username = username or _default_git_username(provider)
            return resolved_username, password
    return _default_git_username(provider), raw_token


@contextmanager
def _build_git_auth_env(
    token: str | None,
    provider: str | None,
):
    """Construye env temporal de autenticación Git usando GIT_ASKPASS."""
    if not token or not token.strip():
        yield None
        return

    username, password = _resolve_git_credentials(token, provider)
    script_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".sh",
            prefix="coderag_git_askpass_",
            delete=False,
        ) as script:
            script.write("#!/bin/sh\n")
            script.write('case "$1" in\n')
            script.write(
                '  *Username*) printf "%s\\n" "$CODERAG_GIT_USERNAME" ;;\n'
            )
            script.write(
                '  *Password*) printf "%s\\n" "$CODERAG_GIT_PASSWORD" ;;\n'
            )
            script.write(
                '  *) printf "%s\\n" "$CODERAG_GIT_PASSWORD" ;;\n'
            )
            script.write("esac\n")
            script_path = Path(script.name)

        script_path.chmod(0o700)
        yield {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(script_path),
            "CODERAG_GIT_USERNAME": username,
            "CODERAG_GIT_PASSWORD": password,
        }
    finally:
        if script_path and script_path.exists():
            try:
                script_path.unlink()
            except OSError:
                pass


def _run_git_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    auth_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando Git con entorno de autenticación opcional."""
    env = None
    if auth_env:
        env = os.environ.copy()
        env.update(auth_env)

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
    provider: str | None = None,
    token: str | None = None,
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
    with _build_git_auth_env(token=token, provider=provider) as auth_env:
        try:
            _run_git_command(command, check=True, auth_env=auth_env)
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
                _run_git_command(fallback, check=True, auth_env=auth_env)
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
        )

    return repo_id, destination
