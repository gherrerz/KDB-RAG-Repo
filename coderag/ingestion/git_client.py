"""Utilidades del cliente Git para clonar y preparar repositorios."""

import hashlib
import os
import re
import shutil
import stat
import subprocess
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


def clone_repository(
    repo_url: str,
    destination_root: Path,
    branch: str = "main",
    commit: str | None = None,
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
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
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
            subprocess.run(fallback, check=True, capture_output=True, text=True)
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
            checkout_result = subprocess.run(
                ["git", "checkout", commit],
                check=False,
                capture_output=True,
                text=True,
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
        subprocess.run(
            ["git", "checkout", commit],
            check=True,
            capture_output=True,
            text=True,
            cwd=destination,
        )

    return repo_id, destination
