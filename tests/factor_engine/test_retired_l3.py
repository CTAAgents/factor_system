"""tests/factor_engine/test_retired_l3.py — 阶段 2 L3 组合侧退役登记测试（plans/57 §4.1/§5.3）。"""

from __future__ import annotations

import warnings

from fts.factor_engine.retired_l3 import (
    retired_registry,
    is_retired,
    warn_if_retired,
)


class TestRetiredRegistry:
    def test_registry_nonempty(self):
        """§4.1 退役清单已登记（≥35 项）。"""
        reg = retired_registry()
        assert len(reg) >= 35

    def test_pipeline_functions_registered(self):
        """futures_signal_pipeline 组合侧函数在清单内。"""
        names = {e["name"] for e in retired_registry()}
        assert {"_compute_composite_scores", "_compute_per_variety_weights",
                "_load_l3_combo_weights", "_compute_holdout_validation"} <= names

    def test_portfolio_functions_registered(self):
        """portfolio_loop 策略侧函数在清单内。"""
        names = {e["name"] for e in retired_registry()}
        assert {"synthesize_signals", "build_combo", "_cap_safety_valve",
                "_validate_combo_sharpe", "_run_sharpe_randomization_test",
                "decay_test", "_greedy_select_by_correlation"} <= names

    def test_migrated_modules_registered(self):
        """整体迁移模块在清单内（标记弃用）。"""
        names = {e["name"] for e in retired_registry()}
        assert {"weight_learning", "capital_allocator", "regime_crowding"} <= names

    def test_is_retired(self):
        assert is_retired("build_combo") is True
        assert is_retired("momentum_20") is False

    def test_warn_if_retired_emits(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_retired("build_combo")
        assert len(w) == 1
        assert "退役" in str(w[0].message)

    def test_warn_if_retired_unknown_noop(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_retired("not_a_function")
        assert len(w) == 0


class TestRetiredModulesImport:
    def test_migrated_modules_importable(self):
        """退役模块仍可导入（存量调用点兼容，迁移期不删除）。"""
        from fts.factor_engine import capital_allocator, regime_crowding, weight_learning  # noqa: F401

        assert hasattr(weight_learning, "risk_adjust_weights")
        assert hasattr(capital_allocator, "CapitalAllocator")
        assert hasattr(regime_crowding, "compute_crowding_signals")
