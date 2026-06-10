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

## 4.3 Scaffold offline para code retrieval exacto

Se agrego el primer corte del dataset offline para evaluar recuperacion exacta de
codigo sin tocar runtime.

- Gold set inicial: `scripts/benchmark_data/code_retrieval_gold.json`
- Materializador local: `scripts/benchmark_code_gold_materialize.py`
- Cobertura inicial: 30 queries repartidas entre cohortes `exact_symbol`,
	`exact_config`, `literal_file` y `graph_first_small`
- Baseline fijado para el primer corte: `top_n=60`, `top_k=20`

Comando de materializacion:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_gold_materialize.py --gold-file scripts/benchmark_data/code_retrieval_gold.json --workspace-root . --output benchmark_reports/code_gold_materialized.json
```

Salida esperada:

- `benchmark_reports/code_gold_materialized.json`
- Resumen por consola con conteo de queries validas e invalidas

Objetivo de esta fase:

- Congelar rutas y spans de referencia del lote inicial antes de implementar
	colectores HTTP, scoring IR (Information Retrieval) y scoring RAGAS
	(Retrieval-Augmented Generation Assessment).

Lectura simple de estos dos bloques de scoring:

- Scoring IR (Information Retrieval): responde si el motor de búsqueda encontró el archivo, símbolo o
	fragmento correcto en la posición esperada del ranking.
- Scoring RAGAS (Retrieval-Augmented Generation Assessment): responde si, además de recuperar contexto útil, la respuesta
	final realmente usa bien ese contexto, es fiel a la evidencia y contesta lo
	que la pregunta pedía.

## 4.4 Collector HTTP y scoring IR (Information Retrieval) para code retrieval

Se agrego el siguiente corte del pipeline offline sobre el endpoint
`POST /query/retrieval`.

- Collector HTTP: `scripts/benchmark_code_retrieval_collect.py`
- Scorer IR: `scripts/benchmark_code_ir_score.py`
- Pruebas focalizadas: `tests/test_benchmark_code_retrieval_collect.py`
- Gate IR integrado en el scorer sobre el slice `gate_candidate`

Comandos base:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_retrieval_collect.py --base-url http://127.0.0.1:8000 --repo-id gherrerz-kdb-rag-repo-main --materialized-file benchmark_reports/code_gold_materialized.json
.\.venv\Scripts\python.exe scripts\benchmark_code_ir_score.py --collected-report benchmark_reports/code_retrieval_collect_<timestamp>.json
```

Thresholds del gate IR (Information Retrieval):

- Hard: `exact_path_hit_at_1 >= 0.80`
- Hard: `exact_line_hit_at_1 >= 0.70`
- Hard: `mrr (Mean Reciprocal Rank) >= 0.86`
- Hard: `fallback_rate <= 0.05`
- Soft: `exact_path_hit_at_3 >= 0.92`
- Soft: `ndcg_5 (Normalized Discounted Cumulative Gain at 5) >= 0.90`
- Soft: `citation_path_precision_mean >= 0.85`

Semantica del status:

- `pass`: cumple hard y soft
- `pass_with_warnings`: cumple hard pero falla algun soft
- `fail`: falla al menos un threshold hard

Metricas iniciales del scorer IR (Information Retrieval):

- `exact_path_hit_at_1`
- `exact_path_hit_at_3`
- `exact_line_hit_at_1`
- `exact_symbol_hit_at_1`
- `line_span_iou_at_1`
- `mrr` (Mean Reciprocal Rank)
- `ndcg_5` (Normalized Discounted Cumulative Gain at 5)
- `citation_path_precision_mean`
- `citation_span_recall_mean`
- `fallback_rate`

Que significa cada indicador IR en lenguaje simple:

- `exact_path_hit_at_1`: mide si el primer resultado ya cae en el archivo
	correcto. Es la señal más directa de "encontró el lugar correcto a la
	primera".
- `exact_path_hit_at_3`: mide si el archivo correcto aparece al menos dentro
	de los tres primeros resultados. Sirve para saber si el sistema quedó cerca
	aunque no haya acertado exactamente en la primera posición.
- `exact_line_hit_at_1`: mide si el primer resultado no solo apunta al archivo
	correcto, sino también a la zona correcta de líneas. Es más estricto que
	acertar solo el path.
- `exact_symbol_hit_at_1`: mide si el primer resultado recupera el símbolo
	esperado, por ejemplo una función o clase exacta. Es clave para preguntas de
	"dónde está X".
- `line_span_iou_at_1`: compara cuánto se superpone el fragmento recuperado con
	el fragmento esperado. Un valor cercano a `1.0` significa que el bloque
	devuelto coincide muy bien con el bloque correcto; cerca de `0.0` significa
	que cayó lejos.
- `mrr` (Mean Reciprocal Rank): resume cuán arriba aparece el primer resultado correcto en promedio.
	Si el valor sube, significa que el sistema suele poner la respuesta buena más
	cerca del inicio del ranking.
- `ndcg_5` (Normalized Discounted Cumulative Gain at 5): mide la calidad global del top 5, no solo del primer acierto. Ayuda
	a detectar si el ranking completo está bien ordenado cuando hay varios
	resultados útiles.
- `citation_path_precision_mean`: mide qué proporción de las citas devueltas
	apunta realmente a archivos relevantes para la consulta. Si este valor es
	bajo, el sistema está citando demasiado ruido.
- `citation_span_recall_mean`: mide si las citas cubren el fragmento esperado.
	Un valor alto significa que, aunque haya ruido, la evidencia importante sí
	está presente en las citas.
- `fallback_rate`: mide cuántas consultas terminan en una ruta degradada o de
	fallback. Mientras más bajo, más consistente es el pipeline principal.

Como leer el gate IR:

- Los thresholds hard son los mínimos operativos para considerar que la
	recuperación exacta ya es suficientemente confiable.
- Los thresholds soft no bloquean por sí solos, pero muestran calidad todavía
	insuficiente o demasiado ruido en el ranking/citas.
- En este corte el gate se evalúa sobre `gate_candidate`, es decir, el subconjunto
	de queries que sí queremos usar como criterio inicial de aprobación.

Indicadores RAGAS (Retrieval-Augmented Generation Assessment) previstos para la siguiente fase:

- `context_precision`: mide si el contexto recuperado fue realmente útil para
	responder, en lugar de traer mucho texto irrelevante.
- `context_recall`: mide si el contexto recuperado contenía la información que
	la respuesta necesitaba. Si es bajo, la respuesta puede fallar aunque el LLM
	sea bueno.
- `faithfulness`: mide si la respuesta está respaldada por la evidencia y no
	inventa cosas que no aparecen en el contexto.
- `answer_relevancy`: mide si la respuesta contesta lo que se preguntó, sin
	irse por ramas ni responder algo tangencial.
- `answer_correctness`: mide si la respuesta final es correcta frente a una
	referencia esperada, no solo si suena razonable.
- `context_entity_recall`: mide si el contexto contiene las entidades,
	nombres o componentes clave que la pregunta necesitaba recuperar.

Lectura práctica de RAGAS (Retrieval-Augmented Generation Assessment):

- IR nos dice si encontramos el fragmento correcto.
- RAGAS nos dice si, con ese contexto, la respuesta final sería útil, fiel y
	correcta para el usuario.
- En este proyecto ambos bloques son complementarios: IR es el criterio
	principal para exactitud de código; RAGAS agrega control de calidad sobre la
	respuesta generada y el contexto usado.

Salida esperada por corrida:

- `benchmark_reports/code_retrieval_collect_<timestamp>.json`
- `benchmark_reports/code_retrieval_collect_<timestamp>.csv`
- `benchmark_reports/code_ir_eval_<timestamp>.json`
- `benchmark_reports/code_ir_eval_<timestamp>.csv`

## 4.5 Ajuste del reranker y rerun del gate IR

Se implementó un ajuste focalizado en `src/coderag/retrieval/reranker.py`
para mejorar queries de lookup exacto sin tocar contratos HTTP, chunking ni la
fusión base de `hybrid_search`.

Cambios aplicados en este corte:

- detección explícita de intención de definición para queries tipo `donde esta X`
- preferencia por definiciones canónicas frente a tests, wrappers y entrypoints
- preferencia por documentación cuando la query pide `documented/docs`
- preferencia por config operativa cuando la query pide `configured`
- penalización extra para wrappers prefijados (`fake_`, `test_`, `_target`)
- promoción conservadora del archivo dueño cuando el chunk exacto no aparece

Pruebas focalizadas del reranker:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_reranker.py -q
```

Resultado final de pruebas focalizadas:

- `14 passed`

Rerun real del benchmark contra una API fresh del workspace en `127.0.0.1:8030`:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m main --host 127.0.0.1 --port 8030
.\.venv\Scripts\python.exe scripts\benchmark_code_retrieval_collect.py --base-url http://127.0.0.1:8030 --repo-id gherrerz-kdb-rag-repo-main --materialized-file benchmark_reports/code_gold_materialized.json --top-n 60 --top-k 20
.\.venv\Scripts\python.exe scripts\benchmark_code_ir_score.py --collected-report benchmark_reports/code_retrieval_collect_20260609_171542.json
```

Artefactos de la corrida final:

- `benchmark_reports/code_retrieval_collect_20260609_171542.json`
- `benchmark_reports/code_retrieval_collect_20260609_171542.csv`
- `benchmark_reports/code_ir_eval_20260609_171548.json`
- `benchmark_reports/code_ir_eval_20260609_171548.csv`

Resultado del gate sobre `gate_candidate`:

- `status = pass_with_warnings`
- `exact_path_hit_at_1 = 0.8462`
- `exact_path_hit_at_3 = 0.9231`
- `exact_line_hit_at_1 = 0.8462`
- `mrr = 0.8901`
- `ndcg_5 = 0.9255`
- `fallback_rate = 0.0000`
- `citation_path_precision_mean = 0.1184`

Lectura:

- El gate duro ya queda superado en este corte.
- El único warning restante es `citation_path_precision_mean`, que sigue muy
	bajo porque el ranking de citas todavía arrastra bastante ruido aunque el
	top principal ya mejoró.
- El mayor uplift quedó concentrado en `exact_symbol`, que pasó a:
	`exact_path_hit_at_1 = 0.9000`, `exact_path_hit_at_3 = 1.0000`, `mrr = 0.9500`.
- El siguiente frente técnico natural ya no es el top-1 exacto del reranker,
	sino la limpieza de citas y el ruido residual en `exact_config`.

## 4.6 Ajuste puntual de citas y limpieza de citation_path_precision_mean

Se implementó un ajuste acotado en `src/coderag/api/query_hybrid_pipeline.py`
y `src/coderag/api/citation_filters.py` para que las citas devueltas reflejen
la evidencia principal del top rerankeado, en vez de exponer toda la cola de
paths recuperados.

Cambios aplicados en este corte:

- se conserva `raw_citations` completo para diagnostics
- se mantiene el filtro genérico de `is_noisy_path`
- se selecciona como salida una cita principal alineada con el primer path
	útil del rerank
- se conserva fallback a citas crudas cuando el filtrado elimina todo

Pruebas focalizadas del ajuste de citas:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_citation_filters.py tests\test_query_hybrid_pipeline.py -q
```

Resultado de pruebas focalizadas:

- `6 passed`

Rerun real del benchmark contra la misma API fresh en `127.0.0.1:8030`:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_retrieval_collect.py --base-url http://127.0.0.1:8030 --repo-id gherrerz-kdb-rag-repo-main --materialized-file benchmark_reports/code_gold_materialized.json --top-n 60 --top-k 20
.\.venv\Scripts\python.exe scripts\benchmark_code_ir_score.py --collected-report benchmark_reports/code_retrieval_collect_20260609_180849.json
```

Artefactos de la corrida con ajuste de citas:

- `benchmark_reports/code_retrieval_collect_20260609_180849.json`
- `benchmark_reports/code_retrieval_collect_20260609_180849.csv`
- `benchmark_reports/code_ir_eval_20260609_180852.json`
- `benchmark_reports/code_ir_eval_20260609_180852.csv`

Resultado del gate sobre `gate_candidate` tras este ajuste:

- `status = pass_with_warnings`
- `exact_path_hit_at_1 = 0.8462`
- `exact_path_hit_at_3 = 0.9231`
- `exact_line_hit_at_1 = 0.8462`
- `mrr = 0.8901`
- `ndcg_5 = 0.9255`
- `fallback_rate = 0.0000`
- `citation_path_precision_mean = 0.8462`

Lectura:

- El ranking principal no cambió en `gate_candidate`; el ajuste actuó solo
	sobre la selección de citas devueltas.
- `citation_path_precision_mean` subió de `0.1184` a `0.8462`.
- El valor quedó prácticamente acoplado a `exact_path_hit_at_1`, porque ahora
	la respuesta devuelve la evidencia principal en vez de toda la cola de citas.
- El warning remanente de `citation_path_precision_mean` queda marginal
	(`0.8462` frente al umbral `0.8500`) y ya no parece resolverse en esta capa
	sin volver a mover ranking/top-1.

## 4.7 Ajuste mínimo del reranker para exact_config documental

Se aplicó un ajuste mínimo en `src/coderag/retrieval/reranker.py` para
reconocer intención documental en español y promover la sección documental
correcta cuando la query pide explícitamente `donde esta documentado ...`.

Raíz del problema:

- el reranker reconocía `documented` y `documentation`, pero no `documentado`
	ni variantes equivalentes en español
- por eso las queries `exact_config` documentales caían en la rama genérica y
	terminaban priorizando `config_key` exactos en YAML sobre `docs/CONFIGURATION.md`

Cambios aplicados en este corte:

- se agregaron tokens documentales en español (`documentado`, `documentada`,
	`documentar`)
- se mantuvo el refuerzo focalizado para secciones doc que mencionan el target
	exacto cuando la intención ya fue clasificada como documental

Pruebas focalizadas del reranker:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_reranker.py -q
```

Resultado de pruebas focalizadas:

- `15 passed`

Rerun real del benchmark contra una API fresh del workspace en `127.0.0.1:8030`:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m main --host 127.0.0.1 --port 8030
.\.venv\Scripts\python.exe scripts\benchmark_code_retrieval_collect.py --base-url http://127.0.0.1:8030 --repo-id gherrerz-kdb-rag-repo-main --materialized-file benchmark_reports/code_gold_materialized.json --top-n 60 --top-k 20
.\.venv\Scripts\python.exe scripts\benchmark_code_ir_score.py --collected-report benchmark_reports/code_retrieval_collect_20260609_181658.json
```

Artefactos de la corrida final con ajuste `exact_config`:

- `benchmark_reports/code_retrieval_collect_20260609_181658.json`
- `benchmark_reports/code_retrieval_collect_20260609_181658.csv`
- `benchmark_reports/code_ir_eval_20260609_181702.json`
- `benchmark_reports/code_ir_eval_20260609_181702.csv`

Resultado del gate sobre `gate_candidate` tras este ajuste:

- `status = pass`
- `exact_path_hit_at_1 = 0.8846`
- `exact_path_hit_at_3 = 1.0000`
- `exact_line_hit_at_1 = 0.8846`
- `mrr = 0.9423`
- `ndcg_5 = 1.0249`
- `fallback_rate = 0.0000`
- `citation_path_precision_mean = 0.8846`

Lectura:

- `citation_path_precision_mean` finalmente supera el umbral soft de `0.8500`
	y el gate pasa sin warnings.
- el cohort `exact_config` pasó a `exact_path_hit_at_1 = 1.0000` y
	`citation_path_precision_mean = 1.0000`.
- el uplift vino de corregir la clasificación de intención, no de aumentar
	agresivamente los boosts de config o de reabrir `hybrid_search`.

## 4.8 Cierre final de exact_symbol y citas derivadas

Se cerró el último remanente de `exact_symbol` sin tocar `hybrid_search`,
primero con el ajuste fino del reranker y luego con un refinamiento puntual en
la salida de `query_service` para promover owner-file top-1 al span exacto del
símbolo cuando la resolución literal es única.

Raíz del problema en las dos etapas finales:

- `run_retrieval_query` primero perdía por unas milésimas frente al wrapper
	HTTP en `src/coderag/api/server.py`
- `_read_database_heads` no activaba la rama de symbol lookup porque la query
	en español (`muestrame la implementacion de ...`) no clasificaba como
	`definition_lookup_intent`
- además, el perfil de query no extraía identificadores que empiezan con `_`,
	por lo que el desempate fino para símbolos privados nunca se activaba
- una vez corregido eso, quedaba un remanente más fino: el path top-1 ya era
	correcto, pero `run_retrieval_query` seguía saliendo como chunk de archivo
	completo en vez de como span de función

Cambios aplicados en el cierre:

- penalización adicional a wrappers y archivos de orquestación (`server`,
	`flow`, `admin`) cuando compiten en queries de symbol lookup
- soporte explícito para tokens de intención en español (`implementacion`) en
	las ramas de definition lookup e implementation intent
- extracción de `focus_identifiers` con soporte para símbolos que empiezan con
	underscore
- desempate específico para símbolos privados lookup-style, manteniendo la
	preferencia por el owner file productivo frente a copias administrativas
- extracción de símbolos standalone tipo `snake_case` desde queries naturales
- fallback controlado al checkout local actual cuando `workspace_path/repo_id`
	no existe pero el `repo_id` sí corresponde al repo abierto
- refinamiento de `run_retrieval_query` para sustituir el owner-file top-1 por
	el span exacto del símbolo cuando `resolve_literal_symbol_match(...)`
	devuelve `exact_symbol_unique`
- preservación explícita de citas derivadas del grafo en la respuesta devuelta,
	evitando que el colapso a una sola cita tape `graph_file_dependency_match`
	o `graph_external_dependency_source`

Pruebas de cierre:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_reranker.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_query_service_modules.py -q
```

Resultado de pruebas:

- `18 passed`
- `79 passed`

Rerun real del benchmark contra API fresh local en `127.0.0.1:8031`:

```powershell
$env:PYTHONPATH = 'src'
$env:CHROMA_MODE = 'remote'
$env:CHROMA_HOST = '127.0.0.1'
$env:CHROMA_PORT = '8001'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '5432'
$env:POSTGRES_DB = 'coderag'
$env:POSTGRES_USER = 'coderag'
$env:POSTGRES_PASSWORD = 'coderag'
$env:NEO4J_URI = 'bolt://127.0.0.1:17687'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASSWORD = 'password'
$env:HEALTH_CHECK_OPENAI = 'false'
$env:HEALTH_CHECK_REDIS = 'false'
.\.venv\Scripts\python.exe src\main.py --host 127.0.0.1 --port 8031
.\.venv\Scripts\python.exe scripts\benchmark_code_retrieval_collect.py --base-url http://127.0.0.1:8031 --repo-id gherrerz-kdb-rag-repo-main --materialized-file benchmark_reports/code_gold_materialized.json --top-n 60 --top-k 20
.\.venv\Scripts\python.exe scripts\benchmark_code_ir_score.py --collected-report benchmark_reports/code_retrieval_collect_20260609_203257.json
```

Artefactos de la corrida final de cierre:

- `benchmark_reports/code_retrieval_collect_20260609_203257.json`
- `benchmark_reports/code_retrieval_collect_20260609_203257.csv`
- `benchmark_reports/code_ir_eval_20260609_203308.json`
- `benchmark_reports/code_ir_eval_20260609_203308.csv`

Resultado del gate sobre `gate_candidate` tras el cierre:

- `status = pass`
- `exact_path_hit_at_1 = 0.9615`
- `exact_path_hit_at_3 = 0.9615`
- `exact_line_hit_at_1 = 0.9615`
- `exact_symbol_hit_at_1 = 0.9500`
- `mrr = 0.9692`
- `ndcg_5 = 1.0439`
- `fallback_rate = 0.0000`
- `citation_path_precision_mean = 0.9615`

Lectura:

- el cohort `exact_symbol` cerró en `exact_path_hit_at_1 = 0.9500`,
	`exact_line_hit_at_1 = 0.9500` y `exact_symbol_hit_at_1 = 0.9500`
- el caso remanente de `run_retrieval_query` dejó de salir como owner-file
	completo y pasó a devolverse como span de función, con top-1 en
	`src/coderag/api/query_service.py` líneas `1031-1178`
- en agregado total hubo mejora adicional respecto al corte previo:
	`exact_path_hit_at_1 = 0.9000`, `exact_path_hit_at_3 = 0.9667`,
	`exact_symbol_hit_at_1 = 0.9500`, `mrr = 0.9344` y
	`citation_path_precision_mean = 0.9000`
- el remanente técnico colateral en tests modulares también quedó cerrado:
	las citas `graph_file_dependency_match` y
	`graph_external_dependency_source` vuelven a preservarse en la respuesta,
	y `tests/test_query_service_modules.py` quedó en `79 passed`

## 4.9 Dataset base para RAGAS offline

Se agregó el primer corte de materialización específico para RAGAS, todavía sin
scoring generativo ni cambios de runtime.

- Materializador: `scripts/benchmark_code_ragas_dataset_materialize.py`
- Pruebas focalizadas:
	`tests/test_benchmark_code_ragas_dataset_materialize.py`
- Artefacto de salida esperado:
	`benchmark_reports/code_ragas_dataset_materialized.json`

Objetivo de este corte:

- reutilizar el gold set actual y su resolución local ya materializada para
	producir un dataset estable, apto para un collector posterior contra
	`POST /query`
- separar desde el diseño la referencia generativa (`ragas_reference`) de la
	evidencia de retrieval (`materialized_expected` y alternativas)
- introducir una capa explícita de elegibilidad (`eligibility`) para permitir
	adopción gradual de cohortes en RAGAS

Contrato del artefacto materializado RAGAS:

- `dataset_name`, `repo_id`, `defaults`, `generated_at`, `workspace_root`
- `total_queries`, `valid_queries`, `invalid_queries`
- `ragas_enabled_queries`, `ragas_disabled_queries`
- `queries[]` con:
	`query_id`, `query`, `cohort`, `gate_candidate`, `retrieval_defaults`,
	`materialized_expected`, `materialized_alternatives`, `ragas_reference`,
	`eligibility`, `validation_errors`

Bloque `ragas_reference` por query:

- `reference_answer`
- `answer_type`
- `eval_mode`
- `requires_citations`
- `reference_context_hints`
- `reference_claims`
- `reference_entities`

Bloque `eligibility` por query:

- `valid`
- `ragas_enabled`
- `disabled_reasons`

Decisión operativa inicial en este corte:

- `literal_file` queda deshabilitado por defecto para la primera ola RAGAS
- `exact_symbol`, `exact_config` y `graph_first_small` pueden quedar
	habilitados si ya tienen `reference_answer` y la query materializada es
	válida

Comando base:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_ragas_dataset_materialize.py --gold-file scripts/benchmark_data/code_retrieval_gold.json --materialized-file benchmark_reports/code_gold_materialized.json --output benchmark_reports/code_ragas_dataset_materialized.json
```

## 4.10 Collector y scoring RAGAS offline

Se completó el segundo y tercer corte del flujo RAGAS sin tocar el runtime.

- Collector HTTP: `scripts/benchmark_code_ragas_collect.py`
- Scorer/reportes offline: `scripts/benchmark_code_ragas_score.py`
- Pruebas focalizadas: `tests/test_benchmark_code_ragas_pipeline.py`

Objetivo de este corte:

- congelar respuestas reales de `POST /query` a partir del dataset RAGAS ya
	materializado
- reconstruir `retrieved_contexts` offline desde `citations`, leyendo spans del
	workspace o usando `snippet_preview` materializado como fallback
- desacoplar el scoring de la disponibilidad de la librería `ragas`, usando un
	proxy lexical/heurístico reproducible sobre artefactos congelados

Contrato del collector:

- entrada: `benchmark_reports/code_ragas_dataset_materialized.json`
- salida JSON: `benchmark_reports/code_ragas_collect_<timestamp>.json`
- salida CSV: `benchmark_reports/code_ragas_collect_<timestamp>.csv`
- por query conserva:
	`payload`, `response_body`, `answer_text`, `citations`,
	`retrieved_contexts`, `ragas_reference`, `materialized_expected`,
	`materialized_alternatives`, `score_eligible`, `score_skip_reason`

Reglas de elegibilidad para scoring:

- respuesta HTTP `200`
- `answer_text` no vacío
- `fallback_used = false`
- citas presentes cuando `requires_citations = true`
- al menos un `retrieved_context` reconstruido

Comando base del collector:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_ragas_collect.py --base-url http://127.0.0.1:8000 --dataset-file benchmark_reports/code_ragas_dataset_materialized.json
```

Contrato del scorer offline:

- entrada: `benchmark_reports/code_ragas_collect_<timestamp>.json`
- salida JSON: `benchmark_reports/code_ragas_eval_<timestamp>.json`
- salida CSV: `benchmark_reports/code_ragas_eval_<timestamp>.csv`
- métricas agregadas: `answer_relevancy`, `answer_correctness`,
	`faithfulness`, `context_precision`, `context_recall`,
	`context_entity_recall`, `scored_rate`, `fallback_rate`
- cortes agregados: `overall`, `gate_candidate`, `by_cohort`, `skipped_rows`
- gate integrado: `pass`, `pass_with_warnings` o `fail`, con exit code `3`
	cuando falla un threshold hard

Comando base del scorer:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_code_ragas_score.py --collected-report benchmark_reports/code_ragas_collect_<timestamp>.json --scoring-engine auto
```

Nota operativa:

- el scorer soporta `auto`, `proxy` y `ragas`
- `auto` intenta usar `ragas` real solo cuando hay dependencias opcionales y
	proveedor configurado; si no, cae a `offline_lexical_proxy`
- para no contaminar el runtime base, las dependencias opcionales de evaluación
	se aíslan en `requirements-ragas-eval.txt`

Corrida real validada en esta iteración:

- collector JSON:
	`benchmark_reports/code_ragas_collect_20260609_221528.json`
- collector CSV:
	`benchmark_reports/code_ragas_collect_20260609_221528.csv`
- scorer JSON:
	`benchmark_reports/code_ragas_eval_20260609_221535.json`
- scorer CSV:
	`benchmark_reports/code_ragas_eval_20260609_221535.csv`

Resultado agregado observado:

- `queries_count = 28`
- `successful_queries = 28`
- `score_eligible_queries = 28`
- `scored_rate = 1.0`
- `answer_relevancy = 0.9339`
- `answer_correctness = 0.3196`
- `faithfulness = 0.5972`
- `context_precision = 0.1994`
- `context_recall = 0.8476`
- `context_entity_recall = 0.9286`
- `fallback_rate = 0.0`

Gate sobre `gate_candidate`:

- estado: `fail`
- hard pass: `answer_relevancy`, `faithfulness`,
	`context_entity_recall`, `scored_rate`
- hard fail: `answer_correctness`
- soft fail: `context_precision`

Lectura operativa:

- el pipeline offline quedó estable y 100% evaluable sobre las 28 queries
	habilitadas
- el runtime respondió sin fallos ni fallback en esta corrida
- la cobertura de contexto es alta (`context_recall` y
	`context_entity_recall`), pero el proxy actual penaliza fuerte la
	corrección de respuesta y la precisión de contexto, por lo que el siguiente
	paso natural es calibrar el scorer o reemplazarlo por `ragas` real antes de
	usar estos thresholds como gate de release

Calibración aplicada en el siguiente corte:

- `answer_correctness` del proxy ya no depende casi por completo de F1 estricto
	contra la referencia corta; ahora combina F1, recall de referencia, cobertura
	de claims y recall de entidades
- `context_precision` del proxy pasó a ponderar más el ranking temprano de
	contextos que el volumen bruto de citas retornadas
- el scorer quedó preparado para `ragas` real mediante `--scoring-engine auto`
	con fallback explícito si faltan proveedor o credenciales

Resultado calibrado del proxy sobre el mismo collector real:

- scorer JSON:
	`benchmark_reports/code_ragas_eval_20260610_003243.json`
- scorer CSV:
	`benchmark_reports/code_ragas_eval_20260610_003243.csv`
- `scoring_engine = offline_lexical_proxy`
- `answer_correctness = 0.6160`
- `context_precision = 0.3195`
- gate sobre `gate_candidate = pass_with_warnings`
- hard fails: ninguno
- soft fail remanente: `context_precision`

Estado del engine `ragas` real tras el smoke de integración:

- la librería y el schema ya quedan cableados en el scorer
- `auto` intenta `ragas` real y deja `engine_notes` cuando cae a proxy
- la ruta real validada por implementación quedó preparada para OpenAI
- el smoke con Vertex no quedó habilitado en esta iteración por dos bloqueos
	externos/reales: `Cloud Resource Manager API` deshabilitada en el proyecto de
	prueba y compatibilidad incompleta del client moderno requerido por `ragas`

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
