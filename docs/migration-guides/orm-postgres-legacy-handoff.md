# Handoff: Cierre de Bloque ORM, PostgreSQL y Retiro Legacy

## Resumen ejecutivo

Este bloque queda formalmente cerrado.

El objetivo original era reemplazar el enfoque de SQL nativo disperso por un
esquema soportado por clases y objetos Python, con control de esquema,
migración de datos reales y retiro progresivo de caminos legacy.

El resultado final es una arquitectura operativa centrada en PostgreSQL
versionado con SQLAlchemy 2 y Alembic para metadata y corpus léxico, más un
runtime y tooling donde SQLite, BM25 y las tablas PostgreSQL legacy ya no
forman parte del camino soportado.

## Alcance cerrado

- Base ORM/PostgreSQL compartida para API, worker y tooling.
- Migraciones Alembic y política de startup mixta por entorno.
- Migración real desde tablas PostgreSQL legacy al esquema versionado.
- Cutover con auditoría exportable y checklist manual/automático.
- Validación real de API y worker sobre entorno representativo.
- Retiro estructural de SQLite y BM25 del runtime principal.
- Retiro físico de tablas y artefactos legacy del tooling operativo.
- Clasificación explícita de cobertura legacy en la suite de pruebas.

## Arquitectura objetivo entregada

- Metadata operativa: PostgreSQL versionado.
- Búsqueda léxica operativa: LexicalStore Postgres.
- Control de esquema: Alembic.
- Runtime principal: API + worker + health + reset sin dependencia estructural
  de SQLite ni BM25.
- Compatibilidad residual: ninguna dependencia legacy activa en runtime,
  tooling o suite principal.

## Entregables principales

### Runtime y persistencia

- Session factory y adaptación SQLAlchemy/Postgres compartida.
- Modelos ORM para metadata operativa.
- Resolución de backend de metadata y lexical alineada al runtime actual.
- Retiro completo de módulos legacy fuera del runtime soportado.

### Tooling y operación

- Administración de esquema Alembic desde scripts del repo.
- Migración de datos legacy con auditoría source/target.
- Runner de cutover con export JSON/Markdown y checks manuales.

### Retiro legacy

- SQLite deja de ser fallback implícito del runtime.
- BM25 deja de formar parte del tooling operativo y de la limpieza por repo.
- `lexical_loaded` pasa a ser el único campo visible de readiness léxico en
  status por repositorio.

### Documentación y gobierno

- Guía de cutover PostgreSQL legacy.
- Política de retiro de storage legacy.
- Suite activa alineada al runtime Postgres-only sin marcadores legacy.

## Backlog final del bloque

| Línea de trabajo | Estado final | Entregable de cierre | Criterio de aceptación alcanzado |
| --- | --- | --- | --- |
| Base ORM y sesiones PostgreSQL | Cerrado | Session factory, modelos y esquema versionado | API, worker y tooling consumen la misma base compartida |
| Migraciones y startup | Cerrado | Alembic + política dev/test/prod | `development`/`test` auto-upgradean y `production` valida sin mutar |
| Migración de datos legacy | Cerrado | Script de migración con auditoría | Conteos source/target exportables y validados |
| Cutover y post-check | Cerrado | Runner de cutover y checklist | Reporte final con `cutover_ready=true` |
| Retiro estructural SQLite/BM25 | Cerrado | Runtime principal desacoplado | Worker, retrieval, health, reset y pipeline no dependen del legacy como backend operativo |
| Gobierno de compatibilidad legacy | Cerrado | Eliminación de marcadores, pruebas y shims legacy | La suite principal y el contrato público reflejan solo comportamiento soportado |

## Evidencia de validación

- Migración y cutover validados con base representativa.
- API y worker validados en modo `production` contra el stack operativo.
- Suites focalizadas del bloque legacy y de retrieval en verde.
- Validación de documentación en verde mediante `scripts/docs/validate_docs.py`.

## Superficie residual aceptada para handoff

| Superficie residual | Estado | Propósito |
| --- | --- | --- |
| Ninguna cobertura legacy activa | Cerrado | La suite principal ya refleja solo comportamiento soportado |

## Comandos útiles para handoff

Validar camino operativo principal:

```powershell
pytest
```

Validar esquema PostgreSQL ya migrado:

```powershell
.\.venv\Scripts\python.exe scripts/postgres_schema_admin.py validate
```

Validar documentación del bloque:

```powershell
.\.venv\Scripts\python.exe scripts/docs/validate_docs.py
```

## Riesgos residuales

- Consumidores externos deben consumir `lexical_loaded`; el alias histórico
  `bm25_loaded` ya no forma parte del contrato visible.
- El runtime soportado ya no admite fallback BM25/SQLite; cualquier rollback
  posterior requiere un procedimiento separado del runtime actual.
- La operación futura debe mantener documentación y tooling alineados con el
  runtime Postgres-only.

## Trabajo posterior al bloque

Este trabajo ya no forma parte del bloque cerrado, pero debe quedar visible
para el siguiente owner:

1. Eliminar tablas PostgreSQL legacy al cerrar la ventana de observación.
2. Mantener la documentación operativa alineada al runtime Postgres-only.

## Handoff recomendado

- Owner técnico siguiente: mantenimiento operativo de PostgreSQL/Alembic.
- Estado del bloque: cerrado, sin trabajo técnico crítico pendiente dentro del
  alcance original.
- Tipo de seguimiento recomendado: operación y mantenimiento, no rediseño.