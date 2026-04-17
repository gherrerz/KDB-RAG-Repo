"""Utilidades del cliente Git para clonar y preparar repositorios."""

import base64
import binascii
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

from coderag.core.models import RepoAuthConfig


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


def _extract_repo_host(repo_url: str) -> str:
    """Extrae host del repositorio tanto para URLs HTTPS como SSH."""
    normalized = repo_url.strip()
    if not normalized:
        return ""
    if normalized.startswith("git@"):
        without_prefix = normalized.split("@", maxsplit=1)[-1]
        return without_prefix.split(":", maxsplit=1)[0].strip().lower()
    parsed = urlparse(normalized)
    return (parsed.hostname or "").strip().lower()


def _resolve_https_auth_env(
    *,
    username: str,
    secret: str,
    username_env_var: str,
    secret_env_var: str,
) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    """Construye entorno askpass para autenticación HTTPS sin exponer secretos."""
    temp_dir = tempfile.TemporaryDirectory(prefix="coderag_git_askpass_")
    script_path = Path(temp_dir.name) / "askpass.sh"
    script_path.write_text(
        "#!/bin/sh\n"
        "prompt=$(printf '%s' \"$1\" | tr '[:upper:]' '[:lower:]')\n"
        "case \"$prompt\" in\n"
        f"  *username*) printf '%s\\n' \"${username_env_var}\" ;;\n"
        f"  *) printf '%s\\n' \"${secret_env_var}\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script_path.chmod(0o700)
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(script_path),
        username_env_var: username,
        secret_env_var: secret,
    }, temp_dir


def _resolve_github_token_env(
    token: str,
) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    """Construye entorno askpass para usar token GitHub sin exponerlo en args."""
    return _resolve_https_auth_env(
        username="x-access-token",
        secret=token,
        username_env_var="CODERAG_GITHUB_USERNAME",
        secret_env_var="CODERAG_GITHUB_TOKEN",
    )


def _decode_ssh_secret_content(
    raw_content: str | None,
    b64_content: str | None,
    *,
    secret_name: str,
) -> bytes | None:
    """Decodifica contenido SSH desde variables raw o base64 con precedencia raw."""
    raw_value = raw_content or ""
    if raw_value.strip():
        return raw_value.encode("utf-8")

    b64_value = b64_content or ""
    if not b64_value.strip():
        return None

    try:
        return base64.b64decode(b64_value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(
            f"{secret_name} contiene base64 inválido."
        ) from exc


def _materialize_ssh_runtime_files(
    *,
    ssh_key_content: str | None,
    ssh_key_content_b64: str | None,
    ssh_known_hosts_content: str | None,
    ssh_known_hosts_content_b64: str | None,
) -> tuple[
    Path | None,
    Path | None,
    tempfile.TemporaryDirectory[str] | None,
]:
    """Materializa archivos temporales SSH cuando las credenciales llegan por entorno."""
    key_bytes = _decode_ssh_secret_content(
        ssh_key_content,
        ssh_key_content_b64,
        secret_name="GIT_SSH_KEY_CONTENT_B64",
    )
    known_hosts_bytes = _decode_ssh_secret_content(
        ssh_known_hosts_content,
        ssh_known_hosts_content_b64,
        secret_name="GIT_SSH_KNOWN_HOSTS_CONTENT_B64",
    )
    if key_bytes is None and known_hosts_bytes is None:
        return None, None, None

    temp_dir = tempfile.TemporaryDirectory(prefix="coderag_git_ssh_")
    temp_root = Path(temp_dir.name)
    key_path: Path | None = None
    known_hosts_path: Path | None = None

    if key_bytes is not None:
        key_path = temp_root / "id_key"
        key_path.write_bytes(key_bytes)
        key_path.chmod(0o600)

    if known_hosts_bytes is not None:
        known_hosts_path = temp_root / "known_hosts"
        known_hosts_path.write_bytes(known_hosts_bytes)

    return key_path, known_hosts_path, temp_dir


def _infer_transport(repo_url: str, requested_transport: str) -> str:
    """Resuelve el transporte efectivo a partir de URL y preferencia."""
    if requested_transport in {"https", "ssh"}:
        return requested_transport
    return "ssh" if _is_ssh_repo_url(repo_url) else "https"


def _infer_deployment(
    provider: str,
    repo_url: str,
    requested_deployment: str,
) -> str:
    """Resuelve el tipo de despliegue efectivo para defaults por provider."""
    if requested_deployment in {"cloud", "server", "data_center"}:
        return requested_deployment

    normalized_provider = _normalize_provider(provider)
    if normalized_provider != "bitbucket":
        return "auto"

    host = _extract_repo_host(repo_url)
    if host in {"bitbucket.org", "altssh.bitbucket.org"}:
        return "cloud"
    return "server"


def _resolve_default_auth_method(
    provider: str,
    transport: str,
    requested_method: str,
) -> str:
    """Resuelve el método de autenticación efectivo para el clone."""
    if requested_method in {"ssh_key", "http_basic", "http_token"}:
        return requested_method

    normalized_provider = _normalize_provider(provider)
    if transport == "ssh":
        return "ssh_key"
    if normalized_provider == "bitbucket":
        return "http_basic"
    return "http_token"


def _build_effective_auth(
    provider: str,
    auth: RepoAuthConfig | None,
    token: str | None,
) -> RepoAuthConfig:
    """Combina auth explícita con compatibilidad legacy del token."""
    effective = auth.normalized_copy() if auth is not None else RepoAuthConfig()
    normalized_provider = _normalize_provider(provider)
    cleaned_token = (token or "").strip()

    if cleaned_token and not effective.secret and normalized_provider == "github":
        if effective.transport == "auto":
            effective.transport = "https"
        if effective.method == "auto":
            effective.method = "http_token"
        effective.secret = cleaned_token
        if not effective.username:
            effective.username = "x-access-token"

    return effective


def _resolve_bitbucket_https_env(
    *,
    deployment: str,
    method: str,
    username: str | None,
    secret: str | None,
) -> tuple[dict[str, str] | None, tempfile.TemporaryDirectory[str] | None]:
    """Construye auth HTTPS para Bitbucket Cloud y Server/Data Center."""
    cleaned_secret = (secret or "").strip()
    cleaned_username = (username or "").strip()
    if not cleaned_secret:
        return None, None

    if method == "http_token":
        raise RuntimeError(
            "Bitbucket requiere auth.method='http_basic' en esta primera "
            "implementación. Define auth.username y auth.secret."
        )

    if method != "http_basic":
        raise RuntimeError(
            "Método HTTPS no soportado para Bitbucket. Usa auth.method="
            "'http_basic' o transporte SSH."
        )

    if not cleaned_username:
        raise RuntimeError(
            "Bitbucket HTTPS requiere auth.username explícito para "
            "deployment='cloud' o 'server/data_center'."
        )

    return _resolve_https_auth_env(
        username=cleaned_username,
        secret=cleaned_secret,
        username_env_var="CODERAG_GIT_HTTP_USERNAME",
        secret_env_var="CODERAG_GIT_HTTP_SECRET",
    )


def _resolve_runtime_auth_env(
    repo_url: str,
    *,
    provider: str,
    token: str | None,
    auth: RepoAuthConfig | None,
    ssh_key_content: str | None,
    ssh_key_content_b64: str | None,
    ssh_known_hosts_content: str | None,
    ssh_known_hosts_content_b64: str | None,
    ssh_strict_host_key_checking: str,
) -> tuple[dict[str, str] | None, tempfile.TemporaryDirectory[str] | None]:
    """Resuelve el entorno de autenticación según provider y tipo de URL."""
    normalized_provider = _normalize_provider(provider)
    effective_auth = _build_effective_auth(provider, auth, token)
    transport = _infer_transport(repo_url, effective_auth.transport)
    deployment = _infer_deployment(
        normalized_provider,
        repo_url,
        effective_auth.deployment,
    )
    method = _resolve_default_auth_method(
        normalized_provider,
        transport,
        effective_auth.method,
    )

    if transport == "ssh":
        if method != "ssh_key":
            raise RuntimeError(
                "auth.method incompatible con transporte SSH. Usa "
                "auth.method='ssh_key' o cambia auth.transport='https'."
            )
        return _resolve_ssh_env(
            repo_url,
            ssh_key_content=ssh_key_content,
            ssh_key_content_b64=ssh_key_content_b64,
            ssh_known_hosts_content=ssh_known_hosts_content,
            ssh_known_hosts_content_b64=ssh_known_hosts_content_b64,
            ssh_strict_host_key_checking=ssh_strict_host_key_checking,
        )

    if method == "ssh_key":
        raise RuntimeError(
            "auth.method='ssh_key' requiere auth.transport='ssh' o una URL SSH."
        )

    if normalized_provider == "github":
        cleaned_secret = (effective_auth.secret or "").strip()
        if not cleaned_secret:
            return None, None
        if method not in {"http_token", "http_basic"}:
            raise RuntimeError(
                "Método HTTPS no soportado para GitHub. Usa auth.method="
                "'http_token', 'http_basic' o deja 'auto'."
            )
        if method == "http_basic":
            cleaned_username = (effective_auth.username or "").strip()
            if not cleaned_username:
                raise RuntimeError(
                    "GitHub HTTPS con auth.method='http_basic' requiere "
                    "auth.username explícito."
                )
            return _resolve_https_auth_env(
                username=cleaned_username,
                secret=cleaned_secret,
                username_env_var="CODERAG_GIT_HTTP_USERNAME",
                secret_env_var="CODERAG_GIT_HTTP_SECRET",
            )
        return _resolve_github_token_env(cleaned_secret)

    if normalized_provider == "bitbucket":
        return _resolve_bitbucket_https_env(
            deployment=deployment,
            method=method,
            username=effective_auth.username,
            secret=effective_auth.secret,
        )

    cleaned_secret = (effective_auth.secret or "").strip()
    cleaned_username = (effective_auth.username or "").strip()
    if method == "http_basic":
        if not cleaned_secret or not cleaned_username:
            return None, None
        return _resolve_https_auth_env(
            username=cleaned_username,
            secret=cleaned_secret,
            username_env_var="CODERAG_GIT_HTTP_USERNAME",
            secret_env_var="CODERAG_GIT_HTTP_SECRET",
        )
    if method == "http_token" and cleaned_secret:
        if not cleaned_username:
            cleaned_username = "x-access-token"
        return _resolve_https_auth_env(
            username=cleaned_username,
            secret=cleaned_secret,
            username_env_var="CODERAG_GIT_HTTP_USERNAME",
            secret_env_var="CODERAG_GIT_HTTP_SECRET",
        )
    return None, None


def _resolve_ssh_env(
    repo_url: str,
    *,
    ssh_key_content: str | None,
    ssh_key_content_b64: str | None,
    ssh_known_hosts_content: str | None,
    ssh_known_hosts_content_b64: str | None,
    ssh_strict_host_key_checking: str,
) -> tuple[dict[str, str] | None, tempfile.TemporaryDirectory[str] | None]:
    """Construye entorno SSH para clones privados sin exponer credenciales por request."""
    if not _is_ssh_repo_url(repo_url):
        return None, None

    materialized_key_path, materialized_known_hosts_path, temp_dir = (
        _materialize_ssh_runtime_files(
            ssh_key_content=ssh_key_content,
            ssh_key_content_b64=ssh_key_content_b64,
            ssh_known_hosts_content=ssh_known_hosts_content,
            ssh_known_hosts_content_b64=ssh_known_hosts_content_b64,
        )
    )

    strict_mode = str(ssh_strict_host_key_checking or "yes").strip().lower()
    if strict_mode not in {"yes", "accept-new", "no"}:
        raise RuntimeError(
            "GIT_SSH_STRICT_HOST_KEY_CHECKING debe ser uno de: yes, accept-new, no."
        )

    known_hosts = materialized_known_hosts_path
    if strict_mode == "yes" and known_hosts is None:
        raise RuntimeError(
            "Falta known_hosts para SSH estricto. Define "
            "GIT_SSH_KNOWN_HOSTS_CONTENT o "
            "GIT_SSH_KNOWN_HOSTS_CONTENT_B64."
        )

    ssh_parts = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"StrictHostKeyChecking={strict_mode}",
    ]

    if known_hosts is not None:
        ssh_parts.extend(["-o", f"UserKnownHostsFile={known_hosts}"])

    key_file = materialized_key_path
    if key_file is None:
        raise RuntimeError(
            "Falta clave SSH privada para clonar repositorio. Define "
            "GIT_SSH_KEY_CONTENT o GIT_SSH_KEY_CONTENT_B64."
        )
    ssh_parts.extend(["-i", str(key_file), "-o", "IdentitiesOnly=yes"])

    command = " ".join(shlex.quote(part) for part in ssh_parts)
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": command,
    }, temp_dir


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
    auth: RepoAuthConfig | None = None,
    ssh_key_content: str | None = None,
    ssh_key_content_b64: str | None = None,
    ssh_known_hosts_content: str | None = None,
    ssh_known_hosts_content_b64: str | None = None,
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
        auth=auth,
        ssh_key_content=ssh_key_content,
        ssh_key_content_b64=ssh_key_content_b64,
        ssh_known_hosts_content=ssh_known_hosts_content,
        ssh_known_hosts_content_b64=ssh_known_hosts_content_b64,
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
