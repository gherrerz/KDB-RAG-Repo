# Alembic Shared DB Cutover

## Objetivo

Ejecutar Fase 6 del cutover cuando KDB-RAG-Repo y KDB-RAG-Docs comparten la misma base Postgres.

Contrato operativo:

- Repo escribe y valida solo en `alembic_version_repo`.
- Docs escribe y valida solo en `alembic_version_docs`.

## Prerrequisitos

- Ventana de cambio aprobada.
- Credenciales `POSTGRES_*` operativas para la base objetivo.
- Conectividad desde el runner a la base Postgres.
- Backups habilitados y espacio suficiente para dump.

## Fase 6.1: Backup

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = "migration_reports/coipo_db_pre_cutover_$timestamp.sql"

pg_dump \
  --host $env:POSTGRES_HOST \
  --port $env:POSTGRES_PORT \
  --username $env:POSTGRES_USER \
  --dbname $env:POSTGRES_DB \
  --format=plain \
  --file $backupFile
```

Verificacion minima:

```powershell
Test-Path $backupFile
Get-Item $backupFile | Select-Object FullName, Length, LastWriteTime
```

## Fase 6.2: Poblar/Actualizar tabla Alembic de Repo

```powershell
# 1) Estado previo
.\.venv\Scripts\python scripts/postgres_schema_admin.py current

# 2) Upgrade
.\.venv\Scripts\python scripts/postgres_schema_admin.py upgrade head

# 3) Validacion strict
.\.venv\Scripts\python scripts/postgres_schema_admin.py validate
```

## Fase 6.3: Verificacion cruzada

Comprobar que Repo no usa la tabla de Docs:

```sql
SELECT version_num FROM alembic_version_repo;
SELECT version_num FROM alembic_version_docs;
```

Criterio de aceptacion:

- `alembic_version_repo` contiene solo revisiones del repo.
- `alembic_version_docs` contiene solo revisiones de docs.
- `scripts/postgres_schema_admin.py validate` finaliza OK.

## Rollback Basico

Si falla validacion posterior al upgrade:

1. Detener despliegue de la aplicacion afectada.
2. Restaurar backup pre-cutover.
3. Repetir validacion con `current` y `validate`.

```powershell
# Ejemplo orientativo, ajustar a politica interna
psql --host $env:POSTGRES_HOST --port $env:POSTGRES_PORT --username $env:POSTGRES_USER --dbname postgres -c "DROP DATABASE IF EXISTS $env:POSTGRES_DB"
psql --host $env:POSTGRES_HOST --port $env:POSTGRES_PORT --username $env:POSTGRES_USER --dbname postgres -c "CREATE DATABASE $env:POSTGRES_DB"
psql --host $env:POSTGRES_HOST --port $env:POSTGRES_PORT --username $env:POSTGRES_USER --dbname $env:POSTGRES_DB -f <backup_file.sql>
```

## Fase 7.2: Politica de retiro de `alembic_version` legacy

La tabla `alembic_version` legacy no se elimina durante el cutover de Fase 6.
Su retiro se hace en dos pasos para mantener rollback rapido.

Ventana minima recomendada:

- 14 dias calendario desde el cutover.
- Al menos 2 ciclos de despliegue de Repo y Docs sin colisiones Alembic.

Prevalidaciones obligatorias antes de retirar legacy:

```sql
SELECT version_num FROM alembic_version_repo;
SELECT version_num FROM alembic_version_docs;
SELECT to_regclass('public.alembic_version') AS legacy_table;
```

Criterios previos:

- `alembic_version_repo` y `alembic_version_docs` existen y reflejan los heads esperados.
- Repo y Docs arrancan y validan migraciones sin errores en la ventana de observacion.
- La tabla `alembic_version` no es usada por ninguna ruta operativa activa.

Ejecucion recomendada (reversible):

```sql
ALTER TABLE IF EXISTS alembic_version
RENAME TO alembic_version_legacy_retired_yyyymmdd;
```

Mantener la tabla renombrada por 7 dias adicionales. Si no hay incidentes,
eliminarla en una ventana posterior:

```sql
DROP TABLE IF EXISTS alembic_version_legacy_retired_yyyymmdd;
```

Rollback del retiro:

```sql
ALTER TABLE IF EXISTS alembic_version_legacy_retired_yyyymmdd
RENAME TO alembic_version;
```
