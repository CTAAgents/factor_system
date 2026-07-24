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

    # ── 数据配置 ──
    default_market: str = field(
        default_factory=lambda: os.getenv("FTS_DEFAULT_MARKET", "stock")
    )

    # ── LLM 配置 ──
    llm_backend: str = field(
        default_factory=lambda: os.getenv("FTS_LLM_BACKEND", "")
    )

    # ── 演化配置 ──
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
        config_path: YAML 配置文件路径，None=自动查找

    Returns:
        FTSConfig 实例
    """
    cfg = FTSConfig()

    # 尝试加载 YAML 文件
    if config_path is None:
        config_path = os.getenv("FTS_CONFIG_FILE", "")
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


__all__ = [
    "FTSConfig",
    "get_config",
    "load_config",
    "DEFAULT_MEMORY_DIR",
    "DEFAULT_ELITE_DIR",
]
