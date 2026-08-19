"""
tests/factor_engine/test_subchain_eval.py — 批量子链质量评估标准工作流测试（2026-08-19 沉淀）。

覆盖:
    - run 全流程：active 因子逐品种 IC → 子链画像 → 落库质量矩阵 → metadata 更新
    - 空因子/无 IC 因子安全（跳过不落库、不抛异常）
    - 无有效链因子 → scope=unknown + pending_validation（不自动降级）
    - 报告 summary 字段（factors_total/rows_saved/scope_distribution）

隔离 DuckDB（tmp_path）+ FakePipe 桩（不触面板加载/真实行情）。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_db.repository import FactorRepository, SubchainQualityRepository
from fts.factor_engine.factor_db.schema import init_database
from fts.factor_engine.subchain_eval import SubchainEvalConfig, SubchainEvalRunner


class FakePipe:
    """桩替代 EnergyQaReviewPipeline（面板加载/逐品种 IC 均为假数据）。"""

    def __init__(self, sic_map: dict[str, dict]) -> None:
        self._sic_map = sic_map

    def _prepare_panel(self):
        return {"SC0": None}, ["2026-01-01"], None

    def _compute_curr_symbol_ic(self, factor_row: dict, panel: dict, common_dates) -> dict:
        return self._sic_map.get(factor_row.get("factor_id", ""), {})


@pytest.fixture()
def tmp_db(tmp_path):
    """独立 tmp DuckDB（init_database 幂等建表）。"""
    db = tmp_path / "energy.duckdb"
    init_database(db)
    return str(db)


def _create_factor(tmp_db: str, fid: str, name: str) -> None:
    repo = FactorRepository(market="energy", db_path=tmp_db)
    try:
        repo.create_factor(
            {
                "factor_id": fid,
                "name": name,
                "code": f"close[{fid}]",
                "params": {"window": 5},
                "status": "active",
                "market": "energy",
                "is_elite": True,
                "sharpe": 2.0,
                "ic": 0.05,
            }
        )
    finally:
        repo.close()


def _runner(tmp_db: str, factors: list[dict], sic_map: dict[str, dict]) -> SubchainEvalRunner:
    runner = SubchainEvalRunner(config=SubchainEvalConfig(market="energy"), db_path=tmp_db)
    pipe = FakePipe(sic_map)
    runner._pipeline = lambda: pipe          # 桩面板
    runner._load_factors = lambda: factors   # 桩因子列表（绕过 DB 查询）
    return runner


class TestSubchainEvalRunner:
    def test_run_saves_quality_rows_and_metadata(self, tmp_db):
        # 有效 IC：4 子链全部命中（n=3 × 4）
        sic = {"SC0": 0.20, "FU0": 0.15, "BU0": 0.18,
               "PF0": 0.12, "TA0": 0.14, "EG0": 0.16,
               "L0": 0.11, "PP0": 0.13, "PG0": 0.15,
               "MA0": 0.17, "UR0": 0.19, "SA0": 0.12}
        _create_factor(tmp_db, "f1", "full_chain")
        runner = _runner(tmp_db, [{"factor_id": "f1", "name": "full_chain", "params": {}}], {"f1": sic})

        summary = runner.run(trace_id="t1")

        assert summary["factors_total"] == 1
        assert summary["factors_failed"] == 0
        assert summary["rows_saved"] == 4  # 4 子链 × 1 因子
        assert summary["scope_distribution"].get("all") == 1

        # 质量矩阵落库
        qrepo = SubchainQualityRepository(market="energy", db_path=tmp_db)
        try:
            rows = qrepo.query_subchain_quality("f1", "energy")
        finally:
            qrepo.close()
        assert len(rows) == 4
        assert all(r["effective"] for r in rows)

        # metadata 更新（scope=all，非 pending）
        repo = FactorRepository(market="energy", db_path=tmp_db)
        try:
            f = repo.get_factor("f1")
        finally:
            repo.close()
        assert f is not None
        meta = f["metadata"]
        assert meta["subchain_scope"] == "all"
        assert meta["subchain_eval"]["pending_validation"] is False

    def test_run_no_symbol_ic_skips(self, tmp_db):
        # 因子存在但无逐品种 IC → 跳过（不落库、不报错）
        _create_factor(tmp_db, "f2", "no_ic")
        runner = _runner(tmp_db, [{"factor_id": "f2", "name": "no_ic", "params": {}}], {"f2": {}})

        summary = runner.run(trace_id="t2")

        assert summary["factors_total"] == 1
        assert summary["rows_saved"] == 0
        assert summary["no_effective_chains"] == []  # 无 IC 不算"无有效链"（跳过）

    def test_run_unknown_scope_pending_validation(self, tmp_db):
        # IC 全部低于门槛 → scope=unknown + pending_validation=true（不自动降级）
        sic = {"SC0": 0.001, "FU0": 0.002, "BU0": 0.001,   # 能源 IC≈0
               "PF0": 0.002, "TA0": 0.001, "EG0": 0.003,
               "L0": 0.001, "PP0": 0.002, "PG0": 0.001,
               "MA0": 0.002, "UR0": 0.001, "SA0": 0.002}
        _create_factor(tmp_db, "f3", "weak")
        runner = _runner(tmp_db, [{"factor_id": "f3", "name": "weak", "params": {}}], {"f3": sic})

        summary = runner.run(trace_id="t3")

        assert summary["rows_saved"] == 4  # 画像行仍落库（effective=False）
        assert summary["no_effective_chains"] != []
        assert summary["scope_distribution"].get("unknown") == 1

        repo = FactorRepository(market="energy", db_path=tmp_db)
        try:
            f = repo.get_factor("f3")
        finally:
            repo.close()
        assert f is not None
        assert f["metadata"]["subchain_scope"] == "unknown"
        assert f["metadata"]["subchain_eval"]["pending_validation"] is True
        # 状态不被降级（仍 active）
        assert f["status"] == "active"

    def test_run_empty_factor_list(self, tmp_db):
        runner = _runner(tmp_db, [], {})
        summary = runner.run(trace_id="t4")
        assert summary["factors_total"] == 0
        assert summary["rows_saved"] == 0

    def test_run_single_factor_exception_does_not_block(self, tmp_db):
        # 单因子逐品种 IC 计算抛异常 → 该因子标记 error，其余继续
        _create_factor(tmp_db, "f5", "boom")
        _create_factor(tmp_db, "f6", "fine")
        sic = {"SC0": 0.20, "FU0": 0.15, "BU0": 0.18,
               "PF0": 0.12, "TA0": 0.14, "EG0": 0.16,
               "L0": 0.11, "PP0": 0.13, "PG0": 0.15,
               "MA0": 0.17, "UR0": 0.19, "SA0": 0.12}

        class BoomPipe(FakePipe):
            def __init__(self) -> None:
                super().__init__({"f6": sic, "f5": {}})

            def _compute_curr_symbol_ic(self, factor_row, panel, common_dates) -> dict:
                if factor_row["factor_id"] == "f5":
                    raise RuntimeError("boom")
                return super()._compute_curr_symbol_ic(factor_row, panel, common_dates)

        runner = SubchainEvalRunner(config=SubchainEvalConfig(market="energy"), db_path=tmp_db)
        runner._pipeline = BoomPipe
        runner._load_factors = lambda: [
            {"factor_id": "f5", "name": "boom", "params": {}},
            {"factor_id": "f6", "name": "fine", "params": {}},
        ]

        summary = runner.run(trace_id="t5")

        assert summary["factors_total"] == 2
        assert summary["factors_failed"] == 1
        assert summary["rows_saved"] == 4
