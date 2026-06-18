"""Montaje del servidor MCP sobre la app FastAPI existente.

Expone un endpoint ``/mcp`` (transporte HTTP streamable) cuyas tools se derivan
automáticamente del OpenAPI de FastAPI. Solo se publican las operaciones de
consulta, lectura e ingesta; los endpoints administrativos/destructivos quedan
fuera mediante un filtro ``include_operations`` (default-deny).
"""

import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi_mcp import AuthConfig, FastApiMCP

from coderag.core.settings import Settings, get_settings

_log = logging.getLogger(__name__)

# Operaciones publicadas como tools MCP. Default-deny: cualquier operation_id
# no listado aquí (ingesta, jobs, inventario, catálogo, admin) queda fuera.
MCP_INCLUDED_OPERATIONS: list[str] = [
    "query_repo",
    "query_retrieval",
    "list_repos",
    "repo_status",
    "storage_health",
]


def _ensure_mcp_access(
    token: str | None = Header(default=None, alias="X-MCP-Token"),
) -> None:
    """Protege el endpoint MCP con flag y token dedicado.

    Espeja el contrato de los endpoints admin: 404 si el servidor MCP está
    deshabilitado y 403 si hay un token configurado que no coincide. Cuando no
    se define ``MCP_API_TOKEN`` el acceso queda abierto (solo protegido por el
    feature flag); el arranque emite una advertencia de seguridad en ese caso.
    """
    settings = get_settings()
    if not bool(getattr(settings, "mcp_enabled", False)):
        raise HTTPException(
            status_code=404,
            detail={
                "message": "El servidor MCP está deshabilitado.",
                "code": "mcp_disabled",
            },
        )

    expected_token = str(getattr(settings, "mcp_api_token", "") or "").strip()
    if expected_token and (token or "").strip() != expected_token:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Token inválido para el endpoint MCP.",
                "code": "invalid_mcp_token",
            },
        )


def setup_mcp(app: FastAPI, settings: Settings | None = None) -> FastApiMCP:
    """Crea y monta el servidor MCP sobre ``app``.

    Debe invocarse tras registrar todas las rutas, ya que ``fastapi-mcp``
    introspecta el OpenAPI en el momento del montaje.
    """
    settings = settings or get_settings()
    auth_config = AuthConfig(dependencies=[Depends(_ensure_mcp_access)])
    mcp = FastApiMCP(
        app,
        name=settings.mcp_server_name,
        include_operations=MCP_INCLUDED_OPERATIONS,
        auth_config=auth_config,
    )
    mcp.mount_http(mount_path=settings.mcp_mount_path)
    _log.info(
        "Servidor MCP montado en %s con %d tools.",
        settings.mcp_mount_path,
        len(MCP_INCLUDED_OPERATIONS),
    )
    return mcp
