Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonExe = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "No se encontro el ejecutable Python del entorno virtual: $pythonExe"
}

Write-Host "[1/3] Levantando Neo4j (compose helper)..."
& ./scripts/compose_neo4j.ps1 up -Services neo4j

Write-Host "[2/3] Levantando API en modo desarrollo (--reload)..."
$env:HEALTH_CHECK_OPENAI = "false"
$env:CODERAG_API_BASE = "http://127.0.0.1:8000"
$env:PYTHONPATH = Join-Path $root "src"
Start-Process -FilePath $pythonExe -ArgumentList "-m main --host 127.0.0.1 --port 8000 --reload" -WorkingDirectory $root | Out-Null

Write-Host "[3/3] Levantando UI..."
Start-Process -FilePath $pythonExe -ArgumentList "-m coderag.ui.main_window" -WorkingDirectory $root | Out-Null

Write-Host "Arranque dev completado. Nota: --reload puede interrumpir ingestas largas."
