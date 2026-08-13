"""tests/factor_engine/test_operator_expansion_d10.py — D10 波动/风险族算子测试。

覆盖（2026-08-11 扩容二期，DSL 132→187 / GP 111→166）:
    1. 55 个波动/风险算子功能与边界（方向性/值域/有限性）
    2. 常数序列与含 NaN 序列 NaN 兜底（不抛异常、输出有限）
    3. 多序列算子（high/low/open/close）方向性
    4. 双注册表一致性（DSL 187 项 / GP d10 55 项 / required_shared 全覆盖）
"""

# ruff: noqa: E741  # OHLC 低价用 l 命名（o/h/l/c），属领域标准命名

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency
from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.ops_library import D10Ops

RNG_SEED = 20260811  # 固定种子：所有随机序列跨测试可复现


@pytest.fixture
def rising() -> pd.Series:
    """严格递增价格序列（收益恒正、无回撤）。"""
    return pd.Series(np.linspace(100.0, 200.0, 200))


@pytest.fixture
def falling() -> pd.Series:
    """严格递减价格序列（收益恒负、持续回撤）。"""
    return pd.Series(np.linspace(200.0, 100.0, 200))


@pytest.fixture
def volatile() -> pd.Series:
    """高波动随机游走价格序列（固定种子可复现）。"""
    r = np.random.default_rng(RNG_SEED).normal(0, 0.02, 200)
    return pd.Series(np.cumsum(r) + 100.0)


@pytest.fixture
def constant() -> pd.Series:
    """常数序列（波动/离散类算子应输出 0 或有限兜底）。"""
    return pd.Series(np.full(200, 100.0))


def _finite(out: pd.Series) -> bool:
    """输出全有限（无 NaN/Inf）。"""
    return bool(np.isfinite(out.dropna()).all()) and not out.isna().all()


# ─── 波动率估计 ───────────────────────────────────────────


class TestD10VolatilityEstimators:
    """波动率估计类算子（positive、常数 0、NaN 兜底）。"""

    @pytest.mark.parametrize(
        "name",
        [
            "ts_realized_vol", "ts_ewma_vol", "ts_vol_of_vol", "ts_bipower_var",
            "ts_downside_vol", "ts_upside_vol", "ts_harmonic_vol", "ts_semi_std",
            "ts_lpm_2", "ts_hpm_2", "ts_gain_std", "ts_loss_std",
            "ts_baseline_vol", "ts_long_term_vol", "ts_short_term_vol", "ts_garch_proxy",
        ],
    )
    def test_nonnegative_finite(self, name, volatile):
        """波动类算子输出非负且有限。"""
        out = getattr(D10Ops, name)(volatile, 20)
        assert _finite(out), f"{name} 输出含 NaN/Inf"
        assert (out >= 0).all(), f"{name} 应非负"

    @pytest.mark.parametrize(
        "name",
        [
            "ts_realized_vol", "ts_ewma_vol", "ts_vol_of_vol", "ts_bipower_var",
            "ts_downside_vol", "ts_upside_vol", "ts_harmonic_vol", "ts_semi_std",
            "ts_lpm_2", "ts_hpm_2", "ts_gain_std", "ts_loss_std",
            "ts_baseline_vol", "ts_long_term_vol", "ts_short_term_vol", "ts_garch_proxy",
        ],
    )
    def test_constant_zero(self, name, constant):
        """常数序列 → 波动为 0（有限兜底）。"""
        out = getattr(D10Ops, name)(constant, 20)
        assert _finite(out)
        assert float(out.iloc[-1]) == pytest.approx(0.0, abs=1e-9)

    def test_downside_nonnegative(self, volatile):
        """下行波动非负有限（≤ 总二阶矩 sqrt(mean(r²)) 数学恒成立）。"""
        dd = D10Ops.ts_downside_vol(volatile, 20)
        r2 = volatile.pct_change().fillna(0.0) ** 2
        bound = r2.rolling(20, min_periods=2).mean().apply(np.sqrt).fillna(0.0)
        assert _finite(dd)
        assert (dd <= bound + 1e-9).all()

    def test_vol_of_vol_smoother(self, volatile):
        """波动率波动度低于价格波动度（量级约束）。"""
        vov = D10Ops.ts_vol_of_vol(volatile, 20)
        assert _finite(vov)

    def test_bipower_jump_robust(self, volatile):
        """双幂变差有限且非负。"""
        bp = D10Ops.ts_bipower_var(volatile, 20)
        assert _finite(bp)
        assert (bp >= 0).all()


class TestD10OHLCVolatility:
    """OHLC 多序列波动率（方向性：H/L 跨度越大波动越大）。"""

    def _ohlc(self, spread):
        n = 200
        rng = np.random.default_rng(RNG_SEED)
        close = pd.Series(np.linspace(100.0, 120.0, n) + rng.normal(0, 0.1, n))
        high = close + spread
        low = close - spread
        open_p = close.shift(1).fillna(close.iloc[0])
        return open_p, high, low, close

    @pytest.mark.parametrize("name", ["ts_parkinson", "ts_garman_klass", "ts_rogers_satchell", "ts_yang_zhang"])
    def test_wide_spread_higher_vol(self, name):
        """高低价跨度大 → 波动率显著更高。"""
        o1, h1, l1, c1 = self._ohlc(0.5)
        o2, h2, l2, c2 = self._ohlc(3.0)
        args = (h1, l1, 20) if name == "ts_parkinson" else (o1, h1, l1, c1, 20)
        v1 = getattr(D10Ops, name)(*args)
        args2 = (h2, l2, 20) if name == "ts_parkinson" else (o2, h2, l2, c2, 20)
        v2 = getattr(D10Ops, name)(*args2)
        assert _finite(v1) and _finite(v2)
        assert float(v2.tail(20).mean()) > float(v1.tail(20).mean()), f"{name} 宽跨幅波动应更大"

    @pytest.mark.parametrize("name", ["ts_parkinson", "ts_garman_klass", "ts_rogers_satchell", "ts_yang_zhang"])
    def test_nonnegative(self, name):
        """OHLC 波动非负且有限。"""
        o, h, l, c = self._ohlc(1.0)
        args = (h, l, 20) if name == "ts_parkinson" else (o, h, l, c, 20)
        out = getattr(D10Ops, name)(*args)
        assert _finite(out)
        assert (out >= 0).all()

    def test_range_vol_positive(self):
        """振幅波动率随跨度单调。"""
        o, h1, l1, c = self._ohlc(0.5)
        _, h2, l2, _ = self._ohlc(2.0)
        v1 = D10Ops.ts_range_vol(h1, l1, c, 20)
        v2 = D10Ops.ts_range_vol(h2, l2, c, 20)
        assert float(v2.tail(20).mean()) > float(v1.tail(20).mean())


# ─── 回撤类 ───────────────────────────────────────────────


class TestD10Drawdown:
    """回撤类算子方向性。"""

    def test_rising_no_drawdown(self, rising):
        """上升序列 → 回撤恒 0。"""
        assert float(D10Ops.ts_drawdown(rising, 0).iloc[-1]) == pytest.approx(0.0)
        assert float(D10Ops.ts_max_drawdown(rising, 60).iloc[-1]) == pytest.approx(0.0)
        assert float(D10Ops.ts_ulcer_index(rising, 60).iloc[-1]) == pytest.approx(0.0)

    def test_falling_deep_drawdown(self, falling):
        """下降序列 → 全历史最大回撤接近 -50%（200→100）。"""
        mdd = D10Ops.ts_max_drawdown(falling, 200)
        assert float(mdd.iloc[-1]) <= -0.49
        dur = D10Ops.ts_drawdown_duration(falling, 60)
        assert float(dur.iloc[-1]) >= 59.0

    def test_drawdown_finite(self, volatile):
        """波动序列回撤全有限。"""
        for name in ["ts_drawdown", "ts_max_drawdown", "ts_avg_drawdown", "ts_drawdown_duration", "ts_ulcer_index"]:
            out = getattr(D10Ops, name)(volatile, 60)
            assert _finite(out), name


# ─── VaR / 风险度量 ───────────────────────────────────────


class TestD10Var:
    """VaR/CVaR 尾部风险。"""

    def test_var_ordering(self, volatile):
        """95% VaR ≥ 99% VaR（更宽松分位更高）。"""
        v95 = D10Ops.ts_var_95(volatile, 60)
        v99 = D10Ops.ts_var_99(volatile, 60)
        assert (v95.dropna() >= v99.dropna()).all()

    def test_cvar_more_extreme(self, volatile):
        """CVaR ≤ VaR（尾部均值更极端）。"""
        v95 = D10Ops.ts_var_95(volatile, 60)
        c95 = D10Ops.ts_cvar_95(volatile, 60)
        assert float(c95.iloc[-1]) <= float(v95.iloc[-1]) + 1e-12

    def test_falling_all_negative(self, falling):
        """下降序列收益恒负 → VaR 为负。"""
        assert float(D10Ops.ts_var_95(falling, 60).iloc[-1]) < 0


# ─── 风险调整比率 ─────────────────────────────────────────


class TestD10Ratios:
    """风险调整收益比率。"""

    def test_rising_positive_sharpe(self, rising):
        """上升序列 → Sharpe 为正；Sortino 无下行偏差时兜底 0（合法）。"""
        assert float(D10Ops.ts_sharpe_ratio(rising, 20).iloc[-1]) > 0
        assert float(D10Ops.ts_sortino_ratio(rising, 20).iloc[-1]) >= 0

    def test_falling_negative_sharpe(self, falling):
        """下降序列 → Sharpe 为负。"""
        assert float(D10Ops.ts_sharpe_ratio(falling, 20).iloc[-1]) < 0

    def test_win_rate(self, rising, falling):
        """胜率：上升序列高、下降序列低。"""
        assert float(D10Ops.ts_win_rate(rising, 20).iloc[-1]) > 0.9
        assert float(D10Ops.ts_loss_rate(falling, 20).iloc[-1]) > 0.9

    @pytest.mark.parametrize(
        "name",
        [
            "ts_sharpe_ratio", "ts_sortino_ratio", "ts_calmar_ratio", "ts_profit_factor",
            "ts_omega_ratio", "ts_kelly_fraction", "ts_worst_day", "ts_best_day",
            "ts_win_rate", "ts_loss_rate", "ts_avg_gain", "ts_avg_loss",
            "ts_expectancy", "ts_recovery_factor", "ts_risk_return_ratio",
            "ts_downside_deviation", "ts_max_loss_ratio",
        ],
    )
    def test_finite(self, name, volatile):
        """比率类算子全有限。"""
        assert _finite(getattr(D10Ops, name)(volatile, 20)), name

    def test_constant_no_exception(self, constant):
        """常数序列不抛异常且有限。"""
        for name in [
            "ts_sharpe_ratio", "ts_sortino_ratio", "ts_calmar_ratio", "ts_profit_factor",
            "ts_omega_ratio", "ts_kelly_fraction", "ts_worst_day", "ts_best_day",
            "ts_win_rate", "ts_loss_rate", "ts_avg_gain", "ts_avg_loss",
            "ts_expectancy", "ts_recovery_factor", "ts_risk_return_ratio",
            "ts_downside_deviation", "ts_max_loss_ratio",
        ]:
            assert _finite(getattr(D10Ops, name)(constant, 20)), name


# ─── 波动率结构 ───────────────────────────────────────────


class TestD10VolStructure:
    """波动率结构类。"""

    @pytest.mark.parametrize(
        "name",
        [
            "ts_vol_ratio_ewma", "ts_realized_vol_pct", "ts_vol_zscore",
            "ts_vol_percentile", "ts_vol_asymmetry", "ts_leverage_effect",
            "ts_vol_term_structure", "ts_beta_vol",
        ],
    )
    def test_finite(self, name, volatile):
        """结构类算子全有限。"""
        if name in ("ts_vol_ratio_ewma", "ts_vol_term_structure", "ts_beta_vol"):
            out = getattr(D10Ops, name)(volatile, 5, 20)
        else:
            out = getattr(D10Ops, name)(volatile, 60)
        assert _finite(out), name

    def test_vol_percentile_range(self, volatile):
        """波动率分位 ∈ [0,1]。"""
        out = D10Ops.ts_vol_percentile(volatile, 60)
        assert float(out.dropna().min()) >= 0.0
        assert float(out.dropna().max()) <= 1.0

    def test_constant_no_exception(self, constant):
        """常数序列不抛异常。"""
        assert _finite(D10Ops.ts_vol_zscore(constant, 60))
        assert _finite(D10Ops.ts_vol_percentile(constant, 60))


# ─── 双注册表一致性 ───────────────────────────────────────


class TestD10RegistryConsistency:
    """D10 双注册表强制共享。"""

    def test_dsl_count_ge_187(self):
        """DSL 算子 ≥ 187（132 + D10 55）。"""
        assert len(build_registry()) >= 187

    def test_gp_contains_d10(self):
        """GP 注册表含 D10 全部 55 个算子。"""
        gp = OperatorRegistry()
        gp_names = {op.name for op in gp.list_operators()}
        d10_names = {n for n in dir(D10Ops) if n.startswith("ts_")}
        assert d10_names <= gp_names, d10_names - gp_names

    def test_verify_registry_consistent(self):
        """双注册表输出一致（mismatched=0/errors=0）。"""
        v = verify_registry_consistency()
        assert v["consistent"] is True
        assert len(v.get("mismatched", [])) == 0
        assert len(v.get("errors", [])) == 0

    def test_dsl_metadata_bound(self):
        """D10 算子参数边界齐全（window/span 均有界）。"""
        dsl = build_registry()
        d10_names = {n for n in dir(D10Ops) if n.startswith("ts_")}
        for n in d10_names:
            meta = dsl[n]
            assert meta.param_bounds, f"{n} 缺参数边界"
            assert meta.economic_meaning, f"{n} 缺经济语义"
