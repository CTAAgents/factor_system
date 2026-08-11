"""
tests/factor_engine/test_regime_model_selection.py — HMM 状态数选择与特征标准化测试（28 计划 T8）

覆盖范围:
    - select_n_states: BIC 状态数选择返回合法候选
    - fit_standardizer: 训练段 fit 的 (mean, std) 变换公式正确（防数据窥探）

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime_model_selection import select_n_states, fit_standardizer


def test_select_n_states_returns_valid_candidate() -> None:
    """BIC 状态数选择应返回候选集中的合法值。

    双 regime 合成数据（前 150 低波动、后 150 高波动），
    select_n_states 必须在给定候选集中选出一个（不抛异常、不越界）。
    """
    rng = np.random.default_rng(42)
    rets = np.concatenate(
        [
            rng.normal(0.0005, 0.005, 150),
            rng.normal(-0.0005, 0.02, 150),
        ]
    )
    features = np.column_stack([rets, np.abs(rets)])
    n = select_n_states(features, candidates=(2, 3, 4))
    assert n in (2, 3, 4)


def test_standardizer_fit_predict_consistent() -> None:
    """fit 的 (mean, std) 变换公式正确：训练段每列均值为 0、标准差为 1。

    （修正说明：原计划断言 scaled.mean()≈0 对单行数组不成立；
    改为验证训练段整体标准化后列均值归零、列标准差归 1，
    并验证单行 transform 与训练段对应行一致——即"特征只 fit 训练段"的同一套参数。）
    """
    x = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    mean, std = fit_standardizer(x)
    scaled = (x - mean) / std
    # 训练段整体标准化：每列均值为 0、标准差为 1
    assert np.allclose(scaled.mean(axis=0), 0.0, atol=1e-6)
    assert np.allclose(scaled.std(axis=0), 1.0, atol=1e-6)
    # 首行经 (x-mean)/std 变换后与训练段首行一致（同一套参数，防泄露）
    first_row = (np.array([[1.0, 2.0]]) - mean) / std
    assert np.allclose(first_row, scaled[0:1], atol=1e-6)
