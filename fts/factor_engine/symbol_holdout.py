"""标的留出验证（GAP-075，跨标的稳健性检查·方案 2）。

同市场学习/选股同池时（如 csi300 全量 300 只学习 + 同池选股），因子可能只对
参与学习的标的有效（记忆池子历史模式）。本模块按**行业分层**留出 20% 验证集：

1. 训练集（80% 标的）：计算截面 IC 并据此确定因子方向（避免用留出集选方向）；
2. 留出集（20% 标的）：用同一方向计算截面 IC，衡量"未参与训练标的"上的预测力；
3. 输出 IC 保持率（holdout_ic / train_ic）与 passed 判定（留出 IC > 0 且保持率达标）。

行业映射缺失时回退随机留出（seed 固定，结果可复现）；留出集过小无法计算截面
IC 时返回 None（调用方将该审计项标记 skipped，不阻断主流程）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


@dataclass
class SymbolHoldoutConfig:
    """标的留出验证配置（契约见 01-architecture GAP-075）。"""

    holdout_ratio: float = 0.2        # 每行业留出比例
    min_holdout_symbols: int = 5      # 留出集最少标的数（不足无法算截面 IC → None）
    min_symbols_per_ic: int = 5       # 单期截面 IC 最少有效标的
    min_ic_retention: float = 0.5     # IC 保持率下限（holdout_ic / |train_ic|）
    min_holdout_ic: float = 0.0       # 留出集 IC 下限（方向对齐后）
    min_train_ic: float = 0.05        # 训练集 |IC| 下限（弱信号下保持率判定噪声主导 → None）
    seed: int = 42                    # 分层留出随机种子（固定保证可复现）


@dataclass
class SymbolHoldoutResult:
    """标的留出验证结果。"""

    n_train: int
    n_holdout: int
    train_ic: float          # 训练集截面 IC（方向对齐后）
    holdout_ic: float        # 留出集截面 IC（与训练集同方向）
    ic_retention: float      # 保持率 = holdout_ic / |train_ic|
    passed: bool             # holdout_ic ≥ min_holdout_ic 且 retention ≥ min_ic_retention
    holdout_symbols: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_train": self.n_train,
            "n_holdout": self.n_holdout,
            "train_ic": self.train_ic,
            "holdout_ic": self.holdout_ic,
            "ic_retention": self.ic_retention,
            "passed": self.passed,
            "holdout_symbols": list(self.holdout_symbols),
            "detail": dict(self.detail),
        }


def _stratified_split(
    symbols: list[str],
    industry_map: Optional[dict[str, str]],
    holdout_ratio: float,
    seed: int,
) -> tuple[list[str], list[str]]:
    """按行业分层留出：每行业至少留 1 只（该行业 ≥2 只时）；行业缺失回退随机。"""
    rng = np.random.default_rng(seed)
    holdout: list[str] = []
    if industry_map:
        by_industry: dict[str, list[str]] = {}
        for s in symbols:
            by_industry.setdefault(industry_map.get(s, "_unknown"), []).append(s)
        for _ind, members in by_industry.items():
            members = list(members)
            if len(members) < 2:
                continue  # 单标的行业不留出，避免行业完全缺失
            n_hold = max(1, int(round(len(members) * float(holdout_ratio))))
            rng.shuffle(members)
            holdout.extend(members[:n_hold])
    else:
        members = list(symbols)
        n_hold = max(1, int(round(len(members) * float(holdout_ratio))))
        rng.shuffle(members)
        holdout = members[:n_hold]
    holdout_set = set(holdout)
    train = [s for s in symbols if s not in holdout_set]
    return train, holdout


def _cs_ics(sig_matrix: np.ndarray, ret_matrix: np.ndarray, min_valid: int = 5) -> list[float]:
    """逐期截面 Spearman IC（与 evaluation_chain._cs_compute_ics 同口径）。"""
    ics: list[float] = []
    for t in range(sig_matrix.shape[0]):
        sig_t, ret_t = sig_matrix[t], ret_matrix[t]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        if np.sum(valid) < min_valid:
            continue
        s, r = sig_t[valid], ret_t[valid]
        if np.std(s) < 1e-10 or np.std(r) < 1e-10:
            continue
        ic, _ = sp_stats.spearmanr(s, r)
        if not np.isnan(ic):
            ics.append(float(ic))
    return ics


def run_symbol_holdout(
    signal_dict: dict[str, pd.Series],
    ret_dict: dict[str, pd.Series],
    config: Optional[SymbolHoldoutConfig] = None,
    industry_map: Optional[dict[str, str]] = None,
) -> SymbolHoldoutResult | None:
    """标的留出验证：行业分层留出 → 训练集定方向 → 留出集验 IC 保持率。

    Args:
        signal_dict: {symbol: 因子信号 Series}
        ret_dict: {symbol: 前向收益 Series}
        config: 留出配置（缺省用默认值）
        industry_map: {symbol: 行业}（可选，启用分层；缺失回退随机）

    Returns:
        SymbolHoldoutResult；留出集过小/数据不足返回 None（审计项 skipped）。
    """
    cfg = config or SymbolHoldoutConfig()
    symbols = list(signal_dict.keys())
    if len(symbols) < cfg.min_holdout_symbols * 2:
        return None

    # 共同日期（信号与收益交集）
    common = None
    for s in symbols:
        idx = signal_dict[s].index.intersection(ret_dict[s].index)
        common = idx if common is None else common.intersection(idx)
    if common is None or len(common) < 2:
        return None
    common = pd.DatetimeIndex(common).sort_values()

    train, holdout = _stratified_split(symbols, industry_map, cfg.holdout_ratio, cfg.seed)
    if len(holdout) < cfg.min_holdout_symbols:
        logger.debug("留出集过小（%d < %d），标的留出验证跳过", len(holdout), cfg.min_holdout_symbols)
        return None

    def build(sub: list[str]) -> tuple[np.ndarray, np.ndarray]:
        sig_m = np.column_stack(
            [pd.to_numeric(signal_dict[s], errors="coerce").reindex(common).to_numpy() for s in sub]
        )
        ret_m = np.column_stack(
            [pd.to_numeric(ret_dict[s], errors="coerce").reindex(common).to_numpy() for s in sub]
        )
        return sig_m, ret_m

    tr_sig, tr_ret = build(train)
    ho_sig, ho_ret = build(holdout)
    tr_ics = _cs_ics(tr_sig, tr_ret, cfg.min_symbols_per_ic)
    ho_ics = _cs_ics(ho_sig, ho_ret, cfg.min_symbols_per_ic)
    if not tr_ics or not ho_ics:
        return None

    # 训练集定方向（避免用留出集数据选方向的数据窥探）
    dir_sign = 1.0 if float(np.mean(tr_ics)) >= 0 else -1.0
    train_ic = float(np.mean(tr_ics)) * dir_sign
    if abs(train_ic) < cfg.min_train_ic:
        # 弱信号下 retention = holdout_ic / |train_ic| 被近零分母放大，
        # 留出集噪声（±0.005）即可主导判定（如 0.03 → 0.0009 / 0.03 = 3%），
        # 判定不可靠 → 返回 None（审计项 skipped，不阻断演化流程）。
        logger.debug(
            "训练集 |IC|=%.4f 低于下限 %.2f，标的留出验证跳过（弱信号判定不可靠）",
            abs(train_ic),
            cfg.min_train_ic,
        )
        return None
    holdout_ic = float(np.mean(ho_ics)) * dir_sign
    if abs(train_ic) > 1e-10:
        retention = holdout_ic / abs(train_ic)
    else:
        retention = 1.0 if holdout_ic >= 0 else -1.0
    passed = holdout_ic >= cfg.min_holdout_ic and retention >= cfg.min_ic_retention
    return SymbolHoldoutResult(
        n_train=len(train),
        n_holdout=len(holdout),
        train_ic=float(train_ic),
        holdout_ic=float(holdout_ic),
        ic_retention=float(retention),
        passed=bool(passed),
        holdout_symbols=list(holdout),
        detail={"n_dates": int(len(common)), "stratified": bool(industry_map)},
    )
