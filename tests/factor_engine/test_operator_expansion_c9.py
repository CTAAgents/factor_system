"""tests/factor_engine/test_operator_expansion_c9.py — C9 算子扩容测试。

覆盖（C8 实施设计延续，2026-08-11，DSL 102→132）:
    1. 30 个高价值算子的功能与边界行为（L1 时序 14 + L2 截面 5 + L3 条件 4 + L5 领域 7）
    2. 双注册表一致性：feature_ops.OperatorRegistry（GP，category=c9）与 expr_dsl.registry 强制共享
    3. 算子目录自动生成幂等：scripts/generate_operator_catalog.py 两次输出一致
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import C9Ops, OperatorRegistry
from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RNG = np.random.default_rng(7)


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
    """常数序列（离散度/极值类算子应输出 0 或有限兜底）。"""
    return pd.Series(np.full(120, 5.0))


C9_NAMES = [
    # L1 时序 14
    "ts_pct_rank_window", "ts_zscore_rolling", "ts_skew", "ts_kurt", "ts_slope_pct",
    "ts_position_in_range", "ts_down_ratio", "ts_up_ratio", "ts_gain_loss_ratio",
    "ts_bias_ma", "ts_boll_position", "ts_ma_diff", "ts_vol_shrink", "ts_tail_risk",
    # L2 截面 5
    "cs_winsor_flag", "cs_demean_ratio", "cs_rank_norm", "cs_med_ratio", "cs_extreme_gap",
    # L3 条件 4
    "where_between", "cross_above", "cross_below", "momentum_break",
    # L5 领域 7
    "vol_regime", "mean_reversion_signal", "price_volume_div", "liquidity_dryup",
    "self_corr", "sign_entropy", "reversal_strength",
]


# ─── L1 时序算子（14）──────────────────────────────────────


class TestC9TimeSeriesOps:
    """L1 时序算子功能与边界。"""

    def test_ts_pct_rank_window_tracks_position(self, rising, constant):
        """递增序列尾值百分位=1；常数序列兜底 0.5。"""
        out = C9Ops.ts_pct_rank_window(rising, window=10)
        assert out.iloc[-1] == pytest.approx(1.0, abs=1e-9)
        assert C9Ops.ts_pct_rank_window(constant, window=10).iloc[-1] == pytest.approx(0.5)

    def test_ts_zscore_rolling_constant_zero(self, constant):
        """常数序列滚动 zscore 全 0。"""
        out = C9Ops.ts_zscore_rolling(constant, window=20)
        assert float(out.abs().max()) == pytest.approx(0.0)

    def test_ts_skew_constant_zero(self, constant):
        """常数序列滚动偏度兜底 0。"""
        assert float(C9Ops.ts_skew(constant, window=20).abs().max()) == pytest.approx(0.0)

    def test_ts_kurt_constant_zero(self, constant):
        """常数序列滚动峰度兜底 0。"""
        assert float(C9Ops.ts_kurt(constant, window=20).abs().max()) == pytest.approx(0.0)

    def test_ts_slope_pct_positive_on_rising(self, rising):
        """递增序列斜率占比为正。"""
        out = C9Ops.ts_slope_pct(rising, window=10)
        assert out.dropna().iloc[-1] > 0

    def test_ts_position_in_range_bounds(self, rising, constant):
        """递增序列尾值区间位置=1（clip 上界）；常数兜底 0.5。"""
        out = C9Ops.ts_position_in_range(rising, window=10)
        assert out.iloc[-1] == pytest.approx(1.0, abs=1e-9)
        assert C9Ops.ts_position_in_range(constant, window=10).iloc[-1] == pytest.approx(0.5)

    def test_ts_down_ratio_rising_zero(self, rising):
        """递增序列下跌占比=0。"""
        assert float(C9Ops.ts_down_ratio(rising, window=10).iloc[-1]) == pytest.approx(0.0)

    def test_ts_up_ratio_rising_one(self, rising):
        """递增序列上涨占比=1。"""
        assert float(C9Ops.ts_up_ratio(rising, window=10).iloc[-1]) == pytest.approx(1.0)

    def test_ts_gain_loss_ratio_finite(self, rising):
        """全涨序列涨跌幅比有限（不抛）。"""
        out = C9Ops.ts_gain_loss_ratio(rising, window=10)
        assert np.isfinite(out.iloc[-1])

    def test_ts_bias_ma_constant_zero(self, constant):
        """常数序列乖离率=0。"""
        assert float(C9Ops.ts_bias_ma(constant, window=10).abs().max()) == pytest.approx(0.0)

    def test_ts_boll_position_constant_zero(self, constant):
        """常数序列布林带位置兜底 0（无 NaN）。"""
        out = C9Ops.ts_boll_position(constant, window=10)
        assert not out.isna().any()
        assert float(out.abs().max()) == pytest.approx(0.0)

    def test_ts_ma_diff_constant_zero(self, constant):
        """常数序列双均线差=0。"""
        assert float(C9Ops.ts_ma_diff(constant, short=5, long=20).abs().max()) == pytest.approx(0.0)

    def test_ts_vol_shrink_constant_zero(self, constant):
        """常数序列波动收缩度兜底 0。"""
        assert float(C9Ops.ts_vol_shrink(constant, short=5, long=20).abs().max()) == pytest.approx(0.0)

    def test_ts_tail_risk_constant_zero(self, constant):
        """常数序列尾部风险=0。"""
        assert float(C9Ops.ts_tail_risk(constant, window=10, q=0.05).abs().max()) == pytest.approx(0.0)


# ─── L2 截面算子（5）────────────────────────────────────────


class TestC9CrossSectionOps:
    """L2 截面（单序列滚动语义）功能与边界。"""

    def test_cs_winsor_flag_constant_zero(self, constant):
        """常数序列无极端值标记。"""
        assert float(C9Ops.cs_winsor_flag(constant, window=10).sum()) == pytest.approx(0.0)

    def test_cs_winsor_flag_detects_outlier(self, series):
        """含离群值序列存在标记=1。"""
        s = series.copy()
        s.iloc[50] = series.max() + 50.0
        assert float(C9Ops.cs_winsor_flag(s, window=20, k=2.0).sum()) > 0

    def test_cs_demean_ratio_constant_zero(self, constant):
        """常数序列去均值比率=0。"""
        assert float(C9Ops.cs_demean_ratio(constant, window=10).abs().max()) == pytest.approx(0.0)

    def test_cs_rank_norm_range(self, series, constant):
        """rank 归一化值域 [-1,1]；常数序列接近 0（tie rank 均值偏差 <0.05）。"""
        out = C9Ops.cs_rank_norm(series)
        assert float(out.min()) >= -1.0 and float(out.max()) <= 1.0
        assert float(C9Ops.cs_rank_norm(constant).abs().max()) < 0.05

    def test_cs_med_ratio_constant_zero(self, constant):
        """常数序列中位数比=0。"""
        assert float(C9Ops.cs_med_ratio(constant, window=10).abs().max()) == pytest.approx(0.0)

    def test_cs_extreme_gap_constant_zero(self, constant):
        """常数序列距极值缺口兜底 0（无 NaN）。"""
        out = C9Ops.cs_extreme_gap(constant, window=10)
        assert not out.isna().any()
        assert float(out.abs().max()) == pytest.approx(0.0)


# ─── L3 条件算子（4）────────────────────────────────────────


class TestC9ConditionOps:
    """L3 条件算子功能与边界。"""

    def test_where_between_selects(self):
        """区间内取 a、区间外取 b。"""
        s = pd.Series([-2.0, 0.0, 5.0, 10.0])
        out = C9Ops.where_between(s, lo=0.0, hi=6.0, a=1.0, b=0.0)
        assert out.tolist() == [0.0, 1.0, 1.0, 0.0]

    def test_cross_above_detects(self):
        """上穿阈值仅在穿越日触发。"""
        s = pd.Series([0.0, 0.0, 1.0, 1.0])
        out = C9Ops.cross_above(s, threshold=0.5)
        assert out.tolist() == [0.0, 0.0, 1.0, 0.0]

    def test_cross_below_detects(self):
        """下穿阈值仅在穿越日触发。"""
        s = pd.Series([1.0, 1.0, 0.0, 0.0])
        out = C9Ops.cross_below(s, threshold=0.5)
        assert out.tolist() == [0.0, 0.0, 1.0, 0.0]

    def test_momentum_break_constant_zero(self, constant):
        """常数序列无动量突破。"""
        assert float(C9Ops.momentum_break(constant, window=5).sum()) == pytest.approx(0.0)


# ─── L5 领域算子（7）────────────────────────────────────────


class TestC9DomainOps:
    """L5 领域算子功能与边界。"""

    def test_vol_regime_ternary_and_constant_zero(self, constant, series):
        """波动率制度三态 ∈{-1,0,1}；常数序列全 0。"""
        out = C9Ops.vol_regime(series, window=20)
        assert set(pd.unique(out)).issubset({-1.0, 0.0, 1.0})
        assert float(C9Ops.vol_regime(constant, window=20).abs().max()) == pytest.approx(0.0)

    def test_mean_reversion_signal_constant_zero(self, constant):
        """常数序列均值回归触发全 0。"""
        assert float(C9Ops.mean_reversion_signal(constant, window=20).abs().max()) == pytest.approx(0.0)

    def test_price_volume_div_detects(self):
        """价升量缩/价跌量升方向相反 → 背离占比>0。"""
        close = pd.Series([1.0, 2.0, 3.0, 2.0, 1.0])
        volume = pd.Series([10.0, 8.0, 6.0, 9.0, 11.0])
        out = C9Ops.price_volume_div(close, volume, window=4)
        assert float(out.sum()) > 0

    def test_liquidity_dryup_detects(self):
        """量能骤降至均值一半以下 → 枯竭标记 1。"""
        volume = pd.Series([100.0, 100.0, 100.0, 10.0, 100.0])
        out = C9Ops.liquidity_dryup(volume, window=4)
        assert out.iloc[3] == pytest.approx(1.0)

    def test_self_corr_rising_positive(self, rising, constant):
        """严格递增序列 lag-1 自相关≈1；常数序列兜底 0。"""
        out = C9Ops.self_corr(rising, window=20)
        assert out.dropna().iloc[-1] == pytest.approx(1.0, abs=1e-6)
        assert float(C9Ops.self_corr(constant, window=20).abs().max()) == pytest.approx(0.0)

    def test_sign_entropy_alternating_high(self):
        """正负收益交替 → 方向熵≈1（完全无序）。"""
        s = pd.Series(np.array([1.0, -1.0] * 60))
        out = C9Ops.sign_entropy(s, window=20)
        assert float(out.iloc[-1]) > 0.9

    def test_reversal_strength_constant_zero(self, constant):
        """常数序列反转强度兜底 0。"""
        assert float(C9Ops.reversal_strength(constant, window=10).abs().max()) == pytest.approx(0.0)


# ─── 双注册表一致性 ─────────────────────────────────────────


class TestC9RegistryConsistency:
    """feature_ops（GP）与 expr_dsl 双注册表强制共享（C8 延续）。"""

    def test_c9_ops_registered_in_gp(self):
        """GP 注册表 category=c9 含全部 30 项。"""
        gp = OperatorRegistry()
        c9 = [op.name for op in gp.list_operators("c9")]
        assert len(c9) == 30
        assert set(c9) == set(C9_NAMES)

    def test_c9_ops_in_dsl(self):
        """DSL 注册表含全部 30 名（扩容后总数 ≥512）。"""
        dsl = build_registry()
        assert len(dsl) >= 512
        assert set(C9_NAMES).issubset(set(dsl))

    def test_verify_registry_consistent(self):
        """双注册表强制一致性校验通过（mismatched=0）。"""
        r = verify_registry_consistency()
        assert r["consistent"] is True
        assert len(r.get("mismatched", [])) == 0
        assert len(r.get("errors", [])) == 0

    def test_gp_metadata_by_name(self):
        """GP 注册表按名查询 C9 算子元数据（name/category/params 可用）。"""
        gp = OperatorRegistry()
        op = next(o for o in gp.list_operators() if o.name == "ts_bias_ma")
        assert op.name == "ts_bias_ma"
        assert op.category == "c9"
        assert "window" in op.params


# ─── 算子目录自动生成 ───────────────────────────────────────


class TestC9OperatorCatalog:
    """scripts/generate_operator_catalog.py 幂等 + 覆盖 C9。"""

    def _import_script(self):
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            import generate_operator_catalog as mod

            return mod
        finally:
            sys.path.remove(str(PROJECT_ROOT / "scripts"))

    def test_catalog_rows_cover_c9(self):
        """目录行数与 DSL 注册表一致（≥132）且覆盖全部 C9 算子。"""
        mod = self._import_script()
        rows = mod.build_catalog_rows()
        assert len(rows) >= 132
        names = {r["name"] for r in rows}
        assert set(C9_NAMES) <= names

    def test_catalog_rows_deterministic(self):
        """两次 build_catalog_rows 输出一致（确定性排序）。"""
        mod = self._import_script()
        assert mod.build_catalog_rows() == mod.build_catalog_rows()

    def test_render_yaml_contains_c9(self):
        """目录 YAML 包含 C9 算子条目与经济含义。"""
        mod = self._import_script()
        text = mod.render_yaml(mod.build_catalog_rows())
        assert "ts_bias_ma" in text
        assert "sign_entropy" in text
        assert "economic_meaning" in text

    def test_main_exit_code_zero(self):
        """脚本 main() 正常退出码 0（写入真实目录，幂等无副作用）。"""
        mod = self._import_script()
        assert mod.main() == 0
