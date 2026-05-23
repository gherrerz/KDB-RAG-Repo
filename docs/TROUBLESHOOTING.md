# Troubleshooting

Incidencias frecuentes y acciones sugeridas.

## API no inicia

- Verifica que el entorno virtual este activo.
- Ejecuta GET /health para confirmar estado de storage.
- Valida conectividad Neo4j y credenciales.

## Repo no listo para consultas (422)

- Consulta GET /repos/{repo_id}/status.
- Revisa query_ready, lexical_loaded, chroma_counts y embedding_compatible.
- `lexical_loaded` es la señal canonica de readiness de la capa lexica.
- Si embedding_compatible es false, reingesta con provider/modelo compatible.

## Fallback en query con LLM

- Revisa diagnostics.fallback_reason.
- Revisa diagnostics.llm_error para detalle tecnico.
- Si el problema es de credenciales o cuota, corrige provider y reintenta.

## Ingesta lenta

- Embeddings suelen ser la etapa mas costosa.
- Usa modo estable sin autoreload para ingestas largas.
- Revisa logs del job con GET /jobs/{job_id}?logs_tail=400.

## Error remoto de Chroma por payload grande, proxy reset o pod reiniciado

Sintomas tipicos:

- `No se pudo completar la operación de Chroma remoto 'upsert' ... (señal=payload_grande ...)`
- `No se pudo completar la operación de Chroma remoto 'upsert' ... (señal=proxy_reset ...)`
- `No se pudo completar la operación de Chroma remoto 'upsert' ... (señal=upstream_reiniciando ...)`

Causas probables:

- `payload_grande`: el request de escritura remota excede lo que acepta Chroma o el proxy intermedio.
- `proxy_reset`: un proxy, ingress o service mesh cerró la conexión antes de devolver respuesta útil.
- `upstream_reiniciando`: el servicio remoto estaba no disponible, reiniciando o sin upstream sano en el momento del write.

Acción recomendada:

- Si ves `señal=payload_grande`, reduce `CHROMA_REMOTE_BATCH_SIZE_OVERRIDE` de forma gradual: `1000`, luego `500`, luego `250` solo si el fallo persiste.
- Si ves `señal=proxy_reset`, revisa timeouts, resets o límites de body en proxy, ingress, Envoy o service mesh entre la API y Chroma.
- Si ves `señal=upstream_reiniciando`, revisa estado del servicio remoto, eventos del deployment y reinicios del pod de Chroma.
- Reintenta la ingesta solo después de corregir la causa operativa, especialmente si el fallo ocurrió durante `code_symbols`, que suele ser el payload más voluminoso.

Notas:

- El fallback actual cuando no hay override ni límite informado por el cliente es `5000`.
- Reducir el batch aumenta la cantidad de requests HTTP y puede hacer la ingesta algo más lenta.
- El ajuste del batch remoto no cambia la semántica de query ni debería afectar la latencia principal de búsqueda.

## Error de dimensión en Chroma durante ingesta o query

Síntoma típico:

- `Collection expecting embedding with dimension of X, got Y`

Causa:

- Se cambió el provider/modelo de embeddings y las colecciones Chroma existentes
  quedaron con una dimensionalidad anterior.

Acción recomendada:

- Ejecuta limpieza total (`POST /admin/reset` o `scripts/reset_cold.ps1`) y luego
  reingesta el repositorio con el mismo provider/modelo que usarás en query.

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

- docs/API_REFERENCE.md#formas-de-error-comunes

## Referencias

- Instalacion: docs/INSTALLATION.md
- Configuracion: docs/CONFIGURATION.md
- API detallada: docs/API_REFERENCE.md
