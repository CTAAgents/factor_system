"""
fts.factor_engine.walk_forward — 走航验证（多窗口样本外评估）。

替代固定 30% 尾部切片，使用滚动多窗口验证因子稳定性。

用法:
    optimizer = WalkForwardOptimizer()
    result = optimizer.evaluate(data, evaluate_fn)
    print(result["consistency_score"])

版本: v0.1.0
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from typing import TypedDict

import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────


class WalkForwardConfig(TypedDict, total=False):
    """走航验证配置。"""
    window_years: int                   # 训练窗口长度（默认 3）
    step_months: int                    # 滚动步长（默认 6）
    min_oos_months: int                 # 最小样本外长度（默认 3）
    n_windows: int                      # 一次运行评估几个窗口（默认 4）
    min_ic_consistency: float           # 至少 % 窗口 IC > 0（默认 0.5）
    max_ic_volatility: float            # IC 跨窗口波动率上限（默认 0.3）


class WalkForwardWindowResult(TypedDict, total=False):
    """单个窗口的走航验证结果。"""
    train_start: str                    # 训练起始日期
    train_end: str                      # 训练结束日期
    oos_start: str                      # 样本外起始日期
    oos_end: str                        # 样本外结束日期
    ic: float                           # 样本外 IC
    sharpe: float                       # 样本外夏普
    turnover: float                     # 样本外换手率


class WalkForwardResult(TypedDict, total=False):
    """走航验证整体结果。"""
    windows: list[WalkForwardWindowResult]   # 各窗口详细结果
    ic_consistency: float                    # IC > 0 的窗口占比
    ic_volatility: float                     # 跨窗口 IC 标准差
    sharpe_volatility: float                 # 跨窗口夏普标准差
    consistency_score: float                 # 综合评分（0-100）
    passed: bool                             # 是否通过验证
    n_windows_completed: int                 # 实际完成的窗口数


# ─── 默认配置 ───────────────────────────────────────────────

DEFAULT_WALK_FORWARD_CONFIG: WalkForwardConfig = WalkForwardConfig(
    window_years=3,
    step_months=6,
    min_oos_months=3,
    n_windows=4,
    min_ic_consistency=0.5,
    max_ic_volatility=0.3,
)
"""v0.1.0 默认走航验证配置。"""


# ─── WalkForwardOptimizer ───────────────────────────────────


class WalkForwardOptimizer:
    """走航验证优化器。

    通过滚动多窗口样本外评估，验证因子在不同时间段的稳定性。
    综合 IC 一致性、波动率等指标，输出综合评分与通过/不通过判定。

    Args:
        config: 走航验证配置（使用默认值若为 None）
    """

    def __init__(self, config: WalkForwardConfig | None = None) -> None:
        merged = dict(DEFAULT_WALK_FORWARD_CONFIG)
        if config:
            merged.update(config)
        self._config = merged

    def evaluate(
        self,
        data: pd.DataFrame,
        evaluate_fn: Callable[[pd.DataFrame, pd.DataFrame], dict],
    ) -> WalkForwardResult:
        """执行走航验证评估。

        Args:
            data: 完整数据集（须包含日期索引或 ``date`` 列）
            evaluate_fn: 评估函数，签名 ``(train_df, oos_df) -> dict``
                         返回字典须包含 ``ic``、``sharpe``、``turnover`` 键

        Returns:
            WalkForwardResult 综合评估结果
        """
        windows = self._create_windows(data)
        window_results: list[WalkForwardWindowResult] = []

        for train_df, oos_df in windows:
            try:
                metrics = evaluate_fn(train_df, oos_df)
                window_results.append(WalkForwardWindowResult(
                    train_start=_to_date_str(train_df.index[0] if hasattr(train_df.index, 'dtype') else train_df.iloc[0].name),
                    train_end=_to_date_str(train_df.index[-1] if hasattr(train_df.index, 'dtype') else train_df.iloc[-1].name),
                    oos_start=_to_date_str(oos_df.index[0] if hasattr(oos_df.index, 'dtype') else oos_df.iloc[0].name),
                    oos_end=_to_date_str(oos_df.index[-1] if hasattr(oos_df.index, 'dtype') else oos_df.iloc[-1].name),
                    ic=metrics.get("ic", 0.0),
                    sharpe=metrics.get("sharpe", 0.0),
                    turnover=metrics.get("turnover", 0.0),
                ))
            except Exception as e:
                logger.warning("窗口评估失败: %s", e)
                continue

        n_completed = len(window_results)
        if n_completed == 0:
            return WalkForwardResult(
                windows=[],
                ic_consistency=0.0,
                ic_volatility=0.0,
                sharpe_volatility=0.0,
                consistency_score=0.0,
                passed=False,
                n_windows_completed=0,
            )

        # IC 一致性：IC > 0 的窗口占比
        ic_values = [w["ic"] for w in window_results]
        ic_consistency = sum(1 for ic in ic_values if ic > 0) / n_completed

        # IC / 夏普 跨窗口波动率
        ic_volatility = _safe_stdev(ic_values)
        sharpe_values = [w["sharpe"] for w in window_results]
        sharpe_volatility = _safe_stdev(sharpe_values)

        # 综合评分（0-100）
        # 权重：一致性 40%，波动率 30%，均值强度 30%
        ic_mean = statistics.mean(ic_values) if ic_values else 0.0
        consistency_score = self._compute_consistency_score(
            ic_consistency=ic_consistency,
            ic_volatility=ic_volatility,
            ic_mean=ic_mean,
        )

        min_ic_consistency = self._config.get("min_ic_consistency", 0.5)
        max_ic_volatility = self._config.get("max_ic_volatility", 0.3)
        passed = ic_consistency >= min_ic_consistency and ic_volatility <= max_ic_volatility

        return WalkForwardResult(
            windows=window_results,
            ic_consistency=ic_consistency,
            ic_volatility=ic_volatility,
            sharpe_volatility=sharpe_volatility,
            consistency_score=consistency_score,
            passed=passed,
            n_windows_completed=n_completed,
        )

    # ─── 窗口创建 ────────────────────────────────────────

    def _create_windows(self, data: pd.DataFrame) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        """创建走航验证窗口分割。

        根据配置的窗口长度和步长，将数据分割为多个 (train, oos) 窗口对。

        Args:
            data: 完整数据集

        Returns:
            窗口对列表，每项为 (train_df, oos_df)
        """
        # 确保数据按时间排序
        df = data.sort_index() if isinstance(data.index, pd.DatetimeIndex) else data.sort_values("date")
        if df.empty:
            return []

        dates = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df["date"])
        total_days = (dates[-1] - dates[0]).days
        window_days = int(self._config.get("window_years", 3) * 365.25)
        step_days = int(self._config.get("step_months", 6) * 30.44)
        min_oos_days = int(self._config.get("min_oos_months", 3) * 30.44)
        n_windows = self._config.get("n_windows", 4)

        windows: list[tuple[pd.DataFrame, pd.DataFrame]] = []

        for i in range(n_windows):
            train_end_offset = step_days * i
            train_end_idx = window_days + train_end_offset

            if train_end_idx >= len(df):
                break

            oos_start_idx = train_end_idx
            oos_end_idx = oos_start_idx + max(min_oos_days, step_days)

            if oos_end_idx > len(df):
                oos_end_idx = len(df)

            if oos_end_idx - oos_start_idx < min_oos_days:
                break

            train_df = df.iloc[:train_end_idx]
            oos_df = df.iloc[oos_start_idx:oos_end_idx]

            windows.append((train_df, oos_df))

        return windows

    # ─── 评分 ────────────────────────────────────────────

    def _compute_consistency_score(
        self,
        ic_consistency: float,
        ic_volatility: float,
        ic_mean: float,
    ) -> float:
        """计算综合评分（0-100）。

        评分公式：
        - 一致性贡献（40%）：ic_consistency * 100 * 0.4
        - 波动率贡献（30%）：max(0, 1 - ic_volatility / max_ic_volatility) * 100 * 0.3
        - 均值强度贡献（30%）：min(1, ic_mean / 0.1) * 100 * 0.3

        Args:
            ic_consistency: IC 一致性比率（0-1）
            ic_volatility: IC 跨窗口波动率
            ic_mean: IC 均值

        Returns:
            综合评分（0-100）
        """
        max_ic_volatility = self._config.get("max_ic_volatility", 0.3)

        consistency_part = ic_consistency * 100 * 0.40
        volatility_part = max(0.0, 1.0 - ic_volatility / max(max_ic_volatility, 1e-6)) * 100 * 0.30
        strength_part = min(1.0, ic_mean / 0.10) * 100 * 0.30

        return round(consistency_part + volatility_part + strength_part, 2)


# ─── 内部工具 ───────────────────────────────────────────────


def _safe_stdev(values: list[float]) -> float:
    """安全计算标准差（处理单元素列表）。"""
    if len(values) <= 1:
        return 0.0
    return statistics.stdev(values)


def _to_date_str(value: pd.Timestamp | object) -> str:
    """将 Timestamp 转为 ISO 日期字符串。"""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


__all__ = [
    "WalkForwardConfig",
    "WalkForwardWindowResult",
    "WalkForwardResult",
    "WalkForwardOptimizer",
    "DEFAULT_WALK_FORWARD_CONFIG",
]
