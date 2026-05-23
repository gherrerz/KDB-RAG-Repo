# Migracion: PostgreSQL legacy a esquema Alembic

## Alcance

- Version origen: tablas PostgreSQL legacy `jobs`, `repos`, `lexical_corpus`.
- Version destino: tablas versionadas `tbl_repository_jobs`,
  `tbl_repository_repos`, `tbl_repository_lexicalcorpus` en `head` de Alembic.
- Impacto: migra metadata operativa y corpus lexico al esquema ORM/Alembic sin
  depender del camino SQL nativo en runtime.

## Que cambia

- El runtime deja de depender de tablas legacy sin versionado.
- La validacion de startup pasa a apoyarse en Alembic `head` como fuente de
  verdad.
- La migracion produce un reporte exportable JSON/CSV para auditoria previa al
  cutover.

## Prerrequisitos

1. Respaldo reciente de la base representativa o productiva.
2. Variables `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`
   y `POSTGRES_PASSWORD` apuntando a la base a auditar.
3. Ventana controlada para ejecutar el cutover y revisar el reporte.
4. Confirmar que la base source contiene las tablas `jobs`, `repos` y
   `lexical_corpus` con el contrato esperado.

## Ejecucion recomendada

1. Validar el estado actual del esquema versionado.

```powershell
.\.venv\Scripts\python scripts/postgres_schema_admin.py current
```

1. Ejecutar la migracion con export de auditoria.

```powershell
.\.venv\Scripts\python scripts/run_postgres_legacy_cutover.py --output-dir migration_reports --report-prefix legacy_cutover_candidate --health-url http://127.0.0.1:8000/health --confirm-backup --confirm-rollback --confirm-retain-legacy
```

1. Revisar los artefactos generados en `migration_reports/`.

Artefactos esperados:

- JSON con precheck, migracion, validate y resumen de readiness.
- Markdown con checklist automatico/manual de cutover.

Confirmaciones manuales soportadas por el runner:

- `--confirm-backup`: cierra el check manual de backup validado.
- `--confirm-rollback`: cierra el check manual de rollback aprobado.
- `--confirm-retain-legacy`: cierra el check manual de retencion temporal de
  tablas legacy.

Perfiles operativos soportados por el runner:

- `--report-profile cutover`: usa el prefijo por defecto
  `legacy_postgres_cutover_run`.
- `--report-profile observation-exit`: usa el prefijo por defecto
  `legacy_observation_exit`.

Si necesitas una variante, `--report-prefix` sigue permitiendo override
explicito.

Confirmaciones adicionales para cierre de observacion y aprobacion formal del
retiro legacy:

- `--confirm-observation-window`: marca la ventana de observacion como
  completada.
- `--confirm-no-sev1-sev2`: confirma ausencia de incidentes Sev1/Sev2
  atribuibles al bloque.
- `--confirm-representative-reingest`: confirma una reingesta representativa
  validada sin fallback.
- `--confirm-no-sustained-legacy-flags`: confirma que no hubo uso sostenido de
  flags legacy durante la observacion.
- `--approve-legacy-removal`: registra la aprobacion formal para iniciar el
  retiro definitivo del legacy.

## Cierre de observacion

El mismo runner puede reutilizarse despues de la ventana de observacion para
emitir evidencia formal de Release 1 del retiro legacy.

Ejemplo recomendado:

```powershell
.\.venv\Scripts\python scripts/run_postgres_legacy_cutover.py --output-dir migration_reports --report-profile observation-exit --health-url http://127.0.0.1:8000/health --confirm-observation-window --confirm-no-sev1-sev2 --confirm-representative-reingest --confirm-no-sustained-legacy-flags --approve-legacy-removal
```

Campos relevantes del reporte exportado:

- `report_profile`
- `report_prefix`
- `observation_exit_automatic_ready`
- `observation_checks_complete`
- `observation_exit_ready`
- `observation_checklist`

## Checklist de cutover

- Backup validado antes de tocar la base.
- El comando de migracion finalizo sin excepcion.
- `current_heads` y `expected_heads` quedaron alineados en el reporte.
- `cutover_ready == true` en el JSON final.
- `legacy_tables_missing` esta vacio para la base que se quiere cortar.
- `matched_after == source_count` en `jobs`.
- `matched_after == source_count` en `repos`.
- `matched_after == source_count` en `lexical_corpus`.
- `missing_after == 0` en `jobs`.
- `missing_after == 0` en `repos`.
- `missing_after == 0` en `lexical_corpus`.
- API/worker inician en modo `RUNTIME_ENVIRONMENT=production` sin intentar
  auto-migrar y sin error de revision.
- `/health` expone estado `postgres_startup` alineado.

## Verificacion posterior

- Ejecutar `python scripts/postgres_schema_admin.py validate`.
- Consultar `/health` y verificar que PostgreSQL aparezca listo.
- Probar una consulta de lectura y un flujo simple de metadata del repositorio.
- Confirmar que el reporte JSON/CSV queda guardado como evidencia de cambio.

## Rollback

1. No borrar tablas legacy en la misma ventana de migracion.
2. Si la auditoria no cumple los criterios de cutover, detener el cambio y usar
   solo las tablas legacy como fuente operativa.
3. Si el runtime ya fue apuntado al esquema nuevo y falla la validacion,
   restaurar la base desde backup o volver a la version anterior del servicio.
4. Posponer la eliminacion de tablas legacy hasta cerrar la validacion
   funcional y operativa.
