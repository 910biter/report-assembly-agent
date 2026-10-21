param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'ReportAssemblyAgent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'ReportAssemblyAgent'),
    [int]$AppPort = 8000,
    [int]$PostgresPort = 15432,
    [int]$QdrantPort = 16333,
    [switch]$InstallerManagedPayload
)

$ErrorActionPreference = 'Stop'
$bundle = $PSScriptRoot
$payload = Join-Path $bundle 'payload'
$firewallRuleName = 'IRA-App-Inbound'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
        '-InstallRoot', "`"$InstallRoot`"", '-DataRoot', "`"$DataRoot`"",
        '-AppPort', $AppPort, '-PostgresPort', $PostgresPort, '-QdrantPort', $QdrantPort)
    $elevated = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $elevated.ExitCode
}
$installLog = Join-Path $bundle 'install-native.log'
Start-Transcript -LiteralPath $installLog -Force | Out-Null
trap {
    Write-Output ("Installation failed: " + $_.Exception.Message)
    Write-Output ($_.ScriptStackTrace)
    Stop-Transcript | Out-Null
    exit 1
}
function Write-InstallProgress([int]$step) {
    Write-Output "IRA_PROGRESS|$step"
}
Write-InstallProgress 0
$windowsBuild = [int](Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name 'CurrentBuildNumber')
if (-not [Environment]::Is64BitOperatingSystem -or $windowsBuild -lt 10240) {
    throw 'Windows 10/11 x64 is required'
}
if (-not (Test-Path -LiteralPath (Join-Path $bundle 'manifest.json'))) { throw 'Bundle manifest is missing' }
$manifest = Get-Content -LiteralPath (Join-Path $bundle 'manifest.json') -Raw | ConvertFrom-Json
foreach ($item in $manifest.component_sha256.PSObject.Properties) {
    Write-Output "IRA_FILE|$($item.Name)"
    $file = Join-Path $payload $item.Name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing payload: $($item.Name)" }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    if ($actual -ne $item.Value) { throw "Checksum mismatch: $($item.Name)" }
}
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
if ($InstallerManagedPayload) {
    if ([IO.Path]::GetFullPath($bundle).TrimEnd('\') -ne $InstallRoot.TrimEnd('\')) {
        throw 'Installer-managed payload must be installed in its current application directory'
    }
} elseif (Test-Path -LiteralPath $InstallRoot) {
    throw "Installation already exists: $InstallRoot"
}
if (Test-Path -LiteralPath $DataRoot) { throw "Data directory already exists: $DataRoot" }
foreach ($port in @($AppPort, $PostgresPort, $QdrantPort)) {
    if ($port -lt 1024 -or $port -gt 65535) { throw "Invalid port: $port" }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use"
    }
}
foreach ($name in @('IRA-PostgreSQL', 'IRA-Qdrant', 'IRA-App')) {
    if ((Get-Service -Name $name -ErrorAction SilentlyContinue) -or
        (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
        throw "Existing service or task conflicts with $name"
    }
}
if (Get-NetFirewallRule -Name $firewallRuleName -ErrorAction SilentlyContinue) {
    throw "Existing firewall rule conflicts with $firewallRuleName"
}

Write-InstallProgress 1
New-Item -ItemType Directory -Path $InstallRoot, $DataRoot -Force | Out-Null
# Program code must not be writable by ordinary local users, even for a custom path.
& icacls.exe $InstallRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' `
    '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the program directory ACL' }
# Data includes database passwords and must not inherit broad user access.
$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $DataRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' `
    '*S-1-5-32-544:(OI)(CI)F' ('*{0}:(OI)(CI)F' -f $installerSid) | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the data directory ACL' }
foreach ($name in @('python', 'models', 'profile', 'app-source')) {
    $source = Join-Path $payload $name
    if ($InstallerManagedPayload) {
        Move-Item -LiteralPath $source -Destination $InstallRoot
    } else {
        Copy-Item -LiteralPath $source -Destination (Join-Path $InstallRoot $name) -Recurse -Force
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'run-component.ps1'))) {
    Copy-Item -LiteralPath (Join-Path $bundle 'run-component.ps1') -Destination $InstallRoot
}
Write-InstallProgress 2
Expand-Archive -LiteralPath (Join-Path $payload 'postgresql.zip') -DestinationPath (Join-Path $InstallRoot 'postgresql')
Write-InstallProgress 3
Expand-Archive -LiteralPath (Join-Path $payload 'qdrant.zip') -DestinationPath (Join-Path $InstallRoot 'qdrant')
Write-InstallProgress 4
Expand-Archive -LiteralPath (Join-Path $payload 'ffmpeg.zip') -DestinationPath (Join-Path $InstallRoot 'ffmpeg')
Write-InstallProgress 5
$python = Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'python') -Filter 'python.exe' -File -Recurse |
    Where-Object { $_.DirectoryName -notmatch '[\\/]Scripts$' } | Select-Object -First 1 -ExpandProperty FullName
if (-not $python) { throw 'Private Python is missing' }
$venv = Join-Path $InstallRoot 'venv'
& (Join-Path $payload 'uv.exe') venv --python $python $venv
if ($LASTEXITCODE -ne 0) { throw 'Private venv creation failed' }
$venvPython = Join-Path $venv 'Scripts\python.exe'
& (Join-Path $payload 'uv.exe') pip install --python $venvPython --no-index `
    --find-links (Join-Path $payload 'wheels') -r (Join-Path $payload 'requirements-windows.lock') --quiet
if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed' }
& (Join-Path $payload 'uv.exe') pip check --python $venvPython --offline
if ($LASTEXITCODE -ne 0) { throw 'Dependency integrity check failed' }
# Moved payloads can keep permissive ACLs. The interpreter, installed libraries,
# model files and application source all belong to the same trusted program tree.
& icacls.exe $InstallRoot /inheritance:r /grant:r `
    '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the installed program ACL' }

$config = [ordered]@{
    app_host = '0.0.0.0'
    app_port = $AppPort
    postgres_port = $PostgresPort
    qdrant_port = $QdrantPort
    http_test_mode = $true
}
$config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DataRoot 'config.json') -Encoding UTF8
function New-RandomHex([int]$length) {
    $bytes = New-Object byte[] $length
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '')
}
$postgresPassword = New-RandomHex 24
$dbPassword = New-RandomHex 24
@{ postgres_password = $postgresPassword; db_password = $dbPassword } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DataRoot 'secrets.json') -Encoding UTF8
$adminPassword = New-RandomHex 20
$adminPassword | Set-Content -LiteralPath (Join-Path $DataRoot 'initial-admin-password') -Encoding ASCII
New-Item -ItemType Directory -Path (Join-Path $DataRoot 'runtime'), (Join-Path $DataRoot 'qdrant') -Force | Out-Null

$pgBin = Join-Path $InstallRoot 'postgresql\pgsql\bin'
Write-InstallProgress 6
$pgData = Join-Path $DataRoot 'postgresql'
$pgPasswordFile = Join-Path $DataRoot 'postgres-password.txt'
$postgresPassword | Set-Content -LiteralPath $pgPasswordFile -Encoding ASCII
& (Join-Path $pgBin 'initdb.exe') -D $pgData -U postgres -A scram-sha-256 --pwfile $pgPasswordFile --encoding UTF8
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed' }
& (Join-Path $pgBin 'pg_ctl.exe') register -N 'IRA-PostgreSQL' -D $pgData -S auto `
    -o "-p $PostgresPort -h 127.0.0.1"
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL service registration failed' }
Start-Service -Name 'IRA-PostgreSQL'
$env:PGPASSWORD = $postgresPassword
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    & (Join-Path $pgBin 'pg_isready.exe') -h 127.0.0.1 -p $PostgresPort -U postgres | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
if ($attempt -eq 60) { throw 'PostgreSQL did not become ready' }
& (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
    -v ON_ERROR_STOP=1 -c "CREATE ROLE ira LOGIN PASSWORD '$dbPassword'"
if ($LASTEXITCODE -ne 0) { throw 'Application database role creation failed' }
& (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
    -v ON_ERROR_STOP=1 -c 'CREATE DATABASE ira OWNER ira'
if ($LASTEXITCODE -ne 0) { throw 'Application database creation failed' }
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

$actionBase = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    "`"$(Join-Path $InstallRoot 'run-component.ps1')`"")
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$taskSettings = New-ScheduledTaskSettingsSet -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Write-InstallProgress 7
foreach ($component in @('qdrant', 'app')) {
    if ($component -eq 'app') {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallRoot 'run-component.ps1') `
            -Component bootstrap -InstallRoot $InstallRoot -DataRoot $DataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Administrator bootstrap failed' }
    }
    $taskName = if ($component -eq 'app') { 'IRA-App' } else { 'IRA-Qdrant' }
    $arguments = $actionBase + @('-Component', $component, '-InstallRoot', "`"$InstallRoot`"",
        '-DataRoot', "`"$DataRoot`"")
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ($arguments -join ' ')
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $taskSettings | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-InstallProgress 8
    $port = if ($component -eq 'app') { $AppPort } else { $QdrantPort }
    $path = if ($component -eq 'app') { '/api/health' } else { '/healthz' }
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port$path" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { break }
        } catch {}
        Start-Sleep -Seconds 2
    }
    if ($attempt -eq 120) { throw "$taskName did not become ready; inspect $DataRoot\logs" }
}
New-NetFirewallRule -Name $firewallRuleName -DisplayName 'IRA application' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $AppPort `
    -Profile Any -ErrorAction Stop | Out-Null
if ($InstallerManagedPayload) {
    Remove-Item -LiteralPath $payload -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $InstallRoot 'Install.cmd') -Force -ErrorAction SilentlyContinue
}
Write-Output "Installed and healthy: http://127.0.0.1:$AppPort"
Write-Output "Inbound application access is enabled on port $AppPort from any remote address."
Write-Output "Initial admin username: admin; password file: $DataRoot\initial-admin-password"
Write-Output 'HTTP login is unencrypted. Configure HTTPS before exposing the port beyond a trusted network.'
Write-InstallProgress 9
Stop-Transcript | Out-Null
