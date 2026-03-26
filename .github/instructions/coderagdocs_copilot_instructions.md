# RAG Hybrid Response Validator -- GitHub Copilot Instructions

## Prompt Maestro para Agente de IA Constructor

Este documento define las instrucciones completas para que un agente de IA construya un sistema RAG para análisis de documentos empresariales y/o espacios de Confluence (Atlassian) con Python, ChromaDB, Neo4j, modelos de LLM con OpenAI, Anthropic, Gemini de Google y Vertex AI de Google.

El sistema final debe permitir:

1. Ingestar documentos de distintos formatos y contenido de Confluence.
2. Construir un RAG híbrido (vector + BM25 + grafo).
3. Consultar el conocimiento documental mediante LLM.
4. Mostrar evidencias y relaciones semánticas trazables entre entidades.

---

# 1. Objetivo del sistema

Construir una aplicación llamada RAG Hybrid Response Validator con interfaz gráfica que permita:

### Ingesta

- Conectar con Confluence (Atlassian) y/o otras fuentes documentales.
- Descargar y normalizar documentos y páginas.
- Analizar contenido textual, tablas y metadatos.
- Generar embeddings con modelo de OpenAI o Gemini de Google o Vertex AI de Google.
- Construir un grafo de conocimiento documental y de entidades.

### Consulta

- Permitir preguntas en lenguaje natural.
- Recuperar contexto con Hybrid RAG.
- Expandir información usando GraphRAG para consultas multi-hop.
- Responder con LLMs de OpenAI o Gemini de Google o Vertex AI de Google.
- Mostrar evidencias (documento/página + sección + rango o bloque).

---

# 2. Arquitectura general

El sistema implementa:

Hybrid Retrieval + GraphRAG + Multi-hop reasoning + Verifier

Componentes principales:

UI (PySide6)  
Backend (FastAPI)  
Vector Store (ChromaDB)  
Graph Database (Neo4j)  
BM25 Index (Whoosh / rank-bm25)  
OpenAI o Gemini Responses API

---

# 3. Arquitectura de módulos Python

    coderag/
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
    │   ├── confluence_client.py
    │   ├── document_loader.py
    │   ├── repo_scanner.py
    │   ├── chunker.py
    │   ├── summarizer.py
    │   ├── embedding.py
    │   ├── index_chroma.py
    │   ├── index_bm25.py
    │   └── graph_builder.py
    │
    ├── parsers/
    │   ├── markdown_parser.py
    │   ├── html_parser.py
    │   ├── pdf_parser.py
    │   └── generic_parser.py
    │
    ├── retrieval/
    │   ├── hybrid_search.py
    │   ├── reranker.py
    │   ├── graph_expand.py
    │   └── context_assembler.py
    │
    ├── llm/
    │   ├── providerlmm_client.py
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

---

# 4. Flujo de ingesta

## Paso 1 --- Conectar fuente documental

Entradas:

- source_type (confluence, folder, blob, s3, etc.)
- source_url o base_url
- token o credenciales
- filtros (space, labels, CQL, fecha de actualización)

Acciones:

- Conectar a la fuente
- Descargar páginas/documentos
- Guardar en workspace local

---

## Paso 2 --- Escaneo de contenido

Detectar:

Documentación estructurada  
Políticas y procedimientos  
Tablas y anexos  
Diagramas e imágenes con texto  
Metadatos de autoría y actualización

Tipos detectados por formato y extensión.

---

## Paso 3 --- Chunking de documentos

Tres niveles de chunking:

### Nivel Entidad/Sección

Entidad  
Sección  
Bloque semántico

Contiene:

- título o etiqueta
- metadatos
- snippet de evidencia

### Nivel Documento

Resumen del documento o página

### Nivel Dominio/Área

Resumen de área funcional o espacio Confluence.

---

# 5. Vector Database (ChromaDB)

Colecciones:

- doc_entities
- doc_sections
- doc_documents
- docs_misc
- infra_ci

Documento indexado:

    {
     id,
     document,
     embedding,
     metadata:{
      source_id,
      path_or_url,
      content_type,
      entity_name,
      entity_type,
      section_name,
      start_ref,
      end_ref,
      version,
      updated_at
     }
    }

Embeddings generados con OpenAI o Gemini o Vertex AI.

---

# 6. Grafo de conocimiento (Neo4j)

## Nodos

Source  
Document  
Section  
Entity  
Topic  
Policy  
Procedure  
Person  
Department  
Project  
Budget  
Product

Ejemplo:

    (:Source)
    (:Document)
    (:Section)
    (:Entity)

## Relaciones

    (:Document)-[:HAS_SECTION]->(:Section)
    (:Section)-[:MENTIONS]->(:Entity)
    (:Entity)-[:RELATES_TO]->(:Entity)
    (:Person)-[:WORKS_ON]->(:Project)
    (:Project)-[:USES_BUDGET]->(:Budget)
    (:Department)-[:OWNS]->(:Procedure)
    (:Procedure)-[:DEPENDS_ON]->(:Policy)

---

# 7. Índice BM25

Se utiliza para recuperar coincidencias exactas.

Ejemplos:

- nombres de personas, áreas y proyectos
- términos regulatorios
- códigos internos y folios
- siglas y etiquetas documentales

Bibliotecas sugeridas:

rank-bm25  
Whoosh

---

# 8. Pipeline de consultas

Pipeline completo:

1. Normalización de consulta
2. Hybrid retrieval
3. Reranking
4. Expansión por grafo multi-hop
5. Compresión de contexto
6. Llamada al LLM
7. Verificación

---

# 9. Hybrid Retrieval

Combina:

Vector search  
BM25 search

Luego fusiona resultados.

top_n inicial = 60

---

# 10. Reranking

Ordenar resultados por relevancia semántica real y consistencia de evidencia.

Reducir a:

top_k = 15

---

# 11. Expansión por grafo

Usar Neo4j para recuperar:

Relaciones multi-hop  
Dependencias entre entidades  
Rutas de trazabilidad documental

Número de hops recomendado:

2 (configurable según costo y latencia)

---

# 12. Context Assembly

Construir contexto final con:

- snippets relevantes
- rutas o URLs de documento/página
- sección, bloque o referencia de evidencia
- caminos de grafo usados para inferencia multi-hop

El contexto debe incluir máximo:

~8000 tokens (configurable)

---

# 13. Uso de OpenAI

Utilizar Responses API.

Modelos configurables:

Embeddings  
Answerer  
Verifier

El modelo debe:

- responder con evidencia
- citar documentos/páginas
- usar relaciones del grafo cuando aplique
- evitar alucinaciones

---

# 14. Política anti-alucinación

Reglas obligatorias:

1. No inventar entidades ni relaciones.
2. Toda afirmación debe tener evidencia textual y/o ruta de grafo verificable.
3. Si no hay evidencia suficiente, responder: No se encontró información en las fuentes indexadas.
4. Para preguntas multi-hop, mostrar al menos una ruta entidad-relación-entidad que respalde la conclusión.

---

# 15. Interfaz gráfica

Framework recomendado:

PySide6

Ventanas:

## Ingesta

Campos:

Source Type  
Base URL o Ruta  
Token  
Filtros (space, labels, fecha)

Botón:

Ingestar

Mostrar:

Logs  
Progreso  
Estadísticas

---

## Consulta

Componentes:

Selector de fuente/dominio  
Caja de prompt  
Botón consultar

Mostrar:

Respuesta del LLM  
Tabla de evidencias  
Rutas de grafo (multi-hop)

---

# 16. API Backend (FastAPI)

Endpoints principales.

### POST /sources/ingest

### GET /jobs/{id}

### POST /query

### POST /query/retrieval

Respuesta:

    {
     answer,
     citations,
     graph_paths,
     diagnostics
    }

---

# 17. Cola de trabajos

Utilizar:

Redis + RQ

Jobs:

fetch_source  
scan_content  
parse_entities  
build_graph  
generate_embeddings

---

# 18. Estrategia incremental

Detectar cambios por version, updated_at o hash del contenido.

Reindexar solo documentos o páginas modificadas.

Actualizar únicamente subgrafos afectados por cambios.

---

# 19. Seguridad

- No guardar tokens en texto plano
- Usar variables de entorno
- Soporte para keyring
- Respetar ACL/permisos de Confluence y filtrado por autorización en consulta

---

# 20. Criterios de aceptación

El sistema está listo cuando:

- Puede ingerir contenido real de Confluence y documentos empresariales
- Construye embeddings, BM25 y grafo de conocimiento
- Responde consultas simples y multi-hop
- Muestra evidencia verificable y rutas de relación

---

# 21. Entregables

Repositorio Python completo.

Incluye:

README  
Docker Compose para Rancher desktop 
Tests con pytest

---

# Fin del documento
