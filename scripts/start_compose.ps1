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

function Wait-HealthEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$Retries = 45,
        [int]$DelaySeconds = 1
    )

    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

Write-Host "[1/3] Levantando stack compose (api + neo4j + chroma + postgres" -NoNewline
if ($WithRedis) {
    Write-Host " + redis + worker)..."
    & ./scripts/compose_neo4j.ps1 up -WithRemote -WithRedis
} else {
    Write-Host ")..."
    & ./scripts/compose_neo4j.ps1 up -WithRemote
}

Write-Host "[2/3] Esperando /health (8000)..."
if (-not (Wait-HealthEndpoint -Url "http://127.0.0.1:8000/health" -Retries 45 -DelaySeconds 1)) {
    throw "API no quedo saludable en http://127.0.0.1:8000/health"
}

Write-Host "[3/3] Stack compose listo."
Write-Host "- OpenAPI: http://127.0.0.1:8000/docs"
Write-Host "- Health storage: http://127.0.0.1:8000/health"
Write-Host "- UI desktop opcional: `$env:PYTHONPATH='src'; .\.venv\Scripts\python -m coderag.ui.main_window"
