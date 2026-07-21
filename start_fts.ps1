# FTS 启动脚本
# 设置环境变量后启动 FTS CLI

# ── LLM 配置（DeepSeek） ──
$env:OPENAI_API_KEY = $env:FDT_LLM_API_KEY
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-v4-flash"
$env:FTS_LLM_BACKEND = "openai"

# ── FTS 配置 ──
$env:FTS_CONFIG_FILE = "D:\Programs\factor_system\config\settings.yaml"
$env:FTS_LOG_LEVEL = "INFO"
$env:FTS_MEMORY_DIR = "D:\Programs\factor_system\memory"

Write-Host "=== FTS Environment Ready ===" -ForegroundColor Green
Write-Host "LLM: DeepSeek ($($env:OPENAI_MODEL))" -ForegroundColor Cyan
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
