"""
tests/scripts/test_signal_common.py — 公共信号模块测试（GAP-S04）

覆盖范围:
    - compute_factor_sign_flips: 方向校正（截面 IC 法）
    - compute_ridge_weights: Ridge 回归权重学习
    - compute_composite_scores: 加权合成

版本: v1.0.0 (GAP-S04)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保能导入 scripts._signal_common
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts._signal_common import (
    compute_composite_scores,
    compute_factor_sign_flips,
    compute_ridge_weights,
)


# ─── 辅助函数 ─────────────────────────────────────────────


def _make_panel(
    n_symbols: int = 3,
    n_dates: int = 60,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """创建合成行情面板。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_dates, freq="B")
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]

    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        sym = f"SYM{i:04d}"
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_dates))
        df = pd.DataFrame(
            {"close": close},
            index=dates,
        )
        panel[sym] = df

    return panel, date_strs


def _make_signal_matrix(
    panel: dict[str, pd.DataFrame],
    n_factors: int = 3,
    n_dates: int = 60,
    seed: int = 42,
) -> dict[str, dict[str, np.ndarray]]:
    """创建合成信号矩阵。"""
    rng = np.random.default_rng(seed)
    factor_names = [f"factor_{i}" for i in range(n_factors)]

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    for sym, df in panel.items():
        n = min(len(df), n_dates)
        sym_signals: dict[str, np.ndarray] = {}
        for i, fname in enumerate(factor_names):
            arr = rng.normal(0, 1, n)
            # 使因子 0 与未来收益正相关，因子 1 负相关
            if i == 0:
                arr += np.linspace(0, 0.5, n)
            elif i == 1:
                arr -= np.linspace(0, 0.5, n)
            sym_signals[fname] = arr
        signal_matrix[sym] = sym_signals

    return signal_matrix


def _make_factors(n_factors: int = 3) -> list[dict[str, str]]:
    return [{"name": f"factor_{i}"} for i in range(n_factors)]


# ─── 方向校正测试 ─────────────────────────────────────────


class TestComputeFactorSignFlips:
    """测试方向校正（截面 IC 法）。"""

    def test_basic_flip_detection(self) -> None:
        """因子方向校正检测：构造强相关信号。"""
        rng = np.random.default_rng(42)
        n_dates = 80
        n_symbols = 10
        dates = [d.strftime("%Y-%m-%d") for d in
                 pd.date_range("2025-01-01", periods=n_dates, freq="B")]

        # 构造未来收益与信号强相关的面板
        panel: dict[str, pd.DataFrame] = {}
        for i in range(n_symbols):
            close = 100.0 + np.cumsum(rng.normal(0, 0.3, n_dates))
            panel[f"SYM{i:04d}"] = pd.DataFrame(
                {"close": close},
                index=pd.date_range("2025-01-01", periods=n_dates, freq="B"),
            )

        # 构造信号：factor_0 与未来收益正相关，factor_1 负相关
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for sym in panel:
            n = n_dates
            # 使用未来收益率本身作为信号基底
            future_rets = np.zeros(n)
            for t in range(n - 5):
                future_rets[t] = (panel[sym]["close"].iloc[t + 5] - panel[sym]["close"].iloc[t]) / panel[sym]["close"].iloc[t]

            # factor_0 = future_rets + 噪声 → 强正相关
            sig0 = future_rets + rng.normal(0, 0.01, n)
            # factor_1 = -future_rets + 噪声 → 强负相关
            sig1 = -future_rets + rng.normal(0, 0.01, n)

            signal_matrix[sym] = {
                "factor_0": sig0,
                "factor_1": sig1,
            }

        flips = compute_factor_sign_flips(
            signal_matrix, panel, dates, ic_lookback=20,
        )

        # factor_0: 正相关 → 预期 +1.0
        assert flips.get("factor_0", 0) > 0, "factor_0 应正相关"
        # factor_1: 负相关 → 预期 -1.0
        assert flips.get("factor_1", 0) < 0, "factor_1 应负相关"

    def test_insufficient_data(self) -> None:
        """数据不足（<5 共同标的）时全部返回 +1.0。"""
        panel, dates = _make_panel(n_symbols=2, n_dates=20)
        signal_matrix = _make_signal_matrix(panel, n_factors=2, n_dates=20)

        flips = compute_factor_sign_flips(
            signal_matrix, panel, dates, ic_lookback=20,
        )

        for fname, flip in flips.items():
            assert flip == 1.0, f"{fname} 应默认为 +1.0"

    def test_empty_panel(self) -> None:
        """空面板优雅降级。"""
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        flips = compute_factor_sign_flips(signal_matrix, {}, [], ic_lookback=20)

        assert flips == {}

    def test_all_positive_ic(self) -> None:
        """所有因子 IC>0 时全部 +1.0。"""
        panel, dates = _make_panel(n_symbols=5, n_dates=60)
        # 构造全是正相关的信号
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for sym, df in panel.items():
            n = len(df)
            signal_matrix[sym] = {
                "pos_factor": np.linspace(0, 0.5, n),
                "pos_factor2": np.linspace(0.1, 0.6, n),
            }

        flips = compute_factor_sign_flips(
            signal_matrix, panel, dates, ic_lookback=20,
        )
        for v in flips.values():
            assert v == 1.0


# ─── Ridge 权重学习测试 ───────────────────────────────────


class TestComputeRidgeWeights:
    """测试 Ridge 回归权重学习。"""

    def test_basic_weight_learning(self) -> None:
        """Ridge 学习应返回归一化权重。"""
        panel, dates = _make_panel(n_symbols=5, n_dates=60)
        signal_matrix = _make_signal_matrix(panel, n_factors=3, n_dates=60)
        flips = {"factor_0": 1.0, "factor_1": 1.0, "factor_2": 1.0}

        weights = compute_ridge_weights(
            signal_matrix, panel, dates, flips, lookback=30,
        )

        assert len(weights) == 3
        # 权重和为 1
        assert abs(sum(weights.values()) - 1.0) < 1e-6
        # 所有权重 >= 0
        assert all(w >= 0 for w in weights.values())

    def test_single_factor(self) -> None:
        """单因子应返回权重 1.0。"""
        panel, dates = _make_panel(n_symbols=3, n_dates=30)
        signal_matrix = _make_signal_matrix(panel, n_factors=1, n_dates=30)
        flips = {"factor_0": 1.0}

        weights = compute_ridge_weights(
            signal_matrix, panel, dates, flips, lookback=20,
        )

        assert abs(weights.get("factor_0", 0) - 1.0) < 1e-6

    def test_insufficient_samples_fallback(self) -> None:
        """样本不足时回退到等权。"""
        panel, dates = _make_panel(n_symbols=2, n_dates=10)
        signal_matrix = _make_signal_matrix(panel, n_factors=2, n_dates=10)
        flips = {"factor_0": 1.0, "factor_1": 1.0}

        weights = compute_ridge_weights(
            signal_matrix, panel, dates, flips, lookback=5,
        )

        # 应回到等权
        assert len(weights) == 2
        for w in weights.values():
            assert abs(w - 0.5) < 1e-6

    def test_high_corr_penalty(self) -> None:
        """高相关因子对被惩罚。"""
        rng = np.random.default_rng(42)
        n = 40
        base = np.cumsum(rng.normal(0, 0.5, n))
        panel: dict[str, pd.DataFrame] = {}
        for i in range(5):
            panel[f"SYM{i:04d}"] = pd.DataFrame(
                {"close": 100.0 + base + rng.normal(0, 0.1, n)},
                index=pd.date_range("2025-01-01", periods=n, freq="B"),
            )
        dates = [d.strftime("%Y-%m-%d") for d in pd.date_range("2025-01-01", periods=n, freq="B")]

        # 构造两个高度相关的因子
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for sym in panel:
            sig = rng.normal(0, 1, n)
            signal_matrix[sym] = {
                "factor_a": sig.copy(),
                "factor_b": sig.copy() + rng.normal(0, 0.01, n),  # ~0.99 correlated
                "factor_c": rng.normal(0, 1, n),
            }

        flips = {"factor_a": 1.0, "factor_b": 1.0, "factor_c": 1.0}

        weights = compute_ridge_weights(
            signal_matrix, panel, dates, flips, lookback=20,
            corr_penalty_lambda=0.5, extreme_threshold=0.90,
        )

        # 极端相关因子 factor_b 被硬删除，保留 2 个因子
        assert len(weights) == 2, f"预期 2 个因子，实际 {len(weights)}"
        # 权重和为 1
        assert abs(sum(weights.values()) - 1.0) < 1e-6


# ─── 加权合成测试 ─────────────────────────────────────────


class TestComputeCompositeScores:
    """测试加权合成。"""

    def test_equal_weight(self) -> None:
        """等权合成。"""
        panel, _ = _make_panel(n_symbols=2, n_dates=30)
        signal_matrix = _make_signal_matrix(panel, n_factors=2, n_dates=30)
        flips = {"factor_0": 1.0, "factor_1": -1.0}
        factors = _make_factors(2)

        scores, details = compute_composite_scores(
            signal_matrix, flips, factors, factor_weights=None,
        )

        assert len(scores) == 2
        for sym in signal_matrix:
            assert sym in scores
            assert sym in details

    def test_ridge_weight(self) -> None:
        """Ridge 权重合成。"""
        panel, _ = _make_panel(n_symbols=2, n_dates=30)
        signal_matrix = _make_signal_matrix(panel, n_factors=2, n_dates=30)
        flips = {"factor_0": 1.0, "factor_1": 1.0}
        factors = _make_factors(2)
        weights = {"factor_0": 0.7, "factor_1": 0.3}

        scores, details = compute_composite_scores(
            signal_matrix, flips, factors, factor_weights=weights,
        )

        assert len(scores) == 2
        for sym in signal_matrix:
            assert sym in scores

    def test_direction_correction(self) -> None:
        """方向校正生效。"""
        panel, _ = _make_panel(n_symbols=1, n_dates=30)
        # 构造简单信号
        sym = next(iter(panel))
        signal_matrix: dict[str, dict[str, np.ndarray]] = {
            sym: {"factor_a": np.array([1.0, 2.0, 3.0])},
        }
        flips = {"factor_a": -1.0}  # 反转
        factors = [{"name": "factor_a"}]

        scores, details = compute_composite_scores(
            signal_matrix, flips, factors, factor_weights=None,
        )

        # factor_a 被反转，信号值应为负
        assert sym in scores
        assert details[sym]["factor_a"] < 0  # 方向校正后为负

    def test_empty_signal_matrix(self) -> None:
        """空信号矩阵返回空。"""
        scores, details = compute_composite_scores({}, {}, [], {})
        assert scores == {}
        assert details == {}


# ─── 集成测试 ─────────────────────────────────────────────


class TestSignalPipelineIntegration:
    """信号管道集成测试（GAP-S04 全链路）。"""

    def test_full_pipeline(self) -> None:
        """方向校正 → Ridge 权重 → 合成 全链路。"""
        panel, dates = _make_panel(n_symbols=5, n_dates=80)
        signal_matrix = _make_signal_matrix(panel, n_factors=3, n_dates=80)
        factors = _make_factors(3)

        # Step 1: 方向校正
        flips = compute_factor_sign_flips(
            signal_matrix, panel, dates, ic_lookback=20,
        )
        assert len(flips) == 3

        # Step 2: Ridge 权重
        weights = compute_ridge_weights(
            signal_matrix, panel, dates, flips, lookback=30,
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-6

        # Step 3: 合成
        scores, details = compute_composite_scores(
            signal_matrix, flips, factors, weights,
        )
        assert len(scores) == 5
        assert len(details) == 5