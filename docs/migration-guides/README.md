# Migration Guides

Este directorio contiene guias para cambios incompatibles.

## Plantilla sugerida

```markdown
# Migracion: <titulo>

## Alcance

- Version origen:
- Version destino:
- Impacto:

## Que cambia

- Cambio 1
- Cambio 2

## Pasos de migracion

1. Paso 1
2. Paso 2
3. Paso 3

## Verificacion

- Check 1
- Check 2

## Rollback

- Paso de rollback 1
- Paso de rollback 2
```

## Convenciones

- Un archivo por cambio incompatible.
- Enlazar cada guia desde CHANGELOG.md.
- Incluir ejemplos before/after cuando aplique.

## Guias disponibles

- [postgres-legacy-cutover.md](postgres-legacy-cutover.md)
- [legacy-storage-retirement.md](legacy-storage-retirement.md)
- [orm-postgres-legacy-handoff.md](orm-postgres-legacy-handoff.md)
