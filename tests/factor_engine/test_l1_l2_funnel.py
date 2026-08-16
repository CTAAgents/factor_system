"""
tests/factor_engine/test_l1_l2_funnel.py — plans/44 Phase 3 D1/D2 L1→L2 闭环测试

覆盖:
    - funnel_record 读-改-写累计（injected/consumed/promoted + 时间戳）
    - funnel_report 转化率/积压 warning
    - MetaLoop.run() 注入回写接线（monkeypatch module-level funnel_record）
    - SeedManager._record_l1_consumed/_record_l1_promoted 回写接线

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.l1_l2_funnel import funnel_record, funnel_report  # noqa: E402


@pytest.fixture(autouse=True)
def _block_network(monkeypatch) -> None:
    """屏蔽真实网络（MetaLoop.run 触发提取器/感知时快速降级）。"""
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("test env: no network")),
    )


def _tmp_store(tmp_path) -> Any:
    from fts.store.state_db import StateKVStore

    return StateKVStore(tmp_path / "state.db")


class TestFunnelRecord:
    """D1 漏斗累计写回。"""

    def test_increments_read_modify_write(self, tmp_path) -> None:
        store = _tmp_store(tmp_path)
        r1 = funnel_record(store, "energy", injected=5, run_id="r1")
        assert r1["injected"] == 5
        assert r1["injected_at"]
        r2 = funnel_record(store, "energy", consumed=3, run_id="r2")
        assert r2["injected"] == 5
        assert r2["l2_consumed"] == 3
        assert r2["l2_consumed_at"]
        r3 = funnel_record(store, "energy", injected=2, promoted=1, run_id="r3")
        assert r3["injected"] == 7
        assert r3["l2_promoted"] == 1
        assert r3["l2_consumed"] == 3, "既有累计保持"

    def test_market_isolation(self, tmp_path) -> None:
        store = _tmp_store(tmp_path)
        funnel_record(store, "futures", injected=3)
        funnel_record(store, "energy", injected=9)
        assert store.get("meta_loop", "futures/l1_l2_funnel")["injected"] == 3
        assert store.get("meta_loop", "energy/l1_l2_funnel")["injected"] == 9


class TestFunnelReport:
    """D2 报告与积压监控。"""

    def test_conversion_rates(self, tmp_path) -> None:
        store = _tmp_store(tmp_path)
        funnel_record(store, "energy", injected=10, run_id="a")
        funnel_record(store, "energy", consumed=5, run_id="b")
        funnel_record(store, "energy", promoted=2, run_id="c")
        rows = funnel_report(store, markets=("energy",))
        assert len(rows) == 1
        r = rows[0]
        assert r["injected"] == 10
        assert r["l2_consumed"] == 5
        assert r["l2_promoted"] == 2
        assert r["consume_rate"] == 0.5
        assert r["promote_rate"] == 0.4
        assert r["backlog"] == 5
        assert r["warning"] == ""

    def test_backlog_warning(self, tmp_path) -> None:
        store = _tmp_store(tmp_path)
        old = (datetime.now() - timedelta(days=30)).isoformat()
        store.upsert(
            "meta_loop",
            "futures/l1_l2_funnel",
            {
                "injected": 10,
                "l2_consumed": 2,
                "l2_promoted": 0,
                "injected_at": old,
                "l2_consumed_at": old,
                "l2_promoted_at": "",
                "updated_at": old,
            },
        )
        rows = funnel_report(store, markets=("futures",), backlog_days=7)
        assert rows[0]["backlog"] == 8
        assert "积压" in rows[0]["warning"]

    def test_empty_report(self, tmp_path) -> None:
        store = _tmp_store(tmp_path)
        assert funnel_report(store, markets=("futures", "energy")) == []


class TestMetaLoopInjectedWriteback:
    """D1 接线: MetaLoop.run() 注入数回写漏斗。"""

    def test_run_records_injected(self, tmp_path, monkeypatch) -> None:
        from fts.factor_engine import meta_loop as ml
        from fts.factor_engine.meta_loop import MetaLoop

        calls: list[dict] = []
        monkeypatch.setattr(ml, "funnel_record", lambda **kw: calls.append(kw) or {})

        meta_dir = tmp_path / "meta"
        meta_dir.mkdir(exist_ok=True)
        pool_path = tmp_path / "factor_pool.json"
        inject_dir = tmp_path / "l1_injected"
        inject_dir.mkdir(exist_ok=True)
        debates_dir = tmp_path / "debates"
        debates_dir.mkdir(exist_ok=True)

        loop = MetaLoop(
            memory_dir=meta_dir,
            factor_pool_path=pool_path,
            inject_dir=inject_dir,
            debates_dir=debates_dir,
            web_collector=None,
            llm_client=None,
            state_store=_tmp_store(tmp_path),
        )
        result = loop.run(max_bootstraps=2)
        assert result.status == "completed"
        assert calls, "run() 应回写漏斗"
        assert calls[0]["market"] == "futures"
        assert calls[0]["injected"] == result.candidates_injected


class TestSeedManagerWriteback:
    """D1 接线: L2 消费/晋升回写漏斗。"""

    @staticmethod
    def _manager(tmp_path):
        from fts.factor_engine.evolution_seeds import SeedManager

        owner = MagicMock()
        owner.market = "energy"
        owner.inject_dir = tmp_path / "l1_injected"
        owner.factor_pool_path = str(tmp_path / "factor_pool.json")
        return SeedManager(owner)

    def test_consumed_records(self, tmp_path, monkeypatch) -> None:
        from fts.factor_engine import evolution_seeds as es

        calls: list[dict] = []
        monkeypatch.setattr(es, "funnel_record", lambda **kw: calls.append(kw) or {})
        mgr = self._manager(tmp_path)
        mgr._record_l1_consumed(["cand_a", "cand_b"], "t")  # noqa: SLF001
        assert calls == [{"market": "energy", "consumed": 2, "run_id": "t"}]

    def test_consumed_empty_noop(self, tmp_path, monkeypatch) -> None:
        from fts.factor_engine import evolution_seeds as es

        calls: list[dict] = []
        monkeypatch.setattr(es, "funnel_record", lambda **kw: calls.append(kw) or {})
        mgr = self._manager(tmp_path)
        mgr._record_l1_consumed([], "t")  # noqa: SLF001
        assert calls == []

    def test_promoted_only_for_l1_candidate(self, tmp_path, monkeypatch) -> None:
        from fts.factor_engine import evolution_seeds as es

        calls: list[dict] = []
        monkeypatch.setattr(es, "funnel_record", lambda **kw: calls.append(kw) or {})
        mgr = self._manager(tmp_path)
        # L1 候选（parent_id=cand_ 前缀）→ 回写
        mgr._record_l1_promoted({"parent_id": "cand_abc", "name": "f"}, "t")  # noqa: SLF001
        assert calls == [{"market": "energy", "promoted": 1, "run_id": "t"}]
        # base 种子（parent_id=None）→ 无操作
        mgr._record_l1_promoted({"parent_id": None, "name": "momentum"}, "t")  # noqa: SLF001
        assert len(calls) == 1
