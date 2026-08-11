"""
tests/scripts/test_validate_sector_clusters.py — 产业链分类聚类校验脚本测试

覆盖: return_corr / hierarchical_cluster / adjusted_rand_index /
      per_chain_purity / within_vs_cross_corr / build_report
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts.validate_sector_clusters import (
    adjusted_rand_index,
    build_report,
    hierarchical_cluster,
    per_chain_purity,
    return_corr,
    within_vs_cross_corr,
)


def _make_close(n_days: int = 200, seed: int = 42) -> pd.DataFrame:
    """构造 3 组强相关、组间独立的价格面板（各组有独立公共冲击）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    close: dict[str, np.ndarray] = {}
    for syms in [["A1", "A2", "A3"], ["B1", "B2"], ["C1", "C2", "C3"]]:
        shock = rng.normal(0, 1, n_days)  # 组内公共冲击
        for s in syms:
            noise = rng.normal(0, 0.3, n_days)
            close[s] = 100 * np.exp(np.cumsum(shock + noise))
    return pd.DataFrame(close, index=dates)


def test_return_corr_fills_nan() -> None:
    """重叠观测不足的配对相关填 0（不产生 NaN）。"""
    close = _make_close(n_days=200)
    corr = return_corr(close, horizon=1, min_obs=20)
    assert not corr.isna().any().any()
    assert abs(corr.loc["A1", "A2"]) > 0.5  # 同组高度相关
    assert corr.shape == (8, 8)


def test_hierarchical_cluster_grouping() -> None:
    """强相关组应被聚到同一簇。"""
    close = _make_close(n_days=200)
    corr = return_corr(close, horizon=1, min_obs=20)
    labels = hierarchical_cluster(corr, n_clusters=3)
    assert len(labels) == 8
    assert labels["A1"] == labels["A2"] == labels["A3"]
    assert labels["B1"] == labels["B2"]
    assert labels["C1"] == labels["C2"] == labels["C3"]
    assert len({labels["A1"], labels["B1"], labels["C1"]}) == 3


def test_adjusted_rand_index() -> None:
    """一致划分 ARI=1.0；完全无信息划分 ARI≈0。"""
    labels_a = {"s1": 0, "s2": 0, "s3": 1, "s4": 1}
    assert adjusted_rand_index(labels_a, dict(labels_a)) == 1.0
    labels_b = {"s1": 0, "s2": 1, "s3": 2, "s4": 3}  # 全独立 vs 2+2
    assert abs(adjusted_rand_index(labels_a, labels_b)) < 0.2


def test_per_chain_purity() -> None:
    """主导簇纯度 = 链内最集中簇占比。"""
    sector_map = {"链A": ["s1", "s2", "s3"], "链B": ["s4"]}
    labels = {"s1": 1, "s2": 1, "s3": 2, "s4": 3}
    purity = per_chain_purity(sector_map, labels)
    assert purity["链A"]["purity"] == round(2 / 3, 4)
    assert purity["链A"]["dominant_cluster"] == 1
    assert "链B" not in purity  # 单品种链不参与纯度


def test_within_vs_cross_corr() -> None:
    """板块内相关性应显著高于板块外。"""
    close = _make_close(n_days=200)
    corr = return_corr(close, horizon=1, min_obs=20)
    sector_map = {"组A": ["A1", "A2", "A3"], "组B": ["B1", "B2"], "组C": ["C1", "C2", "C3"]}
    within, across, chain_avg = within_vs_cross_corr(corr, sector_map)
    assert np.mean(within) > 0.5
    assert np.mean(within) > np.mean(across)
    assert "组A" in chain_avg and chain_avg["组A"] > 0.5


def test_build_report_contains_sections() -> None:
    """Markdown 报告包含关键章节与数据。"""
    close = _make_close(n_days=200)
    corr = return_corr(close, horizon=1, min_obs=20)
    labels = hierarchical_cluster(corr, n_clusters=3)
    sector_map = {"组A": ["A1", "A2", "A3"], "组B": ["B1", "B2"], "组C": ["C1", "C2", "C3"]}
    expert = {s: sec for sec, syms in sector_map.items() for s in syms}
    ari = adjusted_rand_index(expert, labels)
    purity = per_chain_purity(sector_map, labels)
    within, across, chain_avg = within_vs_cross_corr(corr, sector_map)
    lines = build_report(
        close, corr, labels, ari, purity, within, across, chain_avg, ["X0"], horizon=1, days=200
    )
    text = "\n".join(lines)
    assert "Adjusted Rand Index" in text
    assert "主导簇纯度" in text
    assert "内部平均相关" in text
    assert "X0" in text  # 排除品种被列出
