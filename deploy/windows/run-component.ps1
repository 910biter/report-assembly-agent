param(
    [Parameter(Mandatory = $true)][ValidateSet('app', 'qdrant', 'bootstrap')][string]$Component,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$DataRoot
)

$ErrorActionPreference = 'Stop'
$logDir = Join-Path $DataRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
trap {
    $_ | Out-File -LiteralPath (Join-Path $logDir "$Component-errors.log") -Append
    exit 1
}
$config = Get-Content -LiteralPath (Join-Path $DataRoot 'config.json') -Raw | ConvertFrom-Json

if ($Component -eq 'qdrant') {
    $env:QDRANT__SERVICE__HOST = '127.0.0.1'
    $env:QDRANT__SERVICE__HTTP_PORT = [string]$config.qdrant_port
    $env:QDRANT__SERVICE__GRPC_PORT = '0'
    $env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $DataRoot 'qdrant\storage'
    $env:QDRANT__STORAGE__SNAPSHOTS_PATH = Join-Path $DataRoot 'qdrant\snapshots'
    $exe = Join-Path $InstallRoot 'qdrant\qdrant.exe'
    $arguments = @()
} else {
    $secrets = Get-Content -LiteralPath (Join-Path $DataRoot 'secrets.json') -Raw | ConvertFrom-Json
    $env:IRA_DB_URL = "postgresql+psycopg://ira:$($secrets.db_password)@127.0.0.1:$($config.postgres_port)/ira"
    $env:IRA_HOST = if ($config.app_host) { [string]$config.app_host } else { '127.0.0.1' }
    $env:IRA_PORT = [string]$config.app_port
    $env:IRA_QDRANT_URL = "http://127.0.0.1:$($config.qdrant_port)"
    $env:IRA_VECTOR_BACKEND = 'qdrant'
    $env:IRA_RUNTIME_ROOT = Join-Path $DataRoot 'runtime'
    $env:IRA_EMBEDDING_MODEL_PATH = Join-Path $InstallRoot 'models\embedding'
    $env:IRA_DOCLING_ARTIFACTS_PATH = Join-Path $InstallRoot 'models\docling'
    $env:IRA_MULTIUSER_ENABLED = 'true'
    $env:IRA_AUTH_HTTP_TEST_MODE = [string]$config.http_test_mode
    $env:IRA_HF_OFFLINE = 'true'
    $env:HF_HOME = Join-Path $InstallRoot 'models\hf'
    $env:USERPROFILE = Join-Path $InstallRoot 'profile'
    $env:PYTHONPATH = Join-Path $InstallRoot 'app-source'
    $ffmpeg = Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'ffmpeg') -Filter 'ffmpeg.exe' -File -Recurse |
        Select-Object -First 1
    if (-not $ffmpeg) { throw 'Bundled ffmpeg.exe is missing' }
    $env:PATH = "$($ffmpeg.DirectoryName);$env:PATH"
    $exe = Join-Path $InstallRoot 'venv\Scripts\python.exe'
    $arguments = @((Join-Path $InstallRoot 'app-source\run.py'))
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $tcp = [Net.Sockets.TcpClient]::new('127.0.0.1', [int]$config.postgres_port)
            $tcp.Dispose()
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$($config.qdrant_port)/healthz" -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { break }
        } catch {}
        Start-Sleep -Seconds 2
    }
    if ($attempt -eq 120) { throw 'PostgreSQL or Qdrant did not become ready' }
}

if ($Component -eq 'bootstrap') {
    $status = (& $exe -m app.auth.bootstrap --status | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Administrator status check failed' }
    if ($status -eq 'admin-ready') { return }
    if ($status -ne 'needs-bootstrap') { throw "Unexpected administrator status: $status" }
    $password = (Get-Content -LiteralPath (Join-Path $DataRoot 'initial-admin-password') -Raw).Trim()
    $password | & $exe -m app.auth.bootstrap admin --password-stdin
    if ($LASTEXITCODE -ne 0) { throw 'Administrator bootstrap failed' }
    return
}

while ($true) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $logDir "$Component-$stamp.out.log"
    Push-Location (Join-Path $InstallRoot 'app-source')
    try {
        # Native stderr carries normal server logs; the process exit code is authoritative.
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $exe @arguments *> $stdout
        $exitCode = $LASTEXITCODE
    } catch {
        $_ | Out-File -LiteralPath $stdout -Append
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousErrorAction
        Pop-Location
    }
    Add-Content -LiteralPath (Join-Path $logDir "$Component-exits.log") -Value "$(Get-Date -Format o) exit=$exitCode"
    Start-Sleep -Seconds 5
}
