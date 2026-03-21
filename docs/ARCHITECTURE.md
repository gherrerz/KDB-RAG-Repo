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
indexacion vectorial en Chroma, indexacion lexical en BM25 y construccion de
grafo en Neo4j. En query, la API enruta consultas al pipeline de retrieval,
combina evidencia de Chroma/BM25/Neo4j, arma contexto y decide entre sintesis
LLM o salida retrieval-only segun endpoint y condiciones operativas.

## Descripción general del sistema

- Frontend: aplicacion PySide6 para UX operativa de ingesta y consultas.
- Backend: FastAPI para contratos HTTP y orquestacion de flujos.
- Capa de jobs: JobManager para ejecucion asincrona, estado y logs.
- Retrieval: busqueda hibrida, reranking, expansion de grafo y ensamblado de
  contexto.
- Capa LLM: clientes multi-provider para answer/verify.
- Persistencia: Chroma, BM25, Neo4j, SQLite y workspace local.

## Arquitectura por capas

### Vista tecnológica por capas

```mermaid
flowchart TB
    subgraph L1[Layer 1 - Experience]
        UI[PySide6 Desktop UI]
    end

    subgraph L2[Layer 2 - API and Application]
        API[FastAPI - coderag/api/server.py]
        QS[Query Service - coderag/api/query_service.py]
    end

    subgraph L3[Layer 3 - Domain and Orchestration]
        JM[JobManager - coderag/jobs/worker.py]
        ING[Ingestion Pipeline - coderag/ingestion/*]
        RET[Retrieval Pipeline - coderag/retrieval/*]
        HEALTH[Storage Health - coderag/core/storage_health.py]
    end

    subgraph L4[Layer 4 - AI and Model Integration]
        LLM[LLM Clients - coderag/llm/*]
        EMB[Embedding Clients - coderag/ingestion/embedding.py]
    end

    subgraph L5[Layer 5 - Data and Infrastructure]
        CH[(ChromaDB)]
        BM[(BM25 in-memory)]
        NEO[(Neo4j)]
        META[(SQLite metadata.db)]
        WS[(Workspace local clones)]
    end

    UI --> API
    API --> QS
    API --> JM
    QS --> RET
    JM --> ING
    API --> HEALTH

    ING --> EMB
    QS --> LLM

    ING --> CH
    ING --> BM
    ING --> NEO
    JM --> META
    JM --> WS
    RET --> CH
    RET --> BM
    RET --> NEO
```

### Notas sobre las capas

| Layer | Tecnologías de las capas en este proyecto | Responsabilidad principal |
|---|---|---|
| Layer 1 - Experience | PySide6 | Interaccion con usuario para ingesta, consulta y visualizacion de evidencias. |
| Layer 2 - API and Application | FastAPI, Pydantic models, endpoints HTTP | Exponer contratos API, validar entradas y enrutar casos de uso. |
| Layer 3 - Domain and Orchestration | JobManager, pipeline de ingesta, pipeline de retrieval, chequeos de storage | Ejecutar logica de negocio y coordinar flujos asincronos/sincronos. |
| Layer 4 - AI and Model Integration | Clientes LLM multi-provider, clientes de embeddings | Generar respuestas/verificacion y convertir consultas/chunks a embeddings. |
| Layer 5 - Data and Infrastructure | ChromaDB, BM25, Neo4j, SQLite, workspace local | Persistir indices, metadata operativa y datos necesarios para retrieval. |

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

### Flujo

```mermaid
flowchart TB
    A[POST /repos/ingest] --> B[Job queued]
    B --> C[Job running]
    C --> D[Clone repo]
    D --> E[Scan files]
    E --> F[Extract symbols]
    F --> G[Index Chroma]
    F --> H[Index BM25]
    F --> I[Build graph Neo4j]
    G --> J[Persist metadata]
    H --> J
    I --> J
    J --> K{Readiness}
    K -->|ok| L[completed]
    K -->|warning| M[partial]
    C -->|exception| N[failed]
```

### Secuencia

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
    Pipeline->>Storage: write Chroma/BM25/Neo4j/metadata
    Pipeline-->>JobManager: completed|partial|failed
    JobManager-->>API: final state
    API-->>UI: final job info
    UI-->>User: Estado final y repo_id
```

## Journey 2: Query con LLM

### Flujo

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

### Secuencia

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

### Flujo

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

### Secuencia

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
- Retrieval pipeline: fusion vectorial + BM25 + expansion de grafo.
- LLM clients: answer y verify en proveedores soportados.
- Storage: Chroma, BM25, Neo4j, SQLite metadata y workspace local.

## Referencias

- Endpoints y contratos: docs/API_REFERENCE.md
- Instalacion: docs/INSTALLATION.md
- Configuracion: docs/CONFIGURATION.md
- Troubleshooting: docs/TROUBLESHOOTING.md
