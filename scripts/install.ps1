# Claude Code 一键安装脚本 (Windows PowerShell)

$ErrorActionPreference = "Stop"

Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# API 配置
$CLAUDE_API_KEY = "sk-c75b02d02404fd12529f88ee0c223b2b016762ade35429162ba9c1183e949c33"
$CLAUDE_BASE_URL = "https://code.z-daha.cc"

# 全局安装路径（默认为空，即使用系统默认路径）
$script:InstallRoot = ""

Write-Host ""
Write-Host "[Claude Code 安装程序]" -ForegroundColor Cyan
Write-Host ""

# ── 选择安装路径 ────────────────────────────────────
function Select-InstallPath {
    Write-Host "请选择安装位置:" -ForegroundColor Cyan
    Write-Host "  [1] 默认路径 (C 盘)" -ForegroundColor White
    Write-Host "  [2] 自定义路径" -ForegroundColor White
    Write-Host ""
    $choice = Read-Host "请输入选项 (1/2，默认 1)"

    if ($choice -eq '2') {
        $customPath = Read-Host "请输入安装根目录 (例如 D:\DevTools)"
        $customPath = $customPath.Trim().TrimEnd('\')
        if (-not $customPath) {
            Write-Host "[!] 路径为空，使用默认路径" -ForegroundColor Yellow
            return
        }
        if (-not (Test-Path (Split-Path $customPath -Qualifier -ErrorAction SilentlyContinue) -ErrorAction SilentlyContinue)) {
            Write-Host "[!] 磁盘不存在，使用默认路径" -ForegroundColor Yellow
            return
        }
        if (-not (Test-Path $customPath)) {
            Write-Host "目录 $customPath 不存在，正在创建..." -ForegroundColor Cyan
            New-Item -ItemType Directory -Path $customPath -Force | Out-Null
        }
        $script:InstallRoot = $customPath
        Write-Host "[OK] 安装根目录: $customPath" -ForegroundColor Green
        Write-Host "     Git    -> $customPath\Git" -ForegroundColor DarkGray
        Write-Host "     Python -> $customPath\Python" -ForegroundColor DarkGray
        Write-Host "     NodeJS -> $customPath\NodeJS" -ForegroundColor DarkGray
        Write-Host ""
    } else {
        Write-Host "[OK] 使用默认安装路径 (C 盘)" -ForegroundColor Green
        Write-Host ""
    }
}

# ── 持久化 PATH（将自定义路径写入用户环境变量）────────
function Add-ToUserPath {
    param([string]$NewPath)
    if (-not $NewPath -or -not (Test-Path $NewPath)) { return }

    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentUserPath -and $currentUserPath.Split(';') -contains $NewPath) { return }

    $separator = if ($currentUserPath -and -not $currentUserPath.EndsWith(';')) { ';' } else { '' }
    $updatedPath = "$currentUserPath$separator$NewPath"
    [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
    $env:Path = "$NewPath;$env:Path"
    Write-Host "  [PATH] 已永久添加: $NewPath" -ForegroundColor DarkGray
}

# ── Git ──────────────────────────────────────────────
function Test-Git {
    try {
        $v = git --version 2>$null
        if ($v) { Write-Host "[OK] $v 已安装" -ForegroundColor Green; return $true }
    } catch {}
    Write-Host "[!] 未检测到 Git" -ForegroundColor Yellow
    return $false
}

function Install-Git {
    Write-Host "正在安装 Git (静默模式)..." -ForegroundColor Cyan
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        $wingetArgs = @("install", "Git.Git",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent")
        if ($script:InstallRoot) {
            $gitPath = "$script:InstallRoot\Git"
            $wingetArgs += "--location"
            $wingetArgs += $gitPath
            Write-Host "  安装到: $gitPath" -ForegroundColor DarkGray
        }
        & winget @wingetArgs
    } else {
        Write-Host "winget 不可用，请手动下载安装 Git:" -ForegroundColor Yellow
        Write-Host "https://git-scm.com/download/win" -ForegroundColor Yellow
        exit 1
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    if ($script:InstallRoot) {
        Add-ToUserPath "$script:InstallRoot\Git\cmd"
        Add-ToUserPath "$script:InstallRoot\Git\bin"
    }
    Write-Host "[OK] Git 安装完成" -ForegroundColor Green
}

# ── Python ───────────────────────────────────────────
function Test-Python {
    try {
        $v = python --version 2>$null
        if ($v) { Write-Host "[OK] $v 已安装" -ForegroundColor Green; return $true }
    } catch {}
    Write-Host "[!] 未检测到 Python" -ForegroundColor Yellow
    return $false
}

function Install-Python {
    Write-Host "正在安装 Python (静默模式，请稍候)..." -ForegroundColor Cyan
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        $wingetArgs = @("install", "Python.Python.3",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent",
            "--scope", "user")
        if ($script:InstallRoot) {
            $pyPath = "$script:InstallRoot\Python"
            $wingetArgs += "--location"
            $wingetArgs += $pyPath
            Write-Host "  安装到: $pyPath" -ForegroundColor DarkGray
        }
        & winget @wingetArgs
    } else {
        $installer = "$env:TEMP\python-installer.exe"
        Write-Host "正在下载 Python 安装包..." -ForegroundColor Cyan
        try {
            Invoke-WebRequest `
                -Uri "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" `
                -OutFile $installer -UseBasicParsing
            Write-Host "正在静默安装..." -ForegroundColor Cyan
            $installArgs = "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1"
            if ($script:InstallRoot) {
                $pyPath = "$script:InstallRoot\Python"
                $installArgs += " TargetDir=`"$pyPath`""
                Write-Host "  安装到: $pyPath" -ForegroundColor DarkGray
            }
            Start-Process -FilePath $installer -ArgumentList $installArgs -Wait
            Remove-Item $installer -ErrorAction SilentlyContinue
        } catch {
            Write-Host "下载失败，请手动安装 Python:" -ForegroundColor Yellow
            Write-Host "https://www.python.org/downloads/" -ForegroundColor Yellow
            Write-Host "安装时勾选 [Add Python to PATH]" -ForegroundColor Yellow
            exit 1
        }
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    if ($script:InstallRoot) {
        Add-ToUserPath "$script:InstallRoot\Python"
        Add-ToUserPath "$script:InstallRoot\Python\Scripts"
    }
    Write-Host "[OK] Python 安装完成" -ForegroundColor Green
}

# ── Node.js ──────────────────────────────────────────
function Test-NodeJS {
    try {
        $v = node -v 2>$null
        if ($v) {
            $major = [int]($v -replace 'v(\d+)\..*', '$1')
            if ($major -ge 18) {
                Write-Host "[OK] Node.js $v 已安装" -ForegroundColor Green
                return $true
            }
            Write-Host "[!] Node.js 版本过低 (需要 v18+，当前 $v)" -ForegroundColor Yellow
            return $false
        }
    } catch {}
    Write-Host "[!] 未检测到 Node.js" -ForegroundColor Yellow
    return $false
}

function Install-NodeJS {
    Write-Host "正在安装 Node.js..." -ForegroundColor Cyan
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        $wingetArgs = @("install", "OpenJS.NodeJS.LTS",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent")
        if ($script:InstallRoot) {
            $nodePath = "$script:InstallRoot\NodeJS"
            $wingetArgs += "--location"
            $wingetArgs += $nodePath
            Write-Host "  安装到: $nodePath" -ForegroundColor DarkGray
        }
        & winget @wingetArgs
    } else {
        Write-Host "winget 不可用，请手动安装 Node.js:" -ForegroundColor Yellow
        Write-Host "https://nodejs.org/" -ForegroundColor Yellow
        exit 1
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    if ($script:InstallRoot) {
        Add-ToUserPath "$script:InstallRoot\NodeJS"
        # npm global modules need a known location to stay in PATH
        $npmGlobal = "$script:InstallRoot\npm-global"
        if (-not (Test-Path $npmGlobal)) { New-Item -ItemType Directory -Path $npmGlobal -Force | Out-Null }
        npm config set prefix $npmGlobal
        Add-ToUserPath $npmGlobal
        Write-Host "  [npm] 全局模块目录: $npmGlobal" -ForegroundColor DarkGray
    }
    Write-Host "[OK] Node.js 安装完成" -ForegroundColor Green
}

# ── npm 镜像 ─────────────────────────────────────────
function Set-NpmMirror {
    Write-Host "配置 npm 国内镜像..." -ForegroundColor Cyan
    npm config set registry https://registry.npmmirror.com
    Write-Host "[OK] 已切换到淘宝 npm 镜像" -ForegroundColor Green
}

# ── Claude Code ──────────────────────────────────────
function Install-ClaudeCode {
    Write-Host "正在安装 Claude Code..." -ForegroundColor Cyan
    npm install -g @anthropic-ai/claude-code
    Write-Host "[OK] Claude Code 安装完成" -ForegroundColor Green
}

# ── 环境变量 ──────────────────────────────────────────
function Set-ClaudeEnv {
    Write-Host "配置 API 环境变量..." -ForegroundColor Cyan

    [Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL",   $CLAUDE_BASE_URL, "User")
    [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN",  $CLAUDE_API_KEY,  "User")
    [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY",     $CLAUDE_API_KEY,  "User")

    $env:ANTHROPIC_BASE_URL  = $CLAUDE_BASE_URL
    $env:ANTHROPIC_AUTH_TOKEN = $CLAUDE_API_KEY
    $env:ANTHROPIC_API_KEY   = $CLAUDE_API_KEY

    Write-Host "[OK] 环境变量已配置（重启终端后永久生效）" -ForegroundColor Green

    # 添加 claude 别名到 PowerShell Profile
    $profileDir = Split-Path $PROFILE -Parent
    if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }
    if (-not (Test-Path $PROFILE))    { New-Item -ItemType File      -Path $PROFILE    -Force | Out-Null }

    $aliasLine = 'function claude { & (Get-Command claude.ps1 -CommandType Application | Select-Object -First 1).Source --dangerously-skip-permissions @args }'
    $profileContent = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue
    if (-not $profileContent -or $profileContent -notmatch 'dangerously-skip-permissions') {
        Add-Content -Path $PROFILE -Value "`n$aliasLine"
        Write-Host "[OK] 已添加 claude 别名到 PowerShell 配置" -ForegroundColor Green
    }
}

# ── 主流程 ────────────────────────────────────────────
function Main {
    # 0. 选择安装路径
    Select-InstallPath

    # 1. Git
    if (-not (Test-Git)) {
        $ans = Read-Host "是否安装 Git? (y/n)"
        if ($ans -eq 'y' -or $ans -eq 'Y') {
            Install-Git
            if (-not (Test-Git)) {
                Write-Host "Git 安装后需重启终端，请重新运行此脚本" -ForegroundColor Yellow; exit 0
            }
        } else {
            Write-Host "[!] 跳过 Git，部分功能可能不可用" -ForegroundColor Yellow
        }
    }

    # 2. Python
    if (-not (Test-Python)) {
        $ans = Read-Host "是否安装 Python? (y/n)"
        if ($ans -eq 'y' -or $ans -eq 'Y') {
            Install-Python
            if (-not (Test-Python)) {
                Write-Host "Python 安装后需重启终端，请重新运行此脚本" -ForegroundColor Yellow; exit 0
            }
        } else {
            Write-Host "[!] 跳过 Python，部分功能可能不可用" -ForegroundColor Yellow
        }
    }

    # 3. Node.js
    if (-not (Test-NodeJS)) {
        $ans = Read-Host "是否安装 Node.js? (y/n)"
        if ($ans -eq 'y' -or $ans -eq 'Y') {
            Install-NodeJS
            if (-not (Test-NodeJS)) {
                Write-Host "Node.js 安装后需重启终端，请重新运行此脚本" -ForegroundColor Yellow; exit 0
            }
        } else {
            Write-Host "需要 Node.js 才能继续" -ForegroundColor Red; exit 1
        }
    }

    # 4. npm 镜像 + Claude Code + 环境变量
    Set-NpmMirror
    Install-ClaudeCode
    Set-ClaudeEnv

    Write-Host ""
    Write-Host "--------------------------------------------" -ForegroundColor Green
    Write-Host "[OK] 安装完成！" -ForegroundColor Green
    Write-Host "--------------------------------------------" -ForegroundColor Green
    Write-Host ""
    if ($script:InstallRoot) {
        Write-Host "安装根目录: $script:InstallRoot" -ForegroundColor Cyan
        Write-Host "自定义路径已写入用户 PATH 环境变量（永久生效）" -ForegroundColor DarkGray
        Write-Host ""
    }
    Write-Host "运行 " -NoNewline
    Write-Host "claude" -ForegroundColor Cyan -NoNewline
    Write-Host " 启动 Claude Code"
    Write-Host "（如命令未找到，请重启终端使 PATH 生效）" -ForegroundColor DarkGray
    Write-Host ""
}

Main
