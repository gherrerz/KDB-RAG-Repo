"""Resources MCP: guías estáticas + estado en vivo de los repos indexados.

``fastapi-mcp`` no expone resources; se registran sobre el servidor MCP low-level
subyacente (``mcp.server``). Hay dos familias:

- **Estáticos** (``rag://guide/*``): documentos markdown autorados que enseñan a
  los agentes a explotar las tools sin adivinar contratos ni capacidades.
- **Dinámicos** (``rag://repos``, ``rag://repos/{repo_id}/status``): leen estado en
  vivo reutilizando el cliente HTTP ASGI interno de ``FastApiMCP`` (mismas rutas
  REST, mismo proceso) para que el agente descubra el ``repo_id`` correcto y su
  readiness antes de consultar.

Se registra tras construir ``FastApiMCP`` y antes de ``mount_http`` para que el
servidor anuncie la capability ``resources`` en el handshake.
"""

from __future__ import annotations

import json
import logging

import httpx
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource, ResourceTemplate
from pydantic import AnyUrl

_log = logging.getLogger(__name__)

# Base URL del transporte ASGI interno de fastapi-mcp (server.py: _base_url).
_INTERNAL_BASE_URL = "http://apiserver"
_MD = "text/markdown"
_JSON = "application/json"

# --- Guías estáticas --------------------------------------------------------

_TOOLS_OVERVIEW = """\
# Tools MCP disponibles

Este servidor expone 5 tools derivadas de la API REST. Cuándo usar cada una:

| Tool | Uso |
|------|-----|
| `query_repo` | Pregunta sobre el código → respuesta redactada por LLM + citas. |
| `query_retrieval` | Recupera evidencia cruda (chunks) sin síntesis LLM. |
| `list_repos` | Lista los `repo_id` indexados (o lee el resource `rag://repos`). |
| `repo_status` | Readiness de un repo (`query_ready`, `embedding_compatible`). |
| `storage_health` | Salud de Chroma / Postgres / Neo4j / Redis. |

Regla de oro: verifica readiness (`repo_status` / `rag://repos/{repo_id}/status`)
antes de `query_repo` o `query_retrieval`. Un repo no listo devuelve 422.
"""

_QUERY_COOKBOOK = """\
# Cookbook de fraseo de consultas

El fraseo de `query` activa atajos graph-first dentro de `query_repo` /
`query_retrieval`. Recetas por objetivo:

## Definición de un símbolo
- Query: "dónde se define `run_query`" — nombra el identificador exacto.
- Params: `top_n=60`, `top_k=20` (defaults).

## Documentación / guía
- Query: incluye "documentación", "guía", "readme".
- El reranker prioriza paths de docs.

## Configuración runtime
- Query: incluye "config", "settings", "docker", "k8s".

## Impacto de un cambio (graph-first)
- Query: "qué archivos se ven impactados si modifico `path/al/archivo.py`".
- Requiere "impact"/"impacto" + "cambio"/"modificar" + una ruta.

## Reverse-import (graph-first)
- Query: "quién importa `path/al/archivo.py`" / "en qué archivos se usa `X`".

## Inventario amplio (graph-first)
- Query: "cuáles son todos los controllers/modelos del módulo `Y`".
- Params: sube `top_n` (p. ej. 100) para cobertura amplia.

Fundamenta siempre en `citations`; no inventes relaciones ni módulos.
"""

_PARAMETERS = """\
# Referencia de parámetros

Comunes a `query_repo` y `query_retrieval`:

| Parámetro | Default | Notas |
|-----------|---------|-------|
| `repo_id` | — (requerido) | Usa el valor exacto de `rag://repos`. |
| `query` | — (requerido) | Lenguaje natural; ver `rag://guide/query-cookbook`. |
| `top_n` | 60 | Candidatos antes del rerank. Sube para consultas amplias. |
| `top_k` | 20 | Evidencia final usada para responder/citar. |
| `embedding_provider` | `vertex` | Debe coincidir con la última ingesta. |
| `embedding_model` | `text-embedding-005` | Debe coincidir con la ingesta. |

Solo `query_repo` (síntesis LLM):

| Parámetro | Default | Notas |
|-----------|---------|-------|
| `llm_provider` | `vertex` | OpenAI / Gemini / Vertex (Anthropic no activo). |
| `answer_model` | `gemini-2.5-flash` | Modelo de respuesta. |
| `verifier_model` | `gemini-2.5-flash` | Modelo verificador. |

Solo `query_retrieval`:

| Parámetro | Default | Notas |
|-----------|---------|-------|
| `include_context` | `false` | `true` añade el contexto ensamblado (más tokens). |

Si `embedding_provider`/`embedding_model` no coinciden con la ingesta, la consulta
devuelve 422 por incompatibilidad. Consulta `last_embedding_*` en `repo_status`.
"""

_CAPABILITIES = """\
# Capacidades reales (hoja anti-alucinación)

## Lenguajes indexados
- Python (AST nativo), Java (brace), JavaScript/TypeScript (brace),
  Kotlin (tree-sitter), Swift (tree-sitter), otros (fallback genérico).

## Contenido indexado (colecciones Chroma)
- `code_symbols`: definiciones de funciones/clases/métodos.
- `code_files`: resumen del archivo completo.
- `code_modules`: resumen de módulos/paquetes.

## Metadata por chunk
`repo_id`, `path`, `language`, `symbol_name`, `symbol_type`, `start_line`,
`end_line`, `kind` (`code_chunk` / `file_full` / `module_summary`).

## Búsqueda híbrida
Fusión: vector Chroma (peso 0.55) + léxico Postgres FTS (peso 0.45) + ajuste por
identificadores exactos, luego rerank por intención y expansión de grafo Neo4j
(CALLS / IMPORTS / EXTENDS / IMPLEMENTS).

No asumas capacidades fuera de esta lista.
"""

_ERRORS = """\
# Contrato de errores y recuperación

| Código | Causa | Recuperación |
|--------|-------|--------------|
| 422 `repo_not_ready` | El repo no está `query_ready`. | Espera a que termine la ingesta; revisa `rag://repos/{repo_id}/status`. |
| 422 `embedding_incompatible` | `embedding_*` no coincide con la ingesta. | Usa `last_embedding_provider`/`last_embedding_model` de `repo_status`. |
| 503 | Preflight de storage falló. | Revisa `storage_health`; reintenta cuando los componentes estén sanos. |

Un repo no listo **no** devuelve resultados vacíos: devuelve 422. Verifica siempre
readiness antes de consultar.
"""

_STATIC_RESOURCES: dict[str, tuple[str, str, str]] = {
    # uri: (nombre, descripción, contenido)
    "rag://guide/tools-overview": (
        "Overview de tools MCP",
        "Las 5 tools y cuándo usar cada una.",
        _TOOLS_OVERVIEW,
    ),
    "rag://guide/query-cookbook": (
        "Cookbook de fraseo",
        "Recetas de fraseo por objetivo (incluye atajos graph-first).",
        _QUERY_COOKBOOK,
    ),
    "rag://guide/parameters": (
        "Referencia de parámetros",
        "Parámetros de query_repo/query_retrieval, defaults y tuning.",
        _PARAMETERS,
    ),
    "rag://guide/capabilities": (
        "Capacidades reales",
        "Lenguajes, colecciones, metadata y pesos híbridos (anti-alucinación).",
        _CAPABILITIES,
    ),
    "rag://guide/errors": (
        "Contrato de errores",
        "Errores 422/503 y cómo recuperarse.",
        _ERRORS,
    ),
}

# --- Resources dinámicos ----------------------------------------------------

_REPOS_URI = "rag://repos"
_REPO_STATUS_PREFIX = "rag://repos/"
_REPO_STATUS_SUFFIX = "/status"


def _error_json(message: str, detail: object = None) -> str:
    payload: dict[str, object] = {"error": message}
    if detail is not None:
        payload["detail"] = detail
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _read_repos(client: httpx.AsyncClient) -> ReadResourceContents:
    try:
        resp = await client.get(f"{_INTERNAL_BASE_URL}/repos")
    except httpx.HTTPError as exc:  # red interna/ASGI caída
        _log.warning("resource rag://repos: fallo de transporte: %s", exc)
        return ReadResourceContents(
            content=_error_json("No se pudo consultar el catálogo de repos."),
            mime_type=_JSON,
        )
    if resp.status_code != 200:
        return ReadResourceContents(
            content=_error_json(
                "El catálogo de repos devolvió un estado inesperado.",
                {"status_code": resp.status_code},
            ),
            mime_type=_JSON,
        )
    return ReadResourceContents(content=resp.text, mime_type=_JSON)


async def _read_repo_status(
    client: httpx.AsyncClient, repo_id: str
) -> ReadResourceContents:
    if not repo_id:
        return ReadResourceContents(
            content=_error_json("Falta repo_id en el URI del resource."),
            mime_type=_JSON,
        )
    try:
        resp = await client.get(f"{_INTERNAL_BASE_URL}/repos/{repo_id}/status")
    except httpx.HTTPError as exc:
        _log.warning(
            "resource rag://repos/%s/status: fallo de transporte: %s", repo_id, exc
        )
        return ReadResourceContents(
            content=_error_json(f"No se pudo consultar el estado de '{repo_id}'."),
            mime_type=_JSON,
        )
    if resp.status_code != 200:
        return ReadResourceContents(
            content=_error_json(
                f"El estado de '{repo_id}' devolvió un estado inesperado.",
                {"status_code": resp.status_code},
            ),
            mime_type=_JSON,
        )
    return ReadResourceContents(content=resp.text, mime_type=_JSON)


def register_mcp_resources(server: Server, http_client: httpx.AsyncClient) -> int:
    """Registra handlers de resources (estáticos + dinámicos) sobre el servidor.

    Debe llamarse antes de ``mount_http`` para anunciar la capability
    ``resources``. Devuelve la cantidad de resources fijos listados (los
    estáticos + ``rag://repos``); el template de estado no cuenta como resource
    fijo.
    """

    fixed_resources: list[Resource] = [
        Resource(
            uri=AnyUrl(uri),
            name=name,
            description=description,
            mimeType=_MD,
        )
        for uri, (name, description, _content) in _STATIC_RESOURCES.items()
    ]
    fixed_resources.append(
        Resource(
            uri=AnyUrl(_REPOS_URI),
            name="Repos indexados",
            description="Lista en vivo de repo_id disponibles para consultar.",
            mimeType=_JSON,
        )
    )

    resource_templates: list[ResourceTemplate] = [
        ResourceTemplate(
            uriTemplate="rag://repos/{repo_id}/status",
            name="Readiness de un repo",
            description=(
                "Estado en vivo de un repo: query_ready, embedding_compatible, "
                "conteos y warnings."
            ),
            mimeType=_JSON,
        )
    ]

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        return fixed_resources

    @server.list_resource_templates()
    async def _list_resource_templates() -> list[ResourceTemplate]:
        return resource_templates

    @server.read_resource()
    async def _read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        uri_str = str(uri)
        static = _STATIC_RESOURCES.get(uri_str)
        if static is not None:
            return [ReadResourceContents(content=static[2], mime_type=_MD)]
        if uri_str == _REPOS_URI:
            return [await _read_repos(http_client)]
        if uri_str.startswith(_REPO_STATUS_PREFIX) and uri_str.endswith(
            _REPO_STATUS_SUFFIX
        ):
            repo_id = uri_str[
                len(_REPO_STATUS_PREFIX) : -len(_REPO_STATUS_SUFFIX)
            ]
            return [await _read_repo_status(http_client, repo_id)]
        raise ValueError(f"Resource desconocido: {uri_str}")

    return len(fixed_resources)
