# API Reference (Espanol)

Este documento es la fuente de verdad de la API HTTP de Coderag.

- Implementacion base: `coderag/api/server.py`
- Modelos: `coderag/core/models.py`
- Servicios de consulta: `coderag/api/query_service.py`
- Pruebas de contrato: `tests/test_api.py`

## Base URL y OpenAPI

- Base URL local: `http://127.0.0.1:8000`
- OpenAPI JSON: `GET /openapi.json`
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`

## Endpoints

| Metodo | Ruta | Servicio interno | Request | Response |
|---|---|---|---|---|
| POST | `/repos/ingest` | `JobManager.create_ingest_job` | `RepoIngestRequest` | `JobInfo` |
| GET | `/jobs/{job_id}` | `JobManager.get_job` | Path `job_id` | `JobInfo` |
| POST | `/query` | `run_query` | `QueryRequest` | `QueryResponse` |
| POST | `/inventory/query` | `run_inventory_query` | `InventoryQueryRequest` | `InventoryQueryResponse` |
| GET | `/repos` | `JobManager.list_repo_ids` | N/A | `RepoCatalogResponse` |
| GET | `/repos/{repo_id}/status` | `get_repo_query_status` | Path `repo_id` | `RepoQueryStatusResponse` |
| GET | `/health/storage` | `run_storage_preflight` | N/A | `StorageHealthResponse` |
| POST | `/admin/reset` | `JobManager.reset_all_data` | N/A | `ResetResponse` |

## Schemas

## RepoIngestRequest

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| provider | str | no | `github` | Proveedor Git. |
| repo_url | str | si | N/A | URL del repositorio. |
| token | str \| null | no | `null` | Token para repos privados. |
| branch | str | no | `main` | Rama objetivo de ingesta. |
| commit | str \| null | no | `null` | Commit puntual opcional. |
| embedding_provider | str \| null | no | `null` | Provider de embeddings por operación (`openai`, `anthropic`, `gemini`, `vertex_ai`). |
| embedding_model | str \| null | no | `null` | Modelo de embeddings por operación. |

## JobInfo

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| id | str | si | N/A | Id del job. |
| status | enum | si | N/A | `queued`, `running`, `partial`, `completed`, `failed`. |
| progress | float | no | `0.0` | Progreso normalizado 0-1. |
| logs | list[str] | no | `[]` | Logs operativos del job. |
| repo_id | str \| null | no | `null` | Repo asociado al job. |
| error | str \| null | no | `null` | Error terminal si aplica. |
| created_at | datetime | no | `utcnow` | Timestamp de creacion. |
| updated_at | datetime | no | `utcnow` | Timestamp de actualizacion. |

## QueryRequest

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| repo_id | str | si | N/A | Repositorio indexado a consultar. |
| query | str | si | N/A | Pregunta en lenguaje natural. |
| top_n | int | no | `60` | Candidatos de retrieval inicial. |
| top_k | int | no | `15` | Resultados tras reranking. |
| embedding_provider | str \| null | no | `null` | Provider para embedding de la query (`openai`, `anthropic`, `gemini`, `vertex_ai`). |
| embedding_model | str \| null | no | `null` | Modelo para embedding de la query. |
| llm_provider | str \| null | no | `null` | Provider LLM por operación (`openai`, `anthropic`, `gemini`, `vertex_ai`). |
| answer_model | str \| null | no | `null` | Modelo answer por operación. |
| verifier_model | str \| null | no | `null` | Modelo verifier por operación. |

## Citation

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| path | str | si | N/A | Archivo fuente citado. |
| start_line | int | si | N/A | Linea inicial de evidencia. |
| end_line | int | si | N/A | Linea final de evidencia. |
| score | float | si | N/A | Score de recuperacion/cita. |
| reason | str | si | N/A | Motivo de la cita (`hybrid_rag_match` o `inventory_graph_match`). |

## QueryResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| answer | str | si | N/A | Respuesta final (LLM o fallback extractivo). |
| citations | list[Citation] | si | N/A | Evidencia ordenada por prioridad. |
| diagnostics | dict[str, Any] | no | `{}` | Metricas y flags del pipeline. |

## InventoryQueryRequest

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| repo_id | str | si | N/A | Repositorio indexado a consultar. |
| query | str | si | N/A | Consulta de inventario (ej: "todos los X"). |
| page | int | no | `1` | Pagina solicitada (1-indexed). |
| page_size | int | no | `80` | Tamano de pagina solicitado. |

## InventoryItem

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| label | str | si | N/A | Nombre visible del item. |
| path | str | si | N/A | Archivo origen del item. |
| kind | str | no | `file` | Tipo de entidad (`class`, `method`, etc.). |
| start_line | int | no | `1` | Linea inicial. |
| end_line | int | no | `1` | Linea final. |

## InventoryQueryResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| answer | str | si | N/A | Resumen estructurado de inventario. |
| target | str \| null | no | `null` | Objetivo detectado (`service`, `controller`, etc.). |
| module_name | str \| null | no | `null` | Modulo detectado/resuelto. |
| total | int | no | `0` | Total de items encontrados. |
| page | int | no | `1` | Pagina efectiva aplicada. |
| page_size | int | no | `80` | Tamano de pagina efectivo aplicado. |
| items | list[InventoryItem] | no | `[]` | Items paginados. |
| citations | list[Citation] | no | `[]` | Citas de inventario. |
| diagnostics | dict[str, Any] | no | `{}` | Metricas y flags del pipeline de inventario. |

## RepoCatalogResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| repo_ids | list[str] | no | `[]` | Repos disponibles para consulta. |

## RepoQueryStatusResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| repo_id | str | si | N/A | Repo evaluado. |
| listed_in_catalog | bool | si | N/A | Si aparece en `/repos`. |
| query_ready | bool | si | N/A | Si esta listo para `/query`. |
| chroma_counts | dict[str, int \| null] | no | `{}` | Conteo por coleccion (`code_symbols`, `code_files`, `code_modules`). |
| bm25_loaded | bool | si | N/A | Si BM25 esta cargado en memoria. |
| graph_available | bool \| null | no | `null` | Disponibilidad de nodos en Neo4j para ese repo. |
| last_embedding_provider | str \| null | no | `null` | Provider de embedding usado en la ultima ingesta conocida. |
| last_embedding_model | str \| null | no | `null` | Modelo de embedding usado en la ultima ingesta conocida. |
| warnings | list[str] | no | `[]` | Advertencias operativas. |

## StorageHealthItem

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| name | str | si | N/A | Componente (`neo4j`, `chroma`, `bm25`, etc.). |
| ok | bool | si | N/A | Estado del componente. |
| critical | bool | si | N/A | Si rompe preflight estricto. |
| code | str | si | N/A | Codigo tecnico (`neo4j_auth_failed`, etc.). |
| message | str | si | N/A | Mensaje humano de estado. |
| latency_ms | float | si | N/A | Latencia de chequeo. |
| details | dict[str, Any] | no | `{}` | Metadatos extra por componente. |

## StorageHealthResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| ok | bool | si | N/A | Salud consolidada global. |
| strict | bool | si | N/A | Modo de severidad aplicado. |
| checked_at | str | si | N/A | Timestamp ISO del chequeo. |
| context | str | si | N/A | Contexto (`startup`, `ingest`, `query`, `inventory_query`, `health`). |
| repo_id | str \| null | no | `null` | Repo asociado al chequeo, si aplica. |
| cached | bool | no | `false` | Si fue cacheado o freshly computed. |
| failed_components | list[str] | no | `[]` | Lista de componentes fallidos. |
| items | list[StorageHealthItem] | no | `[]` | Estado detallado por componente. |

## ResetResponse

| Campo | Tipo | Requerido | Default | Descripcion |
|---|---|---|---|---|
| message | str | si | N/A | Mensaje final del reset. |
| cleared | list[str] | no | `[]` | Recursos limpiados. |
| warnings | list[str] | no | `[]` | Advertencias no bloqueantes. |

## Errores por endpoint

| Codigo | Endpoint | Causa | Shape resumido |
|---|---|---|---|
| 404 | `GET /jobs/{job_id}` | Job inexistente | `{ "detail": "Job no encontrado" }` |
| 409 | `POST /admin/reset` | Reset con jobs en ejecucion | `{ "detail": "..." }` |
| 422 | `POST /query` | Repo no listo para consulta | `{ "detail": { "code": "repo_not_ready", "repo_status": {...} } }` |
| 422 | Endpoints con body | Error de validacion Pydantic | `{ "detail": [ ... ] }` |
| 500 | `POST /admin/reset` | Error inesperado de limpieza | `{ "detail": "..." }` |
| 503 | `POST /repos/ingest`, `POST /query`, `POST /inventory/query` | Falla preflight de storage | `{ "detail": { "message": "...", "health": StorageHealthResponse } }` |

Ejemplo 422 de readiness (`/query`):

```json
{
  "detail": {
    "message": "El repositorio no esta listo para consultas. Reingesta el repositorio o revisa el estado de indices.",
    "code": "repo_not_ready",
    "repo_status": {
      "repo_id": "mall",
      "listed_in_catalog": true,
      "query_ready": false,
      "chroma_counts": {
        "code_symbols": 0,
        "code_files": 0,
        "code_modules": 0
      },
      "bm25_loaded": false,
      "graph_available": null,
      "warnings": ["No hay indice BM25 en memoria para repo 'mall'."]
    }
  }
}
```

Ejemplo 503 de preflight:

```json
{
  "detail": {
    "message": "Preflight de storage fallo antes de consulta.",
    "health": {
      "ok": false,
      "strict": true,
      "checked_at": "2026-01-01T00:00:00+00:00",
      "context": "query",
      "repo_id": "mall",
      "cached": false,
      "failed_components": ["neo4j"],
      "items": [
        {
          "name": "neo4j",
          "ok": false,
          "critical": true,
          "code": "neo4j_unreachable",
          "message": "neo4j failed",
          "latency_ms": 1.0,
          "details": {}
        }
      ]
    }
  }
}
```

## Diagnostics

## Diagnostics de /query

Campos frecuentes:

- `retrieved`: candidatos recuperados por hybrid search.
- `reranked`: candidatos luego de reranking.
- `graph_nodes`: nodos agregados por expansion de grafo.
- `context_chars`: longitud de contexto ensamblado.
- `raw_citations`, `filtered_citations`, `returned_citations`.
- `low_signal_retrieval`: true cuando `retrieved < 3`.
- `context_sufficient`: validacion minima de contexto.
- `llm_enabled`, `llm_provider`, `llm_answer_model`, `llm_verifier_model`, `llm_verify_enabled`.
- `llm_capabilities`, `embedding_provider`, `embedding_model`, `embedding_capabilities`.
- `discovered_modules`: modulos detectados por heuristica.
- `inventory_intent`: si la consulta parecia de inventario.
- `inventory_route`: `graph_first` o `fallback_to_general` segun routing.
- `fallback_reason`: `not_configured`, `verification_failed`, `generation_error`, `time_budget_exhausted`, `insufficient_context`.
- `verify_valid`, `verify_skipped`.
- `query_budget_seconds`, `budget_exhausted`.
- `stage_timings_ms`: latencia por etapa (`hybrid_search_ms`, `rerank_ms`, `graph_expand_ms`, `module_discovery_ms`, `context_assembly_ms`, `llm_answer_ms`, `llm_verify_ms`, `total_ms`).
- `llm_error`: solo cuando hubo excepcion de generacion/verificacion.

## Diagnostics de /inventory/query

Campos frecuentes:

- `inventory_target`.
- `inventory_terms`.
- `inventory_count`.
- `inventory_explain`.
- `inventory_purpose_count`.
- `module_name_raw`, `module_name_resolved`.
- `query_budget_seconds`, `budget_exhausted`.
- `stage_timings_ms`: `parse_ms`, `graph_inventory_ms`, `pagination_ms`, `component_purpose_ms`, `total_ms`.
- `fallback_reason`: `inventory_target_missing`, `inventory_structured`, `time_budget_exhausted`.

## Comportamientos importantes

- `/query` puede redirigir internamente a inventario cuando detecta intencion tipo "todos los X".
- Si hay intencion de inventario pero no se detecta target, `/query` sigue por flujo general con `inventory_route = fallback_to_general`.
- `/repos/{repo_id}/status` evalua readiness de query por repo sin ejecutar consulta.
- `/health/storage` ejecuta preflight explicito y retorna estado detallado por componente.
- La readiness de query depende de Chroma con documentos para ese repo y BM25 cargado en memoria.

## Ejemplos rapidos

Consulta general:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "mall",
    "query": "cuales son todos los controller del modulo mall-admin?",
    "top_n": 60,
    "top_k": 15
  }'
```

Consulta inventario:

```bash
curl -X POST http://127.0.0.1:8000/inventory/query \
  -H "Content-Type: application/json" \
  -d '{
    "repo_id": "mall",
    "query": "cuales son todos los modelos de mall-mbg",
    "page": 1,
    "page_size": 80
  }'
```

Estado por repo:

```bash
curl http://127.0.0.1:8000/repos/mall/status
```

Salud de storage:

```bash
curl http://127.0.0.1:8000/health/storage
```

## Casos por endpoint

Esta seccion resume respuestas de exito y error por cada servicio HTTP.

### POST /repos/ingest

Exito `200`:

```json
{
  "id": "job-123",
  "status": "queued",
  "progress": 0.0,
  "logs": [],
  "repo_id": null,
  "error": null,
  "created_at": "2026-03-13T10:00:00.000000",
  "updated_at": "2026-03-13T10:00:00.000000"
}
```

Error `503` (preflight de storage):

```json
{
  "detail": {
    "message": "Preflight de storage falló antes de ingesta.",
    "health": {
      "ok": false,
      "failed_components": ["chroma"]
    }
  }
}
```

### GET /jobs/{job_id}

Exito `200`:

```json
{
  "id": "job-123",
  "status": "running",
  "progress": 0.45,
  "logs": ["Escaneando archivos..."],
  "repo_id": null,
  "error": null,
  "created_at": "2026-03-13T10:00:00.000000",
  "updated_at": "2026-03-13T10:00:18.000000"
}
```

Error `404`:

```json
{
  "detail": "Job no encontrado"
}
```

### POST /query

Exito `200`:

```json
{
  "answer": "...",
  "citations": [
    {
      "path": "mall-admin/src/main/java/.../AdminController.java",
      "start_line": 42,
      "end_line": 88,
      "score": 0.91,
      "reason": "hybrid_rag_match"
    }
  ],
  "diagnostics": {
    "retrieved": 60,
    "reranked": 15,
    "graph_nodes": 12,
    "fallback_reason": null,
    "stage_timings_ms": {
      "hybrid_search_ms": 93.2,
      "rerank_ms": 5.4,
      "total_ms": 312.8
    }
  }
}
```

Error `422` (repo no listo):

```json
{
  "detail": {
    "message": "El repositorio no está listo para consultas. Reingesta el repositorio o revisa el estado de índices.",
    "code": "repo_not_ready",
    "repo_status": {
      "repo_id": "mall",
      "listed_in_catalog": true,
      "query_ready": false,
      "chroma_counts": {
        "code_symbols": 0,
        "code_files": 0,
        "code_modules": 0
      },
      "bm25_loaded": false,
      "graph_available": null,
      "warnings": ["No hay indice BM25 en memoria para repo 'mall'."]
    }
  }
}
```

Error `503` (preflight):

```json
{
  "detail": {
    "message": "Preflight de storage falló antes de consulta.",
    "health": {
      "ok": false,
      "failed_components": ["neo4j"]
    }
  }
}
```

### POST /inventory/query

Exito `200`:

```json
{
  "answer": "Se encontraron 11 elementos de inventario.",
  "target": "modelo",
  "module_name": "mall-mbg",
  "total": 11,
  "page": 2,
  "page_size": 5,
  "items": [
    {
      "label": "CmsHelp.java",
      "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
      "kind": "file",
      "start_line": 1,
      "end_line": 1
    }
  ],
  "citations": [
    {
      "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
      "start_line": 1,
      "end_line": 1,
      "score": 1.0,
      "reason": "inventory_graph_match"
    }
  ],
  "diagnostics": {
    "inventory_count": 11,
    "fallback_reason": null
  }
}
```

Error `503` (preflight):

```json
{
  "detail": {
    "message": "Preflight de storage falló antes de inventario.",
    "health": {
      "ok": false,
      "failed_components": ["neo4j"]
    }
  }
}
```

### GET /repos

Exito `200`:

```json
{
  "repo_ids": ["mall", "api-service"]
}
```

### GET /repos/{repo_id}/status

Exito `200`:

```json
{
  "repo_id": "mall",
  "listed_in_catalog": true,
  "query_ready": true,
  "chroma_counts": {
    "code_symbols": 10,
    "code_files": 5,
    "code_modules": 2
  },
  "bm25_loaded": true,
  "graph_available": true,
  "warnings": []
}
```

### GET /health/storage

Exito `200`:

```json
{
  "ok": true,
  "strict": true,
  "checked_at": "2026-03-13T10:00:00+00:00",
  "context": "health",
  "repo_id": null,
  "cached": true,
  "failed_components": [],
  "items": []
}
```

### POST /admin/reset

Exito `200`:

```json
{
  "message": "Limpieza total completada",
  "cleared": ["BM25 en memoria", "Grafo Neo4j"],
  "warnings": []
}
```

Error `409` (jobs en ejecucion):

```json
{
  "detail": "No se puede limpiar mientras hay jobs en ejecución."
}
```

Error `500` (fallo inesperado):

```json
{
  "detail": "<mensaje de error interno>"
}
```
