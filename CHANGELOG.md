# Changelog

Todos los cambios relevantes de este proyecto se documentan en este archivo.

Este formato sigue Keep a Changelog y Semantic Versioning.

## [Unreleased]

### Added
- Archivos de dependencias separados para runtime, desktop, desarrollo y
    entorno completo local:
    `requirements-runtime.txt`, `requirements-desktop.txt`,
    `requirements-dev.txt` y `requirements-full.txt`.
- Estructura documental orientada a customer journeys.
- Nuevas guias en docs/ para instalacion, configuracion, arquitectura,
  troubleshooting y contribucion.
- Ejemplos ejecutables en examples/ para ingesta y consultas.
- Scripts iniciales de validacion de documentacion en scripts/docs/.
- Dockerfile multi-stage y `.dockerignore` para empaquetar la API.
- Manifests Kubernetes nativos (`k8s/base`, overlays cloud y addon Redis opcional).
- Scripts `start_compose.ps1` y `stop_compose.ps1` para operar stack local completo.
- Worker RQ dedicado para ingesta asíncrona distribuida (modo `INGESTION_EXECUTION_MODE=rq`).
- Lock distribuido por `repo_id` para serializar encolado de ingestas en modo RQ.
- Nuevo entrypoint de API en `src/main.py` para arranque directo con `python -m main` usando `PYTHONPATH=src`.
- Nueva guia `KUBERNETES.md` con despliegue, secretos, probes, persistencia, rollback y validacion funcional.

### Changed
- Canonical provider naming now uses `vertex` for `LLM_PROVIDER` y
    `EMBEDDING_PROVIDER`; `vertex_ai` se mantiene temporalmente como alias
    compatible en runtime.
- `requirements.txt` pasa a representar el baseline API/worker para que el
    contrato por defecto priorice levantar la API.
- `Dockerfile` mantiene build de runtime usando el contrato API-first de
    `requirements.txt`, dejando fuera PySide6 y pytest del contenedor.
- `chromadb` se actualiza a `1.5.5` para alinear el stack vectorial con versiones recientes del ecosistema.
- README reestructurado como portal corto de navegacion.
- API reference reorganizada por journeys y operaciones.
- Estructura del paquete movida de `coderag/` a `src/coderag/`.
- Imports y entrypoints actualizados a `coderag.*` para API, UI,
  scripts y tests, manteniendo layout `src/` mediante `PYTHONPATH=src`.
- `docker-compose.yml` evolucionó de solo Neo4j a stack completo API + Neo4j, con perfil opcional Redis.
- `docker-compose.yml` ahora incluye servicio `worker` al activar perfil `redis`.
- Scripts `start_dev`, `start_stable` y `reset_cold` mantienen modo local iniciando solo Neo4j desde Compose.
- Scripts locales, benchmark de rollback y Docker runtime migran a arranque de API por `main` con `PYTHONPATH` configurado.
- Guia tecnica de configuracion ampliada para reflejar variables reales del runtime y defaults de health/model discovery.
- Documentacion de arquitectura aclara mapeo de puerto Redis `16379->6379` en Compose local.
- Requisitos de Python en README/INSTALLATION se normalizan a `3.12+` con referencia a version validada.
- API de ingesta retorna `503` cuando falla el encolado asíncrono.
- API de ingesta retorna `409` si ya existe ingesta activa para el mismo repositorio.
- Worker RQ ahora propaga fallas para activar la política de reintentos configurada.
- Política de reintentos configurable para relanzar solo errores transitorios.

### Fixed
- `datetime.utcnow` se reemplaza por timestamps UTC aware y se agregan filtros temporales de warnings de terceros en `pytest.ini`.
- Cobertura explicita de DELETE /repos/{repo_id} en documentacion de API.
- Cobertura de parametro logs_tail en GET /jobs/{job_id}.
- Imagen runtime ahora incluye `git` para permitir clonación durante ingestas en API/worker.
- `start_compose.ps1` aplica `HEALTH_CHECK_OPENAI=false` por defecto si no está definido.
