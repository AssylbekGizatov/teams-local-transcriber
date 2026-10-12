# Hermetic Windows PowerShell 5.1 contract tests. No Docker, downloads or meetings.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/../easy-start/launcher.ps1"
$OriginalRoot = $Root
$Root = Join-Path ([IO.Path]::GetTempPath()) ('teams-launcher-test-' + [Guid]::NewGuid())
$script:Calls = [Collections.Generic.List[string]]::new()
$script:Opened = $false
$script:Answer = 'CANCEL'
function Assert-True($Value, $Message) { if (-not $Value) { throw $Message } }
function Assert-Throws([scriptblock]$Block, $Pattern) {
    try { & $Block } catch { if ($_.Exception.Message -match $Pattern) { return }; throw }
    throw "Expected failure: $Pattern"
}
function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $script:Calls.Add(($Arguments -join ' '))
    if ($Arguments[0] -eq 'context') { return 'npipe:////./pipe/docker_engine' }
    if ($Arguments[0] -eq 'info') { return 'linux' }
    if ($Arguments -contains 'ps') { return '' }
    return 'ok'
}
function Get-Command { return @{Name='docker'} }
function Invoke-RestMethod { return @{asr_backend='gigaam'} }
function Start-Process { $script:Opened = $true }
function Read-Host { return $script:Answer }
function Set-Clipboard { throw 'Clipboard should not be used after cancellation.' }
try {
    $null = New-Item -ItemType Directory -Path $Root
    Copy-Item "$OriginalRoot/.env.example" "$Root/.env.example"
    $token1 = New-Token
    $token2 = New-Token
    Assert-True ($token1 -match '^[A-Za-z0-9_-]{48}$' -and $token1 -ne $token2) 'Random distinct token generation failed.'
    $settings = @{APP_API_TOKEN=$token1; ASR_API_TOKEN=$token2; ASR_BACKEND='gigaam'; TEAMS_ENABLED='false'}
    Assert-Throws { Test-Prepared @{} } 'APP_API_TOKEN'
    Assert-Throws { Test-Prepared $settings } 'Model files are missing'
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $Root 'models/gigaam')
    [IO.File]::WriteAllText((Join-Path $Root 'models/gigaam/config.json'), '{}')
    Assert-Throws { Test-Prepared $settings } 'checksum mismatch'
    Assert-Throws { Get-Compose @{TEAMS_ENABLED='true'} } 'Teams setup is incomplete'
    Assert-Throws { Get-Compose @{TEAMS_ENABLED='1'} } 'requires TEAMS_ENABLED'
    Assert-Throws { Get-Compose @{TEAMS_ENABLED='"true"'} } 'requires TEAMS_ENABLED'
    Assert-Throws { Initialize-Service } 'cancelled'
    Assert-True (-not (Test-Path "$Root/.env")) 'Cancelled preparation wrote credentials.'
    $script:Answer = 'PREPARE'
    Initialize-Service
    $generated = Read-Settings
    Assert-True ($generated.APP_API_TOKEN -ne $generated.ASR_API_TOKEN) 'Prepared credentials are not distinct.'
    Assert-True ($generated.TEAMS_ENABLED -eq 'false') 'Preparation enabled Teams.'
    $snapshot = [IO.File]::ReadAllText("$Root/.env")
    Initialize-Service
    Assert-True ([IO.File]::ReadAllText("$Root/.env") -eq $snapshot) 'Preparation overwrote existing credentials.'
    # Start contract uses a stub for the separately covered real checksum preflight.
    function Test-Prepared { param($Settings) }
    $Action = 'Start'
    Invoke-Launcher
    Assert-True $script:Opened 'Start did not open the dashboard.'
    $startCall = @($script:Calls | Where-Object { $_ -match ' up ' })[-1]
    Assert-True ($startCall -match '--no-build --pull never platform') 'Start could download/build unexpectedly.'
    Assert-True ($startCall -notmatch 'compose.teams') 'Default start enabled Teams override.'
    $script:Answer = 'CANCEL'
    $Action = 'Token'
    Invoke-Launcher
    $Action = 'Stop'
    Invoke-Launcher
    Assert-True (-not @($script:Calls | Where-Object { $_ -match ' stop ' }).Count) 'Cancelled stop changed service.'
    $script:Answer = 'STOP'
    [IO.File]::WriteAllText((Join-Path $Root '.env'), 'TEAMS_ENABLED=true')
    Invoke-Launcher
    Assert-True (@($script:Calls | Where-Object { $_ -match ' stop platform$' }).Count -eq 1) 'Stop did not target only platform.'
    $env:DOCKER_HOST = 'tcp://remote.invalid:2375'
    Assert-Throws { Test-Docker } 'Remote Docker'
    Write-Host 'Launcher contract tests passed (mocked Docker; no live runtime or Teams test).'
} finally {
    Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Root -Recurse -Force
}
