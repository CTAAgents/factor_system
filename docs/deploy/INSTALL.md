# FTS 安装指南

> 版本: v1.0.0

## 环境要求

- Python 3.10+
- Git
- Windows 10/11 或 Linux

## 安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/CTAAgents/factor_system.git
   cd factor_system
   ```

2. **创建虚拟环境（推荐）**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   source .venv/bin/activate  # Linux
   ```

3. **安装 FTS**
   ```bash
   pip install -e .[dev,evolution]
   ```

4. **可选依赖**
   ```bash
   pip install -e .[llm]       # LLM 因子演化
   pip install -e .[mcp]       # MCP 行情数据接入（腾讯自选股/东方财富）
   pip install watchdog         # 热重载开发模式
   pip install prometheus_client  # Prometheus 指标导出
   ```

5. **验证安装**
   ```bash
   fts version
   python -m pytest tests/ --no-cov --tb=short -q
   ```
