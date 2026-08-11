"""plans/29 P2：scripts/migrate_state_to_duckdb.py 测试。

覆盖:
- 权威状态发现（state.json/combo_history/权重/动态池 glob 规则 + 去重）
- 状态入库 + 读回对账（逐字段一致）
- 幂等（重复运行不产生不一致）
- dry-run 不写入 / verify-only 只读
- jsonl（live_feedback）多行导入
- 过程痕迹发现与归档（tar.gz，不删除源）
- CLI 入口
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from fts.store.state_db import StateKVStore
from scripts.migrate_state_to_duckdb import (
    archive_process_traces,
    discover_archivable,
    discover_stateful_sources,
    migrate_state,
)


def _build_memory_tree(root: Path) -> Path:
    """构造测试 memory 树（含权威状态 + 过程痕迹）。"""
    memory = root / "memory"
    # 权威状态
    (memory / "evolution").mkdir(parents=True)
    (memory / "evolution" / "state.json").write_text(
        json.dumps({"run_id": "r1", "status": "circuit_broken", "evolution_method_counts": {"macro": 10}}),
        encoding="utf-8",
    )
    (memory / "meta_loop").mkdir(parents=True)
    (memory / "meta_loop" / "state.json").write_text(
        json.dumps({"run_id": "r2", "status": "completed"}), encoding="utf-8"
    )
    (memory / "portfolio").mkdir(parents=True)
    (memory / "portfolio" / "state.json").write_text(json.dumps({"run_id": "r3", "status": "frozen"}), encoding="utf-8")
    (memory / "portfolio" / "combo_history").mkdir(parents=True)
    (memory / "portfolio" / "combo_history" / "cmb_a.json").write_text(
        json.dumps({"combo_id": "cmb_a", "signals": [{"factor_id": "fct_1", "weight": 0.5}]}), encoding="utf-8"
    )
    (memory / "portfolio" / "combo_history" / "cmb_b.json").write_text(
        json.dumps({"combo_id": "cmb_b"}), encoding="utf-8"
    )
    (memory / "portfolio" / "futures_dynamic_pool.json").write_text(
        json.dumps({"pool_size": 2, "pool": ["RB0", "CU0"]}), encoding="utf-8"
    )
    (memory / "portfolio" / "live_feedback.jsonl").write_text(
        '{"factor_id": "fct_1", "ic": 0.1}\n{"factor_id": "fct_2", "ic": 0.2}\n', encoding="utf-8"
    )
    # 过程痕迹
    (memory / "evolution" / "traces").mkdir(parents=True)
    (memory / "evolution" / "traces" / "l2_r_g0_fail_fct_1.json").write_text(
        json.dumps({"stage": "audit_fail"}), encoding="utf-8"
    )
    (memory / "portfolio" / "agent_proposals").mkdir(parents=True)
    (memory / "portfolio" / "agent_proposals" / "prop_1.json").write_text(
        json.dumps({"proposal": "x"}), encoding="utf-8"
    )
    return memory


class TestDiscover:
    def test_stateful_sources_found(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        sources = discover_stateful_sources(memory)
        keys = {(ns, k) for ns, k, _, _ in sources}
        assert ("evolution", "state") in keys
        assert ("meta_loop", "state") in keys
        assert ("portfolio", "state") in keys
        assert ("portfolio", "combo_history/cmb_a") in keys
        assert ("portfolio", "combo_history/cmb_b") in keys
        assert ("portfolio", "futures_dynamic_pool") in keys
        assert ("portfolio", "live_feedback") in keys
        # 去重：portfolio/*.json 顶层不应重复匹配 state.json
        assert len(sources) == len({str(fp.resolve()) for _, _, fp, _ in sources})

    def test_archivable_discovered(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        files = discover_archivable(memory)
        names = {p.name for p in files}
        assert "l2_r_g0_fail_fct_1.json" in names
        assert "prop_1.json" in names


class TestMigrate:
    def test_migrate_and_verify_roundtrip(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        db = tmp_path / "state.duckdb"
        stats = migrate_state(db_path=db, memory_root=memory, trace_id="t1")
        assert stats["migrated"] == stats["total"] >= 7
        assert stats["mismatched"] == 0
        assert stats["verified"] == stats["total"]
        # combo_history 逐字段对账
        store = StateKVStore(db)
        assert store.get("portfolio", "combo_history/cmb_a")["signals"][0]["factor_id"] == "fct_1"
        assert store.get("portfolio", "live_feedback") == [
            {"factor_id": "fct_1", "ic": 0.1},
            {"factor_id": "fct_2", "ic": 0.2},
        ]
        store.close()

    def test_idempotent_rerun(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        db = tmp_path / "state.duckdb"
        migrate_state(db_path=db, memory_root=memory, trace_id="t1")
        stats2 = migrate_state(db_path=db, memory_root=memory, trace_id="t2")
        assert stats2["mismatched"] == 0
        assert stats2["verified"] == stats2["total"]
        store = StateKVStore(db)
        assert len(store.history(namespace="portfolio", key="state")) == 2  # 两次追加
        store.close()

    def test_dry_run_no_write(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        db = tmp_path / "state.duckdb"
        stats = migrate_state(db_path=db, memory_root=memory, dry_run=True, trace_id="t1")
        assert stats["migrated"] == stats["total"]
        assert not db.exists()

    def test_verify_only_no_write(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        db = tmp_path / "state.duckdb"
        migrate_state(db_path=db, memory_root=memory, trace_id="t1")
        store = StateKVStore(db)
        before = len(store.history())
        store.close()
        stats = migrate_state(db_path=db, memory_root=memory, verify_only=True, trace_id="t2")
        assert stats["migrated"] == 0
        assert stats["verified"] == stats["total"]
        store = StateKVStore(db)
        assert len(store.history()) == before  # verify-only 不追加历史
        store.close()

    def test_bad_state_json_reported(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        (memory / "portfolio" / "state.json").write_text("{ not json", encoding="utf-8")
        db = tmp_path / "state.duckdb"
        stats = migrate_state(db_path=db, memory_root=memory, trace_id="t1")
        assert stats["failed"] == 1
        assert stats["migrated"] == stats["total"] - 1


class TestArchive:
    def test_archive_creates_tar_and_keeps_source(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        ar = tmp_path / "archive"
        stats = archive_process_traces(memory_root=memory, archive_root=ar, trace_id="t1")
        assert stats["total_files"] == 2
        assert Path(stats["tar_path"]).exists()
        with tarfile.open(stats["tar_path"], "r:gz") as tar:
            names = tar.getnames()
        assert any("traces" in n for n in names)
        assert any("agent_proposals" in n for n in names)
        # 复制语义：源文件保留
        assert (memory / "evolution" / "traces" / "l2_r_g0_fail_fct_1.json").exists()
        assert (memory / "portfolio" / "agent_proposals" / "prop_1.json").exists()

    def test_archive_dry_run_counts_only(self, tmp_path):
        memory = _build_memory_tree(tmp_path)
        ar = tmp_path / "archive"
        stats = archive_process_traces(memory_root=memory, archive_root=ar, dry_run=True, trace_id="t1")
        assert stats["total_files"] == 2
        assert stats["tar_path"] == ""
        assert not ar.exists()


class TestCli:
    def test_main_dry_run(self, tmp_path, monkeypatch):
        memory = _build_memory_tree(tmp_path)
        monkeypatch.setattr(
            "sys.argv", ["migrate_state_to_duckdb", "--dry-run", "--state-dir", str(tmp_path / "memory")]
        )
        import scripts.migrate_state_to_duckdb as mod

        monkeypatch.setattr(mod, "MEMORY_ROOT", memory)
        assert mod.main() == 0
