param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$InnoCompiler
)

$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path -LiteralPath $BundleDirectory).ProviderPath
$output = [IO.Path]::GetFullPath($OutputDirectory)
$script = Join-Path $PSScriptRoot 'installer\ReportAssemblyAgent.iss'
$licenseDirectory = Join-Path $bundle 'payload\app-source\app\licensing'
foreach ($name in @('_trust', 'protocol', 'service', 'storage')) {
    if (@(Get-ChildItem -LiteralPath $licenseDirectory -Filter "$name*.pyd" -File -ErrorAction SilentlyContinue).Count -ne 1) {
        throw "Missing or ambiguous compiled license module: $name"
    }
    foreach ($extension in @('.py', '.pyx', '.pyc')) {
        if (Test-Path -LiteralPath (Join-Path $licenseDirectory "$name$extension")) {
            throw "License source fallback must not ship: $name$extension"
        }
    }
}
foreach ($required in @('manifest.json', 'install-native.ps1', 'run-component.ps1', 'uninstall-native.ps1', 'payload')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bundle $required))) {
        throw "Bundle is missing required item: $required"
    }
}
if (-not (Test-Path -LiteralPath $output)) {
    New-Item -ItemType Directory -Path $output -Force | Out-Null
}
$setup = Join-Path $output 'ReportAssemblyAgent-Setup-0824-x64.exe'
if (Test-Path -LiteralPath $setup) {
    throw "Refusing to overwrite existing installer: $setup"
}
if (-not $InnoCompiler) { $InnoCompiler = $env:ISCC }
if (-not $InnoCompiler) {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $InnoCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw 'Inno Setup ISCC.exe not found. Set -InnoCompiler or ISCC.'
}
$arguments = @(
    "/DBundleDirectory=$bundle",
    "/O$output",
    $script
)
& $InnoCompiler @arguments
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed with exit code $LASTEXITCODE" }
$result = Get-Item -LiteralPath $setup
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
Write-Output "Installer: $($result.FullName)"
Write-Output "SizeBytes: $($result.Length)"
Write-Output "SHA256: $hash"
