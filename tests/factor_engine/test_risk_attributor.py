"""
tests/factor_engine/test_risk_attributor.py — GAP-L307 风险归因分析器测试。

覆盖:
    1. 因子贡献度与理论一致（已知权重合成组合，误差 < 1e-6）
    2. VaR / ES 数值断言（历史模拟法）
    3. 空数据 / 样本不足降级
    4. 暴露分析

版本: v1.0.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.risk_attributor import RiskAttributor


class TestFactorContribution:
    """因子贡献度 — 已知权重合成组合。"""

    def test_contribution_matches_theory(self) -> None:
        """单因子主导组合：贡献度 = 1（权重传入，方差分解，误差 < 1e-6）。"""
        rng = np.random.default_rng(1)
        n = 200
        f1 = rng.normal(0.0, 0.01, size=n)
        f2 = rng.normal(0.0, 0.01, size=n)
        fr = pd.DataFrame({"f1": f1, "f2": f2})
        w = np.array([1.0, 0.0])  # 纯 f1
        pf = pd.Series(fr.values @ w)
        attr = RiskAttributor().attribute(pf, fr, weights={"f1": 1.0, "f2": 0.0})
        assert attr.factor_contributions["f1"] == pytest.approx(1.0, abs=1e-6)
        assert attr.factor_contributions["f2"] == pytest.approx(0.0, abs=1e-6)

    def test_equal_weight_contribution(self) -> None:
        """两个独立因子等权：贡献度各约 0.5（Σ贡献=1 方差分解）。"""
        rng = np.random.default_rng(2)
        n = 300
        fr = pd.DataFrame(rng.normal(0.0, 0.01, size=(n, 2)), columns=["a", "b"])
        w = np.array([0.5, 0.5])
        pf = pd.Series(fr.values @ w)
        attr = RiskAttributor().attribute(pf, fr, weights={"a": 0.5, "b": 0.5})
        # 独立同分布 → 贡献度近似相等，且合计 ≈ 1
        assert abs(attr.factor_contributions["a"] - 0.5) < 0.05
        assert abs(attr.factor_contributions["b"] - 0.5) < 0.05
        assert abs(sum(attr.factor_contributions.values()) - 1.0) < 1e-6

    def test_insufficient_samples_returns_empty(self) -> None:
        """样本 < 20 → 贡献度为 {}。"""
        fr = pd.DataFrame(np.random.default_rng(3).normal(size=(10, 2)))
        pf = pd.Series(np.random.default_rng(4).normal(size=10))
        attr = RiskAttributor().attribute(pf, fr)
        assert attr.factor_contributions == {}


class TestVarEs:
    """VaR / ES 数值断言。"""

    def test_var_negative_for_losses(self) -> None:
        """历史模拟法：VaR 为负值（亏损侧）。"""
        rng = np.random.default_rng(5)
        returns = pd.Series(rng.normal(-0.001, 0.01, size=500))
        attr = RiskAttributor().attribute(returns)
        assert attr.var_95 < 0
        assert attr.var_99 < attr.var_95  # 99% 分位更极端

    def test_es_more_extreme_than_var(self) -> None:
        """ES 95（尾部均值）应 ≤ VaR 95。"""
        rng = np.random.default_rng(6)
        returns = pd.Series(rng.normal(0.0, 0.02, size=1000))
        attr = RiskAttributor().attribute(returns)
        assert attr.es_95 <= attr.var_95 + 1e-12

    def test_realized_vol_positive(self) -> None:
        """年化波动率为正。"""
        rng = np.random.default_rng(7)
        returns = pd.Series(rng.normal(0.0, 0.01, size=500))
        attr = RiskAttributor().attribute(returns)
        assert attr.realized_vol > 0


class TestDegradation:
    """空数据 / 边界降级。"""

    def test_empty_returns(self) -> None:
        """空收益序列 → 全 0 报告（不崩溃）。"""
        attr = RiskAttributor().attribute(pd.Series(dtype=float))
        assert attr.var_95 == 0.0
        assert attr.realized_vol == 0.0
        assert attr.factor_contributions == {}

    def test_single_value(self) -> None:
        """单观测 → 波动率为 0，不抛异常。"""
        attr = RiskAttributor().attribute(pd.Series([0.01]))
        assert attr.realized_vol == 0.0

    def test_exposure_analysis(self) -> None:
        """暴露分析：平均绝对暴露。"""
        holdings = pd.DataFrame(
            {
                "stock_a": [0.5, -0.5],
                "stock_b": [0.2, 0.1],
            }
        )
        attr = RiskAttributor().attribute(
            pd.Series([0.01, -0.01]),
            holdings=holdings,
        )
        assert attr.exposures["stock_a"] == pytest.approx(0.5)
        assert attr.exposures["stock_b"] == pytest.approx(0.15)


__all__: list[str] = []
