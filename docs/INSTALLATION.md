# Installation

Guia de instalacion y arranque local.

## Requisitos

- Python 3.12+ recomendado (compatibilidad verificada con 3.12.3)
- Git
- Rancher Desktop con nerdctl compose o Docker Desktop con docker compose
- kubectl y Kustomize (opcional para despliegue cloud en Kubernetes)

Requisito adicional en Windows (solo si falla instalacion de dependencias nativas):

- Microsoft Visual Studio 2022 Build Tools con workload C++
	(`Microsoft.VisualStudio.Workload.VCTools`)

## Setup rapido

1. Instalar dependencias.

```powershell
py -3.12 -m venv .venv
```

```bash
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Perfiles de dependencias:

- `requirements.txt`: baseline API/worker para levantar la API.
- `requirements-runtime.txt`: alias explicito del perfil headless.
- `requirements-desktop.txt`: agrega PySide6 para UI local.
- `requirements-full.txt`: agrega UI y tests para desarrollo completo.

2. Crear archivo de entorno.

```powershell
copy .env.example .env
```

3. Levantar stack local con Docker Compose (API + Neo4j).

```powershell
./scripts/start_compose.ps1
```

Opcional con Redis:

```powershell
./scripts/start_compose.ps1 -WithRedis
```

Para ingesta distribuida con worker RQ en local (recomendado con Redis):

```powershell
$env:INGESTION_EXECUTION_MODE = "rq"
./scripts/start_compose.ps1 -WithRedis
```

4. Levantar UI (opcional, desktop local).

Si instalaste solo `requirements.txt`, agrega primero soporte UI:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-desktop.txt
```

```powershell
.\.venv\Scripts\python -m coderag.ui.main_window
```

5. Detener stack compose cuando termines.

```powershell
./scripts/stop_compose.ps1
```

## Modos recomendados

- Estable para ingestas largas:

```powershell
./scripts/start_stable.ps1
```

- Desarrollo con autoreload:

```powershell
./scripts/start_dev.ps1
```

- Arranque directo de API (sin scripts):

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python -m main --host 127.0.0.1 --port 8000
```

## Kubernetes (manifests nativos)

- Base cloud (API + Neo4j):

```powershell
kubectl apply -k k8s/overlays/cloud
```

- Cloud con Redis opcional:

```powershell
kubectl apply -k k8s/overlays/cloud-with-redis
```

El overlay `cloud-with-redis` habilita modo `rq` y despliega worker dedicado.

Antes de aplicar en cloud, ajusta la imagen en
`k8s/overlays/cloud/patch-api-deployment.yaml`.

## Verificacion

- OpenAPI: http://127.0.0.1:8000/docs
- Health storage: GET /health

## Siguientes pasos

- Configuracion de providers: ver docs/CONFIGURATION.md.
- Flujos y arquitectura: ver docs/ARCHITECTURE.md.
- Referencia de endpoints: ver docs/API_REFERENCE.md.
- Despliegue Kubernetes detallado: ver [KUBERNETES.md](KUBERNETES.md).
