"""
FTS — Factor Trading System

从 FDT 剥离的独立因子策略系统，专注于多因子挖掘、演化与交易。
数据层基于腾讯自选股 MCP (akshare) 提供 A 股/ETF 行情数据，
基于 DuckDB + AKShare 提供期货连续合约数据。

核心模块：
    - core: 核心契约层（因子引擎 TypedDict 契约 + FTS 特有枚举）
    - factor_engine: 因子引擎（L1/L2/L3 三层循环 + 种子池 + 验证器）
    - pipeline: 因子推演管线（因子组合与融合）
    - strategies: 策略层（多因子策略）
    - scheduler: 调度层
    - cli: 统一命令行入口
    - data / data_mcp / data_futures: 数据适配层（A 股/ETF/期货）

版本: 见 __version__（CLI 默认演化期货因子，监控面板动态版本号）"""

from pathlib import Path

# ── 版本号：从 pyproject.toml 动态读取（单一真实源）─────────────────
__version__: str = "0.0.0"
try:
    import tomllib as _toml
except ImportError:
    import tomli as _toml  # type: ignore[no-redef]
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as _f:
        __version__ = _toml.load(_f)["project"]["version"]

# ── 自动加载 .env ────────────────────────────────────────
_env_loaded = False
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
        _env_loaded = True
except Exception:  # noqa: BLE001  # pragma: no cover
    pass
