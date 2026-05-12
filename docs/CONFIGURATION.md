# Configuration

Guia de configuracion de entorno y providers.

Esta guia distingue entre defaults reales del codigo, setup local versionado y
overrides de despliegue. La fuente de verdad de runtime sigue siendo
`src/coderag/core/settings.py`; `docker-compose.yml` y `.env.example`
representan escenarios operativos especificos que pueden diferir del backend
principal recomendado.

## Variables clave

### LLM y proveedores

- `LLM_PROVIDER`: proveedor principal de LLM para answer/verify (`openai`, `gemini`, `vertex`). Default: `vertex`.
- `LLM_ANSWER_MODEL`: override del modelo de respuesta multi-provider. Default: `gemini-2.5-flash`.
- `LLM_VERIFIER_MODEL`: override del modelo de verificacion multi-provider. Default: `gemini-2.5-flash`.
- `LLM_VERIFY_ENABLED`: habilita la verificacion semantica de respuesta. Default: `false`.
- `OPENAI_API_KEY`: credencial OpenAI. Default: vacio.
- `OPENAI_TIMEOUT_SECONDS`: timeout de llamadas OpenAI. Default: `60`.
- `GEMINI_API_KEY`: credencial Gemini. Default: vacio.
- `VERTEX_AI_AUTH_MODE`: metadato legacy del modo de autenticacion Vertex. Default: `service_account`.
- `VERTEX_SERVICE_ACCOUNT_JSON_B64`: JSON de Service Account codificado en Base64. Es la credencial canónica de Vertex. Default: vacio.
- `VERTEX_API_BASE_URL`: base URL regional efectiva de Vertex. De esta URL se deriva `location`. Default: `https://us-central1-aiplatform.googleapis.com`.
- `VERTEX_API_VERSION`: version REST para endpoints Vertex. Default: `v1`.
- `VERTEX_GENERATE_CONTENT_PATH_TEMPLATE`: template del path `generateContent`. Default: `/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent`.
- `VERTEX_PREDICT_PATH_TEMPLATE`: template del path `predict`. Default: `/projects/{project}/locations/{location}/publishers/google/models/{model}:predict`.
- `VERTEX_MODELS_PATH_TEMPLATE`: template del path de `publisher models`. Default: `/projects/{project}/locations/{location}/publishers/google/models`.
- `VERTEX_AUTH_TOKEN_URL`: URL de token OAuth para Vertex auth. Default: `https://oauth2.googleapis.com/token`.
- `VERTEX_AI_PROJECT_ID`: fallback legacy si no puede derivarse `project_id` desde el service account. Default: vacio.
- `VERTEX_AI_LOCATION`: fallback legacy si no puede derivarse `location` desde `VERTEX_API_BASE_URL`. Default: `us-central1`.
- `VERTEX_AI_LABELS_ENABLED`: habilita labels de request en llamadas Vertex AI. Default: `true`.
- `VERTEX_AI_LABEL_SERVICE`: nombre de servicio para labels Vertex. Default: `webspec-coipo`.
- `VERTEX_AI_LABEL_SERVICE_ACCOUNT`: override opcional del label `service_account`. Default: `qa-anthos`.
- `VERTEX_AI_LABEL_USE_CASE_ID`: use case base para labels Vertex. Default: `tbd`.
- `VERTEX_AI_CORRELATION_ID_ENABLED`: agrega `x-correlation-id` por request Vertex. Default: `true`.

### Embeddings

- `EMBEDDING_PROVIDER`: proveedor de embeddings (`openai`, `gemini`, `vertex`). Default: `vertex`.
- `EMBEDDING_MODEL`: override del modelo de embedding. Default: `text-embedding-005`.

Compatibilidad temporal de naming:

- `vertex_ai` sigue siendo aceptado como alias legacy y se normaliza a `vertex`.
- El prefijo de variables `VERTEX_AI_*` se mantiene por compatibilidad operativa.

### Retrieval y limites de consulta

- `CHROMA_MODE`: modo de acceso a Chroma (`remote`, `embedded`). Default del codigo: `remote`.
- `CHROMA_HOST`: host del servicio Chroma remoto. Default: `localhost`.
- `CHROMA_PORT`: puerto del servicio Chroma remoto. Default: `8000`.
- `CHROMA_TOKEN`: bearer token opcional para Chroma remoto. Default: vacio.
- `CHROMA_USERNAME`: usuario opcional para Basic auth contra Chroma remoto. Default: vacio.
- `CHROMA_PASSWORD`: password opcional para Basic auth contra Chroma remoto. Default: vacio.
- `CHROMA_PATH`: ruta fisica del indice vectorial solo relevante en `CHROMA_MODE=embedded`. Default: `/app/storage/chroma`.
- `CHROMA_HNSW_SPACE`: metrica del indice HNSW (`cosine` o `l2`). Default: `cosine`.
- `MAX_CONTEXT_TOKENS`: limite superior de tokens de contexto armado para LLM. Default: `8000`.
- `GRAPH_HOPS`: profundidad de expansion de grafo estructural. Default: `2`.
- `QUERY_MAX_SECONDS`: limite global de latencia para query API. Default: `55`.
- `UI_REQUEST_TIMEOUT_SECONDS`: timeout de request desde UI a API. Default: `90`.

### Storage, metadata, lexical y workspace

- `POSTGRES_HOST`: host del backend operativo de Postgres para metadata y store lexico. Cuando esta configurado, el runtime usa Postgres en lugar de SQLite/BM25 local. Default: vacio.
- `POSTGRES_PORT`: puerto TCP de Postgres. Default: `5432`.
- `POSTGRES_DB`: nombre de base para despliegues locales o Compose que levanten Postgres gestionado por este repo. Default: `coderag`.
- `POSTGRES_USER`: usuario de Postgres para despliegues locales o Compose. Default: `coderag`.
- `POSTGRES_PASSWORD`: password de Postgres para despliegues locales o Compose. Default: `coderag`.
- `POSTGRES_POOL_SIZE`: tamano de pool para conexiones Postgres. Default: `5`.
- `POSTGRES_POOL_TIMEOUT`: timeout de pool Postgres. Default: `30`.
- `LEXICAL_FTS_LANGUAGE`: lenguaje de FTS para Postgres lexical. Default: `english`.
- `WORKSPACE_PATH`: ruta de clones temporales y archivos operativos. Default: `/app/storage/workspace`.
- `RETAIN_WORKSPACE_AFTER_INGEST`: conserva el clone local tras la ingesta. Si se configura en `false`, el worker elimina el workspace del repo al finalizar y `literal` queda no disponible para ese repo. Default del codigo: `false`.
- `NEO4J_URI`: URI de conexion de grafo. Default: `bolt://localhost:7687`.
- `NEO4J_USER`: usuario de Neo4j. Default: `neo4j`.
- `NEO4J_PASSWORD`: password de Neo4j. Default: `password`.
- `REDIS_URL`: URL de Redis para cola RQ. Default: `redis://localhost:6379/0`.

Notas operativas de storage:

- Arquitectura operativa principal: Chroma remoto + Postgres + Neo4j.
- Si `POSTGRES_HOST` esta vacio, el codigo aun puede caer en compatibilidad legacy con SQLite para metadata y BM25 local para lexical.
- Si `POSTGRES_HOST` esta configurado, el runtime crea y usa las tablas Postgres
  `tbl_repository_jobs`, `tbl_repository_repos` y
  `tbl_repository_lexical_corpus`; una base existente con tablas legacy
  `jobs`, `repos` y `lexical_corpus` requiere limpieza o recreacion si no se
  aplica una migracion manual.
- Si `CHROMA_MODE=embedded`, `CHROMA_PATH` vuelve a ser relevante, pero ese modo no es el default del runtime.
- En modo remoto, usa exactamente uno de estos mecanismos: `CHROMA_TOKEN` o `CHROMA_USERNAME` + `CHROMA_PASSWORD`.

### Ingesta asincrona distribuida

- `INGESTION_EXECUTION_MODE`: modo de ejecucion de ingesta (`thread` o `rq`). Default: `thread`.
- `INGESTION_QUEUE_NAME`: nombre de cola Redis/RQ. Default: `ingestion`.
- `INGESTION_JOB_TIMEOUT_SECONDS`: timeout maximo por job de ingesta. Default: `7200`.
- `INGESTION_RESULT_TTL_SECONDS`: retencion de jobs exitosos en cola. Default: `86400`.
- `INGESTION_FAILURE_TTL_SECONDS`: retencion de jobs fallidos en cola. Default: `604800`.
- `INGESTION_RETRY_MAX`: maximo de reintentos para errores transitorios. Default: `3`.
- `INGESTION_RETRY_INTERVALS`: intervalos de reintento en segundos (CSV). Default: `30,120,300`.
- `INGESTION_RETRY_TRANSIENT_ONLY`: restringe reintentos a fallas transitorias. Default: `true`.
- `INGESTION_ENQUEUE_LOCK_SECONDS`: TTL del lock distribuido por `repo_id`. Default: `30`.
- `INGESTION_ENQUEUE_LOCK_WAIT_SECONDS`: espera maxima para adquirir lock. Default: `5`.

### Git SSH para repos privados

- `GIT_SSH_KEY_CONTENT`: private key SSH en texto plano. Solo se usa para Bitbucket. Default: vacio.
- `GIT_SSH_KEY_CONTENT_B64`: private key SSH codificada en base64. Solo se usa para Bitbucket si `GIT_SSH_KEY_CONTENT` está vacío. Default: vacio.
- `GIT_SSH_KNOWN_HOSTS_CONTENT`: contenido de `known_hosts` en texto plano. Solo se usa para Bitbucket. Default: vacio.
- `GIT_SSH_KNOWN_HOSTS_CONTENT_B64`: contenido de `known_hosts` codificado en base64. Solo se usa para Bitbucket si `GIT_SSH_KNOWN_HOSTS_CONTENT` está vacío. Default: vacio.
- `GIT_SSH_STRICT_HOST_KEY_CHECKING`: política SSH (`yes`, `accept-new`, `no`). Default: `yes`.

Notas operativas SSH:

- GitHub privado mantiene autenticación por token HTTPS; estas variables SSH nuevas no alteran ese flujo.
- Para Bitbucket SSH, la precedencia es `*_CONTENT` > `*_CONTENT_B64`.
- Con `GIT_SSH_STRICT_HOST_KEY_CHECKING=yes`, debes definir `GIT_SSH_KNOWN_HOSTS_CONTENT` o `GIT_SSH_KNOWN_HOSTS_CONTENT_B64` con la huella del host Git remoto.
- Debes definir `GIT_SSH_KEY_CONTENT` o `GIT_SSH_KEY_CONTENT_B64`; no existe fallback por agent ni por variables del sistema.

Notas operativas HTTPS:

- Bitbucket Cloud y Bitbucket Server/Data Center también pueden autenticarse por request usando el bloque `auth` del endpoint de ingesta.
- En esta primera implementación, Bitbucket HTTPS usa `auth.method=http_basic` con `auth.username` y `auth.secret` explícitos.
- No se agregan variables de entorno nuevas para HTTPS porque el secreto viaja por request y se materializa solo en runtime mediante `GIT_ASKPASS` temporal.

Ejemplo recomendado para Compose o `.env` usando base64:

```dotenv
GIT_SSH_KEY_CONTENT_B64=<base64_private_key_openssh>
GIT_SSH_KNOWN_HOSTS_CONTENT_B64=<base64_known_hosts>
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

Ejemplo equivalente usando texto plano:

```dotenv
GIT_SSH_KEY_CONTENT="-----BEGIN OPENSSH PRIVATE KEY-----
...
-----END OPENSSH PRIVATE KEY-----"
GIT_SSH_KNOWN_HOSTS_CONTENT="bitbucket.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA..."
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

Recomendacion practica:

- En contenedores, CI/CD y secret managers suele ser mejor `*_B64` para evitar problemas de multilinea y escaping.
- En local manual, `*_CONTENT` puede ser suficiente si tu shell y tu archivo `.env` preservan correctamente los saltos de linea.

### Escaneo de ingesta

- `SCAN_MAX_FILE_SIZE_BYTES`: limite de bytes por archivo escaneable. Default en settings: `2000000`. `.env.example` local de este repo lo baja a `200000`.
- `SCAN_EXCLUDED_DIRS`: carpetas excluidas del escaneo. Default en settings: lista recomendada de directorios comunes de build/cache.
- `SCAN_EXCLUDED_EXTENSIONS`: extensiones binarias/no-texto excluidas. Default en settings: lista extensa de binarios y artefactos.
- `SCAN_EXCLUDED_FILES`: nombres de archivo excluidos puntualmente. Default en settings: `.gitignore,.env`.

Default usado por Compose para `SCAN_EXCLUDED_EXTENSIONS`:

`.png,.jpg,.jpeg,.gif,.webp,.ico,.mp3,.mp4,.wav,.ogg,.pdf,.zip,.tar,.gz,.7z,.rar,.jar,.war,.ear,.class,.dll,.exe,.so,.dylib,.o,.a,.bin,.sqlite,.db`

### Grafo semantico (experimental)

- `SEMANTIC_GRAPH_ENABLED`: activa extraccion semantica Python en ingesta. Default: `true`.
- `SEMANTIC_GRAPH_JAVA_ENABLED`: activa extractor semantico Java fase 1. Default: `true`.
- `SEMANTIC_GRAPH_JAVASCRIPT_ENABLED`: activa extractor semantico JavaScript fase 1. Default: `true`.
- `SEMANTIC_GRAPH_TYPESCRIPT_ENABLED`: activa extractor semantico TypeScript fase 1. Default: `true`.
- `SEMANTIC_GRAPH_FILE_EDGES_ENABLED`: persiste aristas derivadas `(:File)-[:IMPORTS_FILE]->(:File)` a partir de relaciones semanticas resueltas. Default: `true`.
- `SEMANTIC_TSCONFIG_RESOLUTION_ENABLED`: habilita resolucion de `baseUrl` y `paths` desde el primer `tsconfig.json` o `jsconfig.json` escaneado para imports JS/TS no relativos. Default: `true`.
- `SEMANTIC_GRAPH_QUERY_ENABLED`: activa expansion semantica en query. Default: `true`.
- `SEMANTIC_RELATION_TYPES`: tipos de relacion considerados en expansion semantica. Default: `CALLS,IMPORTS,EXTENDS,IMPLEMENTS`.
- `SEMANTIC_RELATION_WEIGHTS`: pesos por tipo para scoring semantico. Default: `CALLS:1.0,IMPORTS:0.7,EXTENDS:1.1,IMPLEMENTS:1.0`.
- `SEMANTIC_GRAPH_QUERY_MAX_EDGES`: tope de aristas por query semantica. Default: `400`.
- `SEMANTIC_GRAPH_QUERY_MAX_NODES`: tope de nodos por query semantica. Default: `200`.
- `SEMANTIC_GRAPH_QUERY_MAX_MS`: presupuesto extra de latencia para expansion semantica. Default: `120`.
- `SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL`: fallback automatico a expansion estructural si la semantica falla o poda todo. Default: `true`.

### Health checks y descubrimiento de modelos

- `HEALTH_CHECK_STRICT`: falla startup si un check critico no pasa. Default: `true`.
- `HEALTH_CHECK_TIMEOUT_SECONDS`: timeout por check de preflight. Default: `5`.
- `HEALTH_CHECK_TTL_SECONDS`: cache de resultados de preflight en segundos. Default: `10`.
- `HEALTH_CHECK_OPENAI`: incluye check de conectividad/model list OpenAI. Default en settings: `false`.
- `HEALTH_CHECK_REDIS`: incluye check de Redis en preflight. Default: `false`.
- Neo4j se evalua como no critico solo en `startup` (lifespan), pero se mantiene critico para contextos de operacion como `query` e `ingest`.
- `MODEL_DISCOVERY_TIMEOUT_SECONDS`: timeout de discovery de catalogo de modelos. Default: `8`.
- `MODEL_DISCOVERY_CACHE_TTL_SECONDS`: cache de discovery en segundos. Default: `3600`.
- `MODEL_DISCOVERY_MAX_RESULTS`: maximo de resultados de discovery. Default: `80`.
- `MODEL_DISCOVERY_GEMINI_SDK_ENABLED`: habilita ruta de discovery via SDK Gemini. Default: `true`.

### Inventario

- `INVENTORY_PAGE_SIZE`: tamano de pagina por defecto en `/inventory/query`. Default: `80`.
- `INVENTORY_MAX_PAGE_SIZE`: maximo permitido de pagina en inventario. Default: `300`.
- `INVENTORY_ALIAS_LIMIT`: maximo de aliases por entidad en respuesta. Default: `8`.
- `INVENTORY_ENTITY_LIMIT`: maximo de entidades devueltas por consulta de inventario. Default: `500`.
- `SYMBOL_EXTRACTOR_V2_ENABLED`: activa extractor de simbolos v2. Default: `true`.

## Ejemplo minimo recomendado (.env local)

```dotenv
LLM_PROVIDER=vertex
EMBEDDING_PROVIDER=vertex
VERTEX_AI_AUTH_MODE=service_account
VERTEX_SERVICE_ACCOUNT_JSON_B64=<base64_json_sa>
VERTEX_API_BASE_URL=https://us-central1-aiplatform.googleapis.com
CHROMA_MODE=remote
CHROMA_HOST=<chroma-host>
CHROMA_PORT=8000
# Opcion A: bearer token
CHROMA_TOKEN=<chroma-bearer-token>
# Opcion B: Basic auth (mutuamente excluyente con CHROMA_TOKEN)
# CHROMA_USERNAME=<chroma-username>
# CHROMA_PASSWORD=<chroma-password>
POSTGRES_HOST=<postgres-host>
POSTGRES_PORT=5432
POSTGRES_DB=<postgres-db>
POSTGRES_USER=<postgres-user>
POSTGRES_PASSWORD=<postgres-password>
VERTEX_AI_LABELS_ENABLED=true
VERTEX_AI_LABEL_SERVICE=webspec-coipo
VERTEX_AI_LABEL_SERVICE_ACCOUNT=qa-anthos
VERTEX_AI_LABEL_USE_CASE_ID=tbd
VERTEX_AI_CORRELATION_ID_ENABLED=true
HEALTH_CHECK_OPENAI=false
NEO4J_URI=bolt://127.0.0.1:17687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
SCAN_MAX_FILE_SIZE_BYTES=2000000
SCAN_EXCLUDED_DIRS=.git,node_modules,dist,build,.venv,__pycache__
SCAN_EXCLUDED_EXTENSIONS=.png,.jpg,.jpeg,.gif,.webp,.ico,.mp3,.mp4,.wav,.ogg,.pdf,.zip,.tar,.gz,.7z,.rar,.jar,.war,.ear,.class,.dll,.exe,.so,.dylib,.o,.a,.bin,.sqlite,.db
GIT_SSH_KEY_CONTENT_B64=<base64_private_key_openssh>
GIT_SSH_KNOWN_HOSTS_CONTENT_B64=<base64_known_hosts>
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

Si prefieres reproducir el setup local heredado versionado en `.env.example`,
puedes usar `CHROMA_MODE=embedded` y omitir `POSTGRES_HOST`, pero ese camino no
representa la arquitectura operativa principal.

Cuando uses Chroma remoto autenticado, el mismo mecanismo se aplica a ingesta,
query, health y reset, porque el runtime comparte el mismo cliente HTTP.

## Notas operativas

- Si cambias `CHROMA_HNSW_SPACE`, haz reset y reingesta.
- Si cambias provider/modelo de embedding, valida readiness con
  `GET /repos/{repo_id}/status` antes de consultar.
- Para catálogo de modelos, usa `GET /providers/models`.
- Si `SEMANTIC_GRAPH_ENABLED=true`, la ingesta agrega relaciones Python y usa
  fallback automatico al grafo estructural si falla la extraccion semantica.
- Si `SEMANTIC_GRAPH_JAVA_ENABLED=true` o
  `SEMANTIC_GRAPH_TYPESCRIPT_ENABLED=true`, se activan extractores fase 1 para
  esos lenguajes.
- Si `SEMANTIC_TSCONFIG_RESOLUTION_ENABLED=true`, los extractores JS/TS intentan
  resolver imports no relativos usando `compilerOptions.baseUrl` y
  `compilerOptions.paths` del primer `tsconfig.json` o `jsconfig.json` disponible.
- Si `SEMANTIC_GRAPH_FILE_EDGES_ENABLED=true`, la persistencia a Neo4j agrega
  aristas derivadas entre archivos a partir de relaciones `Symbol -> Symbol`
  ya resueltas, sin cambiar el contrato de expansión semántica por símbolos.
- Si `SEMANTIC_GRAPH_QUERY_ENABLED=true`, la expansion de grafo en query usa
  `SEMANTIC_RELATION_TYPES` y `SEMANTIC_RELATION_WEIGHTS` respetando budgets.
- `NEO4J_URI` cambia por entorno:
  - Local sin contenedores: `bolt://localhost:7687`.
  - Local con puerto mapeado: `bolt://127.0.0.1:17687`.
  - API dentro de Compose: `bolt://neo4j:7687`.
- En Vertex, `project_id` se deriva del service account en Base64 y `location` del host de `VERTEX_API_BASE_URL`; `VERTEX_AI_PROJECT_ID` y `VERTEX_AI_LOCATION` quedan como fallback legacy.

## Despliegue con Docker Compose completo

- `docker-compose.yml` define API + Neo4j como base, perfil `redis` para cola y
  worker, y perfil `remote` para Chroma y Postgres gestionados por Compose.
- Al activar perfil `redis`, tambien se levanta `worker` para ejecutar
  ingestas por cola Redis/RQ.
- Al activar perfil `remote`, tambien se levantan `chroma` y `postgres` para
  probar la topologia remota completa dentro del entorno local.
- API se conecta a Neo4j por DNS interno (`bolt://neo4j:7687`).
- Storage persistente de API se monta en `/app/storage`.
- Redis se activa con perfil `redis` y puede hacerse visible en preflight con
  `HEALTH_CHECK_REDIS=true`.
- El helper `scripts/start_compose.ps1` no activa hoy el perfil `remote` por
  defecto; esa diferencia debe tenerse en cuenta al seguir guias de arranque.

Variables relevantes en Compose:

- `API_IMAGE`: tag/registry de la imagen API/worker. Default: `kdb-rag-api:local`.
- `PYTHONPATH`: path de modulos dentro del contenedor API/worker. Default: `/app/src`.
- `NEO4J_USER`, `NEO4J_PASSWORD`.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: defaults del servicio Postgres de Compose. Defaults: `coderag`, `coderag`, `coderag`.
- `HEALTH_CHECK_OPENAI`, `HEALTH_CHECK_REDIS`.
- `INGESTION_EXECUTION_MODE`, `INGESTION_QUEUE_NAME`.

## Despliegue con Kubernetes

Estructura sugerida:

- `k8s/base`: API + Neo4j + PVC + configuracion comun.
- `k8s/addons/redis`: Redis opcional.
- `k8s/overlays/cloud`: base + ingress + patch de imagen API.
- `k8s/overlays/cloud-with-redis`: cloud + addon Redis.

Comportamiento de ingesta sugerido:

- `cloud`: mantener `INGESTION_EXECUTION_MODE=thread` (single replica API).
- `cloud-with-redis`: usar `INGESTION_EXECUTION_MODE=rq` y worker dedicado.

Mapeo de configuracion:

- Config no sensible en `ConfigMap` (`coderag-api-config`).
- Secrets en `Secret` (`coderag-api-secret`, `neo4j-auth`).
- Persistencia:
  - API: PVC `coderag-api-storage` montado en `/app/storage`.
  - Neo4j: `volumeClaimTemplates` en `StatefulSet`.

Antes de desplegar en cloud:

1. Cambia la imagen en `k8s/overlays/cloud/patch-api-deployment.yaml`.
2. Sustituye placeholders en secrets.
3. Ajusta host/TLS del ingress segun tu dominio.

## Referencias

- Flujos de consulta y fallback: [docs/ARCHITECTURE.md](ARCHITECTURE.md).
- Contratos API: [docs/API_REFERENCE.md](API_REFERENCE.md).
- Guia de despliegue Kubernetes: [k8s/README.md](../k8s/README.md).
- Guia Kubernetes consolidada: [KUBERNETES.md](KUBERNETES.md).
