# Contrato MCP — KDB-RAG-Repo

Documento de referencia autocontenido para integraciones externas (orquestadores
de agentes, gateways MCP, Hexa) que consuman el servidor MCP de este proyecto.
Describe las 3 superficies del protocolo (tools, prompts, resources), sus
payloads exactos de entrada/salida y todos los códigos de error posibles, sin
necesidad de leer el código fuente.

> Para el resto de la API REST (no-MCP) ver [API_REFERENCE.md](API_REFERENCE.md).
> Para variables de entorno ver [CONFIGURATION.md](CONFIGURATION.md).

## 1. Overview

| Aspecto | Valor |
| --- | --- |
| Nombre del servidor MCP | `repositories-kdb-mcp` (default en código sin `MCP_SERVER_NAME`; el `.env.example` distribuido usa `coderag-mcp`) |
| Versión del servicio | `0.1.0` (`app.version`, expuesta en `/health` e `/info`) |
| Protocolo MCP | `mcp==1.28.1` |
| Envoltura HTTP | `fastapi-mcp==0.4.0` |
| Transporte | HTTP streamable (`Accept: application/json, text/event-stream`) |
| Endpoint de montaje | `POST/GET {MCP_MOUNT_PATH}` (default `/mcp`) |
| Coexistencia | El servidor MCP se monta sobre la misma app FastAPI y el mismo proceso/puerto que la API REST, después de registrar todas las rutas (`src/coderag/api/mcp_server.py::setup_mcp`, invocado desde `server.py` si `mcp_enabled`). |
| server_type (`/info`) | `"tools"` — operaciones discretas y sincrónicas, no un pipeline de orquestación interna. |

Primitivas publicadas:

| Primitiva | Cantidad | Detalle |
| --- | --- | --- |
| Tools | 5 | Sección 4 |
| Prompts | 3 | Sección 5 |
| Resources | 7 (5 estáticos + 2 dinámicos) | Sección 6 |

Log de arranque esperado (`src/coderag/api/mcp_server.py::setup_mcp`):

```text
Servidor MCP montado en /mcp con 5 tools, 3 prompts y 7 resources.
```

## 2. Autenticación

Función `_ensure_mcp_access()` en `src/coderag/api/mcp_server.py`, aplicada como
dependencia de `AuthConfig` sobre **todo** el endpoint `/mcp` (todas las
tools comparten esta única puerta de entrada):

1. **Feature flag** — si `MCP_ENABLED=false`:
   - HTTP `404`
   - Body: `{"message": "El servidor MCP está deshabilitado.", "code": "mcp_disabled"}`
2. **Bearer token** — si `MCP_API_TOKEN` está configurado (no vacío):
   - Header requerido: `Authorization: Bearer {MCP_API_TOKEN}` (esquema
     case-insensitive, token con trim automático).
   - Si el header falta, no sigue el esquema `Bearer`, o el token no coincide:
     HTTP `401` con body
     `{"message": "Token inválido para el endpoint MCP.", "code": "invalid_mcp_token"}`.
3. **Sin token configurado** (`MCP_API_TOKEN=""`): el endpoint queda accesible
   solo protegido por el feature flag. Al arranque se emite una advertencia de
   seguridad (`MCP_ENABLED=true sin MCP_API_TOKEN: /mcp quedará accesible sin
   autenticación...`).

`GET /health` y `GET /info` **no** requieren autenticación (contrato Hexa).

### Headers de identidad (pass-through, opcionales)

El servidor MCP reenvía estos headers desde la conexión `/mcp` hacia cada
llamada interna de tool (allowlist de `fastapi-mcp`, declarados en el OpenAPI
de cada operación expuesta vía `Depends(identity_headers)`):

| Header | Obligatorio | Descripción |
| --- | --- | --- |
| `x-role-id` | No | Rol del llamante. |
| `x-user-id` | No | ID del usuario del llamante. |
| `x-country-id` | No | País del llamante. |

Por limitación de `fastapi-mcp==0.4.0` estos headers **no** aparecen como
argumentos JSON de la tool; se fijan una única vez en la conexión `/mcp`
(inicialización del cliente MCP o del gateway) y aplican a todas las llamadas
de esa sesión.

## 3. Endpoints públicos no-MCP

### 3.1 `GET /health`

Sin autenticación. Ejecuta preflight forzado (`context="health"`) de todos
los componentes de storage críticos y no críticos. Handler `storage_health`
(operation_id de la tool MCP homónima).

Modelo `StorageHealthResponse` (`src/coderag/core/models.py`) — combina el
shape legacy detallado con los campos exigidos por el contrato Hexa:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `ok` | `bool` | Estado global consolidado (legacy). |
| `strict` | `bool` | Si se aplicó modo estricto en la evaluación. |
| `checked_at` | `str` | Fecha/hora ISO del chequeo. |
| `context` | `str` | Contexto del preflight (`"health"` en esta llamada). |
| `repo_id` | `str \| null` | Repo evaluado, si aplica (`null` en `/health` global). |
| `cached` | `bool` | Si el resultado proviene de caché interna. |
| `failed_components` | `list[str]` | Componentes fallidos. |
| `items` | `list[StorageHealthItem]` | Detalle por componente (`name`, `ok`, `critical`, `code`, `message`, `latency_ms`, `details`). |
| `postgres_startup` | `PostgresStartupStatus \| null` | Estado de bootstrap de migraciones Alembic. |
| `status` | `"healthy" \| "degraded" \| "unhealthy" \| null` | **Contrato Hexa**: estado consolidado. |
| `name` | `str \| null` | **Contrato Hexa**: nombre del servidor MCP. |
| `version` | `str \| null` | **Contrato Hexa**: versión semántica. |
| `uptime_s` | `int \| null` | **Contrato Hexa**: segundos desde el arranque. |
| `dependencies` | `dict[str, McpDependencyStatus] \| null` | **Contrato Hexa**: estado por dependencia (`{status, latency_ms}`), derivado de `items`. |

Ejemplo de respuesta:

```json
{
  "ok": true,
  "strict": true,
  "checked_at": "2026-01-01T00:00:00+00:00",
  "context": "health",
  "repo_id": null,
  "cached": false,
  "failed_components": [],
  "items": [
    { "name": "postgres", "ok": true, "critical": true, "code": "reachable", "message": "OK", "latency_ms": 5.2, "details": {} },
    { "name": "chroma", "ok": true, "critical": true, "code": "reachable", "message": "OK", "latency_ms": 18.7, "details": {} }
  ],
  "postgres_startup": { "enabled": true, "policy": "auto", "action": "upgrade_head", "current_heads": ["abc123"], "expected_heads": ["abc123"], "cached": true },
  "status": "healthy",
  "name": "repositories-kdb-mcp",
  "version": "0.1.0",
  "uptime_s": 3600,
  "dependencies": {
    "postgres": { "status": "healthy", "latency_ms": 5.2 },
    "chroma": { "status": "healthy", "latency_ms": 18.7 }
  }
}
```

Códigos HTTP: `200` siempre (el estado degradado/unhealthy se refleja en el
body, no en el status code).

### 3.2 `GET /info`

Sin autenticación. Metadata estática, no depende de dependencias runtime.

Modelo `McpInfoResponse`:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `name` | `str` | Nombre único del servidor MCP. |
| `version` | `str` | Versión semántica del servicio. |
| `server_type` | `"tools" \| "agent"` | `"tools"` en este servidor. |
| `description` | `str` | Descripción legible del sistema integrado. |
| `sensitive_fields` | `list[str]` | Campos con contenido libre de usuario. |

Ejemplo de respuesta:

```json
{
  "name": "repositories-kdb-mcp",
  "version": "0.1.0",
  "server_type": "tools",
  "description": "Ingesta y consulta de repositorios Git con RAG híbrido (vector + lexical + grafo).",
  "sensitive_fields": ["query", "question", "answer"]
}
```

Códigos HTTP: `200` siempre.

## 4. Tools MCP (5)

Tabla resumen (`operation_id` = nombre de la tool en `tools/list`):

| Tool | Método + path REST | Resumen |
| --- | --- | --- |
| `query_repo` | `POST /query` | Hybrid RAG con síntesis LLM sobre un repositorio indexado. |
| `query_retrieval` | `POST /query/retrieval` | Retrieval-only: evidencia ranqueada sin síntesis LLM. |
| `list_repos` | `GET /repos` | Catálogo de repositorios disponibles para consulta. |
| `repo_status` | `GET /repos/{repo_id}/status` | Readiness de consulta de un repositorio (`query_ready`, compatibilidad de embeddings). |
| `storage_health` | `GET /health` | Estado consolidado de todos los componentes de storage (misma tool que la Sección 3.1). |

Todos los errores de tools usan el shape estándar `{error, message, retryable}`
descrito en la Sección 7. Todas las llamadas se invocan como JSON-RPC 2.0
`tools/call` sobre el transporte `/mcp` ya autenticado (Sección 2).

Recordatorio de restricción de runtime: `/query` y `/query/retrieval`
**exigen** `query_ready=true` y compatibilidad de embeddings, o retornan
`422`. Se recomienda invocar `repo_status` antes de `query_repo`/
`query_retrieval` para repositorios no verificados previamente.

---

### 4.1 `query_repo`

**Cuerpo** (`QueryRequest`):

| Campo | Tipo | Default | Descripción |
| --- | --- | --- | --- |
| `repo_id` | `str` | requerido | Repositorio indexado objetivo (ej. `"gherrerz-kdb-rag-repo-main"`). |
| `query` | `str` | requerido | Pregunta en lenguaje natural. |
| `top_n` | `int` | `60` | Candidatos recuperados antes del reranking (`ge=1`). |
| `top_k` | `int` | `20` | Cantidad final tras reranking usada para contexto/citas (`ge=1`). |
| `embedding_provider` | `str \| null` | `"vertex"` | `"openai"` \| `"gemini"` \| `"vertex"`. |
| `embedding_model` | `str \| null` | `"text-embedding-005"` | Modelo de embeddings para vectorizar la query. |
| `llm_provider` | `str \| null` | `"vertex"` | `"openai"` \| `"gemini"` \| `"vertex"`. **Anthropic no es provider activo.** |
| `answer_model` | `str \| null` | `"gemini-2.5-flash"` | Modelo de síntesis de respuesta. |
| `verifier_model` | `str \| null` | `"gemini-2.5-flash"` | Modelo verificador de la respuesta. |

**Respuesta** (`QueryResponse`):

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `answer` | `str` | Respuesta final al usuario. |
| `citations` | `list[Citation]` | Evidencia trazable utilizada para responder. |
| `diagnostics` | `dict[str, Any]` | Diagnóstico técnico (timings, fallback, conteos). |

`Citation`: `path: str`, `start_line: int`, `end_line: int`, `score: float`,
`reason: str` (`hybrid_rag_match` \| `inventory_graph_match` \|
`graph_file_dependency_match` \| `graph_external_dependency_source`).

**Errores:**

| HTTP | Body | Causa |
| --- | --- | --- |
| `422` | `{"error": "REPO_VALIDATION", "retryable": false, "message": "El embedding seleccionado para consulta no es compatible con la última ingesta del repositorio...", "code": "embedding_incompatible", "repo_status": {RepoQueryStatusResponse}}` | `embedding_provider`/`embedding_model` de la query no coincide con la última ingesta persistida del repo. |
| `422` | `{"error": "REPO_VALIDATION", "retryable": false, "message": "El repositorio no está listo para consultas...", "code": "repo_not_ready", "repo_status": {RepoQueryStatusResponse}}` | `query_ready=false` (repo no ingerido, índices incompletos). |
| `503` | `{"error": "REPO_UNAVAILABLE", "retryable": true, "message": "Preflight de storage falló antes de consulta.", "health": {StorageHealthResponse}}` | Preflight de storage (Postgres/Chroma/Neo4j) falló antes de ejecutar la consulta. |

**Ejemplo `tools/call`:**

```json
{ "jsonrpc": "2.0", "id": 10, "method": "tools/call",
  "params": { "name": "query_repo", "arguments": {
    "repo_id": "gherrerz-kdb-rag-repo-main",
    "query": "cuales son todos los controller del modulo mall-admin?" } } }
```

```json
{ "jsonrpc": "2.0", "id": 10, "result": { "content": [{ "type": "text",
  "text": "{\"answer\": \"...\", \"citations\": [{\"path\": \"mall-admin/src/controller/BrandController.java\", \"start_line\": 1, \"end_line\": 40, \"score\": 0.88, \"reason\": \"hybrid_rag_match\"}], \"diagnostics\": {\"timings_ms\": {\"hybrid_retrieval\": 245, \"reranking\": 120, \"llm_synthesis\": 1850}, \"graph_hops\": 2}}" }] } }
```

**Ejemplo `tools/call` (error `repo_not_ready`):**

```json
{ "jsonrpc": "2.0", "id": 10, "result": { "isError": true, "content": [{ "type": "text",
  "text": "{\"error\": \"REPO_VALIDATION\", \"retryable\": false, \"message\": \"El repositorio no está listo para consultas. Reingesta el repositorio o revisa el estado de índices.\", \"code\": \"repo_not_ready\", \"repo_status\": {\"repo_id\": \"mall\", \"listed_in_catalog\": true, \"query_ready\": false, \"chroma_counts\": {\"code_symbols\": 0, \"code_files\": 0, \"code_modules\": 0}, \"lexical_loaded\": false, \"graph_available\": null, \"warnings\": [\"No hay corpus léxico listo para repo 'mall'.\"]}}" }] } }
```

> Nota de transporte: `fastapi-mcp==0.4.0` propaga el `status_code` HTTP
> original y serializa el `detail` como texto JSON dentro de `content`; no
> implementa aún `isError` estricto por tool (ver Sección 7).

**Política anti-alucinación:** no inventar relaciones/módulos ausentes del
repositorio ingerido; si no hay evidencia, `answer` debe indicarlo
explícitamente en vez de completar con contenido no verificado.

---

### 4.2 `query_retrieval`

**Cuerpo** (`RetrievalQueryRequest`):

| Campo | Tipo | Default | Descripción |
| --- | --- | --- | --- |
| `repo_id` | `str` | requerido | Repositorio indexado objetivo. |
| `query` | `str` | requerido | Pregunta en lenguaje natural para retrieval de evidencia. |
| `top_n` | `int` | `60` | Candidatos recuperados antes del reranking (`ge=1`). |
| `top_k` | `int` | `20` | Cantidad final tras reranking retornada como evidencia (`ge=1`). |
| `embedding_provider` | `str \| null` | `"vertex"` | Proveedor de embeddings para vectorizar la query. |
| `embedding_model` | `str \| null` | `"text-embedding-005"` | Modelo de embeddings para vectorizar la query. |
| `include_context` | `bool` | `false` | Incluye el contexto ensamblado completo del pipeline en la respuesta. |

**Respuesta** (`RetrievalQueryResponse`):

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `mode` | `str` | `"retrieval_only"`. |
| `answer` | `str` | Resumen textual extractivo basado en evidencia recuperada (sin LLM). |
| `chunks` | `list[RetrievedChunk]` | Evidencia ranqueada recuperada. |
| `citations` | `list[Citation]` | Citas trazables asociadas a la evidencia. |
| `statistics` | `RetrievalStatistics` | `{total_before_rerank, total_after_rerank, graph_nodes_count}`. |
| `diagnostics` | `dict[str, Any]` | Diagnóstico técnico del pipeline retrieval-only. |
| `context` | `str \| null` | Contexto ensamblado completo cuando `include_context=true`. |

`RetrievedChunk`: `id: str`, `text: str`, `score: float`, `path: str`,
`start_line: int`, `end_line: int`, `kind: str = "code_chunk"`,
`metadata: dict[str, Any]`.

**Errores:** idénticos a `query_repo` (Sección 4.1) — mismas 3 causas
(`embedding_incompatible`, `repo_not_ready`, preflight de storage fallido),
mismo shape `REPO_VALIDATION` / `REPO_UNAVAILABLE`.

**Ejemplo `tools/call`:**

```json
{ "jsonrpc": "2.0", "id": 11, "method": "tools/call",
  "params": { "name": "query_retrieval", "arguments": {
    "repo_id": "gherrerz-kdb-rag-repo-main",
    "query": "donde esta la configuracion de neo4j",
    "include_context": false } } }
```

```json
{ "jsonrpc": "2.0", "id": 11, "result": { "content": [{ "type": "text",
  "text": "{\"mode\": \"retrieval_only\", \"answer\": \"...\", \"chunks\": [{\"id\": \"c1\", \"text\": \"NEO4J_URI = ...\", \"score\": 0.79, \"path\": \"src/coderag/core/settings.py\", \"start_line\": 40, \"end_line\": 55, \"kind\": \"code_chunk\", \"metadata\": {}}], \"citations\": [{\"path\": \"src/coderag/core/settings.py\", \"start_line\": 40, \"end_line\": 55, \"score\": 0.79, \"reason\": \"hybrid_rag_match\"}], \"statistics\": {\"total_before_rerank\": 60, \"total_after_rerank\": 20, \"graph_nodes_count\": 0}, \"diagnostics\": {}, \"context\": null}" }] } }
```

**Notas:** útil para clientes que quieren aplicar su propia síntesis/LLM
sobre la evidencia recuperada, sin depender del proveedor LLM configurado en
el servidor.

---

### 4.3 `list_repos`

**Parámetros:** ninguno.

**Respuesta** (`RepoCatalogResponse`):

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `repo_ids` | `list[str]` | Lista de `repo_id` disponibles para consulta. |
| `repositories` | `list[RepoCatalogEntry]` | Metadata por repositorio (ver abajo). |

`RepoCatalogEntry`: `repo_id: str`, `organization: str | null` (owner/grupo
derivado de la URL remota), `url: str | null` (URL usada en la última
ingesta), `branch: str | null` (rama usada en la última ingesta).

**Errores:** ninguno (`200` siempre; lista vacía si no hay repos ingeridos).

**Ejemplo `tools/call`:**

```json
{ "jsonrpc": "2.0", "id": 12, "method": "tools/call",
  "params": { "name": "list_repos", "arguments": {} } }
```

```json
{ "jsonrpc": "2.0", "id": 12, "result": { "content": [{ "type": "text",
  "text": "{\"repo_ids\": [\"gherrerz-kdb-rag-repo-main\"], \"repositories\": [{\"repo_id\": \"gherrerz-kdb-rag-repo-main\", \"organization\": \"gherrerz\", \"url\": \"https://github.com/gherrerz/kdb-rag-repo\", \"branch\": \"main\"}]}" }] } }
```

---

### 4.4 `repo_status`

**Parámetros:** `repo_id: str` (requerido, path param).

**Respuesta** (`RepoQueryStatusResponse`):

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `repo_id` | `str` | Repositorio evaluado. |
| `listed_in_catalog` | `bool` | Si aparece en `GET /repos`. |
| `workspace_available` | `bool` | Si conserva workspace local para operaciones live-file. |
| `query_ready` | `bool` | Si está listo para `/query`/`/query/retrieval`. |
| `chroma_counts` | `dict[str, int \| null]` | Conteos por colección (`code_symbols`, `code_files`, `code_modules`). |
| `chroma_hnsw_space_configured` | `str \| null` | Valor configurado de `CHROMA_HNSW_SPACE`. |
| `chroma_hnsw_space_detected` | `dict[str, str \| null]` | Espacio HNSW detectado por colección. |
| `chroma_hnsw_space_compatible` | `bool \| null` | Compatibilidad entre configurado y detectado. |
| `chroma_hnsw_space_mismatched_collections` | `list[str]` | Colecciones desalineadas. |
| `lexical_loaded` | `bool` | Readiness léxico del repo. |
| `graph_available` | `bool \| null` | Disponibilidad de grafo (si pudo evaluarse). |
| `last_embedding_provider` | `str \| null` | Proveedor de embedding de la última ingesta conocida. |
| `last_embedding_model` | `str \| null` | Modelo de embedding de la última ingesta conocida. |
| `embedding_compatible` | `bool \| null` | Compatibilidad entre embedding de consulta y de la última ingesta. |
| `compatibility_reason` | `str \| null` | Código breve del resultado de compatibilidad. |
| `warnings` | `list[str]` | Advertencias de readiness no bloqueantes. |

**Errores:** ninguno (`200` siempre; `query_ready=false` si el repo no
existe o no está indexado — no es un error HTTP, es información de estado).

**Ejemplo `tools/call`:**

```json
{ "jsonrpc": "2.0", "id": 13, "method": "tools/call",
  "params": { "name": "repo_status", "arguments": { "repo_id": "gherrerz-kdb-rag-repo-main" } } }
```

```json
{ "jsonrpc": "2.0", "id": 13, "result": { "content": [{ "type": "text",
  "text": "{\"repo_id\": \"gherrerz-kdb-rag-repo-main\", \"listed_in_catalog\": true, \"workspace_available\": true, \"query_ready\": true, \"chroma_counts\": {\"code_symbols\": 1200, \"code_files\": 340, \"code_modules\": 18}, \"chroma_hnsw_space_configured\": \"cosine\", \"chroma_hnsw_space_detected\": {\"code_symbols\": \"cosine\"}, \"chroma_hnsw_space_compatible\": true, \"chroma_hnsw_space_mismatched_collections\": [], \"lexical_loaded\": true, \"graph_available\": true, \"last_embedding_provider\": \"vertex\", \"last_embedding_model\": \"text-embedding-005\", \"embedding_compatible\": true, \"compatibility_reason\": \"match\", \"warnings\": []}" }] } }
```

**Notas:** invocar antes de `query_repo`/`query_retrieval` para repos no
verificados evita el `422` de `repo_not_ready`/`embedding_incompatible`.

---

### 4.5 `storage_health`

Idéntica a la Sección 3.1 (`GET /health`), publicada además como tool MCP
explícita para que un agente pueda invocarla dentro del flujo `tools/call`
sin depender del endpoint HTTP directo.

**Parámetros:** ninguno.

**Respuesta:** `StorageHealthResponse` (ver tabla completa en Sección 3.1).

**Errores:** ninguno (`200` siempre; el estado degradado/unhealthy se
refleja en `status`/`ok`/`failed_components`, no en el código HTTP).

**Ejemplo `tools/call`:**

```json
{ "jsonrpc": "2.0", "id": 14, "method": "tools/call",
  "params": { "name": "storage_health", "arguments": {} } }
```

```json
{ "jsonrpc": "2.0", "id": 14, "result": { "content": [{ "type": "text",
  "text": "{\"ok\": true, \"strict\": true, \"checked_at\": \"2026-01-01T00:00:00+00:00\", \"context\": \"health\", \"repo_id\": null, \"cached\": false, \"failed_components\": [], \"items\": [...], \"postgres_startup\": {...}, \"status\": \"healthy\", \"name\": \"repositories-kdb-mcp\", \"version\": \"0.1.0\", \"uptime_s\": 3600, \"dependencies\": {...}}" }] } }
```

## 5. Prompts MCP (3)

Registrados vía `register_mcp_prompts()` (`src/coderag/api/mcp_prompts.py`),
antes de montar `/mcp`. Se invocan con `prompts/get`.

| Prompt | Argumentos | Propósito |
| --- | --- | --- |
| `query_repo_guide` | `repo_id`, `pregunta` (ambos requeridos) | Cómo usar `query_repo` para Hybrid RAG con síntesis LLM sobre un repo concreto. |
| `query_retrieval_guide` | `repo_id`, `pregunta` (ambos requeridos) | Cómo usar `query_retrieval` para obtener evidencia sin síntesis LLM. |
| `hybrid_rag_workflow` | ninguno | Flujo end-to-end: `list_repos` → `repo_status` → `query_repo`/`query_retrieval`. |

### 5.1 `query_repo_guide`

Enseña: cómo invocar `query_repo` con `repo_id`+`query`, los parámetros
opcionales de tuning (`top_n`, `top_k`, `embedding_provider`/`embedding_model`,
`llm_provider`, `answer_model`, `verifier_model`), el shape de respuesta
(`answer`+`citations`+`diagnostics`), y recomienda invocar `repo_status`
primero si no se conoce el estado de readiness del repo.

**Ejemplo `prompts/get`:**

```json
{ "jsonrpc": "2.0", "id": 20, "method": "prompts/get",
  "params": { "name": "query_repo_guide",
    "arguments": { "repo_id": "gherrerz-kdb-rag-repo-main", "pregunta": "que controllers expone el modulo mall-admin?" } } }
```

```json
{ "jsonrpc": "2.0", "id": 20, "result": { "description": "Guía para responder 'que controllers expone el modulo mall-admin?' en el repo gherrerz-kdb-rag-repo-main con query_repo.",
  "messages": [{ "role": "user", "content": { "type": "text",
    "text": "Para responder la pregunta sobre gherrerz-kdb-rag-repo-main usa la tool `query_repo`... [contenido completo en src/coderag/api/mcp_prompts.py]" } }] } }
```

### 5.2 `query_retrieval_guide`

Análogo a `query_repo_guide` pero orientado a `query_retrieval`: cuándo
preferir evidencia cruda sin síntesis LLM (auditoría, verificación manual,
cuando el cliente aplicará su propio LLM), uso de `include_context` y el
shape `RetrievalQueryResponse` (`chunks`+`citations`+`statistics`).

### 5.3 `hybrid_rag_workflow`

Flujo en 3 pasos sin argumentos: (1) `list_repos` para descubrir
`repo_id`s disponibles; (2) `repo_status(repo_id)` para verificar
`query_ready`/`embedding_compatible` antes de consultar; (3)
`query_repo`/`query_retrieval` según se necesite síntesis LLM o evidencia
cruda. Refuerza no inventar módulos/relaciones ausentes del repositorio
ingerido.

## 6. Resources MCP (7)

Registrados vía `register_mcp_resources()`
(`src/coderag/api/mcp_resources.py`), antes de montar `/mcp`. Se listan con
`resources/list` (+ `resources/templates/list` para el template) y se leen
con `resources/read`.

| URI / template | Tipo | mimeType | Contenido |
| --- | --- | --- | --- |
| `rag://guide/tools-overview` | estático | `text/markdown` | Las 5 tools y cuándo usar cada una. |
| `rag://guide/query-cookbook` | estático | `text/markdown` | Recetas de fraseo para consultas de código, uso de `top_n`/`top_k`, cuándo usar retrieval-only. |
| `rag://guide/parameters` | estático | `text/markdown` | Parámetros de `QueryRequest`/`RetrievalQueryRequest` con defaults (`embedding_provider="vertex"`, etc.). |
| `rag://guide/capabilities` | estático | `text/markdown` | Capacidades reales: ingesta incremental por diff de commits, grafo siempre completo, proveedores LLM soportados (**sin Anthropic**). |
| `rag://guide/errors` | estático | `text/markdown` | Tabla de errores 422/503 y cómo recuperarse (`repo_not_ready`, `embedding_incompatible`). |
| `rag://repos` | dinámico | `application/json` | Resultado en vivo de `GET /repos` (mismo shape que `list_repos`). |
| `rag://repos/{repo_id}/status` | template dinámico | `application/json` | Resultado en vivo de `GET /repos/{repo_id}/status` (mismo shape que `repo_status`). |

Los resources dinámicos reutilizan el cliente HTTP ASGI interno de
`FastApiMCP` (mismas rutas REST, mismo proceso); si la llamada interna falla,
retornan un JSON de error legible (`{"error": "...", ...}`) en vez de
propagar una excepción de protocolo — siempre con HTTP `200` a nivel de
transporte MCP.

**Ejemplo `resources/read` (dinámico):**

```json
{ "jsonrpc": "2.0", "id": 21, "method": "resources/read",
  "params": { "uri": "rag://repos/gherrerz-kdb-rag-repo-main/status" } }
```

```json
{ "jsonrpc": "2.0", "id": 21, "result": { "contents": [{
  "uri": "rag://repos/gherrerz-kdb-rag-repo-main/status", "mimeType": "application/json",
  "text": "{\"repo_id\": \"gherrerz-kdb-rag-repo-main\", \"query_ready\": true, \"embedding_compatible\": true, ...}" }] } }
```

**Ejemplo `resources/read` (estático):**

```json
{ "jsonrpc": "2.0", "id": 22, "method": "resources/read",
  "params": { "uri": "rag://guide/errors" } }
```

```json
{ "jsonrpc": "2.0", "id": 22, "result": { "contents": [{
  "uri": "rag://guide/errors", "mimeType": "text/markdown",
  "text": "# Contrato de errores\n\n| Código | HTTP | ... |\n..." }] } }
```

## 7. Códigos de error consolidados

### 7.1 Errores de autenticación (endpoint `/mcp` completo, no por tool)

| Código | HTTP | Body | Causa |
| --- | --- | --- | --- |
| `mcp_disabled` | `404` | `{"message": "El servidor MCP está deshabilitado.", "code": "mcp_disabled"}` | `MCP_ENABLED=false`. |
| `invalid_mcp_token` | `401` | `{"message": "Token inválido para el endpoint MCP.", "code": "invalid_mcp_token"}` | `MCP_API_TOKEN` configurado y el Bearer recibido no coincide (o falta). |

### 7.2 Errores de tools (`REPO_*`)

Shape estándar: `{"error": "REPO_{CODE}", "retryable": <bool>, "message": "<descripción>", ...campos adicionales}`.

| Código | HTTP | Retryable | Campos adicionales | Causa | Tools donde aplica |
| --- | --- | --- | --- | --- | --- |
| `REPO_VALIDATION` (`code=embedding_incompatible`) | `422` | `false` | `code`, `repo_status: RepoQueryStatusResponse` | El embedding de la query no coincide con el de la última ingesta. | `query_repo`, `query_retrieval` |
| `REPO_VALIDATION` (`code=repo_not_ready`) | `422` | `false` | `code`, `repo_status: RepoQueryStatusResponse` | El repositorio no está listo (`query_ready=false`). | `query_repo`, `query_retrieval` |
| `REPO_UNAVAILABLE` | `503` | `true` | `health: StorageHealthResponse` | Preflight de storage falló antes de la consulta. | `query_repo`, `query_retrieval` |

> `list_repos`, `repo_status` y `storage_health` no producen errores de tool
> (siempre `200`); su información de estado se expresa en los campos de la
> respuesta, no en excepciones.
>
> Nota de transporte MCP: `fastapi-mcp==0.4.0` no implementa `isError`
> estricto por tool; el `status_code` HTTP original se conserva y el body de
> error se serializa como texto JSON dentro de `content`/`result`. Un cliente
> integrador debe parsear el texto y verificar el campo `error` para detectar
> fallos, no solo el código de transporte.

## 8. Ejemplo de sesión MCP completa

Secuencia mínima para un cliente nuevo (ver script ejecutable equivalente en
[scripts/mcp_smoke.sh](../scripts/mcp_smoke.sh)):

1. `POST /mcp` `initialize` → responde con `mcp-session-id` en headers y
   `capabilities: {tools, prompts, resources}`.
2. `POST /mcp` `notifications/initialized` (con header `mcp-session-id`).
3. `POST /mcp` `tools/list` → devuelve las 5 tools con sus JSON Schemas de
   entrada derivados del OpenAPI.
4. `POST /mcp` `tools/call` con `name` + `arguments` de la tool elegida (ver
   Sección 4 para el shape exacto de cada una). Flujo recomendado:
   `list_repos` → `repo_status` → `query_repo`/`query_retrieval`.

```bash
./scripts/mcp_smoke.sh http://127.0.0.1:8000 "$MCP_API_TOKEN"
```

El script ejecuta el handshake (`initialize` → `notifications/initialized` →
`tools/list` → `tools/call storage_health`); usar `curl http://127.0.0.1:8000/health`
y `curl http://127.0.0.1:8000/info` por separado para verificar los endpoints
públicos no-MCP de la Sección 3.

## 9. Configuración relevante

| Env var | Default | Descripción |
| --- | --- | --- |
| `MCP_ENABLED` | `true` | Habilita el montaje de `/mcp`. |
| `MCP_API_TOKEN` | `""` (vacío) | Token Bearer (`Authorization: Bearer {MCP_API_TOKEN}`); si vacío, sin protección adicional. |
| `MCP_MOUNT_PATH` | `/mcp` | Ruta de montaje del servidor MCP. |
| `MCP_SERVER_NAME` | `repositories-kdb-mcp` (código) / `coderag-mcp` (`.env.example`) | Nombre publicado en `/health` e `/info`. |
| `MCP_SERVER_DESCRIPTION` | Descripción genérica del servicio | Publicada sin autenticación en `/info`. |

Detalle completo de variables en [CONFIGURATION.md](CONFIGURATION.md).

## 10. Versionado

- `fastapi-mcp==0.4.0`, `mcp==1.28.1` (`requirements-runtime.txt`).
- Cambios rompientes de este contrato (nuevas tools, cambios de shape,
  cambios de auth) se documentan en [CHANGELOG.md](../CHANGELOG.md) bajo
  `[Unreleased]` con la marca **BREAKING** cuando corresponda.
