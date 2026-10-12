$ErrorActionPreference = 'Stop'
$bundle = $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x86_64 is required' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop with the WSL 2 Linux-container backend first' }
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable' }
$envFile = Join-Path $bundle '.env'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'Missing .env' }
if ((Get-Content -LiteralPath $envFile) -contains 'POSTGRES_PASSWORD=replace-with-a-strong-password') {
    throw 'Set a unique POSTGRES_PASSWORD in .env before installation'
}
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
}
finally { Pop-Location }
