param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'ReportAssemblyAgent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'ReportAssemblyAgent'),
    [int]$AppPort = 8000,
    [int]$PostgresPort = 15432,
    [int]$QdrantPort = 16333,
    [switch]$ReuseData
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
    if ($ReuseData) { $arguments += '-ReuseData' }
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
if ($InstallRoot.TrimEnd('\').Equals($DataRoot.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
    $InstallRoot.TrimEnd('\').Equals([IO.Path]::GetPathRoot($InstallRoot).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
    $DataRoot.TrimEnd('\').StartsWith($InstallRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $InstallRoot.TrimEnd('\').StartsWith($DataRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Program and data directories must be separate non-root paths'
}
if (Test-Path -LiteralPath $InstallRoot) {
    throw "Installation already exists: $InstallRoot"
}
if ($ReuseData) {
    foreach ($name in @('config.json', 'secrets.json', 'postgresql\PG_VERSION', 'runtime', 'qdrant')) {
        if (-not (Test-Path -LiteralPath (Join-Path $DataRoot $name))) {
            throw "Existing data is incomplete: $name"
        }
    }
    $existingConfig = Get-Content -LiteralPath (Join-Path $DataRoot 'config.json') -Raw | ConvertFrom-Json
    if ([int]$existingConfig.app_port -ne $AppPort -or
        [int]$existingConfig.postgres_port -ne $PostgresPort -or
        [int]$existingConfig.qdrant_port -ne $QdrantPort) {
        throw 'Existing data ports differ from installation ports'
    }
    $existingSecrets = Get-Content -LiteralPath (Join-Path $DataRoot 'secrets.json') -Raw | ConvertFrom-Json
    if (-not $existingSecrets.db_password -or -not $existingSecrets.postgres_password) {
        throw 'Existing database credentials are incomplete'
    }
    if (Test-Path -LiteralPath (Join-Path $DataRoot 'postgresql\postmaster.pid')) {
        throw 'Existing PostgreSQL data appears to be in use'
    }
} elseif (Test-Path -LiteralPath $DataRoot) {
    throw "Data directory already exists: $DataRoot; choose reuse explicitly"
}
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
if ($ReuseData) {
    $backup = "$DataRoot-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    if (Test-Path -LiteralPath $backup) { throw "Backup path already exists: $backup" }
    $sourceFiles = @(Get-ChildItem -LiteralPath $DataRoot -File -Recurse -Force)
    $sourceBytes = ($sourceFiles | Measure-Object -Property Length -Sum).Sum
    $dataDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($DataRoot).Substring(0, 1))
    if ($dataDrive.Free -lt ($sourceBytes + 1GB)) { throw 'Insufficient space for a protected data backup' }
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    $installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $backup /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' `
        '*S-1-5-32-544:(OI)(CI)F' ('*{0}:(OI)(CI)F' -f $installerSid) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the data backup' }
    Copy-Item -Path (Join-Path $DataRoot '*') -Destination $backup -Recurse -Force
    & icacls.exe (Join-Path $backup '*') /reset /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the copied data backup' }
    $backupFiles = @(Get-ChildItem -LiteralPath $backup -File -Recurse -Force)
    if ($backupFiles.Count -ne $sourceFiles.Count -or
        ($backupFiles | Measure-Object -Property Length -Sum).Sum -ne $sourceBytes) {
        throw 'Data backup verification failed'
    }
    Write-Output "Existing data backed up to: $backup"
} else {
    New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
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
    Write-Output "IRA_DETAIL|copy:$name"
    Copy-Item -LiteralPath $source -Destination (Join-Path $InstallRoot $name) -Recurse -Force
}
foreach ($name in @('run-component.ps1', 'uninstall-native.ps1', 'remove-native.ps1')) {
    Copy-Item -LiteralPath (Join-Path $bundle $name) -Destination $InstallRoot
}
Write-InstallProgress 2
Write-Output 'IRA_DETAIL|extract:PostgreSQL'
Expand-Archive -LiteralPath (Join-Path $payload 'postgresql.zip') -DestinationPath (Join-Path $InstallRoot 'postgresql')
Write-InstallProgress 3
Write-Output 'IRA_DETAIL|extract:Qdrant'
Expand-Archive -LiteralPath (Join-Path $payload 'qdrant.zip') -DestinationPath (Join-Path $InstallRoot 'qdrant')
Write-InstallProgress 4
Write-Output 'IRA_DETAIL|extract:FFmpeg'
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
# Reset copied children to inherit the protected root ACL. Applying /inheritance:r
# recursively would leave regular files with an empty DACL on Windows.
& icacls.exe (Join-Path $InstallRoot '*') /reset /T /C | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the installed program ACL' }

function New-RandomHex([int]$length) {
    $bytes = New-Object byte[] $length
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '')
}
if ($ReuseData) {
    $postgresPassword = [string]$existingSecrets.postgres_password
    $dbPassword = [string]$existingSecrets.db_password
} else {
    $config = [ordered]@{
        app_host = '0.0.0.0'
        app_port = $AppPort
        postgres_port = $PostgresPort
        qdrant_port = $QdrantPort
        http_test_mode = $true
    }
    $config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DataRoot 'config.json') -Encoding UTF8
    $postgresPassword = New-RandomHex 24
    $dbPassword = New-RandomHex 24
    @{ postgres_password = $postgresPassword; db_password = $dbPassword } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DataRoot 'secrets.json') -Encoding UTF8
    $adminPassword = New-RandomHex 20
    $adminPassword | Set-Content -LiteralPath (Join-Path $DataRoot 'initial-admin-password') -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $DataRoot 'runtime'), (Join-Path $DataRoot 'qdrant') -Force | Out-Null
}

$pgBin = Join-Path $InstallRoot 'postgresql\pgsql\bin'
Write-InstallProgress 6
$pgData = Join-Path $DataRoot 'postgresql'
$pgPasswordFile = Join-Path $DataRoot 'postgres-password.txt'
if ($ReuseData) {
    $pgMajor = (Get-Content -LiteralPath (Join-Path $pgData 'PG_VERSION') -Raw).Trim()
    $binaryVersion = (& (Join-Path $pgBin 'postgres.exe') --version | Out-String).Trim()
    if ($binaryVersion -notmatch "PostgreSQL\) $([regex]::Escape($pgMajor))\.") {
        throw "PostgreSQL major version mismatch: data=$pgMajor binary=$binaryVersion"
    }
} else {
    $postgresPassword | Set-Content -LiteralPath $pgPasswordFile -Encoding ASCII
    & (Join-Path $pgBin 'initdb.exe') -D $pgData -U postgres -A scram-sha-256 --pwfile $pgPasswordFile --encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL initialization failed' }
}
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
if (-not $ReuseData) {
    & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
        -v ON_ERROR_STOP=1 -c "CREATE ROLE ira LOGIN PASSWORD '$dbPassword'"
    if ($LASTEXITCODE -ne 0) { throw 'Application database role creation failed' }
    & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p $PostgresPort -U postgres -d postgres `
        -v ON_ERROR_STOP=1 -c 'CREATE DATABASE ira OWNER ira'
    if ($LASTEXITCODE -ne 0) { throw 'Application database creation failed' }
}
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
$uninstallKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ReportAssemblyAgent'
New-Item -Path $uninstallKey -Force | Out-Null
$uninstallCommand = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
    (Join-Path $InstallRoot 'remove-native.ps1') + '" -InstallRoot "' + $InstallRoot +
    '" -DataRoot "' + $DataRoot + '"'
New-ItemProperty -Path $uninstallKey -Name DisplayName -Value 'AI 报告整编工作台' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value '1.0.2' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name Publisher -Value 'Report Assembly Agent' -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
New-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $InstallRoot -PropertyType String -Force | Out-Null
$shortcut = Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'AI 报告整编工作台.url'
"[InternetShortcut]`r`nURL=http://127.0.0.1:$AppPort/`r`n" | Set-Content -LiteralPath $shortcut -Encoding ASCII
Write-Output "Installed and healthy: http://127.0.0.1:$AppPort"
Write-Output "Inbound application access is enabled on port $AppPort from any remote address."
Write-Output "Initial admin username: admin; password file: $DataRoot\initial-admin-password"
Write-Output 'HTTP login is unencrypted. Configure HTTPS before exposing the port beyond a trusted network.'
Write-InstallProgress 9
Stop-Transcript | Out-Null
exit 0
