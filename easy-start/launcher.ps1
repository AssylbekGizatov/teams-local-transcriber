param([ValidateSet('Start','Stop','Check','Prepare','Token')][string]$Action = 'Check')
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Image = 'teams-asr-platform:local'
$Url = 'http://127.0.0.1:8000'

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker operation failed. Ask IT to check Docker and the approved deployment.' }
}
function Read-Settings {
    $values = @{}
    if (Test-Path (Join-Path $Root '.env')) {
        foreach ($line in [IO.File]::ReadAllLines((Join-Path $Root '.env'))) {
            if ($line -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $values
}
function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker is not installed. This launcher installs nothing. Ask IT for a prepared host or the service URL.'
    }
    if ($env:DOCKER_HOST -and $env:DOCKER_HOST -notmatch '^(npipe|unix)://') { throw 'Remote Docker hosts are not supported by easy start. Ask IT for the service URL.' }
    $context = @(Invoke-Docker -Arguments @('context','inspect','--format','{{.Endpoints.docker.Host}}')) -join ''
    if ($context.Trim() -notmatch '^(npipe|unix)://') { throw 'Select an approved local Docker context with IT. This launcher does not change contexts.' }
    $os = @(Invoke-Docker -Arguments @('info','--format','{{.OSType}}')) -join ''
    if ($os.Trim() -ne 'linux') { throw 'A running, IT-approved Linux Docker engine is required.' }
    $null = Invoke-Docker -Arguments @('compose','version')
    Write-Host '[OK] Docker engine and Compose v2 are available.'
}
function Get-Compose {
    param($Settings)
    if ($Settings['TEAMS_ENABLED'] -notmatch '^(true|false)$') {
        throw 'Easy start requires TEAMS_ENABLED=true or TEAMS_ENABLED=false without quotes. Ask IT to normalize .env.'
    }
    $arguments = @('compose','--project-directory',$Root,'-f',(Join-Path $Root 'compose.yaml'))
    if ($Settings['TEAMS_ENABLED'] -eq 'true') {
        foreach ($key in @('TEAMS_AUTHORIZATION_REFERENCE','TEAMS_ALLOWED_MEETING_IDS','VEXA_API_KEY')) {
            if (-not $Settings[$key]) { throw "Teams setup is incomplete: $key. Ask IT; Teams will not be enabled automatically." }
        }
        $null = Invoke-Docker -Arguments @('network','inspect','teams-asr-vexa','--format','{{.Name}}')
        $arguments += @('-f',(Join-Path $Root 'deploy/compose.teams.yaml'))
        Write-Host '[INFO] IT-configured Teams integration selected. This does not verify a live Teams join.'
    }
    return $arguments + @('--profile','cpu')
}
function Test-Prepared {
    param($Settings)
    foreach ($key in @('APP_API_TOKEN','ASR_API_TOKEN')) {
        if ($Settings[$key] -notmatch '^[A-Za-z0-9_-]{32,}$') { throw "Missing or unsupported $key. Ask IT to prepare the .env file. No secret is printed." }
    }
    if ($Settings['APP_API_TOKEN'] -eq $Settings['ASR_API_TOKEN']) { throw 'Operator and ASR tokens must differ.' }
    if ($Settings['ASR_BACKEND'] -ne 'gigaam') { throw 'Easy start supports the bundled GigaAM CPU configuration only.' }
    $null = Invoke-Docker -Arguments @('image','inspect',$Image,'--format','{{.Id}}')
    Write-Host '[OK] Prepared local service image exists. Nothing will be pulled or built during Start.'
    $manifest = Get-Content -Raw (Join-Path $PSScriptRoot 'model-checksums.json') | ConvertFrom-Json
    foreach ($item in $manifest.PSObject.Properties) {
        $path = Join-Path $Root ('models/gigaam/' + $item.Name)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Model files are missing. Ask IT to run IT/PREPARE.cmd or transfer the verified model folder.' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value) {
            throw 'Model checksum mismatch. Do not start; ask IT to provision verified weights again.'
        }
    }
    Write-Host '[OK] Model files match the pinned SHA256 checksums.'
}
function New-Token {
    $bytes = New-Object byte[] 36
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
}
function Initialize-Service {
    Write-Host 'IT preparation requires an approved Docker host and internet access.'
    Write-Host 'This builds the service image (OS/Python packages, several GB) and downloads pinned public GigaAM weights (~883 MB) and license.'
    Write-Host 'Sources: Docker Hub, Debian, PyPI, download.pytorch.org, GitHub, huggingface.co and their download CDNs.'
    Write-Host 'New distinct credentials are saved in .env if absent. Use a private folder with IT-approved permissions.'
    Write-Host 'No Docker installation, elevation, policy change, firewall change or Teams activation is performed.'
    if ((Read-Host 'Type PREPARE to approve these downloads and local credential creation') -cne 'PREPARE') {
        throw 'Preparation cancelled. Nothing was prepared.'
    }
    if (-not (Test-Path (Join-Path $Root '.env'))) {
        $content = [IO.File]::ReadAllText((Join-Path $Root '.env.example'))
        $content = $content.Replace("APP_API_TOKEN=`n", "APP_API_TOKEN=$(New-Token)`n").Replace("ASR_API_TOKEN=`n", "ASR_API_TOKEN=$(New-Token)`n")
        # Normalize CRLF templates before replacement as well.
        if ($content -match '(?m)^APP_API_TOKEN=\r?$') {
            $content = $content -replace '(?m)^APP_API_TOKEN=\r?$', ('APP_API_TOKEN=' + (New-Token))
            $content = $content -replace '(?m)^ASR_API_TOKEN=\r?$', ('ASR_API_TOKEN=' + (New-Token))
        }
        $file = [IO.File]::Open((Join-Path $Root '.env'), [IO.FileMode]::CreateNew)
        try {
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes($content)
            $file.Write($bytes, 0, $bytes.Length)
        } finally { $file.Dispose() }
        Write-Host '[OK] Created .env. Keep it private; do not upload or email it.'
    }
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $Root 'models/gigaam')
    Invoke-Docker -Arguments @('build','-t',$Image,$Root)
    Invoke-Docker -Arguments @('run','--rm','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--mount',('type=bind,source=' + (Join-Path $Root 'models') + ',target=/models'),$Image,'python','scripts/download_models.py','--output','/models/gigaam')
    Write-Host '[OK] Preparation finished. Run CHECK.cmd, then START.cmd. Teams remains separately configured by IT.'
}
function Invoke-Launcher {
    if ($Action -eq 'Token') {
        $settings = Read-Settings
        if ($settings['APP_API_TOKEN'] -notmatch '^[A-Za-z0-9_-]{32,}$') { throw 'No prepared operator token. Ask IT.' }
        Write-Host 'This copies the operator credential to your clipboard. It grants access to this entire installation.'
        if ((Read-Host 'Type COPY to copy it; paste only in the local service login and then clear the clipboard') -cne 'COPY') { return }
        Set-Clipboard -Value $settings['APP_API_TOKEN']
        Write-Host 'Copied. Open START.cmd and paste into the login field. Do not paste into chat, URLs or screenshots.'
        return
    }
    Test-Docker
    if ($Action -eq 'Prepare') { Initialize-Service; return }
    $settings = Read-Settings
    if ($Action -eq 'Stop') {
        Write-Host 'Stop active Teams sessions in the dashboard first and verify the bot has left Teams.'
        Write-Host 'Stopping this service does not stop the separate Vexa stack and can leave a meeting bot running.'
        if ((Read-Host 'Type STOP after checking active sessions (including needs_review)') -cne 'STOP') { return }
        # Shutdown must remain possible when Teams configuration/network is damaged.
        $compose = @('compose','--project-directory',$Root,'-f',(Join-Path $Root 'compose.yaml'),'--profile','cpu')
        Invoke-Docker -Arguments ($compose + @('stop','platform'))
        Write-Host '[OK] Platform stopped. Data volume and model files retained.'
        return
    }
    $compose = @(Get-Compose $settings)
    Test-Prepared $settings
    $containers = @(Invoke-Docker -Arguments ($compose + @('ps','--all','--format','json')))
    # Refuse to interfere with a separately running GPU deployment on the same port/queue.
    foreach ($line in $containers) {
        if (-not $line.Trim()) { continue }
        foreach ($container in @($line | ConvertFrom-Json)) {
            if ($container.Service -eq 'platform-gpu' -and $container.State -eq 'running') {
                throw 'GPU profile is running. Use the deployment selected by IT; do not start the CPU profile.'
            }
        }
    }
    if ($Action -eq 'Check') {
        Write-Host '[OK] Static launch prerequisites passed. This is not a neural inference or live Teams test.'
        return
    }
    # No build/pull: a restricted host never downloads dependencies implicitly.
    Invoke-Docker -Arguments ($compose + @('up','-d','--no-build','--pull','never','platform'))
    Write-Host 'Waiting for the authenticated dashboard (up to 90 seconds)...'
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $config = Invoke-RestMethod -Uri "$Url/api/config" -Headers @{Authorization=('Bearer ' + $settings['APP_API_TOKEN'])} -TimeoutSec 2
            if ($config.asr_backend -eq 'gigaam') { $ready = $true; break }
        } catch { Start-Sleep -Seconds 3 }
    }
    if (-not $ready) { throw 'Service did not become available. It may still be starting. Run CHECK.cmd or ask IT; do not send .env or full Docker logs containing private meeting data.' }
    Write-Host '[OK] Dashboard is available at http://127.0.0.1:8000. First login: use LOGIN-TOKEN.cmd.'
    Write-Host 'Check the readiness panel, then test an authorized audio file. A live Teams meeting has not been verified by this launcher.'
    Start-Process $Url
}
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-Launcher; exit 0 }
    catch { Write-Host ('[BLOCKED] ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
}
