"""
fts.factor_engine.regime — 市场制度感知与因子选择性激活。

检测当前市场制度（bull/bear/震荡/高波/低波），
记录因子在各 regime 下的历史表现，
仅选择在当前制度下有效的因子参与组合构建。

用法:
    selector = RegimeAwareSelector()
    regime = selector.detect(ohlcv_df)
    active_factors = selector.select_factors(regime, elite_factors)

版本: v0.1.0
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

import numpy as np
import pandas as pd


# ─── 契约 ─────────────────────────────────────────────────

class MarketRegime(TypedDict):
    """市场制度检测结果。"""
    regime: str                # bull / bear / oscillate / high_vol / low_vol
    confidence: float          # 置信度 0~1
    detected_at: str           # ISO 8601
    features: dict             # 检测特征（trend_strength, volatility, volume_ratio, breadth）


class RegimePerformance(TypedDict, total=False):
    """因子在某 regime 下的历史表现。"""
    ic_mean: float
    sharpe: float
    n_windows: int


class RegimeFactorProfile(TypedDict, total=False):
    """因子在各 regime 下的表现记录。"""
    factor_id: str
    regime_performance: dict[str, RegimePerformance]  # regime -> performance


# ─── 默认阈值 ─────────────────────────────────────────────

_TREND_THRESHOLD = 0.02       # MA20 斜率超过 ±2% → 明确趋势
_HIGH_VOL_THRESHOLD = 0.03    # ATR/价格 > 3% → 高波
_LOW_VOL_THRESHOLD = 0.01     # ATR/价格 < 1% → 低波


# ─── RegimeAwareSelector ─────────────────────────────────

class RegimeAwareSelector:
    """市场制度感知的选择器。

    参数:
        lookback_days: 趋势斜率计算的回看天数（默认 60）。
    """

    def __init__(self, lookback_days: int = 60) -> None:
        self.lookback_days = lookback_days
        self._profiles: dict[str, RegimeFactorProfile] = {}

    # ── 检测 ──────────────────────────────────────────────

    def detect(self, ohlcv: pd.DataFrame) -> MarketRegime:
        """从 OHLCV 数据检测当前市场制度。

        检测逻辑（分层）:
            1. 趋势优先：MA20 斜率 > +2% → bull；< -2% → bear
            2. 波动率次之：ATR/价格 > 3% → high_vol；< 1% → low_vol
            3. 兜底：oscillate（无明显趋势且波动适中）

        参数:
            ohlcv: 含 open/high/low/close/volume 列的 DataFrame，DatetimeIndex。

        返回:
            MarketRegime — 制度名、置信度、检测时间、特征字典。
        """
        # ── 空/不足 20 行 → 兜底 ─────────────────────────
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
            return MarketRegime(
                regime="oscillate",
                confidence=0.0,
                detected_at=datetime.now().isoformat(),
                features={},
            )

        close = ohlcv["close"].dropna()
        if len(close) < 20:
            return MarketRegime(
                regime="oscillate",
                confidence=0.0,
                detected_at=datetime.now().isoformat(),
                features={},
            )

        # ── MA20 斜率（趋势强度） ────────────────────────
        ma20 = close.rolling(20).mean()
        lookback = min(self.lookback_days, max(len(ma20) - 1, 1))
        ma20_start = ma20.iloc[-lookback]
        ma20_end = ma20.iloc[-1]
        ma20_slope = (
            (ma20_end - ma20_start) / abs(ma20_start)
            if abs(ma20_start) > 1e-12
            else 0.0
        )

        # ── ATR/价格（波动率） ────────────────────────────
        high = ohlcv["high"].ffill()
        low = ohlcv["low"].ffill()
        close_filled = close.ffill()

        tr = pd.concat(
            [
                high - low,
                (high - close_filled.shift(1)).abs(),
                (low - close_filled.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        last_close = float(close_filled.iloc[-1])
        atr_ratio = atr / last_close if abs(last_close) > 1e-12 else 0.0

        # ── 成交量比率 ────────────────────────────────────
        volume = ohlcv["volume"].fillna(0)
        vol_ma = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / vol_ma if vol_ma > 1e-12 else 1.0

        # ── 广度（收益率自相关，近似品种内相关性） ────────
        rets = close_filled.pct_change().dropna()
        breadth = float(rets.autocorr()) if len(rets) > 2 else 0.0

        features: dict = {
            "trend_strength": round(float(ma20_slope), 6),
            "volatility": round(atr_ratio, 6),
            "volume_ratio": round(vol_ratio, 4),
            "breadth": round(breadth, 4),
        }

        # ── 分层判定 ──────────────────────────────────────
        if ma20_slope > _TREND_THRESHOLD:
            regime = "bull"
            confidence = min(1.0, abs(ma20_slope) / (_TREND_THRESHOLD * 2))
        elif ma20_slope < -_TREND_THRESHOLD:
            regime = "bear"
            confidence = min(1.0, abs(ma20_slope) / (_TREND_THRESHOLD * 2))
        elif atr_ratio > _HIGH_VOL_THRESHOLD:
            regime = "high_vol"
            confidence = min(1.0, atr_ratio / (_HIGH_VOL_THRESHOLD * 2))
        elif atr_ratio < _LOW_VOL_THRESHOLD:
            regime = "low_vol"
            confidence = min(1.0, 1.0 - atr_ratio / _LOW_VOL_THRESHOLD)
        else:
            regime = "oscillate"
            confidence = 0.5

        return MarketRegime(
            regime=regime,
            confidence=round(float(confidence), 4),
            detected_at=datetime.now().isoformat(),
            features=features,
        )

    # ── 因子表现记录 ──────────────────────────────────────

    def profile_factor(
        self,
        factor_id: str,
        history: dict[str, RegimePerformance],
    ) -> None:
        """记录因子在各制度下的历史表现。

        参数:
            factor_id: 因子唯一标识。
            history:   regime 名称 → 表现指标（ic_mean, sharpe, n_windows）。
        """
        self._profiles[factor_id] = RegimeFactorProfile(
            factor_id=factor_id,
            regime_performance=history,
        )

    # ── 因子选择 ──────────────────────────────────────────

    def select_factors(
        self,
        regime: MarketRegime,
        elite_pool: list[dict],
    ) -> list[dict]:
        """根据当前制度筛选精英因子。

        选择逻辑:
            - 有 profile 数据的因子：IC_mean > 0 或 sharpe > 0 才保留
            - 无 profile 数据的因子：默认保留（中性权重）
            - 空 elite_pool：返回空列表

        参数:
            regime:     detect() 返回的当前制度。
            elite_pool: 精英因子列表，每项至少含 factor_id 键。

        返回:
            筛选后的因子列表。
        """
        current_regime = regime["regime"]
        result: list[dict] = []

        for factor in elite_pool:
            fid = factor.get("factor_id", "")
            profile = self._profiles.get(fid)
            if profile is not None:
                perf = profile.get("regime_performance", {}).get(current_regime)
                if perf is not None:
                    ic_mean = perf.get("ic_mean", 0.0)
                    sharpe = perf.get("sharpe", 0.0)
                    if ic_mean > 0 or sharpe > 0:
                        result.append(factor)
                    # 若 IC 和夏普均 ≤ 0，跳过（不加入结果）
                else:
                    # 该 regime 下无表现记录 → 保留
                    result.append(factor)
            else:
                # 无 profile → 保留
                result.append(factor)

        return result

    # ── 报告 ──────────────────────────────────────────────

    def regime_report(self) -> str:
        """生成当前制度与各因子表现的人类可读报告。

        返回:
            多行字符串报告。
        """
        lines: list[str] = [
            "=== RegimeAwareSelector 报告 ===",
            f"  已记录的因子数: {len(self._profiles)}",
        ]

        for fid, profile in self._profiles.items():
            perfs = profile.get("regime_performance", {})
            lines.append(f"  因子 [{fid}]:")
            for regime_name, perf in perfs.items():
                ic = perf.get("ic_mean", float("nan"))
                sp = perf.get("sharpe", float("nan"))
                nw = perf.get("n_windows", 0)
                lines.append(
                    f"    {regime_name}: IC={ic:.4f}, Sharpe={sp:.4f}, windows={nw}"
                )

        if not self._profiles:
            lines.append("  （无因子表现数据）")

        return "\n".join(lines)
