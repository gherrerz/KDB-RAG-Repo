# Legacy Storage Retirement Policy

Esta politica fija como retirar los caminos legacy de storage despues del
cutover ORM/Alembic sobre PostgreSQL. Queda como referencia archivistica del
retiro ya completado.

## Alcance

- Metadata SQLite local.
- BM25 local y snapshots en disco.
- Tablas PostgreSQL legacy `jobs`, `repos` y `lexical_corpus`.

## Estado objetivo

- Metadata operativa solo en PostgreSQL versionado.
- Busqueda lexica operativa solo en LexicalStore Postgres.
- Tablas legacy eliminadas con migracion auditada al cerrar la observacion.
- Sin artefactos BM25/SQLite activos en runtime ni en tooling operativo.

## Matriz de soporte residual

| Superficie | Estado actual | Guard o contrato | Cobertura recomendada |
| --- | --- | --- | --- |
| Metadata Postgres versionada | Operativa principal | `POSTGRES_*` | Suite normal |
| LexicalStore Postgres | Operativa principal | `POSTGRES_*` | Suite normal |
| Snapshots BM25 legacy | Retirados del tooling operativo | N/A | Sin cobertura dedicada activa |
| `metadata.db` legacy | Retirado del tooling operativo | N/A | Sin cobertura dedicada activa |
| Tablas PostgreSQL legacy | Retiradas por Alembic | `0002_drop_legacy_postgres_tables` | Validacion de esquema |

## Gobernanza de pruebas legacy

- Release 5 elimina los marcadores `legacy_rollback` y `legacy_compat` de la suite activa.
- El camino operativo principal se valida ahora con la suite normal y con las validaciones focalizadas por componente.
- Si reaparece una necesidad de rollback excepcional, debe modelarse como procedimiento operativo o suite aislada fuera del runtime soportado, no como marcador permanente en `pytest.ini`.

## Ventana de observacion

- Duracion recomendada: 14 dias corridos desde el primer reporte de cutover con
  `cutover_ready=true`.
- Durante la ventana, PostgreSQL es el sistema de registro para metadata y
  lexical.
- Antes de Release 3, las flags legacy debian permanecer desactivadas durante
   la operacion normal. Desde Release 3 esas flags ya no forman parte del
   runtime soportado.
- Las tablas `jobs`, `repos` y `lexical_corpus` deben permanecer retenidas solo
   hasta ejecutar la migracion fisica de retiro.

## Criterio de salida

La retirada definitiva del legacy puede ejecutarse cuando se cumplan todos los
siguientes puntos:

1. La ventana de observacion se completa sin incidentes Sev1/Sev2 atribuibles a
   metadata Postgres o LexicalStore Postgres.
2. API y worker arrancan en modo `production` con politica `validate` y `/health`
   permanece en verde.
3. Al menos una reingesta representativa posterior al cutover completa query y
   retrieval con backend lexico Postgres sin desvio a una version legacy.
4. Durante la observacion previa a Release 3 no se requirio reactivar caminos
   BM25/SQLite como mitigacion sostenida.
5. El reporte final de cutover mantiene auditoria sin `missing_after` para
   `jobs`, `repos` y `lexical_corpus`.

Como evidencia formal de esta fase puede reutilizarse el runner
`scripts/run_postgres_legacy_cutover.py`, que ahora también exporta un bloque
de salida de observacion con:

- `observation_exit_automatic_ready`
- `observation_checks_complete`
- `observation_exit_ready`
- `observation_checklist`

La aprobacion operativa recomendada para iniciar el retiro definitivo requiere
que `observation_exit_ready == true`.

## Corte de implementacion por release

Para evitar mezclar runtime, tooling y drop fisico en la misma ventana, el
retiro tecnico posterior al criterio de salida se separa asi:

| Release | Superficie | Decisión |
| --- | --- | --- |
| Release 3 | `core/settings.py` | Retirar flags `LEXICAL_LEGACY_BM25_READ_FALLBACK`, `LEXICAL_LEGACY_BM25_DUAL_WRITE` y `METADATA_LEGACY_SQLITE_FALLBACK` del runtime soportado |
| Release 3 | `storage/metadata_store_factory.py` | Forzar solo metadata Postgres versionada |
| Release 3 | `core/lexical_index.py` | Forzar solo LexicalStore Postgres como backend lexico activo |
| Release 3 | `ingestion/pipeline.py` | Eliminar dual-write BM25 del camino normal de ingesta |
| Release 3 | `core/storage_health.py` | Eliminar checks operativos de SQLite/BM25 como backend soportado |
| Release 4 | `maintenance/reset_service.py` | Retirado: el tooling ya no limpia snapshots BM25 ni `metadata.db` |
| Release 4 | `storage/legacy_runtime.py` | Retirado: helpers legacy eliminados del codigo operativo |
| Release 4 | Base PostgreSQL legacy | Completado mediante `0002_drop_legacy_postgres_tables` |

## Rollback

### Rollback lexico

1. Desde Release 3 ya no existe rollback lexico soportado por flags.
2. Si operacion necesitara una mitigacion extraordinaria, debe hacerse por
   despliegue controlado de una version anterior o por procedimiento tecnico
   separado, no reabriendo el runtime actual.
3. Mantener PostgreSQL intacto para diagnostico; el rollback no debe borrar el
   esquema versionado ni las tablas legacy retenidas.

### Rollback de metadata

1. Desde Release 3 ya no existe fallback SQLite soportado por configuracion.
2. `metadata.db` ya no forma parte del runtime ni del tooling operativo.
3. Si se requiere rollback de metadata, hacerlo por despliegue o procedimiento
   excepcional, no reescribiendo las tablas versionadas en caliente.

## Retiro definitivo

Con el cierre del bloque:

1. La migracion `0002_drop_legacy_postgres_tables` queda como mecanismo
   auditado del retiro fisico en los entornos objetivo.
2. Los snapshots BM25 y `metadata.db` ya no forman parte del tooling ni de la
   limpieza operativa residual.
3. Las ultimas pruebas reservadas a rollback o compatibilidad legacy fueron
   retiradas de la suite activa.
4. La documentacion operativa debe mantenerse centrada en el esquema
   PostgreSQL versionado.