"""Utilidades compartidas para autenticación y labels en Vertex AI."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from threading import Lock
from typing import Mapping

from google.auth.transport.requests import Request
from google.oauth2 import service_account


_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_LABEL_MAX_LENGTH = 63
_LABEL_ALLOWED_RE = re.compile(r"[^a-z0-9_-]+")
_REFRESH_LOCK = Lock()


@dataclass(frozen=True)
class VertexAuthContext:
    """Contexto resuelto de autenticación para llamadas Vertex AI."""

    access_token: str
    service_account_email: str


@lru_cache(maxsize=4)
def _load_service_account_credentials(credentials_path: str):
    """Carga y cachea credenciales de Service Account desde archivo JSON."""
    path = Path(credentials_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            "No se encontró GOOGLE_APPLICATION_CREDENTIALS en "
            f"{path}."
        )
    return service_account.Credentials.from_service_account_file(
        str(path),
        scopes=[_CLOUD_PLATFORM_SCOPE],
    )


def resolve_vertex_auth_context(credentials_path: str) -> VertexAuthContext:
    """Resuelve token OAuth y email de Service Account para Vertex AI."""
    sanitized_path = credentials_path.strip()
    if not sanitized_path:
        raise ValueError("GOOGLE_APPLICATION_CREDENTIALS está vacío.")

    credentials = _load_service_account_credentials(sanitized_path)
    with _REFRESH_LOCK:
        if not credentials.valid or credentials.expired or not credentials.token:
            credentials.refresh(Request())
        token = str(credentials.token or "").strip()

    if not token:
        raise RuntimeError("No se pudo obtener token OAuth para Vertex AI.")

    return VertexAuthContext(
        access_token=token,
        service_account_email=str(
            getattr(credentials, "service_account_email", "") or ""
        ),
    )


def _normalize_label_key(label_key: str) -> str:
    """Normaliza claves de labels al formato compatible con Google Cloud."""
    normalized = _LABEL_ALLOWED_RE.sub("_", label_key.strip().lower())
    normalized = normalized.strip("_-")
    if not normalized:
        return ""
    if not normalized[0].isalpha():
        normalized = f"x_{normalized}"
    return normalized[:_LABEL_MAX_LENGTH]


def _normalize_label_value(label_value: str) -> str:
    """Normaliza valores de labels al formato compatible con Google Cloud."""
    normalized = _LABEL_ALLOWED_RE.sub("_", label_value.strip().lower())
    normalized = normalized.strip("_-")
    return normalized[:_LABEL_MAX_LENGTH]


def build_vertex_labels(
    *,
    enabled: bool,
    namespace: str,
    service: str,
    use_case_id: str,
    model_name: str,
    service_account_email: str,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construye labels base de Vertex y permite overrides opcionales."""
    if not enabled:
        return {}

    labels: dict[str, str] = {
        "namespace": namespace,
        "service": service,
        "use_case_id": use_case_id,
        "model_name": model_name,
        "service_account": service_account_email.replace("@", "_at_"),
    }

    if overrides:
        for key, value in overrides.items():
            labels[str(key)] = str(value)

    normalized: dict[str, str] = {}
    for key, value in labels.items():
        label_key = _normalize_label_key(str(key))
        label_value = _normalize_label_value(str(value))
        if not label_key or not label_value:
            continue
        normalized[label_key] = label_value

    return normalized
