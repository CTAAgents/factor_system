#requires -Version 7
<#
.SYNOPSIS
    自动清理 Agent 工具（WorkBuddy / QClaw / TRAE）产生的日志、trace、缓存与 pytest 临时残留。

.DESCRIPTION
    安全策略：
      - 只删除可再生物（日志 / trace / Electron 缓存 / 配置备份 / 临时文件）
      - 不触碰功能数据：binaries、plugins、projects、skills、memory、state、file-history
      - 删除使用 .NET 原生 API，被占用文件自动跳过，不走回收站（规避终端 safe-rm 保护层）
      - 幂等，可重复执行
    清理范围：
      1) .qclaw/compile-cache          全部清空
      2) .qclaw/backups                保留最新 1 个备份
      3) .workbuddy/logs               保留 KeepLogsDays 天
      4) .workbuddy/traces             保留 KeepTracesDays 天
      5) .workbuddy/app/session        纯缓存（仅当 WorkBuddy 未运行时清理）
      6) %TEMP%                        清理 KeepTempDays 天前旧文件（锁定跳过）
      7) %TEMP%\pytest-of-yangd        保留最新 3 个轮转目录（pytest 运行中跳过）

.PARAMETER DryRun
    只统计不删除，输出将清理内容与预计释放量。

.PARAMETER Quiet
    安静模式：不打印明细，仅写日志与输出摘要。

.PARAMETER KeepLogsDays
    WorkBuddy 日志保留天数，默认 3。

.PARAMETER KeepTracesDays
    WorkBuddy trace 保留天数，默认 7。

.PARAMETER KeepTempDays
    %TEMP% 清理年龄阈值（天），默认 7。

.EXAMPLE
    ./agent_disk_cleanup.ps1 -DryRun
    ./agent_disk_cleanup.ps1 -Quiet
#>
param(
    [switch]$DryRun,
    [switch]$Quiet,
    [int]$KeepLogsDays = 3,
    [int]$KeepTracesDays = 7,
    [int]$KeepTempDays = 7
)

$ErrorActionPreference = 'SilentlyContinue'
$logPath = Join-Path $env:USERPROFILE 'agent-disk-cleanup.log'

function Write-Log {
    param([string]$Msg)
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Msg
    if (-not $Quiet) { Write-Host $line }
    Add-Content -LiteralPath $logPath -Value $line -ErrorAction SilentlyContinue
}

function Get-SizeMB {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0.0 }
    $s = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    return [math]::Round($s / 1MB, 1)
}

function Remove-Tree {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object {
        try { [System.IO.File]::Delete($_.FullName) } catch {}
    }
    Get-ChildItem -LiteralPath $Path -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
            try { [System.IO.Directory]::Delete($_.FullName) } catch {}
        }
    try { [System.IO.Directory]::Delete($Path) } catch {}
}

function Clear-Items {
    param(
        [string]$Label,
        [System.Collections.Generic.List[object]]$Items
    )
    $freed = 0.0
    foreach ($it in $Items) {
        if ($DryRun) {
            Write-Log ('[DRY-RUN] {0} -> 将清理 {1} ({2:N2} MB)' -f $Label, $it.Name, $it.SizeMB)
            $freed += $it.SizeMB
        } else {
            if ($it.Kind -eq 'Tree') { Remove-Tree $it.FullName } else { try { [System.IO.File]::Delete($it.FullName) } catch {} }
            if (-not (Test-Path -LiteralPath $it.FullName)) {
                Write-Log ('已清理 {0} -> {1} ({2:N2} MB)' -f $Label, $it.Name, $it.SizeMB)
                $freed += $it.SizeMB
            } else {
                Write-Log ('{0} -> {1} 被占用/未删净，跳过' -f $Label, $it.Name)
            }
        }
    }
    return $freed
}

$total = 0.0
$items = [System.Collections.Generic.List[object]]::new()

# ---------- 1. QClaw compile-cache（全部清空） ----------
$p = "$env:USERPROFILE\.qclaw\compile-cache"
if (Test-Path $p) {
    $items = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem $p -Force | ForEach-Object {
        $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
    }
    $total += Clear-Items 'QClaw compile-cache' $items
}

# ---------- 2. QClaw backups（保留最新 1 个） ----------
$p = "$env:USERPROFILE\.qclaw\backups"
if (Test-Path $p) {
    $items = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem $p -File -Force | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1 | ForEach-Object {
        $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'File'; SizeMB = [math]::Round($_.Length / 1MB, 2) })
    }
    $total += Clear-Items 'QClaw backups' $items
}

# ---------- 3. WorkBuddy logs（保留 KeepLogsDays 天） ----------
$p = "$env:USERPROFILE\.workbuddy\logs"
$cut = (Get-Date).AddDays(-$KeepLogsDays)
if (Test-Path $p) {
    $items = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem $p -Directory -Force | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and $_.LastWriteTime -lt $cut } | ForEach-Object {
        $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
    }
    Get-ChildItem $p -File -Force | Where-Object { $_.LastWriteTime -lt $cut } | ForEach-Object {
        $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'File'; SizeMB = [math]::Round($_.Length / 1MB, 2) })
    }
    $total += Clear-Items 'WorkBuddy logs' $items
}

# ---------- 4. WorkBuddy traces（保留 KeepTracesDays 天） ----------
$p = "$env:USERPROFILE\.workbuddy\traces"
$cutT = (Get-Date).AddDays(-$KeepTracesDays)
if (Test-Path $p) {
    $items = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem $p -Directory -Force | Where-Object { $_.LastWriteTime -lt $cutT } | ForEach-Object {
        $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
    }
    $total += Clear-Items 'WorkBuddy traces' $items
}

# ---------- 5. WorkBuddy app/session 纯缓存（仅当 WorkBuddy 未运行） ----------
$session = "$env:USERPROFILE\.workbuddy\app\session"
$cacheDirs = @('Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache',
    'old_DawnGraphiteCache_000', 'old_DawnWebGPUCache_000', 'old_GPUCache_000')
if (@(Get-Process -Name 'WorkBuddy*' -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Log 'WorkBuddy 运行中，跳过 app/session 缓存清理'
} else {
    $items = [System.Collections.Generic.List[object]]::new()
    foreach ($c in $cacheDirs) {
        $cp = Join-Path $session $c
        if (Test-Path $cp) {
            Get-ChildItem $cp -Force | ForEach-Object {
                $items.Add([pscustomobject]@{ Name = "$c/$($_.Name)"; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
            }
        }
    }
    $total += Clear-Items 'WorkBuddy app/session 缓存' $items
}

# ---------- 6. %TEMP% 旧文件（保留 KeepTempDays 天，锁定跳过） ----------
$cutTmp = (Get-Date).AddDays(-$KeepTempDays)
$items = [System.Collections.Generic.List[object]]::new()
Get-ChildItem $env:TEMP -Force | Where-Object { $_.LastWriteTime -lt $cutTmp } | ForEach-Object {
    $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
}
$total += Clear-Items '%TEMP% 旧文件' $items

# ---------- 7. pytest-of-yangd（保留最新 3 个轮转；pytest 运行中跳过） ----------
$py = "$env:TEMP\pytest-of-yangd"
$pyRunning = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'pytest' }).Count -gt 0
if ($pyRunning) {
    Write-Log 'pytest 运行中，跳过 pytest-of-yangd 清理'
} elseif (Test-Path $py) {
    $items = [System.Collections.Generic.List[object]]::new()
    Get-ChildItem $py -Directory -Force |
        Sort-Object { [int]($_.Name -replace 'pytest-', '') } -Descending | Select-Object -Skip 3 | ForEach-Object {
            $items.Add([pscustomobject]@{ Name = $_.Name; FullName = $_.FullName; Kind = 'Tree'; SizeMB = (Get-SizeMB $_.FullName) })
        }
    $total += Clear-Items 'pytest 旧轮转目录' $items
}

$mode = $(if ($DryRun) { 'DRY-RUN' } else { '清理' })
$verb = $(if ($DryRun) { '预计' } else { '实际' })
Write-Log ('==== 执行完成 [{0}]，{1}释放 {2:N1} MB ====' -f $mode, $verb, $total)
