"""
fts.factor_engine.subchain_profile — 子链适用性画像与显著性护栏（plans/47 §A）

背景
----
能化产业链 elite 因子存在"子链特异"现象（2026-08-17 实证：196 因子中 10 个单链特异、
52 个部分链有效、仅 132 个全链同向），而 L3 组合层全链统一权重会把特异因子在无效子链
上的负贡献算进组合。本模块为每个因子产出"子链适用性画像"，用统计检验（而非均值直觉）
判定各子链是否 effective，供 L3 子链差异化权重（plans/47 §B）与特异因子治理（§D）消费。

§A3 护栏判定链（三门槛 AND，任一不过 → effective=False）
    ① n_symbols ≥ min_symbols(=3)：剔除 NaN 后品种数，不足直接 False（防单/双品种决定结论）
    ② 单样本 t 检验：t = mean_ic / (std_ic / √n)，自由度 df = n − 1 = 2，要求 |t| ≥ min_t_stat(=2.0)
    ③ |mean_ic| ≥ min_chain_ic(=0.10)

df=2 的 t 分布双侧临界值（scipy.stats.t.ppf）：
    |t|    p        含义
    1.00   0.423    极不显著（门槛下）
    1.50   0.273    不显著（门槛下）
    2.00   0.184    默认门槛 min_t_stat（刻意宽松）
    2.92   0.050    5% 显著
    4.30   0.010    1% 显著
    解读：n=3 小样本 t 分布肥尾，df=2 下 |t|=2 仅是弱支持；护栏目标不是"证明强显著"
    （强显著由演化/审计链负责），而是滤掉方向混杂与噪声 → 门槛刻意宽松。

几何含义
    |t| ≥ 2 ⇔ |mean_ic| ≥ (2/√3)·std ≈ 1.155·std —— 3 品种 IC 必须高度一致才有效。
    t 检验隐式编码"方向一致性"：符号混杂 → std 大 → t 小，无需单独检查同号。

工程实现约定
    - std_ic = np.std(ics, ddof=1)（样本标准差；n=3 用总体 std 会低估噪声）
    - std_ic < 1e-12 视为完全一致 → t = +inf，effective 仅由门槛③决定
    - NaN IC 品种先剔除再重算 n；n < min_symbols 走门槛①
    - p_value = scipy.stats.t.sf(|t|, df) * 2（t=inf → p=0）
    - 全部阈值参数化（SubchainProfileConfig），禁硬编码

保守性设计
    误标（把全链因子裁成单链）损失 alpha 与多样性 > 漏标（维持现状）——护栏偏向漏标；
    t 不显著 → 保持全链（plans/47 §D3 防误伤），决策权交给显著性而非均值直觉。

HARNESS §契约优先：SubchainProfile / ChainStat 即对外契约；A2 落库
（factor_catalog.metadata.subchain_ic_profile / subchain_scope）由实施阶段接线。

版本: v0.1.0（草案，plans/47 §A）
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from typing import Any, Optional, Union

import numpy as np
from pydantic import BaseModel, Field
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)

# 完全一致判定阈值（std 相对机器精度视为 0 → t=∞ 兜底）
_STD_ZERO_EPS = 1e-12

# 子链适用性 scope 类型：单链/部分链 = 链名列表；"all"/"unknown" 为语义标记
SubchainScope = Union[list[str], str]


class SubchainProfileConfig(BaseModel):
    """子链显著性护栏参数（config/settings.yaml → subchain_profile.*，禁硬编码）。"""

    min_symbols: int = Field(default=3, ge=1, description="子链内最小品种数（门槛①）")
    min_t_stat: float = Field(default=2.0, gt=0.0, description="|t| 门槛（门槛②，df=n-1）")
    min_chain_ic: float = Field(default=0.02, ge=0.0, description="|mean_ic| 门槛（门槛③）")
    all_chains_effective_min: int = Field(
        default=3, ge=1, description="≥ 此子链数 effective 时 scope='all'"
    )


class ChainStat(BaseModel):
    """单子链统计画像（对外契约，含审计留痕字段）。"""

    n_symbols: int
    mean_ic: Optional[float] = None
    std_ic: Optional[float] = None
    t_stat: Optional[float] = None
    p_value: Optional[float] = None
    effective: bool


class SubchainProfile(BaseModel):
    """因子子链适用性画像（对外契约，plans/47 §A2）。"""

    factor_id: str
    chain_stats: dict[str, ChainStat]
    subchain_scope: SubchainScope = "unknown"
    subchain_specific: bool = False


def _chain_effective(ics: list[float], cfg: SubchainProfileConfig) -> ChainStat:
    """单子链有效性判定（plans/47 §A3，三门槛 AND，任一不过 → effective=False）。

    Args:
        ics: 子链内各品种的 IC 值（可能含 None/NaN）
        cfg: 护栏参数

    Returns:
        ChainStat — 含 n/mean/std/t/p/effective；门槛①拦截时统计字段为 None。
    """
    # ① 剔除 NaN/None 后重算 n（NaN 当 0 计会污染均值和方差）
    ics = [float(v) for v in ics if v is not None and not np.isnan(v)]
    n = len(ics)
    if n < cfg.min_symbols:  # 门槛①：品种数不足，不给机会（防单/双品种决定结论）
        return ChainStat(
            n_symbols=n, mean_ic=None, std_ic=None, t_stat=None, p_value=None, effective=False
        )
    mean = float(np.mean(ics))
    std = float(np.std(ics, ddof=1))  # 样本标准差（n=3 必须 ddof=1；总体 std 低估噪声）
    if std < _STD_ZERO_EPS:  # 完全一致 → t=∞ 兜底，effective 仅由门槛③决定
        t_stat, p_value = math.inf, 0.0
    else:
        t_stat = mean / (std / math.sqrt(n))  # 门槛②：单样本 t 检验，df = n − 1 = 2
        p_value = float(sp_stats.t.sf(abs(t_stat), df=n - 1) * 2)  # 双侧 p，审计留痕
    effective = abs(t_stat) >= cfg.min_t_stat and abs(mean) >= cfg.min_chain_ic  # ② AND ③
    return ChainStat(
        n_symbols=n, mean_ic=mean, std_ic=std, t_stat=t_stat, p_value=p_value, effective=effective
    )


def compute_subchain_profile(
    factor_id: str,
    symbol_ic: dict[str, float],
    chain_symbols: dict[str, list[str]],
    cfg: Optional[SubchainProfileConfig] = None,
) -> SubchainProfile:
    """计算因子子链适用性画像（plans/47 §A1/A3）。

    Args:
        factor_id: 因子 ID（画像/落库标识）
        symbol_ic: 逐品种时序 IC（evaluation.level_1_backtest.symbol_ic）
        chain_symbols: {子链名: [品种]}（energy 用 portfolio_loop.ENERGY_CHAIN_SUB_SYMBOLS）
        cfg: 护栏参数（None → 默认）

    Returns:
        SubchainProfile — 各子链 ChainStat + 派生 scope/specific：
          - effective ≥ all_chains_effective_min → scope="all"、specific=False
          - 仅 1 链 effective → scope=[链]、specific=True（§D1 受限使用）
          - 2 链 effective → scope=[链1,链2]、specific=False
          - 无链 effective → scope="unknown"、specific=False（保守性设计 §D3，保持全链）
    """
    cfg = cfg or SubchainProfileConfig()
    if not symbol_ic or not chain_symbols:
        return SubchainProfile(factor_id=factor_id, chain_stats={}, subchain_scope="unknown", subchain_specific=False)

    stats: dict[str, ChainStat] = {}
    for chain, syms in chain_symbols.items():
        ics = [symbol_ic[s] for s in syms if s in symbol_ic]
        stats[chain] = _chain_effective(ics, cfg)

    effective_chains = [c for c, st in stats.items() if st.effective]
    if len(effective_chains) >= cfg.all_chains_effective_min:
        scope: SubchainScope = "all"
        specific = False
    elif len(effective_chains) == 1:
        scope = effective_chains
        specific = True
    elif len(effective_chains) > 1:
        scope = effective_chains
        specific = False
    else:
        scope = "unknown"
        specific = False
    logger.info(
        "[subchain_profile] factor=%s scope=%s specific=%s effective_chains=%s",
        factor_id, scope, specific, effective_chains,
    )
    return SubchainProfile(
        factor_id=factor_id, chain_stats=stats, subchain_scope=scope, subchain_specific=specific
    )


def build_subchain_metadata(
    factor_id: str,
    symbol_ic: dict[str, float],
    chain_symbols: Optional[dict[str, list[str]]] = None,
    cfg: Optional[SubchainProfileConfig] = None,
) -> dict[str, Any]:
    """构建可写入 factor_catalog.metadata 的子链画像字段（plans/47 §A2 落库）。

    Args:
        factor_id: 因子 ID
        symbol_ic: 逐品种时序 IC（evaluation.level_1_backtest.symbol_ic）
        chain_symbols: {子链名: [品种]}（None → 懒加载 ENERGY_CHAIN_SUB_SYMBOLS）
        cfg: 护栏参数（None → 默认）

    Returns:
        {"subchain_ic_profile": {子链: ChainStat dict}, "subchain_scope": ..., "subchain_specific": bool}

    落库安全：t=±inf（std 兜底场景）序列化为 None，避免 DuckDB JSON 存非标准 Infinity。
    """
    if chain_symbols is None:
        try:  # 懒加载，避免模块级循环依赖
            from fts.factor_engine.portfolio_loop import ENERGY_CHAIN_SUB_SYMBOLS

            chain_symbols = ENERGY_CHAIN_SUB_SYMBOLS
        except Exception:  # noqa: BLE001
            chain_symbols = {}
    prof = compute_subchain_profile(factor_id, symbol_ic, chain_symbols, cfg)
    profile_dict: dict[str, Any] = {}
    for chain, st in prof.chain_stats.items():
        stat = st.model_dump()
        if isinstance(stat.get("t_stat"), float) and math.isinf(stat["t_stat"]):
            stat["t_stat"] = None  # JSON 安全（effective 已承载判定结论）
        profile_dict[chain] = stat
    return {
        "subchain_ic_profile": profile_dict,
        "subchain_scope": prof.subchain_scope,
        "subchain_specific": prof.subchain_specific,
    }


def build_subchain_quality_rows(
    factor_id: str,
    market: str,
    symbol_ic: dict[str, float],
    chain_symbols: Optional[dict[str, list[str]]] = None,
    cfg: Optional[SubchainProfileConfig] = None,
    source: str = "promotion",
    evaluated_at: Optional[str] = None,
    period_end: Optional[str] = None,
) -> list[dict[str, Any]]:
    """画像 → ``subchain_factor_quality`` 矩阵行（plans/49 §A2，每子链一行）。

    供评估晋升/评审巡检/生命周期接入点将当前子链画像写入质量矩阵时序
    （SSOT 存储由 ``SubchainQualityRepository.save_subchain_quality`` 负责）。

    Args:
        factor_id: 因子 ID
        market: 市场（energy/futures）
        symbol_ic: 逐品种时序 IC（当前评估窗口）
        chain_symbols: {子链名: [品种]}（None → 懒加载 ENERGY_CHAIN_SUB_SYMBOLS）
        cfg: 护栏参数（None → 默认）
        source: 数据来源（promotion | review | inspect | lifecycle）
        evaluated_at: 评估时间（ISO；None → 当前 UTC）
        period_end: 窗口截止日（ISO date；None → 今天）

    Returns:
        [{factor_id, market, chain, evaluated_at, period_end, n_symbols, mean_ic,
          std_ic, t_stat, p_value, effective, source, decision}]——空 symbol_ic/无子链
        映射时返回 []。t=±inf（std 兜底）序列化为 None（JSON/DuckDB 安全）。
    """
    if chain_symbols is None:
        try:  # 懒加载，避免模块级循环依赖
            from fts.factor_engine.portfolio_loop import ENERGY_CHAIN_SUB_SYMBOLS

            chain_symbols = ENERGY_CHAIN_SUB_SYMBOLS
        except Exception:  # noqa: BLE001
            chain_symbols = {}
    prof = compute_subchain_profile(factor_id, symbol_ic, chain_symbols, cfg)
    if not prof.chain_stats:
        return []
    ts = evaluated_at or datetime.now(timezone.utc).isoformat()
    end = period_end or date.today().isoformat()
    rows: list[dict[str, Any]] = []
    for chain, st in prof.chain_stats.items():
        t = st.t_stat
        rows.append(
            {
                "factor_id": factor_id,
                "market": market,
                "chain": chain,
                "evaluated_at": ts,
                "period_end": end,
                "n_symbols": st.n_symbols,
                "mean_ic": st.mean_ic,
                "std_ic": st.std_ic,
                "t_stat": None if (isinstance(t, float) and math.isinf(t)) else t,
                "p_value": st.p_value,
                "effective": st.effective,
                "source": source,
                "decision": "keep",
            }
        )
    return rows


__all__ = [
    "SubchainProfileConfig",
    "ChainStat",
    "SubchainProfile",
    "compute_subchain_profile",
    "build_subchain_metadata",
    "build_subchain_quality_rows",
]
