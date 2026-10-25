$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitProcess) {
    $nativePowerShell = Join-Path $env:WINDIR 'Sysnative\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $nativePowerShell -PathType Leaf)) {
        throw '64-bit Windows PowerShell is required'
    }
    $child = Start-Process -FilePath $nativePowerShell -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") `
        -Wait -PassThru
    exit $child.ExitCode
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $child = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") `
        -Verb RunAs -Wait -PassThru
    exit $child.ExitCode
}

[Windows.Forms.Application]::EnableVisualStyles()
$bg = [Drawing.Color]::FromArgb(16, 18, 23)
$surface = [Drawing.Color]::FromArgb(30, 34, 42)
$raised = [Drawing.Color]::FromArgb(37, 43, 53)
$border = [Drawing.Color]::FromArgb(77, 87, 103)
$primary = [Drawing.Color]::FromArgb(244, 247, 251)
$muted = [Drawing.Color]::FromArgb(194, 204, 218)
$accent = [Drawing.Color]::FromArgb(82, 111, 183)
$accentHover = [Drawing.Color]::FromArgb(99, 130, 204)
$form = New-Object Windows.Forms.Form
$form.Text = 'AI 报告整编工作台  |  安装'
$form.ClientSize = New-Object Drawing.Size(680, 428)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 9.5)
$form.BackColor = $bg
$form.ForeColor = $primary

$eyebrow = New-Object Windows.Forms.Label
$eyebrow.Text = 'AI REPORT WORKSPACE  /  SETUP'
$eyebrow.Location = New-Object Drawing.Point(32, 28)
$eyebrow.Size = New-Object Drawing.Size(590, 25)
$eyebrow.ForeColor = $muted
$eyebrow.Font = New-Object Drawing.Font('Segoe UI', 9)
$form.Controls.Add($eyebrow)

$heading = New-Object Windows.Forms.Label
$heading.Text = '安装报告整编工作台'
$heading.Font = New-Object Drawing.Font('Microsoft YaHei UI', 18, [Drawing.FontStyle]::Bold)
$heading.Location = New-Object Drawing.Point(30, 59)
$heading.Size = New-Object Drawing.Size(600, 42)
$heading.ForeColor = $primary
$form.Controls.Add($heading)

$description = New-Object Windows.Forms.Label
$description.Text = '离线部署应用、模型文件和后台服务。已有报告数据可沿用，并会在安装前备份。'
$description.Location = New-Object Drawing.Point(32, 108)
$description.Size = New-Object Drawing.Size(610, 28)
$description.ForeColor = $muted
$form.Controls.Add($description)

$section = New-Object Windows.Forms.Label
$section.Text = '安装位置'
$section.Location = New-Object Drawing.Point(32, 158)
$section.Size = New-Object Drawing.Size(580, 28)
$section.ForeColor = $primary
$section.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10, [Drawing.FontStyle]::Bold)
$form.Controls.Add($section)

$path = New-Object Windows.Forms.TextBox
$path.Location = New-Object Drawing.Point(32, 193)
$path.Size = New-Object Drawing.Size(486, 29)
$path.Text = Join-Path $env:ProgramFiles 'ReportAssemblyAgent'
$path.ReadOnly = $true
$path.BackColor = $surface
$path.ForeColor = $primary
$path.BorderStyle = 'FixedSingle'
$path.Font = New-Object Drawing.Font('Segoe UI', 10)
$form.Controls.Add($path)

$browse = New-Object Windows.Forms.Button
$browse.Text = '更改位置'
$browse.Location = New-Object Drawing.Point(530, 190)
$browse.Size = New-Object Drawing.Size(118, 34)
$browse.FlatStyle = 'Flat'
$browse.FlatAppearance.BorderColor = $border
$browse.FlatAppearance.MouseOverBackColor = $raised
$browse.BackColor = $surface
$browse.ForeColor = $primary
$browse.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.SelectedPath = Split-Path -Parent $path.Text
    if ($dialog.ShowDialog() -eq 'OK') { $path.Text = Join-Path $dialog.SelectedPath 'ReportAssemblyAgent' }
})
$form.Controls.Add($browse)

$note = New-Object Windows.Forms.Label
$note.Text = '程序文件与报告数据分开存放。不会更改系统 Python 或 PATH。'
$note.Location = New-Object Drawing.Point(32, 236)
$note.Size = New-Object Drawing.Size(600, 24)
$note.ForeColor = $muted
$form.Controls.Add($note)

$divider = New-Object Windows.Forms.Panel
$divider.Location = New-Object Drawing.Point(32, 274)
$divider.Size = New-Object Drawing.Size(616, 1)
$divider.BackColor = $border
$form.Controls.Add($divider)

$statusPanel = New-Object Windows.Forms.Panel
$statusPanel.Location = New-Object Drawing.Point(24, 284)
$statusPanel.Size = New-Object Drawing.Size(632, 120)
$statusPanel.BackColor = $raised
$statusPanel.BorderStyle = 'FixedSingle'
$form.Controls.Add($statusPanel)

$status = New-Object Windows.Forms.Label
$status.Text = '准备就绪'
$status.Location = New-Object Drawing.Point(18, 14)
$status.Size = New-Object Drawing.Size(490, 26)
$status.ForeColor = $primary
$status.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10, [Drawing.FontStyle]::Bold)
$statusPanel.Controls.Add($status)

$statusDetail = New-Object Windows.Forms.Label
$statusDetail.Text = '选择安装位置后开始。'
$statusDetail.Location = New-Object Drawing.Point(18, 45)
$statusDetail.Size = New-Object Drawing.Size(600, 24)
$statusDetail.ForeColor = $muted
$statusPanel.Controls.Add($statusDetail)

$progressTrack = New-Object Windows.Forms.Panel
$progressTrack.Location = New-Object Drawing.Point(18, 84)
$progressTrack.Size = New-Object Drawing.Size(306, 10)
$progressTrack.BackColor = $border
$statusPanel.Controls.Add($progressTrack)
$progressFill = New-Object Windows.Forms.Panel
$progressFill.Location = New-Object Drawing.Point(0, 0)
$progressFill.Size = New-Object Drawing.Size(0, 10)
$progressFill.BackColor = $accentHover
$progressTrack.Controls.Add($progressFill)

$viewLog = New-Object Windows.Forms.Button
$viewLog.Text = '查看日志'
$viewLog.Location = New-Object Drawing.Point(344, 70)
$viewLog.Size = New-Object Drawing.Size(118, 36)
$viewLog.FlatStyle = 'Flat'
$viewLog.FlatAppearance.BorderColor = $border
$viewLog.FlatAppearance.MouseOverBackColor = $raised
$viewLog.BackColor = $surface
$viewLog.ForeColor = $primary
$viewLog.Visible = $false
$viewLog.Add_Click({
    if ($script:stdout -and (Test-Path -LiteralPath $script:stdout)) {
        Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + $script:stdout + '"') | Out-Null
    }
})
$statusPanel.Controls.Add($viewLog)

$install = New-Object Windows.Forms.Button
$install.Text = '开始安装'
$install.Location = New-Object Drawing.Point(476, 70)
$install.Size = New-Object Drawing.Size(118, 36)
$install.FlatStyle = 'Flat'
$install.FlatAppearance.BorderSize = 0
$install.FlatAppearance.MouseOverBackColor = $accentHover
$install.BackColor = $accent
$install.ForeColor = [Drawing.Color]::White
$install.Font = New-Object Drawing.Font('Microsoft YaHei UI', 9.5, [Drawing.FontStyle]::Bold)
$statusPanel.Controls.Add($install)
$form.AcceptButton = $install

$script:process = $null
$script:stdout = $null
$script:stderr = $null
$script:exitCode = 1
$script:lastLine = 0
$script:startedAt = $null
$script:currentStep = -1
$script:currentDetail = '正在启动安装程序'
$stages = @('校验离线文件', '准备运行目录', '解压 PostgreSQL', '解压 Qdrant',
    '解压 FFmpeg', '安装 Python 依赖', '初始化数据库', '注册后台服务',
    '检查服务', '安装完成')
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if (-not $script:process) { return }
    if (Test-Path -LiteralPath $script:stdout) {
        $lines = @(Get-Content -LiteralPath $script:stdout -ErrorAction SilentlyContinue)
        for ($index = $script:lastLine; $index -lt $lines.Count; $index++) {
            $line = $lines[$index]
            if ($line -match '^IRA_PROGRESS\|(\d+)$') {
                $step = [int]$Matches[1]
                if ($step -ge 0 -and $step -lt $stages.Count) {
                    $script:currentStep = $step
                    $script:currentDetail = '正在处理，请保持窗口开启'
                    $progressFill.Width = [int][Math]::Round($progressTrack.Width * [Math]::Min($step, 9) / 10)
                    $status.Text = $stages[$step]
                }
            } elseif ($line -match '^IRA_FILE\|(.+)$' -and $script:currentStep -eq 0) {
                $script:currentDetail = '正在校验 ' + [IO.Path]::GetFileName($Matches[1])
            } elseif ($line -match '^IRA_DETAIL\|copy:(python|models|profile|app-source)$') {
                $names = @{ python = 'Python 环境'; models = '模型文件'; profile = '运行配置'; 'app-source' = '应用程序' }
                $script:currentDetail = '正在复制' + $names[$Matches[1]] + '，大文件可能需要数分钟'
            } elseif ($line -match '^IRA_DETAIL\|extract:(PostgreSQL|Qdrant|FFmpeg)$') {
                $script:currentDetail = '正在解压 ' + $Matches[1]
            }
        }
        $script:lastLine = $lines.Count
    }
    $elapsed = [int]([datetime]::Now - $script:startedAt).TotalSeconds
    $duration = '{0:D2}:{1:D2}' -f [int][Math]::Floor($elapsed / 60), ($elapsed % 60)
    $stepLabel = if ($script:currentStep -ge 0) { "第 $($script:currentStep + 1)/$($stages.Count) 步" } else { '启动中' }
    $statusDetail.Text = "$stepLabel · $script:currentDetail · 已用 $duration"
    $script:process.Refresh()
    if (-not $script:process.HasExited) { return }
    $timer.Stop()
    $script:process.WaitForExit()
    $script:exitCode = $script:process.ExitCode
    if ($null -eq $script:exitCode) {
        $outputLines = @(Get-Content -LiteralPath $script:stdout -ErrorAction SilentlyContinue)
        $completed = $outputLines -contains 'IRA_PROGRESS|9'
        $failed = @($outputLines | Where-Object { $_ -like 'Installation failed:*' }).Count -gt 0
        $healthy = $false
        if ($completed -and -not $failed) {
            try {
                $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 3
                $healthy = $response.StatusCode -eq 200
            } catch {}
        }
        $script:exitCode = if ($healthy) { 0 } else { 1 }
    }
    if ($script:exitCode -eq 0) {
        $progressFill.Width = $progressTrack.Width
        $status.Text = '安装完成，服务已启动。'
        $statusDetail.Text = '现在可以打开工作台。已有数据保持在原位置。'
        $statusDetail.ForeColor = $muted
        $install.Text = '打开工作台'
        $install.BackColor = $accent
        $install.Enabled = $true
        Start-Process 'http://127.0.0.1:8000' | Out-Null
    } else {
        $outputLines = @(Get-Content -LiteralPath $script:stdout -ErrorAction SilentlyContinue)
        $failure = @($outputLines | Where-Object { $_ -like 'Installation failed:*' } | Select-Object -Last 1)
        $details = if ($failure.Count) { $failure[0] } else {
            "安装进程返回代码 $script:exitCode。请查看完整日志确认状态。"
        }
        $status.Text = '安装未完成'
        $statusDetail.Text = '可查看日志了解原因，然后关闭向导。'
        $statusDetail.ForeColor = [Drawing.Color]::FromArgb(236, 160, 145)
        $viewLog.Visible = $true
        $install.Text = '关闭'
        $install.Enabled = $true
        [Windows.Forms.MessageBox]::Show("$details`n`n日志：$script:stdout", '安装失败') | Out-Null
    }
})

$install.Add_Click({
    if ($script:process) {
        $script:process.Refresh()
        if (-not $script:process.HasExited) { return }
        if ($script:exitCode -eq 0) {
            Start-Process 'http://127.0.0.1:8000' | Out-Null
        } else {
            $form.Close()
        }
        return
    }
    try { $destination = [IO.Path]::GetFullPath($path.Text) }
    catch {
        [Windows.Forms.MessageBox]::Show('安装目录无效。', '安装位置') | Out-Null
        return
    }
    $dataRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'ReportAssemblyAgent'))
    if (-not [IO.Path]::IsPathRooted($destination) -or
        $destination.Equals($dataRoot, [StringComparison]::OrdinalIgnoreCase) -or
        (Test-Path -LiteralPath $destination)) {
        [Windows.Forms.MessageBox]::Show('请选择尚未存在的完整安装目录。', '安装位置') | Out-Null
        return
    }
    $reuseData = Test-Path -LiteralPath $dataRoot
    if ($reuseData) {
        $choice = [Windows.Forms.MessageBox]::Show(
            "检测到已有报告数据：$dataRoot`n安装前会创建受限权限备份，并沿用原数据库和账号。是否继续？",
            '沿用已有数据', [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Question)
        if ($choice -ne [Windows.Forms.DialogResult]::Yes) { return }
    }
    $install.Enabled = $false
    $install.Text = '安装中…'
    $install.BackColor = $surface
    $browse.Enabled = $false
    $path.Enabled = $false
    $status.Text = '正在校验安装环境...'
    $statusDetail.ForeColor = $muted
    $statusDetail.Text = '正在启动安装程序'
    $script:lastLine = 0
    $script:startedAt = [datetime]::Now
    $script:currentStep = -1
    $script:currentDetail = '正在启动安装程序'
    $name = 'ira-install-' + [guid]::NewGuid().ToString('N')
    $script:stdout = Join-Path $env:TEMP "$name.out.log"
    $script:stderr = Join-Path $env:TEMP "$name.err.log"
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
        (Join-Path $PSScriptRoot 'install-native.ps1') + '" -InstallRoot "' + $destination +
        '" -DataRoot "' + (Join-Path $env:ProgramData 'ReportAssemblyAgent') + '"'
    if ($reuseData) { $arguments += ' -ReuseData' }
    try {
        $script:process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
            -RedirectStandardOutput $script:stdout -RedirectStandardError $script:stderr `
            -WindowStyle Hidden -PassThru
        $timer.Start()
    } catch {
        $install.Enabled = $true
        $install.Text = '开始安装'
        $install.BackColor = $accent
        $browse.Enabled = $true
        $path.Enabled = $true
        $script:process = $null
        $status.Text = '无法启动安装程序。'
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, '安装失败') | Out-Null
    }
})

$form.Add_FormClosing({ param($sender, $event)
    if ($script:process) {
        $script:process.Refresh()
        if (-not $script:process.HasExited) { $event.Cancel = $true }
    }
})
[void]$form.ShowDialog()
exit $script:exitCode
