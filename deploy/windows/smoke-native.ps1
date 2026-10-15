param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$PostgresDirectory,
    [Parameter(Mandatory = $true)][string]$QdrantDirectory,
    [Parameter(Mandatory = $true)][string]$TestDirectory,
    [int]$PostgresPort = 25432,
    [int]$QdrantPort = 26333,
    [int]$AppPort = 28000
)

$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $BundleDirectory).ProviderPath
$pgBin = Join-Path (Resolve-Path -LiteralPath $PostgresDirectory).ProviderPath 'pgsql\bin'
$qdrantExe = Join-Path (Resolve-Path -LiteralPath $QdrantDirectory).ProviderPath 'qdrant.exe'
$test = [IO.Path]::GetFullPath($TestDirectory)
if (Test-Path -LiteralPath $test) { throw "Smoke directory must not exist: $test" }
foreach ($port in @($PostgresPort, $QdrantPort, $AppPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Test port $port is already in use"
    }
}
New-Item -ItemType Directory -Path $test -Force | Out-Null
$pgData = Join-Path $test 'postgresql'
$qdrantData = Join-Path $test 'qdrant'
$pgStarted = $false
$qdrant = $null
$app = $null
try {
    $password = 'SmokeOnly123'
    $passwordFile = Join-Path $test 'postgres-password'
    $password | Set-Content -LiteralPath $passwordFile -Encoding ASCII
    & (Join-Path $pgBin 'initdb.exe') -D $pgData -U postgres -A scram-sha-256 --pwfile $passwordFile --encoding UTF8 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Smoke PostgreSQL initdb failed' }
    $pgArgs = @('-D', "`"$pgData`"", '-o', "`"-p $PostgresPort -h 127.0.0.1`"",
        '-l', "`"$(Join-Path $test 'postgres.log')`"", '-w', 'start')
    $pgCtl = Start-Process -FilePath (Join-Path $pgBin 'pg_ctl.exe') -ArgumentList $pgArgs `
        -WorkingDirectory $test -PassThru -RedirectStandardOutput (Join-Path $test 'pgctl.out.log') `
        -RedirectStandardError (Join-Path $test 'pgctl.err.log')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & (Join-Path $pgBin 'pg_isready.exe') -h 127.0.0.1 -p $PostgresPort -U postgres | Out-Null
        if ($LASTEXITCODE -eq 0) { $pgStarted = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $pgStarted) { throw "Smoke PostgreSQL failed to start; pg_ctl exit=$($pgCtl.ExitCode)" }
    $env:PGPASSWORD = $password
    & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
        -v ON_ERROR_STOP=1 -c "CREATE ROLE ira LOGIN PASSWORD '$password'" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Smoke database role creation failed' }
    & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
        -v ON_ERROR_STOP=1 -c 'CREATE DATABASE ira OWNER ira' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Smoke database creation failed' }
    Remove-Item Env:\PGPASSWORD

    $env:QDRANT__SERVICE__HOST = '127.0.0.1'
    $env:QDRANT__SERVICE__HTTP_PORT = [string]$QdrantPort
    $env:QDRANT__SERVICE__GRPC_PORT = '0'
    $env:QDRANT__STORAGE__STORAGE_PATH = $qdrantData
    $qdrant = Start-Process -FilePath $qdrantExe -WorkingDirectory $test -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $test 'qdrant.out.log') `
        -RedirectStandardError (Join-Path $test 'qdrant.err.log')
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$QdrantPort/healthz" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if ($attempt -eq 60) { throw 'Smoke Qdrant failed to become ready' }

    $payload = Join-Path $bundle 'payload'
    $python = Get-ChildItem -LiteralPath (Join-Path $payload 'python') -Filter python.exe -File -Recurse |
        Where-Object { $_.DirectoryName -notmatch '[\\/]Scripts$' } | Select-Object -First 1 -ExpandProperty FullName
    $venv = Join-Path $test 'venv'
    & (Join-Path $payload 'uv.exe') venv --python $python $venv
    if ($LASTEXITCODE -ne 0) { throw 'Smoke venv creation failed' }
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    & (Join-Path $payload 'uv.exe') pip install --python $venvPython --no-index `
        --find-links (Join-Path $payload 'wheels') -r (Join-Path $payload 'requirements-windows.lock') --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Smoke offline dependency install failed' }
    $env:PYTHONPATH = Join-Path $payload 'app-source'
    $env:IRA_DB_URL = "postgresql+psycopg://ira:$password@127.0.0.1:$PostgresPort/ira"
    $env:IRA_HOST = '127.0.0.1'
    $env:IRA_PORT = [string]$AppPort
    $env:IRA_QDRANT_URL = "http://127.0.0.1:$QdrantPort"
    $env:IRA_VECTOR_BACKEND = 'qdrant'
    $env:IRA_RUNTIME_ROOT = Join-Path $test 'runtime'
    $env:IRA_EMBEDDING_MODEL_PATH = Join-Path $payload 'models\embedding'
    $env:IRA_DOCLING_ARTIFACTS_PATH = Join-Path $payload 'models\docling'
    $env:IRA_MULTIUSER_ENABLED = 'true'
    $env:IRA_AUTH_HTTP_TEST_MODE = 'true'
    $env:IRA_HF_OFFLINE = 'true'
    $env:HF_HOME = Join-Path $payload 'models\hf'
    $env:USERPROFILE = Join-Path $payload 'profile'
    'SmokeAdmin123456' | & $venvPython -m app.auth.bootstrap admin --password-stdin
    if ($LASTEXITCODE -ne 0) { throw 'Smoke administrator bootstrap failed' }
    $app = Start-Process -FilePath $venvPython -ArgumentList (Join-Path $payload 'app-source\run.py') `
        -WorkingDirectory (Join-Path $payload 'app-source') -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $test 'app.out.log') `
        -RedirectStandardError (Join-Path $test 'app.err.log')
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$AppPort/api/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { break }
        } catch {}
        if ($app.HasExited) { throw "Smoke application exited: $($app.ExitCode)" }
        Start-Sleep -Seconds 2
    }
    if ($attempt -eq 90) { throw 'Smoke application failed to become ready' }
    Write-Output "Native smoke passed: PostgreSQL, Qdrant, app, admin bootstrap on $AppPort"
} finally {
    if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id -Force }
    if ($qdrant -and -not $qdrant.HasExited) { Stop-Process -Id $qdrant.Id -Force }
    if ($pgStarted) { & (Join-Path $pgBin 'pg_ctl.exe') -D $pgData -m fast -w stop | Out-Null }
}
