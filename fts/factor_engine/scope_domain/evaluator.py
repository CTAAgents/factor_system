"""
fts/factor_engine/scope_domain/evaluator.py — 域内统计量计算器

scope 域内口径（非全链）：
  - 域内 IC：scope 域内各品种时序 IC（Spearman）均值，方向以多数符号对齐；
  - 域内 Sharpe：域内逐日 IC 序列 mean/std × sqrt(252)（无逐日序列 → None）；
  - 符号一致性：域内同号品种占比；
  - 子期一致性：域内逐日 IC 序列切 K 个不重叠子期，同号子期占比（真伪护栏维度）；
  - 半衰期：域内逐日 IC 序列估计（复用 factor_lifecycle.estimate_ic_half_life）。

纯函数 / 零未来函数 / 不判失败不崩溃（缺失输入返回 valid=False）。
"""

from __future__ import annotations

import logging
import math
import warnings
from typing import Any, Optional

import numpy as np

from fts.factor_engine.scope_domain.resolver import resolve_chain_map
from fts.factor_engine.scope_domain.types import DomainStats, FactorScope

logger = logging.getLogger(__name__)

_ANNUALIZATION = math.sqrt(252.0)


def _finite(v: object) -> bool:
    try:
        return bool(math.isfinite(float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def resolve_domain_symbols(scope: FactorScope, market: str = "futures") -> list[str]:
    """scope 域内品种列表（全链=全部已知品种；chain=各链并集；symbol=品种列表）。"""
    if scope.kind == "all":
        all_syms: list[str] = []
        for syms in resolve_chain_map(market).values():
            all_syms.extend(syms)
        return sorted(set(all_syms))
    if scope.kind == "chain":
        chain_map = resolve_chain_map(market)
        out: list[str] = []
        for c in scope.chains:
            out.extend(chain_map.get(c, []))
        return sorted(set(out))
    return list(scope.symbols)


def aggregate_domain(
    symbol_ic: dict[str, float],
    scope: FactorScope,
    market: str = "futures",
) -> tuple[Optional[float], float, int]:
    """域内 IC 聚合（纯函数）。

    Args:
        symbol_ic: 逐品种时序 IC（评估链已产出，方向翻转同步）。
        scope: 有效域；market: 链映射市场。

    Returns:
        (domain_ic, ic_positive_ratio, n_symbols)；域内无有效品种 → (None, 0.0, 0)。
    """
    domain_syms = set(resolve_domain_symbols(scope, market))
    ics = [float(v) for s, v in symbol_ic.items() if s in domain_syms and _finite(v)]
    if not ics:
        return None, 0.0, 0
    n = len(ics)
    # 方向以多数符号对齐（正/负计数），域内 IC = 对齐后均值
    pos = sum(1 for v in ics if v > 0)
    neg = n - pos
    sign = 1.0 if pos >= neg else -1.0
    domain_ic = float(sign * np.mean([abs(v) for v in ics]))
    ratio = pos / n if pos >= neg else neg / n
    return domain_ic, ratio, n


def domain_sharpe(daily_ic: list[float]) -> Optional[float]:
    """域内 Sharpe：域内逐日 IC 序列 mean/std × sqrt(252)。

    样本 <2 或 std≈0 → None（不判失败）。
    """
    arr = [float(v) for v in daily_ic if _finite(v)]
    if len(arr) < 2:
        return None
    std = float(np.std(arr, ddof=1))
    if std < 1e-12:
        return None
    return float(np.mean(arr) / std * _ANNUALIZATION)


def subperiod_consistency(daily_ic: list[float], subperiods: int = 3) -> float:
    """跨不重叠子期符号一致率：域内逐日 IC 切 K 段，各段均值同号占比。

    无有效值 → 0.0；subperiods<=1 → 1.0（单期无意义即"一致"）。
    """
    arr = [float(v) for v in daily_ic if _finite(v)]
    if not arr:
        return 0.0
    if subperiods <= 1:
        return 1.0
    k = min(subperiods, len(arr))
    # 均分 k 段
    idx = np.array_split(np.arange(len(arr)), k)
    signs: list[float] = []
    for part in idx:
        seg = [arr[i] for i in part]
        m = float(np.mean(seg))
        if abs(m) > 1e-12:
            signs.append(1.0 if m > 0 else -1.0)
    if not signs:
        return 0.0
    maj = sum(1 for s in signs if s == signs[0])
    return maj / len(signs)


def estimate_domain_half_life(daily_ic: list[float]) -> Optional[float]:
    """域内 IC 半衰期（复用 factor_lifecycle.estimate_ic_half_life；失败 → None）。"""
    try:
        from fts.factor_engine.factor_lifecycle import estimate_ic_half_life
    except Exception:  # noqa: BLE001
        return None
    try:
        import numpy as _np

        arr = _np.array([v for v in daily_ic if _finite(v)], dtype=float)
        if arr.size < 20:
            return None
        # 以 6 个月（≈126 交易日）IC 序列估计衰减
        decay = estimate_ic_half_life(float(arr[-126:].mean())) if arr.size >= 126 else None
        if decay is not None:
            return float(decay)
        return None
    except Exception:  # noqa: BLE001
        return None


def compute_domain_stats(
    *,
    symbol_ic: dict[str, float],
    scope: FactorScope,
    market: str = "futures",
    daily_ic: Optional[list[float]] = None,
    cfg: Optional[object] = None,
) -> DomainStats:
    """组装域内统计量（评估链/评审/退化统一入口）。

    Args:
        symbol_ic: 逐品种时序 IC。
        scope: 有效域。
        market: 链映射市场。
        daily_ic: 域内逐日 IC 序列（可选；提供时计算 Sharpe/子期一致/半衰期）。
        cfg: scope_domain 配置（读取 subperiods 等；None → 默认 3）。

    Returns:
        DomainStats（无有效品种 → valid=False，统计字段 None）。
    """
    ic, ratio, n = aggregate_domain(symbol_ic, scope, market)
    subperiods = int(getattr(cfg, "subperiods", 3) or 3)
    stats = DomainStats(
        scope=scope,
        n_symbols=n,
        n_dates=len([v for v in (daily_ic or []) if _finite(v)]),
        ic=ic,
        sharpe=domain_sharpe(daily_ic or []) if daily_ic else None,
        ic_positive_ratio=ratio,
        subperiod_consistency=subperiod_consistency(daily_ic or [], subperiods) if daily_ic else 0.0,
        half_life_days=estimate_domain_half_life(daily_ic or []) if daily_ic else None,
        valid=bool(n > 0 and (ic is not None)),
    )
    return stats


def evaluate_symbol_scope(
    signal: Any,
    forward_returns: Any,
    symbol: str,
    cfg: Optional[object] = None,
) -> DomainStats:
    """品种级域内评估（P2）：单品种时序 IC + 跨子期一致性 + 显著性。

    Args:
        signal: 因子在该品种的信号序列（np.ndarray / list，NaN 允许）。
        forward_returns: 对齐的前向收益序列。
        symbol: 品种代码（如 "RB0"）。
        cfg: scope_domain 配置（subperiods；None → 默认 3）。

    Returns:
        DomainStats（kind=symbol）；样本不足/常数信号 → valid=False。
    """
    import numpy as _np
    from scipy import stats as _sp

    s = _np.asarray(signal, dtype=float)
    r = _np.asarray(forward_returns, dtype=float)
    if s.ndim != 1 or r.ndim != 1 or len(s) != len(r):
        return DomainStats(scope=FactorScope(kind="symbol", symbols=[symbol]))
    valid = _np.isfinite(s) & _np.isfinite(r)
    s, r = s[valid], r[valid]
    n_dates = int(len(s))
    scope = FactorScope(kind="symbol", symbols=[symbol])
    if n_dates < 5 or float(_np.std(s)) < 1e-10 or float(_np.std(r)) < 1e-10:
        return DomainStats(scope=scope, n_symbols=1, n_dates=n_dates)

    subperiods = int(getattr(cfg, "subperiods", 3) or 3)
    # 跨子期一致性：均分 K 段，各段时序 IC 同号占比
    seg_ics: list[float] = []
    if subperiods > 1:
        for part in _np.array_split(_np.arange(n_dates), min(subperiods, n_dates)):
            seg = part[part < n_dates]
            if len(seg) < 5:
                continue
            ss, rr = s[seg], r[seg]
            if float(_np.std(ss)) < 1e-10 or float(_np.std(rr)) < 1e-10:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                ic_seg, _ = _sp.spearmanr(ss, rr)
            if not _np.isnan(ic_seg):
                seg_ics.append(float(ic_seg))
    consistency = 0.0
    if seg_ics:
        maj = max(sum(1 for v in seg_ics if v > 0), sum(1 for v in seg_ics if v < 0))
        consistency = maj / len(seg_ics)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ic_val, _ = _sp.spearmanr(s, r)
    if _np.isnan(ic_val):
        return DomainStats(scope=scope, n_symbols=1, n_dates=n_dates)
    ic = float(ic_val)
    stats = DomainStats(
        scope=scope,
        n_symbols=1,
        n_dates=n_dates,
        ic=abs(ic),
        ic_positive_ratio=1.0 if ic >= 0 else 0.0,
        subperiod_consistency=consistency,
        valid=True,
    )
    # 显著性：品种级由 run_scope_guard 基于子期一致 + 样本窗判定（permutation 为
    # P2 增强位，接入面板后可落逐日 IC 序列）
    return stats


__all__ = [
    "aggregate_domain",
    "compute_domain_stats",
    "domain_sharpe",
    "estimate_domain_half_life",
    "resolve_domain_symbols",
    "subperiod_consistency",
]
