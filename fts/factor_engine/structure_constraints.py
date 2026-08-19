"""
fts.factor_engine.structure_constraints — 期货特有结构搜索空间约束（v2.105.0+32，任务 B）。

基于**真实可得权威数据**（QuantData，见 data_sources.quantdata_provider）对演化挖掘
施加"期货特有结构"约束，防空谈因子：

  R1 结构字段完整性   — 候选使用结构字段（hold/settle 衍生）时，字段必须落在
                        字段权威矩阵可得层（L0/L1）；L2 缺失字段（fundamental 类）禁依赖
  R2 子链有效性（软约束）— energy 市场候选在有效子链上 IC 不显著（subchain_profile
                        三门槛）→ 标记 structure.subchain_invalid 降权（灰度，不硬拦截）
  R3 结构信号去冗余   — 候选信号与既有结构因子信号截面相关 > 阈值 → 标记冗余
  R4 家族筛选 API     — get_seeds_by_family：按 YAML 家族文件筛选种子，演化通道可限定
                        家族内搜索
  R5 期限结构接线     — term_spread/roll_yield 已由 QuantDataProvider.get_term_structure
                        权威构建（P1），演化候选可消费（D15 算子 ts_term_spread/ts_roll_yield）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── 配置与常量 ─────────────────────────────────────────

# 既有"期货结构因子"名称（R3 冗余比对基准；来源：seeds/futures 结构家族 + 演化晋升）
STRUCTURE_FACTOR_NAMES: frozenset[str] = frozenset(
    {
        "fut_roll_yield_carry", "fut_stable_term_structure", "fut_basis_factor",
        "fut_open_interest_full", "fut_hedge_pressure", "fut_warehouse_receipt",
        "fut_volatility_term_structure", "fut_momentum_oi_confirmation",
        "fut_carry_momentum", "fut_basis_momentum", "fut_oi_trend",
    }
)

# 结构字段（面板可得，R1 校验允许集）
STRUCTURE_FIELDS: frozenset[str] = frozenset(
    {"hold", "settle", "term_spread", "roll_yield", "open", "high", "low", "close", "volume", "vwap"}
)


@dataclass
class StructureConstraintConfig:
    """结构约束配置（软约束灰度，默认不硬拦截）。"""

    subchain_waiver_enabled: bool = True      # R2：energy 子链有效性降权标记（软）
    redundancy_corr_threshold: float = 0.95   # R3：结构信号冗余相关阈值
    subchain_min_t_stat: float = 2.0          # R2：子链 t 检验门槛（复用 SubchainProfileConfig）
    hard_block_l2_fields: bool = True         # R1：L2 缺失字段硬拦截（防空谈因子）
    extra: dict[str, Any] = field(default_factory=dict)


# ─── R1 结构字段完整性 ───────────────────────────────────


def check_structure_fields(
    input_fields: list[str] | tuple[str, ...],
    config: Optional[StructureConstraintConfig] = None,
) -> dict[str, Any]:
    """R1：校验候选因子结构字段可得性。

    Returns:
        {"ok", "fields": [...], "l2_missing": [...], "blocked": bool}
        blocked=True 表示含 L2 缺失字段（fundamental 类，防依赖防空谈因子）。
    """
    cfg = config or StructureConstraintConfig()
    from ..data_sources.quantdata_provider import (
        L2_MISSING_FIELDS,
        L1_FALLBACK_FIELDS,
        L0_AUTHORITATIVE_FIELDS,
        L0_STRUCTURE_FIELDS,
    )

    l2_missing = [f for f in input_fields if f in L2_MISSING_FIELDS]
    ok_fields = [
        f for f in input_fields
        if f in L0_AUTHORITATIVE_FIELDS or f in L0_STRUCTURE_FIELDS or f in L1_FALLBACK_FIELDS
    ]
    blocked = bool(l2_missing) and cfg.hard_block_l2_fields
    return {
        "ok": not blocked,
        "fields": list(ok_fields),
        "l2_missing": l2_missing,
        "blocked": blocked,
    }


# ─── R2 子链有效性（软约束） ─────────────────────────────


def check_subchain_effectiveness(
    factor_id: str,
    symbol_ic: dict[str, float],
    chain_symbols: dict[str, list[str]],
    config: Optional[StructureConstraintConfig] = None,
) -> dict[str, Any]:
    """R2：候选在子链上的有效性检查（复用 subchain_profile 三门槛）。

    Returns:
        {"scope", "subchain_specific", "effective_chains", "subchain_invalid", "detail"}
        subchain_invalid=True = 无任何有效子链 → 演化端降权标记（软约束，不硬拦截）。
        无子链画像数据（symbol_ic/chain_symbols 不足）→ 返回 neutral（不误判）。
    """
    cfg = config or StructureConstraintConfig()
    # 无子链输入（品种 IC 或子链映射缺失）→ 直接 neutral，不误判
    if not symbol_ic or not chain_symbols:
        return {
            "scope": "unknown", "subchain_specific": False,
            "effective_chains": [], "subchain_invalid": False, "detail": "profile_unavailable",
        }
    try:
        from .subchain_profile import SubchainProfileConfig, compute_subchain_profile

        profile_cfg = SubchainProfileConfig(min_t_stat=cfg.subchain_min_t_stat)
        profile = compute_subchain_profile(
            factor_id, symbol_ic, chain_symbols, cfg=profile_cfg
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[structure.R2] 子链画像计算失败（数据不足，neutral）: %s", e)
        return {
            "scope": "unknown", "subchain_specific": False,
            "effective_chains": [], "subchain_invalid": False, "detail": "profile_unavailable",
        }

    scope = profile.subchain_scope
    effective = [
        name for name, st in profile.chain_stats.items() if st.effective
    ]
    # 软约束：无任何有效子链 → 标记 invalid（energy 市场由演化端消费降权）
    invalid = scope in ("unknown", "none", None) and not effective
    return {
        "scope": scope,
        "subchain_specific": bool(profile.subchain_specific),
        "effective_chains": effective,
        "subchain_invalid": invalid and cfg.subchain_waiver_enabled,
        "detail": "no_effective_chain" if invalid else "ok",
    }


# ─── R3 结构信号去冗余 ───────────────────────────────────


def check_structure_redundancy(
    signal: Any,
    existing_signals: dict[str, Any],
    threshold: float | None = None,
) -> dict[str, Any]:
    """R3：候选信号与既有结构因子信号的截面相关性（Spearman）。

    Args:
        signal: 候选信号（pd.Series，Index=日期，多品种时用截面 rank 计算）
        existing_signals: {factor_name: signal_series}
        threshold: 相关阈值（默认 0.95）

    Returns:
        {"ok", "max_corr", "conflict_with", "detail"}
    """
    import pandas as pd

    thr = threshold if threshold is not None else StructureConstraintConfig().redundancy_corr_threshold
    if signal is None or not existing_signals:
        return {"ok": True, "max_corr": 0.0, "conflict_with": None, "detail": "no_baseline"}
    try:
        s = pd.Series(signal, dtype="float64")
        max_corr, conflict = 0.0, None
        for name, base in existing_signals.items():
            if base is None or len(base) == 0:
                continue
            common = pd.concat([s.rename("s"), pd.Series(base, name="b")], axis=1).dropna()
            if len(common) < 10:
                continue
            corr = common["s"].rank().corr(common["b"].rank())
            if pd.notna(corr) and abs(corr) > max_corr:
                max_corr, conflict = abs(corr), name
        ok = max_corr <= thr
        return {
            "ok": bool(ok),
            "max_corr": float(max_corr),
            "conflict_with": conflict,
            "detail": f"corr={max_corr:.3f}" if conflict else "no_conflict",
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("[structure.R3] 冗余检查异常（跳过）: %s", e)
        return {"ok": True, "max_corr": 0.0, "conflict_with": None, "detail": "error_skipped"}


# ─── R4 家族筛选 API ─────────────────────────────────────


_FUTURES_SEED_FAMILIES: dict[str, str] = {
    "momentum": "seeds/futures/momentum.yaml",
    "term_structure": "seeds/futures/term_structure.yaml",
    "position_flow": "seeds/futures/position_flow.yaml",
    "liquidity": "seeds/futures/liquidity.yaml",
    "higher_moments": "seeds/futures/higher_moments.yaml",
    "volatility": "seeds/futures/volatility.yaml",
    "fundamental": "seeds/futures/fundamental.yaml",
    "crowding": "seeds/futures/crowding.yaml",
    "alpha_behavior": "seeds/futures/alpha_behavior.yaml",
    "high_frequency": "seeds/futures/high_frequency.yaml",
    "options": "seeds/futures/options.yaml",
    "market_regime": "seeds/futures/market_regime.yaml",
    "cta_registry": "seeds/futures/cta_registry.yaml",
    "operator_dict": "seeds/futures/operator_dict.yaml",
    "vnpy_cta": "seeds/futures/vnpy_cta.yaml",
    "wind_cta": "seeds/futures/wind_cta.yaml",
    "mc_cta": "seeds/futures/mc_cta.yaml",
    "academic_papers": "seeds/futures/academic_papers.yaml",
    "broker_reports": "seeds/futures/broker_reports.yaml",
    "tinysoft": "seeds/futures/tinysoft.yaml",
}


def get_seeds_by_family(family: str, market: str = "futures") -> list[Any]:
    """R4：按 YAML 家族文件加载种子因子（演化通道 --families 限定搜索）。

    Args:
        family: 家族名（如 "momentum"/"term_structure"/"position_flow"，不含 .yaml）
        market: 市场（futures）

    Returns:
        list[FactorProgram]；家族不存在或加载失败返回空列表。
    """
    if market != "futures" or family not in _FUTURES_SEED_FAMILIES:
        return []
    try:
        from .seed_loader import load_factors_from_yaml

        return load_factors_from_yaml(_FUTURES_SEED_FAMILIES[family])
    except Exception as e:  # noqa: BLE001
        logger.warning("[structure.R4] 家族加载失败 [%s]: %s", family, e)
        return []


def list_families() -> list[str]:
    """R4：列出全部期货 YAML 种子家族名。"""
    return sorted(_FUTURES_SEED_FAMILIES.keys())


# ─── 汇总评估（演化端接入点） ─────────────────────────────


def evaluate_structure_constraints(
    factor: dict[str, Any],
    *,
    symbol_ic: Optional[dict[str, float]] = None,
    chain_symbols: Optional[dict[str, list[str]]] = None,
    signal: Any = None,
    existing_signals: Optional[dict[str, Any]] = None,
    config: Optional[StructureConstraintConfig] = None,
) -> dict[str, Any]:
    """汇总执行 R1-R3 结构约束（R4 为独立 API）。

    Returns:
        {"r1_field": {...}, "r2_subchain": {...}, "r3_redundancy": {...},
         "structure_ok": bool, "warnings": [...]}
    """
    cfg = config or StructureConstraintConfig()
    signature = factor.get("signature") or {}
    input_fields = list(signature.get("input_fields") or [])

    r1 = check_structure_fields(input_fields, cfg)
    r2 = (
        check_subchain_effectiveness(factor.get("factor_id", factor.get("name", "")),
                                     symbol_ic or {}, chain_symbols or {}, cfg)
        if symbol_ic and chain_symbols
        else {"scope": "unknown", "subchain_invalid": False, "detail": "no_subchain_input"}
    )
    r3 = (
        check_structure_redundancy(signal, existing_signals or {}, cfg.redundancy_corr_threshold)
        if signal is not None
        else {"ok": True, "detail": "no_signal_input"}
    )

    warnings: list[str] = []
    if not r1["ok"]:
        warnings.append(f"L2 缺失字段禁依赖: {r1['l2_missing']}")
    if r2.get("subchain_invalid"):
        warnings.append("无有效子链（软约束降权，灰度不硬拦截）")
    if not r3["ok"]:
        warnings.append(f"与结构因子冗余: {r3['conflict_with']} (corr={r3['max_corr']:.3f})")

    return {
        "r1_field": r1,
        "r2_subchain": r2,
        "r3_redundancy": r3,
        "structure_ok": r1["ok"] and r2.get("subchain_invalid") is False and r3["ok"],
        "warnings": warnings,
    }


__all__ = [
    "StructureConstraintConfig",
    "STRUCTURE_FACTOR_NAMES",
    "STRUCTURE_FIELDS",
    "check_structure_fields",
    "check_subchain_effectiveness",
    "check_structure_redundancy",
    "get_seeds_by_family",
    "list_families",
    "evaluate_structure_constraints",
]
