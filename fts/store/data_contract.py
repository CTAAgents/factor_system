"""
fts/store/data_contract.py — 数据契约字段完整性校验（GAP-151，v3.1.0+6 单源化）。

K 线数据契约的分级字段完整性断言，供数据加载路径（``fts/data_futures.py``）
与调度巡检（``fts/scheduler/jobs.py``）复用，消除两处各自实现的漂移：

- 核心字段（date/open/high/low/close/volume）缺失或全空 → 数据不可用，
  调用方应跳过（宁缺毋滥，error 级）；
- 增强字段（hold/settle/pre_settle）缺失或全空 → 显式暴露代理降级链
  （warning 级，不阻断——因子计算走代理值但缺口可观测）。

契约（K 线必填/增强字段清单）为唯一权威定义，消费方一律 import 本模块。
"""

from __future__ import annotations

import logging
from typing import Any

# K 线契约必填字段（核心）：任一缺失/全空 → 数据不可用
KLINE_CORE_FIELDS: tuple[str, ...] = ("date", "open", "high", "low", "close", "volume")
# K 线契约增强字段：缺失/全空 → 代理降级（hold 20 日滚动均量 / settle 典型价），显式告警
KLINE_EXTENDED_FIELDS: tuple[str, ...] = ("hold", "settle", "pre_settle")


def _field_available(df: Any, col: str) -> bool:
    """字段可用性：存在且非全空。

    date 兼容两种形态：位于 columns（``SELECT *`` 原生行）或位于 index
    （``df.set_index("date")`` 形态，data_futures 主加载路径）。
    """
    if col == "date":
        if col in df.columns:
            return bool(int(df[col].notna().sum()))
        return getattr(df.index, "name", None) == "date" and len(df) > 0
    if col not in df.columns:
        return False
    return bool(int(df[col].notna().sum()))


def classify_kline_field_integrity(
    df: Any, extended_fields: tuple[str, ...] = KLINE_EXTENDED_FIELDS
) -> tuple[list[str], list[str]]:
    """分级归类缺失字段（纯逻辑，不产生日志）。

    Args:
        df: K 线 DataFrame
        extended_fields: 本加载路径的增强字段子集（如 data_futures 无
            pre_settle 列，传 ("hold", "settle") 避免噪音告警）。

    Returns:
        (core_missing, ext_missing): 缺失/全空的 核心字段 / 增强字段 列表。
    """
    core_missing = [c for c in KLINE_CORE_FIELDS if not _field_available(df, c)]
    ext_missing = [c for c in extended_fields if not _field_available(df, c)]
    return core_missing, ext_missing


def check_kline_field_integrity(
    df: Any,
    symbol: str,
    logger: logging.Logger,
    extended_fields: tuple[str, ...] = KLINE_EXTENDED_FIELDS,
) -> bool:
    """数据契约字段完整性校验（GAP-151 分级）——代理填充前调用。

    - 核心字段不可用 → error 日志 + 返回 False（调用方跳过，宁缺毋滥）；
    - 增强字段缺失 → warning 日志（下游走代理值，缺口显式暴露）。

    Args:
        df: K 线 DataFrame
        symbol: 品种代码（日志上下文）
        logger: 调用方 logger（保持调用方命名空间的日志血缘）
        extended_fields: 本加载路径的增强字段子集（见 classify_kline_field_integrity）

    Returns:
        True=数据可用；False=核心字段不可用（调用方应跳过）。
    """
    core_missing, ext_missing = classify_kline_field_integrity(df, extended_fields)
    if core_missing:
        logger.error(
            "数据级监控 核心字段缺失[symbol=%s] 缺失/全空列=%s —— 数据不可用，跳过（宁缺毋滥）",
            symbol,
            core_missing,
        )
        return False
    if ext_missing:
        logger.warning(
            "数据级监控 增强字段缺失[symbol=%s] 缺失/全空列=%s（下游走代理值——请优先修复数据源）",
            symbol,
            ext_missing,
        )
    return True
