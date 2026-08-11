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
    apply_stock_regime_weights,
    build_stock_regime_panels,
    compute_composite_scores,
    compute_factor_sign_flips,
    compute_ridge_weights,
    neutralize_signal_matrix,
    normalize_signal_matrix,
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
        dates = [d.strftime("%Y-%m-%d") for d in pd.date_range("2025-01-01", periods=n_dates, freq="B")]

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
                future_rets[t] = (panel[sym]["close"].iloc[t + 5] - panel[sym]["close"].iloc[t]) / panel[sym][
                    "close"
                ].iloc[t]

            # factor_0 = future_rets + 噪声 → 强正相关
            sig0 = future_rets + rng.normal(0, 0.01, n)
            # factor_1 = -future_rets + 噪声 → 强负相关
            sig1 = -future_rets + rng.normal(0, 0.01, n)

            signal_matrix[sym] = {
                "factor_0": sig0,
                "factor_1": sig1,
            }

        flips = compute_factor_sign_flips(
            signal_matrix,
            panel,
            dates,
            ic_lookback=20,
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
            signal_matrix,
            panel,
            dates,
            ic_lookback=20,
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
            signal_matrix,
            panel,
            dates,
            ic_lookback=20,
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
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=30,
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
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=20,
        )

        assert abs(weights.get("factor_0", 0) - 1.0) < 1e-6

    def test_insufficient_samples_fallback(self) -> None:
        """样本不足时回退到等权。"""
        panel, dates = _make_panel(n_symbols=2, n_dates=10)
        signal_matrix = _make_signal_matrix(panel, n_factors=2, n_dates=10)
        flips = {"factor_0": 1.0, "factor_1": 1.0}

        weights = compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=5,
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
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=20,
            corr_penalty_lambda=0.5,
            extreme_threshold=0.90,
        )

        # 极端相关因子 factor_b 被硬删除，保留 2 个因子
        assert len(weights) == 2, f"预期 2 个因子，实际 {len(weights)}"
        # 权重和为 1
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_empty_cross_symbol_intersection_uses_coverage(self) -> None:
        """回归: 全标的因子交集为空时（标的多、因子执行成功集合不同），
        覆盖率阈值过滤应保留覆盖>=50%标的的因子，避免权重静默回退等权。"""
        rng = np.random.default_rng(7)
        panel, dates = _make_panel(n_symbols=5, n_dates=120)
        factor_names = ["factor_0", "factor_1", "factor_2", "factor_3"]
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for i, sym in enumerate(panel):
            n = len(panel[sym])
            signal_matrix[sym] = {f: rng.normal(0, 1, n) for f in factor_names}
            # 前 4 只标的各缺 1 个因子 → 全标的交集为空，但各因子覆盖 4/5 >= 50%
            if i < 4:
                del signal_matrix[sym][f"factor_{i}"]
        flips = {f: 1.0 for f in factor_names}

        weights = compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=60,
        )

        assert len(weights) == 4, f"覆盖率过滤应保留 4 个因子, 实际 {len(weights)}"
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
            signal_matrix,
            flips,
            factors,
            factor_weights=None,
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
            signal_matrix,
            flips,
            factors,
            factor_weights=weights,
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
            signal_matrix,
            flips,
            factors,
            factor_weights=None,
        )

        # factor_a 被反转，信号值应为负
        assert sym in scores
        assert details[sym]["factor_a"] < 0  # 方向校正后为负

    def test_empty_signal_matrix(self) -> None:
        """空信号矩阵返回空。"""
        scores, details = compute_composite_scores({}, {}, [], {})
        assert scores == {}
        assert details == {}


# ─── 截面标准化测试 ──────────────────────────────────────


class TestNormalizeSignalMatrix:
    """测试截面标准化（z-score / rank，GAP-076 信号管道增强）。"""

    def _build(self, n_symbols: int = 4, n_dates: int = 30) -> tuple[dict[str, pd.DataFrame], list[str]]:
        """构造 panel + 对齐到 common_dates 的信号矩阵。"""
        panel, date_strs = _make_panel(n_symbols=n_symbols, n_dates=n_dates, seed=7)
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for i, sym in enumerate(panel):
            # factor_a: 截面值=股票序号偏移（每只股票常数）+ 随时间线性漂移
            arr = np.linspace(0, 1.0, n_dates) + float(i)
            signal_matrix[sym] = {"factor_a": arr.astype(np.float64)}
        return panel, date_strs, signal_matrix

    def test_none_is_noop(self) -> None:
        """method=none 不修改信号。"""
        panel, dates, sm = self._build()
        before = {s: sm[s]["factor_a"].copy() for s in sm}
        normalize_signal_matrix(sm, panel, dates, method="none")
        for s in sm:
            assert np.array_equal(sm[s]["factor_a"], before[s]), "none 不应修改信号"

    def test_zscore_centers_and_scales(self) -> None:
        """zscore 后每交易日截面均值≈0、标准差≈1。"""
        panel, dates, sm = self._build(n_symbols=4)
        normalize_signal_matrix(sm, panel, dates, method="zscore")
        # 最后一交易日截面（所有股票 sig[-1]）
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        assert abs(last.mean()) < 1e-9, f"截面均值应≈0，实际 {last.mean()}"
        assert abs(last.std() - 1.0) < 1e-6, f"截面标准差应≈1，实际 {last.std()}"

    def test_zscore_constant_section_to_zero(self) -> None:
        """常数截面（所有股票同值）zscore 后为 0（无信息不贡献）。"""
        panel, dates, sm = self._build(n_symbols=3)
        # 把 factor_a 改为全常数 5.0
        for s in sm:
            sm[s]["factor_a"] = np.full(len(sm[s]["factor_a"]), 5.0)
        normalize_signal_matrix(sm, panel, dates, method="zscore")
        for s in sm:
            assert np.allclose(sm[s]["factor_a"], 0.0), "常数截面 zscore 应全为 0"

    def test_rank_range_and_order(self) -> None:
        """rank 后值域 [-1,1] 且保序（截面内单调）。"""
        panel, dates, sm = self._build(n_symbols=4)
        normalize_signal_matrix(sm, panel, dates, method="rank")
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        assert last.min() >= -1.0 - 1e-9 and last.max() <= 1.0 + 1e-9, "rank 应在 [-1,1]"
        # 与原始截面序一致（factor_a = i + 时间漂移 → 按 i 排序）
        orig_order = np.argsort(np.array([float(i) for i in range(4)]))
        rank_order = np.argsort(last)
        assert np.array_equal(orig_order, rank_order), "rank 应保持截面序"

    def test_nan_write_back_as_zero(self) -> None:
        """截面含 NaN 时 zscore 保持 NaN，写回后置 0（与合成 isfinite 兜底一致）。"""
        panel, dates, sm = self._build(n_symbols=4, n_dates=30)
        # 某只股票某日置 NaN
        sm["SYM0001"]["factor_a"][15] = np.nan
        normalize_signal_matrix(sm, panel, dates, method="zscore")
        assert np.isfinite(sm["SYM0001"]["factor_a"][15]), "NaN 写回应为有限值"

    def test_invalid_method_raises(self) -> None:
        """非法 method 抛 ValueError。"""
        panel, dates, sm = self._build()
        with pytest.raises(ValueError):
            normalize_signal_matrix(sm, panel, dates, method="minmax")

    def test_empty_matrix_noop(self) -> None:
        """空矩阵/空日期优雅降级。"""
        normalize_signal_matrix({}, {}, [], method="zscore")
        normalize_signal_matrix({}, {}, ["2025-01-01"], method="rank")

    def test_datetimeindex_dates(self) -> None:
        """common_dates 为 pd.DatetimeIndex 时可正常工作（真值判断回归）。"""
        panel, dates, sm = self._build(n_symbols=4)
        dt_index = pd.DatetimeIndex(dates)
        normalize_signal_matrix(sm, panel, dt_index, method="zscore")
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        assert abs(last.mean()) < 1e-9, "DatetimeIndex 输入下 zscore 应正常生效"


class TestNeutralizeSignalMatrix:
    """测试截面中性化适配器（D.2 股票 L3：行业/市值 proxy 剥离）。

    与 normalize_signal_matrix 同构（{symbol: {factor_name: array}} 结构）。
    """

    def _build(
        self, n_symbols: int = 4, n_dates: int = 30
    ) -> tuple[dict[str, pd.DataFrame], list[str], dict[str, dict[str, np.ndarray]]]:
        panel, date_strs = _make_panel(n_symbols=n_symbols, n_dates=n_dates, seed=7)
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for i, sym in enumerate(panel):
            arr = np.linspace(0, 1.0, n_dates) + float(i)
            signal_matrix[sym] = {"factor_a": arr.astype(np.float64)}
        return panel, date_strs, signal_matrix

    def test_none_is_noop(self) -> None:
        """method=none 不修改信号。"""
        panel, dates, sm = self._build()
        before = {s: sm[s]["factor_a"].copy() for s in sm}
        neutralize_signal_matrix(sm, panel, dates, method="none")
        for s in sm:
            assert np.array_equal(sm[s]["factor_a"], before[s])

    def test_industry_group_mean_zero(self) -> None:
        """industry 后同行业组内均值归零（跨行业量纲保留）。"""
        panel, dates, sm = self._build(n_symbols=4)
        ind = {"SYM0000": "bank", "SYM0001": "bank", "SYM0002": "tech", "SYM0003": "tech"}
        neutralize_signal_matrix(sm, panel, dates, method="industry", industry_map=ind)
        # 末截面: 值=股票序号(0..3) → bank 组(0,1)、tech 组(2,3) 各去均值
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        # 组内和为零（去均值语义）
        assert abs((last[0] + last[1])) < 1e-6
        assert abs((last[2] + last[3])) < 1e-6

    def test_missing_map_skips(self) -> None:
        """industry_map=None 且 method=industry → 跳过不抛错（映射缺失降级）。"""
        panel, dates, sm = self._build()
        before = {s: sm[s]["factor_a"].copy() for s in sm}
        neutralize_signal_matrix(sm, panel, dates, method="industry", industry_map=None)
        for s in sm:
            assert np.array_equal(sm[s]["factor_a"], before[s])

    def test_suffixed_map_keys_aligned(self) -> None:
        """映射键带后缀（600519.SH）、面板纯代码时行业中性化真正生效（真实数据格式回归）。

        修复前映射键未归一化 → 组内去均值静默空转（信号不变），本用例断言组内和归零。
        """
        panel, dates, sm = self._build(n_symbols=4)
        ind = {f"SYM{i:04d}.SH": ("bank" if i < 2 else "tech") for i in range(4)}
        neutralize_signal_matrix(sm, panel, dates, method="industry", industry_map=ind)
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        assert abs((last[0] + last[1])) < 1e-6, "bank 组内应去均值归零"
        assert abs((last[2] + last[3])) < 1e-6, "tech 组内应去均值归零"

    def test_suffixed_size_map_aligned(self) -> None:
        """市值映射带后缀键时 size 中性化同样对齐（真实数据格式回归）。"""
        panel, dates, sm = self._build(n_symbols=6)
        caps = np.array([float(100.0 * (i + 1) ** 2) for i in range(6)])
        cap_map = {sym + ".SH": c for sym, c in zip(panel, caps)}
        rng = np.random.default_rng(11)
        for i, sym in enumerate(panel):
            log_cap = np.log(caps[i])
            sm[sym]["factor_a"] = (2.0 * log_cap + rng.normal(0, 0.3, len(sm[sym]["factor_a"]))).astype(
                np.float64
            )
        neutralize_signal_matrix(sm, panel, dates, method="size", cap_map=cap_map)
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        corr = np.corrcoef(last, np.log(caps))[0, 1]
        assert abs(corr) < 0.1

    def test_size_residual(self) -> None:
        """size 后残差与市值无关（线性市值分量被 OLS 完整剥离）。"""
        panel, dates, sm = self._build(n_symbols=6)
        caps = np.array([float(100.0 * (i + 1) ** 2) for i in range(6)])
        cap_map = {sym: c for sym, c in zip(panel, caps)}
        # 信号 = 2*log(cap) + 噪声 → OLS 回归应完整剥离市值线性分量
        rng = np.random.default_rng(11)
        for i, sym in enumerate(panel):
            log_cap = np.log(caps[i])
            sm[sym]["factor_a"] = (2.0 * log_cap + rng.normal(0, 0.3, len(sm[sym]["factor_a"]))).astype(
                np.float64
            )
        neutralize_signal_matrix(sm, panel, dates, method="size", cap_map=cap_map)
        last = np.array([sm[s]["factor_a"][-1] for s in sm])
        corr = np.corrcoef(last, np.log(caps))[0, 1]
        assert abs(corr) < 0.1

    def test_both_applies_industry_then_size(self) -> None:
        """both 先行业去均值再市值回归，正常结束不抛错。"""
        panel, dates, sm = self._build(n_symbols=6)
        ind = {sym: ("bank" if i < 3 else "tech") for i, sym in enumerate(panel)}
        cap_map = {sym: float(100.0 * (i + 1)) for i, sym in enumerate(panel)}
        neutralize_signal_matrix(sm, panel, dates, method="both", industry_map=ind, cap_map=cap_map)
        for s in sm:
            assert np.all(np.isfinite(sm[s]["factor_a"]))

    def test_invalid_method_raises(self) -> None:
        """非法 method 抛 ValueError。"""
        panel, dates, sm = self._build()
        with pytest.raises(ValueError):
            neutralize_signal_matrix(sm, panel, dates, method="minmax")

    def test_empty_matrix_noop(self) -> None:
        """空矩阵/空日期优雅降级。"""
        neutralize_signal_matrix({}, {}, [], method="industry", industry_map={"a": "x"})
        neutralize_signal_matrix({}, {}, ["2025-01-01"], method="both", industry_map={"a": "x"})


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
            signal_matrix,
            panel,
            dates,
            ic_lookback=20,
        )
        assert len(flips) == 3

        # Step 2: Ridge 权重
        weights = compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=30,
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-6

        # Step 3: 合成
        scores, details = compute_composite_scores(
            signal_matrix,
            flips,
            factors,
            weights,
        )
        assert len(scores) == 5
        assert len(details) == 5


# ─── 权重快照（GAP-072，v2.99.0）─────────────────────────


class TestWeightSnapshot:
    """save/load/filter 权重快照（解绑 L3 与信号管道后，冻结日复用）。"""

    def test_save_and_load_roundtrip(self, tmp_path):
        """保存后读取返回相同权重/方向校正/品种级权重/标准化方式。"""
        from scripts._signal_common import load_weight_snapshot, save_weight_snapshot

        path = tmp_path / "weights.json"
        save_weight_snapshot(
            path,
            {"fct_a": 0.6, "fct_b": 0.4},
            factor_sign_flips={"fct_a": -1.0, "fct_b": 1.0},
            per_variety_weights={"RB0": {"fct_a": 0.7, "fct_b": 0.3}},
            recomputed_at="2026-08-07",
            normalize="zscore",
        )
        assert path.exists()

        snap = load_weight_snapshot(path)
        assert snap is not None
        assert snap["recomputed_at"] == "2026-08-07"
        assert snap["factor_weights"] == {"fct_a": 0.6, "fct_b": 0.4}
        assert snap["factor_sign_flips"] == {"fct_a": -1.0, "fct_b": 1.0}
        assert snap["per_variety_weights"] == {"RB0": {"fct_a": 0.7, "fct_b": 0.3}}
        assert snap["normalize"] == "zscore"

    def test_load_legacy_snapshot_normalize_default_none(self, tmp_path):
        """旧快照无 normalize 字段时默认回退 'none'（向后兼容）。"""
        from scripts._signal_common import load_weight_snapshot

        legacy = tmp_path / "legacy.json"
        legacy.write_text(
            '{"schema": "signal_weights_v1", "recomputed_at": "2026-08-07", '
            '"factor_weights": {"fct_a": 1.0}, "factor_sign_flips": {"fct_a": 1.0}}',
            encoding="utf-8",
        )
        snap = load_weight_snapshot(legacy)
        assert snap is not None
        assert snap.get("normalize", "none") == "none"

    def test_load_missing_returns_none(self, tmp_path):
        """快照文件缺失返回 None（触发冷启动重算）。"""
        from scripts._signal_common import load_weight_snapshot

        assert load_weight_snapshot(tmp_path / "missing.json") is None

    def test_load_corrupt_returns_none(self, tmp_path):
        """损坏/空权重快照返回 None（触发冷启动重算）。"""
        from scripts._signal_common import load_weight_snapshot

        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert load_weight_snapshot(bad) is None

        empty = tmp_path / "empty.json"
        empty.write_text('{"schema": "signal_weights_v1", "factor_weights": {}}', encoding="utf-8")
        assert load_weight_snapshot(empty) is None

    def test_regime_field_roundtrip(self, tmp_path):
        """快照 regime 字段往返（重算日写入 auto，冻结日读取同值）。"""
        from scripts._signal_common import load_weight_snapshot, save_weight_snapshot

        path = tmp_path / "weights_regime.json"
        save_weight_snapshot(
            path,
            {"fct_a": 0.6},
            factor_sign_flips={"fct_a": 1.0},
            neutralize="both",
            regime="auto",
        )
        snap = load_weight_snapshot(path)
        assert snap is not None
        assert snap["regime"] == "auto"
        assert snap["neutralize"] == "both"

    def test_load_legacy_snapshot_regime_default_none(self, tmp_path):
        """旧快照无 regime 字段时默认回退 'none'（向后兼容）。"""
        from scripts._signal_common import load_weight_snapshot

        legacy = tmp_path / "legacy_regime.json"
        legacy.write_text(
            '{"schema": "signal_weights_v1", "recomputed_at": "2026-08-07", '
            '"factor_weights": {"fct_a": 1.0}, "factor_sign_flips": {"fct_a": 1.0}}',
            encoding="utf-8",
        )
        snap = load_weight_snapshot(legacy)
        assert snap is not None
        assert snap.get("regime", "none") == "none"

    def test_filter_factors_by_weights(self):
        """冻结日过滤：仅保留快照内因子，新因子等待下次重算进入。"""
        from scripts._signal_common import filter_factors_by_weights

        factors = [
            {"name": "fct_a", "factor_id": "a"},
            {"name": "fct_b", "factor_id": "b"},
            {"name": "fct_c", "factor_id": "c"},  # 快照外新因子
        ]
        kept = filter_factors_by_weights(factors, {"fct_a": 0.6, "fct_b": 0.4})
        assert [f["name"] for f in kept] == ["fct_a", "fct_b"]


# ─── Regime 自适应权重（D.2 偏差 b 补齐）─────────────────────────


class TestBuildStockRegimePanels:
    """测试由个股面板聚合构造行业/风格收益面板。"""

    def _panel(self, n_symbols: int = 8, n_dates: int = 60, seed: int = 7):
        """8 只股票：前 4 只上涨趋势，后 4 只下跌趋势（行业分化可检测）。"""
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2025-01-01", periods=n_dates, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        for i in range(n_symbols):
            drift = 0.3 if i < n_symbols // 2 else -0.3
            close = 100.0 + np.cumsum(rng.normal(drift, 0.5, n_dates))
            panel[f"SYM{i:04d}"] = pd.DataFrame({"close": close}, index=dates)
        return panel

    def test_industry_panel_built(self) -> None:
        """industry_map 存在时按行业聚合等权收益面板。"""
        panel = self._panel()
        ind = {f"SYM{i:04d}": ("bank" if i < 4 else "tech") for i in range(8)}
        industry_panel, style_panel = build_stock_regime_panels(panel, ind, None)

        assert set(industry_panel.keys()) == {"bank", "tech"}
        assert style_panel == {}
        for series in industry_panel.values():
            assert len(series) >= 2
            assert bool(np.isfinite(series).all()), "收益序列应全有限"

    def test_suffix_symbol_alignment(self) -> None:
        """symbol 键带后缀（600519.SH）时与纯代码行业映射对齐。"""
        panel = self._panel(n_symbols=4)
        suffixed = {f"SYM{i:04d}.SH": df for i, (k, df) in enumerate(panel.items())}
        ind = {f"SYM{i:04d}": ("bank" if i < 2 else "tech") for i in range(4)}
        industry_panel, _ = build_stock_regime_panels(suffixed, ind, None)

        assert set(industry_panel.keys()) == {"bank", "tech"}

    def test_suffixed_map_keys_aligned(self) -> None:
        """映射键带后缀（600519.SH）、面板纯代码时同样对齐（真实数据格式回归）。"""
        panel = self._panel(n_symbols=4)
        ind = {f"SYM{i:04d}.SH": ("bank" if i < 2 else "tech") for i in range(4)}
        industry_panel, _ = build_stock_regime_panels(panel, ind, None)

        assert set(industry_panel.keys()) == {"bank", "tech"}

    def test_style_panel_large_small(self) -> None:
        """cap_map 存在时按市值中位数分 large/small 两组。"""
        panel = self._panel(n_symbols=6)
        cap_map = {f"SYM{i:04d}": float(100.0 * (i + 1) ** 2) for i in range(6)}
        industry_panel, style_panel = build_stock_regime_panels(panel, None, cap_map)

        assert industry_panel == {}
        assert set(style_panel.keys()) == {"large", "small"}
        # large 组应为市值较大的一半（SYM0003~SYM0005）
        assert len(style_panel["large"]) >= 2
        assert len(style_panel["small"]) >= 2

    def test_no_maps_returns_empty(self) -> None:
        """两个映射均缺失 → 两个面板均为空（检测器降级 unknown）。"""
        panel = self._panel()
        industry_panel, style_panel = build_stock_regime_panels(panel, None, None)
        assert industry_panel == {}
        assert style_panel == {}

    def test_insufficient_cap_members_degrades(self) -> None:
        """有市值映射但匹配股票 < 4 → style_panel 为空（不抛错）。"""
        panel = self._panel(n_symbols=3)
        cap_map = {f"SYM{i:04d}": float(100.0 * (i + 1)) for i in range(3)}
        _, style_panel = build_stock_regime_panels(panel, None, cap_map)
        assert style_panel == {}


class TestApplyStockRegimeWeights:
    """测试按 StockRegime 调整因子权重。"""

    def _factors_and_weights(self) -> tuple[list[dict], dict[str, float]]:
        factors = [
            {"factor_id": "f1", "name": "momentum_alpha"},  # → style momentum
            {"factor_id": "f2", "name": "quality_blue"},  # → style quality
            {"factor_id": "f3", "name": "opaque_signal_z9"},  # → style other（倍率 1.0）
        ]
        weights = {"momentum_alpha": 0.5, "quality_blue": 0.3, "opaque_signal_z9": 0.2}
        return factors, weights

    def test_style_multiplier_applied(self) -> None:
        """large_cap 制度下 momentum 权重下调（×0.9）、quality 上调（×1.2）。"""
        factors, weights = self._factors_and_weights()
        adjusted = apply_stock_regime_weights(weights, factors, {"regime": "large_cap", "confidence": 0.8})

        assert abs(adjusted["momentum_alpha"] - 0.45) < 1e-9, "momentum 应 ×0.9"
        assert abs(adjusted["quality_blue"] - 0.36) < 1e-9, "quality 应 ×1.2"
        assert abs(adjusted["opaque_signal_z9"] - 0.2) < 1e-9, "other 风格倍率应为 1.0"

    def test_key_set_preserved(self) -> None:
        """调整后键集合与入参一致。"""
        factors, weights = self._factors_and_weights()
        adjusted = apply_stock_regime_weights(weights, factors, {"regime": "small_cap"})
        assert set(adjusted.keys()) == set(weights.keys())

    def test_empty_regime_noop(self) -> None:
        """regime 为空/无 regime 键 → 原样返回。"""
        factors, weights = self._factors_and_weights()
        assert apply_stock_regime_weights(weights, factors, {}) == weights
        assert apply_stock_regime_weights(weights, factors, None) == weights
        assert apply_stock_regime_weights({}, factors, {"regime": "large_cap"}) == {}

    def test_unknown_regime_noop(self) -> None:
        """无配置倍率的 regime 键 → 倍率 1.0 保持原权重（不抛错）。"""
        factors, weights = self._factors_and_weights()
        adjusted = apply_stock_regime_weights(weights, factors, {"regime": "unknown_regime"})
        for k in weights:
            assert abs(adjusted[k] - weights[k]) < 1e-9

    def test_family_dimension_supported(self) -> None:
        """dimension="family" 走 REGIME_FAMILY_MULTIPLIERS（兼容期货语义）。"""
        factors = [{"factor_id": "f1", "name": "trend_alpha"}]
        weights = {"trend_alpha": 0.4}
        # trend 在 bull 制度下 ×1.3
        adjusted = apply_stock_regime_weights(weights, factors, {"regime": "bull"}, dimension="family")
        assert abs(adjusted["trend_alpha"] - 0.52) < 1e-9


class TestRegimeChainIntegration:
    """面板构造 → StockRegimeSelector 检测 → 权重调整 全链路（偏差 b 集成）。"""

    def test_detect_and_adjust_smoke(self) -> None:
        """端到端：行业分化面板可检测出 regime 并完成权重调整（不抛错、键一致）。"""
        from fts.factor_engine.stock_regime import StockRegimeSelector

        rng = np.random.default_rng(7)
        dates = pd.date_range("2025-01-01", periods=80, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        ind: dict[str, str] = {}
        for i in range(8):
            drift = 0.3 if i < 4 else -0.3  # bank 上行 / tech 下行
            close = 100.0 + np.cumsum(rng.normal(drift, 0.5, 80))
            panel[f"SYM{i:04d}"] = pd.DataFrame({"close": close}, index=dates)
            ind[f"SYM{i:04d}"] = "bank" if i < 4 else "tech"

        industry_panel, style_panel = build_stock_regime_panels(panel, ind, None)
        assert industry_panel, "行业面板不应为空"
        regime = StockRegimeSelector().detect(industry_panel, style_panel)
        assert regime.get("regime"), "应检测出非空 regime"

        factors = [{"factor_id": "f1", "name": "momentum_alpha"}]
        weights = {"momentum_alpha": 0.4}
        adjusted = apply_stock_regime_weights(weights, factors, regime)
        assert set(adjusted.keys()) == {"momentum_alpha"}
        assert np.isfinite(adjusted["momentum_alpha"])
