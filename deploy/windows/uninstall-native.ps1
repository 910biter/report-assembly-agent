param(
    [string]$InstallRoot = (Join-Path $env:ProgramFiles 'ReportAssemblyAgent'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'ReportAssemblyAgent')
)

$ErrorActionPreference = 'Continue'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$log = $null
if (Test-Path -LiteralPath $DataRoot -PathType Container) {
    $logRoot = Join-Path $DataRoot 'logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $log = Join-Path $logRoot 'uninstall.log'
}
function Write-Log([string]$Message) {
    if ($script:log) {
        Add-Content -LiteralPath $script:log -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
    }
}

$firewallRule = Get-NetFirewallRule -Name 'IRA-App-Inbound' -ErrorAction SilentlyContinue
if ($firewallRule) {
    $firewallRule | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Log 'Removed application inbound firewall rule'
}

foreach ($taskName in @('IRA-App', 'IRA-Qdrant')) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Log "Stopped and removed scheduled task $taskName"
    }
}

$service = Get-Service -Name 'IRA-PostgreSQL' -ErrorAction SilentlyContinue
if ($service) {
    Stop-Service -Name 'IRA-PostgreSQL' -Force -ErrorAction SilentlyContinue
    try { $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) } catch {}
    & sc.exe delete 'IRA-PostgreSQL' | Out-Null
    Write-Log 'Stopped and removed service IRA-PostgreSQL'
}

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
    $path = $_.ExecutablePath
    if ($path -and $path.StartsWith($InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Log "Stopped application-owned process $($_.ProcessId)"
    }
}

Write-Log 'Application data was preserved'
Write-Output "Removed application services and tasks. Data remains at $DataRoot"
