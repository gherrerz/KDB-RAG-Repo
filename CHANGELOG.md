# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.

Este formato sigue Keep a Changelog y Semantic Versioning.

## [Unreleased]

### Added

- Resolución de URLs y credenciales de infraestructura (Chroma, Postgres, Neo4j,
  Redis) diferenciada por entorno mediante variables con sufijo
  `_DEV`/`_TEST`/`_PROD`, gobernadas por `RUNTIME_ENVIRONMENT`. Precedencia por
  variable: `{VAR}_{SUFIJO}` → `{VAR}` (base, fallback) → default. Implementado en
  el `model_validator(mode="before")` de `settings.py`; documentado en
  `docs/CONFIGURATION.md` y `.env.example`. Sin breaking change: si solo existe la
  variable base, el comportamiento es idéntico al previo.

### Security

- Remediadas **CVE-2026-48818** (SSRF/robo NTLM vía rutas UNC en `StaticFiles`, solo Windows) y
    **CVE-2026-54283** (DoS por bypass de límites en `request.form()` para
    `application/x-www-form-urlencoded`) en Starlette: se sube `fastapi` a `0.137.1` y `starlette`
    a `1.3.1` (que también mantiene corregida CVE-2025-62727). La app no usa `StaticFiles` ni parseo
    de formularios y corre en Linux, por lo que ninguna era explotable, pero se actualiza para limpiar
    el escaneo Trivy. Solo cambio de dependencias (la app no importa Starlette directamente).
- Remediada **CVE-2025-62727** (DoS en Starlette por parseo cuadrático del header `Range`):
    `fastapi` se actualizó a `0.120.1` y se fijó explícitamente `starlette==0.49.1` en
    `requirements.txt` y `requirements-runtime.txt` (posteriormente elevado a `starlette==1.3.1`).
- Remediada **CVE-2026-45829** ("ChromaToast", RCE pre-auth en el servidor Python de ChromaDB):
    la imagen de servidor (`requirements.txt`) reemplaza `chromadb` por el thin client
    `chromadb-client`, que no incluye el código del servidor vulnerable. La imagen pasa a soportar
    solo `CHROMA_MODE=remote`; el modo `embedded` (PersistentClient) sigue disponible en
    desarrollo/desktop con `chromadb` completo. Hardening adicional en `docker-compose.yml`
    (tag de imagen fijo, puerto publicado solo en loopback, guía de autenticación del servidor).

### Added

- Soporte **MCP (Model Context Protocol)** coexistiendo con la API REST en el mismo
    proceso/puerto: se monta un endpoint `/mcp` (transporte HTTP streamable) mediante
    `fastapi-mcp`, cuyas tools se derivan automáticamente del OpenAPI de FastAPI (el nombre
    de cada tool es el `operation_id` de la ruta). Cualquier agente de IA compatible con MCP
    puede descubrir (`tools/list`) y ejecutar (`tools/call`) las operaciones. Solo se exponen
    operaciones de consulta, lectura e ingesta (`ingest_repo`, `get_job`, `query_repo`,
    `query_retrieval`, `query_inventory`, `list_repos`, `list_repo_snapshots`,
    `list_stale_repos`, `list_provider_models`, `repo_status`, `storage_health`) mediante un
    filtro `include_operations` (default-deny); los endpoints admin/destructivos quedan fuera.
    El endpoint se controla con `MCP_ENABLED` y se protege con `MCP_API_TOKEN` (header
    `X-MCP-Token`); configurable también con `MCP_MOUNT_PATH` y `MCP_SERVER_NAME`. Nuevo módulo
    `src/coderag/api/mcp_server.py` (`setup_mcp`) y script de smoke test `scripts/mcp_smoke.sh`.
    Se fija `fastapi-mcp==0.4.0` y `mcp==1.23.0`; al exigir `mcp>=1.13` `pydantic>=2.11` y
    `uvicorn>=0.31.1`, se elevan `pydantic` a `2.13.4`, `pydantic-settings` a `2.14.1` y `uvicorn`
    a `0.49.0` (se mantienen `starlette==1.3.1` y `httpx==0.27.2`).
- Headers de identidad opcionales en los servicios MCP: `x-role-id`, `x-user-id` y
    `x-country-id` se reenvían (pass-through) desde la conexión `/mcp` hacia cada tool vía el
    allowlist de `fastapi-mcp` y se declaran en el OpenAPI de las operaciones expuestas. Nuevo
    módulo `src/coderag/api/identity_headers.py` (dependencia `identity_headers` +
    `IDENTITY_HEADER_NAMES`).

### Changed

- **Scope de tools MCP reducido de 11 a 5**: el servidor MCP en `/mcp` ahora solo
    expone operaciones de consulta y lectura directa (`query_repo`, `query_retrieval`,
    `list_repos`, `repo_status`, `storage_health`). Se retiran `ingest_repo`,
    `get_job`, `query_inventory`, `list_repo_snapshots`, `list_stale_repos` y
    `list_provider_models` del filtro `include_operations` en
    `src/coderag/api/mcp_server.py`. Los endpoints REST correspondientes no se
    modifican y siguen disponibles via HTTP.

- Nuevos indicadores operativos en `GET /repos/{repo_id}/snapshots`:
    `repo_size_mb` para el tamaño efectivamente leído del repositorio y
    `embedding_tokens_read_estimated` para el estimado de tokens enviados a la
    etapa de embeddings.
- Flujo offline RAGAS para benchmark de respuestas completas de código, con
    dataset materializado, collector contra `POST /query`, reconstrucción local
    de contextos citados y scorer con selección `auto/proxy/ragas`, fallback
    seguro al proxy local y overlay opcional de dependencias en
    `requirements-ragas-eval.txt`, además de calibración del proxy para reducir
    el castigo a respuestas largas pero correctamente fundamentadas. El scorer
    real ahora reutiliza la configuración Vertex del runtime para el path
    `ragas` y el overlay opcional incorpora `jsonref` para soportar el smoke
    real sobre Vertex.
- Archivos de dependencias separados para runtime, desktop, desarrollo y
    entorno completo local:
    `requirements-runtime.txt`, `requirements-desktop.txt`,
    `requirements-dev.txt` y `requirements-full.txt`.
- Estructura documental orientada a customer journeys.
- Nuevas guias en docs/ para instalacion, configuracion, arquitectura,
  troubleshooting y contribucion.
- Ejemplos ejecutables en examples/ para ingesta y consultas.
- Scripts iniciales de validacion de documentacion en scripts/docs/.
- Dockerfile multi-stage y `.dockerignore` para empaquetar la API.
- Manifests Kubernetes nativos (`k8s/base`, overlays cloud y addon Redis opcional).
- Scripts `start_compose.ps1` y `stop_compose.ps1` para operar stack local completo.
- Worker RQ dedicado para ingesta asíncrona distribuida (modo `INGESTION_EXECUTION_MODE=rq`).
- Lock distribuido por `repo_id` para serializar encolado de ingestas en modo RQ.
- Nuevo entrypoint de API en `src/main.py` para arranque directo con `python -m main` usando `PYTHONPATH=src`.
- Nueva guia `KUBERNETES.md` con despliegue, secretos, probes, persistencia, rollback y validacion funcional.
- Helper operativo `scripts/postgres_schema_admin.py` para ejecutar
    `current`, `validate`, `upgrade` y `stamp` de Alembic usando la misma
    resolucion `POSTGRES_*` del runtime.
- Script `scripts/migrate_legacy_postgres_to_alembic.py` para copiar datos
    desde las tablas PostgreSQL legacy `jobs`, `repos` y `lexical_corpus` al
    esquema versionado `tbl_repository_*`.
- Auditoria source/target en la migracion legacy para comparar conteos antes y
    despues del cutover (`source_count`, `target_count_before`,
    `target_count_after`, `matched_after`, `missing_after`).
- Guia de cutover para PostgreSQL legacy con checklist operativo y uso de
    reportes exportables JSON/CSV.
- Runner `scripts/run_postgres_legacy_cutover.py` para ejecutar secuencia de
    pre-check, migracion, validate y checklist exportable durante una ventana de
    cutover real o representativa.
- El runner de cutover ahora puede registrar confirmaciones manuales de
    backup, rollback y retencion temporal de tablas legacy, y reflejar
    `manual_checks_complete` / `cutover_ready` en el reporte final.
- El runner de cutover ahora puede emitir evidencia formal de cierre de
    observacion para el retiro legacy, incluyendo
    `observation_exit_automatic_ready`, `observation_checks_complete`,
    `observation_exit_ready` y confirmaciones manuales de aprobacion.
- El contrato visible de status por repositorio deja de exponer
    `bm25_loaded`; `lexical_loaded` queda como único indicador público de
    readiness léxico.
- Nueva guia de retiro de storage legacy con ventana de observacion,
    criterio de salida y rollback para tablas PostgreSQL legacy, SQLite y BM25.
- Nueva guia de handoff para cierre formal del bloque ORM/PostgreSQL y retiro
    legacy, con backlog final, evidencia de validacion y superficie residual
    aceptada.
- Tracking operativo `last_queried_at` por repositorio en metadata runtime y
    nuevo endpoint `GET /repos/last-query/stale` para listar repositorios sin
    consultas recientes o nunca consultados.
- Nueva guia operativa de Fase 6 para cutover Alembic en base Postgres
    compartida con KDB-RAG-Docs:
    `docs/migration-guides/alembic-shared-db-cutover.md`.
- Politica de retiro controlado para la tabla `alembic_version` legacy,
    con ventana minima de observacion, validaciones previas y estrategia
    reversible rename-then-drop en la guia de cutover compartido.
- Soporte AST embebido con Tree-sitter para Kotlin y Swift en la ingesta,
    sin sidecars JVM ni toolchain Swift adicional.
- Nuevos extractores estructurales Kotlin y Swift con spans exactos y snippets
    completos integrados al chunker por registry.
- Nuevas flags `SEMANTIC_GRAPH_KOTLIN_ENABLED` y
    `SEMANTIC_GRAPH_SWIFT_ENABLED` para habilitar semantica gradual por
    lenguaje.
- Nueva semantica Kotlin de Fase 1 para `IMPORTS`, `CALLS`, `EXTENDS` e
    `IMPLEMENTS`, con cobertura intrafile y cross-file basica.
- Nueva semantica Swift de Fase 1 para `IMPORTS`, `CALLS`, `EXTENDS` e
    `IMPLEMENTS`, con soporte de `extension`, `associatedtype`, `where`
    constraints y desambiguacion basica cross-file.
- Validacion manual de cierre de Fase 1 para Kotlin y Swift sobre archivos
    reales temporales usando `scan_repository()`, `extract_symbol_chunks()` y
    semantica por lenguaje.
- Nueva ruta graph-first para consultas de impacto por archivo en
    `POST /query` y `POST /query/retrieval`, con dependientes directos y
    transitivos hasta profundidad 2 via `IMPORTS_FILE`.
- Nuevos diagnosticos de ingesta para file imports:
    `semantic_graph.file_import_resolution_counts`,
    `semantic_graph.file_import_resolution_counts_by_language` y
    `semantic_graph.file_import_counts_by_language`.

### Changed

- `POST /admin/reset` ahora queda deshabilitado por default y exige
    `ADMIN_RESET_ENABLED=true`, `ADMIN_RESET_TOKEN`, header
    `X-Admin-Reset-Token` y body explícito de confirmación con
    `confirm=true` y `confirmation_phrase="RESET ALL DATA"`.
- La UI desktop ahora envía el nuevo contrato administrativo de reset y falla
    localmente si no tiene `ADMIN_RESET_TOKEN` configurado al apuntar a una API
    protegida.
- El versionado Alembic queda aislado por aplicacion cuando KDB-RAG-Repo y
    KDB-RAG-Docs comparten la misma base Postgres: Repo usa
    `alembic_version_repo` y Docs usa `alembic_version_docs`, evitando
    colisiones en `alembic_version`.
- Se agregan guardrails de CI para bloquear regresiones hacia
    `alembic_version` default en rutas criticas de migracion y bootstrap
    (`tests/test_alembic_version_contract.py`).
- Se elimina la variable monolitica legacy de conexion Postgres del contrato de configuracion; el runtime, Compose,
  Kubernetes y scripts pasan a usar `POSTGRES_HOST`, `POSTGRES_PORT`,
  `POSTGRES_DB`, `POSTGRES_USER` y `POSTGRES_PASSWORD`.
- El CLI directo de Alembic vuelve a resolver `POSTGRES_*` correctamente al
    no tratar placeholders vacios de `sqlalchemy.url` como una URL efectiva.
- El backend Postgres ahora crea y consulta las tablas
    `tbl_repository_jobs`, `tbl_repository_repos` y
    `tbl_repository_lexical_corpus`; despliegues existentes con tablas legacy
    `jobs`, `repos` y `lexical_corpus` requieren reset o recreacion de la base
    si no se aplica una migracion manual.
- La organización persistida del repositorio ahora usa solo el último segmento
    padre del path Git y deja de derivarse al vuelo en `GET /repos`.
- `repo_id` pasa a formarse como `organizacion-repo-rama`; las ingestas
    existentes requieren reingesta para alinearse con el nuevo formato.
- Canonical provider naming now uses `vertex` for `LLM_PROVIDER` y
    `EMBEDDING_PROVIDER`; `vertex_ai` se mantiene temporalmente como alias
    compatible en runtime.
- `requirements.txt` pasa a representar el baseline API/worker para que el
    contrato por defecto priorice levantar la API.
- `Dockerfile` mantiene build de runtime usando el contrato API-first de
    `requirements.txt`, dejando fuera PySide6 y pytest del contenedor.
- Python, Java, JavaScript, TypeScript, Kotlin y Swift ahora alimentan
        `FileImportRelation` para aristas `IMPORTS_FILE`/`IMPORTS_EXTERNAL_FILE`;
        Swift mantiene emision conservadora y Go queda fuera del contrato
        semantico de impacto por archivo.
- `chromadb` se actualiza a `1.5.5` para alinear el stack vectorial con versiones recientes del ecosistema.
- README reestructurado como portal corto de navegacion.
- API reference reorganizada por journeys y operaciones.
- Estructura del paquete movida de `coderag/` a `src/coderag/`.
- Imports y entrypoints actualizados a `coderag.*` para API, UI,
  scripts y tests, manteniendo layout `src/` mediante `PYTHONPATH=src`.
- `docker-compose.yml` evolucionó de solo Neo4j a stack completo API + Neo4j, con perfil opcional Redis.
- `docker-compose.yml` ahora incluye servicio `worker` al activar perfil `redis`.
- Scripts `start_dev`, `start_stable` y `reset_cold` mantienen modo local iniciando solo Neo4j desde Compose.
- Scripts locales, benchmark de rollback y Docker runtime migran a arranque de API por `main` con `PYTHONPATH` configurado.
- Guia tecnica de configuracion ampliada para reflejar variables reales del runtime y defaults de health/model discovery.
- Documentacion de arquitectura aclara mapeo de puerto Redis `16379->6379` en Compose local.
- Requisitos de Python en README/INSTALLATION se normalizan a `3.12+` con referencia a version validada.
- API de ingesta retorna `503` cuando falla el encolado asíncrono.
- API de ingesta retorna `409` si ya existe ingesta activa para el mismo repositorio.
- Worker RQ ahora propaga fallas para activar la política de reintentos configurada.
- Política de reintentos configurable para relanzar solo errores transitorios.
- La operacion de migraciones PostgreSQL queda cerrada sobre SQLAlchemy 2 +
    Alembic: API y worker auto-upgradean en `development`/`test`, `production`
    valida sin mutar, y el entorno puede ejecutar el mismo flujo manualmente con
    `scripts/postgres_schema_admin.py`.
- El backlog ORM avanza al bloque de migracion de datos reales: el repo ahora
    puede crear el esquema Alembic actual y copiar metadata/corpus desde las
    tablas PostgreSQL legacy sin depender del camino SQL nativo en runtime.
- Con Postgres activo, la ingesta deja de hacer dual-write a BM25 por defecto;
    BM25 queda como rollback controlado mediante
    `LEXICAL_LEGACY_BM25_READ_FALLBACK` y
    `LEXICAL_LEGACY_BM25_DUAL_WRITE`.
- Los flujos de health, status y reset/delete dejan de tratar BM25 y SQLite
    como backend operativo principal: ahora se presentan como fallbacks legacy,
    y el borrado BM25 se omite cuando Postgres es el backend primario.
- `lexical_loaded` pasa a ser el indicador canónico de readiness en payloads
    internos; `bm25_loaded` queda reducido a compatibilidad del endpoint de
    status por repositorio.
- SQLite de metadata deja de ser fallback implícito del runtime y pasa a
    requerir `METADATA_LEGACY_SQLITE_FALLBACK=true` para rollback local
    controlado cuando no hay Postgres.
- Worker y pruebas de integración RQ dejan de asumir `metadata.db` como ruta
    estructural del backend operativo; ahora validan el store resuelto por la
    misma factory de metadata del runtime.
- La búsqueda híbrida deja de depender directamente de `GLOBAL_BM25` en la
    capa de ejecución y pasa a resolver el backend léxico a través de la
    abstracción compartida del runtime.
- El runtime operativo queda restringido a Postgres versionado + LexicalStore
    Postgres: se retiran del contrato soportado las flags
    `METADATA_LEGACY_SQLITE_FALLBACK`,
    `LEXICAL_LEGACY_BM25_READ_FALLBACK` y
    `LEXICAL_LEGACY_BM25_DUAL_WRITE`.
- La factory de metadata, la selección de backend léxico, el healthcheck y la
    ingesta dejan de resolver SQLite/BM25 como caminos operativos; los
    artefactos legacy restantes quedan solo para limpieza física posterior.
- Nueva migración Alembic `0002_drop_legacy_postgres_tables` para retirar las
    tablas PostgreSQL legacy `jobs`, `repos` y `lexical_corpus` de forma
    auditada.
- `reset_service` deja de limpiar snapshots BM25 y `metadata.db`; el tooling
    operativo ya no manipula artefactos físicos legacy.
- La política de fallback legacy para BM25 y metadata SQLite se concentra en un
    módulo único de runtime, y las pruebas de pipeline/health dejan de pinchar
    esos artefactos como camino operativo principal salvo en escenarios de
    rollback controlado.
- El selector léxico legacy deja de materializar `GLOBAL_BM25` fuera del gate
    centralizado, y las pruebas de contrato restantes pasan a usar helpers de
    runtime legacy o el contrato SQLite aislado en lugar de rutas hardcodeadas.
- Release 5 elimina los marcadores temporales `legacy_compat` y
    `legacy_rollback`; la suite activa vuelve a reflejar solo comportamiento
    soportado y la guía de retiro queda actualizada al estado final.
- Se retira por completo el módulo histórico de BM25, su dependencia
    `rank-bm25`, los tests reservados a ese backend y el tooling auxiliar que
    aún referenciaba snapshots o benchmarks BM25.

### Fixed

- `datetime.utcnow` se reemplaza por timestamps UTC aware y se agregan filtros temporales de warnings de terceros en `pytest.ini`.
- Cobertura explicita de DELETE /repos/{repo_id} en documentacion de API.
- Cobertura de parametro logs_tail en GET /jobs/{job_id}.
- Query ya no trata el workspace local como prerequisito para query semántico,
    retrieval-only e inventario; `literal` queda bloqueado explícitamente cuando
    el workspace no existe.
- `inventory explain` y el discovery de módulos dejan de leer el workspace y
    pasan a resolverse con metadata persistida en Neo4j.
- `GET /repos` y `GET /repos/{repo_id}/status` dejan de depender del clone
    local para listar repos indexados; el status ahora expone
    `workspace_available` para distinguir readiness de query frente a
    disponibilidad de modo literal.
- Nueva opción `RETAIN_WORKSPACE_AFTER_INGEST` para eliminar automáticamente
    el clone local al terminar la ingesta y ahorrar espacio cuando no se
    necesita modo literal.
- Los manifests de despliegue locales/cloud de este repo activan
    `RETAIN_WORKSPACE_AFTER_INGEST=false` para que el cleanup post-ingesta quede
    habilitado por defecto en runtime.
- Imagen runtime ahora incluye `git` para permitir clonación durante ingestas en API/worker.
- `start_compose.ps1` aplica `HEALTH_CHECK_OPENAI=false` por defecto si no está definido.
- El bootstrap de PostgreSQL ya no estampa como `head` bases legacy parciales o
    incompatibles: ahora exige tablas y columnas requeridas antes de considerar
    un esquema legacy como compatible.
- El scanner ahora clasifica archivos `.kt` como `kotlin` y `.swift` como
    `swift`, y el pipeline de grafo puede procesar ambos lenguajes detras de sus
    flags dedicadas.
- La semantica Swift amplía la inferencia de receivers sin introducir nuevos
    relation types: ahora reutiliza contexto de `extension`, propiedades del
    tipo, herencia, imports/modulo, aliases locales, optional bindings y
    constraints de `associatedtype`/`where`.
- La resolucion de `CALLS` en Swift ahora normaliza wrappers livianos que
    preservan el tipo de elemento, incluyendo optionals, arrays tipados,
    subscript de colecciones, `first`, `last`, `lazy`, `dropFirst`, `dropLast`,
    `prefix`, `suffix`, `reversed`, `sorted` y `filter { ... }` cuando el
    receiver observable sigue siendo el mismo tipo base.
