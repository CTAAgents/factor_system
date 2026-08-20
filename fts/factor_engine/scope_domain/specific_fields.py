"""
fts/factor_engine/scope_domain/specific_fields.py — 品种特有字段注册与加载（P3 框架）

为品种级特异因子提供"数据前提"通道：按 config/specific_fields.yaml 注册表
按品种加载特有条目（source/channel/storage/description/enabled）。

框架先行：真实数据源接入列为后续 GAP；本模块交付注册表加载 + 字段注入骨架
（缺失/未启用/解析失败一律降级不阻断——数据前提缺失不阻断因子流程）。

开关：FTS_SPECIFIC_FIELDS_ENABLED=1 显式开启（默认关）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_YAML = Path(__file__).resolve().parent.parent.parent.parent / "config" / "specific_fields.yaml"


@lru_cache(maxsize=1)
def load_specific_fields(path: Optional[str | Path] = None) -> dict[str, dict[str, dict[str, Any]]]:
    """加载品种特有字段注册表 {symbol: {field: {source,...}}}（缓存）。

    缺失/解析失败/无 fields 键 → 空 dict（降级不阻断）。
    """
    p = Path(path) if path else _DEFAULT_YAML
    if not p.exists():
        logger.warning("[specific-fields] %s 缺失，注册表为空", p.name)
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        fields = cfg.get("fields") or {}
        if not isinstance(fields, dict):
            return {}
        return {str(sym): {str(f): dict(m) for f, m in items.items()} for sym, items in fields.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning("[specific-fields] %s 解析失败: %s", p.name, e)
        return {}


def enabled_specific_fields(path: Optional[str | Path] = None) -> dict[str, dict[str, dict[str, Any]]]:
    """已启用（enabled=true）的品种特有字段（按需采集清单；无启用字段 → 空 dict）。"""
    reg = load_specific_fields(path)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for sym, items in reg.items():
        active = {f: m for f, m in items.items() if bool(m.get("enabled"))}
        if active:
            out[sym] = active
    return out


def _default_cache_dir() -> Path:
    """品种特有字段缓存目录（memory/cache/specific_fields/，配置可覆盖）。"""
    try:
        from fts.config import get_config

        p = get_config().specific_fields_cache_dir
        return Path(p) if p else Path("memory/cache/specific_fields")
    except Exception:  # noqa: BLE001
        return Path("memory/cache/specific_fields")


def enrich_specific_fields(
    panel: dict[str, Any],
    market: str = "futures",
    enabled: Optional[bool] = None,
    cache_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """按启用清单从缓存注入品种特有字段（真实数据源通道，GAP-162）。

    数据约定：外部采集脚本将字段写入 ``{cache_dir}/{symbol}.parquet``（date 列 +
    字段列，与 kline 面板 date 对齐）；本函数按日期对齐追加字段列到 panel[symbol]。
    缓存缺失/解析失败 → 降级不阻断（数据前提缺失不阻断因子流程）。

    Args:
        panel: 逐品种 DataFrame dict（FTSDataProvider 面板）。
        market: 市场（框架预留）。
        enabled: 通道开关（None → 读 FTS_SPECIFIC_FIELDS_ENABLED）。
        cache_dir: 缓存目录（None → 默认 memory/cache/specific_fields）。

    Returns:
        注入后的面板（注入降级不阻断，返回原对象或副本）。
    """
    del market  # 框架预留市场路由
    if enabled is None:
        try:
            from fts.config import get_config

            enabled = bool(get_config().specific_fields_enabled)
        except Exception:  # noqa: BLE001
            enabled = False
    if not enabled:
        return panel
    try:
        import pandas as pd  # type: ignore[import-untyped]

        active = enabled_specific_fields()
        if not active:
            return panel
        cache = Path(cache_dir or _default_cache_dir())
        out = dict(panel)
        for sym, fields in active.items():
            if sym not in out or not fields:
                continue
            pf = cache / f"{sym}.parquet"
            if not pf.exists():
                logger.warning("[specific-fields] %s 字段缓存缺失（降级不阻断）: %s", sym, pf)
                continue
            try:
                df = pd.read_parquet(pf)
                if df is None or len(df) == 0:
                    continue
                base = out[sym]
                if "date" in df.columns:
                    # base 索引即日期（DatetimeIndex），按 index 对齐注入
                    merged = base.join(df.set_index("date"), how="left")
                else:
                    merged = base.join(df, how="left")
                inject = [f for f in fields if f in df.columns]
                if inject:
                    out[sym] = merged
                    logger.info("[specific-fields] 注入 %s: %s", sym, inject)
            except Exception as fe:  # noqa: BLE001 — 单品种注入失败降级不阻断
                logger.warning("[specific-fields] %s 注入失败（降级不阻断）: %s", sym, fe)
        return out
    except Exception as e:  # noqa: BLE001 — 注入整体失败降级不阻断
        logger.warning("[specific-fields] 字段注入降级（不阻断）: %s", e)
        return panel


__all__ = ["enabled_specific_fields", "enrich_specific_fields", "load_specific_fields"]
