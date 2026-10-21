param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)][string]$PostgresArchive,
    [Parameter(Mandatory = $true)][string]$QdrantArchive,
    [Parameter(Mandatory = $true)][string]$FfmpegArchive,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$CreateZip,
    [Parameter(Mandatory = $true)][string]$LicenseBuildPython
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).ProviderPath
$licensePython = (Resolve-Path -LiteralPath $LicenseBuildPython).ProviderPath
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ((Test-Path -LiteralPath $output) -and (Get-ChildItem -LiteralPath $output -Force | Select-Object -First 1)) {
    throw "Output directory must be empty: $output"
}
foreach ($path in @($PostgresArchive, $QdrantArchive, $FfmpegArchive)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing archive: $path" }
}
foreach ($path in @('python', 'wheels', 'models', 'profile', 'requirements-windows.lock', 'uv.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtime $path))) { throw "Missing runtime item: $path" }
}

$payload = Join-Path $output 'payload'
New-Item -ItemType Directory -Path $payload -Force | Out-Null
foreach ($name in @('python', 'wheels', 'models', 'profile')) {
    Copy-Item -LiteralPath (Join-Path $runtime $name) -Destination (Join-Path $payload $name) -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $runtime 'requirements-windows.lock') -Destination $payload
Copy-Item -LiteralPath (Join-Path $runtime 'uv.exe') -Destination $payload
foreach ($item in @(
    @{ Source = $PostgresArchive; Name = 'postgresql.zip' },
    @{ Source = $QdrantArchive; Name = 'qdrant.zip' },
    @{ Source = $FfmpegArchive; Name = 'ffmpeg.zip' }
)) {
    Copy-Item -LiteralPath $item.Source -Destination (Join-Path $payload $item.Name)
}

$app = Join-Path $payload 'app-source'
New-Item -ItemType Directory -Path $app | Out-Null
foreach ($name in @('app', 'web')) {
    & robocopy.exe (Join-Path $repo $name) (Join-Path $app $name) /E /XD __pycache__ /XF *.pyc *.so *.pyd /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Source copy failed: $name" }
}
Copy-Item -LiteralPath (Join-Path $repo 'run.py') -Destination $app

# Compile on the build host only, using the exact bundled Python ABI.
$targetPython = Get-ChildItem -LiteralPath (Join-Path $payload 'python') -Filter python.exe -File -Recurse |
    Where-Object { $_.DirectoryName -notmatch '[\\/]Scripts$' } | Select-Object -First 1 -ExpandProperty FullName
$abiCode = 'import sys,sysconfig,platform; print(sys.implementation.cache_tag,sysconfig.get_config_var("EXT_SUFFIX"),platform.machine())'
$targetAbi = & $targetPython -c $abiCode
if ($LASTEXITCODE -ne 0) { throw 'Cannot read target Python ABI' }
$buildAbi = & $licensePython -c $abiCode
if ($LASTEXITCODE -ne 0 -or $buildAbi -ne $targetAbi) { throw 'License build Python must match bundled Python version and architecture' }
& $licensePython (Join-Path $repo 'tools\build_license_native.py') --output $app
if ($LASTEXITCODE -ne 0) { throw 'Native license build failed; install MSVC and tools/license-build-requirements.txt on build host' }
$licenseDirectory = (Resolve-Path -LiteralPath (Join-Path $app 'app\licensing')).ProviderPath
foreach ($name in @('_trust.pyx', 'trusted_keys.json', 'protocol.py', 'service.py', 'storage.py')) {
    $path = Join-Path $licenseDirectory $name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}

foreach ($name in @('install-native.ps1', 'run-component.ps1', 'uninstall-native.ps1', 'Install.cmd', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $output $name)
}
$hashes = @{}
foreach ($name in @('postgresql.zip', 'qdrant.zip', 'ffmpeg.zip', 'requirements-windows.lock', 'uv.exe')) {
    $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $payload $name) -Algorithm SHA256).Hash
}
$manifest = [ordered]@{
    format = 1
    architecture = 'windows-x64'
    source_commit = (& git -C $repo rev-parse HEAD).Trim()
    component_sha256 = $hashes
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'manifest.json') -Encoding UTF8
if ($CreateZip) {
    $zip = "$output.zip"
    if (Test-Path -LiteralPath $zip) { throw "Refusing to replace existing package: $zip" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($output, $zip, [IO.Compression.CompressionLevel]::Fastest, $false)
    Write-Output "Offline package: $zip"
}
Write-Output "Bundle staging directory: $output"
