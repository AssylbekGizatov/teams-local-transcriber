# Start prepared local services; never create a meeting, join a bot or modify tokens.
[CmdletBinding()]
param([ValidateRange(30, 1800)][int]$WarmupTimeoutSeconds = 600)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-DockerChecked {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker operation failed. See the diagnostic output above.' }
}

function Read-OperatorToken {
    $lines = @(Get-Content -LiteralPath (Join-Path $projectRoot '.env') |
        Where-Object { $_ -match '^APP_API_TOKEN=' })
    if ($lines.Count -ne 1) { throw 'Set exactly one APP_API_TOKEN in the existing .env file.' }
    $value = $lines[0].Substring('APP_API_TOKEN='.Length).Trim().Trim('"').Trim("'")
    if ($value.Length -lt 32) { throw 'APP_API_TOKEN is not configured.' }
    return $value
}

Push-Location -LiteralPath $projectRoot
try {
    $engineType = Invoke-DockerChecked -Arguments @('info', '--format', '{{.OSType}}')
    if ($engineType -ne 'linux') { throw 'This deployment requires the Linux Docker engine.' }
    $activeBots = Invoke-DockerChecked -Arguments @('ps', '--filter', 'name=vexa-mtg-', '--format', '{{.Names}}')
    if ($activeBots) { throw 'An existing bot is running. Stop its owned job before updating this deployment.' }
    foreach ($file in @('.env', 'deploy/vexa/.env', 'vendor/vexa/.local-build/PATCH_SHA256')) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Missing prepared deployment file: $file. Follow docs/TEAMS_CONNECTOR.md first."
        }
    }
    # Never build/download implicitly. These are the reviewed, prepared images.
    Invoke-DockerChecked -Arguments @('image', 'inspect', 'teams-asr-platform:local',
        'teams-asr-vexa-bot:f1a62fbb-local1', '--format', '{{.Id}}')
    Invoke-DockerChecked -Arguments @('run', '--rm', '--pull', 'never', '--network', 'none',
        '--entrypoint', 'python', '--mount', "type=bind,source=$projectRoot,target=/deployment,readonly",
        'teams-asr-platform:local', '/deployment/deploy/vexa/verify_vendor.py', '/deployment/vendor/vexa')
    $vexaArgs = @('compose', '--env-file', 'deploy/vexa/.env', '-f', 'deploy/vexa/compose.yaml')
    Invoke-DockerChecked -Arguments ($vexaArgs + @('up', '-d', '--no-build', '--wait', '--wait-timeout', '120'))
    Invoke-DockerChecked -Arguments @('compose', '-f', 'compose.yaml', '-f', 'deploy/compose.teams.yaml',
        '--profile', 'cpu', 'up', '-d', '--no-build', '--wait', '--wait-timeout', '120')
    $headers = @{Authorization = 'Bearer ' + (Read-OperatorToken)}
    $deadline = (Get-Date).AddSeconds($WarmupTimeoutSeconds)
    do {
        $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/readiness' -Headers $headers -TimeoutSec 10
        if ($ready.model.warmup -eq 'failed') { throw 'ASR warmup failed. Inspect docker compose logs platform.' }
        if ($ready.model.warmup -eq 'ready') {
            Write-Output 'Local services are healthy; ASR is warm. Open http://127.0.0.1:8000. No meeting was created.'
            return
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw 'Services started, but ASR warmup did not finish within the deadline. Inspect platform logs.'
} finally {
    Pop-Location
}
