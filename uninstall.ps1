param(
    [switch]$Silent,
    [int]$RemoveFiles = 1,
    [int]$RemoveConfig = 0,
    [int]$RemoveShortcut = 1
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LogLines = New-Object System.Collections.ArrayList

function Write-Log {
    param([string]$Msg)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Msg
    [void]$script:LogLines.Add($line)
    Write-Host $line
}

function Remove-Shortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "MC 服务器管理器.lnk"
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force -ErrorAction SilentlyContinue
        Write-Log "已删除桌面快捷方式。"
    }
}

function Remove-ProgramFiles {
    # ★ 防呆：绝对不能在盘符根目录下执行删除！
    #   如果在 D:\ 运行，Remove-Item 会误删 D:\mcserver 等同名文件夹；
    #   rmdir /s /q 甚至试图删除 D 盘根目录。
    $pathTest = $AppDir.TrimEnd('\', '/')
    if ($pathTest -match '^[A-Za-z]:$') {
        Write-Log "[错误] 程序不能在盘符根目录卸载，已停止删除以防误删系统文件。"
        throw "检测到程序目录在盘符根目录（如 D:\）。为防止误删你的文件，卸载程序已停止。请手动删除该目录。"
    }

    $codeItems = @(
        "mcserver", "icons", "start_manager.bat", "换图标.bat", 
        "使用说明.txt", "更新日志.txt", "icon.ico", "icon.png", 
        "安装日志.txt", "安装.bat", "setup.ps1", "卸载.bat", "uninstall.ps1"
    )
    foreach ($it in $codeItems) {
        $p = Join-Path $AppDir $it
        if (Test-Path $p) {
            Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue
            Write-Log "已删除：$it"
        }
    }

    if ($RemoveConfig -eq 1) {
        foreach ($it in @("servers.json", "theme.json", "sources.json", "mc_versions_cache.json")) {
            $p = Join-Path $AppDir $it
            if (Test-Path $p) {
                Remove-Item $p -Force -ErrorAction SilentlyContinue
                Write-Log "已删除配置：$it"
            }
        }
        # 目录自己删不掉（脚本在目录里运行），交给 cmd 延迟删除
        $cmd = "timeout /t 2 >nul & rmdir /s /q `"$AppDir`""
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $cmd -WindowStyle Hidden
        Write-Log "程序目录会在几秒后删除。"
    }
}
if ($Silent) {
    if ($RemoveShortcut -eq 1) { Remove-Shortcut }
    if ($RemoveFiles -eq 1) { Remove-ProgramFiles }
    exit 0
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "MC 服务器管理器 - 卸载"
$form.Size = New-Object System.Drawing.Size(560, 340)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "确定要卸载 MC 服务器管理器吗？"
$lbl.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
$lbl.Location = New-Object System.Drawing.Point(24, 20)
$lbl.Size = New-Object System.Drawing.Size(500, 28)
$form.Controls.Add($lbl)

$lblDir = New-Object System.Windows.Forms.Label
$lblDir.Text = "程序目录：" + $AppDir
$lblDir.Location = New-Object System.Drawing.Point(26, 56)
$lblDir.Size = New-Object System.Drawing.Size(500, 40)
$form.Controls.Add($lblDir)

$chkFiles = New-Object System.Windows.Forms.CheckBox
$chkFiles.Text = "删除程序文件"
$chkFiles.Checked = $true
$chkFiles.Location = New-Object System.Drawing.Point(28, 110)
$chkFiles.Size = New-Object System.Drawing.Size(480, 24)
$form.Controls.Add($chkFiles)

$chkConfig = New-Object System.Windows.Forms.CheckBox
$chkConfig.Text = "同时删除配置（servers.json / theme.json / sources.json，不可恢复）"
$chkConfig.Checked = $false
$chkConfig.Location = New-Object System.Drawing.Point(28, 140)
$chkConfig.Size = New-Object System.Drawing.Size(500, 24)
$form.Controls.Add($chkConfig)

$chkShortcut = New-Object System.Windows.Forms.CheckBox
$chkShortcut.Text = "删除桌面快捷方式"
$chkShortcut.Checked = $true
$chkShortcut.Location = New-Object System.Drawing.Point(28, 170)
$chkShortcut.Size = New-Object System.Drawing.Size(480, 24)
$form.Controls.Add($chkShortcut)

$btnUninstall = New-Object System.Windows.Forms.Button
$btnUninstall.Text = "卸载"
$btnUninstall.Location = New-Object System.Drawing.Point(330, 250)
$btnUninstall.Size = New-Object System.Drawing.Size(100, 30)
$form.Controls.Add($btnUninstall)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "取消"
$btnCancel.Location = New-Object System.Drawing.Point(436, 250)
$btnCancel.Size = New-Object System.Drawing.Size(100, 30)
$btnCancel.Add_Click({ $form.Close() })
$form.Controls.Add($btnCancel)

$btnUninstall.Add_Click({
    $RemoveFiles = if ($chkFiles.Checked) { 1 } else { 0 }
    $RemoveConfig = if ($chkConfig.Checked) { 1 } else { 0 }
    $RemoveShortcut = if ($chkShortcut.Checked) { 1 } else { 0 }
    $btnUninstall.Enabled = $false
    $btnCancel.Enabled = $false
    try {
        if ($RemoveShortcut -eq 1) { Remove-Shortcut }
        if ($RemoveFiles -eq 1) { Remove-ProgramFiles }
        [System.Windows.Forms.MessageBox]::Show(
            "卸载完成。", "完成",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
        $form.Close()
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message, "卸载失败",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        $btnUninstall.Enabled = $true
        $btnCancel.Enabled = $true
    }
})

[void]$form.ShowDialog()
