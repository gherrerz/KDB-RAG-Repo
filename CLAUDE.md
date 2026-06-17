# CLAUDE.md — RAG Hybrid Response Validator

Instrucciones de proyecto para Claude Code. Se carga automáticamente en cada sesión.

---

## Sistema

API FastAPI + UI PySide6 que permite ingestar repositorios Git (GitHub/Bitbucket) y consultarlos con Hybrid RAG (vector + léxico + grafo). Stack: Chroma (vectores), Neo4j (grafo), Postgres (FTS + metadata), Redis/RQ (jobs distribuidos, opcional).

Módulos clave bajo `src/coderag/`:
- `api/` — FastAPI: `server.py` (rutas), `webhook_bitbucket.py` (webhook directo BB)
- `ingestion/` — pipeline de ingesta (`pipeline.py`, `git_client.py`, extractores por lenguaje)
- `retrieval/` — búsqueda híbrida + reranker + expansión de grafo
- `jobs/` — `worker.py` (JobManager), `rq_worker.py` (modo distribuido)
- `core/` — `models.py` (Pydantic), `settings.py` (env vars), `storage_health.py`
- `storage/` — `metadata_store.py`, lexical store

---

## Restricciones críticas del runtime

- **No SQLite fallback** — el único backend de metadata soportado es Postgres.
- **Chroma remoto por defecto** — `CHROMA_MODE=remote`.
- **Reingesta incremental por diff de commits (Chroma + Postgres)** — si el repo tiene un
  `last_indexed_commit` persistido y la ingesta resuelve un HEAD distinto (o se pasa `changed_files`
  explícito), solo se reembedden/reindexan los archivos cambiados en Chroma y el léxico. **El grafo Neo4j
  siempre se reconstruye completo** para preservar la consistencia de aristas cross-file. Sin commit base,
  sin diff resoluble, o sin data previa → fallback a **purge + reindex completo**.
- **Anthropic no es provider activo** — LLM runtime usa OpenAI, Gemini o Vertex.
- **`metadata.db`** solo existe en artefactos de pruebas aisladas.

---

## Endpoints principales

| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/repos/ingest` | Crea job de ingesta async → devuelve `JobInfo` |
| GET | `/jobs/{id}` | Estado del job + logs (query param `logs_tail`) |
| POST | `/query` | Hybrid RAG + LLM |
| POST | `/query/retrieval` | Retrieval-only sin LLM |
| POST | `/inventory/query` | Graph-first inventory |
| GET | `/repos/{id}/status` | Readiness: `query_ready`, `embedding_compatible` |
| GET | `/health` | Estado de todos los componentes de storage |
| POST | `/webhook/bitbucket` | Webhook directo BB Server/DC → dispara ingesta |
| POST/GET | `/mcp` | Servidor MCP (envoltura `fastapi-mcp`) para agentes de IA: `tools/list` + `tools/call` |

`/query` y `/query/retrieval` exigen `query_ready=true` y compatibilidad de embeddings o devuelven 422.

El servidor MCP (`/mcp`) coexiste con la API REST en el mismo proceso/puerto; deriva sus tools del
OpenAPI (nombre = `operation_id`). Solo expone consulta/lectura/ingesta (admin/destructivo excluido por
`include_operations`). Config: `MCP_ENABLED`, `MCP_API_TOKEN` (header `X-MCP-Token`), `MCP_MOUNT_PATH`,
`MCP_SERVER_NAME`. Impl: `src/coderag/api/mcp_server.py`.

---

## Webhook Bitbucket (`/webhook/bitbucket`)

Escucha `pr:merged` de Bitbucket Server/Data Center. Configuración via env vars:

| Env var | Tipo K8s | Descripción |
|---------|----------|-------------|
| `WEBHOOK_BITBUCKET_SECRET` | secret | HMAC-SHA256 para validar firma |
| `WEBHOOK_BITBUCKET_AUTH_USERNAME` | secret | Usuario cuenta de servicio |
| `WEBHOOK_BITBUCKET_AUTH_SECRET` | secret | Password/app-password |
| `WEBHOOK_BITBUCKET_REPO_REGISTRY` | extraEnv | JSON: repos habilitados + ramas + embedding |
| `WEBHOOK_BITBUCKET_INTERNAL_BASE_URL` | extraEnv | Host interno K8s para clonar |
| `WEBHOOK_BITBUCKET_TARGET_BRANCHES` | extraEnv | Fallback de ramas (default: `main,master`) |

Formato del registro de repos:
```json
{
  "_defaults": { "target_branches": ["main", "master"], "auth_method": "http_basic", ... },
  "PROJ/repo": { "enabled": true, "target_branches": ["main", "release/v2"] }
}
```

---

## Convenciones Python

- Type hints obligatorios en todas las funciones públicas. Usar built-ins: `list[str]`, `dict[str, int]`.
- Docstrings solo en funciones públicas. Comentarios inline solo para WHY no-obvios.
- Preferir composición sobre herencia. Separar I/O de lógica de negocio.
- SOLID: SRP, DI por parámetro/constructor (no instanciar dependencias dentro de lógica).
- PEP 8: 4 espacios, líneas ≤ 79 caracteres.
- `Protocol` o ABC para abstracciones; escribir tests de contrato cuando hay múltiples implementaciones.

---

## Convenciones Docker/K8s

Al editar `Dockerfile` o `docker-compose*.yml`, aplicar las guías en
[.github/instructions/containerization-docker-best-practices.instructions.md](.github/instructions/containerization-docker-best-practices.instructions.md).
Resumen: multi-stage builds, imagen base slim/alpine con tag fijo, usuario no-root, HEALTHCHECK, sin secretos en layers.

---

## Actualización de documentación

Al cambiar endpoints, env vars, configuración o flujos de ingesta, actualizar:
- `CHANGELOG.md` — **siempre**: registrar el cambio bajo `[Unreleased]` en la categoría
  Keep a Changelog correspondiente (`Added`, `Changed`, `Fixed`, `Security`, etc.)
- `docs/API_REFERENCE.md` — contratos de endpoints
- `docs/CONFIGURATION.md` — nuevas variables de entorno
- `README.md` — si cambia el quick-start o la arquitectura general

Ver reglas completas en [.github/instructions/update-docs-on-code-change.instructions.md](.github/instructions/update-docs-on-code-change.instructions.md).

---

## Agentes disponibles

Subagentes definidos en `.claude/agents/` que puedes invocar con el Agent tool:

| Agente | Cuándo usarlo |
|--------|---------------|
| `qa` | Planificar tests, analizar edge cases, verificar comportamiento de un feature |
| `sast-sca` | Análisis de seguridad estático (SAST) o auditoría de dependencias (SCA) |

---

## Política anti-alucinación

1. No inventar relaciones, módulos ni capacidades que no existan en el código.
2. Si no hay evidencia en el repo para una afirmación, decirlo explícitamente.
3. No documentar como capacidad actual algo que solo está en roadmap.
4. Si el repositorio no está `query_ready`, rechazar la consulta con error de contrato.
