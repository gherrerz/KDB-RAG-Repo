# API Reference

Fuente de verdad de la API HTTP expuesta por el servicio.

- Implementación: `src/coderag/api/server.py`
- Modelos: `src/coderag/core/models.py`
- Servicios de consulta: `src/coderag/api/query_service.py`

## Base URL y OpenAPI

- Base URL local: `http://127.0.0.1:8000`
- OpenAPI JSON: `GET /openapi.json`
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`

## Endpoints (rutas exactas)

### Ingest

#### POST /repos/ingest

Crea un job asíncrono de ingesta de repositorio.

- Request schema: `RepoIngestRequest`
- Response schema: `JobInfo`
- Error responses:
  - `409`: ya existe ingesta activa para el mismo repositorio (`detail` es objeto)
  - `503`: error al iniciar encolado asíncrono (`detail` es objeto)
  - `503`: preflight de storage falló antes de ingest (`detail` es objeto)

#### GET /jobs/{job_id}

Consulta estado de job y logs.

- Path params:
  - `job_id: str`
- Query params:
  - `logs_tail: int = 200` (min `0`, max `2000`)
- Response schema: `JobInfo`
- Error responses:
  - `404`: `{"detail": "Job no encontrado"}`

### Query

#### POST /query

Ejecuta retrieval híbrido y síntesis de respuesta.

- Request schema: `QueryRequest`
- Response schema: `QueryResponse`
- Error responses:
  - `422`: `repo_not_ready` o `embedding_incompatible` (`detail` es objeto)
  - `503`: preflight de storage falló antes de query (`detail` es objeto)

#### POST /query/retrieval

Ejecuta modo retrieval-only (sin síntesis LLM).

- Request schema: `RetrievalQueryRequest`
- Response schema: `RetrievalQueryResponse`
- Error responses:
  - `422`: `repo_not_ready` o `embedding_incompatible` (`detail` es objeto)
  - `503`: preflight de storage falló antes de retrieval (`detail` es objeto)

#### POST /inventory/query

Ejecuta consulta de inventario paginada.

- Request schema: `InventoryQueryRequest`
- Response schema: `InventoryQueryResponse`
- Error responses:
  - `503`: preflight de storage falló antes de inventory query (`detail` es objeto)

Notas de comportamiento:

- Para objetivos de inventario como `dependency`, `dependencies`,
  `dependencia` o `dependencias`, el endpoint consulta aristas de archivo
  persistidas en Neo4j.
- En ese modo, `items[].kind` puede ser `file_dependency` para dependencias
  internas entre archivos del repositorio o `external_dependency` para
  imports externos asociados al archivo fuente citado.

### Catalog

#### GET /repos

Lista los `repo_id` disponibles para consultar y, cuando existe metadata de
ingesta persistida, retorna además URL, rama y organización persistida.

- Formato actual de `repo_id`: `organizacion-repo-rama`
- `organization` se persiste en el backend de metadata operativa,
  normalmente Postgres, y deja de derivarse al vuelo en el endpoint.

- Response schema: `RepoCatalogResponse`

#### GET /repos/{repo_id}/status

Retorna estado de readiness de consulta para un repositorio.

Notas de comportamiento:

- `query_ready=true` ya no exige workspace local si Chroma, Postgres y Neo4j
  estan disponibles.
- `workspace_available=false` no bloquea query semántico, retrieval-only ni
  inventory query, pero sí implica que modo literal quedará rechazado.

Notas de catálogo:

- `GET /repos` y `GET /repos/{repo_id}/status` se apoyan en metadata
  operativa persistida, normalmente en Postgres, por lo que el repo puede
  seguir visible aunque el clone local haya sido eliminado post-ingesta.

- Path params:
  - `repo_id: str`
- Query params:
  - `requested_embedding_provider: str | null`
  - `requested_embedding_model: str | null`
- Response schema: `RepoQueryStatusResponse`

#### GET /providers/models

Retorna catálogo de modelos por provider y kind.

- Query params:
  - `provider: str` (required)
  - `kind: str` (required; valores usados: `embedding`, `llm`)
  - `force_refresh: bool = false`
- Response schema: `ProviderModelCatalogResponse`

### Admin

#### GET /health

Retorna reporte consolidado de salud de storage.

- Response schema: `StorageHealthResponse`

#### DELETE /repos/{repo_id}

Elimina datos del repositorio en todas las capas de storage.

- Path params:
  - `repo_id: str`
- Response schema: `RepoDeleteResponse`
- Error responses:
  - `404`: repo no encontrado (`detail` es string)
  - `409`: repo con jobs en ejecución (`detail` es string)
  - `422`: `repo_id` vacío/inválido (`detail` es string)
  - `500`: error inesperado en delete (`detail` es string)

#### POST /admin/reset

Limpia todo el estado indexado.

- Response schema: `ResetResponse`
- Error responses:
  - `409`: reset bloqueado por jobs en ejecución (`detail` es string)
  - `500`: error inesperado en reset (`detail` es string)

## Mapping interno

| Method | Path | Internal service | Request model | Response model |
| --- | --- | --- | --- | --- |
| POST | `/repos/ingest` | `JobManager.create_ingest_job` | `RepoIngestRequest` | `JobInfo` |
| GET | `/jobs/{job_id}` | `JobManager.get_job` | Path/query params | `JobInfo` |
| POST | `/query` | `run_query` | `QueryRequest` | `QueryResponse` |
| POST | `/query/retrieval` | `run_retrieval_query` | `RetrievalQueryRequest` | `RetrievalQueryResponse` |
| POST | `/inventory/query` | `run_inventory_query` | `InventoryQueryRequest` | `InventoryQueryResponse` |
| GET | `/repos` | `JobManager.list_repo_catalog` | N/A | `RepoCatalogResponse` |
| GET | `/repos/{repo_id}/status` | `get_repo_query_status` | Path/query params | `RepoQueryStatusResponse` |
| GET | `/providers/models` | `discover_models` | Query params | `ProviderModelCatalogResponse` |
| GET | `/health` | `run_storage_preflight` | N/A | `StorageHealthResponse` |
| DELETE | `/repos/{repo_id}` | `JobManager.delete_repo` | Path params | `RepoDeleteResponse` |
| POST | `/admin/reset` | `JobManager.reset_all_data` | N/A | `ResetResponse` |

## Schemas

Notas operativas de storage:

- Arquitectura operativa principal: Chroma remoto + Postgres + Neo4j.
- SQLite y BM25 local pueden seguir apareciendo como compatibilidad legacy en
  algunas rutas cuando Postgres no esta configurado, pero no son el
  backend principal documentado aqui.

### Enum: JobStatus

- `queued`
- `running`
- `partial`
- `completed`
- `failed`

### RepoIngestRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `provider` | `str` | no | `"github"` |
| `repo_url` | `str` | sí | - |
| `branch` | `str` | no | `"main"` |
| `commit` | `str \| null` | no | `null` |
| `token` | `str \| null` | no | `null` |
| `auth` | `object \| null` | no | `null` |
| `embedding_provider` | `str \| null` | no | `null` |
| `embedding_model` | `str \| null` | no | `null` |

Notas para repos privados:

- `provider=github`: mantiene compatibilidad con URL HTTPS (`https://github.com/...`) y `token`, o puede usar `auth` explícito.
- `provider=bitbucket`: soporta SSH (`git@...` o `ssh://...`) con configuración SSH del entorno, y también HTTPS con `auth.method=http_basic`.

Contrato del bloque `auth`:

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `deployment` | `"auto" \| "cloud" \| "server" \| "data_center"` | no | `"auto"` |
| `transport` | `"auto" \| "https" \| "ssh"` | no | `"auto"` |
| `method` | `"auto" \| "ssh_key" \| "http_basic" \| "http_token"` | no | `"auto"` |
| `username` | `str \| null` | no | `null` |
| `secret` | `str \| null` | no | `null` |

Reglas iniciales:

- `token` se conserva como compatibilidad legacy y se mapea internamente a GitHub HTTPS.
- Bitbucket HTTPS en esta primera implementación requiere `auth.method=http_basic`, `auth.transport=https`, `auth.username` y `auth.secret`.
- Bitbucket SSH mantiene las variables `GIT_SSH_*` existentes.
- Si no necesitas fijar una revisión exacta, omite `commit` o envíalo como `null`; no uses placeholders como `"string"` desde Swagger/OpenAPI.

### JobInfo

| Field | Type | Requerido | Descripción |
| --- | --- | --- | --- |
| `id` | `str` | sí | ID del job |
| `status` | `JobStatus` | sí | Estado de ciclo de vida |
| `progress` | `float` | sí | Rango `[0.0, 1.0]` |
| `logs` | `list[str]` | sí | Líneas de log |
| `repo_id` | `str \| null` | sí | Se completa cuando aplica |
| `error` | `str \| null` | sí | Error si falla |
| `diagnostics` | `dict[str, Any]` | sí | Diagnósticos estructurados de ingesta |
| `created_at` | `datetime` | sí | Timestamp UTC |
| `updated_at` | `datetime` | sí | Timestamp UTC |

Notas de `diagnostics` en jobs de ingesta con grafo semántico habilitado:

- `semantic_graph.enabled`: `true|false`
- `semantic_graph.status`: `ok|fallback|disabled`
- `semantic_graph.relation_counts`: total de relaciones semánticas extraídas
- `semantic_graph.relation_counts_by_type`: conteos por tipo (`CALLS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`)
- `semantic_graph.java_cross_file_resolved_count`: relaciones Java resueltas hacia símbolos en archivos distintos
- `semantic_graph.java_cross_file_resolved_by_type`: desglose por tipo de relación Java resuelta cross-file
- `semantic_graph.java_resolution_source_counts`: desglose por origen de resolución Java (`local`, `import`, `import_wildcard`, `static_import_member`, `static_import_wildcard`, `same_package`, `global_unique`, `fqcn`)
- `semantic_graph.typescript_resolution_source_counts`: desglose por origen de resolución TypeScript (ej. `local`, `global_unique`, `unresolved`)
- `semantic_graph.unresolved_count`: cantidad de relaciones con target no resuelto
- `semantic_graph.unresolved_by_type`: desglose de no resueltos por tipo de relación
- `semantic_graph.unresolved_ratio`: proporción de targets no resueltos
- `semantic_graph.semantic_extraction_ms`: latencia de extracción semántica

Notas de `diagnostics` en respuestas de query/retrieval con expansión semántica habilitada:

- `semantic_query_enabled`: indica si se activó ruta de expansión semántica en query
- `semantic_relation_types`: tipos de relación usados para expansión (`SEMANTIC_RELATION_TYPES`)
- `semantic_edges_used`: aristas efectivamente usadas por expansión
- `semantic_nodes_used`: nodos de grafo efectivamente incorporados
- `semantic_file_context_used`: nodos adicionales incorporados desde `IMPORTS_FILE` e `IMPORTS_EXTERNAL_FILE`
- `semantic_file_context_pruned`: nodos de file-context descartados por budget
- `reverse_import_seed_boosted_count`: chunks rerankeados por lookup inverso de `IMPORTS_FILE`
- `reverse_import_seed_chunks_added_count`: seeds sintéticos agregados para importadores internos ausentes del retrieval inicial
- `reverse_import_target_paths`: archivos objetivo resueltos cuando la query pregunta qué archivos importan un path concreto
- `semantic_graph_chunk_boosted_count`: chunks rerankeados por señal de aristas de archivo
- `semantic_graph_citations_count`: citas derivadas agregadas desde aristas de archivo
- `semantic_expand_ms`: latencia de expansión semántica
- `semantic_pruned_edges`: aristas podadas por budgets de query
- `semantic_noise_ratio`: proporción de aristas podadas sobre total evaluado
- `semantic_fallback_used`: indica si se activó degradación a expansión estructural
- `semantic_fallback_reason`: causa de fallback (`semantic_budget_pruned_all`, `semantic_exception`)

### QueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `query` | `str` | sí | - |
| `top_n` | `int` | no | `60` |
| `top_k` | `int` | no | `15` |
| `embedding_provider` | `str \| null` | no | `null` |
| `embedding_model` | `str \| null` | no | `null` |
| `llm_provider` | `str \| null` | no | `null` |
| `answer_model` | `str \| null` | no | `null` |
| `verifier_model` | `str \| null` | no | `null` |

### RetrievalQueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `query` | `str` | sí | - |
| `top_n` | `int` | no | `60` |
| `top_k` | `int` | no | `15` |
| `embedding_provider` | `str \| null` | no | `null` |
| `embedding_model` | `str \| null` | no | `null` |
| `include_context` | `bool` | no | `false` |

### InventoryQueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `query` | `str` | sí | - |
| `page` | `int` | no | `1` |
| `page_size` | `int` | no | `80` |

### Citation

| Field | Type | Requerido |
| --- | --- | --- |
| `path` | `str` | sí |
| `start_line` | `int` | sí |
| `end_line` | `int` | sí |
| `score` | `float` | sí |
| `reason` | `str` | sí |

Valores frecuentes de `reason`:

- `hybrid_rag_match`: evidencia proveniente del retrieval híbrido principal.
- `inventory_graph_match`: evidencia generada por el flujo de inventario.
- `graph_file_dependency_match`: evidencia derivada de una arista `File -> File` relevante para la query.
- `graph_external_dependency_source`: evidencia derivada de un import externo, citando el archivo fuente donde aparece.

### QueryResponse

| Field | Type | Requerido |
| --- | --- | --- |
| `answer` | `str` | sí |
| `citations` | `list[Citation]` | sí |
| `diagnostics` | `dict[str, Any]` | sí |

### RetrievedChunk

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `id` | `str` | sí | - |
| `text` | `str` | sí | - |
| `score` | `float` | sí | - |
| `path` | `str` | sí | - |
| `start_line` | `int` | sí | - |
| `end_line` | `int` | sí | - |
| `kind` | `str` | no | `"code_chunk"` |
| `metadata` | `dict[str, Any]` | no | `{}` |

### RetrievalStatistics

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `total_before_rerank` | `int` | no | `0` |
| `total_after_rerank` | `int` | no | `0` |
| `graph_nodes_count` | `int` | no | `0` |

### RetrievalQueryResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `mode` | `str` | no | `"retrieval_only"` |
| `answer` | `str` | sí | - |
| `chunks` | `list[RetrievedChunk]` | no | `[]` |
| `citations` | `list[Citation]` | no | `[]` |
| `statistics` | `RetrievalStatistics` | no | `{}` |
| `diagnostics` | `dict[str, Any]` | no | `{}` |
| `context` | `str \| null` | no | `null` |

### InventoryItem

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `label` | `str` | sí | - |
| `path` | `str` | sí | - |
| `kind` | `str` | no | `"file"` |
| `start_line` | `int` | no | `1` |
| `end_line` | `int` | no | `1` |

### InventoryQueryResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `answer` | `str` | sí | - |
| `target` | `str \| null` | no | `null` |
| `module_name` | `str \| null` | no | `null` |
| `total` | `int` | no | `0` |
| `page` | `int` | no | `1` |
| `page_size` | `int` | no | `80` |
| `items` | `list[InventoryItem]` | no | `[]` |
| `citations` | `list[Citation]` | no | `[]` |
| `diagnostics` | `dict[str, Any]` | no | `{}` |

Notas operativas de inventory/discovery:

- La explicación textual usa `purpose_summary` persistido en Neo4j cuando está disponible.
- El inventario de dependencias usa `IMPORTS_FILE` e `IMPORTS_EXTERNAL_FILE`
  cuando esas aristas existen en el grafo del repositorio.
- Consultas como `who imports metadata_store.py`, `who uses metadata_store.py`,
  `where is X used`, `which files import X directly` o `qué archivos importan`
  y `en qué archivos se usa X` pueden resolverse por una ruta graph-first
  específica que busca importadores directos del archivo objetivo vía
  `IMPORTS_FILE`, sin depender del retrieval híbrido.
- Para frontend React/Next, la ingesta ahora reconoce heurísticas de archivo como
  `page.tsx`, `layout.tsx`, `loading.tsx`, `route.ts` y `middleware.ts`, además de
  hooks `use*` y providers `*Provider`, para describir mejor el propósito del archivo
  sin depender del workspace local.

### RepoCatalogResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_ids` | `list[str]` | no | `[]` |
| `repositories` | `list[RepoCatalogEntry]` | no | `[]` |

### ProviderModelCatalogResponse

| Field | Type | Requerido | Descripción |
| --- | --- | --- | --- |
| `provider` | `str` | sí | Provider solicitado |
| `kind` | `str` | sí | `embedding` o `llm` |
| `models` | `list[str]` | no | Modelos disponibles o fallback |
| `source` | `str` | sí | `remote`, `cache` o `fallback` |
| `warning` | `str \| null` | no | Warning opcional de fallback |

### RepoQueryStatusResponse

Notas de interpretación:

- `query_ready` refleja readiness para query semántico y retrieval; no garantiza
  disponibilidad de modo literal.
- Si el cliente necesita devolver código exacto desde archivos vivos, debe
  verificar además que exista workspace local para el repositorio.

| Field | Type | Requerido |
| --- | --- | --- |
| `repo_id` | `str` | sí |
| `listed_in_catalog` | `bool` | sí |
| `workspace_available` | `bool` | sí |
| `query_ready` | `bool` | sí |
| `chroma_counts` | `dict[str, int \| null]` | no |
| `chroma_hnsw_space_configured` | `str \| null` | no |
| `chroma_hnsw_space_detected` | `dict[str, str \| null]` | no |
| `chroma_hnsw_space_compatible` | `bool \| null` | no |
| `chroma_hnsw_space_mismatched_collections` | `list[str]` | no |
| `bm25_loaded` | `bool` | sí |
| `graph_available` | `bool \| null` | no |
| `last_embedding_provider` | `str \| null` | no |
| `last_embedding_model` | `str \| null` | no |
| `embedding_compatible` | `bool \| null` | no |
| `compatibility_reason` | `str \| null` | no |
| `warnings` | `list[str]` | no |

Nota: `bm25_loaded` conserva un nombre historico por compatibilidad, pero en
despliegues con Postgres activo actua como señal de readiness de la capa
lexica en Postgres.

### StorageHealthItem

| Field | Type | Requerido |
| --- | --- | --- |
| `name` | `str` | sí |
| `ok` | `bool` | sí |
| `critical` | `bool` | sí |
| `code` | `str` | sí |
| `message` | `str` | sí |
| `latency_ms` | `float` | sí |
| `details` | `dict[str, Any]` | no |

### StorageHealthResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `ok` | `bool` | sí | - |
| `strict` | `bool` | sí | - |
| `checked_at` | `str` | sí | - |
| `context` | `str` | sí | - |
| `repo_id` | `str \| null` | no | `null` |
| `cached` | `bool` | no | `false` |
| `failed_components` | `list[str]` | no | `[]` |
| `items` | `list[StorageHealthItem]` | no | `[]` |

### ResetResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `message` | `str` | sí | - |
| `cleared` | `list[str]` | no | `[]` |
| `warnings` | `list[str]` | no | `[]` |

### RepoDeleteResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `message` | `str` | sí | - |
| `repo_id` | `str` | sí | - |
| `cleared` | `list[str]` | no | `[]` |
| `deleted_counts` | `dict[str, int]` | no | `{}` |
| `warnings` | `list[str]` | no | `[]` |

## Ejemplos JSON mínimos

### Ejemplo POST /repos/ingest

Request:

```json
{
  "provider": "github",
  "repo_url": "https://github.com/macrozheng/mall.git",
  "branch": "main"
}
```

Request (Bitbucket privado):

```json
{
  "provider": "bitbucket",
  "repo_url": "git@bitbucket.example:team/proyecto.git",
  "branch": "master"
}
```

Response:

```json
{
  "id": "job-123",
  "status": "queued",
  "progress": 0.0,
  "logs": [],
  "repo_id": "macrozheng-mall-main",
  "error": null,
  "diagnostics": {},
  "created_at": "2026-03-23T12:00:00Z",
  "updated_at": "2026-03-23T12:00:00Z"
}
```

### Ejemplo POST /query/retrieval

Request:

```json
{
  "repo_id": "macrozheng-mall-main",
  "query": "where is neo4j configuration",
  "top_n": 60,
  "top_k": 15,
  "include_context": false
}
```

Response:

```json
{
  "mode": "retrieval_only",
  "answer": "Evidence found in configuration modules.",
  "chunks": [
    {
      "id": "chunk-1",
      "text": "NEO4J_URI=bolt://localhost:7687",
      "score": 0.88,
      "path": "src/coderag/core/settings.py",
      "start_line": 10,
      "end_line": 40,
      "kind": "code_chunk",
      "metadata": {}
    }
  ],
  "citations": [
    {
      "path": "src/coderag/core/settings.py",
      "start_line": 10,
      "end_line": 40,
      "score": 0.88,
      "reason": "hybrid_rag_match"
    }
  ],
  "statistics": {
    "total_before_rerank": 60,
    "total_after_rerank": 15,
    "graph_nodes_count": 8
  },
  "diagnostics": {},
  "context": null
}
```

## Formas de error comunes

### Error con `detail` objeto (422/503 en queries)

```json
{
  "detail": {
    "message": "...",
    "code": "repo_not_ready",
    "repo_status": {}
  }
}
```

### Error con `detail` string (404/409/422/500 en admin/jobs)

```json
{
  "detail": "Job no encontrado"
}
```

## Ejemplos ejecutables

- `examples/python/ingest_and_poll.py`
- `examples/python/query_with_llm.py`
- `examples/python/query_retrieval_only.py`
- `examples/curl/`
- `examples/powershell/`
