"""tests/factor_engine/factor_db/test_migrate_from_json.py — JSON→DuckDB 迁移补充测试。

覆盖缺口（相对 tests/factor_engine/test_factor_db.py::TestMigration）:
    - compute_code_hash / parse_factor_json / extract_evaluation_metrics 单元级
    - migrate_factors 错误路径：目录不存在 / JSON 非 dict / 进度日志 / 异常回滚
    - main() CLI：dry-run / verbose / 目录不存在 / 失败返回码
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fts.factor_engine.factor_db.migrate_from_json import (  # noqa: E402
    compute_code_hash,
    extract_evaluation_metrics,
    main,
    migrate_factors,
    parse_factor_json,
)


# ─── 工具函数 ─────────────────────────────────────────────


def _write_factor(elite_dir: Path, name: str, data: dict) -> Path:
    p = elite_dir / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _sample_factor_data() -> dict:
    return {
        "factor_id": "fct_mig_001",
        "name": "mig_factor",
        "code": "def factor_program(data, params):\n    return data['close']",
        "params": {"window": 20},
        "signature": {"input": ["close"]},
        "economic_logic": {"narrative": "test"},
        "source": "seed",
        "generation": 0,
        "decay_6m": 0.03,
        "market": "futures",
        "evaluation": {
            "trace_id": "eval_tr_1",
            "evaluated_at": "2026-08-01T00:00:00",
            "passed": True,
            "failure_reasons": [],
            "level_1_backtest": {
                "ic": 0.06,
                "icir": 1.5,
                "sharpe": 2.8,
                "max_drawdown": 0.12,
                "turnover_monthly": 0.3,
                "t_stat": 2.1,
                "monotonicity": True,
                "oos_ratio": 0.7,
            },
            "level_2_economic": {
                "theory": 7,
                "behavioral": 6,
                "microstructure": 5,
                "institutional": 8,
                "dimensions_passed": 3,
            },
            "level_3_multiple": {
                "bonferroni_p": 0.01,
                "fdr_q": 0.02,
                "effective_n_factors": 3,
                "adjusted_t": 2.5,
                "passed": True,
            },
        },
        "correlation_metadata": {"max_pearson": 0.4},
    }


# ─── compute_code_hash ────────────────────────────────────


class TestComputeCodeHash:
    def test_normal(self):
        h = compute_code_hash("def factor_program(data, params):\n    pass")
        assert len(h) == 64  # sha256 hex

    def test_deterministic(self):
        assert compute_code_hash("abc") == compute_code_hash("abc")
        assert compute_code_hash("abc") != compute_code_hash("abd")


# ─── parse_factor_json ────────────────────────────────────


class TestParseFactorJson:
    def test_valid_json(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text(json.dumps({"factor_id": "x"}), encoding="utf-8")
        data = parse_factor_json(p)
        assert data == {"factor_id": "x"}

    def test_invalid_json_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert parse_factor_json(p) is None

    def test_type_error_returns_none(self, tmp_path, monkeypatch):
        """json.loads 抛 TypeError（非 UTF-8 字节等）时返回 None。"""
        p = tmp_path / "bytes.json"
        p.write_text("{}", encoding="utf-8")

        def _bad_loads(*args, **kwargs):
            raise TypeError("bytes-like object required")

        monkeypatch.setattr("fts.factor_engine.factor_db.migrate_from_json.json.loads", _bad_loads)
        assert parse_factor_json(p) is None


# ─── extract_evaluation_metrics ───────────────────────────


class TestExtractEvaluationMetrics:
    def test_full_metrics(self):
        metrics = extract_evaluation_metrics(_sample_factor_data())
        assert metrics["ic"] == 0.06
        assert metrics["icir"] == 1.5
        assert metrics["sharpe"] == 2.8
        assert metrics["max_drawdown"] == 0.12
        assert metrics["turnover_monthly"] == 0.3
        assert metrics["t_stat"] == 2.1
        assert metrics["monotonicity"] is True
        assert metrics["oos_ratio"] == 0.7
        assert metrics["l2_theory"] == 7
        assert metrics["l2_dims_passed"] == 3
        assert metrics["l3_bonferroni_p"] == 0.01
        assert metrics["l3_fdr_q"] == 0.02
        assert metrics["l3_effective_n"] == 3
        assert metrics["l3_adjusted_t"] == 2.5
        assert metrics["l3_passed"] is True
        assert metrics["overall_passed"] is True

    def test_missing_evaluation_defaults(self):
        """evaluation 缺失/不完整时全部回默认值。"""
        metrics = extract_evaluation_metrics({"factor_id": "x"})
        assert metrics["ic"] == 0.0
        assert metrics["icir"] == 0.0
        assert metrics["monotonicity"] is False
        assert metrics["l2_theory"] == 0
        assert metrics["l3_bonferroni_p"] == 1.0
        assert metrics["l3_fdr_q"] == 0.05
        assert metrics["l3_effective_n"] == 1
        assert metrics["l3_passed"] is False
        assert metrics["overall_passed"] is False
        assert metrics["failure_reasons"] == []


# ─── migrate_factors 错误路径 ─────────────────────────────


class TestMigrateFactorsErrors:
    def test_missing_dir_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="精英因子目录不存在"):
            migrate_factors(tmp_path / "no_such_dir", tmp_path / "db.duckdb", dry_run=True)

    def test_json_list_payload_raises(self, tmp_path):
        """JSON 顶层为 list（非 dict）→ data.get 抛异常 → 迁移整体失败并 re-raise。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "list.json", [1, 2, 3])
        db_path = tmp_path / "db.duckdb"
        with pytest.raises(AttributeError):
            migrate_factors(elite_dir, db_path, force=True)

    def test_invalid_json_counts_failed(self, tmp_path):
        """parse 返回 None → failed 计数并跳过该文件。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "bad.json").write_text("{invalid json", encoding="utf-8")
        db_path = tmp_path / "db.duckdb"
        stats = migrate_factors(elite_dir, db_path, force=True)
        assert stats["total_files"] == 1
        assert stats["failed"] == 1
        assert stats["success"] == 0

    def test_duplicate_migration_counts_skipped(self, tmp_path):
        """非 force 重复迁移 → 已存在因子计入 skipped。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "f1.json", _sample_factor_data())
        db_path = tmp_path / "db.duckdb"

        s1 = migrate_factors(elite_dir, db_path)
        assert s1["success"] == 1
        s2 = migrate_factors(elite_dir, db_path)
        assert s2["success"] == 0
        assert s2["skipped"] == 1

    def test_db_insert_failure_raises_and_records_error(self, tmp_path, monkeypatch):
        """DB 写入异常 → stats.errors 记录 + re-raise（覆盖异常回滚路径）。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "f1.json", _sample_factor_data())
        db_path = tmp_path / "db.duckdb"

        import sys as _sys

        class _FakeConn:
            def execute(self, sql, params=None):
                raise RuntimeError("模拟插入失败")

            def close(self):
                pass

        class _FakeDuckDB:
            def connect(self, path):
                return _FakeConn()

        monkeypatch.setitem(_sys.modules, "duckdb", _FakeDuckDB())
        monkeypatch.setattr(
            "fts.factor_engine.factor_db.schema.init_database",
            lambda p: None,
        )
        with pytest.raises(RuntimeError, match="模拟插入失败"):
            migrate_factors(elite_dir, db_path, force=True)

    def test_progress_log_every_100(self, tmp_path, monkeypatch):
        """成功数 % 100 == 0 时打进度日志。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        for i in range(100):
            data = _sample_factor_data()
            data["factor_id"] = f"fct_p{i:03d}"
            data["name"] = f"fct_p{i:03d}"
            _write_factor(elite_dir, f"f_{i:03d}.json", data)

        logged: list[str] = []

        def _capture(msg, *args):
            logged.append(msg % args if args else msg)

        monkeypatch.setattr("fts.factor_engine.factor_db.migrate_from_json.logger.info", _capture)
        stats = migrate_factors(elite_dir, tmp_path / "db.duckdb", dry_run=True)
        assert stats["success"] == 100
        assert any("[Migrate] 进度: 100/100" in m for m in logged)


# ─── main() CLI ───────────────────────────────────────────


class TestMain:
    """main() CLI 入口各分支。"""

    def _run_main(self, monkeypatch, argv: list[str], tmp_path: Path):
        monkeypatch.setattr(
            sys,
            "argv",
            ["migrate_from_json.py", *argv],
        )
        return main()

    def test_main_dry_run(self, tmp_path, monkeypatch):
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "ok.json", _sample_factor_data())
        db_path = tmp_path / "db.duckdb"
        rc = self._run_main(
            monkeypatch,
            ["--elite-dir", str(elite_dir), "--db-path", str(db_path), "--dry-run"],
            tmp_path,
        )
        assert rc == 0

    def test_main_success(self, tmp_path, monkeypatch):
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "ok.json", _sample_factor_data())
        db_path = tmp_path / "db.duckdb"
        rc = self._run_main(
            monkeypatch,
            ["--elite-dir", str(elite_dir), "--db-path", str(db_path)],
            tmp_path,
        )
        assert rc == 0

    def test_main_verbose(self, tmp_path, monkeypatch):
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        _write_factor(elite_dir, "ok.json", _sample_factor_data())
        db_path = tmp_path / "db.duckdb"
        rc = self._run_main(
            monkeypatch,
            ["--elite-dir", str(elite_dir), "--db-path", str(db_path), "--dry-run", "--verbose"],
            tmp_path,
        )
        assert rc == 0

    def test_main_dir_not_found_exits_1(self, tmp_path, monkeypatch):
        """elite 目录不存在 → sys.exit(1)。"""
        with pytest.raises(SystemExit) as e:
            self._run_main(
                monkeypatch,
                ["--elite-dir", str(tmp_path / "missing"), "--dry-run"],
                tmp_path,
            )
        assert e.value.code == 1

    def test_main_failure_returns_1(self, tmp_path, monkeypatch):
        """存在失败因子 → main 返回 1。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "bad.json").write_text("{invalid json", encoding="utf-8")
        db_path = tmp_path / "db.duckdb"
        rc = self._run_main(
            monkeypatch,
            ["--elite-dir", str(elite_dir), "--db-path", str(db_path)],
            tmp_path,
        )
        assert rc == 1

    def test_main_exception_returns_1(self, tmp_path, monkeypatch):
        """migrate_factors 抛异常 → main 捕获并返回 1。"""
        monkeypatch.setattr(
            "fts.factor_engine.factor_db.migrate_from_json.migrate_factors",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        rc = self._run_main(monkeypatch, ["--dry-run"], tmp_path)
        assert rc == 1