"""tests/factor_engine/test_sector_linkage.py — 品种间板块联动检测测试（GAP-065）。

HARNESS §测试随重构: 成功路径 + 边界 + 降级路径。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from fts.factor_engine.sector_linkage import (
    compute_sector_linkage,
    factor_dispersion_by_sector,
    DEFAULT_LINKAGE_THRESHOLD,
)


def _panel(seed: int = 21) -> tuple[pd.DataFrame, dict[str, str]]:
    """构造双板块收益面板：黑色系（RB/HC/I）强联动，有色系（CU/AL）独立。

    Returns:
        (returns, sector_map)
    """
    rng = np.random.default_rng(seed)
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    common_black = rng.normal(0, 0.02, n)  # 黑色系共同驱动
    df = pd.DataFrame(index=idx)
    for sym in ["RB", "HC", "I"]:
        df[sym] = common_black + rng.normal(0, 0.004, n)
    for sym in ["CU", "AL"]:
        df[sym] = rng.normal(0, 0.015, n)  # 有色系独立
    sector_map = {"RB": "黑色系", "HC": "黑色系", "I": "黑色系", "CU": "有色系", "AL": "有色系"}
    return df, sector_map


def test_correlated_sector_flagged_high_linkage():
    """强联动板块 intra 相关高且 high_linkage=True。"""
    returns, sector_map = _panel()
    reports = compute_sector_linkage(returns, sector_map)
    by_sector = {r.sector: r for r in reports}
    black = by_sector["黑色系"]
    assert black.intra_sector_avg_corr > DEFAULT_LINKAGE_THRESHOLD
    assert black.high_linkage is True


def test_independent_sector_low_linkage():
    """独立品种板块联动低。"""
    returns, sector_map = _panel()
    reports = compute_sector_linkage(returns, sector_map)
    by_sector = {r.sector: r for r in reports}
    nonferrous = by_sector["有色系"]
    assert nonferrous.intra_sector_avg_corr < 0.3
    assert nonferrous.high_linkage is False


def test_cross_sector_corr_computed():
    """跨板块相关被计算且为有限值。"""
    returns, sector_map = _panel()
    reports = compute_sector_linkage(returns, sector_map)
    for r in reports:
        assert np.isfinite(r.cross_sector_avg_corr)


def test_members_below_two_zero_linkage():
    """板块成员 <2 时联动强度为 0。"""
    returns, sector_map = _panel()
    sector_map = dict(sector_map)
    sector_map["PB"] = "有色系"  # 加入不在面板中的品种，不影响
    # 单成员板块
    sector_map["EC"] = "航运"
    returns["EC"] = np.random.default_rng(2).normal(0, 0.01, len(returns))
    reports = compute_sector_linkage(returns, sector_map)
    by_sector = {r.sector: r for r in reports}
    assert by_sector["航运"].intra_sector_avg_corr == 0.0


def test_factor_dispersion_computed():
    """提供信号面板时输出板块内因子截面分散度。"""
    returns, sector_map = _panel()
    signal = returns * 0.5 + np.random.default_rng(4).normal(0, 0.001, returns.shape)
    reports = compute_sector_linkage(returns, sector_map, signal=signal)
    for r in reports:
        assert r.factor_dispersion is not None
        assert r.factor_dispersion > 0


def test_factor_dispersion_by_sector():
    """独立接口 factor_dispersion_by_sector 输出各板块分散度。"""
    returns, sector_map = _panel()
    disp = factor_dispersion_by_sector(returns, sector_map)
    assert set(disp) == {"黑色系", "有色系"}
    assert all(v > 0 for v in disp.values())


def test_empty_returns_returns_empty():
    """空面板返回空列表。"""
    assert compute_sector_linkage(pd.DataFrame(), {}) == []


def test_to_dict_serializable():
    """to_dict 可 JSON 序列化。"""
    returns, sector_map = _panel()
    reports = compute_sector_linkage(returns, sector_map)
    for r in reports:
        json.dumps(r.to_dict())
