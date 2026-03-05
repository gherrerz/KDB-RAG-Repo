# CodeRAG Studio

CodeRAG Studio es una solución de análisis de repositorios basada en Hybrid RAG
(Vector + BM25 + Grafo) para responder preguntas sobre código con evidencia
verificable (archivos y líneas).

## Tabla de Contenidos

- [Descripción General](#descripción-general)
- [Características Principales](#características-principales)
- [Arquitectura del Sistema](#arquitectura-del-sistema)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Ejemplos de Uso](#ejemplos-de-uso)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## Descripción General

El sistema permite:

1. Ingestar repositorios Git (GitHub/Bitbucket).
2. Construir índices híbridos para búsqueda semántica y exacta.
3. Construir y consultar un grafo de conocimiento del código.
4. Responder consultas en lenguaje natural con citas verificables.

Se incluye API (FastAPI), UI de escritorio (PySide6), almacenamiento vectorial
(ChromaDB), índice lexical (BM25) y base de grafo (Neo4j).

## Características Principales

- Ingesta asíncrona por job con tracking de estado y logs.
- Indexación híbrida: símbolos, archivos y módulos.
- Recuperación robusta multi-stack con inventario estructural por grafo para
   consultas tipo “todos los X”.
- Soporte de expansión GraphRAG para enriquecer contexto.
- Respuestas con citas y diagnósticos (`retrieved`, `reranked`, `graph_nodes`,
   etc.).
- Fallback seguro cuando no hay configuración de LLM.

## Arquitectura del Sistema

### Componentes

- UI: PySide6 (`ingesta`, `consulta`, `evidencias`).
- API: FastAPI (`/repos/ingest`, `/jobs/{id}`, `/query`).
- Ingesta: clonación, escaneo, chunking, embeddings, BM25, grafo.
- Retrieval: fusión vectorial + BM25 + expansión de grafo + ensamblado de
   contexto.
- LLM: OpenAI para respuesta y verificación anti-alucinación.

### Diagrama (Mermaid)

```mermaid
flowchart LR
      U[Usuario / UI PySide6] --> API[FastAPI API]

      subgraph ING[Pipeline de Ingesta]
         GIT[Git Clone + Scan]
         CHK[Chunking Símbolos/Archivos/Módulos]
         EMB[Embeddings]
         IDX1[ChromaDB]
         IDX2[BM25]
         GRF[Neo4j Graph]
         GIT --> CHK --> EMB --> IDX1
         CHK --> IDX2
         CHK --> GRF
      end

      API -->|POST /repos/ingest| ING
      API -->|GET /jobs/{id}| JOBS[(Job State)]

      subgraph QRY[Pipeline de Consulta]
         QN[Normalización]
         HYB[Hybrid Search<br/>Vector + BM25 + Modules]
         RER[Reranking]
         EXP[Graph Expand]
         ASM[Context Assembly]
         LLM[OpenAI Answer + Verify]
         QN --> HYB --> RER --> EXP --> ASM --> LLM
      end

      API -->|POST /query| QRY
      IDX1 --> HYB
      IDX2 --> HYB
      GRF --> EXP
      LLM --> API --> U
```

## Instalación

### Requisitos

- Python 3.10+
- Git
- Docker (recomendado para Neo4j/Redis)

### Pasos

1. Instalar dependencias:

    ```bash
    pip install -r requirements.txt
    ```

2. Crear archivo de entorno:

    ```bash
    copy .env.example .env
    ```

3. Levantar servicios auxiliares:

    ```bash
    docker compose up -d
    ```

## Configuración

Variables relevantes en `.env`:

- `OPENAI_API_KEY`: clave API de OpenAI.
- `OPENAI_EMBEDDING_MODEL`, `OPENAI_ANSWER_MODEL`, `OPENAI_VERIFIER_MODEL`.
- `CHROMA_PATH`: ruta persistente de Chroma.
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
- `WORKSPACE_PATH`: ruta de repos clonados.
- `MAX_CONTEXT_TOKENS`, `GRAPH_HOPS`.

> Nota: en esta configuración se recomienda `NEO4J_URI=bolt://127.0.0.1:17687`
para evitar conflictos de puertos locales comunes.

## Ejemplos de Uso

### 1) Ejecutar API

```bash
uvicorn coderag.api.server:app --reload
```

### 2) Ejecutar UI

```bash
python -m coderag.ui.main_window
```

### 3) Ingestar repositorio (PowerShell)

```powershell
$body = @{
   provider = 'github'
   repo_url = 'https://github.com/macrozheng/mall.git'
   branch = 'main'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/repos/ingest -ContentType 'application/json' -Body $body
```

### 4) Consultar

```powershell
$q = @{
   repo_id = 'dd2ca7fffe603df3'
   query = 'cuales son todos los controller del modulo mall-admin?'
   top_n = 80
   top_k = 20
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query -ContentType 'application/json' -Body $q
```

## Estructura del Proyecto

```text
coderag/
├── api/            # FastAPI, orquestación de query
├── core/           # settings, modelos, logging
├── ingestion/      # git, scanner, chunker, embedding, índices, grafo
├── jobs/           # job manager
├── llm/            # cliente OpenAI y prompts
├── parsers/        # parseadores por lenguaje
├── retrieval/      # búsqueda híbrida, reranking, context assembly
├── storage/        # metadata store
└── ui/             # aplicación PySide6
tests/              # pruebas unitarias
```

## Testing

Ejecutar pruebas:

```bash
pytest -q
```

Cobertura validada en la implementación actual:

- Ingesta y recuperación.
- Parsing de símbolos.
- Manejo de límites de batch/embeddings.
- Detección de inventarios estructurados.

## Troubleshooting

- **`OPENAI no configurado`**
   - Verifica que la clave esté en `.env` (no en `.env.example`).
   - Reinicia la API después de cambios en entorno.

- **Neo4j `Unauthorized` o `connection` error**
   - Valida `NEO4J_URI`, usuario y contraseña.
   - Verifica que el contenedor esté arriba y escuchando en el puerto esperado.

- **Ingesta tarda mucho en `Generando embeddings`**
   - Es normal en repositorios grandes.
   - Revisa logs del job en `GET /jobs/{id}`.

- **Respuestas incompletas en consultas enumerativas**
   - Usa consultas explícitas tipo “todos los X del módulo Y”.
   - Revisa `diagnostics.inventory_count` y `diagnostics.graph_nodes`.

- **Conflictos de puertos Docker**
   - Ajusta puertos host en `docker-compose.yml`.
   - Actualiza `NEO4J_URI` en `.env` acorde al puerto bolt configurado.
