"""tests/scripts/test_verify_factor_mapping.py — 因子映射核验脚本冒烟测试（plans/57 §4.3）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from verify_factor_mapping import MAPPING, build_panel, verify  # noqa: E402


@pytest.fixture(scope="module")
def report():
    return verify(n_days=200)


class TestMappingTable:
    def test_12_factors_declared(self):
        """RD 11 因子（含 Tier B 2 个）+ 映射表共 12 项。"""
        assert len(MAPPING) == 12
        names = [m["rd_name"] for m in MAPPING]
        assert "momentum_20" in names and "volume_price_fit" in names
        assert "roll_yield" in names and "near_far_spread_z" in names

    def test_fts_expr_none_marked_pending(self):
        """无 FTS 等价实现的因子标待定（roll_yield / near_far_spread_z 期限结构）。"""
        pending = {m["rd_name"] for m in MAPPING if m["fts_expr"] is None}
        assert pending == {"roll_yield", "near_far_spread_z"}


class TestVerifyRun:
    def test_no_d_failures(self, report):
        """核验结果无不达标（D 档）因子。"""
        failed = [f for f in report["factors"] if f["grade"] == "D(不达标)"]
        assert failed == []

    def test_all_mappable_verified(self, report):
        """10 个有 FTS 等价实现的因子全部核验（A 档），2 个待定（期限结构）。"""
        assert report["summary"]["verified"] == 10
        assert report["summary"]["pending"] == 2
        verified = [f for f in report["factors"] if f["grade"].startswith("A")]
        assert len(verified) == 10

    def test_panel_shapes(self):
        """合成面板含 RD 所需 open_interest 与 FTS 所需 hold。"""
        panel = build_panel(n_days=300)
        df = next(iter(panel.values()))
        assert "open_interest" in df.columns and "hold" in df.columns
