"""test_permutation_test — 因子有效性置换检验单元测试（手册阶段4/6/9）。"""

from __future__ import annotations

import numpy as np

from fts.factor_engine.permutation_test import (
    PermutationConfig,
    PermutationResult,
    factor_ic_permutation_test,
    portfolio_sharpe_permutation_test,
)


def _random_pair(n: int = 60, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """构造互相独立的随机信号与收益（无预测能力）。"""
    rng = np.random.RandomState(seed)
    return rng.standard_normal(n), rng.standard_normal(n)


def _strong_pair(n: int = 60, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """构造强预测因子：signal ≈ 未来收益（高 IC）。"""
    rng = np.random.RandomState(seed)
    ret = rng.standard_normal(n)
    signal = ret * 0.8 + rng.standard_normal(n) * 0.1
    return signal, ret


# ─── IC 置换检验 ──────────────────────────────────────────


def test_ic_random_not_significant() -> None:
    """随机信号：IC 不显著，不通过。"""
    signal, ret = _random_pair()
    result = factor_ic_permutation_test(signal, ret)
    assert result.passed is False
    assert result.p_value > 0.05


def test_ic_strong_factor_significant() -> None:
    """强预测因子：IC 显著非零，通过。"""
    signal, ret = _strong_pair()
    result = factor_ic_permutation_test(signal, ret)
    assert result.observed > 0
    assert result.p_value < 0.05
    assert result.passed is True


def test_ic_negative_correlation_bilateral() -> None:
    """负相关因子（反向有效）：双侧检验同样显著通过。"""
    signal, ret = _strong_pair()
    result = factor_ic_permutation_test(-signal, ret)
    assert result.observed < 0
    assert result.p_value < 0.05
    assert result.passed is True


def test_ic_reproducible_seed() -> None:
    """固定种子可复现。"""
    signal, ret = _strong_pair()
    cfg = PermutationConfig(n_permutations=100, random_seed=42)
    r1 = factor_ic_permutation_test(signal, ret, cfg)
    r2 = factor_ic_permutation_test(signal, ret, cfg)
    assert r1.p_value == r2.p_value
    assert r1.observed == r2.observed


def test_ic_more_permutations_stabilizes() -> None:
    """置换次数增加后零分布趋于稳定（均值接近 0）。"""
    signal, ret = _strong_pair()
    result = factor_ic_permutation_test(signal, ret, PermutationConfig(n_permutations=300))
    assert abs(result.null_mean) < 0.05


def test_ic_nan_handling() -> None:
    """含 NaN 样本对剔除后仍可计算，不崩溃。"""
    signal, ret = _strong_pair(80)
    signal[::7] = np.nan
    ret[3::7] = np.nan
    result = factor_ic_permutation_test(signal, ret)
    assert isinstance(result, PermutationResult)
    assert result.n_permutations > 0


def test_ic_length_mismatch() -> None:
    """长度不匹配 → 不通过且不崩溃。"""
    result = factor_ic_permutation_test(np.ones(10), np.ones(5))
    assert result.passed is False


def test_ic_too_few_samples() -> None:
    """样本不足 → 不通过。"""
    result = factor_ic_permutation_test(np.ones(1), np.ones(1))
    assert result.passed is False


def test_ic_result_to_dict() -> None:
    """结果可序列化。"""
    signal, ret = _strong_pair()
    result = factor_ic_permutation_test(signal, ret)
    d = result.to_dict()
    assert d["statistic"] == "ic"
    assert "p_value" in d and "passed" in d


# ─── 组合夏普置换检验 ─────────────────────────────────────


def test_sharpe_random_not_significant() -> None:
    """随机得分：组合夏普不显著。"""
    scores, ret = _random_pair()
    result = portfolio_sharpe_permutation_test(scores, ret)
    assert result.passed is False
    assert result.statistic == "sharpe"


def test_sharpe_strong_score_significant() -> None:
    """强综合得分：真实夏普显著高于随机。"""
    scores, ret = _strong_pair()
    result = portfolio_sharpe_permutation_test(scores, ret)
    assert result.observed > 0
    assert result.p_value < 0.05
    assert result.passed is True


def test_sharpe_reproducible_seed() -> None:
    """固定种子可复现。"""
    scores, ret = _strong_pair()
    cfg = PermutationConfig(n_permutations=100, random_seed=1)
    r1 = portfolio_sharpe_permutation_test(scores, ret, cfg)
    r2 = portfolio_sharpe_permutation_test(scores, ret, cfg)
    assert r1.p_value == r2.p_value


def test_sharpe_nan_handling() -> None:
    """含 NaN → 剔除后正常计算。"""
    scores, ret = _strong_pair(80)
    scores[::5] = np.nan
    result = portfolio_sharpe_permutation_test(scores, ret)
    assert isinstance(result, PermutationResult)


def test_sharpe_length_mismatch() -> None:
    """长度不匹配 → 不通过。"""
    result = portfolio_sharpe_permutation_test(np.ones(10), np.ones(3))
    assert result.passed is False
