"""Montaje del servidor MCP sobre la app FastAPI existente.

Expone un endpoint ``/mcp`` (transporte HTTP streamable) que ofrece:

- **Tools** derivadas automáticamente del OpenAPI de FastAPI. Solo se publican las
  operaciones de consulta, lectura e ingesta; los endpoints
  administrativos/destructivos quedan fuera mediante un filtro
  ``include_operations`` (default-deny).
- **Prompts** que guían a los agentes en el uso de ``query_repo`` y
  ``query_retrieval`` (ver ``mcp_prompts.py``).
- **Resources** de guía estática y estado en vivo de los repos (ver
  ``mcp_resources.py``).

``fastapi-mcp`` solo deriva tools; prompts y resources se registran sobre el
servidor MCP low-level subyacente (``mcp.server``) tras construir ``FastApiMCP`` y
antes de ``mount_http`` para que sus capabilities se anuncien en el handshake.
"""

import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi_mcp import AuthConfig, FastApiMCP

from coderag.api.identity_headers import IDENTITY_HEADER_NAMES
from coderag.api.mcp_prompts import register_mcp_prompts
from coderag.api.mcp_resources import register_mcp_resources
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

# Metadata publicada en GET /info (contrato Hexa). "tools" refleja que este
# servidor expone N operaciones discretas y sincrónicas (no un pipeline agent
# de orquestación interna de cara a Hexa).
MCP_SERVER_TYPE = "tools"

# Campos que pueden contener datos libres ingresados por usuarios (parámetros
# de tools o contenido devuelto). Declarados para que Hexa configure su
# DualLLM Sanitizer.
MCP_SENSITIVE_FIELDS: list[str] = [
    "query",
    "question",
    "answer",
]


def _parse_bearer_token(authorization: str | None) -> str | None:
    """Extrae el token de un header ``Authorization: Bearer <token>``.

    Retorna ``None`` si el header está ausente o no sigue el esquema Bearer
    (comparación case-insensitive del esquema, con trim del token).
    """
    if not authorization:
        return None
    parts = authorization.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _ensure_mcp_access(
    authorization: str | None = Header(default=None),
) -> None:
    """Protege el endpoint MCP con flag y token Bearer dedicado.

    Contrato Hexa: ``Authorization: Bearer {MCP_API_TOKEN}`` y HTTP 401 si el
    header falta o el token no coincide. 404 si el servidor MCP está
    deshabilitado. Cuando no se define ``MCP_API_TOKEN`` el acceso queda
    abierto (solo protegido por el feature flag); el arranque emite una
    advertencia de seguridad en ese caso.
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
    if expected_token:
        token = _parse_bearer_token(authorization)
        if token != expected_token:
            raise HTTPException(
                status_code=401,
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
        # Reenvía la identidad del llamante desde la conexión /mcp a cada tool.
        headers=["authorization", *IDENTITY_HEADER_NAMES],
    )
    # Registrar prompts y resources sobre el servidor MCP low-level ANTES de
    # mount_http: sus capabilities se calculan por conexión inspeccionando los
    # handlers registrados, por lo que deben existir en el momento del montaje.
    prompt_count = register_mcp_prompts(mcp.server)
    resource_count = register_mcp_resources(mcp.server, mcp._http_client)
    mcp.mount_http(mount_path=settings.mcp_mount_path)
    _log.info(
        "Servidor MCP montado en %s con %d tools, %d prompts y %d resources.",
        settings.mcp_mount_path,
        len(MCP_INCLUDED_OPERATIONS),
        prompt_count,
        resource_count,
    )
    return mcp
