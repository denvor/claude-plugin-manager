<#
.SYNOPSIS
部署 cpm：生成两个 shim 放进 ~/.local/bin，并确保该目录在持久 PATH 里。

.DESCRIPTION
cpm 是单文件脚本，部署 = 把它放上 PATH；shim 指向仓库里的 cpm.py，所以以后 git pull 即生效。
同一目录放两个 shim：
  cpm.cmd   给 cmd / PowerShell（PATHEXT 命中它）
  cpm       给 Git Bash

PATH 必须写进**持久**的用户环境变量（HKCU\Environment）：只在当前进程的 $env:PATH 里加，
新开 PowerShell 依然 CommandNotFoundException（README 的警告块记录了这次真机踩坑）。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repo    = $PSScriptRoot
$script  = Join-Path $repo "cpm.py"
$dir     = Join-Path $HOME ".local\bin"
$dirFull = $dir.TrimEnd("\")

if (-not (Test-Path $script -PathType Leaf)) { throw "找不到 $script（本脚本应放在仓库根目录）" }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "PATH 里没有 python。Windows 上请用 python，不要用 python3（常是 Microsoft Store 占位）"
}

New-Item -ItemType Directory -Force -Path $dir | Out-Null

# --- 1) 持久 PATH（HKCU\Environment），已存在则不重复添加 ---
$parts = @([Environment]::GetEnvironmentVariable("Path", "User") -split ";" |
           Where-Object { $_.Trim() })
$exists = $parts | Where-Object {
    $p = $_.Trim("\")
    $p.Equals($dirFull, "OrdinalIgnoreCase") -or
    [Environment]::ExpandEnvironmentVariables($p).Trim("\").Equals($dirFull, "OrdinalIgnoreCase")
}
if ($exists) {
    Write-Host "持久 PATH 已含 $dirFull"
} else {
    [Environment]::SetEnvironmentVariable("Path", (($parts + $dirFull) -join ";"), "User")
    Write-Host "已把 $dirFull 写入用户 PATH（HKCU\Environment）"
}

# --- 2) 两个 shim：UTF-8 无 BOM，行尾分别为 CRLF / LF ---
$utf8 = New-Object System.Text.UTF8Encoding($false)
$cmd = '@echo off' + "`r`n" + 'python "' + $script + '" %*' + "`r`n" +
       'exit /b %ERRORLEVEL%' + "`r`n"
$sh  = '#!/bin/sh' + "`n" + 'exec python "' + ($script -replace '\\', '/') + '" "$@"' + "`n"
[System.IO.File]::WriteAllText((Join-Path $dir "cpm.cmd"), $cmd, $utf8)
[System.IO.File]::WriteAllText((Join-Path $dir "cpm"), $sh, $utf8)

# --- 3) 当前窗口立即生效，并自检 shim ---
$inProc = @($env:PATH -split ";" | ForEach-Object { $_.Trim("\") })
if ($inProc -notcontains $dirFull) { $env:PATH = "$dirFull;$env:PATH" }
& (Join-Path $dir "cpm.cmd") --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "shim 自检失败（cpm.cmd --help 退出码 $LASTEXITCODE）" }

Write-Host "部署完成：$dirFull\cpm.cmd（cmd/PowerShell）、$dirFull\cpm（Git Bash）"
Write-Host "新开终端即可使用 cpm；本窗口已临时加入 PATH。"
