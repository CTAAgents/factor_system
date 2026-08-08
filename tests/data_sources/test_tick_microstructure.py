"""
tests.data_sources.test_tick_microstructure — tick 盘口微观结构分析测试（v2.31.0 Phase 5）。

测试覆盖:
    1. 买卖价差计算（绝对/相对价差）
    2. 盘口深度计算（五档深度/OBI 不平衡）
    3. 冲击成本计算（Amihud/有效价差/Kyle's Lambda）
    4. 价差-深度联动

HARNESS §5.4 测试随重构: 每阶段测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.tick_microstructure_analysis import (
    analyze_depth,
    analyze_impact,
    analyze_spread,
    analyze_spread_depth_relation,
)


# ─── 构造模拟 tick 数据 ───────────────────────────────────


def _make_tick_df(n: int = 1000, spread: float = 1.0) -> pd.DataFrame:
    """构造含 5 档盘口的模拟 tick 数据。"""
    times = pd.date_range("2026-08-07 14:00:00", periods=n, freq="500ms")
    mid = 3010.0 + np.sin(np.arange(n) / 50) * 2
    # 价差在 base spread 附近波动（模拟真实市场，深度厚时价差收窄）
    depth_cycle = 1 + np.sin(np.arange(n) / 30) * 0.3
    spread_series = spread * (1.5 - depth_cycle * 0.5)  # 深度高 → 价差窄
    bid1 = mid - spread_series / 2
    ask1 = mid + spread_series / 2

    rows = {"datetime": times, "last_price": mid + np.random.randn(n) * 0.2}
    rows["bid_price1"] = bid1
    rows["bid_volume1"] = 100 + np.random.randint(0, 50, n)
    rows["ask_price1"] = ask1
    rows["ask_volume1"] = 100 + np.random.randint(0, 50, n)

    # 2-5 档逐步远离最优报价
    for lvl in range(2, 6):
        rows[f"bid_price{lvl}"] = bid1 - (lvl - 1)
        rows[f"bid_volume{lvl}"] = 80 + np.random.randint(0, 30, n)
        rows[f"ask_price{lvl}"] = ask1 + (lvl - 1)
        rows[f"ask_volume{lvl}"] = 80 + np.random.randint(0, 30, n)

    # 成交量/成交额（累计）
    rows["volume"] = np.cumsum(np.random.randint(1, 20, n))
    rows["amount"] = rows["volume"] * 3000
    rows["open_interest"] = 120000
    rows["symbol"] = "RB0"
    rows["source"] = "TEST"
    rows["fetched_at"] = pd.Timestamp.now()
    rows["trace_id"] = "test"
    return pd.DataFrame(rows)


# ─── 1. 买卖价差 ──────────────────────────────────────────


class TestAnalyzeSpread:
    """测试买卖价差分析。"""

    def test_basic(self) -> None:
        """价差计算正确。"""
        df = _make_tick_df(n=500, spread=1.0)
        r = analyze_spread(df)
        assert r["n_ticks"] == 500
        assert abs(r["abs_spread_mean"] - 1.0) < 0.05
        assert abs(r["abs_spread_median"] - 1.0) < 0.05
        assert r["abs_spread_pct_1tick"] > 0  # 存在价差为 1 最小变动价位的 tick

    def test_wider_spread(self) -> None:
        """价差 2 元时均值应约 2。"""
        df = _make_tick_df(n=500, spread=2.0)
        r = analyze_spread(df)
        assert abs(r["abs_spread_mean"] - 2.0) < 0.1

    def test_empty(self) -> None:
        """空数据返回空 dict。"""
        assert analyze_spread(pd.DataFrame()) == {}

    def test_missing_columns(self) -> None:
        """缺盘口列返回空 dict。"""
        df = pd.DataFrame({"last_price": [1.0, 2.0]})
        assert analyze_spread(df) == {}


# ─── 2. 盘口深度 ──────────────────────────────────────────


class TestAnalyzeDepth:
    """测试盘口深度分析。"""

    def test_basic(self) -> None:
        """五档深度与 OBI 计算正确。"""
        df = _make_tick_df(n=500)
        r = analyze_depth(df)
        assert "bid_depth_mean" in r
        assert "obi_mean" in r
        assert r["obi_mean"] >= -1.0 and r["obi_mean"] <= 1.0
        # 买深 = 5 档量之和
        assert r["bid_depth_mean"] > 0
        assert r["total_depth_mean"] == pytest.approx(
            r["bid_depth_mean"] + r["ask_depth_mean"], rel=1e-3
        )

    def test_obi_range(self) -> None:
        """OBI 应在 [-1, 1] 区间。"""
        df = _make_tick_df(n=1000)
        r = analyze_depth(df)
        assert -1.0 <= r["obi_mean"] <= 1.0
        assert 0 <= r["obi_pct_positive"] <= 100

    def test_empty(self) -> None:
        """空数据返回空 dict。"""
        assert analyze_depth(pd.DataFrame()) == {}


# ─── 3. 冲击成本 ──────────────────────────────────────────


class TestAnalyzeImpact:
    """测试冲击成本分析。"""

    def test_basic(self) -> None:
        """Amihud/有效价差/Kyle 计算可运行且值合理。"""
        df = _make_tick_df(n=1000)
        r = analyze_impact(df)
        assert r["amihud_mean"] >= 0
        assert r["eff_spread_mean"] > 0
        assert r["avg_volume_per_tick"] > 0
        assert r["avg_amount_per_tick"] > 0
        assert isinstance(r["kyle_lambda"], float)

    def test_eff_spread_reasonable(self) -> None:
        """有效价差应与绝对价差同量级（成交价贴近中点）。"""
        df = _make_tick_df(n=1000, spread=1.0)
        r = analyze_impact(df)
        # 有效价差 = 2*|last - mid|，last 围绕 mid ±0.2，均值应 < 价差
        assert r["eff_spread_mean"] < 2.0

    def test_empty(self) -> None:
        """空数据返回空 dict。"""
        assert analyze_impact(pd.DataFrame()) == {}


# ─── 4. 价差-深度联动 ─────────────────────────────────────


class TestAnalyzeSpreadDepthRelation:
    """测试价差-深度联动。"""

    def test_basic(self) -> None:
        """相关系数可计算。"""
        df = _make_tick_df(n=1000)
        r = analyze_spread_depth_relation(df)
        assert "spread_depth_corr" in r
        assert -1.0 <= r["spread_depth_corr"] <= 1.0
        # 深度分位价差应存在
        assert "spread_by_depth_q1" in r

    def test_empty(self) -> None:
        """空数据返回空 dict。"""
        assert analyze_spread_depth_relation(pd.DataFrame()) == {}
