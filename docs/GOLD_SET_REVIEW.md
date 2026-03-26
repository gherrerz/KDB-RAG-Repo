# Gold Set Review Guide

Esta guia define como aprobar el gold set factual para usarlo como gate oficial
en staging.

## 1. Objetivo

Asegurar que los expected facts del gold set representan hechos realmente
importantes para consultas arquitectonicas y no solo tokens faciles de detectar.

## 2. Plantilla de revisión

Archivo base:

- scripts/benchmark_data/architecture_facts_review_template.csv

Columnas:

- `query`: consulta evaluada.
- `fact`: hecho esperado a validar.
- `is_correct`: `true` o `false` segun relevancia/correccion del hecho.
- `reviewer`: identificador del revisor.
- `notes`: comentarios opcionales.

## 3. Criterios de aprobación

- Uplift factual ON vs OFF >= +15%.
- Revisión humana completada para >= 90% de filas del CSV.
- Ratio de hechos marcados `is_correct=true` >= 85%.
- Sin discrepancias críticas no resueltas en `notes`.

## 4. Comando de gate

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_facts_gate.py \
  --on-report benchmark_reports/architecture_facts_eval_20260324_223605.json \
  --off-report benchmark_reports/architecture_facts_eval_20260324_224016.json \
  --review-csv scripts/benchmark_data/architecture_facts_review_template.csv \
  --min-uplift 0.15 \
  --min-reviewed-ratio 0.90 \
  --min-correct-ratio 0.85
```

Resultado:

- Genera `benchmark_reports/facts_gate_decision_*.json` con estado final.
- Estados posibles: `pass`, `pending_review`, `fail`.
