"""Prompts MCP para guiar a los agentes en el uso de las tools de consulta.

``fastapi-mcp`` solo deriva *tools* del OpenAPI; no expone prompts. Este módulo
registra prompts sobre el servidor MCP low-level subyacente (``mcp.server``) para
enseñar a los agentes *cuándo* y *cómo* usar ``query_repo`` y ``query_retrieval``,
cómo frasear la consulta para activar los atajos graph-first internos (impacto,
reverse-import, inventario) y cómo interpretar la evidencia.

Se registra tras construir ``FastApiMCP`` y antes de ``mount_http`` para que el
servidor anuncie la capability ``prompts`` en el handshake.
"""

from __future__ import annotations

from mcp.server.lowlevel import Server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)

# --- Texto de los prompts ---------------------------------------------------
#
# Se usan placeholders con formato ``{repo_id}`` / ``{pregunta}`` que se rellenan
# en ``get_prompt`` con los argumentos recibidos. El contenido está en español
# (coherente con el proyecto); los nombres de tools/campos se mantienen en inglés.

_QUERY_REPO_GUIDE = """\
Vas a responder una pregunta sobre el repositorio **{repo_id}** usando la tool MCP \
`query_repo`. Esta tool ejecuta Hybrid RAG (vector Chroma 0.55 + léxico Postgres \
0.45 + expansión de grafo Neo4j), rerankea por intención y **sintetiza una \
respuesta con un LLM**, devolviendo `answer` + `citations` (path, start_line, \
end_line, score, reason) + `diagnostics`.

Antes de llamar:
1. Confirma que `{repo_id}` está `query_ready=true` leyendo el resource \
`rag://repos/{repo_id}/status` (o la tool `repo_status`). Si no está listo, \
detente y reporta el estado; una llamada a un repo no listo devuelve **422** \
(`repo_not_ready` / `embedding_incompatible`), no un resultado vacío.
2. Usa el `repo_id` exacto del resource `rag://repos`; no lo inventes.

Cómo frasear `query` (el fraseo activa atajos graph-first internos):
- Definición de un símbolo → nombra el identificador exacto ("dónde se define \
`run_query`").
- Documentación/guía → incluye términos como "documentación", "guía", "readme".
- Configuración runtime → incluye "config", "settings", "docker", "k8s".
- Impacto de un cambio → "qué archivos se ven impactados si modifico `X.py`" \
(usa "impact" + "cambio"/"modificar" + ruta).
- Quién importa/usa un archivo → "quién importa `X.py`" / "en qué archivos se usa `X`".
- Inventario amplio → "cuáles son todos los controllers/modelos del módulo `Y`".

Parámetros (defaults entre paréntesis):
- `top_n` (60): candidatos antes del rerank. Súbelo (p. ej. 100) para consultas \
amplias o de inventario; bájalo para lookups muy específicos.
- `top_k` (20): evidencia final usada para responder y citar.
- `embedding_provider`/`embedding_model` deben **coincidir** con los de la última \
ingesta (mira `last_embedding_*` en `repo_status`), o la consulta falla por \
incompatibilidad.
- `llm_provider`/`answer_model`/`verifier_model` controlan la síntesis \
(OpenAI/Gemini/Vertex; Anthropic no es provider activo).

Al responder: apóyate solo en `answer` y verifica con `citations`. Si \
`diagnostics` indica `fallback_reason`, acláralo. No afirmes relaciones o módulos \
que no aparezcan en las citas.

Pregunta a resolver: **{pregunta}**
"""

_QUERY_RETRIEVAL_GUIDE = """\
Vas a recuperar **evidencia sin síntesis LLM** del repositorio **{repo_id}** con la \
tool MCP `query_retrieval`. Devuelve `chunks` ranqueados (`id`, `text`, `score`, \
`path`, `start_line`, `end_line`, `kind`, `metadata`), `citations`, `statistics` \
(`total_before_rerank`, `total_after_rerank`, `graph_nodes_count`) y un `answer` \
**extractivo** (no generado por LLM).

Prefiere `query_retrieval` sobre `query_repo` cuando:
- Quieres los fragmentos crudos para razonar tú mismo (eres un agente con tu propio LLM).
- Necesitas trazabilidad exacta / control de tokens y no quieres una respuesta \
pre-sintetizada.
- Quieres evitar coste/latencia del LLM de síntesis del servidor.

Usa `query_repo` en cambio cuando quieras una respuesta en prosa ya redactada y \
verificada.

Parámetros: `top_n` (60) y `top_k` (20) igual que en `query_repo`. \
`embedding_provider`/`embedding_model` deben coincidir con la ingesta. Pon \
`include_context=true` solo si necesitas el contexto ensamblado completo en el \
campo `context` (más tokens).

Requisitos y fraseo: idénticos a `query_repo` — verifica `query_ready` primero \
(resource `rag://repos/{repo_id}/status`); un repo no listo devuelve 422. El mismo \
fraseo activa los atajos graph-first (impact / reverse-import / inventory).

Evidencia a recuperar para: **{pregunta}**
"""

_HYBRID_RAG_WORKFLOW = """\
Flujo recomendado para usar este servidor MCP de código (Hybrid RAG):
1. Lee el resource `rag://repos` para obtener los `repo_id` indexados.
2. Lee `rag://repos/{{repo_id}}/status` y confirma `query_ready=true` y \
`embedding_compatible`. Si no, detente y reporta.
3. Elige tool: `query_repo` (respuesta redactada por LLM + citas) vs \
`query_retrieval` (solo evidencia/chunks para que razones tú). Ante la duda, \
empieza por `query_retrieval`.
4. Frasea según el objetivo (ver resource `rag://guide/query-cookbook`) para \
activar los atajos graph-first de impacto, reverse-import o inventario.
5. Ajusta `top_n`/`top_k` (ver `rag://guide/parameters`). Mantén `embedding_*` \
alineado a la ingesta.
6. Fundamenta toda afirmación en `citations`; no inventes módulos ni relaciones.
"""


# --- Definición declarativa de los prompts ----------------------------------

_ARG_REPO_ID = PromptArgument(
    name="repo_id",
    description="Repositorio indexado objetivo (ver resource rag://repos).",
    required=True,
)
_ARG_PREGUNTA = PromptArgument(
    name="pregunta",
    description="Pregunta en lenguaje natural a resolver.",
    required=True,
)

_PROMPTS: list[Prompt] = [
    Prompt(
        name="query_repo_guide",
        description=(
            "Guía para responder una pregunta con la tool query_repo "
            "(Hybrid RAG + síntesis LLM con citas)."
        ),
        arguments=[_ARG_REPO_ID, _ARG_PREGUNTA],
    ),
    Prompt(
        name="query_retrieval_guide",
        description=(
            "Guía para recuperar evidencia cruda con la tool query_retrieval "
            "(sin síntesis LLM)."
        ),
        arguments=[_ARG_REPO_ID, _ARG_PREGUNTA],
    ),
    Prompt(
        name="hybrid_rag_workflow",
        description=(
            "Flujo end-to-end recomendado: verificar readiness, elegir tool, "
            "frasear y fundamentar en citas."
        ),
        arguments=[],
    ),
]

_TEMPLATES: dict[str, str] = {
    "query_repo_guide": _QUERY_REPO_GUIDE,
    "query_retrieval_guide": _QUERY_RETRIEVAL_GUIDE,
    "hybrid_rag_workflow": _HYBRID_RAG_WORKFLOW,
}


def _render(name: str, arguments: dict[str, str] | None) -> str:
    """Rellena el template del prompt con los argumentos recibidos.

    ``hybrid_rag_workflow`` no toma argumentos: su texto usa ``{{repo_id}}``
    escapado para mostrar el patrón del URI sin requerir sustitución.
    """
    template = _TEMPLATES[name]
    args = arguments or {}
    if name == "hybrid_rag_workflow":
        return template.format()
    return template.format(
        repo_id=args.get("repo_id", "<repo_id>"),
        pregunta=args.get("pregunta", "<pregunta>"),
    )


def register_mcp_prompts(server: Server) -> int:
    """Registra los handlers de prompts sobre el servidor MCP low-level.

    Debe llamarse antes de ``mount_http`` para que la capability ``prompts`` se
    anuncie en el handshake. Devuelve la cantidad de prompts registrados.
    """

    @server.list_prompts()
    async def _list_prompts() -> list[Prompt]:
        return _PROMPTS

    @server.get_prompt()
    async def _get_prompt(
        name: str, arguments: dict[str, str] | None
    ) -> GetPromptResult:
        if name not in _TEMPLATES:
            raise ValueError(f"Prompt desconocido: {name}")
        prompt = next(p for p in _PROMPTS if p.name == name)
        return GetPromptResult(
            description=prompt.description,
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=_render(name, arguments)),
                )
            ],
        )

    return len(_PROMPTS)
