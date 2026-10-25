param(
    [Parameter(Mandatory = $true)][string]$SfxModule,
    [Parameter(Mandatory = $true)][string]$Archiver
)

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:TEMP ('ira-sfx-smoke-' + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $root 'payload'
New-Item -ItemType Directory -Path $payload -Force | Out-Null
$env:IRA_SFX_SMOKE_MARKER = Join-Path $root 'ran.txt'
$succeeded = $false
try {
    'Set-Content -LiteralPath $env:IRA_SFX_SMOKE_MARKER -Value success' |
        Set-Content -LiteralPath (Join-Path $payload 'setup-native.ps1') -Encoding ASCII
    $archive = Join-Path $root 'payload.7z'
    & $Archiver a -t7z $archive (Join-Path $payload '*') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Smoke archive build failed' }
    $setup = Join-Path $root 'smoke.exe'
    $destination = [IO.File]::Create($setup)
    try {
        foreach ($part in @($SfxModule, (Join-Path $PSScriptRoot 'installer\sfx-config.txt'), $archive)) {
            $source = [IO.File]::OpenRead($part)
            try { $source.CopyTo($destination) } finally { $source.Dispose() }
        }
    } finally { $destination.Dispose() }
    $child = Start-Process -FilePath $setup -WindowStyle Hidden -Wait -PassThru
    if ($child.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $env:IRA_SFX_SMOKE_MARKER)) {
        throw "SFX did not run its extracted PowerShell entry point: $($child.ExitCode); fixture: $root"
    }
    $succeeded = $true
    Write-Output 'SFX extraction and entry point: OK'
} finally {
    Remove-Item Env:\IRA_SFX_SMOKE_MARKER -ErrorAction SilentlyContinue
    if ($succeeded) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
}
