"""
tests/factor_engine/test_portfolio_optimizer.py — GAP-F07 组合优化器测试。

覆盖:
    1. risk_parity: 权重满足杠杆/集中度约束 + 风险平价特性（低波动资产权重更高）
    2. mean_variance: 需 expected_returns；无则抛 ValueError；权重满足杠杆上限
    3. 换手约束 / VaR 约束生效
    4. scipy 降级路径（numpy 近似仍满足约束）
    5. 输入校验（非方阵协方差 / 维度不匹配 / 空资产 / 非法模式）
    6. synthesize_signals optimizer 模式接入 + 缺 returns_matrix 回退
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.portfolio_optimizer import (
    OptimizerConfig,
    PortfolioOptimizer,
)
from fts.factor_engine.risk_model import RiskModelEstimator
from fts.factor_engine.portfolio_loop import synthesize_signals


def _make_cov(n: int = 4) -> np.ndarray:
    """构造正定对角协方差矩阵（波动率差异明显，风险平价解可解析验证）。"""
    vols = np.array([0.05, 0.10, 0.15, 0.20])[:n]
    return np.diag(vols**2)


class TestRiskParity:
    def test_weights_satisfy_constraints(self) -> None:
        """权重满足: 非负 + 集中度上限 + 杠杆上限。"""
        cfg = OptimizerConfig(mode="risk_parity", max_leverage=1.0, max_weight=0.4)
        w = PortfolioOptimizer(cfg).optimize(_make_cov())
        assert w.shape == (4,)
        assert np.all(w >= 0)
        assert np.all(w <= cfg.max_weight + 1e-9)
        assert np.sum(w) <= cfg.max_leverage + 1e-6

    def test_lower_vol_asset_gets_higher_weight(self) -> None:
        """风险平价: 低波动资产权重应高于高波动资产（对角协方差下 w∝1/σ）。"""
        cfg = OptimizerConfig(mode="risk_parity")
        w = PortfolioOptimizer(cfg).optimize(_make_cov())
        # 资产0波动率最低(0.05)，权重应最高
        assert w[0] > w[3]
        # 与解析解 1/σ 单调一致
        assert np.all(np.diff(w) < 0)

    def test_concentration_cap(self) -> None:
        """集中度约束: 单资产权重不超过 max_weight。"""
        cfg = OptimizerConfig(mode="risk_parity", max_weight=0.3)
        w = PortfolioOptimizer(cfg).optimize(_make_cov())
        assert np.all(w <= 0.3 + 1e-9)

    def test_leverage_cap(self) -> None:
        """杠杆约束: Σw <= max_leverage。"""
        cfg = OptimizerConfig(mode="risk_parity", max_leverage=1.5, max_weight=0.5)
        w = PortfolioOptimizer(cfg).optimize(_make_cov())
        assert np.sum(w) <= 1.5 + 1e-6


class TestMeanVariance:
    def test_requires_expected_returns(self) -> None:
        """mean_variance 无 expected_returns → ValueError。"""
        opt = PortfolioOptimizer(OptimizerConfig(mode="mean_variance"))
        with pytest.raises(ValueError, match="expected_returns"):
            opt.optimize(_make_cov())

    def test_solves_with_expected_returns(self) -> None:
        """有 expected_returns 时返回满足约束的权重。"""
        mu = np.array([0.08, 0.06, 0.04, 0.02])
        cfg = OptimizerConfig(mode="mean_variance", risk_aversion=2.0)
        w = PortfolioOptimizer(cfg).optimize(_make_cov(), expected_returns=mu)
        assert np.all(w >= 0)
        # 均值方差最优解不一定满杠杆，但须满足杠杆上限
        assert np.sum(w) <= cfg.max_leverage + 1e-6

    def test_expected_returns_dim_mismatch(self) -> None:
        """expected_returns 维度与资产数不一致 → ValueError。"""
        opt = PortfolioOptimizer(OptimizerConfig(mode="mean_variance"))
        with pytest.raises(ValueError, match="不一致"):
            opt.optimize(_make_cov(3), expected_returns=np.array([0.1, 0.1, 0.1, 0.1]))


class TestConstraints:
    def test_turnover_cap(self) -> None:
        """换手约束: Σ|w - prev| <= turnover_cap。"""
        prev = np.array([0.25, 0.25, 0.25, 0.25])
        cfg = OptimizerConfig(mode="risk_parity", turnover_cap=0.05)
        w = PortfolioOptimizer(cfg).optimize(_make_cov(), prev_weights=prev)
        assert np.sum(np.abs(w - prev)) <= 0.05 + 1e-6

    def test_var_ceiling(self) -> None:
        """VaR 约束: 组合 95% VaR <= var_ceiling。"""
        rng = np.random.default_rng(3)
        returns = rng.normal(0.0, 0.02, size=(500, 4))
        cfg = OptimizerConfig(mode="risk_parity", var_ceiling=0.02)
        w = PortfolioOptimizer(cfg).optimize(_make_cov(), returns=returns)
        pr = returns @ w
        var = -np.mean(pr) - 1.645 * np.std(pr, ddof=1)
        assert var <= 0.02 + 1e-6

    def test_prev_weights_dim_mismatch(self) -> None:
        """prev_weights 维度不一致 → ValueError。"""
        opt = PortfolioOptimizer()
        with pytest.raises(ValueError, match="不一致"):
            opt.optimize(_make_cov(3), prev_weights=np.array([0.5, 0.5, 0.0, 0.0]))


class TestDegradation:
    def test_numpy_fallback_without_scipy(self) -> None:
        """无 scipy 时 numpy 降级路径仍返回满足约束的权重。"""
        import fts.factor_engine.portfolio_optimizer as po_mod

        cfg = OptimizerConfig(mode="risk_parity", max_weight=0.4)
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(po_mod, "_HAS_SCIPY", False)
            opt = PortfolioOptimizer(cfg)
            assert not opt.scipy_available
            w = opt.optimize(_make_cov())
        finally:
            mp.undo()
        assert np.all(w >= 0)
        assert np.all(w <= 0.4 + 1e-9)
        assert np.sum(w) <= cfg.max_leverage + 1e-6

    def test_numpy_fallback_mean_variance(self) -> None:
        """无 scipy 时均值方差降级（解析解 + 投影）。"""
        import fts.factor_engine.portfolio_optimizer as po_mod

        mu = np.array([0.08, 0.06, 0.04, 0.02])
        cfg = OptimizerConfig(mode="mean_variance")
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(po_mod, "_HAS_SCIPY", False)
            w = PortfolioOptimizer(cfg).optimize(_make_cov(), expected_returns=mu)
        finally:
            mp.undo()
        assert np.all(w >= 0)
        assert np.sum(w) <= cfg.max_leverage + 1e-6


class TestValidation:
    def test_non_square_cov_raises(self) -> None:
        """非方阵协方差 → ValueError。"""
        with pytest.raises(ValueError, match="方阵"):
            PortfolioOptimizer().optimize(np.ones((3, 4)))

    def test_empty_assets(self) -> None:
        """零资产返回空权重。"""
        assert PortfolioOptimizer().optimize(np.zeros((0, 0))).size == 0

    def test_invalid_mode_raises(self) -> None:
        """不支持的 mode → ValueError。"""
        with pytest.raises(ValueError, match="模式"):
            PortfolioOptimizer(OptimizerConfig(mode="kelly"))

    def test_cov_inf_nan_handled(self) -> None:
        """权重输出不包含 NaN/Inf。"""
        cov = _make_cov()
        cov[0, 0] = np.nan
        w = PortfolioOptimizer().optimize(cov)
        assert np.all(np.isfinite(w))


class TestSynthesizeSignalsOptimizer:
    def _factors(self) -> list[dict]:
        return [
            {
                "factor_id": "f1",
                "name": "mom",
                "sharpe": 1.8,
                "ic": 0.05,
                "turnover": 0.3,
                "decay_6m": 0.9,
            },
            {
                "factor_id": "f2",
                "name": "carry",
                "sharpe": 1.5,
                "ic": 0.04,
                "turnover": 0.2,
                "decay_6m": 0.7,
            },
            {
                "factor_id": "f3",
                "name": "value",
                "sharpe": 1.2,
                "ic": 0.03,
                "turnover": 0.4,
                "decay_6m": 0.8,
            },
        ]

    def test_optimizer_mode_with_returns_matrix(self) -> None:
        """optimizer 模式 + returns_matrix（列名=factor_id）→ 权重满足杠杆约束且非负。"""
        rng = np.random.default_rng(5)
        returns = pd.DataFrame(rng.normal(0.0, 0.01, size=(300, 3)), columns=["f1", "f2", "f3"])
        factors = self._factors()
        signals, _, _ = synthesize_signals(factors, "optimizer", returns_matrix=returns)
        assert len(signals) == 3
        total = sum(s["weight"] for s in signals)
        assert total <= 1.0 + 1e-6
        assert all(s["weight"] >= 0 for s in signals)
        # GAP-L303: 应真正走 optimizer 路径（非回退）——权重与 sharpe 归一化不同
        sharpe_total = sum(f["sharpe"] for f in factors)
        sharpe_weights = [f["sharpe"] / sharpe_total for f in factors]
        opt_weights = [s["weight"] for s in signals]
        assert not np.allclose(opt_weights, sharpe_weights)

    def test_optimizer_mode_mvo(self) -> None:
        """optimizer_mode="mvo" → 均值方差目标生效（权重分布不同于 risk_parity）。"""
        rng = np.random.default_rng(8)
        returns = pd.DataFrame(rng.normal(0.0, 0.01, size=(300, 3)), columns=["f1", "f2", "f3"])
        factors = self._factors()
        rp_signals, _, _ = synthesize_signals(
            factors,
            "optimizer",
            returns_matrix=returns,
            optimizer_mode="risk_parity",
        )
        mvo_signals, _, _ = synthesize_signals(
            factors,
            "optimizer",
            returns_matrix=returns,
            optimizer_mode="mvo",
            optimizer_config={"max_weight": 1.0},  # 放开集中度钳制以凸显目标差异
        )
        rp_w = np.array([s["weight"] for s in rp_signals])
        mvo_w = np.array([s["weight"] for s in mvo_signals])
        assert not np.allclose(rp_w, mvo_w)

    def test_optimizer_mode_fallback_without_matrix(self) -> None:
        """无 returns_matrix → 回退 sharpe_weight（不抛异常）。"""
        factors = self._factors()
        signals, _, _ = synthesize_signals(factors, "optimizer")
        assert len(signals) == 3
        # 回退路径权重按 sharpe 归一化
        total = sum(s["weight"] for s in signals)
        assert abs(total - 1.0) <= 1e-6

    def test_optimizer_mode_wrong_column_count(self) -> None:
        """returns_matrix 列数与因子数不一致 → 回退。"""
        rng = np.random.default_rng(6)
        returns = pd.DataFrame(rng.normal(size=(100, 2)))  # 2 列 vs 3 因子
        factors = self._factors()
        signals, _, _ = synthesize_signals(factors, "optimizer", returns_matrix=returns)
        assert len(signals) == 3


class TestNeutralization:
    """GAP-L304 暴露中性化约束。"""

    def _make_cov(self, n: int = 3) -> np.ndarray:
        rng = np.random.default_rng(9)
        x = rng.normal(size=(200, n))
        return np.cov(x.T)

    def test_exposure_constraint_enforced(self) -> None:
        """启用 neutralization + exposure_matrix → |B'w| ≤ tolerance。"""
        cfg = OptimizerConfig(
            mode="mean_variance",
            neutralization="industry",
            exposure_tolerance=0.02,
            max_leverage=1.0,
            max_weight=0.6,
        )
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov()
        mu = np.array([0.05, 0.03, 0.01])
        # 暴露矩阵: 因子0 对行业A 暴露 1，因子1 对行业B 暴露 1，因子2 中性
        exposure = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
        w = opt.optimize(cov=cov, expected_returns=mu, exposure_matrix=exposure)
        exposure_actual = exposure.T @ w
        assert np.all(np.abs(exposure_actual) <= cfg.exposure_tolerance + 1e-6)

    def test_exposure_ignored_without_neutralization(self) -> None:
        """传入 exposure_matrix 但 neutralization=None → 忽略（不抛异常）。"""
        cfg = OptimizerConfig(mode="mean_variance")
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov()
        mu = np.array([0.05, 0.03, 0.01])
        exposure = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
        w = opt.optimize(cov=cov, expected_returns=mu, exposure_matrix=exposure)
        assert np.all(np.isfinite(w))

    def test_exposure_dimension_mismatch_raises(self) -> None:
        """暴露矩阵行数 ≠ 资产数 → ValueError。"""
        cfg = OptimizerConfig(mode="mean_variance", neutralization="industry")
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov(n=3)
        mu = np.array([0.05, 0.03, 0.01])
        exposure = np.array([[1.0, 0.0]])  # 1 行 vs 3 资产
        with pytest.raises(ValueError, match="暴露矩阵"):
            opt.optimize(cov=cov, expected_returns=mu, exposure_matrix=exposure)

    def test_target_exposure_honored(self) -> None:
        """指定 target_exposure 时约束为 |B'w − target| ≤ tol。"""
        cfg = OptimizerConfig(
            mode="mean_variance",
            neutralization="style",
            exposure_tolerance=0.02,
            max_leverage=1.0,
            max_weight=0.6,
        )
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov()
        mu = np.array([0.05, 0.03, 0.01])
        exposure = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
        target = np.array([0.2, 0.2])
        w = opt.optimize(
            cov=cov,
            expected_returns=mu,
            exposure_matrix=exposure,
            target_exposure=target,
        )
        exposure_actual = exposure.T @ w
        assert np.all(np.abs(exposure_actual - target) <= cfg.exposure_tolerance + 1e-6)


class TestCostAndCapacity:
    """GAP-L305 换手惩罚 / 成本项 / 容量约束。"""

    def _make_cov(self, n: int = 3) -> np.ndarray:
        rng = np.random.default_rng(11)
        x = rng.normal(size=(300, n))
        return np.cov(x.T)

    def test_turnover_penalty_shrinks_change(self) -> None:
        """换手惩罚生效：设 penalty>0 时权重变化应小于 penalty=0。"""
        prev = np.array([0.5, 0.3, 0.2])
        rng = np.random.default_rng(12)
        returns = pd.DataFrame(rng.normal(0.0, 0.01, size=(300, 3)), columns=["f1", "f2", "f3"])
        cov = RiskModelEstimator().estimate(returns).cov
        mu = np.array([0.08, 0.04, 0.02])

        cfg_plain = OptimizerConfig(mode="mean_variance", max_weight=1.0)
        cfg_penalty = OptimizerConfig(mode="mean_variance", max_weight=1.0, turnover_penalty=0.01)
        w_plain = PortfolioOptimizer(cfg_plain).optimize(cov=cov, expected_returns=mu, prev_weights=prev)
        w_penalty = PortfolioOptimizer(cfg_penalty).optimize(cov=cov, expected_returns=mu, prev_weights=prev)
        t_plain = float(np.sum(np.abs(w_plain - prev)))
        t_penalty = float(np.sum(np.abs(w_penalty - prev)))
        assert t_penalty <= t_plain + 1e-6

    def test_cost_bps_penalty_reduces_mvo_move(self) -> None:
        """成本项（cost_bps_per_turnover）入目标：权重变化被抑制。"""
        prev = np.array([0.5, 0.3, 0.2])
        rng = np.random.default_rng(13)
        returns = pd.DataFrame(rng.normal(0.0, 0.01, size=(300, 3)), columns=["f1", "f2", "f3"])
        cov = RiskModelEstimator().estimate(returns).cov
        mu = np.array([0.08, 0.04, 0.02])

        cfg_plain = OptimizerConfig(mode="mean_variance", max_weight=1.0)
        cfg_cost = OptimizerConfig(mode="mean_variance", max_weight=1.0, cost_bps_per_turnover=500.0)
        w_plain = PortfolioOptimizer(cfg_plain).optimize(cov=cov, expected_returns=mu, prev_weights=prev)
        w_cost = PortfolioOptimizer(cfg_cost).optimize(cov=cov, expected_returns=mu, prev_weights=prev)
        assert float(np.sum(np.abs(w_cost - prev))) <= float(np.sum(np.abs(w_plain - prev))) + 1e-6

    def test_capacity_limits_respected(self) -> None:
        """容量上限生效：w_i <= capacity_limits_i。"""
        cfg = OptimizerConfig(mode="mean_variance", max_weight=0.8, max_leverage=1.0)
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov()
        mu = np.array([0.09, 0.02, 0.01])
        cap = np.array([0.2, 0.4, 0.6])
        w = opt.optimize(cov=cov, expected_returns=mu, capacity_limits=cap)
        assert np.all(w <= cap + 1e-9)

    def test_capacity_limits_dim_mismatch(self) -> None:
        """容量上限长度 ≠ 资产数 → ValueError。"""
        opt = PortfolioOptimizer(OptimizerConfig(mode="mean_variance"))
        with pytest.raises(ValueError, match="容量"):
            opt.optimize(self._make_cov(), expected_returns=np.ones(3), capacity_limits=np.array([0.5, 0.5]))

    def test_capacity_limits_risk_parity(self) -> None:
        """风险平价路径容量约束同样生效。"""
        cfg = OptimizerConfig(mode="risk_parity", max_weight=0.8, max_leverage=1.0)
        opt = PortfolioOptimizer(cfg)
        cov = self._make_cov()
        cap = np.array([0.15, 0.5, 0.6])
        w = opt.optimize(cov=cov, capacity_limits=cap)
        assert np.all(w <= cap + 1e-9)

    def test_capacity_limits_in_optimizer_mode(self) -> None:
        """synthesize_signals optimizer 模式透传 capacity_limits。"""
        rng = np.random.default_rng(14)
        returns = pd.DataFrame(rng.normal(0.0, 0.01, size=(300, 3)), columns=["f1", "f2", "f3"])
        factors = [
            {"factor_id": "f1", "name": "mom", "sharpe": 1.8, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.9},
            {"factor_id": "f2", "name": "carry", "sharpe": 1.5, "ic": 0.04, "turnover": 0.2, "decay_6m": 0.7},
            {"factor_id": "f3", "name": "value", "sharpe": 1.2, "ic": 0.03, "turnover": 0.4, "decay_6m": 0.8},
        ]
        cap = [0.2, 0.4, 0.6]
        signals, _, _ = synthesize_signals(
            factors,
            "optimizer",
            returns_matrix=returns,
            optimizer_config={"max_weight": 0.8, "capacity_limits": cap},
        )
        for s, c in zip(signals, cap):
            assert s["weight"] <= c + 1e-6
