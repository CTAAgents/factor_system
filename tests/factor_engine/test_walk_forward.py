"""tests/factor_engine/test_walk_forward.py — Walk-Forward 优化器测试。

覆盖范围: ~38 个测试用例覆盖 WalkForwardOptimizer 全部公共方法与评分逻辑。

测试策略:
    1. 使用 DatetimeIndex DataFrame 模拟日频时序数据
    2. mock evaluate_fn 工厂函数控制 IC/Sharpe/Turnover 返回值
    3. 窗口划分、评分计算、边界条件分模块测试
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.walk_forward import (
    DEFAULT_WALK_FORWARD_CONFIG,
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
    WalkForwardWindowResult,
    _safe_stdev,
    _to_date_str,
)

# ══════════════════════════════════════════════════════════
# 测试辅助函数
# ══════════════════════════════════════════════════════════

# 小配置：用少量数据即可生成多个窗口
_MINI_CONFIG: WalkForwardConfig = {
    "window_years": 0,      # window_days=0 导致按日期索引行数，而非按天数
}

# 使用精确的行索引模式：用小数值让窗口按索引切割
# window_years=0.1 → 36 天, step_months=1 → 30 天
_SMALL_CONFIG: WalkForwardConfig = {
    "window_years": 0,      # 0 → window_days=0，这样窗口根据行索引切割
}


def make_data(n_rows: int, seed: int = 42) -> pd.DataFrame:
    """生成 n_rows 行的合成 DataFrame（带 DatetimeIndex）。"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    return pd.DataFrame({
        "close": 100 + np.cumsum(np.random.randn(n_rows) * 0.5),
        "volume": np.random.randint(1_000, 10_000, n_rows).astype(float),
    }, index=dates)


def make_evaluate_fn(
    ic: float = 0.0,
    sharpe: float = 0.0,
    turnover: float = 0.0,
    *,
    fail_on_windows: set[int] | None = None,
    raise_exc: bool = False,
) -> Callable[[pd.DataFrame, pd.DataFrame], dict[str, float]]:
    """创建受控的 evaluate_fn。

    evaluate_fn 返回 dict 包含 ic / sharpe / turnover 键。
    """
    call_count: list[int] = [0]

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        idx = call_count[0]
        call_count[0] += 1
        if fail_on_windows is not None and idx in fail_on_windows:
            if raise_exc:
                raise RuntimeError(f"simulated failure on window {idx}")
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
        return {"ic": ic, "sharpe": sharpe, "turnover": turnover}

    return _fn


def make_varied_evaluate_fn(
    metrics_list: list[dict[str, float]],
) -> Callable[[pd.DataFrame, pd.DataFrame], dict[str, float]]:
    """为每个窗口返回不同的指标组合。"""
    call_count: list[int] = [0]

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(metrics_list):
            return dict(metrics_list[idx])
        return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

    return _fn


def _with_config(**overrides: Any) -> WalkForwardConfig:
    """创建用于测试的小型 WalkForwardConfig。

    使用小窗口参数以便快速生成多窗口。
    """
    base: WalkForwardConfig = {
        "window_years": 0,      # window_days=0，直接用索引
        "step_months": 0,       # step_days=0
        "min_oos_months": 0,    # min_oos_days=0
        "n_windows": 3,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════
# 内部工具函数测试
# ══════════════════════════════════════════════════════════


class TestInternalUtils:
    """_safe_stdev / _to_date_str / _compute_consistency_score 测试。"""

    def test_safe_stdev_empty(self) -> None:
        assert _safe_stdev([]) == 0.0

    def test_safe_stdev_single(self) -> None:
        assert _safe_stdev([0.5]) == 0.0

    def test_safe_stdev_two_values(self) -> None:
        assert _safe_stdev([1.0, 1.0]) == 0.0
        assert _safe_stdev([0.0, 1.0]) == pytest.approx(0.707106, abs=1e-5)

    def test_safe_stdev_multiple(self) -> None:
        vals = [0.1, 0.2, 0.3, 0.4, 0.5]
        assert _safe_stdev(vals) == pytest.approx(np.std(vals, ddof=1))

    def test_to_date_str_timestamp(self) -> None:
        ts = pd.Timestamp("2024-06-15")
        assert _to_date_str(ts) == "2024-06-15"

    def test_to_date_str_other(self) -> None:
        assert _to_date_str("2024-06-15") == "2024-06-15"
        assert _to_date_str(123) == "123"


class TestComputeConsistencyScore:
    """_compute_consistency_score 公式验证。"""

    def test_perfect_score(self) -> None:
        """完美指标应得 100 分。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=1.0, ic_volatility=0.0, ic_mean=0.10,
        )
        # 40 + 30 + 30 = 100
        assert score == 100.0

    def test_zero_score(self) -> None:
        """最差指标应得 0 分。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=0.0, ic_volatility=0.3, ic_mean=0.0,
        )
        # 0 + 0 + 0 = 0
        assert score == 0.0

    def test_mid_range_score(self) -> None:
        """中间值应正确计算。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=0.5, ic_volatility=0.15, ic_mean=0.05,
        )
        # consistency: 0.5 * 100 * 0.4 = 20
        # volatility: max(0, 1-0.15/0.3) * 100 * 0.3 = 0.5 * 30 = 15
        # strength: min(1, 0.05/0.1) * 100 * 0.3 = 0.5 * 30 = 15
        # total = 20 + 15 + 15 = 50
        assert score == 50.0

    def test_ic_mean_capped(self) -> None:
        """ic_mean 超过 0.1 应被 min 截断。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=1.0, ic_volatility=0.0, ic_mean=0.50,
        )
        # strength: min(1, 0.5/0.1) * 30 = 1 * 30 = 30
        assert score == 100.0

    def test_volatility_capped(self) -> None:
        """ic_volatility 超过 max_ic_volatility 应被 max(0) 截断。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=1.0, ic_volatility=0.6, ic_mean=0.10,
        )
        # volatility: max(0, 1-0.6/0.3) * 30 = max(0, -1) * 30 = 0
        # 40 + 0 + 30 = 70
        assert score == 70.0

    def test_rounding(self) -> None:
        """结果应四舍五入到 2 位小数。"""
        opt = WalkForwardOptimizer()
        score = opt._compute_consistency_score(
            ic_consistency=1.0 / 3.0, ic_volatility=0.1, ic_mean=0.033,
        )
        # consistency: 0.3333 * 40 = 13.333...
        # volatility: (1-0.1/0.3)*30 = (1-0.3333)*30 = 20
        # strength: min(1, 0.033/0.1)*30 = 0.33*30 = 9.9
        # total = 13.333 + 20 + 9.9 = 43.233 → 43.23
        assert score == pytest.approx(43.23, abs=0.02)

    def test_custom_config_affects_volatility_part(self) -> None:
        """自定义 max_ic_volatility 应影响波动率部分计算。"""
        opt = WalkForwardOptimizer({"max_ic_volatility": 0.1})
        score = opt._compute_consistency_score(
            ic_consistency=1.0, ic_volatility=0.05, ic_mean=0.10,
        )
        # volatility: max(0, 1-0.05/0.1) * 30 = 0.5 * 30 = 15
        # 40 + 15 + 30 = 85
        assert score == 85.0


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — 配置测试
# ══════════════════════════════════════════════════════════


class TestConfig:
    """WalkForwardOptimizer 配置初始化测试。"""

    def test_default_config_values(self) -> None:
        """默认配置应包含所有字段且值与 DEFAULT_WALK_FORWARD_CONFIG 一致。"""
        opt = WalkForwardOptimizer()
        for key, expected in DEFAULT_WALK_FORWARD_CONFIG.items():
            assert key in opt._config, f"missing key: {key}"
            assert opt._config[key] == expected, f"{key}: expected {expected}, got {opt._config[key]}"

    def test_custom_config_override(self) -> None:
        """传入自定义 config 应覆盖默认值。"""
        custom: WalkForwardConfig = {"n_windows": 8, "window_years": 5}
        opt = WalkForwardOptimizer(custom)
        assert opt._config["n_windows"] == 8
        assert opt._config["window_years"] == 5

    def test_partial_config_override_keeps_defaults(self) -> None:
        """部分覆盖时未指定的字段应保持默认值。"""
        custom: WalkForwardConfig = {"n_windows": 6}
        opt = WalkForwardOptimizer(custom)
        assert opt._config["n_windows"] == 6
        # 未覆盖的字段应从 DEFAULT_WALK_FORWARD_CONFIG 继承
        for key in DEFAULT_WALK_FORWARD_CONFIG:
            if key != "n_windows":
                assert opt._config[key] == DEFAULT_WALK_FORWARD_CONFIG[key], (
                    f"{key}: expected {DEFAULT_WALK_FORWARD_CONFIG[key]}, got {opt._config[key]}"
                )

    def test_config_not_mutating_defaults(self) -> None:
        """修改实例的 _config 不应影响 DEFAULT_WALK_FORWARD_CONFIG。"""
        opt = WalkForwardOptimizer()
        orig_n = DEFAULT_WALK_FORWARD_CONFIG["n_windows"]
        opt._config["n_windows"] = 999
        assert DEFAULT_WALK_FORWARD_CONFIG["n_windows"] == orig_n

    def test_config_empty_dict_defaults(self) -> None:
        """在 __init__ 中 config 为 None 时使用全部默认值。"""
        opt = WalkForwardOptimizer()
        for key, expected in DEFAULT_WALK_FORWARD_CONFIG.items():
            assert opt._config[key] == expected


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — 窗口划分测试（_create_windows）
# ══════════════════════════════════════════════════════════


class TestCreateWindows:
    """_create_windows 窗口划分逻辑测试（基于日期的滚动窗口）。"""

    def _make_opt(self, **overrides: Any) -> WalkForwardOptimizer:
        return WalkForwardOptimizer(_with_config(**overrides))

    def test_create_windows_correct_number(self) -> None:
        """应创建最多 n_windows 个窗口（数据充足时）。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(800)
        windows = opt._create_windows(data)
        assert len(windows) == 3

    def test_each_window_has_train_and_oos(self) -> None:
        """每个窗口应为 (train_df, oos_df) 元组，均为非空 DataFrame。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        windows = opt._create_windows(data)
        for train_df, oos_df in windows:
            assert isinstance(train_df, pd.DataFrame)
            assert isinstance(oos_df, pd.DataFrame)
            assert len(train_df) > 0
            assert len(oos_df) > 0

    def test_window_train_before_oos(self) -> None:
        """每个窗口中 train 数据应早于 oos 数据（时间顺序）。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        windows = opt._create_windows(data)
        for train_df, oos_df in windows:
            assert train_df.index[-1] < oos_df.index[0]

    def test_window_boundaries_no_overlap(self) -> None:
        """不同窗口的 OOS 部分不应重叠。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        windows = opt._create_windows(data)
        oos_starts = [w[1].index[0] for w in windows]
        oos_ends = [w[1].index[-1] for w in windows]
        for i in range(1, len(windows)):
            assert oos_ends[i - 1] < oos_starts[i], (
                f"OOS overlap between window {i-1} and window {i}"
            )

    def test_with_datetime_index(self) -> None:
        """应正确处理 DatetimeIndex。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        windows = opt._create_windows(data)
        assert len(windows) > 0
        for train_df, oos_df in windows:
            assert isinstance(train_df.index, pd.DatetimeIndex)
            assert isinstance(oos_df.index, pd.DatetimeIndex)

    def test_n_windows_one(self) -> None:
        """n_windows=1 时应生成单个窗口。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=1)
        data = make_data(500)
        windows = opt._create_windows(data)
        assert len(windows) == 1

    def test_data_too_short_returns_empty(self) -> None:
        """数据不足以容纳一个完整窗口时应返回空列表。"""
        opt = self._make_opt(window_years=3, step_months=6, min_oos_months=3, n_windows=4)
        # 3年 = 1095 天，数据不足
        data = make_data(100)
        windows = opt._create_windows(data)
        assert len(windows) == 0

    def test_train_is_cumulative(self) -> None:
        """后面窗口的 train 应包含前面窗口的 train+oos。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        windows = opt._create_windows(data)
        for i in range(1, len(windows)):
            prev_train_len = len(windows[i - 1][0])
            curr_train_len = len(windows[i][0])
            assert curr_train_len > prev_train_len, (
                f"window {i} train len ({curr_train_len}) should be > "
                f"window {i-1} train len ({prev_train_len})"
            )

    def test_stops_when_train_exceeds_data(self) -> None:
        """当 train_end_idx 超过数据长度时应停止生成窗口。"""
        opt = self._make_opt(window_years=1, step_months=12, min_oos_months=1, n_windows=10)
        data = make_data(500)
        windows = opt._create_windows(data)
        # 不会生成 10 个窗口，因为数据不够
        assert len(windows) < 10

    def test_stops_when_oos_too_short(self) -> None:
        """当剩余 OOS 长度小于 min_oos_days 时应停止。"""
        opt = self._make_opt(window_years=1, step_months=6, min_oos_months=6, n_windows=5)
        data = make_data(700)
        windows = opt._create_windows(data)
        # 数据只够一部分窗口
        assert len(windows) < 5


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — 评估结构测试
# ══════════════════════════════════════════════════════════


class TestEvaluateStructure:
    """evaluate 返回结构测试。"""

    def _make_opt(self, **overrides: Any) -> WalkForwardOptimizer:
        return WalkForwardOptimizer(_with_config(**overrides))

    def test_returns_walk_forward_result(self) -> None:
        """应返回符合 WalkForwardResult 结构的结果。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)

        assert isinstance(result, dict)
        assert "windows" in result
        assert "ic_consistency" in result
        assert "ic_volatility" in result
        assert "sharpe_volatility" in result
        assert "consistency_score" in result
        assert "passed" in result
        assert "n_windows_completed" in result

    def test_window_result_has_all_fields(self) -> None:
        """每个窗口结果应包含 WalkForwardWindowResult 全部字段。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)

        expected_keys = {
            "train_start", "train_end", "oos_start", "oos_end",
            "ic", "sharpe", "turnover",
        }
        for wr in result["windows"]:
            assert set(wr.keys()) == expected_keys, f"missing keys: {expected_keys - set(wr.keys())}"

    def test_window_has_date_strings(self) -> None:
        """窗口结果的日期字段应为 ISO 格式字符串。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)

        for wr in result["windows"]:
            assert isinstance(wr["train_start"], str)
            assert isinstance(wr["train_end"], str)
            assert isinstance(wr["oos_start"], str)
            assert isinstance(wr["oos_end"], str)
            # 验证是有效日期
            pd.Timestamp(wr["train_start"])
            pd.Timestamp(wr["train_end"])

    def test_with_trivial_data(self) -> None:
        """最小可用数据应正常执行。"""
        opt = self._make_opt(window_years=0.1, step_months=1, min_oos_months=1, n_windows=2)
        # window_days ≈ 36, step_days ≈ 30, min_oos_days ≈ 30
        # window 0: train=0:36, oos=36:66 → 66 rows needed
        data = make_data(70)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] > 0

    def test_n_windows_completed_matches_result(self) -> None:
        """n_windows_completed 应与实际完成的窗口数一致。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == len(result["windows"])

    def test_empty_dataframe(self) -> None:
        """传入空 DataFrame 应返回空结果（passed=False）。

        注意: _create_windows 对空 DataFrame 会抛出 IndexError。
              此测试验证 evaluate 能正确处理此异常，返回空结果。
        """
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3)
        data = pd.DataFrame({"close": [], "volume": []},
                            index=pd.DatetimeIndex([]))
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == 0
        assert result["passed"] is False
        assert result["windows"] == []

    def test_no_windows_created(self) -> None:
        """数据不足以创建窗口时应返回空结果。"""
        opt = self._make_opt(window_years=10, step_months=12, min_oos_months=6, n_windows=4)
        data = make_data(50)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == 0
        assert result["windows"] == []


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — IC 一致性/波动率测试
# ══════════════════════════════════════════════════════════


class TestEvaluateConsistency:
    """IC 一致性与波动率计算测试。"""

    def _make_opt(self, **overrides: Any) -> WalkForwardOptimizer:
        return WalkForwardOptimizer(_with_config(**overrides))

    def test_ic_consistency_all_positive(self) -> None:
        """所有窗口 IC > 0 时 ic_consistency 应为 1.0。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["ic_consistency"] == 1.0

    def test_ic_consistency_half_positive(self) -> None:
        """一半窗口 IC > 0 时 ic_consistency 应为 0.5。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=4)
        data = make_data(1200)
        fn = make_varied_evaluate_fn([
            {"ic": 0.05, "sharpe": 1.5, "turnover": 0.1},
            {"ic": 0.03, "sharpe": 1.2, "turnover": 0.1},
            {"ic": -0.02, "sharpe": 0.8, "turnover": 0.2},
            {"ic": -0.01, "sharpe": 0.5, "turnover": 0.2},
        ])
        result = opt.evaluate(data, fn)
        assert result["ic_consistency"] == 0.5

    def test_ic_consistency_none_positive(self) -> None:
        """所有窗口 IC <= 0 时 ic_consistency 应为 0.0。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=-0.05, sharpe=0.5, turnover=0.2)
        result = opt.evaluate(data, fn)
        assert result["ic_consistency"] == 0.0

    def test_ic_consistency_zero_ic(self) -> None:
        """IC = 0 时不算正数，不应计入一致性。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.0, sharpe=1.0, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["ic_consistency"] == 0.0

    def test_ic_volatility_calculation(self) -> None:
        """ic_volatility 应为跨窗口 IC 的标准差。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=4)
        data = make_data(1200)
        fn = make_varied_evaluate_fn([
            {"ic": 0.1, "sharpe": 1.0, "turnover": 0.1},
            {"ic": 0.2, "sharpe": 1.0, "turnover": 0.1},
            {"ic": 0.3, "sharpe": 1.0, "turnover": 0.1},
            {"ic": 0.4, "sharpe": 1.0, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        expected_std = np.std([0.1, 0.2, 0.3, 0.4], ddof=1)
        assert result["ic_volatility"] == pytest.approx(expected_std)

    def test_ic_volatility_single_window(self) -> None:
        """只有一个窗口时 ic_volatility 应为 0。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=1)
        data = make_data(500)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["ic_volatility"] == 0.0

    def test_ic_volatility_all_same(self) -> None:
        """所有窗口 IC 相同时 ic_volatility 应为 0。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["ic_volatility"] == 0.0

    def test_sharpe_volatility_calculation(self) -> None:
        """sharpe_volatility 应为跨窗口 Sharpe 的标准差。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": 0.05, "sharpe": 1.0, "turnover": 0.1},
            {"ic": 0.05, "sharpe": 2.0, "turnover": 0.1},
            {"ic": 0.05, "sharpe": 3.0, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        expected_std = np.std([1.0, 2.0, 3.0], ddof=1)
        assert result["sharpe_volatility"] == pytest.approx(expected_std)

    def test_sharpe_volatility_single_window(self) -> None:
        """只有一个窗口时 sharpe_volatility 应为 0。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=1)
        data = make_data(500)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["sharpe_volatility"] == 0.0


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — passed 判定测试
# ══════════════════════════════════════════════════════════


class TestEvaluatePassed:
    """passed 判定逻辑测试。"""

    def _make_opt(self, config: WalkForwardConfig | None = None) -> WalkForwardOptimizer:
        merged: WalkForwardConfig = dict(_with_config(window_years=1, step_months=3, min_oos_months=3, n_windows=3))
        if config:
            merged.update(config)
        return WalkForwardOptimizer(merged)

    def test_passed_true(self) -> None:
        """ic_consistency >= min 且 ic_volatility <= max 时 passed=True。"""
        opt = self._make_opt({"min_ic_consistency": 0.5, "max_ic_volatility": 0.3})
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        # 3 个窗口全部 ic>0 → consistency=1.0 >= 0.5 ✓
        # ic 全部相同 → volatility=0.0 <= 0.3 ✓
        assert result["passed"] is True

    def test_passed_false_low_consistency(self) -> None:
        """ic_consistency < min 时 passed=False。"""
        opt = self._make_opt({"min_ic_consistency": 0.8, "max_ic_volatility": 0.3})
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": 0.05, "sharpe": 1.5, "turnover": 0.1},
            {"ic": -0.02, "sharpe": 1.0, "turnover": 0.2},
            {"ic": 0.03, "sharpe": 1.2, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        # 2/3 = 0.667 < 0.8 → fail
        assert result["passed"] is False

    def test_passed_false_high_volatility(self) -> None:
        """ic_volatility > max 时 passed=False。"""
        opt = self._make_opt({"min_ic_consistency": 0.3, "max_ic_volatility": 0.05})
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": 0.1, "sharpe": 1.5, "turnover": 0.1},
            {"ic": -0.1, "sharpe": 1.0, "turnover": 0.2},
            {"ic": 0.2, "sharpe": 1.2, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        # consistency = 2/3 ≈ 0.667 >= 0.3 ✓
        # volatility = std([0.1, -0.1, 0.2]) ≈ 0.153 > 0.05 → fail
        assert result["passed"] is False

    def test_passed_both_fail(self) -> None:
        """两个条件均不满足时 passed=False。"""
        opt = self._make_opt({"min_ic_consistency": 0.9, "max_ic_volatility": 0.05})
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": 0.1, "sharpe": 1.5, "turnover": 0.1},
            {"ic": -0.2, "sharpe": 1.0, "turnover": 0.2},
            {"ic": -0.1, "sharpe": 1.2, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        # consistency = 1/3 ≈ 0.333 < 0.9
        # volatility = std([0.1, -0.2, -0.1]) ≈ 0.153 > 0.05
        assert result["passed"] is False

    def test_custom_thresholds(self) -> None:
        """自定义阈值应影响 passed 判定。"""
        opt = WalkForwardOptimizer(_with_config(
            window_years=1, step_months=3, min_oos_months=3,
            n_windows=3, min_ic_consistency=0.9,
        ))
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        # consistency=1.0 但 custom min=0.9 → still pass
        # 但 volatility=0.0 <= 0.3 → passed
        assert result["passed"] is True


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — 异常处理与边界测试
# ══════════════════════════════════════════════════════════


class TestEvaluateEdgeCases:
    """evaluate 异常处理与边界条件测试。"""

    def _make_opt(self, **overrides: Any) -> WalkForwardOptimizer:
        return WalkForwardOptimizer(_with_config(**overrides))

    def test_exception_in_one_window(self) -> None:
        """一个窗口抛出异常应被跳过，不影响其他窗口。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1, fail_on_windows={1}, raise_exc=True)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == 2

    def test_all_exceptions(self) -> None:
        """所有窗口均抛出异常时应返回空结果。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_evaluate_fn(
            ic=0.05, sharpe=1.5, turnover=0.1,
            fail_on_windows={0, 1, 2}, raise_exc=True,
        )
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == 0
        assert result["passed"] is False
        assert result["windows"] == []

    def test_negative_ic_values(self) -> None:
        """负 IC 值应正确计算一致性和波动率。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": -0.05, "sharpe": 0.5, "turnover": 0.2},
            {"ic": -0.03, "sharpe": 0.8, "turnover": 0.2},
            {"ic": -0.10, "sharpe": 0.3, "turnover": 0.3},
        ])
        result = opt.evaluate(data, fn)
        # 全部 ic < 0 → consistency = 0.0
        assert result["ic_consistency"] == 0.0
        # volatility = std
        expected_std = np.std([-0.05, -0.03, -0.10], ddof=1)
        assert result["ic_volatility"] == pytest.approx(expected_std)
        assert result["passed"] is False

    def test_mixed_ic_signs(self) -> None:
        """混合正负 IC 应正确计算一致性比率。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=4)
        data = make_data(1200)
        fn = make_varied_evaluate_fn([
            {"ic": 0.05, "sharpe": 1.5, "turnover": 0.1},
            {"ic": -0.02, "sharpe": 0.8, "turnover": 0.2},
            {"ic": 0.03, "sharpe": 1.2, "turnover": 0.1},
            {"ic": -0.01, "sharpe": 0.5, "turnover": 0.3},
        ])
        result = opt.evaluate(data, fn)
        # 2/4 = 0.5
        assert result["ic_consistency"] == 0.5

    def test_single_window_result(self) -> None:
        """单窗口评估应正常返回。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=1)
        data = make_data(500)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)
        assert result["n_windows_completed"] == 1
        assert result["ic_volatility"] == 0.0  # 单窗口 stdev=0
        assert result["sharpe_volatility"] == 0.0

    def test_large_ic_values(self) -> None:
        """极大 IC 值应不影响标准差计算。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)
        fn = make_varied_evaluate_fn([
            {"ic": 0.5, "sharpe": 10.0, "turnover": 0.1},
            {"ic": 1.0, "sharpe": 20.0, "turnover": 0.1},
            {"ic": 1.5, "sharpe": 30.0, "turnover": 0.1},
        ])
        result = opt.evaluate(data, fn)
        expected_ic_std = np.std([0.5, 1.0, 1.5], ddof=1)
        expected_sharpe_std = np.std([10.0, 20.0, 30.0], ddof=1)
        assert result["ic_volatility"] == pytest.approx(expected_ic_std)
        assert result["sharpe_volatility"] == pytest.approx(expected_sharpe_std)
        # IC 全部 > 0 → consistency=1.0
        assert result["ic_consistency"] == 1.0

    def test_missing_keys_in_evaluate_fn_return(self) -> None:
        """evaluate_fn 返回缺少某些键时应有默认值。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=2)
        data = make_data(600)

        def _incomplete_fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
            return {"ic": 0.05}  # 缺少 sharpe 和 turnover

        result = opt.evaluate(data, _incomplete_fn)
        assert result["n_windows_completed"] == 2
        for wr in result["windows"]:
            assert wr["ic"] == 0.05
            assert wr["sharpe"] == 0.0
            assert wr["turnover"] == 0.0


# ══════════════════════════════════════════════════════════
# WalkForwardOptimizer — 集成测试
# ══════════════════════════════════════════════════════════


class TestIntegration:
    """端到端集成测试。"""

    def _make_opt(self, **overrides: Any) -> WalkForwardOptimizer:
        return WalkForwardOptimizer(_with_config(**overrides))

    def test_full_flow_all_pass(self) -> None:
        """所有窗口 IC 为正且低波动时应通过验证。"""
        opt = self._make_opt(
            window_years=1, step_months=3, min_oos_months=3,
            n_windows=3, min_ic_consistency=0.5, max_ic_volatility=0.3,
        )
        data = make_data(1000)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)

        assert result["n_windows_completed"] == 3
        assert result["ic_consistency"] == 1.0
        assert result["ic_volatility"] == 0.0
        assert result["passed"] is True
        assert result["consistency_score"] > 0

    def test_full_flow_all_fail(self) -> None:
        """IC 负值时应不通过验证。"""
        opt = self._make_opt(
            window_years=1, step_months=3, min_oos_months=3,
            n_windows=3, min_ic_consistency=0.5, max_ic_volatility=0.3,
        )
        data = make_data(1000)
        fn = make_evaluate_fn(ic=-0.05, sharpe=0.5, turnover=0.3)
        result = opt.evaluate(data, fn)

        assert result["n_windows_completed"] == 3
        assert result["ic_consistency"] == 0.0
        assert result["passed"] is False

    def test_consistency_score_in_0_100_range(self) -> None:
        """consistency_score 应在 0-100 范围内。"""
        opt = self._make_opt(window_years=1, step_months=3, min_oos_months=3, n_windows=3)
        data = make_data(1000)

        # 好因子
        fn_good = make_evaluate_fn(ic=0.08, sharpe=2.0, turnover=0.05)
        result_good = opt.evaluate(data, fn_good)
        assert 0 <= result_good["consistency_score"] <= 100

        # 差因子
        fn_bad = make_evaluate_fn(ic=-0.05, sharpe=0.3, turnover=0.5)
        result_bad = opt.evaluate(data, fn_bad)
        assert 0 <= result_bad["consistency_score"] <= 100

    def test_varying_consistency_scores(self) -> None:
        """不同 IC 信号应产生不同一致性分数。"""
        opt = self._make_opt(
            window_years=1, step_months=3, min_oos_months=3,
            n_windows=3, min_ic_consistency=0.5, max_ic_volatility=0.3,
        )
        data = make_data(1000)

        # 高质量：一致性强
        fn_high = make_evaluate_fn(ic=0.08, sharpe=2.0, turnover=0.05)
        result_high = opt.evaluate(data, fn_high)

        # 低质量：不一致
        fn_low = make_varied_evaluate_fn([
            {"ic": 0.08, "sharpe": 2.0, "turnover": 0.05},
            {"ic": -0.05, "sharpe": 0.5, "turnover": 0.30},
            {"ic": 0.02, "sharpe": 0.8, "turnover": 0.20},
        ])
        result_low = opt.evaluate(data, fn_low)

        assert result_high["consistency_score"] > result_low["consistency_score"]

    def test_custom_config_flow(self) -> None:
        """自定义配置下的完整评估流程。"""
        config: WalkForwardConfig = {
            "window_years": 1,
            "step_months": 3,
            "min_oos_months": 3,
            "n_windows": 2,
            "min_ic_consistency": 0.5,
            "max_ic_volatility": 0.2,
        }
        opt = WalkForwardOptimizer(config)
        data = make_data(600)
        fn = make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1)
        result = opt.evaluate(data, fn)

        assert result["n_windows_completed"] == 2
        assert len(result["windows"]) == 2
        # 验证窗口日期范围
        for wr in result["windows"]:
            train_start = pd.Timestamp(wr["train_start"])
            train_end = pd.Timestamp(wr["train_end"])
            oos_start = pd.Timestamp(wr["oos_start"])
            oos_end = pd.Timestamp(wr["oos_end"])
            assert train_start <= train_end
            assert train_end < oos_start
            assert oos_start <= oos_end
