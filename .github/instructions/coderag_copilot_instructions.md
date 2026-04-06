# RAG Hybrid Response Validator -- GitHub Copilot Instructions

## Prompt Maestro para Agente de IA Constructor

Este documento define **las instrucciones completas** para que un agente
de IA construya un sistema **RAG para análisis de repositorios de
código** con Python, ChromaDB, Neo4j, modelos de LLM con OpenAI, Anthropic, Gemini de Google y Vertex AI de Google

El sistema final debe permitir: 1. **Ingestar repositorios de GitHub o
Bitbucket** 2. Construir un **RAG híbrido (vector + BM25 + grafo)** 3.
Consultar el conocimiento del repositorio mediante **LLM** 4. Mostrar
**evidencias y relaciones del código**

------------------------------------------------------------------------

# 1. Objetivo del sistema

Construir una aplicación llamada **RAG Hybrid Response Validator** con interfaz gráfica
que permita:

### Ingesta

-   Conectar con GitHub o Bitbucket
-   Clonar repositorios
-   Analizar código fuente
-   Generar embeddings con modelo de de OpenAI o Anthropic o Gemini de Google o Vertex AI de Google
-   Construir un grafo de conocimiento del código

### Consulta

-   Permitir preguntas en lenguaje natural
-   Recuperar contexto con **Hybrid RAG**
-   Expandir información usando **GraphRAG**
-   Responder con LLMs de OpenAI o Anthropic o Gemini de Google o Vertex AI de Google
-   Mostrar evidencias (archivo + líneas)

------------------------------------------------------------------------

# 2. Arquitectura general

El sistema implementa:

Hybrid Retrieval + GraphRAG + Multi-hop reasoning + Verifier

Componentes principales:

UI (PySide6)\
Backend (FastAPI)\
Vector Store (ChromaDB)\
Graph Database (Neo4j)\
BM25 Index (Whoosh / rank-bm25)\
OpenAI Responses API

------------------------------------------------------------------------

# 3. Arquitectura de módulos Python

    src/coderag/
    │
    ├── ui/
    │   ├── main_window.py
    │   ├── ingestion_view.py
    │   ├── query_view.py
    │   └── evidence_view.py
    │
    ├── api/
    │   └── server.py
    │
    ├── ingestion/
    │   ├── git_client.py
    │   ├── repo_scanner.py
    │   ├── chunker.py
    │   ├── summarizer.py
    │   ├── embedding.py
    │   ├── index_chroma.py
    │   ├── index_bm25.py
    │   └── graph_builder.py
    │
    ├── parsers/
    │   ├── python_parser.py
    │   ├── js_parser.py
    │   ├── java_parser.py
    │   └── generic_parser.py
    │
    ├── retrieval/
    │   ├── hybrid_search.py
    │   ├── reranker.py
    │   ├── graph_expand.py
    │   └── context_assembler.py
    │
    ├── llm/
    │   ├── openai_client.py
    │   └── prompts.py
    │
    ├── core/
    │   ├── models.py
    │   ├── settings.py
    │   └── logging.py
    │
    ├── storage/
    │   └── metadata.db
    │
    └── jobs/
        └── worker.py

------------------------------------------------------------------------

# 4. Flujo de ingesta

## Paso 1 --- Clonar repositorio

Entradas:

-   repo_url
-   provider
-   token
-   branch o commit

Acciones:

-   Clonar repositorio
-   Guardar en workspace local

------------------------------------------------------------------------

## Paso 2 --- Escaneo de archivos

Detectar:

Código fuente\
Configuraciones\
Infraestructura\
Documentación\
Tests

Lenguajes detectados por extensión.

------------------------------------------------------------------------

## Paso 3 --- Chunking de código

Tres niveles de chunking:

### Nivel Símbolo

Clase\
Función\
Método

Contiene:

-   firma
-   comentarios
-   snippet

### Nivel Archivo

Resumen del archivo

### Nivel Módulo

Resumen del paquete o servicio.

------------------------------------------------------------------------

# 5. Vector Database (ChromaDB)

Colecciones:

-   code_symbols
-   code_files
-   code_modules
-   docs_misc
-   infra_ci

Documento indexado:

    {
     id,
     document,
     embedding,
     metadata:{
      repo_id,
      path,
      language,
      symbol_name,
      symbol_type,
      start_line,
      end_line,
      commit,
      branch
     }
    }

Embeddings generados con OpenAI.

------------------------------------------------------------------------

# 6. Grafo de conocimiento (Neo4j)

## Nodos

Repo\
Commit\
Module\
File\
Symbol\
Endpoint\
Config

Ejemplo:

    (:Repo)
    (:Commit)
    (:File)
    (:Symbol)

## Relaciones

    (:File)-[:DECLARES]->(:Symbol)
    (:Symbol)-[:CALLS]->(:Symbol)
    (:Symbol)-[:EXTENDS]->(:Symbol)
    (:Symbol)-[:IMPLEMENTS]->(:Symbol)
    (:File)-[:IMPORTS]->(:File)
    (:Endpoint)-[:HANDLED_BY]->(:Symbol)

------------------------------------------------------------------------

# 7. Índice BM25

Se utiliza para recuperar coincidencias exactas.

Ejemplos:

-   nombres de clases
-   rutas
-   flags
-   identificadores

Bibliotecas sugeridas:

rank-bm25\
Whoosh

------------------------------------------------------------------------

# 8. Pipeline de consultas

Pipeline completo:

1.  Normalización de consulta
2.  Hybrid retrieval
3.  Reranking
4.  Expansión por grafo
5.  Compresión de contexto
6.  Llamada al LLM
7.  Verificación

------------------------------------------------------------------------

# 9. Hybrid Retrieval

Combina:

Vector search\
BM25 search

Luego fusiona resultados.

top_n inicial = 50

------------------------------------------------------------------------

# 10. Reranking

Ordenar resultados por relevancia semántica real.

Reducir a:

top_k = 10

------------------------------------------------------------------------

# 11. Expansión por grafo

Usar Neo4j para recuperar:

Callers\
Callees\
Dependencias

Número de hops recomendado:

2

------------------------------------------------------------------------

# 12. Context Assembly

Construir contexto final con:

-   snippets relevantes
-   rutas de archivo
-   rangos de línea

El contexto debe incluir máximo:

\~8000 tokens

------------------------------------------------------------------------

# 13. Uso de OpenAI

Utilizar **Responses API**.

Modelos configurables:

Embeddings\
Answerer\
Verifier

El modelo debe:

-   responder con evidencia
-   citar archivos
-   evitar alucinaciones

------------------------------------------------------------------------

# 14. Política anti-alucinación

Reglas obligatorias:

1.  No inventar relaciones.
2.  Toda afirmación debe tener evidencia.
3.  Si no hay evidencia, responder: "No se encontró información en el
    repositorio."

------------------------------------------------------------------------

# 15. Interfaz gráfica

Framework recomendado:

PySide6

Ventanas:

## Ingesta

Campos:

Provider\
Repo URL\
Token\
Branch

Botón:

Ingestar

Mostrar:

Logs\
Progreso\
Estadísticas

------------------------------------------------------------------------

## Consulta

Componentes:

Selector de repo\
Caja de prompt\
Botón consultar

Mostrar:

Respuesta del LLM\
Tabla de evidencias\
Snippets de código

------------------------------------------------------------------------

# 16. API Backend (FastAPI)

Endpoints principales.

### POST /repos/ingest

### GET /jobs/{id}

### POST /query

Respuesta:

    {
     answer,
     citations,
     diagnostics
    }

------------------------------------------------------------------------

# 17. Cola de trabajos

Utilizar:

Redis + RQ

Jobs:

clone_repo\
scan_files\
parse_symbols\
build_graph\
generate_embeddings

------------------------------------------------------------------------

# 18. Estrategia incremental

Detectar cambios entre commits.

Reindexar solo archivos modificados.

------------------------------------------------------------------------

# 19. Seguridad

-   No guardar tokens en texto plano
-   Usar variables de entorno
-   Soporte para keyring

------------------------------------------------------------------------

# 20. Criterios de aceptación

El sistema está listo cuando:

-   Puede ingerir un repositorio real
-   Construye embeddings y grafo
-   Responde consultas
-   Muestra evidencia verificable

------------------------------------------------------------------------

# 21. Entregables

Repositorio Python completo.

Incluye:

README\
Docker Compose\
Tests con pytest

------------------------------------------------------------------------

# Fin del documento

