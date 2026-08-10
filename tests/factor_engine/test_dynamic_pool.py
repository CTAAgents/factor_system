"""tests/factor_engine/test_dynamic_pool.py — 数据驱动动态池测试（GAP-054）。

覆盖:
    ① get_dynamic_core_subset 缓存读取与降级（缺失/非法/损坏 → 回退静态 25 池）
    ② build_pool 渐进式替换（全达标不换血 / 部分不达标替换 + 产业约束）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fts.data_futures as df
from fts.data_futures import FUTURES_CORE_SUBSET, get_dynamic_core_subset
from scripts.sync_liquidity_pool import build_pool


# ─── ① get_dynamic_core_subset ─────────────────────────


def _make_pool_json(tmp_path: Path, payload) -> Path:
    p = tmp_path / "futures_dynamic_pool.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


class TestGetDynamicCoreSubset:
    def test_missing_cache_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(tmp_path / "nope.json"))
        pool = get_dynamic_core_subset()
        assert pool == list(FUTURES_CORE_SUBSET)

    def test_valid_cache_returns_pool(self, tmp_path, monkeypatch):
        p = _make_pool_json(tmp_path, {"version": 1, "pool": ["RB0", "CU0"]})
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(p))
        assert get_dynamic_core_subset() == ["RB0", "CU0"]

    def test_empty_pool_falls_back(self, tmp_path, monkeypatch):
        p = _make_pool_json(tmp_path, {"pool": []})
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(p))
        assert get_dynamic_core_subset() == list(FUTURES_CORE_SUBSET)

    def test_non_list_pool_falls_back(self, tmp_path, monkeypatch):
        p = _make_pool_json(tmp_path, {"pool": "RB0"})
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(p))
        assert get_dynamic_core_subset() == list(FUTURES_CORE_SUBSET)

    def test_corrupt_json_falls_back(self, tmp_path, monkeypatch):
        p = tmp_path / "futures_dynamic_pool.json"
        p.write_text("{bad json", encoding="utf-8")
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(p))
        assert get_dynamic_core_subset() == list(FUTURES_CORE_SUBSET)

    def test_whitespace_symbols_filtered(self, tmp_path, monkeypatch):
        p = _make_pool_json(tmp_path, {"pool": ["RB0", "", "  ", "CU0"]})
        monkeypatch.setattr(df, "DYNAMIC_POOL_CACHE", str(p))
        assert get_dynamic_core_subset() == ["RB0", "CU0"]


# ─── ② build_pool 渐进式替换 ───────────────────────────


def _snap(symbols: list[str], turnovers: list[float], qualified: bool = True) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbols,
            "avg_turnover_yi": turnovers,
            "rank": list(range(1, len(symbols) + 1)),
            "qualified": [qualified] * len(symbols),
        }
    )


class TestBuildPool:
    def test_all_qualified_no_replacement(self):
        """池内全达标 → 渐进保留，无新增无移除。"""
        core = list(FUTURES_CORE_SUBSET)
        snap = _snap(core, [float(i) for i in range(len(core), 0, -1)], qualified=True)
        result = build_pool(snap, pool_size=25, max_per_sector=6)
        assert result["removed"] == []
        assert result["added"] == []
        assert set(result["pool"]) == set(core)

    def test_unqualified_replaced_by_candidates(self):
        """池内 2 个不达标 → 由池外够格候补按排名替换（产业约束下）。"""
        core = list(FUTURES_CORE_SUBSET)
        unqualified = core[-2:]  # 排名最低的 2 个标记不达标
        keep = core[:-2]
        # 构造快照：池内 keep 达标 + 2 个不达标 + 池外候选（高流动性）
        candidates = ["PP0", "V0", "LH0"]  # 池外高流动性品种
        syms = keep + unqualified + candidates
        turns = [float(1000 - i) for i in range(len(syms))]
        snap = _snap(syms, turns, qualified=True)
        # 不达标：把 unqualified 的 qualified 置 False
        snap.loc[snap["symbol"].isin(unqualified), "qualified"] = False
        result = build_pool(snap, pool_size=25, max_per_sector=6)
        assert set(result["kept"]) == set(keep)
        assert set(result["removed"]) == set(unqualified)
        assert len(result["added"]) == 2
        assert len(result["pool"]) == 25

    def test_sector_constraint_limits_candidates(self):
        """产业约束：同一板块超过 max_per_sector 的候选被跳过。"""
        core = list(FUTURES_CORE_SUBSET)
        unqualified = core[-3:]  # 3 个不达标
        keep = core[:-3]
        cand = ["PP0", "V0", "L0"]
        syms = core + cand
        n = len(syms)
        snap = _snap(syms, [float(n - i) for i in range(n)], qualified=True)
        snap.loc[snap["symbol"].isin(unqualified), "qualified"] = False
        result = build_pool(snap, pool_size=25, max_per_sector=1)
        # max_per_sector=1 时，候补品种被产业约束限制：added 显著少于候选数 3
        assert len(result["added"]) < 3
        assert len(result["pool"]) >= len(keep)  # 保留品种不因约束丢失
        assert len(result["pool"]) <= 25

    def test_pool_size_respected(self):
        """池大小固定 pool_size。"""
        core = list(FUTURES_CORE_SUBSET)
        snap = _snap(core + ["PP0", "V0"], [float(100 - i) for i in range(27)], qualified=True)
        result = build_pool(snap, pool_size=20, max_per_sector=6)
        assert len(result["pool"]) == 20
