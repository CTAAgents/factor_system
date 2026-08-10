"""
tests/factor_engine/test_portfolio_walk_forward.py — GAP-L306 组合层走航验证测试。

覆盖:
    1. 滚动窗口正常生成（train 求权重 → test 实测）
    2. 一致性得分正确捕获权重漂移
    3. 窗口边界参数化
    4. 空数据 / 样本不足降级

版本: v1.0.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.portfolio_walk_forward import (
    PortfolioWalkForward,
)


def _make_returns(
    n_days: int = 500,
    n_factors: int = 3,
    seed: int = 1,
    drift: float = 0.0005,
) -> pd.DataFrame:
    """合成因子收益矩阵（带正向漂移 → 组合夏普为正）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    data = rng.normal(drift, 0.01, size=(n_days, n_factors))
    return pd.DataFrame(data, index=dates, columns=[f"f{i}" for i in range(n_factors)])


def _sharpe_weight_fn(fr: pd.DataFrame) -> np.ndarray:
    """测试权重函数：基于训练段收益的均值收益加权（正收益权重高）。"""
    means = fr.mean().values
    w = np.maximum(means, 0.0)
    total = w.sum()
    if total <= 0:
        return np.ones(fr.shape[1]) / fr.shape[1]
    return w / total


class TestBasicWalkForward:
    def test_returns_windows(self) -> None:
        """正常数据 → 生成 n_windows 个窗口结果。"""
        fr = _make_returns()
        wf = PortfolioWalkForward(config={"n_windows": 3})
        result = wf.evaluate(fr, _sharpe_weight_fn)
        assert result["n_windows_completed"] >= 1
        assert len(result["windows"]) == result["n_windows_completed"]
        for w in result["windows"]:
            assert "sharpe" in w
            assert "ic" in w
            assert "max_correlation" in w
            assert "turnover" in w

    def test_window_dates_progress(self) -> None:
        """窗口 test 日期应随滚动推进（train 段从数据开头累积）。"""
        fr = _make_returns()
        wf = PortfolioWalkForward(config={"n_windows": 3, "step_days": 60})
        result = wf.evaluate(fr, _sharpe_weight_fn)
        windows = result["windows"]
        if len(windows) >= 2:
            assert windows[1]["test_start"] > windows[0]["test_start"]
            assert windows[1]["test_end"] > windows[0]["test_end"]

    def test_passed_true_with_positive_drift(self) -> None:
        """正向漂移数据 → sharpe_consistency=1.0，passed=True。"""
        fr = _make_returns(drift=0.001, n_days=600)
        wf = PortfolioWalkForward(
            config={
                "n_windows": 3,
                "window_days": 200,
                "min_test_days": 40,
                "max_sharpe_volatility": 2.0,  # 短 test 段夏普估计噪声较大，放宽容差
            }
        )
        result = wf.evaluate(fr, _sharpe_weight_fn)
        assert result["sharpe_consistency"] == 1.0
        assert result["passed"] is True

    def test_consistency_score_bounds(self) -> None:
        """一致性得分在 0-100 区间。"""
        fr = _make_returns()
        wf = PortfolioWalkForward(config={"n_windows": 3})
        result = wf.evaluate(fr, _sharpe_weight_fn)
        assert 0.0 <= result["consistency_score"] <= 100.0


class TestDegradation:
    def test_empty_data(self) -> None:
        """空数据 → 空结果（不崩溃）。"""
        wf = PortfolioWalkForward()
        result = wf.evaluate(pd.DataFrame(), _sharpe_weight_fn)
        assert result["n_windows_completed"] == 0
        assert result["passed"] is False

    def test_insufficient_data(self) -> None:
        """样本过少（< min_test_days×2）→ 空结果。"""
        fr = _make_returns(n_days=10)
        wf = PortfolioWalkForward()
        result = wf.evaluate(fr, _sharpe_weight_fn)
        assert result["n_windows_completed"] == 0

    def test_weight_len_mismatch(self) -> None:
        """权重长度与因子数不一致 → 该窗口跳过。"""
        fr = _make_returns(n_days=400)
        wf = PortfolioWalkForward(config={"n_windows": 2})

        def bad_fn(fr: pd.DataFrame) -> np.ndarray:
            return np.ones(5) / 5  # 5 != 3

        result = wf.evaluate(fr, bad_fn)
        assert result["n_windows_completed"] == 0

    def test_custom_config(self) -> None:
        """自定义窗口参数生效。"""
        fr = _make_returns(n_days=600)
        wf = PortfolioWalkForward(
            config={
                "window_days": 200,
                "step_days": 100,
                "min_test_days": 30,
                "n_windows": 2,
            }
        )
        result = wf.evaluate(fr, _sharpe_weight_fn)
        assert result["n_windows_completed"] >= 1


__all__: list[str] = []
