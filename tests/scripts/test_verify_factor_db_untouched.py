"""GAP-129：真实因子库零污染护栏脚本测试（隔离 DuckDB）。

覆盖: snapshot 生成 / 相同快照 check 通过 / 插入·修改·建库差异检出 / 双侧 absent 通过。
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

import fts.factor_engine.factor_db.schema as schema
from scripts.verify_factor_db_untouched import main


def _make_db(path: Path) -> None:
    """构造最小三表因子库（脚本动态列指纹不依赖真实表结构）。"""
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE factor_catalog (factor_id VARCHAR PRIMARY KEY, name VARCHAR, code VARCHAR, status VARCHAR)"
    )
    conn.execute("INSERT INTO factor_catalog VALUES ('fct_a','A','def factor_program(c,p): return c','active')")
    conn.execute(
        "CREATE TABLE factor_quality_scores (score_id VARCHAR PRIMARY KEY, factor_id VARCHAR, total_score DOUBLE)"
    )
    conn.execute("INSERT INTO factor_quality_scores VALUES ('q1','fct_a',80.0)")
    conn.execute(
        "CREATE TABLE factor_audit_reports (report_id VARCHAR PRIMARY KEY, factor_id VARCHAR, passed BOOLEAN)"
    )
    conn.execute("INSERT INTO factor_audit_reports VALUES ('r1','fct_a',true)")
    conn.close()


def _redirect(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """将 get_db_path 路由指向 tmp 隔离库。"""
    futures_path = tmp_path / "futures.duckdb"
    energy_path = tmp_path / "energy.duckdb"
    monkeypatch.setattr(schema, "DATABASE_PATH_FUTURES", futures_path)
    monkeypatch.setattr(schema, "DATABASE_PATH_ENERGY", energy_path)
    return futures_path, energy_path


def _run(mode: str, snapshot_file: Path) -> int:
    return main(
        [
            "--mode",
            mode,
            "--snapshot-file",
            str(snapshot_file),
            "--markets",
            "futures,energy",
            "--trace-id",
            "test_trace",
        ]
    )


class TestSnapshot:
    def test_snapshot_creates_file_with_counts(self, tmp_path, monkeypatch) -> None:
        futures_path, energy_path = _redirect(tmp_path, monkeypatch)
        _make_db(futures_path)
        snapshot_file = tmp_path / "snapshot.json"

        rc = _run("snapshot", snapshot_file)

        assert rc == 0
        assert snapshot_file.exists()
        data = json.loads(snapshot_file.read_text(encoding="utf-8"))
        assert data["trace_id"] == "test_trace"
        futs = data["databases"]["futures"]
        assert futs["absent"] is False
        assert futs["tables"]["factor_catalog"]["count"] == 1
        assert futs["tables"]["factor_quality_scores"]["count"] == 1
        assert futs["tables"]["factor_audit_reports"]["count"] == 1
        # 未建库的 energy 记录 absent
        assert data["databases"]["energy"]["absent"] is True


class TestCheck:
    def test_check_identical_passes(self, tmp_path, monkeypatch) -> None:
        futures_path, _ = _redirect(tmp_path, monkeypatch)
        _make_db(futures_path)
        snapshot_file = tmp_path / "snapshot.json"

        assert _run("snapshot", snapshot_file) == 0
        assert _run("check", snapshot_file) == 0

    def test_check_detects_insert(self, tmp_path, monkeypatch, capsys) -> None:
        futures_path, _ = _redirect(tmp_path, monkeypatch)
        _make_db(futures_path)
        snapshot_file = tmp_path / "snapshot.json"
        assert _run("snapshot", snapshot_file) == 0

        conn = duckdb.connect(str(futures_path))
        try:
            conn.execute("INSERT INTO factor_catalog VALUES ('fct_b','B','def factor_program(c,p): return c','active')")
        finally:
            conn.close()

        rc = _run("check", snapshot_file)
        assert rc == 1
        out = capsys.readouterr().out
        assert "COUNT 变化" in out
        assert "factor_catalog" in out

    def test_check_detects_content_modification(self, tmp_path, monkeypatch, capsys) -> None:
        """同 COUNT 下行数据被修改（指纹差异）。"""
        futures_path, _ = _redirect(tmp_path, monkeypatch)
        _make_db(futures_path)
        snapshot_file = tmp_path / "snapshot.json"
        assert _run("snapshot", snapshot_file) == 0

        conn = duckdb.connect(str(futures_path))
        try:
            conn.execute("UPDATE factor_quality_scores SET total_score = 90.0 WHERE score_id = 'q1'")
        finally:
            conn.close()

        rc = _run("check", snapshot_file)
        assert rc == 1
        assert "内容指纹变化" in capsys.readouterr().out

    def test_check_detects_db_created(self, tmp_path, monkeypatch, capsys) -> None:
        """基线 absent → 测试运行创建真实库（隔离被绕过的核心回归场景）。"""
        futures_path, _ = _redirect(tmp_path, monkeypatch)
        snapshot_file = tmp_path / "snapshot.json"
        assert _run("snapshot", snapshot_file) == 0  # futures 库不存在

        _make_db(futures_path)  # 模拟测试写真实库

        rc = _run("check", snapshot_file)
        assert rc == 1
        assert "真实库被创建" in capsys.readouterr().out

    def test_check_absent_both_ok(self, tmp_path, monkeypatch) -> None:
        """CI 无 data/ 场景：双侧 absent 视为零污染（通过）。"""
        _redirect(tmp_path, monkeypatch)
        snapshot_file = tmp_path / "snapshot.json"

        assert _run("snapshot", snapshot_file) == 0
        assert _run("check", snapshot_file) == 0
