param(
    [ValidateSet("up", "down", "ps", "logs")]
    [string]$Action = "up",
    [switch]$NoDetach,
    [string[]]$Services = @(),
    [switch]$WithRedis
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-RancherDesktopEngineName {
    $settingsPath = Join-Path $env:LOCALAPPDATA "rancher-desktop\settings.json"
    if (-not (Test-Path $settingsPath)) {
        return $null
    }

    try {
        $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
        return [string]$settings.containerEngine.name
    }
    catch {
        return $null
    }
}

function Invoke-ContainerCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    $output = & $Command @Args 2>&1
    $exitCode = $LASTEXITCODE

    foreach ($line in $output) {
        Write-Host $line
    }

    return @{ ExitCode = $exitCode }
}

$root = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $root "docker-compose.yml"
if (-not (Test-Path $composeFile)) {
    throw "Compose file not found: $composeFile"
}

$canUseNerdctl = $false
$canUseDocker = $false
$rancherDesktopEngine = Get-RancherDesktopEngineName

if (Test-CommandExists -Name "nerdctl") {
    if ($rancherDesktopEngine -eq "moby") {
        Write-Host "Rancher Desktop detectado con engine 'moby'; se omite nerdctl compose."
    }
    else {
        $nerdctlVersion = Invoke-ContainerCommand -Command "nerdctl" -Args @("version")
        if ($nerdctlVersion.ExitCode -eq 0) {
            $canUseNerdctl = $true
        }
    }
}

if (Test-CommandExists -Name "docker") {
    $dockerVersion = Invoke-ContainerCommand -Command "docker" -Args @("compose", "version")
    if ($dockerVersion.ExitCode -eq 0) {
        $canUseDocker = $true
    }
}

if (-not $canUseNerdctl -and -not $canUseDocker) {
    throw "No compose runtime available. Install Rancher Desktop (nerdctl compose) or Docker Desktop (docker compose)."
}

$actionArgs = @()
switch ($Action) {
    "up" {
        $actionArgs = @("up")
        if (-not $NoDetach) {
            $actionArgs += "-d"
        }
        if ($WithRedis) {
            $actionArgs = @("--profile", "redis") + $actionArgs
        }
    }
    "down" {
        $actionArgs = @("down")
        if ($WithRedis) {
            $actionArgs = @("--profile", "redis") + $actionArgs
        }
    }
    "ps" {
        $actionArgs = @("ps")
        if ($WithRedis) {
            $actionArgs = @("--profile", "redis") + $actionArgs
        }
    }
    "logs" {
        if ($Services.Count -gt 0) {
            $actionArgs = @("logs", "--tail", "200") + $Services
        } else {
            if ($WithRedis) {
                $actionArgs = @(
                    "logs",
                    "--tail",
                    "200",
                    "neo4j",
                    "api",
                    "redis",
                    "worker"
                )
            } else {
                $actionArgs = @("logs", "--tail", "200", "neo4j", "api")
            }
        }
    }
}

$composeArgs = @("compose", "--file", $composeFile) + $actionArgs
if ($Services.Count -gt 0 -and $Action -ne "logs") {
    $composeArgs += $Services
}

if ($canUseNerdctl) {
    Write-Host "Using Rancher Desktop runtime via nerdctl compose..."
    $result = Invoke-ContainerCommand -Command "nerdctl" -Args $composeArgs
    if ($result.ExitCode -eq 0) {
        exit 0
    }

    Write-Host "nerdctl compose failed (exit $($result.ExitCode))."
    if ($canUseDocker) {
        Write-Host "Falling back to docker compose..."
        $dockerResult = Invoke-ContainerCommand -Command "docker" -Args $composeArgs
        exit $dockerResult.ExitCode
    }

    exit $result.ExitCode
}

Write-Host "Using Docker Desktop runtime via docker compose..."
$dockerOs = (& docker info --format "{{.OperatingSystem}}" 2>$null)
if ($rancherDesktopEngine -eq "moby" -and $dockerOs -match "Docker Desktop") {
    Write-Warning (
        "Rancher Desktop esta configurado con engine 'moby', pero el CLI docker " +
        "activo esta conectado a Docker Desktop. Si quieres usar Rancher Desktop, " +
        "cambia el contexto/daemon de docker antes de levantar el stack."
    )
}
$onlyDockerResult = Invoke-ContainerCommand -Command "docker" -Args $composeArgs
exit $onlyDockerResult.ExitCode
