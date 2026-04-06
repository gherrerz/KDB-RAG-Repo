# Configuration

Guia de configuracion de entorno y providers.

## Variables clave

### LLM

- LLM_PROVIDER: openai, anthropic, gemini, vertex_ai
- LLM_ANSWER_MODEL
- LLM_VERIFIER_MODEL
- LLM_VERIFY_ENABLED

### Embeddings

- EMBEDDING_PROVIDER: openai, anthropic, gemini, vertex_ai
- EMBEDDING_MODEL

### Chroma y retrieval

- CHROMA_PATH
- CHROMA_HNSW_SPACE: cosine o l2
- MAX_CONTEXT_TOKENS
- GRAPH_HOPS
- QUERY_MAX_SECONDS

### Storage y workspace

- NEO4J_URI
- NEO4J_USER
- NEO4J_PASSWORD
- WORKSPACE_PATH

### Escaneo de ingesta (obligatorias)

- SCAN_MAX_FILE_SIZE_BYTES
- SCAN_EXCLUDED_DIRS
- SCAN_EXCLUDED_EXTENSIONS
- SCAN_EXCLUDED_FILES (opcional)

### Grafo semántico (experimental)

- SEMANTIC_GRAPH_ENABLED: true o false (default false)
- SEMANTIC_GRAPH_JAVA_ENABLED: true o false (default false)
- SEMANTIC_GRAPH_TYPESCRIPT_ENABLED: true o false (default false)
- SEMANTIC_GRAPH_QUERY_ENABLED: true o false (default false)
- SEMANTIC_RELATION_TYPES: CSV de tipos de relación para expansión semántica
  (CALLS,IMPORTS,EXTENDS,IMPLEMENTS)
- SEMANTIC_RELATION_WEIGHTS: pesos por tipo para scoring semántico en query
  (ej. CALLS:1.0,IMPORTS:0.7,EXTENDS:1.1,IMPLEMENTS:1.0)
- SEMANTIC_GRAPH_QUERY_MAX_EDGES: presupuesto máximo de aristas por query
- SEMANTIC_GRAPH_QUERY_MAX_NODES: presupuesto máximo de nodos por query
- SEMANTIC_GRAPH_QUERY_MAX_MS: presupuesto máximo de latencia adicional en ms
- SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL: activa degradación automática
  a expansión estructural si la ruta semántica falla o poda todo

## Ejemplo minimo recomendado

```dotenv
LLM_PROVIDER=openai
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=your_key
NEO4J_URI=bolt://127.0.0.1:17687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4jpassword
SCAN_MAX_FILE_SIZE_BYTES=200000
SCAN_EXCLUDED_DIRS=.git,node_modules,dist,build,.venv,__pycache__
SCAN_EXCLUDED_EXTENSIONS=.png,.jpg,.jpeg,.gif,.pdf,.zip,.jar,.class,.dll,.exe
```

## Notas operativas

- Si cambias CHROMA_HNSW_SPACE, haz reset y reingesta.
- Si habilitas SEMANTIC_GRAPH_ENABLED, la ingesta agrega relaciones
  CALLS/IMPORTS/EXTENDS para Python con fallback automático al grafo estructural
  si falla la extracción semántica.
- Si habilitas SEMANTIC_GRAPH_JAVA_ENABLED junto con SEMANTIC_GRAPH_ENABLED,
  la ingesta agrega relaciones Java fase 1 (IMPORTS, EXTENDS/IMPLEMENTS,
  CALLS básicos).
- Si habilitas SEMANTIC_GRAPH_TYPESCRIPT_ENABLED junto con
  SEMANTIC_GRAPH_ENABLED, la ingesta agrega relaciones TypeScript fase 1
  (IMPORTS, EXTENDS/IMPLEMENTS, CALLS básicos).
- Si habilitas SEMANTIC_GRAPH_QUERY_ENABLED, la expansión de grafo en query usa
  SEMANTIC_RELATION_TYPES, aplica SEMANTIC_RELATION_WEIGHTS y respeta budgets
  (MAX_EDGES, MAX_NODES, MAX_MS).
- Si SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL=true, cuando la expansión
  semántica se queda sin nodos por budget o falla por excepción, el sistema
  degrada automáticamente a expansión estructural para no romper la respuesta.
- Si cambias provider/modelo de embedding, valida compatibilidad del repo con
  GET /repos/{repo_id}/status antes de consultar.
- Para provider catalog, usa GET /providers/models.

## Despliegue con Docker Compose completo

- `docker-compose.yml` define API + Neo4j y un perfil opcional para Redis.
- API se conecta a Neo4j por DNS interno (`bolt://neo4j:7687`).
- Storage persistente de API se monta en `/app/storage`.
- Redis se activa con perfil `redis` y requiere `HEALTH_CHECK_REDIS=true`
  cuando se quiera volver crítico en preflight.

Variables relevantes en compose:

- `API_IMAGE` (tag/registry de la imagen API)
- `NEO4J_USER`, `NEO4J_PASSWORD`
- `HEALTH_CHECK_OPENAI`, `HEALTH_CHECK_REDIS`

## Despliegue con Kubernetes

Estructura sugerida:

- `k8s/base`: API + Neo4j + PVC + configuración común.
- `k8s/addons/redis`: Redis opcional.
- `k8s/overlays/cloud`: base + ingress + patch de imagen API.
- `k8s/overlays/cloud-with-redis`: cloud + addon Redis.

Mapeo de configuración:

- Config no sensible en `ConfigMap` (`coderag-api-config`).
- Secrets en `Secret` (`coderag-api-secret`, `neo4j-auth`).
- Persistencia:
  - API: PVC `coderag-api-storage` montado en `/app/storage`.
  - Neo4j: `volumeClaimTemplates` en StatefulSet.

Antes de desplegar en cloud:

1. Cambia la imagen en `k8s/overlays/cloud/patch-api-deployment.yaml`.
2. Sustituye placeholders en secrets.
3. Ajusta host/TLS del ingress según tu dominio.

## Referencias

- Flujos de consulta y fallback: docs/ARCHITECTURE.md.
- Contratos API: docs/API_REFERENCE.md.
- Guía de despliegue Kubernetes: k8s/README.md.
