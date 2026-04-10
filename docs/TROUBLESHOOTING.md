# Troubleshooting

Incidencias frecuentes y acciones sugeridas.

## API no inicia

- Verifica que el entorno virtual este activo.
- Ejecuta GET /health para confirmar estado de storage.
- Valida conectividad Neo4j y credenciales.

## Repo no listo para consultas (422)

- Consulta GET /repos/{repo_id}/status.
- Revisa query_ready, chroma_counts y bm25_loaded.
- Si embedding_compatible es false, reingesta con provider/modelo compatible.

## Fallback en query con LLM

- Revisa diagnostics.fallback_reason.
- Revisa diagnostics.llm_error para detalle tecnico.
- Si el problema es de credenciales o cuota, corrige provider y reintenta.

## Ingesta lenta

- Embeddings suelen ser la etapa mas costosa.
- Usa modo estable sin autoreload para ingestas largas.
- Revisa logs del job con GET /jobs/{job_id}?logs_tail=400.

## Error de dimensión en Chroma durante ingesta

Síntoma típico:

- `Collection expecting embedding with dimension of 3072, got 768`

Causa:

- Se cambió el provider/modelo de embeddings y las colecciones Chroma existentes
	quedaron con una dimensionalidad anterior.

Acción recomendada:

- Ejecuta limpieza total (`POST /admin/reset` o `scripts/reset_cold.ps1`) y luego
	reingesta el repositorio.

## Error al instalar dependencias en Windows

Si durante `pip install -r requirements.txt` aparece un error en
`chroma-hnswlib` indicando que falta `Microsoft Visual C++ 14.0 or greater`:

- Instala Visual Studio 2022 Build Tools.
- Incluye el workload C++: `Microsoft.VisualStudio.Workload.VCTools`.
- Reintenta instalacion con `\.venv\Scripts\python -m pip install -r requirements.txt`.

## Errores de puertos o runtime de contenedores

- Si nerdctl no responde, usa scripts/compose_neo4j.ps1 para fallback.
- Ajusta puertos en docker-compose.yml y sincroniza NEO4J_URI.

## Triage rapido por error HTTP

- 404 en jobs: valida job_id y reintenta ingesta si aplica.
- 422 en query: consulta primero GET /repos/{repo_id}/status.
- 503 en ingesta/query: revisa GET /health antes de reintentar.
- 409 en delete/reset: espera fin de jobs en ejecucion.

Ver matriz completa de acciones:

- docs/API_REFERENCE.md#matriz-de-accion-recomendada

## Referencias

- Instalacion: docs/INSTALLATION.md
- Configuracion: docs/CONFIGURATION.md
- API detallada: docs/API_REFERENCE.md
