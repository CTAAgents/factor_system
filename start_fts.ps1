# FTS 启动脚本
# 自动加载 .env 文件中的环境变量

# ── 从 .env 文件加载 ──
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)\s*$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "env:$name" -Value $value
        }
    }
    Write-Host "Loaded .env file" -ForegroundColor Green
} else {
    Write-Host ".env file not found, using defaults" -ForegroundColor Yellow
}

# ── FTS 路径配置（覆盖 .env 中的相对路径） ──
$env:FTS_CONFIG_FILE = "D:\Programs\factor_system\config\settings.yaml"
$env:FTS_MEMORY_DIR = "D:\Programs\factor_system\memory"

Write-Host "=== FTS Environment Ready ===" -ForegroundColor Green
Write-Host "LLM: $($env:OPENAI_MODEL) @ $($env:OPENAI_BASE_URL)" -ForegroundColor Cyan
Write-Host "Config: $($env:FTS_CONFIG_FILE)" -ForegroundColor Cyan
Write-Host "Memory: $($env:FTS_MEMORY_DIR)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Available commands:" -ForegroundColor Yellow
Write-Host "  fts version             - 查看版本" -ForegroundColor White
Write-Host "  fts monitor             - 系统监控" -ForegroundColor White
Write-Host "  fts evolution run       - L2 因子演化" -ForegroundColor White
Write-Host "  fts meta-loop run       - L1 市场感知" -ForegroundColor White
Write-Host "  fts portfolio run       - L3 组合构建" -ForegroundColor White
Write-Host "  fts factor list         - 查看 elite 因子" -ForegroundColor White
Write-Host "  fts scheduler list      - 查看调度任务" -ForegroundColor White
Write-Host ""
