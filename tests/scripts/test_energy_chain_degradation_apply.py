"""tests/scripts/test_energy_chain_degradation_apply.py — 能源链退化检测 apply 落库测试。

覆盖 scripts/energy_chain_degradation_dryrun.apply_b_results：
    - CRITICAL → is_elite=false/status=degraded + elite JSON 移入 _deprecated + status_history 留痕
    - WARN     → status=shadow（is_elite=true 保留，观察池）且 JSON 不移除
    - OK       → 状态不变仅追加 metadata.degradation_revalidation 留痕
    - 因子不存在 → failed 计数不中断
    - 落库统计返回准确

版本: v0.1.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts.energy_chain_degradation_dryrun import TeeLogger, apply_b_results  # noqa: E402
from fts.factor_engine.factor_db.repository import FactorRepository  # noqa: E402


class _NullLog:
    def __call__(self, msg: str = "", *args, **kwargs) -> None:
        pass

    def log(self, msg: str = "", *args, **kwargs) -> None:
        pass


def _seed_factor(repo: FactorRepository, fid: str, name: str, code: str) -> None:
    repo.create_factor(
        {
            "factor_id": fid,
            "name": name,
            "code": code,
            "market": "energy",
            "status": "active",
            "is_elite": True,
            "metadata": {},
        }
    )


def _write_elite_json(elite_dir: Path, fid: str, name: str) -> Path:
    elite_dir.mkdir(parents=True, exist_ok=True)
    fp = elite_dir / f"{fid}.json"
    fp.write_text(json.dumps({"factor_id": fid, "name": name}), encoding="utf-8")
    return fp


class TestApplyBResults:
    def test_critical_degrades_and_moves_json(self, tmp_path: Path) -> None:
        db = tmp_path / "energy_qa.duckdb"
        elite_dir = tmp_path / "elite"
        with FactorRepository(db_path=db, market="energy") as repo:
            _seed_factor(repo, "fct_00000001", "crit_f", "code1")
        _write_elite_json(elite_dir, "fct_00000001", "crit_f")

        results = [
            {
                "factor_id": "fct_00000001",
                "name": "crit_f",
                "status": "CRITICAL",
                "hist_ic": 0.20,
                "curr_ic": 0.01,
                "ic_drop": 0.95,
                "reasons": "|IC|<0.02+降幅>30%",
            },
        ]
        stats = apply_b_results(results=results, log=_NullLog(), elite_dir=elite_dir, trace_id="t", db_path=db)
        assert stats == {"degraded": 1, "shadow": 0, "retain": 0, "failed": 0}

        with FactorRepository(db_path=db, market="energy") as repo:
            f = repo.get_factor("fct_00000001")
            assert f["status"] == "degraded"
            assert f["is_elite"] is False
            meta = f["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            assert meta["degradation_revalidation"]["status"] == "CRITICAL"
            n = repo._execute("SELECT COUNT(*) FROM factor_status_history").fetchone()[0]
            assert n == 1

        assert not (elite_dir / "fct_00000001.json").exists()
        dep = elite_dir / "_deprecated" / "fct_00000001.json"
        assert dep.exists()

    def test_critical_without_json_still_degrades(self, tmp_path: Path) -> None:
        db = tmp_path / "energy_qa.duckdb"
        elite_dir = tmp_path / "elite"
        with FactorRepository(db_path=db, market="energy") as repo:
            _seed_factor(repo, "fct_00000002", "crit_nojson", "code2")

        results = [{"factor_id": "fct_00000002", "name": "crit_nojson", "status": "CRITICAL", "hist_ic": 0.1, "curr_ic": 0.01, "ic_drop": 0.9, "reasons": "x"}]
        stats = apply_b_results(results=results, log=_NullLog(), elite_dir=elite_dir, trace_id="t", db_path=db)
        assert stats == {"degraded": 1, "shadow": 0, "retain": 0, "failed": 0}

        with FactorRepository(db_path=db, market="energy") as repo:
            f = repo.get_factor("fct_00000002")
            assert f["status"] == "degraded"
            assert f["is_elite"] is False

    def test_warn_shadow_keeps_json(self, tmp_path: Path) -> None:
        db = tmp_path / "energy_qa.duckdb"
        elite_dir = tmp_path / "elite"
        with FactorRepository(db_path=db, market="energy") as repo:
            _seed_factor(repo, "fct_00000003", "warn_f", "code3")
        _write_elite_json(elite_dir, "fct_00000003", "warn_f")

        results = [{"factor_id": "fct_00000003", "name": "warn_f", "status": "WARN", "hist_ic": 0.15, "curr_ic": 0.05, "ic_drop": 0.67, "reasons": "降幅>30%"}]
        stats = apply_b_results(results=results, log=_NullLog(), elite_dir=elite_dir, trace_id="t", db_path=db)
        assert stats == {"degraded": 0, "shadow": 1, "retain": 0, "failed": 0}

        with FactorRepository(db_path=db, market="energy") as repo:
            f = repo.get_factor("fct_00000003")
            assert f["status"] == "shadow"
            assert f["is_elite"] is True
            meta = f["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            assert meta["degradation_revalidation"]["status"] == "WARN"

        assert (elite_dir / "fct_00000003.json").exists()
        assert not (elite_dir / "_deprecated").exists()

    def test_ok_retain_appends_trace(self, tmp_path: Path) -> None:
        db = tmp_path / "energy_qa.duckdb"
        elite_dir = tmp_path / "elite"
        with FactorRepository(db_path=db, market="energy") as repo:
            _seed_factor(repo, "fct_00000004", "ok_f", "code4")

        results = [{"factor_id": "fct_00000004", "name": "ok_f", "status": "OK", "hist_ic": 0.2, "curr_ic": 0.18, "ic_drop": 0.1, "reasons": "—"}]
        stats = apply_b_results(results=results, log=_NullLog(), elite_dir=elite_dir, trace_id="t", db_path=db)
        assert stats == {"degraded": 0, "shadow": 0, "retain": 1, "failed": 0}

        with FactorRepository(db_path=db, market="energy") as repo:
            f = repo.get_factor("fct_00000004")
            assert f["status"] == "active"
            assert f["is_elite"] is True
            meta = f["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            assert meta["degradation_revalidation"]["status"] == "OK"

    def test_missing_factor_failed_not_abort(self, tmp_path: Path) -> None:
        db = tmp_path / "energy_qa.duckdb"
        elite_dir = tmp_path / "elite"
        with FactorRepository(db_path=db, market="energy") as repo:
            _seed_factor(repo, "fct_00000005", "real_f", "code5")

        results = [
            {"factor_id": "fct_missing", "name": "ghost", "status": "CRITICAL", "hist_ic": 0.1, "curr_ic": 0.01, "ic_drop": 0.9, "reasons": "x"},
            {"factor_id": "fct_00000005", "name": "real_f", "status": "OK", "hist_ic": 0.2, "curr_ic": 0.18, "ic_drop": 0.1, "reasons": "—"},
        ]
        stats = apply_b_results(results=results, log=_NullLog(), elite_dir=elite_dir, trace_id="t", db_path=db)
        assert stats == {"degraded": 0, "shadow": 0, "retain": 1, "failed": 1}

    def test_teelogger_is_callable_and_logs_to_file(self, tmp_path: Path) -> None:
        p = tmp_path / "apply.log"
        lg = TeeLogger(p)
        try:
            lg("hello %s", "world")
            lg.log("second")
        finally:
            lg.close()
        text = p.read_text(encoding="utf-8")
        assert "hello world" in text
        assert "second" in text
