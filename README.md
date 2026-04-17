# RAG Hybrid Response Validator

Plataforma de analisis de repositorios con Hybrid RAG para responder preguntas
de codigo con evidencia verificable (archivos y lineas).

## Que hace

- Ingesta repositorios Git en segundo plano con seguimiento por job.
- Construye indices complementarios: vectorial, lexico y grafo.
- Permite habilitar grafo semantico Python (CALLS, IMPORTS, EXTENDS) con
  flag de entorno y fallback seguro.
- Permite habilitar grafo semantico Java fase 1 (IMPORTS,
  EXTENDS/IMPLEMENTS, CALLS basicos) con flag dedicado.
- Permite habilitar grafo semantico TypeScript fase 1 (IMPORTS,
  EXTENDS/IMPLEMENTS, CALLS basicos) con flag dedicado.
- Permite habilitar expansion semantica en query con filtros por tipo de
  relacion y budgets de nodos/aristas/latencia.
- Responde consultas por dos rutas:
  - Query con LLM y verificacion.
  - Retrieval-only sin sintesis LLM.
- Devuelve citas y diagnosticos para trazabilidad de resultados.

## Requisitos

- Python 3.12+ recomendado (compatibilidad verificada con 3.12.3)
- Git
- Rancher Desktop con nerdctl compose o Docker Desktop con docker compose
- kubectl y Kustomize (opcional para despliegue en Kubernetes)

Nota Windows: si `pip install -r requirements.txt` falla al compilar
`chroma-hnswlib`, instala Visual Studio 2022 Build Tools con workload C++
(`Microsoft.VisualStudio.Workload.VCTools`).

## Quick Start
1. Instala dependencias y crea entorno.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Perfiles de dependencias:

- `requirements.txt`: baseline API/worker para levantar el backend.
- `requirements-runtime.txt`: alias explicito del perfil API/worker.
- `requirements-desktop.txt`: runtime + UI de escritorio.
- `requirements-full.txt`: entorno completo local con UI y tests.

Configura credenciales de Vertex AI con Service Account en `.env`:

```powershell
$env:VERTEX_AI_SERVICE_ACCOUNT_FILE = 'C:/ruta/segura/service-account.json'
```

Variables mínimas en `.env` para Vertex:

```dotenv
LLM_PROVIDER=vertex
EMBEDDING_PROVIDER=vertex
VERTEX_AI_AUTH_MODE=service_account
VERTEX_AI_SERVICE_ACCOUNT_JSON_B64=<base64_json_sa>
VERTEX_AI_PROJECT_ID=your_project
VERTEX_AI_LOCATION=us-central1
```

2. Levanta stack local con Docker Compose (API + Neo4j).

```powershell
./scripts/start_compose.ps1
```

Opcional con Redis:

```powershell
./scripts/start_compose.ps1 -WithRedis
```

Para ingesta asíncrona distribuida (API encola + worker procesa):

```powershell
$env:INGESTION_EXECUTION_MODE = 'rq'
./scripts/start_compose.ps1 -WithRedis
```

Alternativa para desarrollo local (API/UI fuera de contenedor):

```powershell
./scripts/start_stable.ps1
```

Arranque directo de API (sin scripts):

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python -m main --host 127.0.0.1 --port 8000
```

3. Inicia una ingesta.

```powershell
$body = @{
  provider = 'github'
  repo_url = 'https://github.com/macrozheng/mall.git'
  branch = 'main'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/repos/ingest -ContentType 'application/json' -Body $body
```

Para repos privados en GitHub, usa URL HTTPS y envía `token` en el request de
ingesta.

Para repos privados en Bitbucket, ahora hay dos caminos soportados:

- SSH: usa URL SSH (por ejemplo `git@bitbucket.org:workspace/proyecto.git`)
  y resuelve autenticación en runtime vía `GIT_SSH_KEY_CONTENT(_B64)` y
  `GIT_SSH_KNOWN_HOSTS_CONTENT(_B64)`.
- HTTPS: usa URL HTTPS y envía un bloque `auth` con `deployment`,
  `transport=https`, `method=http_basic`, `username` y `secret`.

Ejemplo rápido para Bitbucket Cloud o Server/Data Center vía HTTPS:

```json
{
  "provider": "bitbucket",
  "repo_url": "https://bitbucket.org/workspace/proyecto.git",
  "branch": "main",
  "auth": {
    "deployment": "cloud",
    "transport": "https",
    "method": "http_basic",
    "username": "usuario",
    "secret": "app-password-o-pat"
  }
}
```

Ejemplo rapido para `.env` o Docker Compose:

```dotenv
GIT_SSH_KEY_CONTENT_B64=<base64_private_key_openssh>
GIT_SSH_KNOWN_HOSTS_CONTENT_B64=<base64_known_hosts>
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

4. Consulta estado del job.

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/jobs/<job_id>?logs_tail=200"
```

## Kubernetes (Cloud)

Despliegue base (API + Neo4j):

```powershell
kubectl apply -k k8s/overlays/cloud
```

Despliegue con Redis opcional:

```powershell
kubectl apply -k k8s/overlays/cloud-with-redis
```

Nota: actualiza la imagen en `k8s/overlays/cloud/patch-api-deployment.yaml`
con tu registry/tag antes de aplicar en entornos gestionados.

## Customer Journeys

```mermaid
flowchart LR
    U[Usuario] --> I[Ingesta]
    U --> Q1[Query con LLM]
    U --> Q2[Query retrieval-only]

    I --> R[Repo query_ready]
    R --> Q1
    R --> Q2

    Q1 --> O1[Respuesta sintetizada + citas]
    Q2 --> O2[Evidencia estructurada + citas]
```

| Journey | Entrada | Salida | Referencia |
|---|---|---|---|
| Ingesta | POST /repos/ingest | Job con estado y logs | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Query con LLM | POST /query | Answer con citas + diagnostics | [docs/API_REFERENCE.md](docs/API_REFERENCE.md) |
| Query retrieval-only | POST /query/retrieval | Chunks + citations + stats | [docs/API_REFERENCE.md](docs/API_REFERENCE.md) |

## API Rapida

Rutas principales:

- POST /repos/ingest
- GET /jobs/{job_id}
- POST /query
- POST /query/retrieval
- POST /inventory/query
- GET /repos
- DELETE /repos/{repo_id}
- GET /repos/{repo_id}/status
- GET /providers/models
- GET /health
- POST /admin/reset

Referencia completa por journeys y contratos:

- [docs/API_REFERENCE.md](docs/API_REFERENCE.md)

## Errores HTTP frecuentes

Si recibes errores durante ingesta o consulta:

- Revisa guia de troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Revisa matriz de accion recomendada: [docs/API_REFERENCE.md#matriz-de-accion-recomendada](docs/API_REFERENCE.md#matriz-de-accion-recomendada)

Atajo de diagnostico:

- Readiness por repo: GET /repos/{repo_id}/status
- Salud de storage: GET /health

## Comandos por Journey

Consulta con LLM:

```powershell
$q = @{
  repo_id = 'mall'
  query = 'cuales son los controller del modulo mall-admin'
  top_n = 60
  top_k = 15
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query -ContentType 'application/json' -Body $q
```

Consulta retrieval-only:

```powershell
$r = @{
  repo_id = 'mall'
  query = 'donde esta la configuracion de neo4j'
  top_n = 60
  top_k = 15
  include_context = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query/retrieval -ContentType 'application/json' -Body $r
```

Eliminar repositorio indexado:

```powershell
Invoke-RestMethod -Method Delete -Uri http://127.0.0.1:8000/repos/mall
```

## Documentacion

- Instalacion: [docs/INSTALLATION.md](docs/INSTALLATION.md)
- Configuracion: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- Arquitectura y secuencias Mermaid: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- API detallada: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Runbook rollout/rollback semántico: [docs/SEMANTIC_GRAPH_RUNBOOK.md](docs/SEMANTIC_GRAPH_RUNBOOK.md)
- Guía de despliegue Kubernetes: [k8s/README.md](k8s/README.md)
- Guia Kubernetes consolidada: [docs/KUBERNETES.md](docs/KUBERNETES.md)
- Benchmark Sprint 3: [docs/SPRINT3_BENCHMARK.md](docs/SPRINT3_BENCHMARK.md)
- Extractores de simbolos: [docs/SYMBOL_EXTRACTORS.md](docs/SYMBOL_EXTRACTORS.md)
- Guia de contribucion: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)
- Migraciones: [docs/migration-guides/README.md](docs/migration-guides/README.md)
- Historial de cambios: [CHANGELOG.md](CHANGELOG.md)

## Ejemplos Ejecutables

- Python: [examples/python/](examples/python/)
- Curl: [examples/curl/](examples/curl/)
- PowerShell: [examples/powershell/](examples/powershell/)

Resultado esperado de los ejemplos:

- Ingesta: obtienes job_id y estado final completed o partial.
- Query con LLM: obtienes answer, citations y diagnostics.
- Retrieval-only: obtienes chunks, citations y statistics sin sintesis LLM.

## Validacion de Documentacion

```powershell
.\.venv\Scripts\python scripts/docs/validate_docs.py
.\.venv\Scripts\python scripts/docs/validate_links.py
.\.venv\Scripts\python scripts/docs/validate_examples.py
```

## Benchmarking

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_compare_pre_post.py ..\KDB-RAG-Repo-pre-s3
.\.venv\Scripts\python.exe scripts\benchmark_api_live.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --iterations 20 --warmup 2 --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_queries.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_quality.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_facts.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --gold-file scripts/benchmark_data/architecture_facts_gold.json --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_facts_gate.py --on-report benchmark_reports/architecture_facts_eval_20260324_223605.json --off-report benchmark_reports/architecture_facts_eval_20260324_224016.json --review-csv scripts/benchmark_data/architecture_facts_review_template.csv --min-uplift 0.15 --min-reviewed-ratio 0.90 --min-correct-ratio 0.85
.\.venv\Scripts\python.exe scripts\benchmark_rollback_simulation.py --repo-id kdb-rag-repo --host 127.0.0.1 --port 8013
```

## Testing

En Windows, usa el interprete del venv de forma explicita:

```powershell
.\.venv\Scripts\python -m pytest -q
```
