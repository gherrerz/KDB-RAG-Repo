"""Utilidades compartidas para autenticación y labels en Vertex AI."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from functools import lru_cache
import json
from urllib.parse import urlparse
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
    project_id: str


@lru_cache(maxsize=4)
def _load_service_account_credentials_from_info(serialized_info: str):
    """Carga y cachea credenciales de Service Account desde JSON serializado."""
    info = json.loads(serialized_info)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=[_CLOUD_PLATFORM_SCOPE],
    )


def _decode_service_account_info_b64(payload_b64: str) -> dict[str, object]:
    """Decodifica y valida un JSON de Service Account codificado en Base64."""
    try:
        decoded_bytes = base64.b64decode(payload_b64, validate=True)
    except binascii.Error as exc:
        raise ValueError(
            "VERTEX service account JSON B64 no contiene Base64 válido."
        ) from exc

    try:
        decoded_text = decoded_bytes.decode("utf-8")
        payload = json.loads(decoded_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "VERTEX service account JSON B64 no contiene JSON válido."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "VERTEX service account JSON B64 debe decodificar a un objeto JSON."
        )
    return payload


def derive_vertex_location_from_base_url(base_url: str) -> str:
    """Deriva location de una base URL regional de Vertex AI."""
    sanitized_base_url = base_url.strip()
    if not sanitized_base_url:
        return ""

    parsed_url = urlparse(sanitized_base_url)
    hostname = (parsed_url.hostname or "").strip().lower()
    if not hostname:
        return ""

    suffix = "-aiplatform.googleapis.com"
    if not hostname.endswith(suffix):
        return ""

    location = hostname[: -len(suffix)].strip()
    return location.rstrip("-.")


def build_vertex_api_url(
    *,
    base_url: str,
    api_version: str,
    path_template: str,
    project_id: str,
    model_name: str | None = None,
    location: str | None = None,
) -> str:
    """Construye una URL Vertex a partir de base URL y path template."""
    sanitized_base_url = base_url.strip().rstrip("/")
    sanitized_api_version = api_version.strip().strip("/")
    resolved_location = (location or "").strip() or derive_vertex_location_from_base_url(
        sanitized_base_url
    )
    if not sanitized_base_url or not sanitized_api_version or not project_id.strip():
        return ""
    if not resolved_location:
        return ""

    path = path_template.format(
        project=project_id.strip(),
        location=resolved_location,
        model=(model_name or "").strip(),
    ).strip()
    if not path:
        return ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{sanitized_base_url}/{sanitized_api_version}{path}"


def resolve_vertex_auth_context(
    credentials_source: str,
    *,
    token_url: str | None = None,
) -> VertexAuthContext:
    """Resuelve token OAuth de Service Account desde JSON Base64."""
    sanitized_source = credentials_source.strip()
    if not sanitized_source:
        raise ValueError("Faltan credenciales Vertex en VERTEX_SERVICE_ACCOUNT_JSON_B64.")

    service_account_info = _decode_service_account_info_b64(sanitized_source)
    project_id = str(service_account_info.get("project_id") or "").strip()
    sanitized_token_url = (token_url or "").strip()
    if sanitized_token_url:
        service_account_info["token_uri"] = sanitized_token_url
    serialized_info = json.dumps(service_account_info, sort_keys=True)
    credentials = _load_service_account_credentials_from_info(serialized_info)

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
        project_id=project_id,
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
    service: str,
    use_case_id: str,
    model_name: str,
    service_account_email: str,
    service_account_label: str | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construye labels base de Vertex y permite overrides opcionales."""
    if not enabled:
        return {}

    resolved_service_account = (service_account_label or "").strip()
    if not resolved_service_account:
        resolved_service_account = service_account_email.replace("@", "_at_")

    labels: dict[str, str] = {
        "service": service,
        "use_case_id": use_case_id,
        "model_name": model_name,
        "service_account": resolved_service_account,
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
