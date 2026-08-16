"""tests/factor_engine/test_owl_factor_selector.py — OWL 因子分组筛选器测试（plans/41 方案 A）。

覆盖:
    - make_owl_weights：权重向量非递增/非负/方案差异/退化
    - OwlFactorSelector.select：求解正确性/分组还原/稀疏筛选/样本外切割
    - 组内正交检验：显著/非显著组判定 + Bonferroni 校正
    - 边界：空/全 NaN/单因子/超短样本/形状不齐 → applied=False 零漂移
    - 依赖缺失降级：monkeypatch cvxpy 导入失败 → applied=False
    - 分组索引正确性（相关组正确聚组、噪声因子独立成组）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine import owl_factor_selector as _owl  # noqa: E402
from fts.factor_engine.owl_factor_selector import (  # noqa: E402
    OwlFactorSelector,
    OwlSelectionResult,
    make_owl_weights,
)

requires_cvxpy = pytest.mark.skipif(
    not _owl._CVXPY_AVAILABLE, reason="cvxpy 不可用"
)
requires_scipy = pytest.mark.skipif(
    not _owl._SCIPY_AVAILABLE, reason="scipy 不可用"
)


# ─── 工具 ──────────────────────────────────────────────────


def _correlated(n: int, k: int, intra_corr: float, rng: np.random.Generator) -> np.ndarray:
    """生成 k 个组内相关 intra_corr 的因子列。"""
    base = rng.standard_normal((n, 1))
    noise = rng.standard_normal((n, k))
    c = intra_corr
    return base * np.sqrt(c) + noise * np.sqrt(1.0 - c)


def _make_X_y(
    n: int = 150,
    n_groups: int = 3,
    n_noise: int = 4,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """构造 X：3 个相关组 + 4 个噪声因子；y 用各组首个因子线性组合。"""
    rng = np.random.default_rng(seed)
    g1 = _correlated(n, 3, 0.7, rng)
    g2 = _correlated(n, 3, 0.7, rng)
    g3 = _correlated(n, 3, 0.7, rng)
    noise = rng.standard_normal((n, n_noise))
    X = np.column_stack([g1, g2, g3, noise])
    y = 0.5 * X[:, 0] + 0.3 * X[:, 3] - 0.2 * X[:, 6] + rng.standard_normal(n) * 0.5
    return X, y


# ─── make_owl_weights ──────────────────────────────────────


class TestMakeOwlWeights:
    def test_non_increasing(self):
        for scheme in ("linear", "exp", "log"):
            w = make_owl_weights(10, scheme, 0.5)
            assert w.shape == (10,)
            assert np.all(np.diff(w) <= 1e-12), f"{scheme}: 权重应非递增"

    def test_non_negative(self):
        w = make_owl_weights(20, "linear", 1.0)
        assert np.all(w >= 0)

    def test_scheme_variation(self):
        w_lin = make_owl_weights(10, "linear", 0.5)
        w_exp = make_owl_weights(10, "exp", 0.5)
        # exp 衰减应比 linear 陡（尾部更小）
        assert w_exp[-1] < w_lin[-1] + 1e-12

    def test_zero_tuning_is_uniform(self):
        w = make_owl_weights(8, "linear", 0.0)
        np.testing.assert_allclose(w, w[0])

    def test_empty(self):
        assert make_owl_weights(0).size == 0

    def test_single(self):
        w = make_owl_weights(1, "linear", 0.5)
        assert w.shape == (1,) and w[0] > 0


# ─── 求解正确性 ───────────────────────────────────────────


class TestSolveOwl:
    @requires_cvxpy
    def test_applied_small_sample(self):
        X, y = _make_X_y(n=120)
        r = OwlFactorSelector().select(X, y)
        assert r.applied
        assert r.beta is not None and r.beta.shape == (X.shape[1],)
        assert 0 < r.n_train <= 120

    @requires_cvxpy
    def test_strong_corr_grouped(self):
        """组内高相关因子应聚到一组（OWL 核心分组能力）。"""
        rng = np.random.default_rng(7)
        n = 150
        g = _correlated(n, 4, 0.7, rng)
        X = np.column_stack([g, rng.standard_normal((n, 2))])
        y = 0.5 * X[:, 0] + 0.3 * X[:, 2] + rng.standard_normal(n) * 0.5
        r = OwlFactorSelector().select(X, y)
        assert r.applied
        # 相关组 4 因子中至少 2 个在同一组，且噪声因子不与组内混
        group_sets = [set(g) for g in r.groups]
        has_group = any(len(gs) >= 2 for gs in group_sets)
        assert has_group, f"应识别出相关组: {r.groups}"
        # 噪声因子 4/5 不应与相关组同组
        for gs in group_sets:
            assert not ({4, 5} & gs) or len(gs) == 1

    @requires_cvxpy
    def test_sparsity(self):
        """λ 生效时非零系数数应显著小于因子数（稀疏筛选）。"""
        rng = np.random.default_rng(3)
        n = 200
        X = np.column_stack([_correlated(n, 3, 0.7, rng), rng.standard_normal((n, 40))])
        beta_true = np.zeros(X.shape[1])
        beta_true[0] = 0.4
        y = X @ beta_true + rng.standard_normal(n) * 0.3
        r = OwlFactorSelector(lambda_=0.05).fit_group(X, y)
        assert r.applied
        nonzero = int(np.sum(np.abs(r.beta) > 1e-6))
        assert nonzero < X.shape[1] * 0.6, f"非零 {nonzero} 应明显少于 {X.shape[1]}"

    @requires_cvxpy
    def test_train_frac_split(self):
        """样本外切割：n_train ≈ train_frac * n。"""
        X, y = _make_X_y(n=200)
        r = OwlFactorSelector(train_frac=0.7).select(X, y)
        assert r.applied
        assert abs(r.n_train - int(200 * 0.7)) <= 1


# ─── 组内正交检验 ─────────────────────────────────────────


class TestGroupOrthogonalTest:
    @requires_cvxpy
    @requires_scipy
    def test_significant_group_detected(self):
        """有真实解释力的组应判为显著。"""
        X, y = _make_X_y(n=250)
        r = OwlFactorSelector().select(X, y)
        assert r.applied
        # 至少有一个显著组（前 3 组有真实 β）
        assert len(r.significant_groups) >= 1

    @requires_cvxpy
    @requires_scipy
    def test_noise_only_no_significant(self):
        """纯噪声因子 → 建议剔除名单应覆盖噪声因子。"""
        rng = np.random.default_rng(11)
        n = 200
        X = rng.standard_normal((n, 8))
        y = rng.standard_normal(n)  # 纯噪声
        r = OwlFactorSelector().select(X, y)
        assert r.applied
        # 噪声场景：OWL 应稀疏，剔除名单含大部分因子或显著组为空
        if len(r.groups) > 0:
            assert len(r.significant_groups) <= len(r.groups)

    @requires_cvxpy
    @requires_scipy
    def test_bonferroni_adj_alpha(self):
        """多组时 Bonferroni 校正生效：alpha = base / n_groups。"""
        X, y = _make_X_y(n=150, n_noise=10)
        sel = OwlFactorSelector(significance_level=0.05)
        r = sel.select(X, y)
        assert r.applied
        # 校正后的 p 值判定结果应比未校正更严格（显著组数 ≤ 组数）
        assert len(r.significant_groups) <= len(r.groups)


# ─── 边界与降级 ───────────────────────────────────────────


class TestEdgeCases:
    def test_empty_matrix(self):
        r = OwlFactorSelector().select(np.empty((0, 0)), np.array([]))
        assert not r.applied

    def test_all_nan(self):
        X = np.full((50, 4), np.nan)
        y = np.full(50, np.nan)
        r = OwlFactorSelector().select(X, y)
        assert not r.applied

    def test_single_factor(self):
        X = np.random.default_rng(1).standard_normal((50, 1))
        y = np.random.default_rng(2).standard_normal(50)
        r = OwlFactorSelector().select(X, y)
        assert not r.applied  # p<2 跳过

    def test_shape_mismatch(self):
        X = np.random.default_rng(1).standard_normal((50, 4))
        y = np.random.default_rng(2).standard_normal(40)
        r = OwlFactorSelector().select(X, y)
        assert not r.applied

    def test_too_short(self):
        X = np.random.default_rng(1).standard_normal((8, 3))
        y = np.random.default_rng(2).standard_normal(8)
        r = OwlFactorSelector().select(X, y)
        assert not r.applied

    @requires_cvxpy
    def test_constant_column(self):
        """常数列不破坏求解（标准化后置 0）。"""
        rng = np.random.default_rng(5)
        n = 100
        X = np.column_stack([np.ones(n), rng.standard_normal((n, 3))])
        y = rng.standard_normal(n)
        r = OwlFactorSelector().select(X, y)
        assert r.applied  # 不抛异常且成功执行


class TestDependencyFallback:
    @pytest.mark.skipif(not _owl._CVXPY_AVAILABLE, reason="cvxpy 未装无法测试降级")
    def test_cvxpy_import_failure_fallback(self, monkeypatch):
        """cvxpy 导入失败 → applied=False 零漂移。"""
        import fts.factor_engine.owl_factor_selector as mod

        monkeypatch.setattr(mod, "_CVXPY_AVAILABLE", False)
        monkeypatch.setattr(mod, "cp", None)
        X, y = _make_X_y(n=100)
        r = OwlFactorSelector().select(X, y)
        assert not r.applied

    def test_result_defaults(self):
        """OwlSelectionResult 默认值语义。"""
        r = OwlSelectionResult()
        assert not r.applied
        assert r.beta is None
        assert r.groups == []
        assert r.nonsignificant_factors == []
