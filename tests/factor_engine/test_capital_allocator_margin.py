"""tests/factor_engine/test_capital_allocator_margin.py — 保证金建模测试（GAP-F09，v2.60.0）。

覆盖:
1. 无保证金率配置时不改变分配行为（回归兼容）
2. 保证金占用计算（含未配置品种默认 0.10）
3. 保证金超限 → 权重等比缩放至 max_margin_usage + 强平风险告警标记
4. 配置 margin_rate_map 缺省读取
"""

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.capital_allocator import CapitalAllocator


def _make_returns(n: int = 300, n_assets: int = 4, seed: int = 7) -> pd.DataFrame:
    """构造多资产收益率面板（确定性种子）。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({f"A{i}": rng.normal(0.0005, 0.01, n) for i in range(n_assets)})


class TestGapF09MarginModeling:
    """GAP-F09: 保证金占用约束 + 强平风险告警。"""

    def test_no_margin_rates_keeps_behavior(self):
        """未配置保证金率时不改变分配行为（回归兼容）。"""
        returns = _make_returns()
        alloc = CapitalAllocator()
        result = alloc.allocate(returns, total_capital=1_000_000, mode="fixed")
        assert result.weights["portfolio"] == pytest.approx(1.0)
        assert result.details.get("margin_usage") is None

    def test_margin_usage_calculated(self):
        """配置保证金率时应计算保证金占用。"""
        returns = _make_returns()
        alloc = CapitalAllocator()
        margin_rates = {"A0": 0.10, "A1": 0.10, "A2": 0.10, "A3": 0.10}
        result = alloc.allocate(
            returns,
            total_capital=1_000_000,
            mode="risk_parity",
            margin_rates=margin_rates,
        )
        # risk_parity 权重和为 1.0，保证金占用 = Σ w_i × 0.10 = 0.10
        assert result.details["margin_usage"] == pytest.approx(0.10, abs=1e-6)
        assert result.details["margin_scaled"] is False

    def test_margin_unconfigured_uses_default(self):
        """未配置品种用默认保证金率 0.10。"""
        returns = _make_returns()
        alloc = CapitalAllocator()
        result = alloc.allocate(
            returns,
            total_capital=1_000_000,
            mode="fixed",
            margin_rates={"A0": 0.05},  # 表中无 portfolio → 默认 0.10
        )
        # fixed 模式权重 1.0 × 默认 0.10 = 0.10
        assert result.details["margin_usage"] == pytest.approx(0.10, abs=1e-6)

    def test_margin_over_limit_scales_weights(self):
        """保证金占用超限时权重应等比缩放至 max_margin_usage。"""
        returns = _make_returns()
        alloc = CapitalAllocator()
        # 高保证金品种：margin=0.20，fixed 模式权重 1.0 → 占用 0.20 > 0.10 上限
        margin_rates = {"portfolio": 0.20}
        result = alloc.allocate(
            returns,
            total_capital=1_000_000,
            mode="fixed",
            margin_rates=margin_rates,
            max_margin_usage=0.10,
        )
        assert result.details["margin_scaled"] is True
        assert result.details["margin_usage"] == pytest.approx(0.10, abs=1e-6)
        # 权重缩放：0.10 / 0.20 = 0.50
        assert result.weights["portfolio"] == pytest.approx(0.50)
        assert result.allocated_capital["portfolio"] == pytest.approx(500_000.0)

    def test_margin_under_limit_no_scaling(self):
        """保证金占用未超限时不缩放。"""
        returns = _make_returns()
        alloc = CapitalAllocator()
        margin_rates = {"portfolio": 0.05}
        result = alloc.allocate(
            returns,
            total_capital=1_000_000,
            mode="fixed",
            margin_rates=margin_rates,
            max_margin_usage=0.10,
        )
        assert result.details["margin_scaled"] is False
        assert result.weights["portfolio"] == pytest.approx(1.0)

    def test_margin_rates_from_config(self, monkeypatch):
        """margin_rates 缺省时读取配置 margin_rate_map。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(margin_rate_map={"portfolio": 0.20}),
        )
        returns = _make_returns()
        alloc = CapitalAllocator()
        result = alloc.allocate(
            returns,
            total_capital=1_000_000,
            mode="fixed",
            max_margin_usage=0.10,
        )
        assert result.details["margin_scaled"] is True
        assert result.weights["portfolio"] == pytest.approx(0.50)
