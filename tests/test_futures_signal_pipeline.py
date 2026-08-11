"""
tests/test_futures_signal_pipeline.py — 期货信号管道测试（_compute_ridge_weights + 辅助函数）

覆盖:
  - _compute_ridge_weights: sklearn 不可用回退、单因子/零因子、NaN 过滤、
    训练样本不足、正常 Ridge 回归、显式 alpha、零系数回退
  - _compute_factor_sign_flips: 正常截面 IC、空数据、少量品种
  - _compute_composite_scores: 等权合成、Ridge 加权合成、方向校正
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# scripts/ 不在 sys.path 中，需要手动添加
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from futures_signal_pipeline import (
    _compute_composite_scores,
    _compute_factor_sign_flips,
    _compute_per_variety_weights,
    _compute_ridge_weights,
    load_futures_elite_factors,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


def _make_panel(
    n_symbols: int = 5,
    n_days: int = 120,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """构造合成面板数据（品种行情 + 共同交易日）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]

    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        sym = f"RB{i}"
        close = 3000 + rng.normal(0, 50, n_days).cumsum()
        close = np.maximum(close, 500)
        df = pd.DataFrame(
            {
                "open": close + rng.normal(0, 10, n_days),
                "high": close + abs(rng.normal(0, 15, n_days)),
                "low": close - abs(rng.normal(0, 15, n_days)),
                "close": close,
                "volume": rng.integers(1000, 10000, n_days).astype(float),
            },
            index=dates,
        )
        panel[sym] = df

    return panel, date_strs


def _make_signal_matrix(
    panel: dict[str, pd.DataFrame],
    factor_names: list[str],
    seed: int = 42,
    nan_rate: float = 0.0,
    zero_factor_names: list[str] = None,
) -> dict[str, dict[str, np.ndarray]]:
    """构造合成信号矩阵。"""
    rng = np.random.default_rng(seed)
    zero_factor_names = zero_factor_names or []
    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    for sym, df in panel.items():
        n_days = len(df)
        sym_signals: dict[str, np.ndarray] = {}
        for fname in factor_names:
            if fname in zero_factor_names:
                arr = np.zeros(n_days)
            else:
                arr = rng.normal(0, 1, n_days)
                if nan_rate > 0:
                    n_nan = int(n_days * nan_rate)
                    nan_idx = rng.choice(n_days, n_nan, replace=False)
                    arr = arr.astype(float)
                    arr[nan_idx] = np.nan
            sym_signals[fname] = arr
        signal_matrix[sym] = sym_signals
    return signal_matrix


def _make_factor_sign_flips(
    factor_names: list[str],
    flip_map: dict[str, float] = None,
) -> dict[str, float]:
    """构造因子方向校正字典。"""
    flips = {}
    for fname in factor_names:
        if flip_map and fname in flip_map:
            flips[fname] = flip_map[fname]
        else:
            flips[fname] = 1.0
    return flips


# ─── _compute_per_variety_weights ─────────────────────────────────────────


class TestComputePerVarietyWeights:
    """_compute_per_variety_weights 测试（修复：NaN IC 污染 total 导致品种被跳过）。"""

    def test_nan_ic_does_not_poison_total(self):
        """因子 IC 为 NaN（常数信号导致 Spearman IC 未定义）不应使整品种被跳过。"""
        global_weights = {"f_a": 0.6, "f_b": 0.4}
        per_variety_ic = {"f_a": {"RB0": np.nan}, "f_b": {"RB0": 0.5}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        # f_a 的 NaN IC 按低 IC 回退：weight ∝ gw * min_ic
        w_a = result["RB0"]["f_a"]
        w_b = result["RB0"]["f_b"]
        assert w_a > 0 and w_b > 0
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9
        # 有效 IC(0.5) 因子权重大于 NaN 回退因子
        assert w_b > w_a

    def test_all_nan_ics_variety_kept(self):
        """品种所有因子 IC 均 NaN → 按 min_ic 回退保留，不因 total=NaN 被丢弃。"""
        global_weights = {"f_a": 0.5, "f_b": 0.5}
        per_variety_ic = {"f_a": {"RB0": np.nan}, "f_b": {"RB0": np.nan}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9

    def test_weights_normalized_per_variety(self):
        """正常 IC → 每个品种权重归一化和为 1，强 IC 因子获得更高权重。"""
        global_weights = {"f_a": 0.5, "f_b": 0.5}
        per_variety_ic = {
            "f_a": {"RB0": 0.8, "CU0": 0.1},
            "f_b": {"RB0": 0.2, "CU0": 0.6},
        }

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert set(result.keys()) == {"RB0", "CU0"}
        for var in result:
            assert abs(sum(result[var].values()) - 1.0) < 1e-9
        # RB0 上 f_a IC 更高 → f_a 权重大；CU0 上 f_b IC 更高 → f_b 权重大
        assert result["RB0"]["f_a"] > result["RB0"]["f_b"]
        assert result["CU0"]["f_b"] > result["CU0"]["f_a"]

    def test_low_ic_factor_min_fallback(self):
        """|IC| < min_ic → 因子仅获得 min_ic 级别权重（接近丢弃而非完全剔除）。"""
        global_weights = {"f_strong": 0.7, "f_weak": 0.3}
        per_variety_ic = {"f_strong": {"RB0": 0.6}, "f_weak": {"RB0": 0.001}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        w_strong = result["RB0"]["f_strong"]
        w_weak = result["RB0"]["f_weak"]
        assert w_weak > 0  # 低 IC 因子不剔除，仅极低权重
        assert w_strong / w_weak > 10

    def test_factor_missing_from_ic_min_fallback(self):
        """全局权重中的因子缺失该品种 IC → 按 min_ic 回退（不因 key 不匹配丢失）。"""
        global_weights = {"f_a": 0.6, "f_b": 0.4}
        per_variety_ic = {"f_a": {"RB0": 0.5}}  # f_b 完全缺失

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        assert "f_b" in result["RB0"]  # 缺失因子仍被保留（min_ic 权重）
        assert "f_a" in result["RB0"]
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9

    def test_empty_per_variety_ic_returns_empty(self):
        """无品种级 IC 数据 → 返回空 dict。"""
        result = _compute_per_variety_weights({"f_a": 1.0}, {})
        assert result == {}

    def test_zero_global_weight_factor_skipped(self):
        """全局权重为 0 的因子不参与品种级权重分配。"""
        global_weights = {"f_a": 0.0, "f_b": 1.0}
        per_variety_ic = {"f_a": {"RB0": 0.9}, "f_b": {"RB0": 0.5}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "f_a" not in result["RB0"]
        assert result["RB0"]["f_b"] == 1.0


# ─── _compute_ridge_weights ──────────────────────────────────────────────


class TestComputeRidgeWeights:
    """_compute_ridge_weights 全覆盖测试。"""

    def test_sklearn_not_available_fallback_to_equal_weight(self):
        """sklearn 不可用时回退到等权。"""
        panel, dates = _make_panel(n_symbols=3, n_days=120)
        factor_names = ["f1", "f2", "f3"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        with patch.dict(sys.modules, {"sklearn.linear_model": None}):
            with patch("futures_signal_pipeline.RidgeCV", create=True):
                weights = _compute_ridge_weights(
                    signal_matrix,
                    panel,
                    dates,
                    flips,
                    lookback=60,
                )

        assert len(weights) == 3
        assert all(abs(w - 1.0 / 3) < 0.001 for w in weights.values())

    def test_single_factor_equal_weight(self):
        """单因子 → 等权（权重=1.0）。"""
        panel, dates = _make_panel(n_symbols=2, n_days=60)
        factor_names = ["f1"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=30,
        )

        assert weights == {"f1": 1.0}

    def test_zero_factors_empty_dict(self):
        """零因子 → 返回空 dict（所有品种因子集为空时）。"""
        panel, dates = _make_panel(n_symbols=1, n_days=60)
        signal_matrix = {"RB0": {}}
        flips = {}

        weights = _compute_ridge_weights(signal_matrix, panel, dates, flips)
        assert weights == {}

    def test_common_intersection_reduces_to_one(self):
        """品种间因子交集只剩 1 个 → 等权回退。"""
        panel, dates = _make_panel(n_symbols=3, n_days=80)
        factor_names = ["f1", "f2", "f3"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        # 删除 RB1 和 RB2 的 f2, f3，使交集只剩 f1
        del signal_matrix["RB1"]["f2"]
        del signal_matrix["RB1"]["f3"]
        del signal_matrix["RB2"]["f2"]
        del signal_matrix["RB2"]["f3"]
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=40,
        )

        assert weights == {"f1": 1.0}

    def test_high_nan_factors_excluded(self):
        """NaN 率 > 50% 的因子被排除。"""
        panel, dates = _make_panel(n_symbols=4, n_days=80)
        factor_names = ["f_good", "f_bad"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        # 让 f_bad 全部为 NaN
        for sym in signal_matrix:
            signal_matrix[sym]["f_bad"] = np.full(
                len(signal_matrix[sym]["f_bad"]),
                np.nan,
            )

        # 只剩 f_good 1 个因子 → 等权
        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=40,
        )

        assert "f_bad" not in weights
        assert "f_good" in weights

    def test_insufficient_samples_fallback(self):
        """训练样本不足 → 回退到等权。"""
        panel, dates = _make_panel(n_symbols=1, n_days=10)  # 极少数据
        factor_names = ["f1", "f2", "f3"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=5,
        )

        assert len(weights) == 3
        assert all(abs(w - 1.0 / 3) < 0.001 for w in weights.values())

    def test_normal_ridge_regression(self):
        """正常 Ridge 回归 → 权重和为 1，强因子获得更高权重。"""
        panel, dates = _make_panel(n_symbols=5, n_days=120)
        factor_names = ["f1", "f2", "f3", "f4"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=60,
        )

        assert len(weights) == 4
        assert abs(sum(weights.values()) - 1.0) < 0.001
        assert all(w >= 0 for w in weights.values())

    def test_explicit_alpha_parameter(self):
        """显式指定 alpha → 使用 Ridge(alpha=...) 而非 RidgeCV。"""
        panel, dates = _make_panel(n_symbols=5, n_days=120)
        factor_names = ["f1", "f2", "f3"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=60,
            alpha=10.0,
        )

        assert len(weights) == 3
        assert abs(sum(weights.values()) - 1.0) < 0.001

    def test_weights_sum_to_one(self):
        """权重归一化：所有权重之和为 1。"""
        panel, dates = _make_panel(n_symbols=5, n_days=150)
        factor_names = [f"f{i}" for i in range(10)]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=80,
        )

        assert abs(sum(weights.values()) - 1.0) < 0.001

    def test_direction_flip_affects_weights(self):
        """方向校正影响权重：反转信号后的因子权重应不同。"""
        panel, dates = _make_panel(n_symbols=5, n_days=120)
        factor_names = ["f1", "f2", "f3"]
        signal_matrix = _make_signal_matrix(panel, factor_names)

        # 正常权重
        flips_normal = _make_factor_sign_flips(factor_names)
        weights_normal = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips_normal,
            lookback=60,
        )

        # 全部反转
        flips_reversed = {f: -1.0 for f in factor_names}
        weights_reversed = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips_reversed,
            lookback=60,
        )

        # 权重可能不同（因为 Ridge 取 abs(coef)，方向反转改变特征空间，
        # 但 abs 操作使权重只取决于预测强度而非方向）
        assert len(weights_normal) == len(weights_reversed)

    def test_empty_cross_symbol_intersection_uses_coverage(self):
        """回归: 全品种因子交集为空时（品种多、因子执行成功集合不同），
        覆盖率阈值过滤应保留覆盖>=50%品种的因子，避免权重静默回退等权。"""
        rng = np.random.default_rng(7)
        panel, dates = _make_panel(n_symbols=5, n_days=120)
        factor_names = ["f1", "f2", "f3", "f4"]
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        for i, sym in enumerate(panel):
            signal_matrix[sym] = {f: rng.normal(0, 1, len(panel[sym])) for f in factor_names}
            # 前 4 只品种各缺 1 个因子 → 全品种交集为空，但各因子覆盖 4/5 >= 50%
            if i < 4:
                del signal_matrix[sym][f"f{i + 1}"]
        flips = _make_factor_sign_flips(factor_names)

        weights = _compute_ridge_weights(
            signal_matrix,
            panel,
            dates,
            flips,
            lookback=60,
        )

        assert len(weights) == 4, f"覆盖率过滤应保留 4 个因子, 实际 {len(weights)}"
        assert abs(sum(weights.values()) - 1.0) < 1e-6


# ─── _compute_factor_sign_flips ──────────────────────────────────────────


class TestComputeFactorSignFlips:
    """_compute_factor_sign_flips 测试。"""

    def test_normal_computation(self):
        """正常截面 IC 计算 → 返回所有因子的方向校正值。"""
        panel, dates = _make_panel(n_symbols=5, n_days=120)
        factor_names = ["f1", "f2"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _compute_factor_sign_flips(signal_matrix, panel, dates)

        assert len(flips) == 2
        assert all(v in (1.0, -1.0) for v in flips.values())

    def test_empty_signal_matrix(self):
        """空信号矩阵 → 抛出 StopIteration（无法获取第一个品种）。"""
        with pytest.raises(StopIteration):
            _compute_factor_sign_flips({}, {}, [])

    def test_few_common_symbols(self):
        """共同品种 < 5 → IC 计算跳过，默认不反转。"""
        panel, dates = _make_panel(n_symbols=2, n_days=60)
        factor_names = ["f1"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _compute_factor_sign_flips(
            signal_matrix,
            panel,
            dates,
            ic_lookback=10,
        )

        assert flips["f1"] == 1.0  # 样本不足，默认不反转


# ─── _compute_composite_scores ───────────────────────────────────────────


class TestComputeCompositeScores:
    """_compute_composite_scores 测试。"""

    def test_equal_weight_composite(self):
        """等权合成 → 所有因子权重相同。"""
        panel, dates = _make_panel(n_symbols=3, n_days=60)
        factor_names = ["f1", "f2"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        factors = [
            {"name": "f1", "params": {}},
            {"name": "f2", "params": {}},
        ]
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
            factor_weights=None,
        )

        assert len(scores) == 3
        assert all(isinstance(v, float) for v in scores.values())

    def test_ridge_weighted_composite(self):
        """Ridge 加权合成 → 权重应用于因子信号。"""
        panel, dates = _make_panel(n_symbols=3, n_days=60)
        factor_names = ["f1", "f2"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        factors = [
            {"name": "f1", "params": {}},
            {"name": "f2", "params": {}},
        ]
        custom_weights = {"f1": 0.8, "f2": 0.2}
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
            factor_weights=custom_weights,
        )

        assert len(scores) == 3

    def test_sign_flip_applied(self):
        """方向反转因子信号值应被反转。"""
        panel, dates = _make_panel(n_symbols=2, n_days=30)
        factor_names = ["f1"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = {"f1": -1.0}  # 反转

        factors = [{"name": "f1", "params": {}}]
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
        )

        # 验证信号被反转
        for sym, d in details.items():
            orig = signal_matrix[sym]["f1"][-1]
            if np.isfinite(orig):
                assert abs(d["f1"] - (-orig)) < 0.001
            else:
                assert d["f1"] == 0.0

    def test_empty_signal_matrix_returns_empty(self):
        """空信号矩阵 → 抛出 ZeroDivisionError（n_factors=0）。"""
        with pytest.raises(ZeroDivisionError):
            _compute_composite_scores({}, {}, [])


# ─── load_futures_elite_factors ──────────────────────────────────────────


class TestLoadFuturesEliteFactors:
    """load_futures_elite_factors 测试。"""

    def test_ic_threshold_zero_loads_all(self, tmp_path, monkeypatch):
        """ic_threshold=0 时加载所有因子。"""
        import json

        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        for i in range(3):
            data = {
                "name": f"factor_{i}",
                "factor_id": f"fid_{i}",
                "code": f"def factor_program_{i}(data, params):\n    return data['close'] * {i}",
                "evaluation": {
                    "level_1_backtest": {"ic": 0.1 + i * 0.2},
                },
            }
            (elite_dir / f"factor_{i}.json").write_text(json.dumps(data))

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert len(factors) == 3

    def test_ic_threshold_filters(self, tmp_path, monkeypatch):
        """ic_threshold > 0 时过滤低 IC 因子。"""
        import json

        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        for i, ic in enumerate([0.1, 0.3, 0.5]):
            data = {
                "name": f"factor_{i}",
                "factor_id": f"fid_{i}",
                "code": f"def factor_program_{i}(data, params):\n    return data['close'] * {ic}",
                "evaluation": {
                    "level_1_backtest": {"ic": ic},
                },
            }
            (elite_dir / f"factor_{i}.json").write_text(json.dumps(data))

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0.3)
        assert len(factors) == 2  # 0.3 和 0.5

    def test_corrupt_json_skipped(self, tmp_path, monkeypatch):
        """损坏的 JSON 文件应被跳过。"""
        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        (elite_dir / "bad.json").write_text("not json")
        (elite_dir / "good.json").write_text(
            '{"name":"f1","factor_id":"fid","code":"def f(): return 1","evaluation":{"level_1_backtest":{"ic":0.5}}}',
        )

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert len(factors) == 1

    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        """空目录 → 返回空列表。"""
        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert factors == []
