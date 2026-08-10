"""
fts.factor_engine.portfolio_walk_forward — 组合层走航验证（GAP-L306，C 阶段）。

在因子收益矩阵 R 上做滚动验证：
    每个窗口仅用前段数据求权重（train），后段实测组合表现（test）。

区别于 `walk_forward.py`（因子级单因子评估），本模块在**组合层**验证：
    - 权重在 train 段确定、在 test 段实测（参数冻结纪律）
    - 输出跨窗口组合夏普 / 组合 IC / 最大相关性
    - 一致性得分（跨窗口夏普波动）供 Verifier 参考

用法:
    wf = PortfolioWalkForward()
    result = wf.evaluate(factor_returns, weight_fn)
    print(result["consistency_score"])

版本: v1.0.0
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from typing import Any, Optional, TypedDict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

WeightFn = Callable[[pd.DataFrame], np.ndarray]
"""权重函数: train 因子收益矩阵 (T×N) -> 权重向量 (N,)。"""


# ─── 契约 ───────────────────────────────────────────────────


class PortfolioWalkForwardConfig(TypedDict, total=False):
    """组合层走航验证配置。"""

    window_days: int  # 训练窗口长度（默认 180 交易日）
    step_days: int  # 滚动步长（默认 60）
    min_test_days: int  # 最小样本外长度（默认 20）
    n_windows: int  # 一次运行评估几个窗口（默认 3）
    min_sharpe_consistency: float  # 至少 % 窗口夏普 > 0（默认 0.5）
    max_sharpe_volatility: float  # 跨窗口夏普波动率上限（默认 0.5）
    annualize_factor: int  # 年化因子（默认 252）


class PortfolioWindowResult(TypedDict, total=False):
    """单个窗口的组合走航结果。"""

    train_start: str
    train_end: str
    test_start: str
    test_end: str
    sharpe: float  # 样本外组合夏普（实测 w×R）
    ic: float  # 样本外组合 IC（权重×因子收益截面）
    max_correlation: float  # 组合内最大因子相关性
    turnover: float  # 权重相对上窗口 L1 变化


class PortfolioWalkForwardResult(TypedDict, total=False):
    """组合层走航整体结果。"""

    windows: list[PortfolioWindowResult]
    sharpe_consistency: float  # 夏普 > 0 的窗口占比
    sharpe_volatility: float  # 跨窗口夏普标准差
    consistency_score: float  # 综合评分（0-100）
    passed: bool  # 是否通过验证
    n_windows_completed: int


# ─── 默认配置 ───────────────────────────────────────────────

DEFAULT_PORTFOLIO_WF_CONFIG: PortfolioWalkForwardConfig = PortfolioWalkForwardConfig(
    window_days=180,
    step_days=60,
    min_test_days=20,
    n_windows=3,
    min_sharpe_consistency=0.5,
    max_sharpe_volatility=0.5,
    annualize_factor=252,
)


# ─── PortfolioWalkForward ───────────────────────────────────


class PortfolioWalkForward:
    """组合层走航验证器（GAP-L306）。

    Args:
        config: 走航配置（None 用默认）
    """

    def __init__(self, config: Optional[PortfolioWalkForwardConfig] = None) -> None:
        merged: dict[str, Any] = dict(DEFAULT_PORTFOLIO_WF_CONFIG)
        if config:
            merged.update(config)
        self._config = merged

    def evaluate(
        self,
        factor_returns: pd.DataFrame,
        weight_fn: WeightFn,
    ) -> PortfolioWalkForwardResult:
        """执行组合层走航验证。

        Args:
            factor_returns: 因子收益矩阵（T×N，DatetimeIndex，列名=factor_id）
            weight_fn: 权重函数 train(因子收益矩阵) -> 权重向量（与列序对齐）

        Returns:
            PortfolioWalkForwardResult
        """
        fr = self._prepare(factor_returns)
        if fr is None:
            return self._empty_result()

        windows = self._create_windows(fr)
        results: list[PortfolioWindowResult] = []
        prev_w: Optional[np.ndarray] = None

        for train_df, test_df in windows:
            try:
                w = np.asarray(weight_fn(train_df), dtype=float)
                w = np.nan_to_num(w, nan=0.0)
                w = w / max(float(np.sum(w)), 1e-12)
                if w.shape[0] != fr.shape[1]:
                    raise ValueError(f"权重长度 {w.shape[0]} != 因子数 {fr.shape[1]}")
            except Exception as e:
                logger.warning("[L3-WF] 窗口权重计算失败: %s", e)
                continue

            test_returns = test_df.values @ w
            sharpe = self._sharpe(test_returns)
            ic = self._portfolio_ic(test_df, w)
            max_corr = self._max_corr(test_df)
            turnover = float(np.sum(np.abs(w - prev_w))) if prev_w is not None else 0.0
            results.append(
                PortfolioWindowResult(
                    train_start=_to_date_str(train_df.index[0]),
                    train_end=_to_date_str(train_df.index[-1]),
                    test_start=_to_date_str(test_df.index[0]),
                    test_end=_to_date_str(test_df.index[-1]),
                    sharpe=float(sharpe),
                    ic=float(ic),
                    max_correlation=float(max_corr),
                    turnover=turnover,
                )
            )
            prev_w = w

        n = len(results)
        if n == 0:
            return self._empty_result()

        sharpes = [r["sharpe"] for r in results]
        sharpe_consistency = sum(1 for s in sharpes if s > 0) / n
        sharpe_vol = _safe_stdev(sharpes)
        consistency_score = self._compute_consistency_score(
            sharpe_consistency,
            sharpe_vol,
            sharpes,
        )
        min_consistency = self._config.get("min_sharpe_consistency", 0.5)
        max_vol = self._config.get("max_sharpe_volatility", 0.5)
        passed = sharpe_consistency >= min_consistency and sharpe_vol <= max_vol

        return PortfolioWalkForwardResult(
            windows=results,
            sharpe_consistency=sharpe_consistency,
            sharpe_volatility=sharpe_vol,
            consistency_score=consistency_score,
            passed=passed,
            n_windows_completed=n,
        )

    # ─── 内部方法 ────────────────────────────────────────

    def _prepare(self, factor_returns: pd.DataFrame) -> Optional[pd.DataFrame]:
        """数据预处理：按时间排序、去空行/空列。"""
        if factor_returns is None or len(factor_returns) == 0:
            return None
        fr = factor_returns.copy()
        if not isinstance(fr.index, pd.DatetimeIndex):
            logger.warning("[L3-WF] 索引非 DatetimeIndex，尝试解析")
            try:
                fr.index = pd.to_datetime(fr.index)
            except Exception as e:
                logger.warning("[L3-WF] 索引解析失败: %s", e)
                return None
        fr = fr.sort_index().dropna(how="all")
        fr = fr.dropna(axis=1, how="all")
        if len(fr) < self._config.get("min_test_days", 20) * 2:
            return None
        return fr

    def _create_windows(
        self,
        fr: pd.DataFrame,
    ) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
        """创建 (train, test) 窗口对（滚动步长推进）。"""
        window_days = int(self._config.get("window_days", 180))
        step_days = int(self._config.get("step_days", 60))
        min_test_days = int(self._config.get("min_test_days", 20))
        n_windows = int(self._config.get("n_windows", 3))

        total = len(fr)
        windows: list[tuple[pd.DataFrame, pd.DataFrame]] = []
        # 用交易日个数近似窗口（日频数据）
        for i in range(n_windows):
            train_end = min(window_days + step_days * i, total - min_test_days)
            test_end = min(train_end + min_test_days, total)
            if train_end < window_days or test_end - train_end < min_test_days:
                break
            windows.append((fr.iloc[:train_end], fr.iloc[train_end:test_end]))
        return windows

    @staticmethod
    def _sharpe(returns: np.ndarray) -> float:
        """年化夏普（样本标准差 ddof=1）。"""
        if len(returns) < 2:
            return 0.0
        std = float(np.std(returns, ddof=1))
        if std < 1e-12:
            return 0.0
        return float(np.mean(returns)) / std * np.sqrt(252.0)

    @staticmethod
    def _portfolio_ic(test_df: pd.DataFrame, w: np.ndarray) -> float:
        """组合 IC：各期组合收益与加权因子信号秩相关均值。

        简化：用权重与因子收益的截面相关（每期），取均值。
        """
        if len(test_df) < 2:
            return 0.0
        ics = []
        for _, row in test_df.iterrows():
            x = row.values
            if np.std(x) < 1e-12 or np.std(w) < 1e-12:
                continue
            r = np.corrcoef(x, w)[0, 1]
            if np.isfinite(r):
                ics.append(r)
        return float(np.mean(ics)) if ics else 0.0

    @staticmethod
    def _max_corr(test_df: pd.DataFrame) -> float:
        """窗口内因子收益矩阵最大绝对相关性。"""
        if test_df.shape[1] < 2:
            return 0.0
        corr = test_df.corr().abs()
        np.fill_diagonal(corr.values, 0.0)
        return float(corr.values.max()) if len(corr) > 0 else 0.0

    def _compute_consistency_score(
        self,
        sharpe_consistency: float,
        sharpe_volatility: float,
        sharpes: list[float],
    ) -> float:
        """综合评分（0-100）：一致性 40% + 波动 30% + 均值强度 30%。"""
        max_vol = self._config.get("max_sharpe_volatility", 0.5)
        consistency_part = sharpe_consistency * 100 * 0.40
        volatility_part = max(0.0, 1.0 - sharpe_volatility / max(max_vol, 1e-6)) * 100 * 0.30
        mean_sharpe = statistics.mean(sharpes) if sharpes else 0.0
        strength_part = min(1.0, max(mean_sharpe, 0.0) / 1.0) * 100 * 0.30
        return round(consistency_part + volatility_part + strength_part, 2)

    def _empty_result(self) -> PortfolioWalkForwardResult:
        return PortfolioWalkForwardResult(
            windows=[],
            sharpe_consistency=0.0,
            sharpe_volatility=0.0,
            consistency_score=0.0,
            passed=False,
            n_windows_completed=0,
        )


# ─── 内部工具 ───────────────────────────────────────────────


def _safe_stdev(values: list[float]) -> float:
    """安全计算标准差（处理单元素列表）。"""
    if len(values) <= 1:
        return 0.0
    return statistics.stdev(values)


def _to_date_str(value: pd.Timestamp) -> str:
    """Timestamp → ISO 日期字符串。"""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


__all__ = [
    "PortfolioWalkForwardConfig",
    "PortfolioWalkForwardResult",
    "PortfolioWindowResult",
    "PortfolioWalkForward",
    "DEFAULT_PORTFOLIO_WF_CONFIG",
]
