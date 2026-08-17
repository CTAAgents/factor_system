"""
fts.factor_engine.subchain_weight — L3 子链差异化权重调制（plans/47 §B）

背景
----
能化产业链 elite 因子存在"子链特异"现象（plans/47 §A 实证：196 因子中 10 个单链特异、
52 个部分链有效），而 L3 组合层（quality_weight）全链统一权重会把特异因子在无效子链
上的负贡献算进组合。本模块为 L3 合成环节提供**子链差异化权重调制矩阵**
`m[factor][子链]`：effective 子链 m=1.0，非 effective 子链按 zero/soft 降权或归零。

与 Step 1.8b 子链去冗余（portfolio_loop，管"数量"：单链 ≤ max_per_chain）互补——
本模块管"权重"（有效子链全权重、无效子链降权），二者先数量后权重串联。

语义（§B1）
    - subchain_scope in ("all", "unknown")            → 全部子链 m=1.0（兼容现状，未知不误杀）
    - subchain_scope 单链/部分链                      → effective 子链 m=1.0；
                                                        非 effective 子链按 decay_mode：
                                                        "zero" → 0.0；"soft" → |mean_ic|/max_chain_ic
    - 无 subchain_scope 画像字段的因子                 → 按 scope_default（默认 "all"=全链保留）
    - 未知子链/缺失品种映射                            → m=1.0 兜底（不破坏盲测池与新增品种）

接入点（§B2/B3）
    PortfolioLoop Step 2 合成前（market=="energy" 且 enable_subchain_weight 时）：
      先去冗余（Step 1.8b）→ 再调权（本模块）→ 合成/组合构建。

HARNESS §契约优先：SubchainWeightConfig / build_subchain_weights / apply_subchain_modulation
即对外契约；D2 子链暴露监控 compute_chain_exposure 供组合报告消费。

版本: v0.1.0（plans/47 §B5）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 非 effective 子链降权模式
DecayMode = str  # "zero" | "soft"


class SubchainWeightConfig(BaseModel):
    """L3 子链差异化权重调制配置（config/settings.yaml → l3.subchain_weight）。"""

    enabled: bool = Field(default=False, description="灰度开关（默认关，兼容现状）")
    decay_mode: DecayMode = Field(default="zero", description="非 effective 子链权重：zero=归零 / soft=按 |mean_ic| 相对缩放")
    soft_min_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="soft 模式最低保留比例（0.0=可归零，1.0=等效全链）")
    scope_default: str = Field(default="all", description="无 subchain_scope 画像因子的默认处理（all=全链保留，防误杀）")
    max_exposure_ratio: float = Field(default=0.50, ge=0.0, le=1.0, description="单子链暴露占比告警阈值（D2 监控用）")


def build_symbol_chain_map(chain_symbols: dict[str, list[str]]) -> dict[str, str]:
    """由 {子链: [品种]} 反查 {品种: 子链}（供 apply_subchain_modulation 按品种定位）。

    Args:
        chain_symbols: {子链名: [品种]}

    Returns:
        {品种: 子链名}；品种属于多个子链时后者覆盖（调用方应保证互斥，如 ENERGY_CHAIN_SUB_SYMBOLS）
    """
    mapping: dict[str, str] = {}
    for chain, syms in chain_symbols.items():
        for s in syms:
            mapping[s] = chain
    return mapping


def build_subchain_weights(
    factors: list[dict[str, Any]],
    chain_symbols: dict[str, list[str]],
    config: SubchainWeightConfig,
) -> dict[str, dict[str, float]]:
    """构建调制矩阵 {factor_id: {子链: m}}（plans/47 §B1/B5）。

    Args:
        factors: 因子列表，每项含 factor_id / subchain_scope / subchain_ic_profile
                 （DuckDB 加载路径从 metadata 兜底读取，L3 调用方负责注入）
        chain_symbols: {子链名: [品种]}
        config: 调制配置

    Returns:
        {factor_id: {子链: 权重 m}}
    """
    matrix: dict[str, dict[str, float]] = {}
    for f in factors:
        fid = f.get("factor_id", f.get("name", "?"))
        scope = f.get("subchain_scope")
        if scope is None:
            scope = config.scope_default  # 无画像字段 → scope_default（默认全链）
        prof = f.get("subchain_ic_profile") or {}
        row: dict[str, float] = {}
        for chain in chain_symbols:
            if scope in ("all", "unknown"):
                row[chain] = 1.0
                continue
            if not isinstance(scope, list):
                row[chain] = 1.0
                continue
            # 单链/部分链：effective 链全权重，非 effective 链按 decay_mode
            chain_stat = prof.get(chain) or {}
            if bool(chain_stat.get("effective", False)):
                row[chain] = 1.0
            elif config.decay_mode == "soft":
                mean_ic = abs(float(chain_stat.get("mean_ic") or 0.0))
                max_ic = max(
                    (abs(float((prof.get(c) or {}).get("mean_ic") or 0.0)) for c in scope if c in prof),
                    default=1e-9,
                )
                row[chain] = max(config.soft_min_ratio, mean_ic / max_ic)
            else:  # "zero"
                row[chain] = 0.0
        matrix[fid] = row
    return matrix


def apply_subchain_modulation(
    signal_matrix: np.ndarray,
    modulation: dict[str, dict[str, float]],
    symbol_chain: dict[str, str],
    factors: list[dict[str, Any]],
) -> np.ndarray:
    """信号矩阵按品种归属子链左乘 m[factor][子链]（plans/47 §B2，仅 market="energy" 调用）。

    Args:
        signal_matrix: 3D 信号矩阵 (n_dates, n_symbols, n_factors)
        modulation: build_subchain_weights 输出 {factor_id: {子链: m}}
        symbol_chain: {品种: 子链}（build_symbol_chain_map 输出）
        factors: 因子列表（signal_matrix 第三维顺序一致，每项含 factor_id）

    Returns:
        调制后信号矩阵（副本，不改原矩阵）

    语义：未知子链/缺失映射品种 m=1.0 兜底（不破坏盲测池与新增品种）；
    仅合成环节生效，不影响因子评估/审计产物。
    """
    out = np.array(signal_matrix, dtype=float, copy=True)
    if out.ndim != 3:
        raise ValueError(f"signal_matrix 必须为 3D (n_dates, n_symbols, n_factors)，got ndim={out.ndim}")
    syms = list(symbol_chain.keys())
    if out.shape[1] != len(syms):
        # 品种顺序由调用方保证与 symbol_chain 对齐；不一致时按名称匹配不适用，直接告警回退
        logger.warning(
            "[subchain_weight] signal_matrix 品种维度 %d 与 symbol_chain %d 不匹配，跳过调制",
            out.shape[1], len(syms),
        )
        return out
    for j, f in enumerate(factors):
        row = modulation.get(f.get("factor_id", f.get("name", "?")), {})
        for i, sym in enumerate(syms):
            w = row.get(symbol_chain.get(sym, ""), 1.0)  # 未知链兜底 1.0
            if w != 1.0:
                out[:, i, j] *= w
    return out


def compute_chain_exposure(
    modulation: dict[str, dict[str, float]],
    chain_symbols: dict[str, list[str]],
) -> dict[str, float]:
    """子链权重暴露占比（plans/47 §D2 监控）：{子链: 调制后权重占比}。

    以调制矩阵权重之和归一化——单子链占比超 max_exposure_ratio 时由调用方告警。

    Args:
        modulation: build_subchain_weights 输出
        chain_symbols: {子链名: [品种]}

    Returns:
        {子链: 占比 (0~1)}
    """
    total = 0.0
    chain_sum: dict[str, float] = {c: 0.0 for c in chain_symbols}
    for row in modulation.values():
        for chain, w in row.items():
            if chain in chain_sum:
                chain_sum[chain] += float(w)
            total += float(w)
    if total <= 1e-12:
        return {c: 0.0 for c in chain_symbols}
    return {c: s / total for c, s in chain_sum.items()}


__all__ = [
    "SubchainWeightConfig",
    "build_symbol_chain_map",
    "build_subchain_weights",
    "apply_subchain_modulation",
    "compute_chain_exposure",
]
