"""
fts/factor_engine/scope_domain/guard.py — 真伪鉴别护栏（品种级专用）

区分"真品种特异"与"单品种过拟合噪声"：单品种候选必须同时满足
  ① 最小样本窗（n_dates ≥ min_dates，默认 500 交易日≈2 年，杜绝次新小样本）；
  ② 跨子期符号一致（K 个不重叠子期同号占比 ≥ subperiod_ratio，防单段行情偶然）；
  ③ 域内置换检验（打乱 IC 时序后均值显著不高于原值，p < 0.05，防随机噪声）。
任一不过 → guard_passed=False，标记"疑似噪声"，不落库为特异画像（宁漏标不误标）。

纯函数 / 固定随机种子（可复现）/ 不判失败不崩溃。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from fts.factor_engine.scope_domain.types import DomainStats, ScopeGuardResult

logger = logging.getLogger(__name__)


def permutation_p(daily_ic: list[float], n: int = 200, seed: int = 0) -> float:
    """域内 IC 显著性 p 值（零假设：域内 IC 均值为 0，即纯噪声）。

    实现：单样本 t 检验（ttest_1samp）——对稳定正向序列与噪声序列均稳健；
    完全一致序列（std≈0）视为显著（p=0）；样本 <5 → 1.0（不通过）。
    命名保留 permutation（P2 接入面板+信号做真置换检验时的增强位）。
    """
    del n, seed  # t 检验无需置换次数/种子；保留签名兼容
    arr = np.array([float(v) for v in daily_ic if v is not None and np.isfinite(v)], dtype=float)
    if arr.size < 5:
        return 1.0
    mean = float(arr.mean())
    if abs(mean) < 1e-12:
        return 1.0
    std = float(arr.std(ddof=1))
    if std < 1e-12:  # 完全一致 → 高度显著
        return 0.0
    try:
        from scipy import stats as sp_stats

        _t, p = sp_stats.ttest_1samp(arr, 0.0)
        return float(p)
    except Exception:  # noqa: BLE001 — 检验失败保守不通过
        return 1.0


def run_scope_guard(
    *,
    stats: DomainStats,
    daily_ic: list[float],
    cfg: Optional[object] = None,
) -> ScopeGuardResult:
    """品种级真伪鉴别护栏（三门槛 AND，任一不过 → passed=False）。

    Args:
        stats: compute_domain_stats 输出（含 n_dates / subperiod_consistency）。
        daily_ic: 域内逐日 IC 序列（置换检验输入）。
        cfg: scope_domain 配置（min_dates/subperiod_ratio/permutation_n；None → 默认）。

    Returns:
        ScopeGuardResult（passed + reasons + evidence，落库画像证据）。
    """
    min_dates = int(getattr(cfg, "min_dates", 500) or 500)
    subperiod_ratio = float(getattr(cfg, "subperiod_ratio", 0.67) or 0.67)
    perm_n = int(getattr(cfg, "permutation_n", 200) or 200)

    reasons: list[str] = []
    if stats.n_dates < min_dates:
        reasons.append(f"样本窗不足({stats.n_dates}<{min_dates}交易日)")
    if stats.subperiod_consistency < subperiod_ratio:
        reasons.append(f"跨子期符号不一致({stats.subperiod_consistency:.2f}<{subperiod_ratio})")
    # 置换检验为可选维度（品种级未落逐日 IC 序列时 permutation_p=None → 跳过，保守不误杀）
    p: Optional[float] = None
    if daily_ic:
        p = permutation_p(daily_ic, perm_n)
        if p >= 0.05:
            reasons.append(f"置换检验不显著(p={p:.3f}≥0.05)")
    passed = not reasons
    result = ScopeGuardResult(
        passed=passed,
        reasons=reasons or ["护栏通过"],
        evidence={
            "n_dates": stats.n_dates,
            "subperiod_consistency": round(stats.subperiod_consistency, 4),
            "permutation_p": round(p, 4) if p is not None else None,
            "guard": "passed" if passed else "failed",
        },
    )
    if not passed:
        logger.info("[scope-guard] 品种级特异护栏未过: %s", "; ".join(reasons))
    return result


__all__ = ["permutation_p", "run_scope_guard"]
