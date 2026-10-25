param(
    [string]$InstallPath = "",
    [switch]$Silent,
    [int]$InstallPython = 1,
    [int]$InstallPySide6 = 1,
    [int]$DesktopShortcut = 1
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $InstallPath) {
    $InstallPath = Join-Path $env:LOCALAPPDATA "MC服务器管理器"
}
$script:Target = $InstallPath
$script:DoPython = $InstallPython
$script:DoQt = $InstallPySide6
$script:DoShortcut = $DesktopShortcut
$script:LogLines = New-Object System.Collections.ArrayList
$script:LogBox = $null

function Write-Log {
    param([string]$Msg)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Msg
    [void]$script:LogLines.Add($line)
    if ($script:LogBox) {
        $script:LogBox.AppendText($line + "`r`n")
        $script:LogBox.SelectionStart = $script:LogBox.TextLength
        $script:LogBox.ScrollToCaret()
        [System.Windows.Forms.Application]::DoEvents()
    } else {
        Write-Host $line
    }
}

function Get-PythonCandidates {
    $list = New-Object System.Collections.ArrayList

    try {
        $exe = (& py -3 -c "import sys;print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) {
            [void]$list.Add(($exe | Select-Object -First 1))
        }
    } catch { }

    foreach ($cmd in @("python", "python3")) {
        try {
            $exe = (& $cmd -c "import sys;print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $exe) {
                [void]$list.Add(($exe | Select-Object -First 1))
            }
        } catch { }
    }

    $patterns = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
        (Join-Path $env:ProgramFiles "Python3*\python.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $patterns += (Join-Path ${env:ProgramFiles(x86)} "Python3*\python.exe")
    }
    foreach ($p in $patterns) {
        Get-ChildItem $p -ErrorAction SilentlyContinue | ForEach-Object {
            [void]$list.Add($_.FullName)
        }
    }

    $result = @()
    foreach ($exe in ($list | Select-Object -Unique)) {
        if (-not (Test-Path $exe)) { continue }
        try {
            $ver = (& $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null)
            if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
            $parts = $ver.Trim().Split(".")
            $maj = [int]$parts[0]
            $min = [int]$parts[1]
            if ($maj -eq 3 -and $min -ge 9) {
                $result += [pscustomobject]@{
                    Exe = $exe
                    Version = $ver.Trim()
                    Minor = $min
                }
            }
        } catch { }
    }
    return ($result | Sort-Object Minor -Descending | Select-Object -First 1)
}

function Install-PythonRuntime {
    Write-Log "没有找到可用的 Python 3.9+，准备下载安装 Python 3.12.10 ..."
    $urls = @(
        "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe",
        "https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-amd64.exe",
        "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe"
    )
    $exePath = Join-Path $env:TEMP "mcsm-python-setup.exe"
    $downloaded = $false
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    foreach ($u in $urls) {
        try {
            Write-Log "下载：$u"
            Invoke-WebRequest -Uri $u -OutFile $exePath -UseBasicParsing -TimeoutSec 300
            if ((Get-Item $exePath).Length -gt 10MB) {
                $downloaded = $true
                break
            }
        } catch {
            Write-Log ("下载失败：" + $_.Exception.Message)
        }
    }
    if (-not $downloaded) {
        throw "Python 安装程序下载失败。请检查网络，或手动安装 Python 3.9+ 后重新运行安装程序。"
    }
    Write-Log "静默安装 Python（当前用户，不需要管理员权限）..."
    $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 InstallLauncherAllUsers=0"
    $proc = Start-Process -FilePath $exePath -ArgumentList $installArgs -Wait -PassThru
    Remove-Item $exePath -Force -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        throw ("Python 安装失败，退出码 " + $proc.ExitCode)
    }
    Start-Sleep -Seconds 2
    $py = Get-PythonCandidates
    if (-not $py) {
        throw "Python 已安装但没找到解释器，请重启电脑后再运行安装程序。"
    }
    return $py
}

function Install-PySide6 {
    param([string]$PyExe)
    Write-Log "安装 PySide6（约 100MB，需要联网）..."
    $mirrors = @(
        "",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple/"
    )
    foreach ($m in $mirrors) {
        $pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "PySide6")
        if ($m) {
            $pipArgs += @("-i", $m)
            Write-Log "使用国内镜像：$m"
        }
        try {
            $out = & $PyExe @pipArgs 2>&1
            $code = $LASTEXITCODE
            foreach ($line in $out) {
                $s = "$line".Trim()
                if ($s) { Write-Log ("  " + $s) }
            }
            if ($code -eq 0) {
                & $PyExe -c "import PySide6" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "PySide6 安装成功。"
                    return
                }
            }
        } catch {
            Write-Log ("安装失败：" + $_.Exception.Message)
        }
        Write-Log "这次没装成功，换一个源重试 ..."
    }
    throw "PySide6 安装失败。请检查网络后重新运行安装程序，或手动执行：python -m pip install PySide6"
}

function Assert-NotRunning {
    if (-not (Test-Path $script:Target)) { return }
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
    if (-not $procs) { return }
    $hits = @($procs | Where-Object {
        $_.CommandLine -and $_.CommandLine.ToLower().Contains($script:Target.ToLower())
    })
    if ($hits.Count -gt 0) {
        throw "要安装到的目录里还有程序在运行。请先完全关闭它（任务管理器里结束 python.exe 或 pythonw.exe），然后再试。"
    }
}

function Copy-AppFiles {
    $same = $false
    try {
        $same = ([IO.Path]::GetFullPath($script:Target).TrimEnd("\") -eq
                 [IO.Path]::GetFullPath($ScriptRoot).TrimEnd("\"))
    } catch { }
    if ($same) {
        Write-Log "安装目录就是当前解压目录，跳过复制（只安装依赖）。"
        return
    }
    Write-Log "复制程序文件到：$($script:Target)"
    New-Item -ItemType Directory -Force -Path $script:Target | Out-Null
    $items = @(
        "start_manager.bat",
        "换图标.bat",
        "使用说明.txt",
        "更新日志.txt",
        "安装.bat",
        "setup.ps1",
        "卸载.bat",
        "uninstall.ps1",
        "icon.ico",
        "icon.png",
        "icons",
        "mcserver"
    )
    foreach ($it in $items) {
        $src = Join-Path $ScriptRoot $it
        if (-not (Test-Path $src)) {
            throw "安装包缺少文件：$it"
        }
        Copy-Item -Path $src -Destination $script:Target -Recurse -Force
    }
    Remove-Item (Join-Path $script:Target "mcserver\__pycache__") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $script:Target "mcserver\core\__pycache__") -Recurse -Force -ErrorAction SilentlyContinue
    Write-Log "程序文件复制完成。"
}

function New-Shortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "MC 服务器管理器.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $script:Target "start_manager.bat"
    $s.WorkingDirectory = $script:Target
    $s.IconLocation = (Join-Path $script:Target "icon.ico") + ",0"
    $s.Description = "MC 服务器管理器"
    $s.Save()
    Write-Log "已创建桌面快捷方式。"
}

function Run-Install {
    # ★ 防呆：禁止直接装到盘符根目录（如 D:\ 或 C:\）
    #   之前用户选 D:\ 会直接触发 .NET 的“路径的形式不合法”，体验很差。
    $pathTest = $script:Target.TrimEnd('\', '/')
    if ($pathTest -match '^[A-Za-z]:$') {
        throw "不要把程序安装到盘符的根目录（如 D:\ 或 C:\）。`r`n请在后面加一个文件夹名，例如 D:\MC服务器管理器。"
    }

    Write-Log "安装目录：$($script:Target)"
    Assert-NotRunning
    $py = Get-PythonCandidates
    if ($py) {
        Write-Log "检测到 Python $($py.Version)：$($py.Exe)"
    } else {
        Write-Log "未检测到 Python 3.9+"
    }
    if (-not $py -and $script:DoPython -eq 1) {
        $py = Install-PythonRuntime
    }
    if (-not $py) {
        throw "本机没有可用的 Python 3.9+。请勾选『安装 Python』，或自己装好后再试。"
    }

    $hasQt = $false
    try {
        & $py.Exe -c "import PySide6" 2>$null
        if ($LASTEXITCODE -eq 0) { $hasQt = $true }
    } catch { }
    if ($hasQt) {
        Write-Log "检测到已经安装 PySide6。"
    } elseif ($script:DoQt -eq 1) {
        Install-PySide6 -PyExe $py.Exe
    }
    if (-not $hasQt -and $script:DoQt -ne 1) {
        throw "缺少 PySide6。请勾选『安装 PySide6』，或手动执行：python -m pip install PySide6"
    }

    Copy-AppFiles
    if ($script:DoShortcut -eq 1) {
        New-Shortcut
    }
    Write-Log "全部完成。以后双击桌面快捷方式，或运行安装目录里的 start_manager.bat 即可。"
}

function Save-Log {
    try {
        if (Test-Path $script:Target) {
            $path = Join-Path $script:Target "安装日志.txt"
            $script:LogLines | Set-Content -Path $path -Encoding UTF8
        }
    } catch { }
}

if ($Silent) {
    try {
        Run-Install
        Save-Log
        exit 0
    } catch {
        Write-Host ("[错误] " + $_.Exception.Message)
        Save-Log
        exit 1
    }
}

# ---------------- 图形界面 ----------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "MC 服务器管理器 - 安装程序"
$form.Size = New-Object System.Drawing.Size(600, 460)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "安装 MC 服务器管理器"
$lblTitle.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 14, [System.Drawing.FontStyle]::Bold)
$lblTitle.Location = New-Object System.Drawing.Point(22, 16)
$lblTitle.Size = New-Object System.Drawing.Size(540, 32)
$form.Controls.Add($lblTitle)

$lblSub = New-Object System.Windows.Forms.Label
$lblSub.Text = "一个管理 Minecraft 服务器的小工具：扫描、初始化、启停、改配置、装模组、备份、看日志。"
$lblSub.Location = New-Object System.Drawing.Point(24, 52)
$lblSub.Size = New-Object System.Drawing.Size(540, 22)
$form.Controls.Add($lblSub)

$lblPath = New-Object System.Windows.Forms.Label
$lblPath.Text = "安装目录："
$lblPath.Location = New-Object System.Drawing.Point(24, 92)
$lblPath.Size = New-Object System.Drawing.Size(80, 22)
$form.Controls.Add($lblPath)

$txtPath = New-Object System.Windows.Forms.TextBox
$txtPath.Text = $script:Target
$txtPath.Location = New-Object System.Drawing.Point(106, 90)
$txtPath.Size = New-Object System.Drawing.Size(378, 24)
$form.Controls.Add($txtPath)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = "浏览..."
$btnBrowse.Location = New-Object System.Drawing.Point(492, 88)
$btnBrowse.Size = New-Object System.Drawing.Size(70, 26)
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "选择安装目录"
    if (Test-Path $txtPath.Text) { $dlg.SelectedPath = $txtPath.Text }
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $txtPath.Text = $dlg.SelectedPath
    }
})
$form.Controls.Add($btnBrowse)

$chkPython = New-Object System.Windows.Forms.CheckBox
$chkPython.Text = "如果本机没有 Python，自动下载安装 Python 3.12（当前用户，不需要管理员）"
$chkPython.Checked = $true
$chkPython.Location = New-Object System.Drawing.Point(26, 132)
$chkPython.Size = New-Object System.Drawing.Size(540, 24)
$form.Controls.Add($chkPython)

$chkQt = New-Object System.Windows.Forms.CheckBox
$chkQt.Text = "安装 PySide6 界面库（约 100MB，需要联网；已安装则自动跳过）"
$chkQt.Checked = $true
$chkQt.Location = New-Object System.Drawing.Point(26, 160)
$chkQt.Size = New-Object System.Drawing.Size(540, 24)
$form.Controls.Add($chkQt)

$chkShortcut = New-Object System.Windows.Forms.CheckBox
$chkShortcut.Text = "创建桌面快捷方式"
$chkShortcut.Checked = $true
$chkShortcut.Location = New-Object System.Drawing.Point(26, 188)
$chkShortcut.Size = New-Object System.Drawing.Size(540, 24)
$form.Controls.Add($chkShortcut)

$chkLaunch = New-Object System.Windows.Forms.CheckBox
$chkLaunch.Text = "安装完成后立即启动"
$chkLaunch.Checked = $true
$chkLaunch.Location = New-Object System.Drawing.Point(26, 216)
$chkLaunch.Size = New-Object System.Drawing.Size(540, 24)
$form.Controls.Add($chkLaunch)

$lblLog = New-Object System.Windows.Forms.Label
$lblLog.Text = "安装日志："
$lblLog.Location = New-Object System.Drawing.Point(24, 248)
$lblLog.Size = New-Object System.Drawing.Size(100, 20)
$form.Controls.Add($lblLog)

$script:LogBox = New-Object System.Windows.Forms.TextBox
$script:LogBox.Multiline = $true
$script:LogBox.ReadOnly = $true
$script:LogBox.ScrollBars = "Vertical"
$script:LogBox.Location = New-Object System.Drawing.Point(26, 270)
$script:LogBox.Size = New-Object System.Drawing.Size(540, 100)
$form.Controls.Add($script:LogBox)

$btnStart = New-Object System.Windows.Forms.Button
$btnStart.Text = "开始安装"
$btnStart.Location = New-Object System.Drawing.Point(360, 386)
$btnStart.Size = New-Object System.Drawing.Size(100, 30)
$form.Controls.Add($btnStart)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "取消"
$btnCancel.Location = New-Object System.Drawing.Point(466, 386)
$btnCancel.Size = New-Object System.Drawing.Size(100, 30)
$btnCancel.Add_Click({ $form.Close() })
$form.Controls.Add($btnCancel)

$btnStart.Add_Click({
    $target = $txtPath.Text.Trim()
    if (-not $target) {
        [System.Windows.Forms.MessageBox]::Show("请先选择安装目录。", "提示")
        return
    }
    $script:Target = $target
    $script:DoPython = if ($chkPython.Checked) { 1 } else { 0 }
    $script:DoQt = if ($chkQt.Checked) { 1 } else { 0 }
    $script:DoShortcut = if ($chkShortcut.Checked) { 1 } else { 0 }
    $btnStart.Enabled = $false
    $btnCancel.Enabled = $false
    $btnBrowse.Enabled = $false
    try {
        Run-Install
        Save-Log
        [System.Windows.Forms.MessageBox]::Show(
            "安装完成！`r`n`r`n安装目录：$($script:Target)`r`n启动方式：桌面快捷方式，或安装目录里的 start_manager.bat",
            "安装完成", [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
        if ($chkLaunch.Checked) {
            try {
                Start-Process (Join-Path $script:Target "start_manager.bat")
            } catch { }
        }
        $form.Close()
    } catch {
        Write-Log ("[错误] " + $_.Exception.Message)
        [System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message, "安装失败",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        $btnStart.Enabled = $true
        $btnCancel.Enabled = $true
        $btnBrowse.Enabled = $true
    }
})
$form.Controls.Add($btnStart)

$form.Add_Shown({ $txtPath.Focus() })
[void]$form.ShowDialog()
