"""fts catalog verify --backfill — JSON 快照回填对齐测试（GAP-119）。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_FTS_ROOT = Path(__file__).resolve().parents[1]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.cli import _backfill_json_snapshots, _cmd_catalog_verify, _scan_json_snapshots  # noqa: E402
from fts.factor_engine.factor_db.repository import FactorRepository  # noqa: E402


# ─── _scan_json_snapshots ────────────────────────────────


def test_scan_empty_dir(tmp_path: Path) -> None:
    elite = tmp_path / "elite"
    elite.mkdir(parents=True, exist_ok=True)
    assert _scan_json_snapshots(elite, "futures") == {}


def test_scan_skips_underscore_files(tmp_path: Path) -> None:
    elite = tmp_path / "elite"
    elite.mkdir()
    (elite / "_l2_seed_correlation_index.json").write_text('{"n": 1}', encoding="utf-8")
    (elite / "f1.json").write_text('{"factor_id": "f1", "market": "futures", "status": "active"}', encoding="utf-8")
    out = _scan_json_snapshots(elite, "futures")
    assert set(out) == {"f1"}
    assert out["f1"]["market"] == "futures"


def test_scan_ignores_broken_json(tmp_path: Path) -> None:
    elite = tmp_path / "elite"
    elite.mkdir()
    (elite / "bad.json").write_text("{broken", encoding="utf-8")
    assert _scan_json_snapshots(elite, "futures") == {}


# ─── _backfill_json_snapshots（真实临时 DuckDB） ─────────


def _make_repo(tmp_path: Path) -> FactorRepository:
    repo = FactorRepository(db_path=tmp_path / "cat.duckdb", market="futures")
    repo.create_factor(
        {
            "factor_id": "fct_a1",
            "name": "alpha1",
            "code": "close - close.shift(1)",
            "market": "futures",
            "status": "active",
            "sharpe": 1.5,
            "ic": 0.04,
        }
    )
    repo.create_factor(
        {
            "factor_id": "fct_b2",
            "name": "alpha2",
            "code": "volume.rolling(5).mean()",
            "market": "futures",
            "status": "active",
        }
    )
    return repo


def test_backfill_writes_missing_snapshots(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    elite = tmp_path / "elite"
    n = _backfill_json_snapshots(repo, elite, {"fct_a1", "fct_b2"})
    assert n == 2
    for fid in ("fct_a1", "fct_b2"):
        fp = elite / f"{fid}.json"
        assert fp.exists()
        data = __import__("json").loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == fid
        assert data["market"] == "futures"
    repo.close()


def test_backfill_skips_existing_snapshots(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    elite = tmp_path / "elite"
    elite.mkdir()
    (elite / "fct_a1.json").write_text('{"factor_id": "fct_a1", "keep": true}', encoding="utf-8")
    n = _backfill_json_snapshots(repo, elite, {"fct_a1", "fct_b2"})
    assert n == 1
    assert "keep" in __import__("json").loads((elite / "fct_a1.json").read_text(encoding="utf-8"))
    repo.close()


def test_backfill_skips_unknown_factor(tmp_path: Path, caplog) -> None:
    repo = _make_repo(tmp_path)
    elite = tmp_path / "elite"
    n = _backfill_json_snapshots(repo, elite, {"no_such_factor"})
    assert n == 0
    assert not (elite / "no_such_factor.json").exists()
    repo.close()


# ─── _cmd_catalog_verify --backfill 集成 ─────────────────


class _FakeRepo:
    """模拟 FactorRepository：5 列查询行 + get_factor/get_evaluations。"""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def _get_conn(self):
        conn = MagicMock()
        res = MagicMock()
        res.fetchall.return_value = self._rows
        conn.execute.return_value = res
        return conn

    def get_factor(self, factor_id: str) -> dict | None:
        for r in self._rows:
            if r[0] == factor_id:
                return {"factor_id": r[0], "name": r[1], "market": r[3], "status": r[4]}
        return None

    def get_evaluations(self, factor_id: str, limit: int = 20):  # noqa: ARG002
        return []


def _run_verify(monkeypatch, tmp_path: Path, rows: list[tuple], backfill: bool) -> int:
    db_path = tmp_path / "cat.duckdb"
    db_path.write_bytes(b"x")  # 存在性检查
    elite = tmp_path / "elite"
    elite.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("fts.cli.get_config", lambda: MagicMock(get_elite_dir=lambda mkt: str(elite)))
    monkeypatch.setattr("fts.cli._get_catalog_db_path", lambda mkt: db_path)
    monkeypatch.setattr("fts.cli._load_factor_repo", lambda market: _FakeRepo(rows))

    args = MagicMock(spec=["json", "backfill"])
    args.json = False
    args.backfill = backfill
    return _cmd_catalog_verify(args)


def test_verify_without_backfill_reports_inconsistent(monkeypatch, tmp_path: Path) -> None:
    rows = [("fct_a1", "alpha1", "futures", "futures", "active")]
    rc = _run_verify(monkeypatch, tmp_path, rows, backfill=False)
    assert rc == 1  # JSON 为空，仅 DuckDB 有 → 不一致


def test_verify_backfill_creates_snapshot_and_consistent(monkeypatch, tmp_path: Path) -> None:
    rows = [("fct_a1", "alpha1", "futures", "futures", "active")]
    rc = _run_verify(monkeypatch, tmp_path, rows, backfill=True)
    assert rc == 0  # 回填后一致
    fp = tmp_path / "elite" / "fct_a1.json"
    assert fp.exists()


def test_verify_backfill_idempotent_second_run(monkeypatch, tmp_path: Path) -> None:
    rows = [("fct_a1", "alpha1", "futures", "futures", "active")]
    assert _run_verify(monkeypatch, tmp_path, rows, backfill=True) == 0
    # 第二次运行：快照已存在，不覆盖（幂等），仍一致
    assert _run_verify(monkeypatch, tmp_path, rows, backfill=True) == 0
