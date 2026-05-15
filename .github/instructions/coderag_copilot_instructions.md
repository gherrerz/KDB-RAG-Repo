# RAG Hybrid Response Validator -- GitHub Copilot Instructions

## Prompt Maestro para Agente de IA Constructor

Este documento define las instrucciones actuales para que un agente de IA
trabaje sobre este repositorio y mantenga alineado el sistema RAG para
analisis de repositorios de codigo.

El sistema real implementado en este repo permite:

1. Ingestar repositorios Git de GitHub y Bitbucket.
2. Construir un RAG hibrido con retrieval vectorial, capa lexica y grafo.
3. Consultar el conocimiento del repositorio con o sin sintesis LLM.
4. Mostrar evidencias verificables con archivo, lineas, score y diagnosticos.

------------------------------------------------------------------------

# 1. Objetivo del sistema

Construir y mantener una aplicacion llamada RAG Hybrid Response Validator con
interfaz grafica y API HTTP que permita:

### Ingesta

- Conectar con GitHub o Bitbucket.
- Clonar repositorios por HTTPS o SSH.
- Resolver autenticacion explicita por bloque auth o secretos runtime.
- Analizar codigo fuente, configuracion, infraestructura, documentacion y tests.
- Extraer simbolos y relaciones semanticas por lenguaje cuando las flags lo
    habilitan.
- Generar embeddings con providers activos del runtime: OpenAI, Gemini o Vertex.
- Construir un grafo de conocimiento en Neo4j.
- Indexar evidencia vectorial en Chroma y evidencia lexical en Postgres o BM25
    local de compatibilidad.

### Consulta

- Permitir preguntas en lenguaje natural contra un repo ya ingerido.
- Recuperar contexto con Hybrid RAG.
- Expandir evidencia mediante grafo estructural y semantico.
- Responder con LLM o en modo retrieval-only.
- Mostrar evidencias trazables, diagnosticos y validaciones de readiness.

------------------------------------------------------------------------

# 2. Arquitectura general

El sistema implementa esta arquitectura operativa:

Hybrid Retrieval + GraphRAG + Inventory Graph-First + Optional Verifier

Componentes principales:

UI Desktop (PySide6)\
Backend HTTP (FastAPI)\
Vector Store (Chroma, remoto por defecto)\
Graph Database (Neo4j)\
Lexical Store (Postgres FTS con fallback BM25 local)\
Metadata Store (Postgres con fallback SQLite)\
LLM Clients multi-provider (OpenAI, Gemini, Vertex)\
Jobs backend (thread por defecto, Redis + RQ opcional)

Notas de alineacion con el runtime:

- Anthropic no forma parte del runtime activo bajo src aunque aparezca en tests
    legacy o residuales.
- OpenAI usa Responses API o variantes compatibles cuando aplica.
- Gemini y Vertex se consumen por REST generateContent en el runtime actual.
- Verificacion LLM existe, pero es opcional y depende de configuracion.

------------------------------------------------------------------------

# 3. Arquitectura de modulos Python

La estructura real relevante hoy es:

        src/coderag/
        |
        |- ui/
        |  |- main_window.py
        |  |- ingestion_view.py
        |  |- query_view.py
        |  |- evidence_view.py
        |  |- model_catalog_client.py
        |  |- provider_capabilities.py
        |  |- provider_ui_state.py
        |  \- query_response_formatter.py
        |
        |- api/
        |  |- server.py
        |  |- query_service.py
        |  |- query_hybrid_pipeline.py
        |  |- inventory_graph_first.py
        |  |- inventory_query_flow.py
        |  |- citation_presentation.py
        |  |- query_diagnostics.py
        |  \- literal_mode.py
        |
        |- ingestion/
        |  |- git_client.py
        |  |- repo_scanner.py
        |  |- chunker.py
        |  |- summarizer.py
        |  |- embedding.py
        |  |- index_chroma.py
        |  |- index_bm25.py
        |  |- graph_builder.py
        |  |- pipeline.py
        |  |- module_resolver.py
        |  |- semantic_python.py
        |  |- semantic_java.py
        |  |- semantic_javascript.py
        |  |- semantic_typescript.py
        |  \- extractors/
        |
        |- parsers/
        |  |- python_parser.py
        |  |- js_parser.py
        |  |- java_parser.py
        |  \- generic_parser.py
        |
        |- retrieval/
        |  |- hybrid_search.py
        |  |- reranker.py
        |  |- graph_expand.py
        |  \- context_assembler.py
        |
        |- llm/
        |  |- openai_client.py
        |  |- model_discovery.py
        |  \- prompts.py
        |
        |- core/
        |  |- models.py
        |  |- settings.py
        |  |- logging.py
        |  |- vector_index.py
        |  |- lexical_index.py
        |  |- storage_health.py
        |  \- vertex_ai.py
        |
        |- storage/
        |  |- metadata_store.py
        |  |- postgres_metadata_store.py
        |  |- lexical_store.py
        |  \- metadata_store_factory.py
        |
        |- jobs/
        |  |- worker.py
        |  |- rq_worker.py
        |  \- ingest_error_policy.py
        |
        \- maintenance/
             \- reset_service.py

Notas:

- metadata.db sigue existiendo como fallback SQLite cuando Postgres no esta
    configurado, pero ya no representa la arquitectura operativa principal.
- El backend real de consulta depende de query_service y servicios auxiliares,
    no solo de server.py.

------------------------------------------------------------------------

# 4. Flujo de ingesta

## Paso 1 --- Resolver identidad y clonar repositorio

Entradas:

- repo_url
- provider
- branch
- commit opcional
- token legacy opcional
- auth opcional con deployment, transport, method, username y secret

Acciones:

- Construir repo_id estable como organizacion-repo-rama cuando es posible.
- Clonar el repositorio en workspace local.
- Soportar GitHub HTTPS con token legacy y Bitbucket con auth explicita.
- Soportar SSH via GIT_SSH_KEY_CONTENT(_B64) y
    GIT_SSH_KNOWN_HOSTS_CONTENT(_B64).

## Paso 2 --- Detectar reingesta y purgar indices previos

Si el repo_id ya existe en indices o grafo:

- Purga Chroma por repo_id.
- Purga capa lexical activa.
- Purga subgrafo Neo4j del repositorio.

La estrategia actual es purge + reindex completo. No existe hoy un pipeline de
reindexacion incremental por archivos modificados.

## Paso 3 --- Escaneo de archivos

Detectar:

Codigo fuente\
Configuraciones\
Infraestructura\
Documentacion\
Tests

El escaneo usa filtros configurables por:

- tamano maximo de archivo
- directorios excluidos
- extensiones excluidas
- archivos excluidos

## Paso 4 --- Chunking y extraccion de simbolos

Tres niveles efectivos de indexacion:

### Nivel simbolo

Clase\
Funcion\
Metodo\
Constante o constructo equivalente cuando aplica

Contiene:

- id estable
- firma o texto relevante
- snippet
- metadata de lenguaje, archivo y rango de lineas

### Nivel archivo

- contenido resumido del archivo
- metadata del archivo

### Nivel modulo

- resumen agregado por modulo o paquete

## Paso 5 --- Relaciones semanticas opcionales

El runtime puede extraer relaciones semanticas por flags de entorno para:

- Python
- Java
- JavaScript
- TypeScript

Relaciones soportadas actualmente:

- CALLS
- IMPORTS
- EXTENDS
- IMPLEMENTS

------------------------------------------------------------------------

# 5. Vector Database

Backend operativo:

- Chroma remoto por defecto.
- Chroma embedded solo como modo alternativo de compatibilidad.

Colecciones gestionadas:

- code_symbols
- code_files
- code_modules
- docs_misc
- infra_ci

Documento indexado:

        {
            id,
            document,
            embedding,
            metadata: {
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

Embeddings:

- Se generan con el provider/modelo efectivo de la ingesta.
- Los providers activos del runtime son openai, gemini y vertex.
- La query valida compatibilidad de embeddings contra la ultima ingesta del repo.

------------------------------------------------------------------------

# 6. Grafo de conocimiento

Backend:

- Neo4j

Nodos actuales principales:

Repo\
Module\
File\
Symbol\
ExternalSymbol

Ejemplo:

        (:Repo)
        (:Module)
        (:File)
        (:Symbol)
        (:ExternalSymbol)

Relaciones actuales principales:

        (:Repo)-[:HAS_MODULE]->(:Module)
        (:Module)-[:CONTAINS]->(:File)
        (:File)-[:DECLARES]->(:Symbol)
        (:Symbol)-[:CALLS]->(:Symbol)
        (:Symbol)-[:IMPORTS]->(:Symbol|:ExternalSymbol)
        (:Symbol)-[:EXTENDS]->(:Symbol|:ExternalSymbol)
        (:Symbol)-[:IMPLEMENTS]->(:Symbol|:ExternalSymbol)
        (:File)-[:IMPORTS_FILE]->(:File)
        (:File)-[:IMPORTS_EXTERNAL_FILE]->(:ExternalSymbol)

Metadata adicional relevante en nodos File:

- purpose_summary
- purpose_source
- top_level_symbol_names
- top_level_symbol_types

Notas:

- Endpoint y Config no son hoy nodos de primer nivel persistidos por el runtime.
- El grafo actual soporta consultas estructurales, inventory graph-first y
    expansion semantica controlada por budgets.

------------------------------------------------------------------------

# 7. Capa lexical

La recuperacion exacta usa dos caminos compatibles:

- Lexical Store sobre Postgres FTS cuando Postgres esta configurado.
- BM25 local con rank-bm25 como fallback o compatibilidad legacy.

Casos de uso tipicos:

- nombres de clases
- rutas de archivo
- flags
- identificadores
- claves de configuracion

Biblioteca BM25 activa en fallback:

rank-bm25

Whoosh no forma parte de la implementacion activa actual.

------------------------------------------------------------------------

# 8. Pipeline de consultas

Pipeline general actual:

1. Normalizacion de consulta.
2. Validacion de storage y readiness del repositorio.
3. Validacion de compatibilidad de embeddings entre query e ingesta.
4. Hybrid retrieval vectorial + lexical.
5. Reranking heuristico.
6. Expansion por grafo estructural y semantico.
7. Assembly de contexto.
8. Sintesis LLM o respuesta retrieval-only segun endpoint.
9. Verificacion opcional si esta habilitada.
10. Construccion de citas y diagnosticos.

Ademas existen rutas especializadas:

- inventory graph-first
- external imports
- reverse file imports
- literal mode para archivos vivos del workspace

------------------------------------------------------------------------

# 9. Hybrid Retrieval

Combina:

Vector search\
Lexical search

Detalles importantes del runtime:

- top_n por API para query y retrieval-only: 60 por defecto.
- La UI de consulta inicia en 80 para top_n.
- El sistema puede ampliar candidate_top_n automaticamente para queries de
    identificadores exactos.
- La capa lexical activa puede ser Postgres FTS o BM25 local.

------------------------------------------------------------------------

# 10. Reranking

El reranking actual prioriza relevancia tecnica real y tipos de evidencia.

Valores por defecto del backend:

- top_k = 20 para query
- top_k = 20 para retrieval-only

El reranker considera, entre otros factores:

- coincidencias de identificadores exactos
- rutas de configuracion
- tipo de simbolo
- ruido por paths poco utiles
- senales de intencion de inventario y configuracion runtime

------------------------------------------------------------------------

# 11. Expansion por grafo

Usar Neo4j para recuperar:

Callers\
Callees\
Dependencias de archivo\
Imports externos\
Relaciones de herencia e implementacion

Numero de hops recomendado por configuracion:

- graph_hops = 2 por defecto

La expansion semantica esta controlada por:

- tipos de relacion permitidos
- maximo de aristas
- maximo de nodos
- presupuesto de latencia

------------------------------------------------------------------------

# 12. Context Assembly

Construir contexto final con:

- snippets relevantes
- rutas de archivo
- rangos de linea
- citas listas para presentacion

El limite actual configurado es:

- MAX_CONTEXT_TOKENS = 8000 por defecto

El sistema tambien puede devolver respuesta extractiva local cuando el provider
LLM no esta disponible o cuando la operacion se ejecuta en retrieval-only.

------------------------------------------------------------------------

# 13. Uso de LLMs

Providers LLM activos en runtime:

- OpenAI
- Gemini
- Vertex

Modelos configurables:

- Embeddings
- Answerer
- Verifier

Comportamiento actual:

- OpenAI usa Responses API o variantes compatibles cuando hace falta fallback.
- Gemini usa generateContent por REST.
- Vertex usa generateContent por REST con service account.
- Existe discovery remoto de catalogos de modelos con cache y fallback local.

El modelo debe:

- responder con evidencia
- citar archivos y lineas
- evitar alucinaciones
- aceptar fallback extractivo cuando el provider no esta listo

------------------------------------------------------------------------

# 14. Politica anti-alucinacion

Reglas obligatorias:

1. No inventar relaciones.
2. Toda afirmacion debe tener evidencia.
3. Si no hay evidencia suficiente, responder: "No se encontró información en el repositorio."
4. Si el LLM no esta disponible, priorizar una respuesta extractiva basada en citas.
5. Si el repositorio no esta query_ready o el embedding es incompatible, rechazar la
     consulta con error de contrato en vez de improvisar una respuesta.

------------------------------------------------------------------------

# 15. Interfaz grafica

Framework:

- PySide6

Ventanas principales:

## Ingesta

Campos actuales relevantes:

Provider\
Deployment\
Transport\
Auth Method\
Auth Username\
Embedding Provider\
Embedding Model\
Repo URL\
Auth Secret\
Branch

Acciones:

- Ingestar
- Limpiar todo
- Refrescar catalogo de modelos

Mostrar:

- logs
- progreso
- job_id
- repo_id
- estado del provider de embeddings
- hints de autenticacion y fallback

## Consulta

Componentes actuales relevantes:

Selector de repo\
Embedding Provider\
Embedding Model\
LLM Provider\
Answer Model\
Verifier Model\
Perfil de consulta\
Top-N\
Top-K\
Modo retrieval-only\
Incluir contexto\
Caja de prompt

Acciones:

- Consultar
- Actualizar IDs de repositorio
- Refrescar modelos
- Eliminar repo
- Copiar historial

Mostrar:

- respuesta sintetizada o extractiva
- historial de consulta
- citas y evidencia
- warnings de providers
- estado de embeddings y LLM

------------------------------------------------------------------------

# 16. API Backend

Backend:

- FastAPI

Endpoints principales vigentes:

### POST /repos/ingest

### GET /jobs/{id}

### POST /query

### POST /query/retrieval

### POST /inventory/query

### GET /repos

### GET /providers/models

### GET /repos/{repo_id}/status

### GET /health

### POST /admin/reset

### DELETE /repos/{repo_id}

Respuesta tipica de /query:

        {
            "answer": "...",
            "citations": [
                {
                    "path": "src/coderag/api/server.py",
                    "start_line": 1,
                    "end_line": 20,
                    "score": 0.91,
                    "reason": "hybrid_rag_match"
                }
            ],
            "diagnostics": {
                "mode": "llm",
                "verify_enabled": false
            }
        }

Contratos importantes:

- /query y /query/retrieval exigen repo query_ready.
- La API devuelve 422 cuando hay incompatibilidad de embeddings o readiness
    insuficiente.
- /repos/{repo_id}/status expone query_ready, lexical_loaded, bm25_loaded,
    graph_available y embedding_compatible.

------------------------------------------------------------------------

# 17. Cola de trabajos

Backends soportados actualmente:

- thread en proceso, por defecto
- Redis + RQ, opcional

Responsabilidades del JobManager:

- crear jobs de ingesta
- persistir estado y logs
- recuperar jobs interrumpidos
- encolar en RQ cuando la configuracion lo habilita
- impedir conflictos de ingesta por repo

Notas:

- Redis + RQ no es obligatorio para ejecutar el proyecto.
- Existe worker dedicado en jobs/rq_worker.py para modo distribuido.

------------------------------------------------------------------------

# 18. Estrategia de reingesta

Estrategia vigente:

- detectar si ya existe data del repo_id
- purgar indices y grafo del repo_id
- reindexar completamente

No documentar como capacidad actual:

- diff entre commits
- reindexacion parcial por archivos modificados

Esa capacidad puede plantearse como roadmap, pero no debe describirse como ya
implementada.

------------------------------------------------------------------------

# 19. Seguridad

Lineamientos actuales:

- No guardar tokens ni secretos en texto plano persistente.
- Usar variables de entorno para credenciales de runtime.
- Materializar secretos SSH solo en archivos temporales de ejecucion.
- Soportar autenticacion remota de Chroma por bearer token o basic auth.
- Soportar service account Base64 para Vertex.
- Validar combinaciones de credenciales incompatibles al arrancar.

No afirmar como capacidad actual:

- keyring integrado en el runtime

------------------------------------------------------------------------

# 20. Criterios de aceptacion

El sistema esta alineado con el repositorio cuando:

- Puede ingerir un repositorio real desde GitHub o Bitbucket.
- Construye embeddings, capa lexical y grafo.
- Registra readiness correcto por repositorio.
- Responde consultas por /query.
- Responde consultas por /query/retrieval sin LLM.
- Soporta consultas de inventario por /inventory/query.
- Muestra evidencia verificable y diagnosticos.
- Permite reset global y borrado por repo sin inconsistencias.

------------------------------------------------------------------------

# 21. Entregables

Repositorio Python completo.

Incluye:

README\
Docker Compose\
Tests con pytest\
Documentacion de arquitectura y API

Documentos clave ya presentes en el repo:

- README.md
- docs/ARCHITECTURE.md
- docs/API_REFERENCE.md
- docs/CONFIGURATION.md
- docs/TROUBLESHOOTING.md

------------------------------------------------------------------------

# Fin del documento

