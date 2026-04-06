# Installation

Guia de instalacion y arranque local.

## Requisitos

- Python 3.12.3 recomendado (compatibilidad verificada)
- Git
- Rancher Desktop con nerdctl compose o Docker Desktop con docker compose

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

2. Crear archivo de entorno.

```powershell
copy .env.example .env
```

3. Levantar Neo4j.

```powershell
./scripts/compose_neo4j.ps1 up
```

4. Levantar API.

```powershell
.\.venv\Scripts\python -m uvicorn src.coderag.api.server:app
```

5. Levantar UI (opcional).

```powershell
.\.venv\Scripts\python -m src.coderag.ui.main_window
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

## Verificacion

- OpenAPI: http://127.0.0.1:8000/docs
- Health storage: GET /health/storage

## Siguientes pasos

- Configuracion de providers: ver docs/CONFIGURATION.md.
- Flujos y arquitectura: ver docs/ARCHITECTURE.md.
- Referencia de endpoints: ver docs/API_REFERENCE.md.
