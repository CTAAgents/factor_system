"""
fts.factor_engine.regime_gate — 子链级方向 Gate（plans/48 §A）

背景
----
期货品种是独立市场类型，子链 Regime（趋势/波动/基差结构）直接提供该市场的方向与
收益来源语境。本模块把 `SectorRegimeSelector.detect_all` 产出的子链 regime 从
"合成全局主制度"升级为"**子链独立参与 Gate**"：

  - long / short：子链 regime 方向明确（bull/bear 且置信度 ≥ min_confidence），
    仅放开对应方向信号（long 子链过滤负分、short 子链过滤正分）
  - avoid：置信度 < min_confidence（方向不明）——hard=剔除 / soft=按 ratio 降权
  - neutral：其余 regime（oscillate/low_vol 等），不 gate，交因子层决定

盲测池/无子链归属品种：`blind_default="avoid"`（默认回避，不放行）或 `"neutral"`（保留）。

与 plans/47（幅度层）串联：Gate（方向层，本模块）→ 子链权重调制（幅度层，
subchain_weight.py）→ 因子合成——构成"能否（Regime）× 怎么赚+赚多少（因子）"正交。

HARNESS §契约优先：GateConfig / GateDecision / build_subchain_gates /
apply_subchain_gate 即对外契约；D 模块灰度开关（--enable-regime-gating）后续接线。

版本: v0.1.0（plans/48 §A）
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GateConfig(BaseModel):
    """子链方向 Gate 配置（config/settings.yaml → l3.regime_gating，禁硬编码）。"""

    enabled: bool = Field(default=False, description="灰度开关（默认关，兼容现状）")
    min_confidence: float = Field(default=0.55, ge=0.0, le=1.0,
        description="子链 regime 置信度门槛：bull/bear 且置信度 ≥ 门槛才给方向；否则 avoid（方向不明不参与）")
    avoid_mode: str = Field(default="hard", description="avoid 模式：hard=剔除（零持仓）/ soft=按 soft_avoid_ratio 降权（小仓位保留）")
    soft_avoid_ratio: float = Field(default=0.3, ge=0.0, le=1.0,
        description="soft_avoid 降权系数（0.3=保留 30% 暴露，连续过渡防 cliff）")
    blind_default: str = Field(default="avoid",
        description="无子链归属品种（盲测池等）默认处理：avoid=回避（不放行）/ neutral=保留")
    # plans/48 §B：品种暴露缩放映射（子链置信度 → 暴露系数）
    exposure_min: float = Field(default=0.4, ge=0.0, le=1.0,
        description="暴露映射下限：置信度 < exposure_min → 暴露 0（不参与）")
    exposure_sat: float = Field(default=0.7, ge=0.0, le=1.0,
        description="暴露映射饱和点：置信度 ≥ exposure_sat → 暴露 1.0（满仓）")


class GateDecision(TypedDict):
    """子链 Gate 判定结果（对外契约）。"""

    regime: str
    confidence: float
    decision: str  # "long" | "short" | "avoid" | "neutral"


def build_symbol_chain_map(chain_symbols: dict[str, list[str]]) -> dict[str, str]:
    """由 {子链: [品种]} 反查 {品种: 子链}（供 apply_subchain_gate 按品种定位 Gate）。

    Args:
        chain_symbols: {子链名: [品种]}

    Returns:
        {品种: 子链名}；品种属于多个子链时后者覆盖（调用方保证互斥）
    """
    mapping: dict[str, str] = {}
    for chain, syms in chain_symbols.items():
        for s in syms:
            mapping[s] = chain
    return mapping


def build_subchain_gates(
    sector_regimes: dict[str, dict[str, Any]],
    chain_symbols: dict[str, list[str]],
    config: GateConfig,
) -> dict[str, GateDecision]:
    """构建子链方向 Gate（plans/48 §A1）。

    Args:
        sector_regimes: SectorRegimeSelector.detect_all 输出 {子链: {regime, confidence, ...}}
        chain_symbols: {子链名: [品种]}
        config: Gate 配置

    Returns:
        {子链: GateDecision}
          - bull/bear 且 conf ≥ min_confidence → long/short
          - 有检测但 conf < min_confidence（方向不明）→ avoid
          - 其余（oscillate/low_vol 等已明确非方向 regime）→ neutral（不 gate）
          - 无检测数据子链 → neutral（缺数据不误杀）
    """
    gates: dict[str, GateDecision] = {}
    for chain in chain_symbols:
        r = sector_regimes.get(chain)
        if not r:
            gates[chain] = {"regime": "unknown", "confidence": 0.0, "decision": "neutral"}
            continue
        regime = str(r.get("regime", "unknown"))
        conf = float(r.get("confidence", 0.0) or 0.0)
        if regime in ("bull", "bear") and conf >= config.min_confidence:
            decision = "long" if regime == "bull" else "short"
        elif regime in ("bull", "bear"):
            decision = "avoid"  # 有方向判定但置信度不足 → 方向不明不参与
        else:
            decision = "neutral"  # oscillate/high_vol/low_vol/unknown 非方向 regime，不 gate（交因子层）
        gates[chain] = {"regime": regime, "confidence": round(conf, 4), "decision": decision}
    return gates


def apply_subchain_gate(
    sym_scores: dict[str, float],
    gates: dict[str, GateDecision],
    symbol_chain: dict[str, str],
    config: GateConfig,
) -> dict[str, float]:
    """对品种综合得分应用子链 Gate（plans/48 §A2，先于全局方向偏置）。

    Args:
        sym_scores: 品种 → 综合得分
        gates: build_subchain_gates 输出
        symbol_chain: {品种: 子链}（build_symbol_chain_map 输出）
        config: Gate 配置

    Returns:
        调制后得分（副本）：
          - avoid（hard）→ 0.0（剔除，不进多空候选）
          - avoid（soft）→ score × soft_avoid_ratio（小仓位保留）
          - long → 负分置 0（仅放开多头信号）
          - short → 正分置 0（仅放开空头信号）
          - neutral → 不变
          - 无子链归属品种：blind_default="avoid" → 0.0；"neutral" → 保留
    """
    out = dict(sym_scores)
    for sym, score in out.items():
        chain = symbol_chain.get(sym)
        if chain is None or chain not in gates:
            if config.blind_default == "avoid":
                out[sym] = 0.0
            continue
        d = gates[chain]["decision"]
        if d == "avoid":
            if config.avoid_mode == "hard":
                out[sym] = 0.0
            else:
                out[sym] = score * config.soft_avoid_ratio
        elif d == "long":
            if score < 0:
                out[sym] = 0.0
        elif d == "short":
            if score > 0:
                out[sym] = 0.0
        # neutral: 不 gate
    return out


def map_confidence_to_exposure(confidence: float, config: GateConfig) -> float:
    """置信度 → 暴露系数分段映射（plans/48 §B1）。

    Args:
        confidence: 子链 regime 置信度 (0~1)
        config: Gate 配置（exposure_min / exposure_sat）

    Returns:
        暴露系数：
          - confidence < exposure_min（0.4）→ 0.0（不参与）
          - exposure_min ≤ confidence < exposure_sat → 线性插值
          - confidence ≥ exposure_sat（0.7）→ 1.0（满仓）
    """
    if confidence < config.exposure_min:
        return 0.0
    if confidence >= config.exposure_sat:
        return 1.0
    span = max(config.exposure_sat - config.exposure_min, 1e-9)
    return (confidence - config.exposure_min) / span


def apply_exposure_scale(
    sym_scores: dict[str, float],
    gates: dict[str, GateDecision],
    symbol_chain: dict[str, str],
    alignment_scores: dict[str, float],
    config: GateConfig,
) -> dict[str, float]:
    """品种暴露缩放（plans/48 §B2/B3）：暴露 = 子链置信度映射 × 品种-链对齐度。

    Args:
        sym_scores: 品种 → 综合得分（A 模块 Gate 应用后）
        gates: build_subchain_gates 输出（含子链置信度）
        symbol_chain: {品种: 子链}
        alignment_scores: {品种: 对齐度 0~1}（compute_alignment 输出，品种偏离子链时额外收缩）
        config: Gate 配置

    Returns:
        暴露缩放后得分（副本）：
          - 已剔除品种（score=0，avoid-hard/盲测回避）→ 跳过（防双重惩罚，B3）
          - avoid-soft 链品种：A 模块已按 ratio 降权 → 保留（B 模块不二次缩放）
          - 其余品种 × [map_confidence_to_exposure(子链置信度) × 对齐度]
          - 无子链归属品种 → 按 blind_default 处理
    """
    out = dict(sym_scores)
    for sym, score in out.items():
        if score == 0.0:
            continue  # 已被 A 模块 Gate 剔除（avoid/blind）→ 防双重惩罚
        chain = symbol_chain.get(sym)
        if chain is None or chain not in gates:
            if config.blind_default == "avoid":
                out[sym] = 0.0
            continue
        gate = gates[chain]
        if gate["decision"] == "avoid":
            continue  # avoid 链已由 A 模块处理（hard=0 / soft=×ratio），B 模块不二次缩放
        exp = map_confidence_to_exposure(gate["confidence"], config) * alignment_scores.get(sym, 0.5)
        out[sym] = score * exp
    return out


def gate_scale_map(
    gates: dict[str, GateDecision],
    config: GateConfig,
) -> dict[str, float]:
    """Gate 决策 → 链级权重缩放系数（plans/50 §A1，L3 权重层消费）。

    供 L3 Step 2.5 并入子链调制矩阵（m'[factor][子链] = m × gate_scale），
    使 Gate 的 avoid 回避在**权重源头**生效，与信号管道 Step 3h1 方向过滤
    乘性串联（权重层已归零/降权 → 信号层对 0 得分跳过，无双重惩罚）。

    Args:
        gates: build_subchain_gates 输出 {子链: GateDecision}
        config: Gate 配置

    Returns:
        {子链: 缩放系数}：
          - avoid + avoid_mode=hard → 0.0（该链权重归零，不参与组合）
          - avoid + avoid_mode=soft → soft_avoid_ratio（小仓位保留，连续过渡）
          - long / short / neutral → 1.0（方向过滤属信号层 Step 3h1 职责，
            权重层不重复做方向过滤；neutral 不干预交因子层）

    注：gates 由 build_subchain_gates 枚举 chain_symbols 全量构建，无缺链场景；
    blind_default 语义在 build_subchain_gates（无检测子链→neutral）与信号层
    apply_subchain_gate（无归属品种）处理，本函数不重复。
    """
    out: dict[str, float] = {}
    for chain, g in gates.items():
        if g["decision"] == "avoid":
            out[chain] = (
                0.0 if config.avoid_mode == "hard" else config.soft_avoid_ratio
            )
        else:
            out[chain] = 1.0  # long/short/neutral 权重层不干预
    return out


__all__ = [
    "GateConfig",
    "GateDecision",
    "build_symbol_chain_map",
    "build_subchain_gates",
    "apply_subchain_gate",
    "map_confidence_to_exposure",
    "apply_exposure_scale",
    "gate_scale_map",
]
