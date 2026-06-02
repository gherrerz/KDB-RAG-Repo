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
- Las consultas válidas a `POST /query`, `POST /query/retrieval` y
  `POST /inventory/query` actualizan `last_queried_at` del repositorio en
  metadata runtime después del preflight de storage y antes de delegar al
  servicio de consulta.

### Catalog

#### GET /repos

Lista los `repo_id` disponibles para consultar y, cuando existe metadata de
ingesta persistida, retorna además URL, rama y organización persistida.

- Formato actual de `repo_id`: `organizacion-repo-rama`
- `organization` se persiste en el backend de metadata operativa,
  normalmente Postgres, y deja de derivarse al vuelo en el endpoint.

- Response schema: `RepoCatalogResponse`

#### GET /repos/last-query/stale

Lista repositorios cuya última consulta es menor o igual a una fecha de corte,
incluyendo repositorios nunca consultados (`last_queried_at = null`).

- Query params:
  - `last_queried_on_or_before: datetime` (required, ISO-8601)
- Response schema: `RepoLastQueryStaleResponse`

#### GET /repos/{repo_id}/status

Retorna estado de readiness de consulta para un repositorio.

Notas de comportamiento:

- `query_ready=true` ya no exige workspace local si Chroma, Postgres y Neo4j
  estan disponibles.
- `workspace_available=false` no bloquea query semántico, retrieval-only ni
  inventory query, pero sí implica que modo literal quedará rechazado.
- El payload expone `lexical_loaded` como único indicador público de readiness
  léxico.

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

Incluye además el bloque top-level `postgres_startup` cuando el runtime tiene
Postgres operativo habilitado, para exponer la política y el resultado del
bootstrap de migraciones Alembic.

- Response schema: `StorageHealthResponse`

#### GET /admin/chroma/diagnostics

Retorna un resumen diagnóstico de colecciones gestionadas de Chroma.

- Query params:
  - `repo_id: str | null`
  - `collection_names: list[str] | null`
  - `page_size: int = 500` (min `1`, max `5000`)
- Header opcional cuando hay token configurado:
  - `X-Chroma-Admin-Token: str`
- Response schema: `ChromaDiagnosticsResponse`
- Error responses:
  - `422`: se solicitaron colecciones no gestionadas (`detail` es objeto)
  - `503`: no se pudo construir el diagnóstico (`detail` es objeto)

Notas de comportamiento:

- Siempre retorna conteo total por colección cuando la lectura es posible.
- Si se envía `repo_id`, agrega además `repo_count` por colección.
- Si algunas colecciones fallan y otras responden, devuelve `200` con
  `partial=true` y llena `warnings[]`.

#### POST /admin/chroma/query

Ejecuta una operación directa de solo lectura sobre Chroma, acotada a un
allowlist de operaciones permitidas.

- Request schema: `ChromaQueryRequest`
- Header opcional cuando hay token configurado:
  - `X-Chroma-Admin-Token: str`
- Response schema: `ChromaQueryResponse`
- Error responses:
  - `422`: payload inválido o colección no gestionada (`detail` es objeto)
  - `503`: fallo durante la operación remota/local en Chroma (`detail` es objeto)

Operaciones permitidas:

- `list_collections`
- `collection_count`
- `collection_metadata`
- `get`
- `peek`
- `query`

Notas de comportamiento:

- Es un endpoint de solo lectura; no expone writes, deletes ni creación de
  colecciones.
- El endpoint solo está disponible cuando `CHROMA_ADMIN_API_ENABLED=true`.
- `query` en esta versión usa `query_texts`; `query_embeddings` no está
  expuesto por el contrato HTTP.
- La colección debe pertenecer al set gestionado por la aplicación.
- `collection_count` acepta `where` para contar subconjuntos arbitrarios.

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

- Request schema: `AdminResetRequest`
- Header requerido:
  - `X-Admin-Reset-Token: str`
- Response schema: `ResetResponse`
- Error responses:
  - `403`: token administrativo faltante o inválido (`detail` es objeto)
  - `404`: endpoint administrativo deshabilitado (`detail` es objeto)
  - `409`: reset bloqueado por jobs en ejecución (`detail` es string)
  - `422`: payload de confirmación inválido (`detail` es objeto)
  - `500`: error inesperado en reset (`detail` es string)

Notas de comportamiento:

- El endpoint solo está disponible cuando `ADMIN_RESET_ENABLED=true`.
- La configuración es inválida si `ADMIN_RESET_ENABLED=true` y
  `ADMIN_RESET_TOKEN` está vacío.
- El body debe incluir `confirm=true` y
  `confirmation_phrase="RESET ALL DATA"`.

## Mapping interno

| Method | Path | Internal service | Request model | Response model |
| --- | --- | --- | --- | --- |
| POST | `/repos/ingest` | `JobManager.create_ingest_job` | `RepoIngestRequest` | `JobInfo` |
| GET | `/jobs/{job_id}` | `JobManager.get_job` | Path/query params | `JobInfo` |
| POST | `/query` | `run_query` | `QueryRequest` | `QueryResponse` |
| POST | `/query/retrieval` | `run_retrieval_query` | `RetrievalQueryRequest` | `RetrievalQueryResponse` |
| POST | `/inventory/query` | `run_inventory_query` | `InventoryQueryRequest` | `InventoryQueryResponse` |
| GET | `/repos` | `JobManager.list_repo_catalog` | N/A | `RepoCatalogResponse` |
| GET | `/repos/last-query/stale` | `JobManager.list_stale_repos` | Query params | `RepoLastQueryStaleResponse` |
| GET | `/repos/{repo_id}/status` | `get_repo_query_status` | Path/query params | `RepoQueryStatusResponse` |
| GET | `/providers/models` | `discover_models` | Query params | `ProviderModelCatalogResponse` |
| GET | `/health` | `run_storage_preflight` | N/A | `StorageHealthResponse` |
| GET | `/admin/chroma/diagnostics` | `build_managed_vector_index` + Chroma diagnostics | Query params | `ChromaDiagnosticsResponse` |
| POST | `/admin/chroma/query` | `build_managed_vector_index` + Chroma direct read | `ChromaQueryRequest` | `ChromaQueryResponse` |
| DELETE | `/repos/{repo_id}` | `JobManager.delete_repo` | Path params | `RepoDeleteResponse` |
| POST | `/admin/reset` | `JobManager.reset_all_data` | `AdminResetRequest` | `ResetResponse` |

## Schemas

Notas operativas de storage:

- Arquitectura operativa principal: Chroma remoto + Postgres + Neo4j.
- SQLite y BM25 local ya no forman parte del runtime ni del tooling soportado;
  el contrato documentado aqui asume Postgres versionado y LexicalStore
  Postgres.

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
| `embedding_provider` | `str \| null` | no | `"vertex"` |
| `embedding_model` | `str \| null` | no | `"text-embedding-005"` |

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
- `semantic_graph.file_import_resolution_counts`: conteos canónicos de `FileImportRelation` por método de resolución normalizado
- `semantic_graph.file_import_resolution_counts_by_language`: desglose del método de resolución de file imports por lenguaje
- `semantic_graph.file_import_counts_by_language`: total de file imports internos/externos por lenguaje

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
- `inventory_route`: ruta de inventario o graph-first efectivamente usada (`graph_first`, `graph_direct_impact`, etc.)
- `graph_first_route`: nombre estable de la ruta graph-first usada cuando aplica
- `graph_first_response`: indica si la respuesta completa se resolvió por graph-first sin pasar por retrieval híbrido
- `fallback_used`: indica si la ruta graph-first/semántica terminó degradando a otra estrategia
- `impact_route_used`: nombre de la ruta usada para consultas de impacto por archivo cuando aplica
- `impact_lookup_used`: indica si la respuesta salió de lookup de impacto por archivo
- `impact_depth`: profundidad máxima explorada para impacto por archivo
- `impact_direct_match_count`: cantidad de dependientes directos encontrados para el archivo objetivo
- `impact_transitive_match_count`: cantidad de dependientes transitivos encontrados para el archivo objetivo
- `target_path_resolved`: archivo objetivo resuelto a partir de la query cuando la ruta graph-first necesita un path concreto
- `target_resolution_confidence`: confianza de la resolución de target (`high`, `medium`, `low`, `none`)

### QueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `query` | `str` | sí | - |
| `top_n` | `int` | no | `60` |
| `top_k` | `int` | no | `20` |
| `embedding_provider` | `str \| null` | no | `"vertex"` |
| `embedding_model` | `str \| null` | no | `"text-embedding-005"` |
| `llm_provider` | `str \| null` | no | `"vertex"` |
| `answer_model` | `str \| null` | no | `"gemini-2.5-flash"` |
| `verifier_model` | `str \| null` | no | `"gemini-2.5-flash"` |

### RetrievalQueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `query` | `str` | sí | - |
| `top_n` | `int` | no | `60` |
| `top_k` | `int` | no | `20` |
| `embedding_provider` | `str \| null` | no | `"vertex"` |
| `embedding_model` | `str \| null` | no | `"text-embedding-005"` |
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
- `graph_direct_impact_match`: evidencia de un dependiente directo del archivo objetivo.
- `graph_transitive_impact_match`: evidencia de un dependiente transitivo del archivo objetivo.

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
- Consultas como `what breaks if I change X`, `qué se afecta si modifico X` o
  `impacto de cambiar X` pueden resolverse por una ruta graph-first específica
  que recorre `IMPORTS_FILE` con profundidad fija 2 y separa dependientes
  directos de transitivos.
- La cobertura actual de file edges e impacto por archivo incluye Python,
  Java, JavaScript, TypeScript, Kotlin y Swift. Go queda fuera de este
  contrato semántico y sigue disponible solo en retrieval estructural.
- Para frontend React/Next, la ingesta ahora reconoce heurísticas de archivo como
  `page.tsx`, `layout.tsx`, `loading.tsx`, `route.ts` y `middleware.ts`, además de
  hooks `use*` y providers `*Provider`, para describir mejor el propósito del archivo
  sin depender del workspace local.

### RepoCatalogResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_ids` | `list[str]` | no | `[]` |
| `repositories` | `list[RepoCatalogEntry]` | no | `[]` |

### RepoCatalogEntry

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `organization` | `str \| null` | no | `null` |
| `url` | `str \| null` | no | `null` |
| `branch` | `str \| null` | no | `null` |

### RepoRuntimeEntry

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `repo_id` | `str` | sí | - |
| `organization` | `str \| null` | no | `null` |
| `url` | `str \| null` | no | `null` |
| `branch` | `str \| null` | no | `null` |
| `local_path` | `str \| null` | no | `null` |
| `created_at` | `datetime` | sí | - |
| `updated_at` | `datetime \| null` | no | `null` |
| `last_queried_at` | `datetime \| null` | no | `null` |

### RepoLastQueryStaleResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `last_queried_on_or_before` | `datetime` | sí | - |
| `repositories` | `list[RepoRuntimeEntry]` | no | `[]` |

### ProviderModelCatalogResponse

| Field | Type | Requerido | Descripción |
| --- | --- | --- | --- |
| `provider` | `str` | sí | Provider solicitado |
| `kind` | `str` | sí | `embedding` o `llm` |
| `models` | `list[str]` | no | Modelos disponibles o fallback |
| `source` | `str` | sí | `remote`, `cache` o `fallback` |
| `warning` | `str \| null` | no | Warning opcional de fallback |

### Enum: ChromaQueryOperation

- `list_collections`
- `collection_count`
- `collection_metadata`
- `get`
- `peek`
- `query`

### ChromaDiagnosticsCollectionResult

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `collection_name` | `str` | sí | - |
| `total_count` | `int \| null` | no | `null` |
| `repo_count` | `int \| null` | no | `null` |
| `metadata` | `dict[str, Any]` | no | `{}` |
| `error` | `str \| null` | no | `null` |

### ChromaDiagnosticsResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `chroma_mode` | `str` | sí | - |
| `repo_id` | `str \| null` | no | `null` |
| `collection_names` | `list[str]` | no | `[]` |
| `partial` | `bool` | no | `false` |
| `warnings` | `list[str]` | no | `[]` |
| `collections` | `list[ChromaDiagnosticsCollectionResult]` | no | `[]` |

### ChromaQueryRequest

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `operation` | `ChromaQueryOperation` | sí | - |
| `collection_name` | `str \| null` | no | `null` |
| `where` | `dict[str, Any] \| null` | no | `null` |
| `where_document` | `dict[str, Any] \| null` | no | `null` |
| `include` | `list[str] \| null` | no | `null` |
| `limit` | `int \| null` | no | `10` |
| `offset` | `int \| null` | no | `0` |
| `n_results` | `int \| null` | no | `10` |
| `query_texts` | `list[str] \| null` | no | `null` |

Notas:

- `query_texts` es obligatorio solo cuando `operation=query`.
- `collection_name` es obligatorio para todas las operaciones excepto
  `list_collections`.

### ChromaQueryResponse

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `operation` | `ChromaQueryOperation` | sí | - |
| `collection_name` | `str \| null` | no | `null` |
| `effective_params` | `dict[str, Any]` | no | `{}` |
| `result` | `Any` | sí | - |
| `warnings` | `list[str]` | no | `[]` |
| `elapsed_ms` | `float` | sí | - |

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
| `lexical_loaded` | `bool` | sí |
| `graph_available` | `bool \| null` | no |
| `last_embedding_provider` | `str \| null` | no |
| `last_embedding_model` | `str \| null` | no |
| `embedding_compatible` | `bool \| null` | no |
| `compatibility_reason` | `str \| null` | no |
| `warnings` | `list[str]` | no |

Notas:

- `lexical_loaded` es el campo canónico y refleja readiness de la capa léxica
  activa sobre LexicalStore en Postgres.

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
| `postgres_startup` | `PostgresStartupStatus \| null` | no | `null` |

### PostgresStartupStatus

| Field | Type | Requerido | Default |
| --- | --- | --- | --- |
| `enabled` | `bool` | sí | - |
| `policy` | `str` | sí | - |
| `action` | `str` | sí | - |
| `current_heads` | `list[str]` | no | `[]` |
| `expected_heads` | `list[str]` | no | `[]` |
| `cached` | `bool` | no | `false` |

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
