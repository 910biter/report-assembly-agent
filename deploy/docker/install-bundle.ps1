$ErrorActionPreference = 'Stop'
$bundle = $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x86_64 is required' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop with the WSL 2 Linux-container backend first' }
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable' }
$serverOs = (& docker info --format '{{.OSType}}' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running. Start it and retry' }
if ($serverOs -ne 'linux') { throw 'Switch Docker Desktop to Linux containers and retry' }
$envFile = Join-Path $bundle '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Missing .env' }
foreach ($line in Get-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS')) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw 'Invalid checksum manifest' }
    $expected = $Matches[1]
    $relative = $Matches[2].Replace('/', '\')
    $path = Join-Path $bundle $relative
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing bundle file: $relative" }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw "Checksum mismatch: $relative"
    }
}
if ((Get-Content -LiteralPath $envFile) -contains 'POSTGRES_PASSWORD=replace-with-a-strong-password') {
    $randomBytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($randomBytes) } finally { $random.Dispose() }
    $dbPassword = [BitConverter]::ToString($randomBytes).Replace('-', '').ToLowerInvariant()
    $content = (Get-Content -LiteralPath $envFile -Raw).Replace(
        'POSTGRES_PASSWORD=replace-with-a-strong-password',
        "POSTGRES_PASSWORD=$dbPassword"
    )
    Set-Content -LiteralPath $envFile -Value $content -NoNewline
}
New-Item -ItemType Directory -Path (Join-Path $bundle 'data/app') -Force | Out-Null
Push-Location $bundle
try {
    & docker load -i images.tar.gz
    if ($LASTEXITCODE -ne 0) { throw 'Docker image import failed' }
    & docker compose --env-file .env config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration invalid' }
    $adminStatus = (& docker compose --env-file .env run --rm -T app python -m app.auth.bootstrap --status | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Administrator status check failed' }
    if ($adminStatus -eq 'needs-bootstrap') {
        $passwordFile = Join-Path $bundle 'initial-admin-password'
        if (-not (Test-Path -LiteralPath $passwordFile)) {
            $randomBytes = New-Object byte[] 24
            $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
            try { $random.GetBytes($randomBytes) } finally { $random.Dispose() }
            $password = [BitConverter]::ToString($randomBytes).Replace('-', '').ToLowerInvariant()
            Set-Content -LiteralPath $passwordFile -Value $password -NoNewline
            $access = Get-Acl -LiteralPath $passwordFile
            $access.SetAccessRuleProtection($true, $false)
            $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
            $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
                $identity,
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                [System.Security.AccessControl.AccessControlType]::Allow
            )
            $access.SetAccessRule($rule)
            Set-Acl -LiteralPath $passwordFile -AclObject $access
        }
        $password = (Get-Content -LiteralPath $passwordFile -Raw).TrimEnd([char[]]@([char]13, [char]10))
        $password | & docker compose --env-file .env run --rm -T app python -m app.auth.bootstrap admin --password-stdin
        if ($LASTEXITCODE -ne 0) { throw 'Administrator bootstrap failed' }
        Write-Output "Administrator: admin; initial password: $passwordFile"
    } elseif ($adminStatus -ne 'admin-ready') {
        throw "Unexpected administrator state: $adminStatus"
    }
    & docker compose --env-file .env up -d --pull never
    if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed' }
    & docker compose --env-file .env ps
    if ($LASTEXITCODE -ne 0) { throw 'Compose status check failed' }
    $port = 8000
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^APP_PORT=(\d+)$') { $port = [int]$Matches[1] }
    }
    $healthUrl = "http://127.0.0.1:$port/api/health"
    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 5 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $healthy = $true; break }
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $healthy) { throw "Application did not become healthy: $healthUrl. Check docker compose logs app" }
    Write-Output "Application ready: http://127.0.0.1:$port"
}
finally { Pop-Location }
