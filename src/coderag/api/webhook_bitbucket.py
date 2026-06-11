"""Webhook handler para eventos de Bitbucket Server / Data Center."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from coderag.core.models import RepoAuthConfig, RepoIngestRequest
from coderag.core.settings import Settings, get_settings
from coderag.jobs.worker import IngestionConflictError, JobManager

_log = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


# ---------------------------------------------------------------------------
# Helpers de validación
# ---------------------------------------------------------------------------

def _verify_hmac(secret: str, raw_body: bytes, signature_header: str | None) -> None:
    """Lanza 403 si la firma HMAC-SHA256 no coincide."""
    if not secret:
        _log.warning("WEBHOOK_BITBUCKET_SECRET vacío — validación HMAC deshabilitada")
        return
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Falta el header X-Hub-Signature",
        )
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Formato de X-Hub-Signature no soportado; se esperaba sha256=<hash>",
        )
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[len(prefix):]
    if not hmac.compare_digest(expected, received):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma HMAC inválida",
        )


def _parse_registry(raw_json: str) -> dict[str, Any]:
    """Parsea WEBHOOK_BITBUCKET_REPO_REGISTRY; lanza 500 si está mal formado."""
    try:
        registry = json.loads(raw_json or "{}")
    except json.JSONDecodeError as exc:
        _log.error("WEBHOOK_BITBUCKET_REPO_REGISTRY JSON inválido: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuración del servidor mal formada (WEBHOOK_BITBUCKET_REPO_REGISTRY)",
        ) from exc
    if not isinstance(registry, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WEBHOOK_BITBUCKET_REPO_REGISTRY debe ser un objeto JSON",
        )
    return registry


def _resolve_http_clone_url(
    payload: dict[str, Any],
    internal_base_url: str,
    project_key: str,
    repo_slug: str,
) -> str:
    """Extrae la URL HTTP de clon del payload y reemplaza el host por el interno.

    El payload de Bitbucket contiene el hostname público (ej. bitbucket.agile.bns).
    La API en K8s debe usar el hostname interno del cluster para clonar
    (ej. bitbucket-cl.external.svc). Si WEBHOOK_BITBUCKET_INTERNAL_BASE_URL está
    configurado, se reemplaza scheme+host manteniendo el path del payload.

    Fallback: si el payload no trae clone URL, construye la URL desde internal_base_url
    + project_key + repo_slug (misma lógica que Jenkinsfile líneas 161-180).
    """
    clone_links: list[Any] = (
        payload.get("pullRequest", {})
        .get("toRef", {})
        .get("repository", {})
        .get("links", {})
        .get("clone", [])
    )
    http_url = next(
        (
            link.get("href", "")
            for link in clone_links
            if isinstance(link, dict) and str(link.get("href", "")).startswith("http")
        ),
        None,
    )
    if http_url:
        if internal_base_url:
            path = urlparse(http_url).path
            return f"{internal_base_url.rstrip('/')}{path}"
        return http_url
    if project_key and repo_slug and internal_base_url:
        return f"{internal_base_url.rstrip('/')}/scm/{project_key}/{repo_slug}.git"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="No se pudo resolver la URL HTTP del repositorio desde el payload del webhook",
    )


def _build_ingest_request(
    *,
    settings: Settings,
    repo_config: dict[str, Any],
    defaults: dict[str, Any],
    repo_url: str,
    branch: str,
    commit: str | None,
) -> RepoIngestRequest:
    """Construye RepoIngestRequest a partir de la configuración del registro y la cuenta de servicio."""

    def _get(key: str, fallback: str = "") -> str:
        return str(repo_config.get(key) or defaults.get(key) or fallback)

    auth_username = settings.webhook_bitbucket_auth_username
    auth_secret = settings.webhook_bitbucket_auth_secret

    auth: RepoAuthConfig | None = None
    if auth_username and auth_secret:
        auth = RepoAuthConfig(
            deployment=_get("auth_deployment", "server"),
            transport=_get("auth_transport", "https"),
            method=_get("auth_method", "http_basic"),
            username=auth_username,
            secret=auth_secret,
        )

    return RepoIngestRequest(
        provider=_get("provider", "bitbucket"),
        repo_url=repo_url,
        branch=branch,
        commit=commit or None,
        embedding_provider=_get("embedding_provider") or None,
        embedding_model=_get("embedding_model") or None,
        auth=auth,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _get_job_manager_dep(request: Request) -> JobManager:
    """Dependencia FastAPI para resolver JobManager desde app.state."""
    override = getattr(request.app.state, "job_manager_override", None)
    if override is not None:
        return override
    return request.app.state.job_manager


@router.post(
    "/webhook/bitbucket",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recibe eventos de Bitbucket y dispara ingesta de repositorio",
    description=(
        "Procesa eventos `pr:merged` de Bitbucket Server / Data Center. "
        "Solo actúa sobre repositorios habilitados en `WEBHOOK_BITBUCKET_REPO_REGISTRY` "
        "y cuya rama destino esté en la lista `target_branches` del repo (o los defaults). "
        "Devuelve 202 con el job_id creado, o 200 si el evento se ignora."
    ),
)
async def bitbucket_webhook(
    request: Request,
    x_hub_signature: str | None = Header(default=None, alias="X-Hub-Signature"),
    x_event_key: str | None = Header(default=None, alias="X-Event-Key"),
    settings: Settings = Depends(get_settings),
    job_manager: JobManager = Depends(_get_job_manager_dep),
) -> dict[str, Any]:
    raw_body = await request.body()

    # 1. Validar firma HMAC
    _verify_hmac(settings.webhook_bitbucket_secret, raw_body, x_hub_signature)

    # 2. Parsear payload JSON
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payload JSON inválido: {exc}",
        ) from exc

    event_key = x_event_key or payload.get("eventKey", "")

    # 3. Filtrar solo pr:merged
    if event_key != "pr:merged":
        _log.debug("Webhook ignorado: event_key=%s", event_key)
        return JSONResponse(
            {"ignored": True, "reason": f"event_key '{event_key}' no es pr:merged"},
            status_code=status.HTTP_200_OK,
        )

    # 4. Extraer campos del payload (rutas del Jenkinsfile:97-106)
    pr = payload.get("pullRequest", {})
    to_ref = pr.get("toRef", {})
    target_branch: str = to_ref.get("displayId", "")
    repo_info = to_ref.get("repository", {})
    project_key: str = repo_info.get("project", {}).get("key", "")
    repo_slug: str = repo_info.get("slug", "")
    latest_commit: str | None = to_ref.get("latestCommit") or None

    if not project_key or not repo_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload incompleto: falta pullRequest.toRef.repository.project.key o slug",
        )

    repo_key = f"{project_key}/{repo_slug}"

    # 5. Consultar registro de repos
    registry = _parse_registry(settings.webhook_bitbucket_repo_registry)
    defaults: dict[str, Any] = registry.get("_defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}

    repo_config = registry.get(repo_key)
    if not isinstance(repo_config, dict) or repo_config.get("enabled") is False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Repo '{repo_key}' no está registrado o está deshabilitado",
        )

    # 6. Verificar rama destino
    raw_target_branches = repo_config.get("target_branches") or defaults.get("target_branches")
    if isinstance(raw_target_branches, list):
        allowed_branches = [str(b) for b in raw_target_branches if b]
    else:
        allowed_branches = [
            b.strip()
            for b in settings.webhook_bitbucket_target_branches.split(",")
            if b.strip()
        ]

    if target_branch not in allowed_branches:
        _log.debug(
            "Webhook ignorado: repo=%s branch=%s allowed=%s",
            repo_key,
            target_branch,
            allowed_branches,
        )
        return JSONResponse(
            {
                "ignored": True,
                "reason": (
                    f"rama '{target_branch}' no está en target_branches {allowed_branches} "
                    f"para '{repo_key}'"
                ),
            },
            status_code=status.HTTP_200_OK,
        )

    # 7. Resolver URL HTTP del repositorio (rebase al hostname interno de K8s)
    repo_url = _resolve_http_clone_url(
        payload,
        internal_base_url=settings.webhook_bitbucket_internal_base_url,
        project_key=project_key,
        repo_slug=repo_slug,
    )

    # 8. Construir request y disparar ingesta
    ingest_request = _build_ingest_request(
        settings=settings,
        repo_config=repo_config,
        defaults=defaults,
        repo_url=repo_url,
        branch=target_branch,
        commit=latest_commit,
    )

    _log.info(
        "Webhook bb: disparando ingesta repo=%s branch=%s commit=%s",
        repo_key,
        target_branch,
        latest_commit or "HEAD",
    )

    try:
        job_info = job_manager.create_ingest_job(ingest_request)
    except IngestionConflictError as exc:
        _log.info("Webhook bb: ingesta ya activa para %s, ignorando: %s", repo_key, exc)
        return JSONResponse(
            {"ignored": True, "reason": f"ingesta ya activa para '{repo_key}': {exc}"},
            status_code=status.HTTP_200_OK,
        )

    return {
        "job_id": job_info.id,
        "status": job_info.status,
        "repo_id": job_info.repo_id,
    }


