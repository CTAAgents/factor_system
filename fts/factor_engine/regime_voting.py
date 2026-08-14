"""
fts.factor_engine.regime_voting — 手册五指标投票 Regime 检测器（CTA 手册阶段5）。

对照《期货CTA多因子策略标准化作业手册》阶段5「行情环境识别与因子动态权重切换」:
    五指标体系（ADX / Hurst / 波动率分位数 / 趋势一致性比率 / 截面离散度）
    → 加权投票判定 趋势 Regime / 震荡 Regime / 过渡 Regime
    → 动态权重切换（regime_multipliers 已实现）+ 切换平滑（adaptive_weight 已实现）
    → Regime 切换风控约束（本模块补齐）:
        - 防抖: 单日 Regime 切换不超过 1 次
        - 过渡 Regime 期间整体仓位降至基准 70%
        - 连续 7 日 Regime 不稳定 → 触发策略降仓复审

另含:
    - 条件 IC（因子在 趋势/震荡/过渡 Regime 下的 IC，IC|趋势 / IC|震荡 / IC|过渡）
    - 动态权重 vs 固定权重样本外夏普对比（提升 ≥ 0.2 验收）

设计约束:
    - 纯函数为主 + RegimeVotingDetector 状态机（防抖/连续不稳计数）
    - NaN 兜底 / 样本不足返回中性
    - 零未来函数: 各指标仅用截至当日的滚动窗口

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)

# ─── 手册判定阈值 ─────────────────────────────────────────

ADX_TREND_THRESHOLD = 25.0  # ADX > 25 趋势
ADX_OSCILLATION_THRESHOLD = 20.0  # ADX < 20 震荡
HURST_TREND_THRESHOLD = 0.55  # H > 0.55 持续性（趋势）
HURST_OSCILLATION_THRESHOLD = 0.45  # H < 0.45 均值回归（震荡）
CONSISTENCY_TREND_THRESHOLD = 0.60  # >60% 品种同方向 → 趋势
CONSISTENCY_OSCILLATION_THRESHOLD = 0.40  # <40% 同方向 → 震荡
TRANSITION_POSITION_SCALE = 0.70  # 过渡 Regime 仓位降至基准 70%
UNSTABLE_REVIEW_DAYS = 7  # 连续 7 日不稳定 → 降仓复审

VOTE_TREND = "trend"
VOTE_OSCILLATION = "oscillation"
VOTE_NEUTRAL = "neutral"


# ─── Hurst 指数（R/S 分析） ───────────────────────────────


def hurst_exponent(returns: np.ndarray | pd.Series, min_window: int = 10) -> float:
    """R/S 分析 Hurst 指数（手册：60 日窗口，H>0.55 持续 / <0.45 均值回归）。

    Args:
        returns: 收益率序列
        min_window: 最小分箱窗口

    Returns:
        Hurst 指数（0~1）；样本不足返回 0.5（中性）。
    """
    series = np.asarray(returns, dtype=float)
    series = series[np.isfinite(series)]
    n = len(series)
    if n < 2 * min_window:
        return 0.5
    max_window = n // 2
    windows = np.unique(np.geomspace(min_window, max_window, 10).astype(int))
    rs_pairs: list[tuple[float, float]] = []
    for w in windows:
        n_chunks = n // w
        if n_chunks < 1:
            continue
        rs_vals: list[float] = []
        for i in range(n_chunks):
            chunk = series[i * w : (i + 1) * w]
            mean = float(np.mean(chunk))
            dev = np.cumsum(chunk - mean)
            r = float(np.max(dev) - np.min(dev))
            s = float(np.std(chunk))
            if s > 0:
                rs_vals.append(r / s)
        if rs_vals:
            rs_pairs.append((float(w), float(np.mean(rs_vals))))
    if len(rs_pairs) < 3:
        return 0.5
    log_w = np.log([w for w, _ in rs_pairs])
    log_rs = np.log([rs for _, rs in rs_pairs])
    h, _ = np.polyfit(log_w, log_rs, 1)
    return float(np.clip(h, 0.0, 1.0))


# ─── ADX（平均趋向指数） ──────────────────────────────────


def _compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    """ADX（平均趋向指数）：衡量趋势强度，>25 趋势 / <20 震荡。

    Args:
        high/low/close: 价格序列
        period: DMI 周期（默认 14）

    Returns:
        最新 ADX 值；数据不足返回 0.0。
    """
    h = np.asarray(high, dtype=float)
    low_arr = np.asarray(low, dtype=float)
    close_arr = np.asarray(close, dtype=float)
    n = len(h)
    if n < period + 1:
        return 0.0
    up_move = np.diff(h)
    down_move = -np.diff(low_arr)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = np.maximum(
        h[1:] - low_arr[1:],
        np.maximum(np.abs(h[1:] - close_arr[:-1]), np.abs(low_arr[1:] - close_arr[:-1])),
    )
    atr = pd.Series(tr).rolling(period).mean().to_numpy()
    atr_safe = np.maximum(atr, 1e-12)
    plus_di = 100.0 * pd.Series(plus_dm).rolling(period).mean().to_numpy() / atr_safe
    minus_di = 100.0 * pd.Series(minus_dm).rolling(period).mean().to_numpy() / atr_safe
    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-12)
    adx = pd.Series(dx).rolling(period).mean().to_numpy()
    valid = adx[np.isfinite(adx)]
    return float(valid[-1]) if len(valid) else 0.0


# ─── 波动率分位数 / 趋势方向 ──────────────────────────────


def _vol_percentile(combo_ret: pd.Series, vol_window: int = 20, lookback: int = 252) -> float:
    """20 日已实现波动率在过去 252 日的分位数（0~1）。"""
    vol = combo_ret.rolling(vol_window).std().dropna()
    if len(vol) < 60:
        return 0.5
    recent = vol.iloc[-lookback:] if len(vol) >= lookback else vol
    return float(sp_stats.percentileofscore(recent, vol.iloc[-1]) / 100.0)


def _trend_direction(series: pd.Series, slope_z_threshold: float = 1.5) -> str:
    """序列方向性：显著单边趋势 → "trend"，否则 "oscillation"。"""
    s = series.dropna()
    if len(s) < 10:
        return "oscillation"
    x: np.ndarray = np.arange(len(s), dtype=float)
    slope, _, r_value, _, _ = sp_stats.linregress(x, s)
    # 斜率 t 统计量：slope / (std_err)；std_err 由 linregress 提供
    if slope == 0:
        return "oscillation"
    if abs(r_value) < 0.4:
        return "oscillation"
    return "trend"


# ─── 五指标计算 ───────────────────────────────────────────


def compute_regime_indicators(panel: dict[str, pd.DataFrame]) -> dict:
    """计算手册五指标（组合等权口径）。

    Args:
        panel: {symbol: OHLCV DataFrame}

    Returns:
        dict: {
            adx, hurst, vol_percentile, consistency_ratio, dispersion_direction,
            n_symbols, n_days,
        }
    """
    syms = [s for s, df in panel.items() if df is not None and not df.empty and "close" in df.columns]
    if not syms:
        return {
            "adx": 0.0,
            "hurst": 0.5,
            "vol_percentile": 0.5,
            "consistency_ratio": 0.5,
            "dispersion_direction": "oscillation",
            "n_symbols": 0,
            "n_days": 0,
        }

    rets = pd.DataFrame({s: panel[s]["close"].pct_change() for s in syms})
    combo_ret = rets.mean(axis=1).dropna()
    if len(combo_ret) < 30:
        return {
            "adx": 0.0,
            "hurst": 0.5,
            "vol_percentile": 0.5,
            "consistency_ratio": 0.5,
            "dispersion_direction": "oscillation",
            "n_symbols": len(syms),
            "n_days": len(combo_ret),
        }

    # ADX：逐品种计算后取中位数（等权平均会抹平独立波动产生伪趋势）
    adx_vals = [
        _compute_adx(
            pd.to_numeric(panel[s]["high"], errors="coerce").values,
            pd.to_numeric(panel[s]["low"], errors="coerce").values,
            pd.to_numeric(panel[s]["close"], errors="coerce").values,
        )
        for s in syms
    ]
    adx_val = float(np.median(adx_vals))
    h = hurst_exponent(combo_ret.values)
    vol_pct = _vol_percentile(combo_ret)

    # 趋势一致性比率：60 日收益符号同向比例
    lookback = min(60, len(combo_ret))
    signs: list[float] = []
    for s in syms:
        r = float(panel[s]["close"].pct_change(lookback).iloc[-1]) if len(panel[s]) > lookback else np.nan
        if np.isfinite(r) and r != 0:
            signs.append(np.sign(r))
    consistency = 0.5
    if signs:
        up_ratio = sum(1 for x in signs if x > 0) / len(signs)
        consistency = float(max(up_ratio, 1 - up_ratio))

    # 截面离散度方向：全品种收益率横截面 std 的滚动趋势
    cs_std = rets.std(axis=1).dropna()
    dispersion = _trend_direction(cs_std.iloc[-40:] if len(cs_std) > 40 else cs_std)

    return {
        "adx": adx_val,
        "hurst": h,
        "vol_percentile": vol_pct,
        "consistency_ratio": consistency,
        "dispersion_direction": dispersion,
        "n_symbols": len(syms),
        "n_days": len(combo_ret),
    }


# ─── 单指标投票 ───────────────────────────────────────────


def _vote_adx(adx: float) -> str:
    if adx > ADX_TREND_THRESHOLD:
        return VOTE_TREND
    if adx < ADX_OSCILLATION_THRESHOLD:
        return VOTE_OSCILLATION
    return VOTE_NEUTRAL


def _vote_hurst(h: float) -> str:
    if h > HURST_TREND_THRESHOLD:
        return VOTE_TREND
    if h < HURST_OSCILLATION_THRESHOLD:
        return VOTE_OSCILLATION
    return VOTE_NEUTRAL


def _vote_vol_percentile(percentile: float) -> str:
    if percentile > 0.75 or percentile < 0.25:
        return VOTE_TREND  # 极端高波动或稳定低波动 → 趋势
    if 0.30 < percentile < 0.70:
        return VOTE_OSCILLATION  # 中等波动 → 震荡
    return VOTE_NEUTRAL


def _vote_consistency(ratio: float) -> str:
    if ratio > CONSISTENCY_TREND_THRESHOLD:
        return VOTE_TREND
    if ratio < CONSISTENCY_OSCILLATION_THRESHOLD:
        return VOTE_OSCILLATION
    return VOTE_NEUTRAL


def _vote_dispersion(direction: str) -> str:
    return direction if direction in (VOTE_TREND, VOTE_OSCILLATION) else VOTE_NEUTRAL


def classify_regime(votes: list[str]) -> str:
    """手册加权投票判定：≥3 票趋势 → trend；≥3 票震荡 → oscillation；否则 transition。

    Args:
        votes: 五指标投票结果列表（len=5）

    Returns:
        "trend" / "oscillation" / "transition"。
    """
    trend_votes = sum(1 for v in votes if v == VOTE_TREND)
    osc_votes = sum(1 for v in votes if v == VOTE_OSCILLATION)
    if trend_votes >= 3:
        return VOTE_TREND
    if osc_votes >= 3:
        return VOTE_OSCILLATION
    return "transition"


def detect_regime_voting(panel: dict[str, pd.DataFrame]) -> dict:
    """一次性五指标投票 Regime 检测（无状态）。

    Args:
        panel: {symbol: OHLCV DataFrame}

    Returns:
        dict: {regime, votes, indicators, n_symbols, n_days}
    """
    ind = compute_regime_indicators(panel)
    votes = [
        _vote_adx(ind["adx"]),
        _vote_hurst(ind["hurst"]),
        _vote_vol_percentile(ind["vol_percentile"]),
        _vote_consistency(ind["consistency_ratio"]),
        _vote_dispersion(ind["dispersion_direction"]),
    ]
    return {
        "regime": classify_regime(votes),
        "votes": votes,
        "indicators": ind,
        "n_symbols": ind["n_symbols"],
        "n_days": ind["n_days"],
    }


# ─── 状态化检测器（防抖 / 连续不稳复审） ──────────────────


class RegimeVotingDetector:
    """状态化手册 Regime 检测器：防抖（单日切换≤1次）+ 连续不稳复审。

    Usage:
        detector = RegimeVotingDetector()
        result = detector.detect(panel, date="2024-01-05")
    """

    def __init__(self) -> None:
        """初始化（状态置空）。"""
        self._current: Optional[str] = None
        self._last_switch_date: Optional[str] = None
        self._unstable_days: int = 0

    def detect(self, panel: dict[str, pd.DataFrame], date: str) -> dict:
        """逐日检测（含防抖与连续不稳计数）。

        Args:
            panel: {symbol: OHLCV DataFrame}
            date: 当日日期（ISO 字符串）

        Returns:
            dict: {regime, votes, indicators, debounced, unstable_days, review_required}
        """
        base = detect_regime_voting(panel)
        regime = base["regime"]

        # 防抖：单日 Regime 切换不超过 1 次
        debounced = False
        if self._current is not None and regime != self._current:
            if date == self._last_switch_date:
                regime = self._current
                debounced = True
            else:
                self._last_switch_date = date
        else:
            # 首次检测或状态未变：记录日期，供同日重复调用防抖
            self._last_switch_date = date

        # 连续不稳计数：transition 或状态切换视为不稳定
        if self._current is not None and regime != self._current:
            self._unstable_days += 1
        elif regime == "transition":
            self._unstable_days += 1
        else:
            self._unstable_days = max(0, self._unstable_days - 1)

        self._current = regime
        return {
            "regime": regime,
            "votes": base["votes"],
            "indicators": base["indicators"],
            "debounced": debounced,
            "unstable_days": self._unstable_days,
            "review_required": self._unstable_days >= UNSTABLE_REVIEW_DAYS,
        }

    @property
    def current(self) -> Optional[str]:
        """当前 Regime。"""
        return self._current


# ─── 过渡降仓 / 连续不稳复审 ──────────────────────────────


def transition_position_scale(
    regime: str,
    base_scale: float = 1.0,
    transition_scale: float = TRANSITION_POSITION_SCALE,
) -> float:
    """过渡 Regime 仓位降至基准的 70%（手册阶段5 风控约束）。

    Args:
        regime: 当前 Regime
        base_scale: 基准仓位比例（默认 1.0）
        transition_scale: 过渡期仓位比例（默认 0.7）

    Returns:
        生效仓位比例。
    """
    if regime == "transition":
        return base_scale * transition_scale
    return base_scale


def unstable_review_required(
    unstable_days: int,
    threshold: int = UNSTABLE_REVIEW_DAYS,
) -> bool:
    """连续 N 日 Regime 不稳定 → 触发策略降仓复审（手册阶段5 风控约束）。

    Args:
        unstable_days: 连续不稳定天数
        threshold: 复审阈值（默认 7 日）

    Returns:
        是否触发复审。
    """
    return unstable_days >= threshold


# ─── 条件 IC（IC|趋势 / IC|震荡 / IC|过渡） ───────────────


def conditional_ic(
    signal: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    regime_map: dict[str, str],
    dates: np.ndarray | pd.Series,
) -> dict:
    """因子条件 IC（手册阶段5.2）：按日期对齐 Regime 后分别计算 IC。

    Args:
        signal: 因子信号
        forward_returns: 未来收益
        regime_map: {date_str: regime}
        dates: 与 signal 对齐的日期

    Returns:
        dict: {regime: {ic, n}}（trend/oscillation/transition），无样本返回空。
    """
    sig = np.asarray(signal, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    dts = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates)))
    out: dict[str, dict[str, float]] = {}
    if len(sig) != len(ret) != len(dts):
        return out
    date_keys = dts.strftime("%Y-%m-%d")
    for regime in (VOTE_TREND, VOTE_OSCILLATION, "transition"):
        mask = np.array([regime_map.get(d, "") == regime for d in date_keys], dtype=bool)
        n = int(mask.sum())
        if n < 5:
            continue
        valid = ~(np.isnan(sig[mask]) | np.isnan(ret[mask]))
        sv, rv = sig[mask][valid], ret[mask][valid]
        if len(sv) < 5 or np.std(sv) < 1e-12 or np.std(rv) < 1e-12:
            out[regime] = {"ic": 0.0, "n": n}
            continue
        ic, _ = sp_stats.spearmanr(sv, rv)
        out[regime] = {"ic": float(ic) if not np.isnan(ic) else 0.0, "n": n}
    return out


# ─── 动态 vs 固定权重对比（手册阶段5 Checkpoint） ─────────


def regime_switch_benefit(
    dynamic_sharpe: float,
    static_sharpe: float,
    min_gain: float = 0.2,
) -> dict:
    """动态权重 vs 固定权重样本外夏普提升验收（提升 ≥ 0.2）。

    Args:
        dynamic_sharpe: 动态权重样本外夏普
        static_sharpe: 固定权重样本外夏普
        min_gain: 最小提升要求（默认 0.2）

    Returns:
        dict: {gain, passed}
    """
    gain = float(dynamic_sharpe) - float(static_sharpe)
    return {"gain": gain, "passed": gain >= min_gain}


__all__ = [
    "hurst_exponent",
    "compute_regime_indicators",
    "classify_regime",
    "detect_regime_voting",
    "RegimeVotingDetector",
    "transition_position_scale",
    "unstable_review_required",
    "conditional_ic",
    "regime_switch_benefit",
]
