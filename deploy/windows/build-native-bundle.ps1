param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)][string]$PostgresArchive,
    [Parameter(Mandatory = $true)][string]$QdrantArchive,
    [Parameter(Mandatory = $true)][string]$FfmpegArchive,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$CreateZip
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).ProviderPath
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
    & robocopy.exe (Join-Path $repo $name) (Join-Path $app $name) /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Source copy failed: $name" }
}
Copy-Item -LiteralPath (Join-Path $repo 'run.py') -Destination $app

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
