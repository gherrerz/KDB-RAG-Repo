Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonExe = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "No se encontro el ejecutable Python del entorno virtual: $pythonExe"
}

Write-Host "[0/5] Cerrando procesos previos de API/UI..."
$patterns = @(
    "-m uvicorn src.coderag.api.server:app",
    "-m src.coderag.ui.main_window",
    "--multiprocessing-fork"
)
$pythonProcs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*.exe" }
foreach ($proc in $pythonProcs) {
    $cmd = [string]$proc.CommandLine
    foreach ($pattern in $patterns) {
        if ($cmd -like "*$pattern*") {
            try {
                Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
                Write-Host "  - detenido PID $($proc.ProcessId): $pattern"
            } catch {
                Write-Host "  - aviso: no se pudo detener PID $($proc.ProcessId): $($_.Exception.Message)"
            }
            break
        }
    }
}
Start-Sleep -Seconds 1

function Wait-Port {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$Retries = 30,
        [int]$DelaySeconds = 1
    )

    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        $ok = Test-NetConnection 127.0.0.1 -Port $Port -WarningAction SilentlyContinue
        if ($ok.TcpTestSucceeded) {
            return $true
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

Write-Host "[1/5] Levantando Neo4j (compose helper)..."
& ./scripts/compose_neo4j.ps1 up -Services neo4j

Write-Host "[2/5] Esperando Neo4j (bolt 17687)..."
if (-not (Wait-Port -Port 17687 -Retries 45 -DelaySeconds 1)) {
    throw "Neo4j no quedo disponible en 127.0.0.1:17687"
}

Write-Host "[3/5] Levantando API estable sin reload..."
$env:HEALTH_CHECK_OPENAI = "false"
$env:CODERAG_API_BASE = "http://127.0.0.1:8000"
Start-Process -FilePath $pythonExe -ArgumentList "-m uvicorn src.coderag.api.server:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root | Out-Null

Write-Host "[4/5] Esperando API (8000)..."
if (-not (Wait-Port -Port 8000 -Retries 30 -DelaySeconds 1)) {
    throw "API no quedo disponible en 127.0.0.1:8000"
}

Write-Host "[5/5] Levantando UI..."
Start-Process -FilePath $pythonExe -ArgumentList "-m src.coderag.ui.main_window" -WorkingDirectory $root | Out-Null

Write-Host "Arranque estable completado."
