# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.

Este formato sigue Keep a Changelog y Semantic Versioning.

## [Unreleased]

### Added
- Estructura documental orientada a customer journeys.
- Nuevas guias en docs/ para instalacion, configuracion, arquitectura,
  troubleshooting y contribucion.
- Ejemplos ejecutables en examples/ para ingesta y consultas.
- Scripts iniciales de validacion de documentacion en scripts/docs/.

### Changed
- README reestructurado como portal corto de navegacion.
- API reference reorganizada por journeys y operaciones.
- Estructura del paquete movida de `coderag/` a `src/coderag/`.
- Imports y entrypoints actualizados a `src.coderag.*` para API, UI,
  scripts y tests.

### Fixed
- Cobertura explicita de DELETE /repos/{repo_id} en documentacion de API.
- Cobertura de parametro logs_tail en GET /jobs/{job_id}.
