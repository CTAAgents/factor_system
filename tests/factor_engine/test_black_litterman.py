"""Black-Litterman 观点融合组合层测试（C3，v2.100.1）。

覆盖:
    - implied_returns 逆优化隐含收益
    - BL 闭式性质（空视图退化=先验 / Q=Pπ 后验=先验 / 观点方向一致 / 置信度单调）
    - 约束（集中度/杠杆）与维度校验 / 奇异兜底 / NaN 清理
    - build_auto_views 自动观点构建
    - synthesize_signals bl 模式集成（自动观点/显式观点/失败回退 risk_parity）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.black_litterman import (  # noqa: E402
    BlackLittermanConfig,
    black_litterman_weights,
    build_auto_views,
    implied_returns,
)
from fts.factor_engine.portfolio_loop import synthesize_signals  # noqa: E402


def _make_cov(n: int = 3, seed: int = 7) -> np.ndarray:
    """构造正定协方差矩阵（对角占优保证正定）。"""
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(n, n))
    cov = a @ a.T + np.eye(n) * 0.5
    return cov


def _make_factors(n: int = 3) -> list[dict]:
    ics = [0.08, 0.02, -0.05]
    return [
        {
            "factor_id": f"f{i}",
            "name": f"factor_{i}",
            "sharpe": 1.5 - 0.3 * i,
            "ic": abs(ics[i]),
            "_ic_raw": ics[i],
            "turnover": 0.1,
            "decay_6m": -0.01,
        }
        for i in range(n)
    ]


def _make_returns_matrix(factors: list[dict], rows: int = 30, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(factors)
    data = {f["factor_id"]: rng.normal(0.001, 0.02, rows) for f in factors}
    return pd.DataFrame(data)


class TestImpliedReturns:
    def test_formula(self) -> None:
        cov = _make_cov(3)
        w = np.array([0.5, 0.3, 0.2])
        pi = implied_returns(cov, w, risk_aversion=2.0)
        np.testing.assert_allclose(pi, 2.0 * cov @ w)

    def test_dimension_mismatch(self) -> None:
        with pytest.raises(ValueError):
            implied_returns(_make_cov(3), np.ones(4))


class TestBLProperties:
    def test_zero_views_returns_prior(self) -> None:
        """空观点（k=0）→ 后验权重 = 先验权重（不融合，经约束投影）。"""
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        # 放宽 max_weight 使先验不受投影截断
        res = black_litterman_weights(
            cov, prior, np.array([], dtype=float), config=BlackLittermanConfig(max_weight=0.6)
        )
        np.testing.assert_allclose(res.weights, prior)

    def test_views_equal_prior_keep_prior_mu(self) -> None:
        """Q = Pπ（观点与先验一致）→ 后验均值 ≈ 先验隐含收益。"""
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        pi = implied_returns(cov, prior)
        res = black_litterman_weights(cov, prior, views_q=pi)  # P=I, Q=π
        np.testing.assert_allclose(res.mu_posterior, pi, atol=1e-8)

    def test_view_direction_boosts_weight(self) -> None:
        """观点方向一致：相对先验更看多/看空的资产，后验权重升/降。"""
        # 对角协方差（无相关性，确定性验证）；观点与先验隐含收益同量级
        cov = np.diag([1.0, 2.0, 3.0])
        prior = np.full(3, 1.0 / 3.0)
        # π = Σ·w_prior = [1/3, 2/3, 1.0]；观点：资产 0 比先验更看多，资产 2 更看空
        views_q = np.array([0.5, 0.0, 0.5])
        res = black_litterman_weights(
            cov, prior, views_q, config=BlackLittermanConfig(omega_scale=0.02, max_weight=0.9)
        )
        assert res.weights[0] > prior[0]
        assert res.weights[2] < prior[2]

    def test_confidence_monotonic(self) -> None:
        """置信度单调：omega_scale 越小（观点越确定）→ 后验偏离先验幅度越大。"""
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        views_q = np.array([0.04, 0.0, -0.02])

        def dist(scale: float) -> float:
            res = black_litterman_weights(
                cov, prior, views_q, config=BlackLittermanConfig(omega_scale=scale)
            )
            return float(np.sum(np.abs(res.weights - prior)))

        d_low = dist(0.005)
        d_high = dist(0.5)
        assert d_low > d_high

    def test_constraints_respected(self) -> None:
        """约束：w ≥ 0、Σw ≤ max_leverage、w_i ≤ max_weight。"""
        cov = _make_cov(5)
        prior = np.full(5, 0.2)
        views_q = np.array([0.06, 0.04, -0.01, 0.02, -0.03])
        cfg = BlackLittermanConfig(max_weight=0.4, max_leverage=1.0)
        res = black_litterman_weights(cov, prior, views_q, config=cfg)
        assert np.all(res.weights >= 0)
        assert float(np.sum(res.weights)) <= 1.0 + 1e-9
        assert float(np.max(res.weights)) <= 0.4 + 1e-9

    def test_sigma_posterior_psd(self) -> None:
        """后验协方差正定（对称且特征值 > 0）。"""
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        views_q = np.array([0.03, -0.01, 0.0])
        res = black_litterman_weights(cov, prior, views_q)
        sp = res.sigma_posterior
        np.testing.assert_allclose(sp, sp.T, atol=1e-10)
        assert float(np.min(np.linalg.eigvalsh(sp))) > 0


class TestBLValidation:
    def test_cov_not_square(self) -> None:
        with pytest.raises(ValueError):
            black_litterman_weights(np.ones((2, 3)), np.ones(2), np.ones(2))

    def test_prior_dim_mismatch(self) -> None:
        with pytest.raises(ValueError):
            black_litterman_weights(_make_cov(3), np.ones(2), np.ones(3))

    def test_view_dim_mismatch(self) -> None:
        with pytest.raises(ValueError):
            black_litterman_weights(_make_cov(3), np.ones(3), np.ones(2))

    def test_views_p_shape_mismatch(self) -> None:
        with pytest.raises(ValueError):
            black_litterman_weights(_make_cov(3), np.ones(3), np.ones(2), views_p=np.ones((2, 2)))

    def test_zero_views_p_stable(self) -> None:
        """观点矩阵无暴露（P 全 0）→ 输出稳定有限权重（不抛异常）。"""
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        views_p = np.zeros((1, 3))
        res = black_litterman_weights(cov, prior, np.array([0.1]), views_p=views_p)
        assert np.all(np.isfinite(res.weights))
        assert float(np.sum(res.weights)) > 0

    def test_nan_in_views_cleaned(self) -> None:
        cov = _make_cov(3)
        prior = np.array([0.5, 0.3, 0.2])
        views_q = np.array([0.02, np.nan, -0.01])
        res = black_litterman_weights(cov, prior, views_q)
        assert np.all(np.isfinite(res.weights))

    def test_empty_assets(self) -> None:
        res = black_litterman_weights(np.array([]).reshape(0, 0), np.array([]), np.array([]))
        assert res.weights.size == 0


class TestBuildAutoViews:
    def test_direction_and_scale(self) -> None:
        factors = _make_factors(3)
        pi = np.array([0.01, 0.02, 0.03])
        views_p, views_q = build_auto_views(factors, pi)
        # 方向 = 原始 IC 符号
        assert views_q[0] > 0 and views_q[2] < 0
        # 尺度 = 原始 IC × (mean|π|/max|IC|)
        assert np.allclose(views_q, np.array([0.08, 0.02, -0.05]) * (0.02 / 0.08))
        # P = I
        np.testing.assert_allclose(views_p, np.eye(3))

    def test_all_zero_ic_returns_empty_views(self) -> None:
        factors = [{"factor_id": f"f{i}", "ic": 0.0, "_ic_raw": 0.0} for i in range(3)]
        views_p, views_q = build_auto_views(factors, np.array([0.01, 0.01, 0.01]))
        assert views_p.shape == (0, 3)
        assert views_q.size == 0

    def test_empty_factors(self) -> None:
        views_p, views_q = build_auto_views([], np.array([]))
        assert views_q.size == 0


class TestSynthesizeBlMode:
    def test_bl_auto_views(self) -> None:
        """optimizer + bl：自动观点构建，信号权重非负且 Σw≈1。"""
        factors = _make_factors(3)
        rm = _make_returns_matrix(factors)
        signals, _, _ = synthesize_signals(
            factors,
            mode="optimizer",
            returns_matrix=rm,
            optimizer_mode="bl",
        )
        assert len(signals) == 3
        weights = [s["weight"] for s in signals]
        assert all(w >= 0 for w in weights)
        # BL 语义：max_leverage 为上限而非强制满仓（对齐 PortfolioOptimizer._project）
        assert sum(weights) <= 1.0 + 1e-9
        assert sum(weights) > 0
        assert any(s["retained"] for s in signals)

    def test_bl_explicit_views(self) -> None:
        """显式 views_q/views_p 透传。"""
        factors = _make_factors(3)
        rm = _make_returns_matrix(factors)
        signals, _, _ = synthesize_signals(
            factors,
            mode="optimizer",
            returns_matrix=rm,
            optimizer_mode="bl",
            optimizer_config={"views_q": np.array([0.05, 0.0, -0.03])},
        )
        assert len(signals) == 3
        # 高 IC 因子（f0）权重应显著为正
        assert signals[0]["weight"] > 0

    def test_bl_config_overrides(self) -> None:
        factors = _make_factors(3)
        rm = _make_returns_matrix(factors)
        signals, _, _ = synthesize_signals(
            factors,
            mode="optimizer",
            returns_matrix=rm,
            optimizer_mode="bl",
            optimizer_config={"omega_scale": 0.01, "tau": 0.1},
        )
        assert len(signals) == 3

    def test_bl_failure_falls_back_to_risk_parity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BL 融合失败 → 回退 risk_parity（不中断）。"""
        factors = _make_factors(3)
        rm = _make_returns_matrix(factors)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated BL failure")

        import fts.factor_engine.portfolio_loop as pl

        monkeypatch.setattr(pl, "_synthesize_bl_weights", _boom)
        signals, _, _ = synthesize_signals(
            factors,
            mode="optimizer",
            returns_matrix=rm,
            optimizer_mode="bl",
        )
        # 回退后仍产出 3 个信号
        assert len(signals) == 3
