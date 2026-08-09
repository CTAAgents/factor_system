"""
fts/config/settings.py — FTS 全局配置。

配置加载优先级（高 → 低）:
    1. 环境变量（FTS_* 前缀）
    2. YAML 配置文件
    3. 本模块定义的默认值
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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


__all__ = [
    "FTSConfig",
    "get_config",
    "load_config",
    "DEFAULT_MEMORY_DIR",
    "DEFAULT_ELITE_DIR",
    "EVOLUTION_MODES",
    "validate_evolution_mode",
]
