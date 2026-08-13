"""tests/monitor/test_reaudit.py — 新标准准入复审模块测试（fts.monitor.reaudit）。

覆盖:
    - summarize_result 处置规则（retain/shadow/retire/error 四分支）
    - build_factor_program（code 缺失）
    - apply_reaudit_results（隔离 DuckDB：retain/shadow/retire 回写 + error 跳过
      + factor_status_history 留痕）

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.monitor.reaudit import (  # noqa: E402
    DECISION_ERROR,
    DECISION_RETAIN,
    DECISION_RETIRE,
    DECISION_SHADOW,
    apply_reaudit_results,
    build_factor_program,
    summarize_result,
)
from fts.factor_engine.factor_db.repository import FactorRepository  # noqa: E402


# ─── 处置规则 ───────────────────────────────────────────


class TestSummarizeResult:
    def test_all_pass_retain(self) -> None:
        rec = {
            "evaluation_passed": True,
            "verifier_passed": True,
            "audit_passed": True,
            "robustness_passed": True,
        }
        assert summarize_result(rec) == DECISION_RETAIN

    def test_robustness_failed_shadow(self) -> None:
        rec = {
            "evaluation_passed": True,
            "verifier_passed": True,
            "audit_passed": True,
            "robustness_passed": False,
        }
        assert summarize_result(rec) == DECISION_SHADOW

    def test_audit_failed_retire(self) -> None:
        rec = {
            "evaluation_passed": True,
            "verifier_passed": True,
            "audit_passed": False,
            "robustness_passed": True,
        }
        assert summarize_result(rec) == DECISION_RETIRE

    def test_evaluation_failed_retire(self) -> None:
        rec = {
            "evaluation_passed": False,
            "verifier_passed": True,
            "audit_passed": True,
            "robustness_passed": True,
        }
        assert summarize_result(rec) == DECISION_RETIRE

    def test_error_wins(self) -> None:
        rec = {"error": "exec failed", "evaluation_passed": True, "audit_passed": True, "robustness_passed": True}
        assert summarize_result(rec) == DECISION_ERROR


# ─── FactorProgram 构造 ─────────────────────────────────


class TestBuildFactorProgram:
    def test_missing_code_returns_none(self) -> None:
        assert build_factor_program({"factor_id": "fct_00000001", "code": ""}) is None

    def test_builds_program(self) -> None:
        fp = build_factor_program(
            {"factor_id": "fct_00000001", "name": "test_factor", "code": "def factor_program(data, params): ..."}
        )
        assert fp is not None
        assert fp["factor_id"] == "fct_00000001"
        assert fp["market"] == "futures"


# ─── 处置回写（隔离 DuckDB） ────────────────────────────


def _seed_factor(repo: FactorRepository, fid: str, name: str, code: str) -> None:
    repo.create_factor(
        {
            "factor_id": fid,
            "name": name,
            "code": code,
            "market": "futures",
            "status": "active",
            "is_elite": True,
            "metadata": {},
        }
    )


class TestApplyReauditResults:
    def test_three_decisions_and_error_skip(self, tmp_path: Path) -> None:
        db = tmp_path / "reaudit.duckdb"
        with FactorRepository(db_path=db, market="futures") as repo:
            _seed_factor(repo, "fct_00000001", "retain_f", "code1")
            _seed_factor(repo, "fct_00000002", "shadow_f", "code2")
            _seed_factor(repo, "fct_00000003", "retire_f", "code3")
            _seed_factor(repo, "fct_00000004", "error_f", "code4")

        results = [
            {
                "factor_id": "fct_00000001",
                "processed_at": "2026-08-13T00:00:00",
                "ic": 0.2,
                "sharpe": 3.0,
                "audit_passed": True,
                "audit_failures": [],
                "robustness_pass_rate": 0.8,
                "grade": "B",
                "decision": DECISION_RETAIN,
            },
            {
                "factor_id": "fct_00000002",
                "processed_at": "2026-08-13T00:00:00",
                "ic": 0.2,
                "sharpe": 3.0,
                "audit_passed": True,
                "audit_failures": [],
                "robustness_pass_rate": 0.545,
                "grade": "B",
                "decision": DECISION_SHADOW,
            },
            {
                "factor_id": "fct_00000003",
                "processed_at": "2026-08-13T00:00:00",
                "ic": 0.02,
                "sharpe": 0.5,
                "audit_passed": False,
                "audit_failures": ["oos_consistency"],
                "robustness_pass_rate": 0.8,
                "grade": "C",
                "decision": DECISION_RETIRE,
            },
            {
                "factor_id": "fct_00000004",
                "error": "exec failed",
                "decision": DECISION_ERROR,
            },
        ]

        summary = apply_reaudit_results(results, trace_id="fts.reaudit.test", db_path=db)
        assert summary == {"retain": 1, "shadow": 1, "retire": 1}

        import json

        def _meta(f: dict) -> dict:
            m = f["metadata"]
            return m if isinstance(m, dict) else json.loads(m)

        with FactorRepository(db_path=db, market="futures") as repo:
            f1 = repo.get_factor("fct_00000001")
            assert f1["status"] == "active"
            m1 = _meta(f1)
            assert m1["reaudit"]["decision"] == DECISION_RETAIN

            f2 = repo.get_factor("fct_00000002")
            assert f2["status"] == "active"
            m2 = _meta(f2)
            assert m2["shadow_pool"]["reason"] == "reaudit_robustness_failed"
            assert m2["reaudit"]["decision"] == DECISION_SHADOW

            f3 = repo.get_factor("fct_00000003")
            assert f3["status"] == "retired"
            m3 = _meta(f3)
            assert m3["reaudit"]["decision"] == DECISION_RETIRE

            # error 因子不处置：status 保持 active 且无 reaudit 标记
            f4 = repo.get_factor("fct_00000004")
            assert f4["status"] == "active"
            assert "reaudit" not in _meta(f4)

            # status_history 留痕（retain/shadow 各 1 + retire 1 = 3 条）
            n = repo._execute("SELECT COUNT(*) FROM factor_status_history").fetchone()[0]
            assert n == 3

    def test_retire_writes_status_history(self, tmp_path: Path) -> None:
        db = tmp_path / "reaudit2.duckdb"
        with FactorRepository(db_path=db, market="futures") as repo:
            _seed_factor(repo, "fct_00000001", "retire_f", "code1")
        apply_reaudit_results(
            [
                {
                    "factor_id": "fct_00000001",
                    "processed_at": "2026-08-13T00:00:00",
                    "audit_passed": False,
                    "audit_failures": ["cross_symbol"],
                    "robustness_pass_rate": 0.9,
                    "decision": DECISION_RETIRE,
                }
            ],
            db_path=db,
        )
        with FactorRepository(db_path=db, market="futures") as repo:
            f = repo.get_factor("fct_00000001")
            assert f["status"] == "retired"
            row = repo._execute(
                "SELECT from_status, to_status FROM factor_status_history WHERE factor_id=?",
                ["fct_00000001"],
            ).fetchone()
            assert row is not None
            assert row[0] == "active"
            assert row[1] == "retired"
