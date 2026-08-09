"""
fts/config/settings.py — FTS 全局配置。

配置加载优先级（高 → 低）:
    1. 环境变量（FTS_* 前缀）
    2. YAML 配置文件
    3. 本模块定义的默认值
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── 默认路径 ────────────────────────────────────────────

DEFAULT_MEMORY_DIR = "memory"
DEFAULT_ELITE_DIR = "memory/knowledge/factors/elite"
DEFAULT_FUTURES_ELITE_DIR = "memory/knowledge/factors/futures_elite"


# ─── 配置类 ──────────────────────────────────────────────

@dataclass
class FTSConfig:
    """FTS 全局配置。"""

    # ── 路径配置 ──
    memory_dir: str = field(
        default_factory=lambda: os.getenv("FTS_MEMORY_DIR", DEFAULT_MEMORY_DIR)
    )
    elite_dir: str = field(
        default_factory=lambda: os.getenv("FTS_ELITE_DIR", DEFAULT_ELITE_DIR)
    )
    futures_elite_dir: str = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_ELITE_DIR", DEFAULT_FUTURES_ELITE_DIR)
    )

    def get_elite_dir(self, market: str = "stock") -> str:
        """按市场获取对应的 elite 目录。

        Args:
            market: "stock" 或 "futures"

        Returns:
            对应的 elite 目录路径
        """
        if market == "futures":
            return self.futures_elite_dir
        return self.elite_dir

    # ── 数据配置 ──
    default_market: str = field(
        default_factory=lambda: os.getenv("FTS_DEFAULT_MARKET", "futures")
    )

    # ── 宏观字段增强层（v2.32.0）──
    macro_field_injection: bool = field(
        default_factory=lambda: os.getenv("FTS_MACRO_FIELD_INJECTION", "1") == "1"
    )
    macro_lag_days: int = field(
        default_factory=lambda: int(os.getenv("FTS_MACRO_LAG_DAYS", "30"))
    )

    # ── LLM 配置 ──
    llm_backend: str = field(
        default_factory=lambda: os.getenv("FTS_LLM_BACKEND", "")
    )
    # LLM 采样温度；提高可增加因子多样性（默认 1.2 > provider 默认 1.0）
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("FTS_LLM_TEMPERATURE", "1.2"))
    )

    # ── 演化配置 ──
    # ── 演化模式 (Phase C.2): operator(算子主干) / code(代码创新) / hybrid(混合) ──
    evolution_mode: str = field(
        default_factory=lambda: os.getenv("FTS_EVOLUTION_MODE", "hybrid")
    )
    max_generations: int = 10
    population_size: int = 20
    micro_trials_per_generation: int = 50

    # ── 并行 ──
    max_workers: int = field(
        default_factory=lambda: int(os.getenv("FTS_MAX_WORKERS", "4"))
    )

    # ── L1 Meta-Loop ──
    meta_loop_interval_hours: int = 24
    meta_loop_max_tokens: int = 8000

    # ── L3 Portfolio ──
    portfolio_max_factors: int = 20
    portfolio_top_n: int = 5
    portfolio_decay_days: int = 90

    # ── 股票因子中性化（v2.54.0+）──
    # 股票因子横截面评估时是否做行业/市值中性化预处理
    stock_neutralization: bool = field(
        default_factory=lambda: os.getenv("FTS_STOCK_NEUTRALIZATION", "true").lower() == "true"
    )
    # 行业映射文件路径（JSON 格式，{symbol: industry_name}）
    industry_map_path: str = field(
        default_factory=lambda: os.getenv("FTS_INDUSTRY_MAP_PATH", "data/industry_map.json")
    )
    # 市值映射文件路径（JSON 格式，{symbol: market_cap}，可选）
    cap_map_path: str = field(
        default_factory=lambda: os.getenv("FTS_CAP_MAP_PATH", "")
    )

    # ── 期货换月复权与展期成本（v2.58.0，GAP-046）──
    # 期货连续合约 K 线是否默认返回换月后复权序列（因子计算用）
    futures_adjusted: bool = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_ADJUSTED", "true").lower() == "true"
    )
    # 展期成本系数（基点/次，回测持仓穿越换月日扣除）
    roll_cost_bps: float = field(
        default_factory=lambda: float(os.getenv("FTS_ROLL_COST_BPS", "2.0"))
    )

    # ── 期货截面中性化 + 回测真实性仿真（v2.59.0，GAP-F03/F02）──
    # 期货横截面因子评估是否做板块/产业链中性化（剥离产业链系统性偏差）
    futures_neutralization: bool = field(
        default_factory=lambda: os.getenv("FTS_FUTURES_NEUTRALIZATION", "true").lower() == "true"
    )
    # 回测是否启用涨跌停拦截 + 停牌过滤（真实成交仿真）
    backtest_trade_filter: bool = field(
        default_factory=lambda: os.getenv("FTS_BACKTEST_TRADE_FILTER", "true").lower() == "true"
    )
    # 期货涨跌停判定阈值（单日涨跌幅 ≥ 该值视为涨跌停，无法成交）
    futures_limit_pct: float = field(
        default_factory=lambda: float(os.getenv("FTS_FUTURES_LIMIT_PCT", "0.08"))
    )

    # ── 样本外强制 + 保证金建模（v2.60.0，GAP-F08/F09）──
    # 因子晋升路径是否强制 WalkForward 冷启动样本外验证（数据不足时跳过并记录原因）
    force_walkforward: bool = field(
        default_factory=lambda: os.getenv("FTS_FORCE_WALKFORWARD", "true").lower() == "true"
    )
    # 品种保证金率表（{symbol: 保证金率}，未配置品种用默认 0.10）
    margin_rate_map: dict = field(default_factory=dict)
    # 最大保证金占用率（保证金占用/总权益，超过触发强平风险告警）
    max_margin_usage: float = field(
        default_factory=lambda: float(os.getenv("FTS_MAX_MARGIN_USAGE", "0.80"))
    )

    # ── 数据源降级加固（v2.60.0，GAP-F04）──
    # WIND/IFIND MCP 客户端是否启用（false=未启用，明确降级跳过增强字段；
    # true=启用，但未注入客户端时显式抛错提示初始化）
    mcp_enabled: bool = field(
        default_factory=lambda: os.getenv("FTS_MCP_ENABLED", "false").lower() == "true"
    )

    # ── L3 Verifier ──
    verifier: dict = field(default_factory=lambda: {
        "min_sharpe": 1.5,
        "max_correlation": 0.5,
        "max_turnover": 0.50,
        "max_decay_rate": 0.30,
        "min_n_factors": 3,
    })

    # ── 日志 ──
    log_level: str = field(
        default_factory=lambda: os.getenv("FTS_LOG_LEVEL", "INFO")
    )
    log_file: str = field(
        default_factory=lambda: os.getenv("FTS_LOG_FILE", "")
    )


# ─── 全局实例 ────────────────────────────────────────────

_default_config: Optional[FTSConfig] = None


def get_config() -> FTSConfig:
    """获取全局配置实例（延迟初始化）。"""
    global _default_config
    if _default_config is None:
        _default_config = load_config()
    return _default_config


def load_config(config_path: Optional[str] = None) -> FTSConfig:
    """加载配置（YAML + 环境变量覆盖）。

    Args:
        config_path: YAML 配置文件路径，None=自动查找 config/settings.yaml

    Returns:
        FTSConfig 实例
    """
    cfg = FTSConfig()

    # 尝试加载 YAML 文件
    if config_path is None:
        config_path = os.getenv("FTS_CONFIG_FILE", "")
    if not config_path:
        # 自动查找 config/settings.yaml
        default_config = Path("config/settings.yaml")
        if default_config.exists():
            config_path = str(default_config)
    if config_path:
        p = Path(config_path)
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                try:
                    import yaml  # type: ignore[import-untyped]
                    yaml_cfg = yaml.safe_load(text) or {}
                    _apply_dict(cfg, yaml_cfg)
                except ImportError:
                    import json
                    json_cfg = json.loads(text)
                    _apply_dict(cfg, json_cfg)
            except Exception:
                pass

    # 环境变量覆盖（FTS_* 前缀）
    _apply_env_overrides(cfg)

    return cfg


def _apply_dict(cfg: FTSConfig, d: dict[str, Any]) -> None:
    """将字典值应用到配置实例。"""
    for key, value in d.items():
        if hasattr(cfg, key) and value is not None:
            if key == "verifier" and isinstance(value, dict):
                current = getattr(cfg, key, {})
                if isinstance(current, dict):
                    current.update(value)
                    setattr(cfg, key, current)
                else:
                    setattr(cfg, key, value)
            else:
                setattr(cfg, key, value)


def _apply_env_overrides(cfg: FTSConfig) -> None:
    """FTS_* 环境变量覆盖配置。"""
    for key in dir(cfg):
        if key.startswith("_"):
            continue
        env_key = f"FTS_{key.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None:
            current = getattr(cfg, key)
            if isinstance(current, bool):
                setattr(cfg, key, env_val.lower() in ("1", "true", "yes"))
            elif isinstance(current, int):
                setattr(cfg, key, int(env_val))
            elif isinstance(current, float):
                setattr(cfg, key, float(env_val))
            else:
                setattr(cfg, key, env_val)


EVOLUTION_MODES: tuple[str, ...] = ("operator", "code", "hybrid")


def validate_evolution_mode(mode: str) -> str:
    """校验演化模式合法性。"""
    if mode not in EVOLUTION_MODES:
        raise ValueError(
            f"evolution_mode 必须是 {EVOLUTION_MODES} 之一, 实际: {mode}"
        )
    return mode


def load_industry_map(path: Optional[str] = None) -> dict[str, str]:
    """加载行业映射文件。

    Args:
        path: 行业映射 JSON 文件路径，None=使用配置中的默认路径

    Returns:
        {symbol: industry_name} 字典

    Raises:
        FileNotFoundError: 文件不存在时抛出
        json.JSONDecodeError: JSON 格式错误时抛出
    """
    import json

    if path is None:
        path = get_config().industry_map_path
    p = Path(path)
    if not p.exists():
        logger.warning("行业映射文件不存在: %s", path)
        return {}
    text = p.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        logger.warning("行业映射文件格式错误: 期望 JSON 对象，实际 %s", type(data).__name__)
        return {}
    # 过滤非字符串键（如注释等）
    result: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip():
            result[k.strip()] = v.strip()
    logger.info("加载行业映射: %d 条记录", len(result))
    return result


def load_cap_map(path: Optional[str] = None) -> dict[str, float]:
    """加载市值映射文件。

    Args:
        path: 市值映射 JSON 文件路径，None=使用配置中的默认路径

    Returns:
        {symbol: market_cap} 字典（值为 float，非数值条目过滤）

    Raises:
        json.JSONDecodeError: JSON 格式错误时抛出
    """
    import json

    if path is None:
        path = get_config().cap_map_path
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        logger.warning("市值映射文件不存在: %s", path)
        return {}
    text = p.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        logger.warning("市值映射文件格式错误: 期望 JSON 对象，实际 %s", type(data).__name__)
        return {}
    result: dict[str, float] = {}
    for k, v in data.items():
        if isinstance(k, str) and k.strip():
            try:
                result[k.strip()] = float(v)
            except (TypeError, ValueError):
                continue
    logger.info("加载市值映射: %d 条记录", len(result))
    return result


__all__ = [
    "FTSConfig",
    "get_config",
    "load_config",
    "load_industry_map",
    "load_cap_map",
    "DEFAULT_MEMORY_DIR",
    "DEFAULT_ELITE_DIR",
    "EVOLUTION_MODES",
    "validate_evolution_mode",
]
