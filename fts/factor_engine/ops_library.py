"""FTS 算子扩展库（2026-08-11 扩容二期，132→500+）。

设计约束（对齐 C8Ops/C9Ops）:
    - 全部 pandas 向量化实现，滚动窗口 NaN 兜底（min_periods/fillna），不抛异常
    - 单序列输入为主，多序列算子（high/low/close/volume 等）按参数名成对传参
    - 常数序列/零值均降级 0 或有限兜底，杜绝 NaN/Inf 泄漏
    - 与 expr_dsl.registry 双注册表共享（verify_registry_consistency 强制一致）

算子族:
    D10Ops  波动/风险族（L1，55 算子）: 多种波动率估计 / 风险收益比率 / 回撤 / VaR
    D11Ops  技术指标族（L1/L3，60 算子）: MACD/RSI/随机指标/OBV/CCI 等经典指标变体
    D12Ops  动量/趋势族（L1/L5，55 算子）: 多尺度动量 / 加速度 / 趋势确认
    D13Ops  截面/排名族（L2，45 算子）: 截面变换 / 排名变体 / 离群处理
    D14Ops  条件/事件族（L3，40 算子）: 穿越 / 状态 / 持续 / 突破
    D15Ops  组合/跨序列族（L4，50 算子）: 双序列相关 / 比值 / 回归
    D16Ops  量价/流动性族（L5，40 算子）: 量价配合 / 流动性 / 换手
    D17Ops  市场结构/分布族（L5，35 算子）: 集中度 / 广度 / 熵
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .feature_ops import RollingOps, _rolling_apply_native

_MINP = 2  # 统一最小窗口期


def _native_apply(
    series: pd.Series | pd.DataFrame,
    window: int,
    min_periods: int,
    row_fn,
    batch_fn,
):
    """pandas ``rolling(window, min_periods).apply`` 的向量化等价（Series/DataFrame 通用）。

    ``row_fn(valid_1d) -> float`` / ``batch_fn(rows_2d) -> (m,)`` 语义见
    ``feature_ops._rolling_apply_native``；DataFrame（面板路径）逐列循环。
    """
    if isinstance(series, pd.DataFrame):
        return series.apply(lambda col: _native_apply(col, window, min_periods, row_fn, batch_fn))
    arr = _rolling_apply_native(series.to_numpy(dtype=float), window, min_periods, row_fn, batch_fn)
    return pd.Series(arr, index=series.index)


def _ret(series: pd.Series) -> pd.Series:
    """日收益（价格序列 → pct_change，NaN 置 0）。"""
    return series.pct_change().fillna(0.0)


def _corr_clean(corr_series: pd.Series) -> pd.Series:
    """相关系数安全兜底（常数/零方差序列 → ±inf/NaN → 0，clip [-1,1]）。"""
    return corr_series.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1.0, 1.0)


class D10Ops:
    """D10 波动/风险族（L1）— 55 个波动率估计与风险收益算子。

    输入约定: 单序列为价格序列（内部转收益）；multi 序列算子按参数名传
    high/low/open/close。全部返回与输入等长 Series，NaN 兜底 0 或有限值。
    """

    # ── 波动率估计 ─────────────────────────────────────────

    @staticmethod
    def ts_realized_vol(series: pd.Series, window: int = 20) -> pd.Series:
        """已实现波动率（滚动收益标准差）。"""
        return _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_ewma_vol(series: pd.Series, span: int = 20) -> pd.Series:
        """指数加权波动率（EWMA，半衰期平滑）。"""
        return _ret(series).ewm(span=span, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_parkinson(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
        """Parkinson 高低价波动率（σ² = mean((ln H/L)²)/(4ln2)）。"""
        hl = (np.log(high.clip(lower=1e-8)) - np.log(low.clip(lower=1e-8))).fillna(0.0)
        var = (hl**2 / (4.0 * np.log(2.0))).rolling(window, min_periods=_MINP).mean()
        return np.sqrt(var.clip(lower=0.0)).fillna(0.0)

    @staticmethod
    def ts_garman_klass(
        open_p: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
    ) -> pd.Series:
        """Garman-Klass 波动率（含跳空的日内波动）。"""
        o = np.log(open_p.clip(lower=1e-8)).fillna(0.0)
        h = np.log(high.clip(lower=1e-8)).fillna(0.0)
        lnl = np.log(low.clip(lower=1e-8)).fillna(0.0)
        c = np.log(close.clip(lower=1e-8)).fillna(0.0)
        var = 0.5 * (h - lnl) ** 2 - (2.0 * np.log(2.0) - 1.0) * (c - o) ** 2
        return np.sqrt(var.rolling(window, min_periods=_MINP).mean().clip(lower=0.0)).fillna(0.0)

    @staticmethod
    def ts_rogers_satchell(
        open_p: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
    ) -> pd.Series:
        """Rogers-Satchell 波动率（含漂移的日内波动）。"""
        o = np.log(open_p.clip(lower=1e-8)).fillna(0.0)
        h = np.log(high.clip(lower=1e-8)).fillna(0.0)
        lnl = np.log(low.clip(lower=1e-8)).fillna(0.0)
        c = np.log(close.clip(lower=1e-8)).fillna(0.0)
        var = (h - c) * (h - o) + (lnl - c) * (lnl - o)
        return np.sqrt(var.rolling(window, min_periods=_MINP).mean().clip(lower=0.0)).fillna(0.0)

    @staticmethod
    def ts_yang_zhang(
        open_p: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
    ) -> pd.Series:
        """Yang-Zhang 波动率（隔夜+日内加权，最稳健）。"""
        o = np.log(open_p.clip(lower=1e-8)).fillna(0.0)
        c = np.log(close.clip(lower=1e-8)).fillna(0.0)
        h = np.log(high.clip(lower=1e-8)).fillna(0.0)
        lnl = np.log(low.clip(lower=1e-8)).fillna(0.0)
        overnight = (o - c.shift(1)).fillna(0.0)
        ov_vol = overnight.rolling(window, min_periods=_MINP).std().fillna(0.0)
        open_vol = (o - c.shift(1)).rolling(window, min_periods=_MINP).std().fillna(0.0)
        rs = ((h - c) * (h - o) + (lnl - c) * (lnl - o)).rolling(window, min_periods=_MINP).mean().clip(lower=0.0)
        k = 0.34 / (1.34 + (window + 1.0) / (window - 1.0))
        var = ov_vol**2 + k * open_vol**2 + (1.0 - k) * rs
        return np.sqrt(var.clip(lower=0.0)).fillna(0.0)

    @staticmethod
    def ts_downside_vol(series: pd.Series, window: int = 20) -> pd.Series:
        """下行波动率（仅负收益的标准差，下行风险）。"""
        r = _ret(series)
        neg = r.where(r < 0, 0.0)
        return np.sqrt((neg**2).rolling(window, min_periods=_MINP).mean()).fillna(0.0)

    @staticmethod
    def ts_upside_vol(series: pd.Series, window: int = 20) -> pd.Series:
        """上行波动率（仅正收益的标准差，上行风险）。"""
        r = _ret(series)
        pos = r.where(r > 0, 0.0)
        return np.sqrt((pos**2).rolling(window, min_periods=_MINP).mean()).fillna(0.0)

    @staticmethod
    def ts_vol_of_vol(series: pd.Series, window: int = 20) -> pd.Series:
        """波动率的波动（已实现波动率的滚动标准差，波动聚集）。"""
        vol = _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)
        return vol.rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_bipower_var(series: pd.Series, window: int = 20) -> pd.Series:
        """双幂变差（跳跃稳健波动估计，σ=mean(|r_t||r_{t-1}|)·π/2）。"""
        r = _ret(series).abs()
        prod = (r * r.shift(1)).fillna(0.0)
        return np.sqrt((prod * np.pi / 2.0).rolling(window, min_periods=_MINP).mean()).fillna(0.0)

    @staticmethod
    def ts_range_vol(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
        """振幅波动率（(H-L)/C 的滚动均值）。"""
        span = (high - low) / close.replace(0.0, np.nan)
        return span.fillna(0.0).clip(lower=0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_harmonic_vol(series: pd.Series, window: int = 20) -> pd.Series:
        """调和波动率（收益绝对值的调和均值，稳健波动代理）。"""
        r = _ret(series).abs().replace(0.0, np.nan)
        inv = (1.0 / r).rolling(window, min_periods=_MINP).mean()
        return (1.0 / inv).fillna(0.0)

    # ── 回撤类 ─────────────────────────────────────────────

    @staticmethod
    def ts_drawdown(series: pd.Series, window: int = 0) -> pd.Series:
        """当前回撤（相对历史峰值，window=0 全历史）。"""
        roll_max = series.rolling(window, min_periods=1).max() if window and window > 1 else series.cummax()
        return (series / roll_max.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_max_drawdown(series: pd.Series, window: int = 60) -> pd.Series:
        """窗口最大回撤（最深跌幅，负值）。"""
        roll_max = series.rolling(window, min_periods=_MINP).max()
        dd = series / roll_max.replace(0.0, np.nan) - 1.0
        return dd.fillna(0.0).rolling(window, min_periods=_MINP).min()

    @staticmethod
    def ts_avg_drawdown(series: pd.Series, window: int = 60) -> pd.Series:
        """窗口平均回撤（负值，回撤深度均值）。"""
        roll_max = series.rolling(window, min_periods=_MINP).max()
        dd = (series / roll_max.replace(0.0, np.nan) - 1.0).fillna(0.0)
        return dd.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_drawdown_duration(series: pd.Series, window: int = 60) -> pd.Series:
        """回撤持续期（当前回撤发生以来的天数，0=无回撤）。"""
        roll_max = series.rolling(window, min_periods=1).max()
        in_dd = (series < roll_max).astype(int)
        return in_dd.rolling(window, min_periods=1).sum()

    @staticmethod
    def ts_ulcer_index(series: pd.Series, window: int = 60) -> pd.Series:
        """溃疡指数（回撤平方均值开方，回撤疼痛度）。"""
        roll_max = series.rolling(window, min_periods=_MINP).max()
        dd = (series / roll_max.replace(0.0, np.nan) - 1.0).fillna(0.0)
        return np.sqrt((dd**2).rolling(window, min_periods=_MINP).mean())

    # ── 风险度量（VaR/CVaR） ───────────────────────────────

    @staticmethod
    def ts_var_95(series: pd.Series, window: int = 60) -> pd.Series:
        """95% VaR（5% 分位收益，负值=损失）。"""
        return _ret(series).rolling(window, min_periods=_MINP).quantile(0.05)

    @staticmethod
    def ts_var_99(series: pd.Series, window: int = 60) -> pd.Series:
        """99% VaR（1% 分位收益）。"""
        return _ret(series).rolling(window, min_periods=_MINP).quantile(0.01)

    @staticmethod
    def ts_cvar_95(series: pd.Series, window: int = 60) -> pd.Series:
        """95% CVaR（低于 5% 分位收益的均值，条件尾部损失）。

        plans/40 C 层：numba 快速路径（收益序列全有限 → ``cvar_1d`` alpha=0.05，
        前缀/主区间分位语义与现值 ``_native_apply`` 逐位一致）；含 NaN/inf 或
        开关关闭 → 回退现值实现，零漂移。
        """
        from .numba_kernels import cvar_1d

        r = _ret(series)
        arr = r.to_numpy(dtype=float)
        if np.isfinite(arr).all():
            nb = cvar_1d(arr, int(window), 0.05)
            if nb is not None:
                return pd.Series(np.nan_to_num(nb, nan=0.0), index=series.index)

        def _cvar(v: np.ndarray) -> float:
            q = np.nanquantile(v, 0.05)
            tail = v[v <= q]
            return float(np.mean(tail)) if tail.size else float(q)

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)
            pos = 0.05 * (rows.shape[1] - 1)
            lo, hi = int(np.floor(pos)), int(np.ceil(pos))
            q = s[:, lo] + (s[:, hi] - s[:, lo]) * (pos - lo)
            mask = rows <= q[:, None]
            cnt = mask.sum(axis=-1)
            return np.where(cnt > 0, np.where(mask, rows, 0.0).sum(axis=-1) / cnt, q)

        return _native_apply(r, window, _MINP, _cvar, _batch).fillna(0.0)

    @staticmethod
    def ts_cvar_99(series: pd.Series, window: int = 60) -> pd.Series:
        """99% CVaR（低于 1% 分位收益的均值）。

        plans/40 C 层：numba 快速路径（收益序列全有限 → ``cvar_1d`` alpha=0.01）；
        含 NaN/inf 或开关关闭 → 回退现值实现，零漂移。
        """
        from .numba_kernels import cvar_1d

        r = _ret(series)
        arr = r.to_numpy(dtype=float)
        if np.isfinite(arr).all():
            nb = cvar_1d(arr, int(window), 0.01)
            if nb is not None:
                return pd.Series(np.nan_to_num(nb, nan=0.0), index=series.index)

        def _cvar(v: np.ndarray) -> float:
            q = np.nanquantile(v, 0.01)
            tail = v[v <= q]
            return float(np.mean(tail)) if tail.size else float(q)

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)
            pos = 0.01 * (rows.shape[1] - 1)
            lo, hi = int(np.floor(pos)), int(np.ceil(pos))
            q = s[:, lo] + (s[:, hi] - s[:, lo]) * (pos - lo)
            mask = rows <= q[:, None]
            cnt = mask.sum(axis=-1)
            return np.where(cnt > 0, np.where(mask, rows, 0.0).sum(axis=-1) / cnt, q)

        return _native_apply(r, window, _MINP, _cvar, _batch).fillna(0.0)

    @staticmethod
    def ts_semi_std(series: pd.Series, window: int = 20) -> pd.Series:
        """半标准差（下行半方差，等价 downside_vol）。"""
        return D10Ops.ts_downside_vol(series, window)

    @staticmethod
    def ts_lpm_2(series: pd.Series, window: int = 20) -> pd.Series:
        """二阶下偏矩（低于 0 的平方收益均值）。"""
        r = _ret(series)
        neg = r.where(r < 0, 0.0)
        return (neg**2).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_hpm_2(series: pd.Series, window: int = 20) -> pd.Series:
        """二阶上偏矩（高于 0 的平方收益均值）。"""
        r = _ret(series)
        pos = r.where(r > 0, 0.0)
        return (pos**2).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_gain_std(series: pd.Series, window: int = 20) -> pd.Series:
        """正收益波动（正收益段标准差）。"""
        r = _ret(series)
        pos = r.where(r > 0)
        return pos.rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_loss_std(series: pd.Series, window: int = 20) -> pd.Series:
        """负收益波动（负收益段标准差）。"""
        r = _ret(series)
        neg = r.where(r < 0)
        return neg.rolling(window, min_periods=_MINP).std().fillna(0.0)

    # ── 风险调整收益比率 ───────────────────────────────────

    @staticmethod
    def ts_sharpe_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """滚动夏普比率（收益均值/标准差）。"""
        r = _ret(series)
        mu = r.rolling(window, min_periods=_MINP).mean()
        sd = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (mu / sd).fillna(0.0)

    @staticmethod
    def ts_sortino_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """Sortino 比率（收益均值/下行偏差）。"""
        r = _ret(series)
        mu = r.rolling(window, min_periods=_MINP).mean()
        neg = r.where(r < 0, 0.0)
        dd = np.sqrt((neg**2).rolling(window, min_periods=_MINP).mean()).replace(0.0, np.nan)
        return (mu / dd).fillna(0.0)

    @staticmethod
    def ts_calmar_ratio(series: pd.Series, window: int = 250) -> pd.Series:
        """Calmar 比率（年化收益/最大回撤绝对值）。"""
        r = _ret(series)
        mu = r.rolling(window, min_periods=_MINP).mean() * 250.0
        roll_max = series.rolling(window, min_periods=_MINP).max()
        mdd = (series / roll_max.replace(0.0, np.nan) - 1.0).fillna(0.0).rolling(window, min_periods=_MINP).min()
        denom = mdd.abs().replace(0.0, np.nan)
        return (mu / denom).fillna(0.0)

    @staticmethod
    def ts_profit_factor(series: pd.Series, window: int = 60) -> pd.Series:
        """盈亏比（正收益和/负收益绝对值之和）。"""
        r = _ret(series)
        gain = r.clip(lower=0.0).rolling(window, min_periods=_MINP).sum()
        loss = (-r.clip(upper=0.0)).rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (gain / loss).fillna(0.0)

    @staticmethod
    def ts_omega_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """Omega 比率（收益大于 0 的加权概率比，阈值 0）。"""
        r = _ret(series)
        gain = r.clip(lower=0.0).rolling(window, min_periods=_MINP).sum()
        loss = (-r.clip(upper=0.0)).rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (gain / loss).fillna(0.0)

    @staticmethod
    def ts_kelly_fraction(series: pd.Series, window: int = 60) -> pd.Series:
        """Kelly 比例（胜率-败率，最优下注比例代理）。"""
        r = _ret(series)
        up = (r > 0).astype(float).rolling(window, min_periods=_MINP).mean()
        down = (r < 0).astype(float).rolling(window, min_periods=_MINP).mean()
        return (up - down).fillna(0.0)

    @staticmethod
    def ts_worst_day(series: pd.Series, window: int = 60) -> pd.Series:
        """窗口最差日收益（极小值）。"""
        return _ret(series).rolling(window, min_periods=_MINP).min()

    @staticmethod
    def ts_best_day(series: pd.Series, window: int = 60) -> pd.Series:
        """窗口最佳日收益（极大值）。"""
        return _ret(series).rolling(window, min_periods=_MINP).max()

    @staticmethod
    def ts_win_rate(series: pd.Series, window: int = 60) -> pd.Series:
        """胜率（正收益占比）。"""
        return (_ret(series) > 0).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_loss_rate(series: pd.Series, window: int = 60) -> pd.Series:
        """败率（负收益占比）。"""
        return (_ret(series) < 0).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_avg_gain(series: pd.Series, window: int = 60) -> pd.Series:
        """平均盈利（正收益均值）。"""
        r = _ret(series)
        return r.where(r > 0).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_avg_loss(series: pd.Series, window: int = 60) -> pd.Series:
        """平均亏损（负收益均值，负值）。"""
        r = _ret(series)
        return r.where(r < 0).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_expectancy(series: pd.Series, window: int = 60) -> pd.Series:
        """期望收益（窗口收益均值）。"""
        return _ret(series).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_recovery_factor(series: pd.Series, window: int = 250) -> pd.Series:
        """恢复因子（收益均值/最大回撤绝对值）。"""
        return D10Ops.ts_calmar_ratio(series, window)

    @staticmethod
    def ts_risk_return_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """风险收益比（收益均值/收益波动）。"""
        return D10Ops.ts_sharpe_ratio(series, window)

    @staticmethod
    def ts_downside_deviation(series: pd.Series, window: int = 20) -> pd.Series:
        """下行偏差（目标 0 的半方差开方）。"""
        return D10Ops.ts_downside_vol(series, window)

    # ── 波动率结构 ─────────────────────────────────────────

    @staticmethod
    def ts_vol_ratio_ewma(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """EWMA 波动比（短/长指数波动比，波动状态切换）。"""
        r = _ret(series)
        vs = r.ewm(span=short, min_periods=_MINP).std().fillna(0.0)
        vl = r.ewm(span=long, min_periods=_MINP).std().replace(0.0, np.nan).fillna(0.0)
        return (vs / vl).replace(np.inf, np.nan).fillna(0.0)

    @staticmethod
    def ts_realized_vol_pct(series: pd.Series, window: int = 20) -> pd.Series:
        """波动率百分比（已实现波动/价格水平，无量纲）。"""
        vol = _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)
        price = series.rolling(window, min_periods=_MINP).mean().abs().replace(0.0, np.nan)
        return (vol / price).fillna(0.0)

    @staticmethod
    def ts_vol_zscore(series: pd.Series, window: int = 60) -> pd.Series:
        """波动率 zscore（当前波动相对长期波动的偏离）。"""
        vol = _ret(series).rolling(10, min_periods=_MINP).std().fillna(0.0)
        mu = vol.rolling(window, min_periods=_MINP).mean()
        sd = vol.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((vol - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_vol_percentile(series: pd.Series, window: int = 60) -> pd.Series:
        """波动率分位（当前波动在历史波动中的百分位）。"""
        vol = _ret(series).rolling(10, min_periods=_MINP).std().fillna(0.0)
        return vol.rolling(window, min_periods=_MINP).rank(pct=True).fillna(0.5)

    @staticmethod
    def ts_garch_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """波动聚集代理（|r| 的滚动均值，波动持续代理）。"""
        return _ret(series).abs().rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_vol_asymmetry(series: pd.Series, window: int = 60) -> pd.Series:
        """波动不对称（下行波动-上行波动，负=下跌波动更大）。"""
        return D10Ops.ts_downside_vol(series, window) - D10Ops.ts_upside_vol(series, window)

    @staticmethod
    def ts_leverage_effect(series: pd.Series, window: int = 60) -> pd.Series:
        """杠杆效应（收益与随后波动的负相关代理，|r| 对滞后收益回归残差符号）。"""
        r = _ret(series)
        avol = r.abs().rolling(window, min_periods=_MINP).mean()
        lead = avol.shift(-1).fillna(0.0)
        cov = (lead * r).rolling(window, min_periods=_MINP).mean()
        vs = lead.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        vr = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (-cov / (vs * vr)).fillna(0.0)

    @staticmethod
    def ts_baseline_vol(series: pd.Series, window: int = 120) -> pd.Series:
        """基准波动（长窗口已实现波动）。"""
        return _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_long_term_vol(series: pd.Series, window: int = 120) -> pd.Series:
        """长期波动（120 窗口）。"""
        return D10Ops.ts_baseline_vol(series, window)

    @staticmethod
    def ts_short_term_vol(series: pd.Series, window: int = 10) -> pd.Series:
        """短期波动（10 窗口）。"""
        return _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_vol_term_structure(series: pd.Series, short: int = 10, long: int = 120) -> pd.Series:
        """波动期限结构（短/长波动比，>1 短期恐慌）。"""
        vs = _ret(series).rolling(short, min_periods=_MINP).std().fillna(0.0)
        vl = _ret(series).rolling(long, min_periods=_MINP).std().replace(0.0, np.nan)
        return (vs / vl).replace(np.inf, np.nan).fillna(0.0)

    @staticmethod
    def ts_max_loss_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """最大损失占比（最差日收益/总波动，尾部强度）。"""
        r = _ret(series)
        worst = r.rolling(window, min_periods=_MINP).min()
        sd = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (worst / sd).fillna(0.0)

    @staticmethod
    def ts_beta_vol(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """波动率 beta（短波动对长波动的滚动回归斜率，波动弹性）。"""
        r = _ret(series)
        vs = r.rolling(short, min_periods=_MINP).std().fillna(0.0)
        return RollingOps.ts_slope(vs, window=long) if hasattr(RollingOps, "ts_slope") else vs.diff().fillna(0.0)


class D11Ops:
    """D11 技术指标族（L1/L3）— 60 个经典技术指标变体。

    输入约定: 单序列为价格/成交量序列；多序列按 high/low/open/close/volume 成对传参。
    全部向量化实现 + NaN 兜底（fillna 0/有限值），常数/零值安全。
    """

    # ── 均线类 ─────────────────────────────────────────────

    @staticmethod
    def ts_ema_fast_slow(series: pd.Series, short: int = 12, long: int = 26) -> pd.Series:
        """快慢 EMA 差（趋势强度与方向）。"""
        es = series.ewm(span=short, min_periods=_MINP).mean()
        el = series.ewm(span=long, min_periods=_MINP).mean()
        return (es - el).fillna(0.0)

    @staticmethod
    def ts_macd(series: pd.Series, short: int = 12, long: int = 26) -> pd.Series:
        """MACD 线（快 EMA - 慢 EMA）。"""
        return D11Ops.ts_ema_fast_slow(series, short, long)

    @staticmethod
    def ts_macd_signal(series: pd.Series, short: int = 12, long: int = 26, signal: int = 9) -> pd.Series:
        """MACD 信号线（MACD 的 EMA）。"""
        macd = D11Ops.ts_macd(series, short, long)
        return macd.ewm(span=signal, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_macd_hist(series: pd.Series, short: int = 12, long: int = 26, signal: int = 9) -> pd.Series:
        """MACD 柱（MACD - 信号线，动能）。"""
        macd = D11Ops.ts_macd(series, short, long)
        sig = D11Ops.ts_macd_signal(series, short, long, signal)
        return (macd - sig).fillna(0.0)

    @staticmethod
    def ts_dema(series: pd.Series, span: int = 20) -> pd.Series:
        """双重指数平均 DEMA（2·EMA - EMA(EMA)，更快响应）。"""
        e1 = series.ewm(span=span, min_periods=_MINP).mean()
        e2 = e1.ewm(span=span, min_periods=_MINP).mean()
        return (2.0 * e1 - e2).fillna(0.0)

    @staticmethod
    def ts_tema(series: pd.Series, span: int = 20) -> pd.Series:
        """三重指数平均 TEMA（3·E1 - 3·E2 + E3）。"""
        e1 = series.ewm(span=span, min_periods=_MINP).mean()
        e2 = e1.ewm(span=span, min_periods=_MINP).mean()
        e3 = e2.ewm(span=span, min_periods=_MINP).mean()
        return (3.0 * e1 - 3.0 * e2 + e3).fillna(0.0)

    @staticmethod
    def ts_kama(series: pd.Series, window: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
        """自适应均线 KAMA（波动调整的平滑系数）。"""
        er_den = series.diff(window).abs().replace(0.0, np.nan)
        er_num = series.diff().abs().rolling(window, min_periods=_MINP).sum()
        eff = (er_num / er_den).fillna(0.0).clip(0.0, 1.0)
        sc = (eff * (2.0 / (fast + 1.0) - 2.0 / (slow + 1.0)) + 2.0 / (slow + 1.0)) ** 2
        out = pd.Series(np.nan, index=series.index)
        out.iloc[:window] = series.iloc[:window]
        for i in range(window, len(series)):
            out.iloc[i] = out.iloc[i - 1] + sc.iloc[i] * (series.iloc[i] - out.iloc[i - 1])
        return out.fillna(series).fillna(0.0)

    @staticmethod
    def ts_vwap(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """成交量加权均价（滚动 VWAP）。"""
        pv = (close * volume).rolling(window, min_periods=_MINP).sum()
        v = volume.rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (pv / v).fillna(0.0)

    # ── 摆动指标 ───────────────────────────────────────────

    @staticmethod
    def ts_rsi(series: pd.Series, window: int = 14) -> pd.Series:
        """RSI 相对强弱指数（0-100，超买超卖）。"""
        r = _ret(series)
        gain = r.clip(lower=0.0).rolling(window, min_periods=_MINP).mean()
        loss = (-r.clip(upper=0.0)).rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        rs = gain / loss
        return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)

    @staticmethod
    def ts_rsi_smoothed(series: pd.Series, window: int = 14) -> pd.Series:
        """平滑 RSI（Wilder 平滑，与 RSI 同向但更平滑）。"""
        r = _ret(series)
        gain = r.clip(lower=0.0).ewm(alpha=1.0 / window, min_periods=_MINP).mean()
        loss = (-r.clip(upper=0.0)).ewm(alpha=1.0 / window, min_periods=_MINP).mean().replace(0.0, np.nan)
        rs = gain / loss
        return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)

    @staticmethod
    def ts_stoch_k(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """随机指标 %K（收盘价在窗口高低区间的位置）。"""
        hh = high.rolling(window, min_periods=_MINP).max()
        ll = low.rolling(window, min_periods=_MINP).min()
        span = (hh - ll).replace(0.0, np.nan)
        return ((close - ll) / span * 100.0).fillna(50.0)

    @staticmethod
    def ts_stoch_d(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14, smooth: int = 3) -> pd.Series:
        """随机指标 %D（%K 的均值，信号线）。"""
        k = D11Ops.ts_stoch_k(high, low, close, window)
        return k.rolling(smooth, min_periods=_MINP).mean().fillna(50.0)

    @staticmethod
    def ts_williams_r(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """威廉 %R（-100~0，超卖超买反转）。"""
        hh = high.rolling(window, min_periods=_MINP).max()
        ll = low.rolling(window, min_periods=_MINP).min()
        span = (hh - ll).replace(0.0, np.nan)
        return (-100.0 * (hh - close) / span).fillna(0.0)

    @staticmethod
    def ts_cci(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
        """CCI 顺势指标（偏离典型价的平均绝对偏差）。"""
        tp = (high + low + close) / 3.0
        ma = tp.rolling(window, min_periods=_MINP).mean()
        md = _native_apply(
            tp,
            window,
            _MINP,
            lambda v: float(np.mean(np.abs(v - np.mean(v)))),
            lambda rows: np.mean(np.abs(rows - np.mean(rows, axis=-1, keepdims=True)), axis=-1),
        )
        return ((tp - ma) / (0.015 * md.replace(0.0, np.nan))).fillna(0.0)

    @staticmethod
    def ts_trix(series: pd.Series, window: int = 15) -> pd.Series:
        """TRIX 三重指数平均（三重 EMA 的变化率）。"""
        e1 = series.ewm(span=window, min_periods=_MINP).mean()
        e2 = e1.ewm(span=window, min_periods=_MINP).mean()
        e3 = e2.ewm(span=window, min_periods=_MINP).mean()
        return e3.pct_change().fillna(0.0)

    @staticmethod
    def ts_ppo(series: pd.Series, short: int = 12, long: int = 26) -> pd.Series:
        """PPO 百分比价格振荡（MACD/慢 EMA）。"""
        es = series.ewm(span=short, min_periods=_MINP).mean()
        el = series.ewm(span=long, min_periods=_MINP).mean().replace(0.0, np.nan)
        return ((es - el) / el * 100.0).fillna(0.0)

    @staticmethod
    def ts_tsi(series: pd.Series, short: int = 13, long: int = 25) -> pd.Series:
        """TSI 真实强弱指数（双平滑动量比）。"""
        r = series.diff().fillna(0.0)
        num = r.ewm(span=short, min_periods=_MINP).mean().ewm(span=long, min_periods=_MINP).mean()
        den = (
            r.abs()
            .ewm(span=short, min_periods=_MINP)
            .mean()
            .ewm(span=long, min_periods=_MINP)
            .mean()
            .replace(0.0, np.nan)
        )
        return (100.0 * num / den).fillna(0.0)

    @staticmethod
    def ts_awesome(high: pd.Series, low: pd.Series, short: int = 5, long: int = 34) -> pd.Series:
        """AO 动量振荡器（短中价均值差）。"""
        mp = (high + low) / 2.0
        ms = mp.rolling(short, min_periods=_MINP).mean()
        ml = mp.rolling(long, min_periods=_MINP).mean()
        return (ms - ml).fillna(0.0)

    @staticmethod
    def ts_ultimate_osc(
        high: pd.Series, low: pd.Series, close: pd.Series, short: int = 7, mid: int = 14, long: int = 28
    ) -> pd.Series:
        """UO 终极振荡器（多周期加权动量）。"""
        prev_close = close.shift(1).fillna(close)
        bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
        tr = pd.concat([high, prev_close], axis=1).max(axis=1) - pd.concat([low, prev_close], axis=1).min(axis=1)
        tr = tr.replace(0.0, np.nan).fillna(1e-12)

        def _uo_avg(n: int) -> pd.Series:
            return bp.rolling(n, min_periods=_MINP).sum() / tr.rolling(n, min_periods=_MINP).sum()

        return (4.0 * _uo_avg(short) + 2.0 * _uo_avg(mid) + _uo_avg(long)).fillna(50.0)

    @staticmethod
    def ts_roc(series: pd.Series, window: int = 12) -> pd.Series:
        """ROC 变动率（(x/x_{t-n}-1)·100）。"""
        prev = series.shift(window).replace(0.0, np.nan)
        return ((series / prev - 1.0) * 100.0).fillna(0.0)

    @staticmethod
    def ts_momentum_index(series: pd.Series, window: int = 14) -> pd.Series:
        """动量指标（当前价 - n 前价）。"""
        return (series - series.shift(window)).fillna(0.0)

    @staticmethod
    def ts_rate_of_change_ma(series: pd.Series, window: int = 12) -> pd.Series:
        """ROC 均线（ROC 的滚动均值，动量平滑）。"""
        roc = D11Ops.ts_roc(series, window)
        return roc.rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_fisher_transform(series: pd.Series, window: int = 9) -> pd.Series:
        """Fisher 变换（价格分布高斯化，增强极值信号）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        ll = series.rolling(window, min_periods=_MINP).min()
        span = (hh - ll).replace(0.0, np.nan)
        x = (2.0 * (series - ll) / span - 1.0).clip(-0.999, 0.999)
        return (0.5 * np.log((1.0 + x) / (1.0 - x))).fillna(0.0)

    @staticmethod
    def ts_stoch_rsi(series: pd.Series, window: int = 14) -> pd.Series:
        """随机 RSI（RSI 的随机 %K，0-1 超买超卖）。"""
        rsi = D11Ops.ts_rsi(series, window)
        hh = rsi.rolling(window, min_periods=_MINP).max()
        ll = rsi.rolling(window, min_periods=_MINP).min()
        span = (hh - ll).replace(0.0, np.nan)
        return ((rsi - ll) / span).fillna(0.5)

    @staticmethod
    def ts_rvi(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
        """RVI 相对活力指数（收盘价波动在高低价波动中的占比）。"""
        c_vol = close.diff().fillna(0.0).abs()
        hl_vol = (high - low).replace(0.0, np.nan)
        num = c_vol.rolling(window, min_periods=_MINP).mean()
        den = hl_vol.rolling(window, min_periods=_MINP).mean()
        return (num / den).fillna(0.5)

    # ── 量能指标 ───────────────────────────────────────────

    @staticmethod
    def ts_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """OBV 能量潮（价格涨跌方向累积成交量）。"""
        sign = np.sign(close.diff().fillna(0.0))
        return (sign * volume).cumsum()

    @staticmethod
    def ts_obv_ma(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """OBV 均线（能量潮的滚动均值，趋势确认）。"""
        return D11Ops.ts_obv(close, volume).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 14) -> pd.Series:
        """MFI 资金流量指数（0-100，量价超买超卖）。"""
        tp = (high + low + close) / 3.0
        mf = tp * volume
        pos = mf.where(tp.diff().fillna(0.0) > 0, 0.0).rolling(window, min_periods=_MINP).sum()
        neg = mf.where(tp.diff().fillna(0.0) < 0, 0.0).rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (100.0 - 100.0 / (1.0 + pos / neg)).fillna(50.0)

    @staticmethod
    def ts_adi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """ADI 累积/派发线（收盘位置加权累积量）。"""
        hl = (high - low).replace(0.0, np.nan)
        clv = ((close - low) - (high - close)) / hl
        return (clv.fillna(0.0) * volume).cumsum()

    @staticmethod
    def ts_cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """CMF 蔡金资金流（资金流入占比，±1）。"""
        hl = (high - low).replace(0.0, np.nan)
        mfv = ((close - low) - (high - close)) / hl
        num = (mfv.fillna(0.0) * volume).rolling(window, min_periods=_MINP).sum()
        den = volume.rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (num / den).fillna(0.0)

    @staticmethod
    def ts_chaikin_vol(high: pd.Series, low: pd.Series, window: int = 10) -> pd.Series:
        """蔡金波动率（振幅 EMA 的变化率）。"""
        span = (high - low).ewm(span=window, min_periods=_MINP).mean()
        return span.pct_change().fillna(0.0)

    @staticmethod
    def ts_chaikin_osc(
        high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, short: int = 3, long: int = 10
    ) -> pd.Series:
        """蔡金振荡（ADI 快慢 EMA 差）。"""
        adi = D11Ops.ts_adi(high, low, close, volume)
        es = adi.ewm(span=short, min_periods=_MINP).mean()
        el = adi.ewm(span=long, min_periods=_MINP).mean()
        return (es - el).fillna(0.0)

    @staticmethod
    def ts_volume_oscillator(volume: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """量振荡器（短/长量均线百分比差）。"""
        vs = volume.rolling(short, min_periods=_MINP).mean()
        vl = volume.rolling(long, min_periods=_MINP).mean().replace(0.0, np.nan)
        return ((vs - vl) / vl * 100.0).fillna(0.0)

    @staticmethod
    def ts_market_facilitation(high: pd.Series, low: pd.Series, volume: pd.Series) -> pd.Series:
        """MFI 市场便利指数（振幅 × 成交量，价格流畅度）。"""
        span = high - low
        return (span * volume).fillna(0.0)

    # ── 波动通道 / ATR ──────────────────────────────────────

    @staticmethod
    def ts_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """ATR 平均真实波幅（真实波幅的均值）。"""
        prev_close = close.shift(1).fillna(close)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_natr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """归一化 ATR（ATR/收盘价，无量纲波动）。"""
        atr = D11Ops.ts_atr(high, low, close, window)
        return (atr / close.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def ts_bb_width(series: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """布林带宽度（2k·std，波动扩张/收缩）。"""
        sd = series.rolling(window, min_periods=_MINP).std().fillna(0.0)
        return (2.0 * k * sd).fillna(0.0)

    @staticmethod
    def ts_bb_percent_b(series: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """布林 %B（价格在带内的位置 0-1）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        sd = series.rolling(window, min_periods=_MINP).std().fillna(0.0)
        lower = ma - k * sd
        upper = ma + k * sd
        span = (upper - lower).replace(0.0, np.nan)
        return ((series - lower) / span).clip(0.0, 1.0).fillna(0.5)

    @staticmethod
    def ts_bb_bandwidth(series: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """布林带宽（(上轨-下轨)/中轨，波动率状态）。"""
        ma = series.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        width = D11Ops.ts_bb_width(series, window, k)
        return (width / ma).fillna(0.0)

    @staticmethod
    def ts_price_channel(series: pd.Series, window: int = 20) -> pd.Series:
        """价格通道位置（(x-LL)/(HH-LL) ∈[0,1]）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        ll = series.rolling(window, min_periods=_MINP).min()
        span = (hh - ll).replace(0.0, np.nan)
        return ((series - ll) / span).clip(0.0, 1.0).fillna(0.5)

    # ── Aroon / DPO / KST ──────────────────────────────────

    @staticmethod
    def ts_aroon_up(series: pd.Series, window: int = 25) -> pd.Series:
        """Aroon 上升（距窗口新高的期数占比）。"""
        hh_pos = _native_apply(
            series,
            window,
            _MINP,
            lambda v: float(np.argmax(v)),
            lambda rows: np.argmax(rows, axis=-1).astype(float),
        )
        return (100.0 * (window - hh_pos) / window).fillna(0.0)

    @staticmethod
    def ts_aroon_down(series: pd.Series, window: int = 25) -> pd.Series:
        """Aroon 下降（距窗口新低的期数占比）。"""
        ll_pos = _native_apply(
            series,
            window,
            _MINP,
            lambda v: float(np.argmin(v)),
            lambda rows: np.argmin(rows, axis=-1).astype(float),
        )
        return (100.0 * (window - ll_pos) / window).fillna(0.0)

    @staticmethod
    def ts_aroon_osc(series: pd.Series, window: int = 25) -> pd.Series:
        """Aroon 振荡（上升-下降，趋势强度）。"""
        return (D11Ops.ts_aroon_up(series, window) - D11Ops.ts_aroon_down(series, window)).fillna(0.0)

    @staticmethod
    def ts_dpo(series: pd.Series, window: int = 20) -> pd.Series:
        """DPO 去趋势价格振荡（x - 偏移 N/2 的均线）。"""
        offset = max(1, window // 2 + 1)
        ma = series.rolling(window, min_periods=_MINP).mean()
        return (series - ma.shift(offset)).fillna(0.0)

    @staticmethod
    def ts_kst(series: pd.Series, window: int = 30) -> pd.Series:
        """KST 综合振荡器（多周期 ROC 加权和，趋势周期识别）。"""
        roc1 = D11Ops.ts_roc(series, max(2, window // 3))
        roc2 = D11Ops.ts_roc(series, max(2, window // 2))
        roc3 = D11Ops.ts_roc(series, max(2, int(window * 2 / 3)))
        roc4 = D11Ops.ts_roc(series, window)
        return (roc1 + 2.0 * roc2 + 3.0 * roc3 + 4.0 * roc4).fillna(0.0)

    @staticmethod
    def ts_kst_signal(series: pd.Series, window: int = 30) -> pd.Series:
        """KST 信号线（KST 的均值）。"""
        return D11Ops.ts_kst(series, window).rolling(9, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_mass_index(high: pd.Series, low: pd.Series, window: int = 9) -> pd.Series:
        """质量指数（高低价波动范围的 EMA 比，趋势反转预警）。"""
        span = (high - low).replace(0.0, np.nan).fillna(1e-9)
        e1 = span.ewm(span=window, min_periods=_MINP).mean()
        e2 = e1.ewm(span=window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (e1 / e2).rolling(window, min_periods=_MINP).sum().fillna(1.0)

    # ── Vortex / Ichimoku ──────────────────────────────────

    @staticmethod
    def ts_vortex_pos(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """Vortex 正向指标（上升运动/真实波幅比）。"""
        prev_low = low.shift(1).fillna(low)
        prev_close = close.shift(1).fillna(close)
        vm = (high - prev_low).abs()
        tr = (
            pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
            .max(axis=1)
            .replace(0.0, np.nan)
        )
        num = vm.rolling(window, min_periods=_MINP).sum()
        den = tr.rolling(window, min_periods=_MINP).sum()
        return (num / den).fillna(1.0)

    @staticmethod
    def ts_vortex_neg(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """Vortex 负向指标（下降运动/真实波幅比）。"""
        prev_high = high.shift(1).fillna(high)
        prev_close = close.shift(1).fillna(close)
        vm = (prev_high - low).abs()
        tr = (
            pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
            .max(axis=1)
            .replace(0.0, np.nan)
        )
        num = vm.rolling(window, min_periods=_MINP).sum()
        den = tr.rolling(window, min_periods=_MINP).sum()
        return (num / den).fillna(1.0)

    @staticmethod
    def ts_vortex_ratio(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """Vortex 比率（正/负，>1 上升趋势）。"""
        pos = D11Ops.ts_vortex_pos(high, low, close, window)
        neg = D11Ops.ts_vortex_neg(high, low, close, window).replace(0.0, np.nan)
        return (pos / neg).fillna(1.0)

    @staticmethod
    def ts_ichimoku_conv(high: pd.Series, low: pd.Series, window: int = 9) -> pd.Series:
        """云图转换线（9 期中值）。"""
        return (
            (high.rolling(window, min_periods=_MINP).max() + low.rolling(window, min_periods=_MINP).min()) / 2.0
        ).fillna(0.0)

    @staticmethod
    def ts_ichimoku_base(high: pd.Series, low: pd.Series, window: int = 26) -> pd.Series:
        """云图基准线（26 期中值）。"""
        return D11Ops.ts_ichimoku_conv(high, low, window)

    @staticmethod
    def ts_ichimoku_span_a(high: pd.Series, low: pd.Series, window: int = 26) -> pd.Series:
        """云图 A（转换+基准均值，先行带）。"""
        conv = D11Ops.ts_ichimoku_conv(high, low, 9)
        base = D11Ops.ts_ichimoku_base(high, low, window)
        return ((conv + base) / 2.0).fillna(0.0)

    @staticmethod
    def ts_ichimoku_span_b(high: pd.Series, low: pd.Series, window: int = 52) -> pd.Series:
        """云图 B（52 期中值，先行带）。"""
        return D11Ops.ts_ichimoku_conv(high, low, window)

    # ── 交叉信号 / SAR ──────────────────────────────────────

    @staticmethod
    def ts_sma_cross_signal(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """双 SMA 交叉信号（金叉 +1 / 死叉 -1 / 其他 0）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean()
        diff = (ms - ml).fillna(0.0)
        cross = np.sign(diff).diff().fillna(0.0)
        return pd.Series(np.where(cross > 0, 1.0, np.where(cross < 0, -1.0, 0.0)), index=series.index)

    @staticmethod
    def ts_ema_cross_signal(series: pd.Series, short: int = 12, long: int = 26) -> pd.Series:
        """双 EMA 交叉信号（金叉/死叉事件）。"""
        es = series.ewm(span=short, min_periods=_MINP).mean()
        el = series.ewm(span=long, min_periods=_MINP).mean()
        diff = (es - el).fillna(0.0)
        cross = np.sign(diff).diff().fillna(0.0)
        return pd.Series(np.where(cross > 0, 1.0, np.where(cross < 0, -1.0, 0.0)), index=series.index)

    @staticmethod
    def ts_parabolic_sar(high: pd.Series, low: pd.Series, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
        """抛物线 SAR（追踪止损线）。"""
        sar = pd.Series(np.nan, index=high.index)
        if len(high) < 2:
            return sar.fillna(0.0)
        trend = 1.0
        ep = high.iloc[0]
        af = step
        sar.iloc[0] = low.iloc[0]
        for i in range(1, len(high)):
            sar.iloc[i] = sar.iloc[i - 1] + af * (ep - sar.iloc[i - 1])
            if trend > 0:
                sar.iloc[i] = min(sar.iloc[i], low.iloc[i - 1])
                if low.iloc[i] < sar.iloc[i]:
                    trend = -1.0
                    sar.iloc[i] = ep
                    ep = low.iloc[i]
                    af = step
                elif high.iloc[i] > ep:
                    ep = high.iloc[i]
                    af = min(af + step, max_step)
            else:
                sar.iloc[i] = max(sar.iloc[i], high.iloc[i - 1])
                if high.iloc[i] > sar.iloc[i]:
                    trend = 1.0
                    sar.iloc[i] = ep
                    ep = high.iloc[i]
                    af = step
                elif low.iloc[i] < ep:
                    ep = low.iloc[i]
                    af = min(af + step, max_step)
        return sar.fillna(0.0)

    @staticmethod
    def ts_price_oscillator(series: pd.Series, short: int = 10, long: int = 30) -> pd.Series:
        """价格振荡器（快慢 MA 差占慢 MA 比例）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean().replace(0.0, np.nan)
        return ((ms - ml) / ml * 100.0).fillna(0.0)

    @staticmethod
    def ts_trend_score(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势得分（价格在 MA 上方且 RSI 温和 → 强趋势）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        above = (series > ma).astype(float)
        rsi = D11Ops.ts_rsi(series, 14)
        return (above * ((rsi - 50.0) / 50.0)).fillna(0.0)

    @staticmethod
    def ts_cycle_score(series: pd.Series, window: int = 20) -> pd.Series:
        """周期得分（近期与远期动量一致性，方向持续性）。"""
        m1 = series.diff(5).fillna(0.0)
        m2 = series.diff(10).fillna(0.0)
        agree = np.sign(m1) * np.sign(m2)
        return pd.Series(agree, index=series.index).rolling(window, min_periods=_MINP).mean().fillna(0.0)


class D12Ops:
    """D12 动量/趋势族（L1/L5）— 55 个动量与趋势确认算子。

    输入约定: 单序列价格；多序列按 high/low/close 成对传参。
    全部向量化 + NaN 兜底，常数/零值安全。
    """

    # ── 速度/加速度 ────────────────────────────────────────

    @staticmethod
    def ts_velocity(series: pd.Series) -> pd.Series:
        """速度（一阶差分）。"""
        return series.diff().fillna(0.0)

    @staticmethod
    def ts_acceleration(series: pd.Series, window: int = 5) -> pd.Series:
        """加速度（速度的平滑差分）。"""
        v = series.diff().fillna(0.0)
        return v.diff(window).fillna(0.0)

    @staticmethod
    def ts_jerk(series: pd.Series, window: int = 5) -> pd.Series:
        """急动度（三阶差分，动量变化加速度）。"""
        return series.diff().diff().diff(window).fillna(0.0)

    @staticmethod
    def ts_momentum_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """动量比（x/x_{t-n}，>1 上涨动量）。"""
        prev = series.shift(window).replace(0.0, np.nan)
        return (series / prev).fillna(1.0)

    @staticmethod
    def ts_momentum_breakout_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """动量突破比（当前动量 / 窗口动量标准差）。"""
        m = series.diff(window).fillna(0.0)
        sd = m.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (m / sd).fillna(0.0)

    @staticmethod
    def ts_ewm_momentum(series: pd.Series, span: int = 20) -> pd.Series:
        """指数动量（EWMA 收益）。"""
        return _ret(series).ewm(span=span, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_momentum_vol_adj(series: pd.Series, window: int = 20) -> pd.Series:
        """波动调整动量（动量 / 已实现波动）。"""
        m = series.diff(window).fillna(0.0)
        vol = _ret(series).rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (m / vol).fillna(0.0)

    @staticmethod
    def ts_roc_zscore(series: pd.Series, window: int = 20) -> pd.Series:
        """ROC 标准化（变动率 zscore）。"""
        roc = D11Ops.ts_roc(series, window)
        mu = roc.rolling(window, min_periods=_MINP).mean()
        sd = roc.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((roc - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_velocity_zscore(series: pd.Series, window: int = 20) -> pd.Series:
        """速度 zscore（一阶差分标准化）。"""
        v = series.diff().fillna(0.0)
        mu = v.rolling(window, min_periods=_MINP).mean()
        sd = v.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((v - mu) / sd).fillna(0.0)

    # ── 趋势强度 ───────────────────────────────────────────

    @staticmethod
    def ts_trend_angle(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势角度（斜率反正切，弧度）。"""
        slope = (
            RollingOps.ts_slope(series, window=window) if hasattr(RollingOps, "ts_slope") else series.diff().fillna(0.0)
        )
        return np.arctan(slope.fillna(0.0))

    @staticmethod
    def ts_linear_trend_score(series: pd.Series, window: int = 20) -> pd.Series:
        """线性趋势得分（窗口线性拟合 R²，趋势确定性）。"""

        def _trend(v: np.ndarray) -> float:
            if np.std(v) > 0:
                return float(np.corrcoef(np.arange(v.size), v)[0, 1] ** 2)
            return 0.0

        def _batch(rows: np.ndarray) -> np.ndarray:
            t = np.arange(rows.shape[1], dtype=float)
            tc = t - t.mean()
            xc = rows - np.mean(rows, axis=-1, keepdims=True)
            var_x = np.mean(xc * xc, axis=-1)
            cov = np.mean(xc * tc, axis=-1)
            denom = np.sqrt(var_x) * np.sqrt(np.mean(tc * tc))
            corr = np.divide(cov, denom, out=np.zeros_like(cov), where=var_x > 0)
            return corr * corr

        r2 = _native_apply(series, window, 5, _trend, _batch)
        return r2.fillna(0.0).clip(0.0, 1.0)

    @staticmethod
    def ts_trend_strength_pct(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势强度百分比（方向收益 / |收益| 和）。"""
        r = _ret(series)
        up = r.clip(lower=0.0).rolling(window, min_periods=_MINP).sum()
        down = (-r.clip(upper=0.0)).rolling(window, min_periods=_MINP).sum()
        denom = (up + down).replace(0.0, np.nan)
        return ((up - down) / denom).fillna(0.0)

    @staticmethod
    def ts_above_ma_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """MA 上方占比（价格高于均线的比例，0-1）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        return (series > ma).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_below_ma_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """MA 下方占比。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        return (series < ma).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_slope_change(series: pd.Series, window: int = 10) -> pd.Series:
        """斜率变化（短期斜率 - 滞后斜率）。"""
        slope = series.diff().fillna(0.0)
        cur = slope.rolling(window, min_periods=_MINP).mean()
        prev = slope.rolling(window, min_periods=_MINP).mean().shift(window).fillna(0.0)
        return (cur - prev).fillna(0.0)

    @staticmethod
    def ts_curvature(series: pd.Series, window: int = 10) -> pd.Series:
        """曲率（二阶差分的平滑，凸性/凹性）。"""
        d2 = series.diff().diff().fillna(0.0)
        return d2.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_momentum_consistency(series: pd.Series, window: int = 20) -> pd.Series:
        """动量一致性（各阶差分同号占比）。"""
        d1 = np.sign(series.diff().fillna(0.0))
        d2 = np.sign(series.diff(2).fillna(0.0))
        d3 = np.sign(series.diff(3).fillna(0.0))
        agree = (d1 + d2 + d3).abs() / 3.0
        return agree.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_trend_persistence(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势持续（同方向收益持续天数占比）。"""
        r = _ret(series)
        up = (r > 0).astype(float)
        down = (r < 0).astype(float)
        best = pd.concat([up, down], axis=1).max(axis=1)
        return best.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_reversal_signal_z(series: pd.Series, window: int = 20) -> pd.Series:
        """反转 zscore 信号（价格偏离均值过远 → 反转倾向）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        sd = series.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (-(series - ma) / sd).fillna(0.0)

    @staticmethod
    def ts_trend_strength_ma(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """均线趋势强度（快慢均线差 / 价格）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean()
        return ((ms - ml) / series.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def ts_relative_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """相对强度（收益 / 波动，标准化的趋势动量）。"""
        return D10Ops.ts_sharpe_ratio(series, window)

    @staticmethod
    def ts_cross_momentum(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """交叉动量（快慢收益差）。"""
        r1 = series.pct_change(short).fillna(0.0)
        r2 = series.pct_change(long).fillna(0.0)
        return (r1 - r2).fillna(0.0)

    @staticmethod
    def ts_momentum_regime(series: pd.Series, window: int = 20) -> pd.Series:
        """动量制度评分（动量 zscore 正负区间映射）。"""
        z = D12Ops.ts_velocity_zscore(series, window)
        return np.sign(z) * np.minimum(np.abs(z), 1.0)

    @staticmethod
    def ts_trend_filter(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势过滤器（价格高于均线且动量同向 → +1，反之 -1，震荡 0）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        m = series.diff(5).fillna(0.0)
        above = (series > ma).astype(float)
        m_pos = (m > 0).astype(float)
        return (above * m_pos - (1.0 - above) * (1.0 - m_pos)).fillna(0.0)

    # ── 新高/新低/突破 ─────────────────────────────────────

    @staticmethod
    def ts_higher_high_count(series: pd.Series, window: int = 20) -> pd.Series:
        """新高计数（窗口内创阶段新高的次数）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        return (series >= hh).astype(float).rolling(window, min_periods=_MINP).sum()

    @staticmethod
    def ts_lower_low_count(series: pd.Series, window: int = 20) -> pd.Series:
        """新低计数。"""
        ll = series.rolling(window, min_periods=_MINP).min()
        return (series <= ll).astype(float).rolling(window, min_periods=_MINP).sum()

    @staticmethod
    def ts_new_high_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """新高占比（近期创窗口新高的比例）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        return (series >= hh).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_new_low_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """新低占比。"""
        ll = series.rolling(window, min_periods=_MINP).min()
        return (series <= ll).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_range_expansion(series: pd.Series, window: int = 20) -> pd.Series:
        """区间扩张（当前窗口振幅 / 前一窗口振幅）。"""
        amp = _native_apply(
            series,
            window,
            _MINP,
            lambda v: float(np.ptp(v)),
            lambda rows: (np.max(rows, axis=-1) - np.min(rows, axis=-1)).astype(float),
        )
        prev = amp.shift(window).replace(0.0, np.nan)
        return (amp / prev).fillna(1.0)

    @staticmethod
    def ts_breakout_distance(series: pd.Series, window: int = 20) -> pd.Series:
        """距突破位距离（当前价相对窗口高点，负=未突破）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        return ((series - hh) / series.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def ts_pullback_depth(series: pd.Series, window: int = 20) -> pd.Series:
        """回踩深度（距窗口高点回调幅度，负值）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        return (series / hh.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_continuation_signal(series: pd.Series, window: int = 20) -> pd.Series:
        """延续信号（趋势中回调不破均线 → +1）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        trend = (series > ma).astype(float)
        m = series.diff(3).fillna(0.0)
        return (trend * (m >= 0).astype(float) - (1.0 - trend) * (m < 0).astype(float)).fillna(0.0)

    @staticmethod
    def ts_exhaustion_signal(series: pd.Series, window: int = 20) -> pd.Series:
        """衰竭信号（连续同向大涨大跌后动能减弱 → 反转预警）。"""
        r = _ret(series)
        extreme = (r.abs() > r.rolling(window, min_periods=_MINP).std().fillna(0.0) * 2.0).astype(float)
        return extreme.rolling(5, min_periods=1).mean() * np.sign(r).fillna(0.0)

    @staticmethod
    def ts_donchian_break(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
        """唐奇安突破（价格破窗口高点 +1 / 破低点 -1）。"""
        hh = high.rolling(window, min_periods=_MINP).max()
        ll = low.rolling(window, min_periods=_MINP).min()
        return pd.Series(
            np.where(high > hh.shift(1).fillna(high), 1.0, np.where(low < ll.shift(1).fillna(low), -1.0, 0.0)),
            index=high.index,
        )

    @staticmethod
    def ts_donchian_mid(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
        """唐奇安中轨（(HH+LL)/2）。"""
        hh = high.rolling(window, min_periods=_MINP).max()
        ll = low.rolling(window, min_periods=_MINP).min()
        return ((hh + ll) / 2.0).fillna(0.0)

    @staticmethod
    def ts_supertrend_signal(
        series: pd.Series, high: pd.Series, low: pd.Series, window: int = 10, mult: float = 3.0
    ) -> pd.Series:
        """超级趋势信号（ATR 通道，+1 上升 / -1 下降）。"""
        atr = D11Ops.ts_atr(high, low, series, window)
        hl2 = (high + low) / 2.0
        upper = hl2 + mult * atr
        lower = hl2 - mult * atr
        return pd.Series(np.where(series > upper, 1.0, np.where(series < lower, -1.0, 0.0)), index=series.index)

    @staticmethod
    def ts_psar_position(high: pd.Series, low: pd.Series, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
        """SAR 位置（价格相对 SAR，>0 多头）。"""
        sar = D11Ops.ts_parabolic_sar(high, low, step, max_step)
        return pd.Series(np.where(high > sar, 1.0, np.where(low < sar, -1.0, 0.0)), index=high.index)

    # ── 方向/分形/支撑压力 ─────────────────────────────────

    @staticmethod
    def ts_uptrend_flag(series: pd.Series, window: int = 20) -> pd.Series:
        """上升趋势标志（价格在 MA 上方且 MA 上行）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        return ((series > ma) & (ma > ma.shift(5).fillna(ma))).astype(float)

    @staticmethod
    def ts_downtrend_flag(series: pd.Series, window: int = 20) -> pd.Series:
        """下降趋势标志。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        return ((series < ma) & (ma < ma.shift(5).fillna(ma))).astype(float)

    @staticmethod
    def ts_sideways_flag(series: pd.Series, window: int = 20) -> pd.Series:
        """横盘标志（振幅收窄且无趋势）。"""
        amp = _native_apply(
            series,
            window,
            _MINP,
            lambda v: float(np.ptp(v)) / max(abs(float(np.mean(v))), 1e-9),
            lambda rows: (
                (np.max(rows, axis=-1) - np.min(rows, axis=-1)) / np.maximum(np.abs(np.mean(rows, axis=-1)), 1e-9)
            ),
        )
        return (amp < 0.05).astype(float)

    @staticmethod
    def ts_trend_direction_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势方向强度（净方向收益 / 波动，-1~1）。"""
        return (D12Ops.ts_uptrend_flag(series, window) - D12Ops.ts_downtrend_flag(series, window)).fillna(0.0)

    @staticmethod
    def ts_multi_tf_trend(series: pd.Series, short: int = 10, mid: int = 30, long: int = 60) -> pd.Series:
        """多周期趋势一致（三周期 MA 方向一致度）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        mm = series.rolling(mid, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean()
        s_up = (ms > mm).astype(float)
        m_up = (mm > ml).astype(float)
        l_up = (ml > ml.shift(long).fillna(ml)).astype(float)
        return (s_up + m_up + l_up) / 3.0 - (1.0 - s_up + 1.0 - m_up + 1.0 - l_up) / 3.0

    @staticmethod
    def ts_fractal_up(high: pd.Series, window: int = 5) -> pd.Series:
        """分形向上（中间高点高于两侧）。"""
        mid = high
        left = high.shift(1).fillna(high)
        right = high.shift(-1).fillna(high)
        return ((mid > left) & (mid > right)).astype(float)

    @staticmethod
    def ts_fractal_down(low: pd.Series, window: int = 5) -> pd.Series:
        """分形向下（中间低点低于两侧）。"""
        mid = low
        left = low.shift(1).fillna(low)
        right = low.shift(-1).fillna(low)
        return ((mid < left) & (mid < right)).astype(float)

    @staticmethod
    def ts_support_proximity(series: pd.Series, window: int = 60) -> pd.Series:
        """支撑接近度（接近窗口低点 → 高值）。"""
        ll = series.rolling(window, min_periods=_MINP).min()
        span = (series.rolling(window, min_periods=_MINP).max() - ll).replace(0.0, np.nan)
        return (1.0 - (series - ll) / span).clip(0.0, 1.0).fillna(0.0)

    @staticmethod
    def ts_resistance_proximity(series: pd.Series, window: int = 60) -> pd.Series:
        """压力接近度（接近窗口高点 → 高值）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        span = (hh - series.rolling(window, min_periods=_MINP).min()).replace(0.0, np.nan)
        return ((hh - series) / span).clip(0.0, 1.0).fillna(0.0)

    @staticmethod
    def ts_breakout_pullback_signal(series: pd.Series, window: int = 20) -> pd.Series:
        """突破回踩信号（突破后回调至突破位附近 → 买点）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        prev_hh = hh.shift(1).fillna(hh)
        broke = (series > prev_hh).astype(float)
        pullback = ((series - hh) / series.replace(0.0, np.nan)).clip(-0.03, 0.0)
        return (broke * (pullback < 0).astype(float)).fillna(0.0)

    # ── ADX / 趋向体系 ─────────────────────────────────────

    @staticmethod
    def ts_directional_up(high: pd.Series, low: pd.Series, window: int = 14) -> pd.Series:
        """+DM 方向运动（上升动量，归一化）。"""
        up = (high - high.shift(1)).fillna(0.0)
        dn = (low.shift(1) - low).fillna(0.0)
        pdm = up.where((up > dn) & (up > 0), 0.0)
        return pdm.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_directional_down(high: pd.Series, low: pd.Series, window: int = 14) -> pd.Series:
        """-DM 方向运动。"""
        up = (high - high.shift(1)).fillna(0.0)
        dn = (low.shift(1) - low).fillna(0.0)
        ndm = dn.where((dn > up) & (dn > 0), 0.0)
        return ndm.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_adx_pos(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """+DI 正向指标（+DM / TR 平滑）。"""
        pdm = D12Ops.ts_directional_up(high, low, window)
        tr = (
            pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1)
            .max(axis=1)
            .rolling(window, min_periods=_MINP)
            .mean()
            .replace(0.0, np.nan)
        )
        return (100.0 * pdm / tr).fillna(0.0)

    @staticmethod
    def ts_adx_neg(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """-DI 负向指标。"""
        ndm = D12Ops.ts_directional_down(high, low, window)
        tr = (
            pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1)
            .max(axis=1)
            .rolling(window, min_periods=_MINP)
            .mean()
            .replace(0.0, np.nan)
        )
        return (100.0 * ndm / tr).fillna(0.0)

    @staticmethod
    def ts_adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """ADX 平均趋向指标（趋势强度 0-100）。"""
        pdi = D12Ops.ts_adx_pos(high, low, close, window)
        ndi = D12Ops.ts_adx_neg(high, low, close, window)
        dx = (100.0 * (pdi - ndi).abs() / (pdi + ndi).replace(0.0, np.nan)).fillna(0.0)
        return dx.rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_trend_vol_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势波动比（方向收益 / 波动，趋势 vs 噪音）。"""
        return D12Ops.ts_relative_strength(series, window)

    @staticmethod
    def ts_trend_entropy(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势熵（方向分布无序度，0 单边 / 1 混乱）。"""
        r = _ret(series)
        up = (r > 0).astype(float).rolling(window, min_periods=_MINP).mean().clip(1e-9, 1 - 1e-9)
        p = np.stack([up, 1.0 - up], axis=1)
        entropy = -(p * np.log(p)).sum(axis=1) / np.log(2.0)
        return pd.Series(entropy, index=series.index).fillna(0.5)

    @staticmethod
    def ts_up_down_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """涨跌强度差（上涨强度-下跌强度，±1 归一化）。"""
        r = _ret(series)
        up = r.clip(lower=0.0).rolling(window, min_periods=_MINP).mean()
        down = (-r.clip(upper=0.0)).rolling(window, min_periods=_MINP).mean()
        denom = (up + down).replace(0.0, np.nan)
        return ((up - down) / denom).fillna(0.0)


class D13Ops:
    """D13 截面/排名族（L2）— 45 个截面变换与排名变体算子。

    语义: 单序列滚动窗口内做"截面"变换（对齐 C9Ops cs_* 模式）。
    全部向量化 + NaN 兜底，常数序列降级 0/中值。
    """

    # ── 排名类 ─────────────────────────────────────────────

    @staticmethod
    def cs_rank_pct(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动排名百分位（0-1，当前值在窗口内的排名位置）。"""
        return series.rolling(window, min_periods=_MINP).rank(pct=True).fillna(0.5)

    @staticmethod
    def cs_percent_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """百分比排名（同 cs_rank_pct，独立别名）。"""
        return D13Ops.cs_rank_pct(series, window)

    @staticmethod
    def cs_rank_demean(series: pd.Series, window: int = 20) -> pd.Series:
        """排名去均值（排名减去窗口平均排名）。"""
        r = series.rolling(window, min_periods=_MINP).rank()
        mu = r.rolling(window, min_periods=_MINP).mean()
        return (r - mu).fillna(0.0)

    @staticmethod
    def cs_inverse_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """逆排名（窗口内最小者得分最高）。"""
        r = series.rolling(window, min_periods=_MINP).rank()
        return (-r).fillna(0.0)

    @staticmethod
    def cs_signed_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """带符号排名（高于中位数正、低于负）。"""
        r = D13Ops.cs_rank_pct(series, window)
        return (2.0 * r - 1.0).fillna(0.0)

    @staticmethod
    def cs_rank_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """排名比（排名/窗口大小，归一化位置）。"""
        r = series.rolling(window, min_periods=_MINP).rank()
        return (r / max(window, 1)).fillna(0.5)

    @staticmethod
    def cs_cross_rank_diff(series: pd.Series, window: int = 20) -> pd.Series:
        """截面排名差（当前排名 - 滞后排名，排名动量）。"""
        r = series.rolling(window, min_periods=_MINP).rank()
        prev = r.shift(5).fillna(r)
        return (r - prev).fillna(0.0)

    @staticmethod
    def cs_rank_momentum(series: pd.Series, window: int = 20) -> pd.Series:
        """排名动量（排名百分位变化）。"""
        pct = D13Ops.cs_rank_pct(series, window)
        return pct.diff(5).fillna(0.0)

    @staticmethod
    def cs_rank_volatility(series: pd.Series, window: int = 20) -> pd.Series:
        """排名波动（排名百分位的滚动标准差，排名稳定性反指标）。"""
        return D13Ops.cs_rank_pct(series, window).rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def cs_rank_stability(series: pd.Series, window: int = 20) -> pd.Series:
        """排名稳定性（排名变化小的占比）。"""
        r = D13Ops.cs_rank_pct(series, window)
        chg = r.diff().abs().fillna(0.0)
        return (chg < 0.1).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def cs_ewm_rank(series: pd.Series, span: int = 20) -> pd.Series:
        """指数排名（EWMA 平滑的排名百分位）。"""
        return D13Ops.cs_rank_pct(series, span).ewm(span=span, min_periods=_MINP).mean().fillna(0.5)

    @staticmethod
    def cs_smooth_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """平滑排名（排名百分位的移动均值）。"""
        return D13Ops.cs_rank_pct(series, window).rolling(window, min_periods=_MINP).mean().fillna(0.5)

    @staticmethod
    def cs_robust_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """稳健排名（MAD 修剪后排名，抗极端值）。"""
        med = series.rolling(window, min_periods=_MINP).median()
        mad = (series - med).abs().rolling(window, min_periods=_MINP).median().replace(0.0, np.nan)
        z = ((series - med) / (1.4826 * mad)).fillna(0.0)
        clipped = z.clip(-3.0, 3.0)
        return clipped.rolling(window, min_periods=_MINP).rank(pct=True).fillna(0.5)

    @staticmethod
    def cs_quantile_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """分位排名（rank pct 分桶到 5 档）。"""
        pct = D13Ops.cs_rank_pct(series, window)
        return np.floor(pct * 5.0).clip(0.0, 4.0)

    @staticmethod
    def cs_cross_section_bucket(series: pd.Series, window: int = 20, n_buckets: int = 5) -> pd.Series:
        """截面分桶（按排名分 n 桶，0..n-1）。"""
        pct = D13Ops.cs_rank_pct(series, window)
        return np.floor(pct * n_buckets).clip(0.0, float(n_buckets - 1))

    # ── 归一化/标准化类 ────────────────────────────────────

    @staticmethod
    def cs_zscore_med(series: pd.Series, window: int = 20) -> pd.Series:
        """中位数 zscore（基于中位数与 MAD 的稳健标准化）。"""
        med = series.rolling(window, min_periods=_MINP).median()
        mad = (series - med).abs().rolling(window, min_periods=_MINP).median().replace(0.0, np.nan)
        return ((series - med) / (1.4826 * mad)).fillna(0.0)

    @staticmethod
    def cs_mad_zscore(series: pd.Series, window: int = 20) -> pd.Series:
        """MAD zscore（同 cs_zscore_med 别名）。"""
        return D13Ops.cs_zscore_med(series, window)

    @staticmethod
    def cs_winsor_z(series: pd.Series, window: int = 20, k: float = 3.0) -> pd.Series:
        """Winsorize 后 zscore（极端值截断再标准化）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        sd = series.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        z = ((series - mu) / sd).fillna(0.0)
        return z.clip(-k, k)

    @staticmethod
    def cs_normalize_01(series: pd.Series, window: int = 20) -> pd.Series:
        """0-1 归一化（min-max 缩放）。"""
        mx = series.rolling(window, min_periods=_MINP).max()
        mn = series.rolling(window, min_periods=_MINP).min()
        span = (mx - mn).replace(0.0, np.nan)
        return ((series - mn) / span).fillna(0.5)

    @staticmethod
    def cs_minmax_norm(series: pd.Series, window: int = 20) -> pd.Series:
        """Min-Max 归一化（同 cs_normalize_01）。"""
        return D13Ops.cs_normalize_01(series, window)

    @staticmethod
    def cs_softmax_weight(series: pd.Series, window: int = 20) -> pd.Series:
        """Softmax 权重（窗口内相对权重，总和 1）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        dev = (series - mu).fillna(0.0)
        exp = np.exp(dev - dev.rolling(window, min_periods=_MINP).max().fillna(0.0))
        den = exp.rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (exp / den).fillna(0.0)

    # ── 偏离/相对类 ────────────────────────────────────────

    @staticmethod
    def cs_distance_median(series: pd.Series, window: int = 20) -> pd.Series:
        """距中位数距离（(x-med)/med，百分比偏离）。"""
        med = series.rolling(window, min_periods=_MINP).median().replace(0.0, np.nan)
        return ((series - med) / med).fillna(0.0)

    @staticmethod
    def cs_distance_mean(series: pd.Series, window: int = 20) -> pd.Series:
        """距均值距离（(x-mean)/mean）。"""
        mu = series.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return ((series - mu) / mu).fillna(0.0)

    @staticmethod
    def cs_relative_to_max(series: pd.Series, window: int = 20) -> pd.Series:
        """相对最大值（x/max-1，距高点回撤）。"""
        mx = series.rolling(window, min_periods=_MINP).max().replace(0.0, np.nan)
        return (series / mx - 1.0).fillna(0.0)

    @staticmethod
    def cs_relative_to_min(series: pd.Series, window: int = 20) -> pd.Series:
        """相对最小值（x/min-1，距低点上涨）。"""
        mn = series.rolling(window, min_periods=_MINP).min().replace(0.0, np.nan)
        return (series / mn - 1.0).fillna(0.0)

    @staticmethod
    def cs_max_share(series: pd.Series, window: int = 20) -> pd.Series:
        """占最大比例（x/窗口最大，≤1）。"""
        mx = series.rolling(window, min_periods=_MINP).max().replace(0.0, np.nan)
        return (series / mx).fillna(0.0)

    @staticmethod
    def cs_trim_mean_diff(series: pd.Series, window: int = 20) -> pd.Series:
        """与修剪均值差（x - 10% 修剪均值）。"""

        def _trim(v: np.ndarray) -> float:
            s = np.sort(v)
            return float(np.mean(s[int(s.size * 0.1) : max(int(s.size * 0.9), 1)]))

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)
            lo = int(s.shape[1] * 0.1)
            hi = max(int(s.shape[1] * 0.9), 1)
            return np.mean(s[:, lo:hi], axis=-1)

        tm = _native_apply(series, window, _MINP, _trim, _batch)
        return (series - tm).fillna(0.0)

    @staticmethod
    def cs_market_relative(series: pd.Series, window: int = 20) -> pd.Series:
        """相对市场（x/窗口均值-1，超额）。"""
        mu = series.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (series / mu - 1.0).fillna(0.0)

    # ── 离散/集中度类 ──────────────────────────────────────

    @staticmethod
    def cs_dispersion(series: pd.Series, window: int = 20) -> pd.Series:
        """截面离散度（窗口内相对标准差）。"""
        mu = series.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        sd = series.rolling(window, min_periods=_MINP).std().fillna(0.0)
        return (sd / mu).fillna(0.0)

    @staticmethod
    def cs_coefficient_variation(series: pd.Series, window: int = 20) -> pd.Series:
        """变异系数（std/mean）。"""
        return D13Ops.cs_dispersion(series, window)

    @staticmethod
    def cs_gini_score(series: pd.Series, window: int = 20) -> pd.Series:
        """基尼系数（窗口内分布不均匀度）。"""

        def _gini(v: np.ndarray) -> float:
            x = np.sort(v[~np.isnan(v)])
            n = x.size
            if n < 2:
                return 0.0
            s = float(np.sum(x))
            if s == 0:
                return 0.0
            return float(2.0 * np.sum(np.arange(1, n + 1) * x) / (n * s) - (n + 1.0) / n)

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)
            sums = np.sum(s, axis=-1)
            num = 2.0 * np.sum(np.arange(1, rows.shape[1] + 1) * s, axis=-1)
            res = num / (rows.shape[1] * sums) - (rows.shape[1] + 1.0) / rows.shape[1]
            return np.where(sums != 0, res, 0.0)

        return _native_apply(series, window, _MINP, _gini, _batch).fillna(0.0)

    @staticmethod
    def cs_herfindahl(series: pd.Series, window: int = 20) -> pd.Series:
        """赫芬达尔指数（窗口内份额集中度，越集中越高）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        share = (series / mu.replace(0.0, np.nan)).fillna(0.0)
        return (share**2).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def cs_concentration(series: pd.Series, window: int = 20) -> pd.Series:
        """集中度（前 20% 值占总和比例）。"""

        def _conc(v: np.ndarray) -> float:
            x = np.sort(v[~np.isnan(v)])[::-1]
            n = x.size
            if n < 2 or np.sum(x) == 0:
                return 0.0
            k = max(1, n // 5)
            return float(np.sum(x[:k]) / np.sum(x))

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)[:, ::-1]
            sums = np.sum(s, axis=-1)
            k = max(1, rows.shape[1] // 5)
            return np.where(sums != 0, np.sum(s[:, :k], axis=-1) / sums, 0.0)

        return _native_apply(series, window, _MINP, _conc, _batch).fillna(0.0)

    @staticmethod
    def cs_top_bottom_spread(series: pd.Series, window: int = 20) -> pd.Series:
        """高低差（窗口 top10% 均值 - bottom10% 均值）。"""

        def _spread(v: np.ndarray) -> float:
            x = np.sort(v[~np.isnan(v)])
            n = x.size
            if n < 10:
                return 0.0
            k = max(1, n // 10)
            return float(np.mean(x[-k:]) - np.mean(x[:k]))

        def _batch(rows: np.ndarray) -> np.ndarray:
            w = rows.shape[1]
            if w < 10:
                return np.zeros(rows.shape[0], dtype=float)
            s = np.sort(rows, axis=-1)
            k = max(1, w // 10)
            return np.mean(s[:, -k:], axis=-1) - np.mean(s[:, :k], axis=-1)

        return _native_apply(series, window, _MINP, _spread, _batch).fillna(0.0)

    @staticmethod
    def cs_winner_loser_gap(series: pd.Series, window: int = 20) -> pd.Series:
        """赢家输家差（窗口内最大-最小，极差）。"""
        mx = series.rolling(window, min_periods=_MINP).max()
        mn = series.rolling(window, min_periods=_MINP).min()
        return (mx - mn).fillna(0.0)

    @staticmethod
    def cs_median_gap(series: pd.Series, window: int = 20) -> pd.Series:
        """中位差（x 与窗口下四分位差距，衡量低位偏离）。"""
        q25 = series.rolling(window, min_periods=_MINP).quantile(0.25)
        return (series - q25).fillna(0.0)

    @staticmethod
    def cs_extreme_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """极端强度（x 超过窗口 95% 分位强度，带符号）。"""
        q95 = series.rolling(window, min_periods=_MINP).quantile(0.95)
        q05 = series.rolling(window, min_periods=_MINP).quantile(0.05)
        up = (series - q95).clip(lower=0.0)
        dn = (q05 - series).clip(lower=0.0)
        return (up - dn).fillna(0.0)

    @staticmethod
    def cs_outlier_flag(series: pd.Series, window: int = 20, k: float = 3.0) -> pd.Series:
        """异常点标记（|z|>k → 1）。"""
        z = D13Ops.cs_winsor_z(series, window, 10.0)
        return (z.abs() > k).astype(float)

    @staticmethod
    def cs_tail_weight(series: pd.Series, window: int = 20) -> pd.Series:
        """尾部权重（x 在窗口尾部 10% 的权重贡献）。"""
        pct = D13Ops.cs_rank_pct(series, window)
        return (pct < 0.1).astype(float) * (1.0 - pct) + (pct > 0.9).astype(float) * pct

    @staticmethod
    def cs_skewness_score(series: pd.Series, window: int = 20) -> pd.Series:
        """偏度得分（窗口偏度归一化）。"""
        sk = series.rolling(window, min_periods=3).skew().fillna(0.0)
        return np.tanh(sk)

    @staticmethod
    def cs_kurtosis_score(series: pd.Series, window: int = 20) -> pd.Series:
        """峰度得分（窗口峰度归一化，厚尾程度）。"""
        kurt = series.rolling(window, min_periods=4).kurt().fillna(0.0)
        return np.tanh(kurt / 3.0)

    @staticmethod
    def cs_extreme_skew(series: pd.Series, window: int = 20) -> pd.Series:
        """极端偏度（尾部不对称，上尾-下尾占比）。"""
        q95 = series.rolling(window, min_periods=_MINP).quantile(0.95)
        q05 = series.rolling(window, min_periods=_MINP).quantile(0.05)
        span = (q95 - q05).replace(0.0, np.nan)
        up = ((series - q95).clip(lower=0.0) / span).fillna(0.0)
        dn = ((q05 - series).clip(lower=0.0) / span).fillna(0.0)
        return up - dn

    @staticmethod
    def cs_breadth_position(series: pd.Series, window: int = 20) -> pd.Series:
        """广度位置（窗口内高于均值的比例，0-1）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        return (series > mu).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def cs_entropy_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """排名熵（窗口内排名分布的均匀度，越低越集中）。"""
        pct = D13Ops.cs_rank_pct(series, window)
        p = pct.clip(1e-9, 1 - 1e-9)
        return -(p * np.log(p)).rolling(window, min_periods=_MINP).mean().fillna(0.5)

    @staticmethod
    def cs_outlier_ratio(series: pd.Series, window: int = 20, k: float = 3.0) -> pd.Series:
        """异常占比（窗口内 |z|>k 的比例，极端值密度）。"""
        z = D13Ops.cs_winsor_z(series, window, 10.0)
        return (z.abs() > k).astype(float).rolling(window, min_periods=_MINP).mean().fillna(0.0)


class D14Ops:
    """D14 条件/事件族（L3）— 40 个条件判断与事件信号算子。

    输入约定: 单序列价格；阈值/窗口参数化。全部向量化 + NaN 兜底。
    """

    # ── 阈值条件 ───────────────────────────────────────────

    @staticmethod
    def ts_cross_threshold_up(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """上穿阈值事件（低位→高位穿越，+1）。"""
        above = (series > threshold).astype(float)
        cross = above.diff().fillna(0.0)
        return (cross > 0).astype(float)

    @staticmethod
    def ts_cross_threshold_down(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """下穿阈值事件（-1）。"""
        below = (series < threshold).astype(float)
        cross = below.diff().fillna(0.0)
        return (cross > 0).astype(float)

    @staticmethod
    def ts_threshold_band(series: pd.Series, lo: float = -1.0, hi: float = 1.0) -> pd.Series:
        """阈值带内（lo≤x≤hi → 1，带内状态）。"""
        return ((series >= lo) & (series <= hi)).astype(float)

    @staticmethod
    def ts_range_condition(series: pd.Series, lo: float = -1.0, hi: float = 1.0) -> pd.Series:
        """区间内状态（同 ts_threshold_band）。"""
        return D14Ops.ts_threshold_band(series, lo, hi)

    @staticmethod
    def ts_condition_count(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """条件满足计数（窗口内 x>阈值 的天数）。"""
        cond = (series > threshold).astype(float)
        return cond.rolling(window, min_periods=_MINP).sum()

    @staticmethod
    def ts_condition_ratio(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """条件占比（窗口内满足条件比例，0-1）。"""
        cond = (series > threshold).astype(float)
        return cond.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_consecutive_above(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """连续高于阈值天数。"""
        above = (series > threshold).astype(int)
        grp = (above != above.shift(1).fillna(0)).cumsum()
        return above.groupby(grp).cumsum().astype(float)

    @staticmethod
    def ts_consecutive_below(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """连续低于阈值天数。"""
        below = (series < threshold).astype(int)
        grp = (below != below.shift(1).fillna(0)).cumsum()
        return below.groupby(grp).cumsum().astype(float)

    @staticmethod
    def ts_consecutive_increase(series: pd.Series) -> pd.Series:
        """连续上涨天数。"""
        up = (series.diff().fillna(0.0) > 0).astype(int)
        grp = (up != up.shift(1).fillna(0)).cumsum()
        return up.groupby(grp).cumsum().astype(float)

    @staticmethod
    def ts_consecutive_decrease(series: pd.Series) -> pd.Series:
        """连续下跌天数。"""
        dn = (series.diff().fillna(0.0) < 0).astype(int)
        grp = (dn != dn.shift(1).fillna(0)).cumsum()
        return dn.groupby(grp).cumsum().astype(float)

    @staticmethod
    def ts_consecutive_same_sign(series: pd.Series) -> pd.Series:
        """连续同号天数（上涨或下跌延续）。"""
        up = (series.diff().fillna(0.0) > 0).astype(int)
        dn = (series.diff().fillna(0.0) < 0).astype(int)
        both = (up + dn).clip(0, 1)
        grp = (both != both.shift(1).fillna(0)).cumsum()
        return both.groupby(grp).cumsum().astype(float)

    @staticmethod
    def ts_condition_change(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """条件切换事件（满足→不满足或反之）。"""
        cond = (series > threshold).astype(float)
        return cond.diff().abs().fillna(0.0)

    @staticmethod
    def ts_condition_switch_rate(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """条件切换率（窗口内状态翻转频率）。"""
        return D14Ops.ts_condition_change(series, threshold).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_state_duration(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """状态持续期（当前状态已持续天数，带符号：上+下-）。"""
        above = D14Ops.ts_consecutive_above(series, threshold)
        below = D14Ops.ts_consecutive_below(series, threshold)
        return above - below

    @staticmethod
    def ts_state_age(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """状态年龄（当前状态持续期，0 表示切换）。"""
        dur = D14Ops.ts_state_duration(series, threshold)
        return dur.abs()

    # ── 事件信号 ───────────────────────────────────────────

    @staticmethod
    def ts_breakout_event(series: pd.Series, window: int = 20) -> pd.Series:
        """突破事件（创窗口新高，+1）。"""
        hh = series.rolling(window, min_periods=_MINP).max().shift(1)
        return ((series > hh.fillna(series)) & (series > series.shift(1).fillna(series))).astype(float)

    @staticmethod
    def ts_breakdown_event(series: pd.Series, window: int = 20) -> pd.Series:
        """跌破事件（创窗口新低，-1）。"""
        ll = series.rolling(window, min_periods=_MINP).min().shift(1)
        return ((series < ll.fillna(series)) & (series < series.shift(1).fillna(series))).astype(float)

    @staticmethod
    def ts_cross_ma_event(series: pd.Series, window: int = 20) -> pd.Series:
        """穿均线事件（价格上穿/下穿 MA，±1）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        above = (series > ma).astype(float)
        cross = above.diff().fillna(0.0)
        return pd.Series(np.where(cross > 0, 1.0, np.where(cross < 0, -1.0, 0.0)), index=series.index)

    @staticmethod
    def ts_golden_cross_event(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """金叉事件（快线上穿慢线，+1）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean()
        diff = (ms - ml).fillna(0.0)
        cross = np.sign(diff).diff().fillna(0.0)
        return (cross > 0).astype(float)

    @staticmethod
    def ts_death_cross_event(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """死叉事件（快线下穿慢线，-1）。"""
        ms = series.rolling(short, min_periods=_MINP).mean()
        ml = series.rolling(long, min_periods=_MINP).mean()
        diff = (ms - ml).fillna(0.0)
        cross = np.sign(diff).diff().fillna(0.0)
        return (cross < 0).astype(float)

    @staticmethod
    def ts_turning_point(series: pd.Series, window: int = 5) -> pd.Series:
        """转折点（局部顶/底，±1）。"""
        hh = series.rolling(window * 2 + 1, min_periods=1, center=True).max()
        ll = series.rolling(window * 2 + 1, min_periods=1, center=True).min()
        return pd.Series(
            np.where(series == hh, 1.0, np.where(series == ll, -1.0, 0.0)),
            index=series.index,
        )

    @staticmethod
    def ts_zigzag_direction(series: pd.Series, window: int = 5) -> pd.Series:
        """之字形方向（相对转折点的方向延续）。"""
        tp = D14Ops.ts_turning_point(series, window)
        last_dir = 0.0
        out = np.zeros(len(series))
        for i, v in enumerate(tp.values):
            if v != 0:
                last_dir = v
            out[i] = last_dir
        return pd.Series(out, index=series.index)

    @staticmethod
    def ts_event_density(series: pd.Series, window: int = 20) -> pd.Series:
        """事件密度（窗口内转折点频率）。"""
        tp = D14Ops.ts_turning_point(series, 3)
        return (tp.abs() > 0).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_event_count_n(series: pd.Series, window: int = 20) -> pd.Series:
        """n 期事件数（窗口内穿阈值次数）。"""
        return D14Ops.ts_condition_change(series, 0.0).rolling(window, min_periods=_MINP).sum()

    @staticmethod
    def ts_signal_persistence(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """信号持续（窗口内同向信号最长连续天数）。"""
        cond = (series > threshold).astype(int)
        grp = (cond != cond.shift(1).fillna(0)).cumsum()
        run = cond.groupby(grp).cumsum()
        return run.rolling(window, min_periods=_MINP).max().fillna(0.0)

    @staticmethod
    def ts_signal_decay(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """信号衰减（条件满足后的逐日衰减权重）。"""
        cond = (series > threshold).astype(float)
        return (cond * np.exp(-np.arange(len(series)) % max(window, 1) / max(window, 1))).fillna(0.0)

    @staticmethod
    def ts_condition_entropy(series: pd.Series, threshold: float = 0.0, window: int = 20) -> pd.Series:
        """条件熵（条件状态分布的无序度，0 恒定 / 1 混乱）。"""
        cond = (series > threshold).astype(float).rolling(window, min_periods=_MINP).mean().clip(1e-9, 1 - 1e-9)
        p = np.stack([cond, 1.0 - cond], axis=1)
        return pd.Series(-(p * np.log(p)).sum(axis=1) / np.log(2.0), index=series.index).fillna(0.5)

    # ── 模式/过滤条件 ──────────────────────────────────────

    @staticmethod
    def ts_pattern_continuation(series: pd.Series, window: int = 20) -> pd.Series:
        """形态延续（趋势中回调不破前低 → 延续信号）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        up = (series > ma).astype(float)
        ll = series.rolling(window, min_periods=_MINP).min()
        not_break = (series > ll.shift(3).fillna(ll)).astype(float)
        return (up * not_break).fillna(0.0)

    @staticmethod
    def ts_pattern_reversal(series: pd.Series, window: int = 20) -> pd.Series:
        """形态反转（新高后回落穿均线 → 反转）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        ma = series.rolling(window, min_periods=_MINP).mean()
        was_high = (series >= hh.shift(3).fillna(hh)).astype(float)
        broke_ma = (series < ma).astype(float)
        return (was_high * broke_ma).fillna(0.0)

    @staticmethod
    def ts_momentum_filter(series: pd.Series, window: int = 20) -> pd.Series:
        """动量过滤（动量为正 → 1，可作条件门）。"""
        return (series.diff(5).fillna(0.0) > 0).astype(float)

    @staticmethod
    def ts_volatility_filter(series: pd.Series, window: int = 20) -> pd.Series:
        """波动过滤（波动率低于窗口均值 → 1，低波动状态）。"""
        vol = _ret(series).rolling(10, min_periods=_MINP).std().fillna(0.0)
        mu = vol.rolling(window, min_periods=_MINP).mean()
        return (vol <= mu).astype(float)

    @staticmethod
    def ts_liquidity_filter(series: pd.Series, window: int = 20) -> pd.Series:
        """流动性过滤（量能高于窗口均值 → 1）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        return (series > mu).astype(float)

    @staticmethod
    def ts_trend_condition(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势条件（价格与动量同向 → 条件满足）。"""
        ma = series.rolling(window, min_periods=_MINP).mean()
        m = series.diff(5).fillna(0.0)
        return ((series > ma) & (m > 0)).astype(float)

    @staticmethod
    def ts_breakout_condition(series: pd.Series, window: int = 20) -> pd.Series:
        """突破条件（价格接近并突破窗口高点）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        return (series >= hh * 0.99).astype(float)

    @staticmethod
    def ts_reversal_condition(series: pd.Series, window: int = 20) -> pd.Series:
        """反转条件（价格偏离均值超 2σ 后回归）。"""
        z = D12Ops.ts_velocity_zscore(series, window)
        return (z.abs() > 2.0).astype(float)

    @staticmethod
    def ts_level_test(series: pd.Series, window: int = 20) -> pd.Series:
        """水平测试（突破窗口高点后 n 日内不回落 → 站稳）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        broke = (series > hh.shift(1).fillna(hh)).astype(float)
        above = (series > hh.shift(1).fillna(hh) * 0.98).astype(float)
        return (broke * above.rolling(3, min_periods=1).mean()).fillna(0.0)

    @staticmethod
    def ts_support_break(series: pd.Series, window: int = 60) -> pd.Series:
        """支撑跌破（收盘跌破窗口低点，-1）。"""
        ll = series.rolling(window, min_periods=_MINP).min().shift(1)
        return ((series < ll.fillna(series)) & (series < series.shift(1).fillna(series))).astype(float)

    @staticmethod
    def ts_resistance_break(series: pd.Series, window: int = 60) -> pd.Series:
        """压力突破（收盘突破窗口高点，+1）。"""
        hh = series.rolling(window, min_periods=_MINP).max().shift(1)
        return ((series > hh.fillna(series)) & (series > series.shift(1).fillna(series))).astype(float)

    @staticmethod
    def ts_condition_combo(series: pd.Series, window: int = 20) -> pd.Series:
        """条件组合（趋势+突破+动量三重条件交集）。"""
        t = D14Ops.ts_trend_condition(series, window)
        b = D14Ops.ts_breakout_condition(series, window)
        m = D14Ops.ts_momentum_filter(series, window)
        return (t + b + m).clip(0, 1).fillna(0.0)

    @staticmethod
    def ts_breakout_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """突破强度（突破窗口高点幅度 / 波动，事件量级）。"""
        hh = series.rolling(window, min_periods=_MINP).max()
        vol = _ret(series).rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((series - hh) / vol).fillna(0.0)


class D15Ops:
    """D15 组合/跨序列族（L4）— 50 个双序列组合与配对算子。

    输入约定: 双序列 x/y 成对传参（如 close 与 volume、open 与 close）。
    全部向量化 + NaN 兜底，零值安全。
    """

    # ── 基础组合 ───────────────────────────────────────────

    @staticmethod
    def cs_ratio(x: pd.Series, y: pd.Series) -> pd.Series:
        """比率 x/y。"""
        return (x / y.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def cs_diff(x: pd.Series, y: pd.Series) -> pd.Series:
        """差 x-y。"""
        return (x - y).fillna(0.0)

    @staticmethod
    def cs_sum(x: pd.Series, y: pd.Series) -> pd.Series:
        """和 x+y。"""
        return (x + y).fillna(0.0)

    @staticmethod
    def cs_product(x: pd.Series, y: pd.Series) -> pd.Series:
        """积 x·y。"""
        return (x * y).fillna(0.0)

    @staticmethod
    def cs_min(x: pd.Series, y: pd.Series) -> pd.Series:
        """两序列小者。"""
        return pd.concat([x, y], axis=1).min(axis=1).fillna(0.0)

    @staticmethod
    def cs_max(x: pd.Series, y: pd.Series) -> pd.Series:
        """两序列大者。"""
        return pd.concat([x, y], axis=1).max(axis=1).fillna(0.0)

    @staticmethod
    def cs_spread(x: pd.Series, y: pd.Series) -> pd.Series:
        """价差 x-y（同 cs_diff）。"""
        return D15Ops.cs_diff(x, y)

    @staticmethod
    def cs_return_spread(x: pd.Series, y: pd.Series) -> pd.Series:
        """收益差（两序列收益率之差）。"""
        return (_ret(x) - _ret(y)).fillna(0.0)

    @staticmethod
    def cs_relative_ratio(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """相对比率（x/y 相对其均值偏离）。"""
        r = D15Ops.cs_ratio(x, y)
        mu = r.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (r / mu - 1.0).fillna(0.0)

    @staticmethod
    def cs_log_ratio(x: pd.Series, y: pd.Series) -> pd.Series:
        """对数比率 log(x/y)。"""
        return (np.log(x.clip(lower=1e-8)) - np.log(y.clip(lower=1e-8))).fillna(0.0)

    @staticmethod
    def cs_pct_diff(x: pd.Series, y: pd.Series) -> pd.Series:
        """百分比差 (x-y)/y。"""
        return ((x - y) / y.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def cs_weighted_average(x: pd.Series, y: pd.Series, w: float = 0.5) -> pd.Series:
        """加权平均 w·x + (1-w)·y。"""
        return (w * x + (1.0 - w) * y).fillna(0.0)

    @staticmethod
    def cs_composite_score(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """复合得分（双序列 zscore 均值）。"""
        zx = D13Ops.cs_zscore_med(x, window)
        zy = D13Ops.cs_zscore_med(y, window)
        return ((zx + zy) / 2.0).fillna(0.0)

    @staticmethod
    def cs_normalized_ratio(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """归一化比率（x/y 的滚动 zscore）。"""
        r = D15Ops.cs_ratio(x, y)
        mu = r.rolling(window, min_periods=_MINP).mean()
        sd = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((r - mu) / sd).fillna(0.0)

    @staticmethod
    def cs_smoothed_ratio(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """平滑比率（x/y 的滚动均值）。"""
        return D15Ops.cs_ratio(x, y).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def cs_exponential_ratio(x: pd.Series, y: pd.Series, span: int = 20) -> pd.Series:
        """指数比率（x/y 的 EWMA）。"""
        return D15Ops.cs_ratio(x, y).ewm(span=span, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def cs_ratio_ma(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """比率均线偏离（x/y 相对其 MA 的偏差）。"""
        r = D15Ops.cs_ratio(x, y)
        ma = r.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (r / ma - 1.0).fillna(0.0)

    @staticmethod
    def cs_ratio_zscore(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """比率 zscore（同 cs_normalized_ratio）。"""
        return D15Ops.cs_normalized_ratio(x, y, window)

    @staticmethod
    def cs_relative_strength_ratio(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """相对强弱比率（两序列夏普比之差）。"""
        sx = D10Ops.ts_sharpe_ratio(x, window)
        sy = D10Ops.ts_sharpe_ratio(y, window)
        return (sx - sy).fillna(0.0)

    # ── 相关/回归类 ────────────────────────────────────────

    @staticmethod
    def ts_pair_corr(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """滚动相关系数（双序列滚动 Pearson 相关）。"""
        return _corr_clean(x.rolling(window, min_periods=_MINP).corr(y))

    @staticmethod
    def ts_cov(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """滚动协方差。"""
        mx = x.rolling(window, min_periods=_MINP).mean()
        my = y.rolling(window, min_periods=_MINP).mean()
        return ((x - mx) * (y - my)).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_beta(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """滚动 beta（x 对 y 的回归斜率）。"""
        cov = D15Ops.ts_cov(x, y, window)
        vy = y.rolling(window, min_periods=_MINP).var().replace(0.0, np.nan)
        return (cov / vy).fillna(0.0)

    @staticmethod
    def ts_alpha(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """滚动 alpha（x 对 y 回归截距）。"""
        beta = D15Ops.ts_beta(x, y, window)
        mx = x.rolling(window, min_periods=_MINP).mean()
        my = y.rolling(window, min_periods=_MINP).mean()
        return (mx - beta * my).fillna(0.0)

    @staticmethod
    def ts_lead_lag_corr(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """前导滞后相关（x 与 y 滞后 1 期相关，领先性）。"""
        return _corr_clean(x.rolling(window, min_periods=_MINP).corr(y.shift(1)))

    @staticmethod
    def ts_cross_corr_lag1(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """滞后 1 互相关（y 领先 x 时为正）。"""
        return _corr_clean(y.rolling(window, min_periods=_MINP).corr(x.shift(1)))

    @staticmethod
    def ts_granger_proxy(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """格兰杰代理（y 滞后值对 x 的增量预测力）。"""
        lag = y.shift(1).fillna(0.0)
        return _corr_clean(x.rolling(window, min_periods=_MINP).corr(lag))

    @staticmethod
    def ts_hedge_ratio(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """对冲比率（收益的滚动 beta）。"""
        rx = _ret(x)
        ry = _ret(y)
        cov = (rx * ry).rolling(window, min_periods=_MINP).mean()
        vr = ry.rolling(window, min_periods=_MINP).var().replace(0.0, np.nan)
        return (cov / vr).fillna(0.0)

    @staticmethod
    def ts_cointegration_proxy(x: pd.Series, y: pd.Series, window: int = 60) -> pd.Series:
        """协整代理（残差序列的自相关反转度，负=协整）。"""
        beta = D15Ops.ts_beta(x, y, window)
        resid = x - beta * y
        lag1 = resid.shift(1)
        return _corr_clean(resid.rolling(window, min_periods=_MINP).corr(lag1))

    # ── 配对交易类 ─────────────────────────────────────────

    @staticmethod
    def ts_spread_zscore(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """价差 zscore（x-y 标准化，配对交易信号）。"""
        s = D15Ops.cs_diff(x, y)
        mu = s.rolling(window, min_periods=_MINP).mean()
        sd = s.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((s - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_spread_band(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """价差带位置（价差在滚动区间的百分位）。"""
        s = D15Ops.cs_diff(x, y)
        return D13Ops.cs_rank_pct(s, window)

    @staticmethod
    def ts_pair_divergence(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """配对背离（|价差 zscore| 高 → 背离）。"""
        return D15Ops.ts_spread_zscore(x, y, window).abs()

    @staticmethod
    def ts_pair_convergence(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """配对收敛（|价差 zscore| 低 → 收敛）。"""
        return 1.0 - np.tanh(D15Ops.ts_spread_zscore(x, y, window).abs().fillna(0.0))

    @staticmethod
    def ts_convergence_rate(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
        """收敛速度（价差 zscore 变化率）。"""
        return D15Ops.ts_spread_zscore(x, y, window).diff(5).abs().fillna(0.0)

    @staticmethod
    def ts_pair_trade_signal(x: pd.Series, y: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """配对交易信号（价差超 2σ 开仓，回归平仓）。"""
        z = D15Ops.ts_spread_zscore(x, y, window)
        return pd.Series(
            np.where(z > k, -1.0, np.where(z < -k, 1.0, 0.0)),
            index=x.index,
        )

    # ── 价格结构类 ─────────────────────────────────────────

    @staticmethod
    def ts_price_gap(open_p: pd.Series, close: pd.Series) -> pd.Series:
        """价格缺口（open/prev_close-1，跳空）。"""
        prev_close = close.shift(1).fillna(close)
        return (open_p / prev_close.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_overnight_return(open_p: pd.Series, close: pd.Series) -> pd.Series:
        """隔夜收益（open/prev_close-1）。"""
        return D15Ops.ts_price_gap(open_p, close)

    @staticmethod
    def ts_intraday_return(open_p: pd.Series, close: pd.Series) -> pd.Series:
        """日内收益（close/open-1）。"""
        return (close / open_p.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_open_close_diff(open_p: pd.Series, close: pd.Series) -> pd.Series:
        """开盘收盘差（close-open）。"""
        return (close - open_p).fillna(0.0)

    @staticmethod
    def ts_high_low_ratio(high: pd.Series, low: pd.Series) -> pd.Series:
        """高低比（high/low-1，日内振幅）。"""
        return (high / low.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_range_ratio(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
        """区间比（(high-low)/close 的滚动均值）。"""
        span = (high - low) / close.replace(0.0, np.nan)
        return span.fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_basis(spot: pd.Series, future: pd.Series) -> pd.Series:
        """基差（spot - future，现货-期货）。"""
        return (spot - future).fillna(0.0)

    @staticmethod
    def ts_basis_ratio(spot: pd.Series, future: pd.Series) -> pd.Series:
        """基差率（(spot-future)/future）。"""
        return ((spot - future) / future.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def ts_term_spread(near: pd.Series, far: pd.Series) -> pd.Series:
        """期限价差（近月-远月）。"""
        return (near - far).fillna(0.0)

    @staticmethod
    def ts_roll_yield(near: pd.Series, far: pd.Series, window: int = 5) -> pd.Series:
        """展期收益（近远月价差的滚动变化）。"""
        spread = (near - far).fillna(0.0)
        return spread.diff(window).fillna(0.0)

    # ── 量价类 ─────────────────────────────────────────────

    @staticmethod
    def ts_volume_price_corr(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量价相关（收盘收益与成交量相关）。"""
        r = _ret(close)
        return _corr_clean(r.rolling(window, min_periods=_MINP).corr(volume))

    @staticmethod
    def ts_volume_ratio_vs_avg(volume: pd.Series, window: int = 20) -> pd.Series:
        """量比（volume/窗口均量）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (volume / mu).fillna(0.0)

    @staticmethod
    def ts_volume_breakout(volume: pd.Series, window: int = 20) -> pd.Series:
        """量突破（volume 创窗口新高）。"""
        hh = volume.rolling(window, min_periods=_MINP).max().shift(1)
        return (volume > hh.fillna(volume)).astype(float)

    @staticmethod
    def ts_volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
        """量 zscore（成交量标准化）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean()
        sd = volume.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((volume - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_price_volume_sync(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量价同步（价格涨且量增 → +1，量价同向度）。"""
        r = _ret(close)
        vchg = np.sign(volume.diff().fillna(0.0))
        pchg = np.sign(r)
        sync = (vchg * pchg).fillna(0.0)
        return sync.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_amount_velocity(amount: pd.Series, window: int = 20) -> pd.Series:
        """成交额速度（成交额差分/均额）。"""
        d = amount.diff().fillna(0.0)
        mu = amount.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (d / mu).fillna(0.0)


class D16Ops:
    """D16 量价/流动性族（L5）— 40 个量价配合与流动性风险算子。

    输入约定: 成交量/成交额/价格序列；多序列按 close/volume 成对传参。
    全部向量化 + NaN 兜底，零值安全。
    """

    # ── 流动性度量 ─────────────────────────────────────────

    @staticmethod
    def ts_amihud_illiquidity(close: pd.Series, amount: pd.Series, window: int = 20) -> pd.Series:
        """Amihud 非流动性（|收益|/成交额，价格冲击度量）。"""
        r = _ret(close).abs()
        return (r / amount.replace(0.0, np.nan)).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_turnover(volume: pd.Series, float_shares: pd.Series, window: int = 20) -> pd.Series:
        """换手率（成交量/流通股本，滚动均值）。"""
        to = (volume / float_shares.replace(0.0, np.nan)).fillna(0.0)
        return to.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_liquidity_ratio(volume: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
        """流动性比率（成交额/价格波动，越大越流动）。"""
        amt = (volume * close).fillna(0.0)
        vol = _ret(close).rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (amt.rolling(window, min_periods=_MINP).mean() / vol).fillna(0.0)

    @staticmethod
    def ts_liquidity_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
        """流动性 zscore（成交量标准化）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean()
        sd = volume.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((volume - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_liquidity_risk(volume: pd.Series, window: int = 60) -> pd.Series:
        """流动性风险（量波动/量均值，供应不稳定度）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        sd = volume.rolling(window, min_periods=_MINP).std().fillna(0.0)
        return (sd / mu).fillna(0.0)

    @staticmethod
    def ts_float_turnover(volume: pd.Series, float_shares: pd.Series) -> pd.Series:
        """流通换手（当日成交量/流通股本）。"""
        return (volume / float_shares.replace(0.0, np.nan)).fillna(0.0)

    @staticmethod
    def ts_dollar_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
        """成交额（价格×成交量）。"""
        return (close * volume).fillna(0.0)

    @staticmethod
    def ts_bid_ask_spread_proxy(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
        """买卖价差代理（高低价差/收盘，波动代理流动性成本）。"""
        span = (high - low) / close.replace(0.0, np.nan)
        return span.fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_trading_intensity(volume: pd.Series, window: int = 20) -> pd.Series:
        """交易强度（量变化率绝对值，活跃度）。"""
        return volume.pct_change().abs().fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_tick_size_proxy(close: pd.Series, window: int = 20) -> pd.Series:
        """最小变动代理（价格精细度，1/收盘的滚动均值）。"""
        return (1.0 / close.replace(0.0, np.nan)).fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_price_impact(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """价格冲击（|收益|/成交量比，成交对价格的边际影响）。"""
        r = _ret(close).abs()
        v = volume.replace(0.0, np.nan)
        return (r / v).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_liquidity_premium(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """流动性溢价（低流动 → 高预期收益，负流动性代理）。"""
        ill = D16Ops.ts_amihud_illiquidity(close, close * volume, window)
        return (-np.log(ill.clip(lower=1e-9))).fillna(0.0)

    # ── 量价配合 ───────────────────────────────────────────

    @staticmethod
    def ts_volume_price_trend(close: pd.Series, volume: pd.Series) -> pd.Series:
        """VPT 量价趋势（累积 量×收益）。"""
        r = _ret(close)
        return (r * volume).cumsum()

    @staticmethod
    def ts_money_flow_ratio(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """资金流比率（上涨日量/下跌日量）。"""
        r = _ret(close)
        up_v = volume.where(r > 0, 0.0).rolling(window, min_periods=_MINP).sum()
        dn_v = volume.where(r < 0, 0.0).rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (up_v / dn_v).fillna(1.0)

    @staticmethod
    def ts_force_index(close: pd.Series, volume: pd.Series, window: int = 13) -> pd.Series:
        """强力指数（价格动量×成交量，趋势力度）。"""
        force = close.diff().fillna(0.0) * volume
        return force.ewm(span=window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_ease_of_movement(
        high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 14
    ) -> pd.Series:
        """EMV 易动度（距离移动/量，价格流畅度）。"""
        mid = (high + low) / 2.0
        dist = mid.diff().fillna(0.0)
        box = (volume / (high - low).replace(0.0, np.nan)).fillna(0.0)
        return (
            (dist / box.replace(0.0, np.nan))
            .replace(np.inf, np.nan)
            .fillna(0.0)
            .rolling(window, min_periods=_MINP)
            .mean()
        )

    @staticmethod
    def ts_volume_price_regime(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量价制度（价涨量增=1 / 价跌量增=-1 / 量缩=0）。"""
        r = _ret(close)
        vchg = np.sign(volume.diff().fillna(0.0))
        pchg = np.sign(r)
        return pd.Series(
            np.where((pchg > 0) & (vchg > 0), 1.0, np.where((pchg < 0) & (vchg > 0), -1.0, 0.0)),
            index=close.index,
        )

    @staticmethod
    def ts_volume_pressure_ratio(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量压比（量增幅度×涨跌幅，买卖压力）。"""
        vchg = volume.pct_change().fillna(0.0)
        r = _ret(close)
        return (vchg * np.sign(r)).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_volume_price_corr_lag(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """滞后量价相关（收益与滞后成交量相关，先行量）。"""
        r = _ret(close)
        return _corr_clean(r.rolling(window, min_periods=_MINP).corr(volume.shift(1)))

    @staticmethod
    def ts_order_flow_proxy(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """订单流代理（主动买卖量差方向）。"""
        r = _ret(close)
        signed = np.sign(r) * volume
        return signed.rolling(window, min_periods=_MINP).sum()

    # ── 量能统计 ───────────────────────────────────────────

    @staticmethod
    def ts_volume_change_rate(volume: pd.Series, window: int = 20) -> pd.Series:
        """量变化率（量 pct_change 滚动均值）。"""
        return volume.pct_change().fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_volume_momentum(volume: pd.Series, window: int = 20) -> pd.Series:
        """量动量（volume/窗口前量）。"""
        prev = volume.shift(window).replace(0.0, np.nan)
        return (volume / prev).fillna(1.0)

    @staticmethod
    def ts_volume_acceleration(volume: pd.Series, window: int = 5) -> pd.Series:
        """量加速度（量的二阶差分平滑）。"""
        return volume.diff().diff().fillna(0.0).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_volume_ma_ratio(volume: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """量均线比（短/长量均线）。"""
        vs = volume.rolling(short, min_periods=_MINP).mean()
        vl = volume.rolling(long, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (vs / vl).fillna(1.0)

    @staticmethod
    def ts_volume_std_ratio(volume: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """量波动比（短/长量波动）。"""
        vs = volume.rolling(short, min_periods=_MINP).std().fillna(0.0)
        vl = volume.rolling(long, min_periods=_MINP).std().replace(0.0, np.nan)
        return (vs / vl).fillna(1.0)

    @staticmethod
    def ts_volume_skewness(volume: pd.Series, window: int = 20) -> pd.Series:
        """量偏度（成交量分布不对称）。"""
        return volume.rolling(window, min_periods=3).skew().fillna(0.0)

    @staticmethod
    def ts_volume_kurtosis(volume: pd.Series, window: int = 20) -> pd.Series:
        """量峰度（成交量分布厚尾）。"""
        return volume.rolling(window, min_periods=4).kurt().fillna(0.0)

    @staticmethod
    def ts_volume_autocorr(volume: pd.Series, window: int = 20) -> pd.Series:
        """量自相关（成交量 lag-1 自相关，惯性）。"""
        lag1 = volume.shift(1)
        return _corr_clean(volume.rolling(window, min_periods=_MINP).corr(lag1))

    @staticmethod
    def ts_volume_entropy(volume: pd.Series, window: int = 20) -> pd.Series:
        """量熵（量能分布均匀度，低=集中）。"""
        share = volume / volume.rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        p = share.fillna(1e-9).clip(1e-9, 1.0)
        return -(p * np.log(p)).rolling(window, min_periods=_MINP).sum().fillna(0.0)

    @staticmethod
    def ts_volume_concentration(volume: pd.Series, window: int = 20) -> pd.Series:
        """量集中度（前 20% 窗口量占比）。"""

        def _conc(v: np.ndarray) -> float:
            x = np.sort(v[~np.isnan(v)])[::-1]
            n = x.size
            if n < 2 or np.sum(x) == 0:
                return 0.0
            k = max(1, n // 5)
            return float(np.sum(x[:k]) / np.sum(x))

        def _batch(rows: np.ndarray) -> np.ndarray:
            s = np.sort(rows, axis=-1)[:, ::-1]
            sums = np.sum(s, axis=-1)
            k = max(1, rows.shape[1] // 5)
            return np.where(sums != 0, np.sum(s[:, :k], axis=-1) / sums, 0.0)

        return _native_apply(volume, window, _MINP, _conc, _batch).fillna(0.0)

    @staticmethod
    def ts_volume_cycle(volume: pd.Series, window: int = 20) -> pd.Series:
        """量周期（量能峰谷位置，周期相位）。"""
        vol_z = D16Ops.ts_liquidity_zscore(volume, window)
        return _native_apply(
            vol_z,
            window,
            _MINP,
            lambda v: float(np.argmax(v)) if v.size else 0.0,
            lambda rows: np.argmax(rows, axis=-1).astype(float),
        ).fillna(0.0)

    # ── 量能异常 ───────────────────────────────────────────

    @staticmethod
    def ts_volume_breakout_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
        """量突破比（当前量/窗口最大量，量能爆发度）。"""
        hh = volume.rolling(window, min_periods=_MINP).max().replace(0.0, np.nan)
        return (volume / hh).fillna(0.0)

    @staticmethod
    def ts_volume_surge(volume: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """量激增（量超过窗口均值 k 倍，事件）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (volume > k * mu).astype(float)

    @staticmethod
    def ts_volume_shrinkage(volume: pd.Series, window: int = 20) -> pd.Series:
        """量萎缩（量低于窗口均值比例）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (1.0 - volume / mu).clip(0.0, 1.0).fillna(0.0)

    @staticmethod
    def ts_volume_spike(volume: pd.Series, window: int = 20) -> pd.Series:
        """量尖峰（量超出窗口 3σ 标记）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean()
        sd = volume.rolling(window, min_periods=_MINP).std().fillna(0.0)
        return (volume > mu + 3.0 * sd).astype(float)

    @staticmethod
    def ts_volume_cluster(volume: pd.Series, window: int = 20) -> pd.Series:
        """量聚集（高量日的窗口内占比，放量持续性）。"""
        mu = volume.rolling(window, min_periods=_MINP).mean()
        return (volume > mu).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_trade_value_ratio(amount: pd.Series, window: int = 20) -> pd.Series:
        """成交额比（当前成交额/窗口均额）。"""
        mu = amount.rolling(window, min_periods=_MINP).mean().replace(0.0, np.nan)
        return (amount / mu).fillna(1.0)

    @staticmethod
    def ts_turnover_zscore(volume: pd.Series, float_shares: pd.Series, window: int = 20) -> pd.Series:
        """换手 zscore（换手率标准化）。"""
        to = D16Ops.ts_float_turnover(volume, float_shares)
        mu = to.rolling(window, min_periods=_MINP).mean()
        sd = to.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return ((to - mu) / sd).fillna(0.0)

    @staticmethod
    def ts_volume_weighted_return(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量加权收益（量占比加权的收益，主力方向）。"""
        r = _ret(close)
        share = volume / volume.rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (r * share.fillna(0.0)).rolling(window, min_periods=_MINP).sum() * window

    @staticmethod
    def ts_price_volume_divergence_score(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """价量背离得分（价动量与量动量符号不一致度）。"""
        p_mom = np.sign(close.diff(5).fillna(0.0))
        v_mom = np.sign(volume.diff(5).fillna(0.0))
        div = -p_mom * v_mom
        return pd.Series(div, index=close.index).rolling(window, min_periods=_MINP).mean().fillna(0.0)


class D17Ops:
    """D17 市场结构/分布族（L5）— 35 个市场结构与制度代理算子。

    输入约定: 单序列（市场指数/组合收益/因子值等）。全部向量化 + NaN 兜底。
    """

    # ── 广度/结构 ──────────────────────────────────────────

    @staticmethod
    def ts_market_breadth(series: pd.Series, window: int = 20) -> pd.Series:
        """市场广度（窗口内正收益占比，参与度）。"""
        return (_ret(series) > 0).astype(float).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_advance_decline_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """涨跌比（上涨天数/下跌天数）。"""
        r = _ret(series)
        up = (r > 0).astype(float).rolling(window, min_periods=_MINP).sum()
        dn = (r < 0).astype(float).rolling(window, min_periods=_MINP).sum().replace(0.0, np.nan)
        return (up / dn).fillna(1.0)

    @staticmethod
    def ts_new_high_low_ratio(series: pd.Series, window: int = 60) -> pd.Series:
        """新高新低比（创新高天数/创新低天数）。"""
        nh = D12Ops.ts_new_high_ratio(series, window)
        nl = D12Ops.ts_new_low_ratio(series, window).replace(0.0, np.nan)
        return (nh / nl).fillna(1.0)

    @staticmethod
    def ts_breadth_momentum(series: pd.Series, window: int = 20) -> pd.Series:
        """广度动量（市场广度的变化）。"""
        return D17Ops.ts_market_breadth(series, window).diff(5).fillna(0.0)

    @staticmethod
    def ts_breadth_divergence(series: pd.Series, window: int = 20) -> pd.Series:
        """广度背离（价格与广度方向不一致度）。"""
        breadth = D17Ops.ts_market_breadth(series, window)
        p_mom = np.sign(series.diff(5).fillna(0.0))
        b_mom = np.sign(breadth.diff(5).fillna(0.0))
        return (-p_mom * b_mom).rolling(window, min_periods=_MINP).mean().fillna(0.0)

    @staticmethod
    def ts_sector_rotation_score(series: pd.Series, window: int = 20) -> pd.Series:
        """板块轮动得分（动量分散度，轮动活跃度）。"""
        m = series.diff(5).fillna(0.0)
        return m.rolling(window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_concentration_index(series: pd.Series, window: int = 20) -> pd.Series:
        """集中度指数（收益贡献集中度）。"""
        r = _ret(series).abs()
        return D13Ops.cs_herfindahl(r, window)

    @staticmethod
    def ts_diversification_index(series: pd.Series, window: int = 20) -> pd.Series:
        """分散度指数（收益来源多样性）。"""
        r = _ret(series)
        sd = r.rolling(window, min_periods=_MINP).std().fillna(0.0)
        mu = r.rolling(window, min_periods=_MINP).mean().abs().replace(0.0, np.nan)
        return (sd / mu).fillna(0.0)

    @staticmethod
    def ts_correlation_regime(series: pd.Series, window: int = 60) -> pd.Series:
        """相关制度（收益自相关方向，趋势/反转制度）。"""
        r = _ret(series)
        return _corr_clean(r.rolling(window, min_periods=_MINP).corr(r.shift(1)))

    @staticmethod
    def ts_market_dispersion(series: pd.Series, window: int = 20) -> pd.Series:
        """市场离散（收益波动，市场分歧度）。"""
        return _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)

    # ── 截面因子代理 ───────────────────────────────────────

    @staticmethod
    def ts_cross_section_momentum(series: pd.Series, window: int = 20) -> pd.Series:
        """截面动量（滚动收益均值，延续性）。"""
        return _ret(series).rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_cross_section_reversal(series: pd.Series, window: int = 20) -> pd.Series:
        """截面反转（负动量，反转性）。"""
        return -D17Ops.ts_cross_section_momentum(series, window)

    @staticmethod
    def ts_size_premium_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """市值溢价代理（收益-波动调整，小盘溢价 proxy）。"""
        r = _ret(series)
        vol = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (r.rolling(window, min_periods=_MINP).mean() / vol).fillna(0.0)

    @staticmethod
    def ts_value_premium_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """价值溢价代理（价格相对均值折价）。"""
        mu = series.rolling(window, min_periods=_MINP).mean()
        return (-(series / mu.replace(0.0, np.nan) - 1.0)).fillna(0.0)

    @staticmethod
    def ts_momentum_factor_proxy(series: pd.Series, window: int = 60) -> pd.Series:
        """动量因子代理（12-1 动量，标准因子暴露）。"""
        return series.pct_change(window).fillna(0.0)

    @staticmethod
    def ts_low_vol_factor_proxy(series: pd.Series, window: int = 60) -> pd.Series:
        """低波因子代理（负波动率，低波暴露）。"""
        vol = _ret(series).rolling(window, min_periods=_MINP).std().fillna(0.0)
        return (-vol).fillna(0.0)

    @staticmethod
    def ts_quality_factor_proxy(series: pd.Series, window: int = 60) -> pd.Series:
        """质量因子代理（低波动高盈利稳定性的回报代理）。"""
        r = _ret(series)
        mu = r.rolling(window, min_periods=_MINP).mean()
        sd = r.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (mu / sd).fillna(0.0)

    # ── 情绪/风险偏好 ──────────────────────────────────────

    @staticmethod
    def ts_sentiment_score(series: pd.Series, window: int = 20) -> pd.Series:
        """情绪得分（涨跌强度+广度综合）。"""
        strength = D12Ops.ts_up_down_strength(series, window)
        breadth = D17Ops.ts_market_breadth(series, window)
        return ((strength + (breadth - 0.5) * 2.0) / 2.0).fillna(0.0)

    @staticmethod
    def ts_risk_appetite(series: pd.Series, window: int = 20) -> pd.Series:
        """风险偏好（收益/波动，追高风险意愿）。"""
        return D10Ops.ts_sharpe_ratio(series, window)

    @staticmethod
    def ts_fear_greed_index(series: pd.Series, window: int = 20) -> pd.Series:
        """恐惧贪婪指数（0 恐惧 / 1 贪婪）。"""
        z = D12Ops.ts_velocity_zscore(series, window)
        return (np.tanh(z) + 1.0) / 2.0

    @staticmethod
    def ts_momentum_crowding(series: pd.Series, window: int = 20) -> pd.Series:
        """动量拥挤度（动量强度与波动偏离，拥挤预警）。"""
        m = series.diff(5).fillna(0.0)
        vol = _ret(series).rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
        return (m / vol).fillna(0.0)

    @staticmethod
    def ts_position_extreme(series: pd.Series, window: int = 60) -> pd.Series:
        """仓位极端度（价格偏离均值的 zscore 绝对值）。"""
        z = D13Ops.cs_winsor_z(series, window, 10.0)
        return z.abs()

    @staticmethod
    def ts_herding_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """羊群代理（连续同向收益占比，从众度）。"""
        r = _ret(series)
        up = (r > 0).astype(float)
        dn = (r < 0).astype(float)
        best = pd.concat([up, dn], axis=1).max(axis=1)
        return best.rolling(window, min_periods=_MINP).mean()

    # ── 波动率/衍生品代理 ──────────────────────────────────

    @staticmethod
    def ts_implied_vol_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """隐含波动代理（EWMA 波动率平滑，IV proxy，零未来）。"""
        r = _ret(series)
        return r.ewm(span=window, min_periods=_MINP).std().fillna(0.0)

    @staticmethod
    def ts_risk_reversal_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """风险逆转代理（上行波动-下行波动，偏斜方向）。"""
        up = D10Ops.ts_upside_vol(series, window)
        dn = D10Ops.ts_downside_vol(series, window)
        return (up - dn).fillna(0.0)

    @staticmethod
    def ts_smile_proxy(series: pd.Series, window: int = 20) -> pd.Series:
        """波动微笑代理（尾部波动-中心波动，凸性）。"""
        r = _ret(series)
        tail = r.abs().rolling(window, min_periods=_MINP).quantile(0.95)
        center = r.abs().rolling(window, min_periods=_MINP).quantile(0.5)
        return (tail - center).fillna(0.0)

    # ── 制度/择时 ──────────────────────────────────────────

    @staticmethod
    def ts_market_regime_score(series: pd.Series, window: int = 20) -> pd.Series:
        """市场制度得分（趋势+波动综合，-1 熊 / +1 牛）。"""
        trend = D12Ops.ts_trend_strength_pct(series, window)
        vol_regime = (D10Ops.ts_short_term_vol(series, 10) > D10Ops.ts_long_term_vol(series, window)).astype(
            float
        ) * 2.0 - 1.0
        return ((trend + vol_regime) / 2.0).fillna(0.0)

    @staticmethod
    def ts_trend_regime_proxy(series: pd.Series, window: int = 60) -> pd.Series:
        """趋势制度代理（长窗口动量，趋势状态）。"""
        return np.sign(series.diff(window).fillna(0.0))

    @staticmethod
    def ts_volatility_regime_proxy(series: pd.Series, window: int = 60) -> pd.Series:
        """波动制度代理（短/长波动比，高风险状态）。"""
        vs = D10Ops.ts_short_term_vol(series, 10)
        vl = D10Ops.ts_long_term_vol(series, window).replace(0.0, np.nan)
        return (vs / vl).fillna(1.0)

    @staticmethod
    def ts_liquidity_regime_proxy(volume: pd.Series, window: int = 60) -> pd.Series:
        """流动性制度代理（量能趋势，流动性扩张/收缩）。"""
        return np.sign(volume.diff(window).fillna(0.0))

    @staticmethod
    def ts_market_timing_score(series: pd.Series, window: int = 20) -> pd.Series:
        """择时得分（趋势+广度+动量的加权综合）。"""
        trend = D12Ops.ts_trend_strength_pct(series, window)
        breadth = D17Ops.ts_market_breadth(series, window) - 0.5
        mom = np.sign(series.diff(5).fillna(0.0))
        return ((trend + breadth * 2.0 + mom) / 3.0).fillna(0.0)

    @staticmethod
    def ts_regime_confidence(series: pd.Series, window: int = 60) -> pd.Series:
        """制度置信度（趋势 R² 与波动稳定度综合）。"""
        r2 = D12Ops.ts_linear_trend_score(series, window)
        vol_cv = D17Ops.ts_diversification_index(series, window)
        return (r2 * (1.0 / (1.0 + vol_cv))).fillna(0.0)

    @staticmethod
    def ts_regime_persistence(series: pd.Series, window: int = 60) -> pd.Series:
        """制度持续（当前制度已持续期数）。"""
        reg = np.sign(series.diff(20).fillna(0.0))
        grp = (reg != reg.shift(1).fillna(0)).cumsum()
        run = reg.groupby(grp).cumsum().abs()
        return run.rolling(window, min_periods=_MINP).max().fillna(0.0)

    @staticmethod
    def ts_regime_transition_prob(series: pd.Series, window: int = 60) -> pd.Series:
        """制度转移概率（近期制度切换频率，不确定性）。"""
        reg = np.sign(series.diff(20).fillna(0.0))
        switch = reg.diff().abs().fillna(0.0)
        return switch.rolling(window, min_periods=_MINP).mean()

    @staticmethod
    def ts_market_phase(series: pd.Series, window: int = 60) -> pd.Series:
        """市场阶段（扩张/收缩/筑顶/筑底四相位代理）。"""
        trend = np.sign(series.diff(20).fillna(0.0))
        mom = np.sign(series.diff(5).fillna(0.0))
        phase = trend + mom
        return pd.Series(
            np.where(phase == 2, 1.0, np.where(phase == -2, -1.0, phase / 2.0)),
            index=series.index,
        )
