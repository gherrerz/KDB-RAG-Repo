# Architecture and Customer Journeys

Documento de referencia para entender la interaccion entre usuario, UI,
API, pipeline de ingesta, retrieval y LLM.

## Reseña de Arquitectura

KDB-RAG-Repo implementa una arquitectura de tipo cliente-servidor orientada a
RAG hibrido para repositorios de codigo. El frontend de escritorio en PySide6
actua como punto de entrada para ingesta, consulta y exploracion de evidencia.
El backend expone una API FastAPI que orquesta jobs de ingesta, valida
precondiciones de storage y ejecuta rutas de consulta con o sin LLM.

La interaccion entre servicios se divide en dos grandes rutas: ingesta y query.
En ingesta, el JobManager coordina clonacion, escaneo, extraccion de simbolos,
indexacion vectorial en Chroma remoto, indexacion lexical y persistencia
operativa en Postgres, y construccion de grafo en Neo4j. En query, la API
enruta consultas al pipeline de retrieval, combina evidencia de
Chroma/Postgres/Neo4j, arma contexto y decide entre sintesis LLM o salida
retrieval-only segun endpoint y condiciones operativas.

## Descripción general del sistema

- Frontend: aplicacion PySide6 para UX operativa de ingesta y consultas.
- Backend: FastAPI para contratos HTTP y orquestacion de flujos.
- Capa de jobs: JobManager para ejecucion asincrona, estado y logs.
- Retrieval: busqueda hibrida, reranking, expansion de grafo y ensamblado de
  contexto.
- Capa LLM: clientes multi-provider para answer/verify.
- Persistencia: Chroma remoto, Postgres para metadata y corpus lexico,
    Neo4j y workspace local opcional post-ingesta.

Notas operativas:

- Query semántico, retrieval-only e inventario pueden operar sin workspace si
    Chroma, Postgres y Neo4j estan listos.
- Modo literal sigue dependiendo de workspace local porque devuelve contenido
    vivo del archivo y no usa snapshots persistidos.
- Neo4j persiste metadata adicional por archivo, incluyendo módulo y
    `purpose_summary`, para soportar discovery e inventory explain sin leer
    archivos locales.
- SQLite, BM25 local y Chroma embedded siguen existiendo como compatibilidad
    legacy en algunas rutas del codigo, pero no representan la arquitectura
    operativa principal documentada aqui.

## Topología de despliegue

### Local con Docker Compose

```mermaid
flowchart LR
    UI[UI Desktop local] --> API[API container :8000]
    API --> CHR[Chroma remoto :8001]
    API --> PG[Postgres :5432]
    API --> NEO[Neo4j container :17687]
    API -. opcional .-> REDIS[Redis container 16379->6379]
    API --> ST[(storage volume)]
```

### Cloud con Kubernetes

```mermaid
flowchart LR
    UI[UI Desktop local] --> ING[Ingress]
    ING --> API[Deployment coderag-api]
    API --> CHR[Servicio Chroma remoto]
    API --> PG[Servicio Postgres]
    API --> NEO[StatefulSet neo4j]
    API -. opcional .-> REDIS[StatefulSet redis]
    API --> APIPVC[(PVC api storage)]
    NEO --> NEOPVC[(PVC neo4j)]
```

## Arquitectura por capas

### Vista tecnológica por capas

```mermaid
flowchart TB
    subgraph L1[Layer 1 - Experience]
        UI[PySide6<br/>Desktop UI]
    end

    subgraph L2[Layer 2 - API and Application]
        direction TB
        API[FastAPI<br/>src/coderag/api/server.py]
        QS[Query Service<br/>src/coderag/api/query_service.py]
    end

    subgraph L3[Layer 3 - Domain and Orchestration]
        direction LR

        subgraph L3L[ ]
            direction TB
            HEALTH[Storage Health<br/>src/coderag/core/storage_health.py]
            JM[JobManager<br/>src/coderag/jobs/worker.py]
        end

        subgraph L3R[ ]
            direction TB
            ING[Ingestion Pipeline<br/>src/coderag/ingestion/*]
            RET[Retrieval Pipeline<br/>src/coderag/retrieval/*]
        end
    end

    subgraph L4[Layer 4 - AI and Model Integration]
        direction TB
        EMB[Embedding Clients<br/>src/coderag/ingestion/embedding.py]
        LLM[LLM Clients<br/>src/coderag/llm/*]
    end

    subgraph L5[Layer 5 - Data and Infrastructure]
        direction LR

        subgraph L5L[Retrieval Data Plane]
            direction TB
            IDX[(Retrieval Stores)]
            CH[(Chroma Remote)]
            PGLEX[(Postgres FTS)]
            NEO[(Neo4j)]
        end

        subgraph L5R[Operational Data Plane]
            direction TB
            OPS[(Operational Stores)]
            META[(Postgres<br/>metadata)]
            WS[(Workspace<br/>local clones)]
        end
    end

    style L3L fill:transparent,stroke:transparent
    style L3R fill:transparent,stroke:transparent

    UI --> API
    API --> QS
    API --> JM
    QS --> RET
    JM --> ING
    API --> HEALTH

    ING --> EMB
    QS --> LLM

    ING --> IDX
    RET --> IDX
    JM --> OPS

    IDX --> CH
    IDX --> PGLEX
    IDX --> NEO
    OPS --> META
    OPS --> WS
```

### Notas sobre las capas

| Layer | Tecnologías de las capas en este proyecto | Responsabilidad principal |
| --- | --- | --- |
| Layer 1 - Experience | PySide6 | Interaccion con usuario para ingesta, consulta y visualizacion de evidencias. |
| Layer 2 - API and Application | FastAPI, Pydantic models, endpoints HTTP | Exponer contratos API, validar entradas y enrutar casos de uso. |
| Layer 3 - Domain and Orchestration | JobManager, pipeline de ingesta, pipeline de retrieval, chequeos de storage | Ejecutar logica de negocio y coordinar flujos asincronos/sincronos. |
| Layer 4 - AI and Model Integration | Clientes LLM multi-provider, clientes de embeddings | Generar respuestas/verificacion y convertir consultas/chunks a embeddings. |
| Layer 5 - Data and Infrastructure | Chroma remoto, Postgres, Neo4j, workspace local opcional | Persistir indices vectoriales, corpus lexico, metadata operativa y los datos requeridos por query/retrieval; el workspace queda reservado para modo literal y operaciones live-file. |

## Vista ejecutiva de journeys

```mermaid
flowchart LR
    U[Usuario] --> J1[Journey 1: Ingesta]
    U --> J2[Journey 2: Query con LLM]
    U --> J3[Journey 3: Query retrieval-only]

    J1 --> O1[Outcome: repo listo para consulta]
    J2 --> O2[Outcome: respuesta sintetizada con citas]
    J3 --> O3[Outcome: evidencia estructurada sin LLM]

    O1 --> R[Readiness check]
    R --> J2
    R --> J3
```

## Journey 1: Ingesta

### Flujo de ingesta

```mermaid
flowchart TB
    A[POST /repos/ingest] --> B[Job queued]
    B --> C[Job running]
    C --> D[Clone repo]
    D --> E[Scan files]
    E --> F[Extract symbols]
    F --> G[Index Chroma remoto]
    F --> H[Index lexical Postgres]
    F --> I[Build graph Neo4j]
    G --> J[Persist metadata operativa]
    H --> J
    I --> J
    J --> K{Readiness}
    K -->|ok| L[completed]
    K -->|warning| M[partial]
    C -->|exception| N[failed]
```

### Secuencia de ingesta

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant UI
    participant API
    participant JobManager
    participant Pipeline
    participant Storage

    User->>UI: Inicia ingesta
    UI->>API: POST /repos/ingest
    API->>JobManager: create_ingest_job
    JobManager-->>UI: job_id, status=queued

    loop Polling
        UI->>API: GET /jobs/{job_id}?logs_tail=200
        API->>JobManager: get_job(job_id)
        JobManager-->>UI: status, progress, logs
    end

    JobManager->>Pipeline: run ingest pipeline
    Pipeline->>Storage: write Chroma/Postgres/Neo4j/metadata operativa
    Pipeline-->>JobManager: completed|partial|failed
    JobManager-->>API: final state
    API-->>UI: final job info
    UI-->>User: Estado final y repo_id
```

Notas operativas de identidad de repositorio:

- `repo_id` se construye como `organizacion-repo-rama` y actúa como clave
    transversal en workspace, Postgres, Chroma y Neo4j.
- `organization` se persiste en Postgres al finalizar la ingesta; para URLs
    con jerarquías anidadas, se conserva solo el último segmento padre antes del
    nombre del repositorio.

## Journey 2: Query con LLM

### Flujo de query con LLM

```mermaid
flowchart TB
    A[POST /query] --> B[Readiness and compatibility]
    B --> C{Intent inventory?}
    C -->|yes| D[Inventory graph-first]
    C -->|no| E[Hybrid search]
    E --> F[Rerank]
    F --> G[Graph expand]
    G --> H[Assemble context]
    D --> I[Build response]
    H --> J[LLM answer]
    J --> K{Verify valid?}
    K -->|yes| I
    K -->|no| L[Extractive fallback]
    L --> I
```

### Secuencia de query con LLM

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant UI
    participant API
    participant QueryService
    participant Retrieval
    participant LLM

    User->>UI: Pregunta de negocio
    UI->>API: POST /query
    API->>QueryService: run_query
    QueryService->>Retrieval: hybrid_search + rerank + graph_expand
    Retrieval-->>QueryService: chunks + context + citations
    QueryService->>LLM: answer(context)
    LLM-->>QueryService: draft answer
    QueryService->>LLM: verify(answer, context)

    alt verify ok
        QueryService-->>API: QueryResponse(answer, citations, diagnostics)
    else verify failed or llm error
        QueryService-->>API: fallback extractivo + diagnostics
    end

    API-->>UI: respuesta final
    UI-->>User: respuesta + evidencia
```

## Journey 3: Query retrieval-only

### Flujo de query retrieval-only

```mermaid
flowchart TB
    A[POST /query/retrieval] --> B[Readiness and compatibility]
    B --> C{Intent inventory?}
    C -->|yes| D[Inventory graph-first]
    C -->|no| E[Hybrid search]
    E --> F[Rerank]
    F --> G[Graph expand]
    G --> H[Assemble context]
    D --> I[Build retrieval response]
    H --> I
    I --> J[Return chunks and citations]
```

### Secuencia de query retrieval-only

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant UI
    participant API
    participant QueryService
    participant Retrieval

    User->>UI: Pregunta tecnica
    UI->>API: POST /query/retrieval
    API->>QueryService: run_retrieval_query
    QueryService->>Retrieval: hybrid_search + rerank + graph_expand
    Retrieval-->>QueryService: chunks + citations + stats
    QueryService-->>API: RetrievalQueryResponse
    API-->>UI: evidencia estructurada
    UI-->>User: chunks, citas y diagnostics
```

## Componentes principales

- UI PySide6: captura inputs de ingesta/consulta y presenta evidencias.
- API FastAPI: valida precondiciones y expone contratos HTTP.
- JobManager: orquesta estados de ingesta y persistencia de logs.
- Retrieval pipeline: fusion vectorial + store lexico en Postgres + expansion
    de grafo.
- LLM clients: answer y verify en proveedores soportados.
- Storage: Chroma remoto, Postgres, Neo4j y workspace local.

## Referencias

- Endpoints y contratos: docs/API_REFERENCE.md
- Instalacion: docs/INSTALLATION.md
- Configuracion: docs/CONFIGURATION.md
- Troubleshooting: docs/TROUBLESHOOTING.md
