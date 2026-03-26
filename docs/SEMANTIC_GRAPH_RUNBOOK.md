# Semantic Graph Rollout Runbook

Guia operativa para habilitar, monitorear y revertir el grafo semantico de forma
progresiva.

## 1. Objetivo

Habilitar expansion semantica en consulta sin degradar estabilidad ni latencia
por encima de umbrales acordados.

## 2. Flags relevantes

- SEMANTIC_GRAPH_ENABLED
- SEMANTIC_GRAPH_JAVA_ENABLED
- SEMANTIC_GRAPH_TYPESCRIPT_ENABLED
- SEMANTIC_GRAPH_QUERY_ENABLED
- SEMANTIC_RELATION_TYPES
- SEMANTIC_RELATION_WEIGHTS
- SEMANTIC_GRAPH_QUERY_MAX_EDGES
- SEMANTIC_GRAPH_QUERY_MAX_NODES
- SEMANTIC_GRAPH_QUERY_MAX_MS
- SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL

## 3. Rollout por entorno

### Dev

1. Activar SEMANTIC_GRAPH_ENABLED y SEMANTIC_GRAPH_QUERY_ENABLED.
2. Ingerir al menos 1 repo representativo por lenguaje.
3. Verificar diagnostics en jobs y query responses.
4. Ejecutar benchmark synthetic y live corto.

Gate de salida Dev:

- tests semanticos y de query en verde.
- delta p95 synthetic <= +10%.
- llm_success_rate >= 0.95 en set de smoke queries.

### Staging

1. Mantener relation types completos o subset segun dominio.
2. Ajustar weights por ruido observado (ej. bajar IMPORTS si hay exceso de ruido).
3. Ejecutar benchmark live extendido con iteraciones >= 20.
4. Validar architecture_query_success_rate sobre set fijo.

Gate de salida Staging:

- delta p95 <= +20%.
- architecture_query_success_rate >= baseline +15% (objetivo del plan).
- semantic_noise_ratio estable y sin tendencia creciente.

### Prod

1. Habilitar por lote de repositorios/usuarios.
2. Monitorear errores y latencia por ventana de 24h.
3. Escalar cobertura gradualmente si los umbrales se mantienen.

Gate de continuidad Prod:

- sin incidentes criticos atribuibles al grafo semantico.
- p95 de /query dentro de SLO acordado.

## 4. Señales de alerta

Disparadores para pausar rollout o rollback inmediato:

- query_latency_p95_delta > +25% sostenido.
- incremento marcado de fallback_reason no esperado.
- semantic_noise_ratio alto y persistente.
- errores recurrentes en path semantico sin degradacion limpia.

## 5. Procedimiento de rollback

Rollback rapido (sin redeploy):

1. Poner SEMANTIC_GRAPH_QUERY_ENABLED=false.
2. Mantener SEMANTIC_GRAPH_QUERY_FALLBACK_TO_STRUCTURAL=true para degradacion limpia.
3. Si el problema es de ingesta, poner SEMANTIC_GRAPH_ENABLED=false.
4. Reiniciar API.
5. Validar /health/storage y smoke queries.

Validacion post-rollback:

- /query responde sin errores.
- diagnostics reflejan ruta estructural.
- p95 vuelve al rango previo aceptable.

## 6. Checklist operativo

Antes de activar:

- [ ] API health en 200.
- [ ] repo status query_ready=true para repos objetivo.
- [ ] credenciales/provider de LLM y embeddings validados.

Despues de activar:

- [ ] benchmark corto ejecutado.
- [ ] diagnostics revisados (semantic_*).
- [ ] decision de continuar/pausar documentada.

## 7. Referencias

- docs/CONFIGURATION.md
- docs/API_REFERENCE.md
- docs/TROUBLESHOOTING.md
- docs/SPRINT3_BENCHMARK.md
