param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$SfxModule,
    [Parameter(Mandatory = $true)][string]$Archiver
)

$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $BundleDirectory).ProviderPath
$module = (Resolve-Path -LiteralPath $SfxModule).ProviderPath
$archiverPath = (Resolve-Path -LiteralPath $Archiver).ProviderPath
$config = Join-Path $PSScriptRoot 'installer\sfx-config.txt'
$output = [IO.Path]::GetFullPath($OutputDirectory)
foreach ($name in @('manifest.json', 'install-native.ps1', 'setup-native.ps1', 'remove-native.ps1', 'payload')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundle $name))) {
        throw "Bundle is missing required item: $name"
    }
}
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw 'SFX configuration is missing' }
foreach ($name in @('install-native.ps1', 'setup-native.ps1', 'remove-native.ps1')) {
    $bytes = [IO.File]::ReadAllBytes((Join-Path $bundle $name))
    if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
        throw "$name must be UTF-8 with BOM for Windows PowerShell 5.1"
    }
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$setup = Join-Path $output 'ReportAssemblyAgent-Setup-0824-x64.exe'
$archive = Join-Path $output 'ReportAssemblyAgent-payload.7z'
foreach ($path in @($setup, $archive)) {
    if (Test-Path -LiteralPath $path) { throw "Refusing to replace existing build: $path" }
}

Push-Location $bundle
try {
    & $archiverPath a -t7z -mx=3 -mmt=on -ms=on $archive '.\*'
    if ($LASTEXITCODE -ne 0) { throw "7-Zip archive creation failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}

$parts = @($module, $config, $archive)
$size = ($parts | ForEach-Object { (Get-Item -LiteralPath $_).Length } | Measure-Object -Sum).Sum
if ($size -ge 4GB) {
    throw "Single-file SFX exceeds the Windows 4 GiB limit ($size bytes); use a smaller payload or a split package"
}
$destination = [IO.File]::Open($setup, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try {
    foreach ($part in $parts) {
        $source = [IO.File]::OpenRead($part)
        try { $source.CopyTo($destination) } finally { $source.Dispose() }
    }
} finally {
    $destination.Dispose()
}
& $archiverPath t $setup
if ($LASTEXITCODE -ne 0) { throw 'The completed SFX failed its archive integrity test' }
Remove-Item -LiteralPath $archive -Force
$result = Get-Item -LiteralPath $setup
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
Write-Output "Installer: $($result.FullName)"
Write-Output "SizeBytes: $($result.Length)"
Write-Output "SHA256: $hash"
