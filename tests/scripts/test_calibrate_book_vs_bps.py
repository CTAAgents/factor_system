"""
tests/scripts/test_calibrate_book_vs_bps.py — book vs bps 标定脚本测试（D.2 §4.9）。

覆盖:
    - simulate_book_vs_bps: 可复现（同 seed 同结果）/ 价差敏感性单调 / 部分成交率范围
    - render_markdown: 报告章节完整
"""

from __future__ import annotations

import pytest

from scripts.calibrate_book_vs_bps import (
    _bps_price,
    render_markdown,
    simulate_book_vs_bps,
)


class TestSimulateBookVsBps:
    def test_reproducible(self) -> None:
        """同 seed 结果完全一致（可复现）。"""
        r1 = simulate_book_vs_bps(n_scenarios=50, seed=42)
        r2 = simulate_book_vs_bps(n_scenarios=50, seed=42)
        assert r1 == r2

    def test_sensitivity_monotonic(self) -> None:
        """价差越宽，平均绝对价差越大（固定 bps 偏差随流动性恶化放大）。"""
        r = simulate_book_vs_bps(n_scenarios=300, seed=7)
        sens = r["spread_sensitivity"]
        assert sens["narrow"] is not None and sens["medium"] is not None and sens["wide"] is not None
        assert sens["narrow"] < sens["medium"] < sens["wide"]

    def test_partial_fill_rate_range(self) -> None:
        """部分成交率在 [0,1] 内。"""
        r = simulate_book_vs_bps(n_scenarios=100, seed=1)
        assert 0.0 <= r["partial_fill_rate"] <= 1.0

    def test_slippage_distribution_keys(self) -> None:
        """滑点分布含 p10/p50/p90。"""
        r = simulate_book_vs_bps(n_scenarios=100, seed=3)
        assert {"p10", "p50", "p90"} <= set(r["slippage_distribution"].keys())


class TestRenderMarkdown:
    def test_sections_present(self) -> None:
        """报告含场景数/滑点分布/价差敏感性章节。"""
        r = simulate_book_vs_bps(n_scenarios=20, seed=9)
        md = render_markdown(r, "2026-08-11")
        assert "场景数" in md
        assert "book 实际滑点分布" in md
        assert "价差宽度敏感性" in md
        assert "结论" in md


class TestBpsPrice:
    def test_buy_marks_up_sell_marks_down(self) -> None:
        """买入上浮、卖下沉。"""
        assert _bps_price(100.0, "buy", 10.0) == pytest.approx(100.1)
        assert _bps_price(100.0, "sell", 10.0) == pytest.approx(99.9)

    def test_default_slippage(self) -> None:
        """默认 0.5 bps。"""
        assert _bps_price(100.0, "buy") == pytest.approx(100.005)
