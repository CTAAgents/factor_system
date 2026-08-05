"""
fts.factor_engine.signal_generator — 因子信号生成器（B.2 Stage 2）。

为筛选出的因子生成横截面/时序信号：
    - 时序信号（time_series）: 因子值 → 滚动标准化 → 方向信号
    - 横截面信号（cross_section）: 多标的因子值截面排名 → 多空信号
      （每期 top 20% 做多 +1，bottom 20% 做空 -1，其余 0）

因子值计算复用 ``BacktestPipeline._execute_factor_code``（与演化循环同源）。

用法:
    from fts.factor_engine.signal_generator import SignalGenerator

    gen = SignalGenerator()
    signal = gen.generate(factor=factor, data=df, signal_type="time_series")
    cross = gen.generate_cross_section(factor=factor, panel=panel)  # {symbol: Series}

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalGenerator:
    """因子信号生成器（B.2 Stage 2）。"""

    def __init__(self, forward_period: int = 1) -> None:
        """初始化信号生成器。

        Args:
            forward_period: 预测周期（天，影响因子收益率对齐，默认 1）
        """
        self._forward_period = int(forward_period)

    # ─── 时序信号 ────────────────────────────────────────

    def generate(
        self,
        factor: dict[str, Any],
        data: pd.DataFrame,
        signal_type: str = "time_series",
    ) -> pd.Series:
        """为单个因子在单标的数据上生成时序信号。

        Args:
            factor: 因子字典（含 code/factor_id/params）
            data: OHLCV DataFrame
            signal_type: 信号类型（仅 "time_series" 用于单标的数据）

        Returns:
            信号 Series（-1 ~ +1），索引与 data 对齐。
        """
        values = self._compute_factor_values(factor, data)
        if values is None or len(values) == 0:
            return pd.Series(dtype=float)
        return self._time_series_signal(values, _index_of(data))

    # ─── 横截面信号 ──────────────────────────────────────

    def generate_cross_section(
        self,
        factor: dict[str, Any],
        panel: dict[str, pd.DataFrame],
        quantile: float = 0.2,
    ) -> dict[str, pd.Series]:
        """为单个因子在面板数据上生成横截面多空信号。

        Args:
            factor: 因子字典（含 code/factor_id/params）
            panel: symbol → OHLCV DataFrame
            quantile: 多空分位（默认 0.2 = top/bottom 20%）

        Returns:
            symbol → 截面信号 Series（+1 做多 / -1 做空 / 0 中性）。
        """
        # 1. 逐标的计算因子值，统一到共同日期索引
        factor_values: dict[str, pd.Series] = {}
        for sym, df in panel.items():
            values = self._compute_factor_values(factor, df)
            if values is None or len(values) == 0:
                continue
            factor_values[sym] = pd.Series(values, index=_index_of(df))

        if not factor_values:
            return {}

        # 2. 宽表：行 = 日期，列 = 标的
        wide = pd.DataFrame(factor_values).dropna(how="all")

        # 3. 每期截面排名 → 多空信号
        signals: dict[str, pd.Series] = {}
        for sym in wide.columns:
            series = wide[sym]
            signals[sym] = pd.Series(0.0, index=wide.index, name=sym)
        for t, row in wide.iterrows():
            valid = row.dropna()
            if len(valid) < 5:
                continue
            q_low = valid.quantile(quantile)
            q_high = valid.quantile(1 - quantile)
            long_mask = row >= q_high
            short_mask = row <= q_low
            for sym in wide.columns:
                if pd.isna(row[sym]):
                    continue
                if long_mask[sym]:
                    signals[sym].loc[t] = 1.0
                elif short_mask[sym]:
                    signals[sym].loc[t] = -1.0
        return signals

    # ─── 内部方法 ────────────────────────────────────────

    @staticmethod
    def _compute_factor_values(
        factor: dict[str, Any], data: pd.DataFrame
    ) -> np.ndarray | None:
        """计算因子值（复用 BacktestPipeline 沙箱执行器）。"""
        code = factor.get("code", "")
        if not code:
            logger.warning("[SignalGenerator] 因子 %s 无 code，跳过", factor.get("factor_id"))
            return None
        from .backtest_pipeline import BacktestPipeline

        return BacktestPipeline._execute_factor_code(
            code, data, factor.get("params") or {}
        )

    @staticmethod
    def _time_series_signal(
        values: np.ndarray, index: pd.DatetimeIndex
    ) -> pd.Series:
        """时序信号：滚动 20 日 z-score → tanh 压缩到 [-1, 1]。"""
        values = np.asarray(values, dtype=float)
        n = len(values)
        window = 20
        z = np.zeros(n)
        for i in range(window, n):
            hist = values[max(0, i - window):i]
            std = np.std(hist)
            if std > 1e-8:
                z[i] = (values[i] - np.mean(hist)) / std
        signal = np.tanh(z * 0.5)
        return pd.Series(signal, index=index, name="signal")


def _index_of(data: pd.DataFrame) -> pd.DatetimeIndex:
    """获取数据索引（date 列存在时优先）。"""
    if "date" in data.columns:
        return pd.DatetimeIndex(data["date"])
    return pd.DatetimeIndex(data.index)


__all__ = ["SignalGenerator"]
