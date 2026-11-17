param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$DataRoot
)

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath +
        '" -InstallRoot "' + $InstallRoot + '" -DataRoot "' + $DataRoot + '"'
    $child = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $child.ExitCode
}

$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$scriptPath = [IO.Path]::GetFullPath($PSCommandPath)
if (-not $scriptPath.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\') -or
    $root.Equals($data, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to remove an unexpected installation directory'
}
$cleanup = Join-Path $root 'uninstall-native.ps1'
if (-not (Test-Path -LiteralPath $cleanup -PathType Leaf)) { throw 'Uninstall script is missing' }
& $cleanup -InstallRoot $root -DataRoot $data
if (-not $?) { throw 'Service cleanup failed; program files were preserved' }

$key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ReportAssemblyAgent'
Remove-Item -LiteralPath $key -Force -ErrorAction SilentlyContinue
$shortcut = Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'AI 报告整编工作台.url'
Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
Set-Location $env:SystemRoot
Remove-Item -LiteralPath $root -Recurse -Force
Write-Output "Program removed. Report data remains at $data"
