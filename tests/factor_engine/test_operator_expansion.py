"""tests/factor_engine/test_operator_expansion.py — C8 算子扩容测试。

覆盖（C8 实施设计 2026-08-11）:
    1. 22 个高价值算子的功能与边界行为（L1 时序 12 + L2 截面 4 + L3 条件 3 + L5 领域 3）
    2. 双注册表一致性：feature_ops.OperatorRegistry（GP）与 expr_dsl.registry 强制共享
    3. 算子目录自动生成幂等：scripts/generate_operator_catalog.py 两次输出一致
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import C8Ops, OperatorRegistry
from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RNG = np.random.default_rng(42)


@pytest.fixture
def series() -> pd.Series:
    """120 期随机游走序列（跨窗口充分）。"""
    r = RNG.normal(0, 1, 120)
    return pd.Series(np.cumsum(r) + 100.0)


@pytest.fixture
def rising() -> pd.Series:
    """严格递增序列。"""
    return pd.Series(np.arange(1, 121, dtype=float))


@pytest.fixture
def constant() -> pd.Series:
    """常数序列（离散度/极值类算子应输出 0）。"""
    return pd.Series(np.full(120, 5.0))


C8_NAMES = [
    "ts_argmin",
    "ts_ema",
    "ts_mad",
    "ts_range",
    "ts_iqr",
    "ts_quantile_range",
    "ts_return_over_max",
    "ts_min_max_ratio",
    "ts_std_ratio",
    "ts_roc_sum",
    "ts_breakout",
    "ts_cumulative_return",
    "cs_rank_diff",
    "cs_zscore_diff",
    "cs_extreme_ratio",
    "cs_median_dev",
    "where_gt",
    "consecutive_true",
    "sign_flip",
    "mean_reversion_z",
    "trend_strength",
    "volume_pressure",
]


# ─── L1 时序算子（12）──────────────────────────────────────


class TestC8TimeSeriesOps:
    """L1 时序算子功能与边界。"""

    def test_ts_argmin_tracks_minimum_position(self, rising):
        """递增序列最小值在窗口最左（时间最远）→ argmin=0。"""
        out = C8Ops.ts_argmin(rising, window=5)
        assert out.iloc[-5:].notna().all()
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_argmin_no_raise_on_small_window(self, series):
        """window<样本仍可计算（min_periods=2 兜底）。"""
        out = C8Ops.ts_argmin(series, window=2)
        assert np.isfinite(out.iloc[-1])

    def test_ts_ema_smooths_trend(self, rising):
        """递增序列 ema 介于首尾之间且滞后于最新值。"""
        out = C8Ops.ts_ema(rising, span=10)
        assert out.iloc[-1] > 1.0
        assert float(out.iloc[-1]) < float(rising.iloc[-1])

    def test_ts_ema_constant_is_constant(self, constant):
        """常数序列 ema 恒等于常数值。"""
        out = C8Ops.ts_ema(constant, span=10)
        assert float(out.iloc[-1]) == pytest.approx(5.0)

    def test_ts_mad_zero_for_constant(self, constant):
        """常数序列 MAD=0。"""
        out = C8Ops.ts_mad(constant, window=10)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_mad_positive_for_volatile(self, series):
        """波动序列 MAD 非负。"""
        out = C8Ops.ts_mad(series, window=10)
        assert (out.dropna() >= 0).all()
        assert float(out.iloc[-1]) > 0.0

    def test_ts_range_zero_for_constant(self, constant):
        """常数序列振幅=0（mean=0 时 fillna(0) 兜底）。"""
        out = C8Ops.ts_range(constant, window=10)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_range_positive_for_rising(self, rising):
        """递增序列振幅为正。"""
        out = C8Ops.ts_range(rising, window=10)
        assert float(out.iloc[-1]) > 0.0

    def test_ts_iqr_zero_for_constant(self, constant):
        """常数序列 IQR=0。"""
        out = C8Ops.ts_iqr(constant, window=10)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_iqr_positive_for_volatile(self, series):
        """波动序列 IQR>0。"""
        out = C8Ops.ts_iqr(series, window=10)
        assert float(out.iloc[-1]) > 0.0

    def test_ts_quantile_range_equals_max_min(self, series):
        """q_hi=1.0, q_lo=0.0 时分位差等价 max−min。"""
        out = C8Ops.ts_quantile_range(series, window=20, q_hi=1.0, q_lo=0.0)
        manual = series.rolling(20, min_periods=2).max() - series.rolling(20, min_periods=2).min()
        assert float(out.iloc[-1]) == pytest.approx(float(manual.iloc[-1]))

    def test_ts_quantile_range_high_minus_low(self, series):
        """分位差非负（q_hi≥q_lo）。"""
        out = C8Ops.ts_quantile_range(series, window=20, q_hi=0.9, q_lo=0.1)
        assert (out.dropna() >= 0).all()

    def test_ts_return_over_max_non_positive(self, series):
        """距滚动高点回撤恒 ≤0。"""
        out = C8Ops.ts_return_over_max(series, window=20)
        assert (out.dropna() <= 1e-12).all()

    def test_ts_return_over_max_recent_high_near_zero(self, rising):
        """递增序列最新值即新高 → 回撤≈0。"""
        out = C8Ops.ts_return_over_max(rising, window=20)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_min_max_ratio_zero_for_constant(self, constant):
        """常数序列 max/min−1=0。"""
        out = C8Ops.ts_min_max_ratio(constant, window=10)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_ts_min_max_ratio_positive_for_rising(self, rising):
        """递增序列区间幅度为正。"""
        out = C8Ops.ts_min_max_ratio(rising, window=10)
        assert float(out.iloc[-1]) > 0.0

    def test_ts_std_ratio_high_when_volatile(self):
        """平稳段后接高幅震荡 → 短/长波动比 >1。"""
        x = pd.Series(np.r_[np.zeros(50), np.tile([10.0, -10.0], 5)])
        out = C8Ops.ts_std_ratio(x, short=5, long=20)
        assert float(out.iloc[-1]) > 1.0

    def test_ts_std_ratio_low_when_quiet(self, constant):
        """常数序列波动比为 NaN（std=0 兜底不抛）。"""
        out = C8Ops.ts_std_ratio(constant, short=5, long=20)
        assert not np.isfinite(out.iloc[-1])

    def test_ts_roc_sum_zero_for_constant(self, constant):
        """常数序列收益率累加=0。"""
        out = C8Ops.ts_roc_sum(constant, window=10)
        assert (out.dropna().abs() < 1e-12).all()

    def test_ts_roc_sum_positive_for_rising(self, rising):
        """递增序列累积动量为正。"""
        out = C8Ops.ts_roc_sum(rising, window=10)
        assert float(out.iloc[-1]) > 0.0

    def test_ts_breakout_fires_on_new_high(self, rising):
        """严格递增序列持续突破新高 → 尾部全 1。"""
        out = C8Ops.ts_breakout(rising, window=20)
        assert float(out.iloc[-1]) == 1.0
        assert float(out.iloc[-10:].mean()) == pytest.approx(1.0)

    def test_ts_breakout_no_fire_within_range(self, constant):
        """常数序列不触发突破。"""
        out = C8Ops.ts_breakout(constant, window=20)
        assert (out.fillna(0) == 0).all()

    def test_ts_cumulative_return_positive_for_rising(self, rising):
        """递增序列 n 期累计收益为正。"""
        out = C8Ops.ts_cumulative_return(rising, window=20)
        assert float(out.iloc[-1]) > 0.0

    def test_ts_cumulative_return_zero_for_constant(self, constant):
        """常数序列累计收益=0。"""
        out = C8Ops.ts_cumulative_return(constant, window=20)
        assert float(out.iloc[-1]) == pytest.approx(0.0)


# ─── L2 截面算子（4）───────────────────────────────────────


class TestC8CrossSectionOps:
    """L2 截面（单序列滚动）算子。"""

    def test_cs_rank_diff_zero_for_constant(self, constant):
        """常数序列排名不变 → 排名差分=0。"""
        out = C8Ops.cs_rank_diff(constant, window=1)
        assert (out.fillna(0).abs() < 1e-12).all()

    def test_cs_rank_diff_sign_changes_with_position(self):
        """排名漂移序列差分非恒零。"""
        x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        out = C8Ops.cs_rank_diff(x, window=1)
        assert (out.fillna(0).abs() > 1e-12).any()

    def test_cs_zscore_diff_zero_for_constant(self, constant):
        """常数序列 zscore 差分=0。"""
        out = C8Ops.cs_zscore_diff(constant, window=1)
        assert (out.fillna(0).abs() < 1e-12).all()

    def test_cs_zscore_diff_matches_manual(self, series):
        """zscore 差分与手算一致。"""
        out = C8Ops.cs_zscore_diff(series, window=1)
        z = (series - series.mean()) / series.std()
        assert float(out.iloc[-1]) == pytest.approx(float(z.iloc[-1] - z.iloc[-2]))

    def test_cs_extreme_ratio_zero_for_constant(self, constant):
        """常数序列无极端值 → 占比 0。"""
        out = C8Ops.cs_extreme_ratio(constant, window=10, n_std=2.0)
        assert float(out.iloc[-1]) == pytest.approx(0.0)

    def test_cs_extreme_ratio_bounded_unit(self, series):
        """极端值占比 ∈ [0,1]。"""
        out = C8Ops.cs_extreme_ratio(series, window=20, n_std=1.0)
        vals = out.dropna()
        assert (vals >= 0).all() and (vals <= 1).all()

    def test_cs_extreme_ratio_detects_spike(self):
        """尖峰段极端占比 > 平稳段。"""
        x = pd.Series(np.r_[np.zeros(50), np.full(10, 100.0), np.zeros(50)])
        out = C8Ops.cs_extreme_ratio(x, window=10, n_std=2.0)
        assert float(out.iloc[-1]) == pytest.approx(0.0)
        # 尖峰段内窗口应有非零占比
        spike_window = out.iloc[50:60]
        assert (spike_window.fillna(0) > 0).any()

    def test_cs_median_dev_zero_for_constant(self, constant):
        """常数序列与中位数偏离=0。"""
        out = C8Ops.cs_median_dev(constant, window=10)
        assert (out.fillna(0).abs() < 1e-12).all()

    def test_cs_median_dev_sign_tracks_position(self, rising):
        """递增序列偏离滚动中位数为正（后半段）。"""
        out = C8Ops.cs_median_dev(rising, window=20)
        assert float(out.iloc[-1]) > 0.0


# ─── L3 条件算子（3）───────────────────────────────────────


class TestC8ConditionalOps:
    """L3 条件/计数算子。"""

    def test_where_gt_selects_a_or_b(self):
        """x>threshold 取 a，否则 b。"""
        x = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = C8Ops.where_gt(x, threshold=3.0, a=10.0, b=0.0)
        assert list(out) == [0.0, 0.0, 0.0, 10.0, 10.0]

    def test_where_gt_inclusive_equality(self):
        """x==threshold 不满足 > → 取 b。"""
        x = pd.Series([2.0, 3.0, 4.0])
        out = C8Ops.where_gt(x, threshold=3.0, a=1.0, b=-1.0)
        assert list(out) == [-1.0, -1.0, 1.0]

    def test_consecutive_true_counts_runs(self):
        """连续 True 计数：T,T,F,T,T → 1,2,0,1,2。"""
        cond = pd.Series([True, True, False, True, True])
        out = C8Ops.consecutive_true(cond, window=10)
        assert list(out) == [1, 2, 0, 1, 2]

    def test_consecutive_true_clips_at_window(self):
        """连续计数上限截断到 window。"""
        cond = pd.Series([True] * 30)
        out = C8Ops.consecutive_true(cond, window=20)
        assert int(out.max()) == 20

    def test_consecutive_true_handles_non_series(self, series):
        """非 Series 输入（如列表）降级为全 0。"""
        out = C8Ops.consecutive_true([True, True, False], window=5)
        assert (out == 0).all()

    def test_sign_flip_counts_direction_changes(self):
        """先正后负再正 → 翻转计数累积。"""
        x = pd.Series([1.0, 1.0, -1.0, -1.0, 1.0, 1.0])
        out = C8Ops.sign_flip(x, window=5)
        assert int(out.max()) >= 2
        assert float(out.iloc[-1]) == pytest.approx(2.0)

    def test_sign_flip_zero_for_constant_sign(self, rising):
        """同向序列无翻转。"""
        out = C8Ops.sign_flip(rising, window=5)
        assert (out.fillna(0) == 0).all()


# ─── L5 领域算子（3）───────────────────────────────────────


class TestC8DomainOps:
    """L5 领域算子。"""

    def test_mean_reversion_z_negative_for_rising(self, rising):
        """递增序列 zscore 为正 → 均值回归强度为负。"""
        out = C8Ops.mean_reversion_z(rising, window=20)
        assert float(out.iloc[-1]) < 0.0

    def test_mean_reversion_z_matches_negative_zscore(self, series):
        """输出等于 −滚动 zscore。"""
        out = C8Ops.mean_reversion_z(series, window=20)
        z = (series - series.rolling(20, min_periods=2).mean()) / series.rolling(20, min_periods=2).std()
        assert float(out.iloc[-1]) == pytest.approx(-float(z.iloc[-1]))

    def test_trend_strength_bounded_unit(self, rising):
        """趋势强度归一化到 [0,1]。"""
        out = C8Ops.trend_strength(rising, window=20)
        vals = out.dropna()
        assert (vals >= 0).all() and (vals <= 1).all()

    def test_trend_strength_positive_for_rising(self, rising):
        """递增序列趋势强度为正。"""
        out = C8Ops.trend_strength(rising, window=20)
        assert float(out.iloc[-1]) > 0.0

    def test_volume_pressure_positive_on_volume_up(self):
        """放量上涨 → 正量价压力。"""
        close = pd.Series(np.linspace(100.0, 110.0, 30))
        volume = pd.Series(np.r_[np.full(20, 1000.0), np.full(10, 3000.0)])
        out = C8Ops.volume_pressure(close, volume, window=10)
        assert float(out.iloc[-1]) > 0.0

    def test_volume_pressure_no_raise_zero_volume(self):
        """零成交量段不抛异常（fillna 兜底）。"""
        close = pd.Series(np.linspace(100.0, 100.5, 30))
        volume = pd.Series(np.zeros(30))
        out = C8Ops.volume_pressure(close, volume, window=10)
        assert (out.dropna().abs() < 1e-12).all()


# ─── 双注册表一致性 ────────────────────────────────────────


class TestC8DualRegistry:
    """C8 扩容算子双注册表强制共享（单一事实源）。"""

    def test_all_22_ops_registered_in_gp(self):
        """feature_ops.OperatorRegistry 注册全部 22 个 C8 算子（category=c8）。"""
        reg = OperatorRegistry()
        names = {op.name for op in reg.list_operators("c8")}
        assert names == set(C8_NAMES)
        assert len(names) == 22

    def test_all_22_ops_registered_in_dsl(self):
        """expr_dsl 注册表包含全部 22 个 C8 算子。"""
        dsl = build_registry()
        assert set(C8_NAMES) <= set(dsl)
        assert len(dsl) >= 102  # C8 验收：算子总数 >100

    def test_verify_consistency_passes(self):
        """双注册表一致性校验通过（无 mismatch / 无 missing）。"""
        result = verify_registry_consistency()
        assert result["consistent"] is True
        assert result["mismatched"] == []
        assert result["errors"] == []
        assert result["overlapping"] >= 56

    def test_required_shared_covers_all_c8(self):
        """required_shared 强制共享名单覆盖全部 22 个 C8 算子。"""
        result = verify_registry_consistency()
        assert result["unshared_required"] == []

    def test_gp_registry_can_invoke_c8_op(self, series):
        """GP 注册表按名调用 C8 算子（位置参数语义）。"""
        reg = OperatorRegistry()
        assert reg.get_operator("ts_mad") is not None
        out = reg.call("ts_mad", series, 10)
        assert out.iloc[-1] > 0.0


# ─── 算子目录自动生成幂等 ──────────────────────────────────


class TestOperatorCatalogGeneration:
    """scripts/generate_operator_catalog.py 幂等性。"""

    def _import_script(self):
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            import generate_operator_catalog as mod

            return mod
        finally:
            sys.path.remove(str(PROJECT_ROOT / "scripts"))

    def test_catalog_rows_cover_102_ops(self):
        """目录行数与 DSL 注册表一致且 ≥102。"""
        mod = self._import_script()
        rows = mod.build_catalog_rows()
        assert len(rows) >= 102
        names = {r["name"] for r in rows}
        assert set(C8_NAMES) <= names

    def test_catalog_rows_deterministic(self):
        """两次 build_catalog_rows 输出一致（确定性排序）。"""
        mod = self._import_script()
        assert mod.build_catalog_rows() == mod.build_catalog_rows()

    def test_render_yaml_idempotent(self):
        """render_yaml 重复渲染文本一致。"""
        mod = self._import_script()
        rows = mod.build_catalog_rows()
        assert mod.render_yaml(rows) == mod.render_yaml(rows)

    def test_render_yaml_contains_c8_ops(self):
        """目录 YAML 包含 C8 算子条目与经济含义。"""
        mod = self._import_script()
        rows = mod.build_catalog_rows()
        text = mod.render_yaml(rows)
        assert "ts_breakout" in text
        assert "economic_meaning" in text

    def test_main_exit_code_zero(self):
        """脚本 main() 正常退出码 0（写入真实目录，幂等无副作用）。"""
        mod = self._import_script()
        rc = mod.main()
        assert rc == 0

    def test_script_runs_via_subprocess(self):
        """脚本可通过 CLI 运行（无语法/导入错误）。"""
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "generate_operator_catalog.py")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert "operator_catalog.yaml" in proc.stdout
