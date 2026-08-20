"""
fts/factor_engine/mhf_signals.py — 混合信号合成（Phase 2）。

截面选品种 + 时序定进出场：

1. **时序得分**：每品种按因子加权合成方向得分（反转逻辑：因子值高 → 看空，
   对 intraday_mom/pos_range/rev_mid/mom_mid 取负加权）
2. **截面选择**：每个时间点对各品种得分截面排序，最强 max_positions//2 个做多、
   最弱 max_positions//2 个做空，其余平仓（多空对冲、市场中性）

设计约束:
    - 零未来：合成仅用 ≤t 因子值（因子本身零未来）
    - 向量化截面排名，NaN 品种不参与选择（保持原仓位逻辑由回测引擎处理）
    - 周期无关：5m/15m 输入均可

设计文档: docs/archive/plans/33-mhf-trading-plan.md §Phase 2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 默认反转权重（阶段1 实测：反转因子 IC 强负，取负后为正信号）
DEFAULT_WEIGHTS: dict[str, float] = {
    "intraday_mom": 0.4,
    "pos_range": 0.3,
    "rev_mid": 0.2,
    "mom_mid": 0.1,
}


@dataclass
class MhfSignalConfig:
    """混合信号配置。

    Attributes:
        weights: 因子名 → 权重（反转逻辑，正值表示"高因子值→做空"）。
        max_positions: 同时持仓品种数（多空各半）。
        min_score: 得分绝对值低于该值不参与截面选择（可 0 关闭）。
    """

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    max_positions: int = 8
    min_score: float = 0.0

    def __post_init__(self) -> None:
        if self.max_positions < 2:
            raise ValueError(f"max_positions 必须 >= 2，收到 {self.max_positions}")
        if not self.weights:
            raise ValueError("weights 不能为空")


def build_hybrid_signals(
    factor_panel: dict[str, dict[str, pd.Series]],
    config: Optional[MhfSignalConfig] = None,
) -> dict[str, pd.Series]:
    """合成截面+时序混合信号。

    Args:
        factor_panel: {symbol: {factor_name: 因子值 Series}}（mhf_factors 输出）。
        config: 信号配置；None 用默认。

    Returns:
        {symbol: 方向信号 Series}（值 ∈ {-1.0, 0.0, +1.0}，索引为该品种 bar 时间轴）；
        无有效因子的品种跳过。
    """
    cfg = config or MhfSignalConfig()

    # 1) 每品种时序得分（反转：取负加权）
    scores: dict[str, pd.Series] = {}
    for sym, factors in factor_panel.items():
        s: Optional[pd.Series] = None
        for name, w in cfg.weights.items():
            f = factors.get(name)
            if f is None or f.empty:
                continue
            f = pd.to_numeric(f, errors="coerce").fillna(0.0)
            s = (-w * f) if s is None else s - w * f
        if s is not None and len(s.dropna()) > 0:
            scores[sym] = s.rename(sym)

    if not scores:
        return {}

    # 2) 截面排名（时间 × 品种）
    mat = pd.DataFrame(scores).sort_index()
    n_sym = mat.shape[1]
    long_n = max(1, cfg.max_positions // 2)
    short_n = max(1, cfg.max_positions // 2)
    ranks = mat.rank(axis=1, method="first", ascending=False, na_option="keep")

    sig = pd.DataFrame(0.0, index=mat.index, columns=mat.columns)
    sig[ranks <= long_n] = 1.0
    sig[ranks > n_sym - short_n] = -1.0
    if cfg.min_score > 0:
        sig[mat.abs() < cfg.min_score] = 0.0

    # 3) 输出按品种拆分
    return {sym: sig[sym].dropna() for sym in scores}
