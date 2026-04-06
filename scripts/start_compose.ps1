param(
    [switch]$WithRedis
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $env:HEALTH_CHECK_OPENAI) {
    $env:HEALTH_CHECK_OPENAI = "false"
}

function Wait-Port {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$Retries = 45,
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

Write-Host "[1/3] Levantando stack compose (api + neo4j" -NoNewline
if ($WithRedis) {
    Write-Host " + redis + worker)..."
    & ./scripts/compose_neo4j.ps1 up -WithRedis
} else {
    Write-Host ")..."
    & ./scripts/compose_neo4j.ps1 up
}

Write-Host "[2/3] Esperando API (8000)..."
if (-not (Wait-Port -Port 8000 -Retries 45 -DelaySeconds 1)) {
    throw "API no quedo disponible en 127.0.0.1:8000"
}

Write-Host "[3/3] Stack compose listo."
Write-Host "- OpenAPI: http://127.0.0.1:8000/docs"
Write-Host "- Health storage: http://127.0.0.1:8000/health"
Write-Host "- UI desktop opcional: .\.venv\Scripts\python -m src.coderag.ui.main_window"
