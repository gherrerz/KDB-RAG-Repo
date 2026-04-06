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
- Dockerfile multi-stage y `.dockerignore` para empaquetar la API.
- Manifests Kubernetes nativos (`k8s/base`, overlays cloud y addon Redis opcional).
- Scripts `start_compose.ps1` y `stop_compose.ps1` para operar stack local completo.

### Changed
- README reestructurado como portal corto de navegacion.
- API reference reorganizada por journeys y operaciones.
- Estructura del paquete movida de `coderag/` a `src/coderag/`.
- Imports y entrypoints actualizados a `src.coderag.*` para API, UI,
  scripts y tests.
- `docker-compose.yml` evolucionó de solo Neo4j a stack completo API + Neo4j, con perfil opcional Redis.
- Scripts `start_dev`, `start_stable` y `reset_cold` mantienen modo local iniciando solo Neo4j desde Compose.

### Fixed
- Cobertura explicita de DELETE /repos/{repo_id} en documentacion de API.
- Cobertura de parametro logs_tail en GET /jobs/{job_id}.
