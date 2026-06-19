"""Headers de identidad aceptados/reenviados por los endpoints expuestos vía MCP.

Los servicios MCP reenvían estos headers desde la conexión ``/mcp`` hacia cada
llamada interna a la tool (allowlist de ``fastapi-mcp``). Declararlos como
dependencia opcional en los endpoints expuestos los documenta en OpenAPI/Swagger
sin alterar la firma de los handlers (pass-through, sin enforcement).
"""

from dataclasses import dataclass

from fastapi import Header

# Allowlist de reenvío para FastApiMCP (en minúsculas; se suma a 'authorization').
IDENTITY_HEADER_NAMES: list[str] = ["x-role-id", "x-user-id", "x-country-id"]


@dataclass(frozen=True)
class IdentityContext:
    """Identidad opcional del llamante propagada vía headers."""

    role_id: str | None
    user_id: str | None
    country_id: str | None


def identity_headers(
    x_role_id: str | None = Header(default=None, alias="x-role-id"),
    x_user_id: str | None = Header(default=None, alias="x-user-id"),
    x_country_id: str | None = Header(default=None, alias="x-country-id"),
) -> IdentityContext:
    """Declara los 3 headers de identidad (opcionales, pass-through)."""
    return IdentityContext(
        role_id=x_role_id,
        user_id=x_user_id,
        country_id=x_country_id,
    )
