# 临时脚本：分目录批量回归测试，各目录独立结果落盘（定位超时用例 + 保住已完成目录结果）
$ErrorActionPreference = "Continue"
$py = "C:\Program Files\Python312\python.exe"
$out = "memory/logs/regression_batches_20260809.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== BATCH REGRESSION START $ts ===" | Out-File $out -Encoding utf8

$dirs = @(
    "tests/cli",
    "tests/core",
    "tests/cross_market",
    "tests/data_sources",
    "tests/factor_engine",
    "tests/live_trade",
    "tests/monitor",
    "tests/risk",
    "tests/scenarios",
    "tests/scheduler"
)

# data_sources 目录排除真实联网数据源测试（akshare/tqsdk/tq/tdx）
$ignore = @(
    "--ignore=tests/data_sources/test_akshare_minute_source.py",
    "--ignore=tests/data_sources/test_akshare_minute_source_full.py",
    "--ignore=tests/data_sources/test_tqsdk_source.py",
    "--ignore=tests/data_sources/test_tqsdk_tick_source.py",
    "--ignore=tests/data_sources/test_tq_source.py",
    "--ignore=tests/data_sources/test_tdx_minute_source.py",
    "--ignore=tests/data_sources/test_tdx_minute_source_full.py",
    "--ignore=tests/data_sources/test_aggregator.py"
)

foreach ($d in $dirs) {
    $start = Get-Date
    if ($d -eq "tests/data_sources") {
        $result = & $py -m pytest $d -q --no-header --timeout=300 -p no:cacheprovider @ignore 2>&1 | Select-Object -Last 3
    } else {
        $result = & $py -m pytest $d -q --no-header --timeout=300 -p no:cacheprovider 2>&1 | Select-Object -Last 3
    }
    $elapsed = ((Get-Date) - $start).TotalSeconds
    $line = "[$d] elapsed=${elapsed}s -> " + ($result -join " | ")
    $line | Out-File $out -Append -Encoding utf8
    Write-Host $line
}

# 根目录测试文件（tests/test_*.py）
$rootFiles = @(Get-ChildItem "tests" -File -Filter "test_*.py" | ForEach-Object { $_.FullName })
$start = Get-Date
$result = & $py -m pytest $rootFiles -q --no-header --timeout=300 -p no:cacheprovider 2>&1 | Select-Object -Last 3
$elapsed = ((Get-Date) - $start).TotalSeconds
$line = "[tests/root] elapsed=${elapsed}s -> " + ($result -join " | ")
$line | Out-File $out -Append -Encoding utf8
Write-Host $line

"=== BATCH REGRESSION DONE $(Get-Date -Format "yyyy-MM-dd HH:mm:ss") ===" | Out-File $out -Append -Encoding utf8
