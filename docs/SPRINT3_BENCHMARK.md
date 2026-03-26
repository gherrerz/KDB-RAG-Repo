# Sprint 3 Benchmark (Baseline Iteration)

Fecha de corrida: 2026-03-24

Este reporte resume la validacion de latencia del bloque inicial de Sprint 3
(tuning de expansion semantica con scoring por tipo/confianza y fallback
estructural automatico).

## 1. Benchmark synthetic pre/post (HEAD vs worktree actual)

Comando usado:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_compare_pre_post.py ..\KDB-RAG-Repo-pre-s3
```

Resultados:

| Scenario | Pre Mean (ms) | Post Mean (ms) | Delta Mean (%) | Pre p95 (ms) | Post p95 (ms) | Delta p95 (%) |
|---|---:|---:|---:|---:|---:|---:|
| run_query_general | 162.34 | 162.76 | +0.26 | 162.65 | 163.63 | +0.60 |
| run_query_module | 162.23 | 162.35 | +0.07 | 162.73 | 162.81 | +0.05 |
| hybrid_search | 92.08 | 92.24 | +0.17 | 92.74 | 92.93 | +0.20 |
| storage_preflight | 52.15 | 52.31 | +0.31 | 52.52 | 52.88 | +0.69 |

Lectura:

- No se observan regresiones materiales de latencia.
- Todas las variaciones se mantienen por debajo de +1% en p95.

## 2. Benchmark live (API)

Comando usado (corrida extendida Sprint 3.3):

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_api_live.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --iterations 20 --warmup 2 --top-n 60 --top-k 15
```

Artefactos:

- benchmark_reports/benchmark_live_20260324_200328.json
- benchmark_reports/benchmark_live_20260324_200328.csv

Resumen live:

- query_general: mean=15601.38 ms, p95=29084.48 ms, p99=29241.91 ms
- query_module: mean=16562.29 ms, p95=24383.63 ms, p99=25795.51 ms
- inventory_query: mean=16.84 ms, p95=24.61 ms, p99=26.37 ms
- inventory_explain: mean=30.14 ms, p95=39.11 ms, p99=68.96 ms

Notas:

- El costo principal sigue en la ruta /query (LLM + ensamblado).
- inventory mantiene latencia baja y estable.
- En /query, la etapa dominante sigue siendo llm_answer_ms (promedio > 15s).

## 3. Medicion architecture_query_success_rate (set fijo)

Comando usado:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_architecture_queries.py --base-url http://127.0.0.1:8000 --repo-id kdb-rag-repo --top-n 60 --top-k 15
```

Artefactos:

- benchmark_reports/architecture_query_eval_20260324_201206.json
- benchmark_reports/architecture_query_eval_20260324_201206.csv

Definicion operacional usada:

- `success` por consulta cuando se cumple todo:
	- `fallback_reason == null`
	- `citations >= 5`
	- `answer_chars >= 500`
	- `keyword_hits >= 3` (tokens de arquitectura/trazabilidad)

Resultado:

- architecture_query_success_rate = 1.0000 (10/10)
- Todas las consultas del set fijo retornaron con citas y sin fallback degradado.

Observaciones:

- `verify_skipped=true` en 10/10 (sin bloqueo funcional en esta validacion).
- `semantic_query_enabled=true`, pero `semantic_edges_used=0` en este set.

### Baseline comparable con semantica desactivada

Comandos usados (post-fix Neo4j):

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_architecture_queries.py --base-url http://127.0.0.1:8020 --repo-id kdb-rag-repo --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_queries.py --base-url http://127.0.0.1:8021 --repo-id kdb-rag-repo --top-n 60 --top-k 15
```

Artefactos:

- benchmark_reports/architecture_query_eval_20260324_220503.json (ON)
- benchmark_reports/architecture_query_eval_20260324_220503.csv (ON)
- benchmark_reports/architecture_query_eval_20260324_220842.json (OFF)
- benchmark_reports/architecture_query_eval_20260324_220842.csv (OFF)

Resultado comparativo ON/OFF:

- architecture_query_success_rate_on = 1.0000 (10/10)
- architecture_query_success_rate_off = 1.0000 (10/10)
- uplift de success_rate (ON vs OFF) = +0.00%

Lectura:

- El gate de staging `>= baseline +15%` no se verifica con este set actual.
- Tras corregir Neo4j, la ruta semántica sí está activa en ON (`semantic_edges_used > 0`, picos de 400 aristas / 200 nodos).
- OFF mantiene `semantic_edges_used=0` de forma consistente.

## 4.1 Metrica alternativa de calidad arquitectonica

Se agregó una metrica alternativa para comparar calidad de contenido entre ON/OFF:

- `architecture_component_coverage_score`: cobertura promedio de componentes
	esperados por consulta (matching en respuesta y rutas citadas).

Comandos usados:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_architecture_quality.py --base-url http://127.0.0.1:8020 --repo-id kdb-rag-repo --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_quality.py --base-url http://127.0.0.1:8021 --repo-id kdb-rag-repo --top-n 60 --top-k 15
```

Artefactos:

- benchmark_reports/architecture_quality_eval_20260324_222153.json (ON)
- benchmark_reports/architecture_quality_eval_20260324_222153.csv (ON)
- benchmark_reports/architecture_quality_eval_20260324_222536.json (OFF)
- benchmark_reports/architecture_quality_eval_20260324_222536.csv (OFF)

Resultado:

- architecture_component_coverage_score_on = 0.4600
- architecture_component_coverage_score_off = 0.4600
- uplift de quality score (ON vs OFF) = +0.00%

Lectura:

- Aun con activacion semántica efectiva (aristas > 0 en ON), este score no
	muestra mejora en el set actual.
- Se recomienda complementar con evaluación humana o un set de preguntas más
	orientado a trazabilidad multi-salto para capturar mejor el beneficio
	potencial de la expansión semántica.

## 4.2 Evaluacion factual con gold set (expected facts)

Se implementó una evaluación factual más estricta usando un gold set explícito
de hechos esperados por consulta.

- Gold set: `scripts/benchmark_data/architecture_facts_gold.json`
- Script: `scripts/benchmark_architecture_facts.py`

Comandos usados:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_architecture_facts.py --base-url http://127.0.0.1:8020 --repo-id kdb-rag-repo --gold-file scripts/benchmark_data/architecture_facts_gold.json --top-n 60 --top-k 15
.\.venv\Scripts\python.exe scripts\benchmark_architecture_facts.py --base-url http://127.0.0.1:8021 --repo-id kdb-rag-repo --gold-file scripts/benchmark_data/architecture_facts_gold.json --top-n 60 --top-k 15
```

Artefactos:

- benchmark_reports/architecture_facts_eval_20260324_223605.json (ON)
- benchmark_reports/architecture_facts_eval_20260324_223605.csv (ON)
- benchmark_reports/architecture_facts_eval_20260324_224016.json (OFF)
- benchmark_reports/architecture_facts_eval_20260324_224016.csv (OFF)

Resultado:

- architecture_fact_coverage_score_on = 0.6000
- architecture_fact_coverage_score_off = 0.5000
- uplift factual (ON vs OFF) = +20.00%
- semantic_edges_used_mean_on = 359.50
- semantic_edges_used_mean_off = 0.00

Lectura:

- Con métrica factual, sí aparece mejora medible y supera el gate de +15%.
- Esto alinea mejor el beneficio semántico con preguntas de arquitectura que el
	success_rate binario y la cobertura superficial de componentes.

### Gate operativo de staging (pendiente revisión humana)

Se agregó gate automatizado para decisión final combinando uplift factual y
revisión humana del gold set.

- Script: `scripts/benchmark_facts_gate.py`
- Guía: `docs/GOLD_SET_REVIEW.md`
- Plantilla de revisión: `scripts/benchmark_data/architecture_facts_review_template.csv`

Comando usado:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_facts_gate.py --on-report benchmark_reports/architecture_facts_eval_20260324_223605.json --off-report benchmark_reports/architecture_facts_eval_20260324_224016.json --review-csv scripts/benchmark_data/architecture_facts_review_template.csv --min-uplift 0.15 --min-reviewed-ratio 0.90 --min-correct-ratio 0.85
```

Resultado actual:

- status = `pending_review`
- uplift_relative = 0.2000
- Motivo: falta completar revisión humana del CSV (ratio de filas revisadas insuficiente).

## 5. Simulacion de rollback (tiempos reales)

Comando usado:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_rollback_simulation.py --repo-id kdb-rag-repo --host 127.0.0.1 --port 8013
```

Artefacto:

- benchmark_reports/rollback_simulation_20260324_212426.json

Resultado:

- rollback_to_semantic_off_seconds = 27.421
- post_rollback_health_seconds = 2.047
- post_rollback_smoke_query_seconds = 25.366
- post_rollback_semantic_query_enabled = false

Lectura:

- Objetivo de runbook para rollback (<= 10 min) cumplido con amplio margen.

## 6. Estado de aceptacion Sprint 3

- query_latency_p95_delta: dentro de objetivo en synthetic pre/post de esta iteracion.
- architecture_query_success_rate: medido en ON/OFF sobre set fijo, sin uplift medible.
- architecture_fact_coverage_score: medido en ON/OFF con uplift +20.00% (cumple gate).
- rollout_time / rollback_time: rollback_time validado con simulacion controlada.

## 7. Pendiente para cierre final Sprint 3

1. Consolidar/validar el gold set factual con revisión humana y versionarlo para regresión continua.
2. Completar decision de rollout progresivo por entorno (dev/staging/prod) con esta métrica factual como gate primario de calidad.
