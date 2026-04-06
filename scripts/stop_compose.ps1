param(
    [switch]$WithRedis
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($WithRedis) {
    & ./scripts/compose_neo4j.ps1 down -WithRedis
} else {
    & ./scripts/compose_neo4j.ps1 down
}

Write-Host "Stack compose detenido."
