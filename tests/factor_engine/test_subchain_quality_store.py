"""
tests/factor_engine/test_subchain_quality_store.py — 因子×子链质量矩阵存储测试（plans/49 §A2 / §五）。

覆盖:
    - SubchainQualityRepository：建表自动初始化/UPSERT 幂等/时序查询/latest 快照/批量加载
    - build_subchain_quality_rows：画像→矩阵行转换（t=inf→None、空输入 []、每子链一行）
    - 空库/空输入安全（不抛异常、no-op）

隔离 DuckDB（tmp_path），不触真实因子库。
"""

from __future__ import annotations

from datetime import date

import pytest

from fts.factor_engine.factor_db.repository import SubchainQualityRepository
from fts.factor_engine.factor_db.schema import init_database
from fts.factor_engine.subchain_profile import build_subchain_quality_rows

# 四大子链品种映射（与 portfolio_loop.ENERGY_CHAIN_SUB_SYMBOLS 对齐）
CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}


def _row(fid: str = "f1", chain: str = "能源", ts: str = "2026-08-17T00:00:00", **over) -> dict:
    r = {
        "factor_id": fid,
        "market": "energy",
        "chain": chain,
        "evaluated_at": ts,
        "period_end": "2026-08-17",
        "n_symbols": 3,
        "mean_ic": 0.2,
        "std_ic": 0.01,
        "t_stat": 34.6,
        "p_value": 0.001,
        "effective": True,
        "source": "promotion",
        "decision": "keep",
    }
    r.update(over)
    return r


@pytest.fixture()
def repo(tmp_path) -> SubchainQualityRepository:
    """独立 tmp DuckDB + 仓储实例（init_database 幂等建表）。"""
    db = tmp_path / "quality.duckdb"
    init_database(db)
    return SubchainQualityRepository(db_path=db, market="energy")


class TestStore:
    def test_init_creates_table(self, repo):
        # 建表（init_database 已含 subchain_factor_quality；空查询不抛错）
        assert repo.query_subchain_quality("f1", "energy") == []

    def test_save_and_query(self, repo):
        rows = [_row(chain="能源"), _row(chain="油化工")]
        assert repo.save_subchain_quality(rows) == 2
        q = repo.query_subchain_quality("f1", "energy")
        assert {r["chain"] for r in q} == {"能源", "油化工"}
        assert all(r["effective"] for r in q)
        assert q[0]["evaluated_at"] == "2026-08-17 00:00:00"  # TIMESTAMP 字符串化

    def test_upsert_idempotent(self, repo):
        # 同主键（factor×market×chain×evaluated_at）重复写 → 覆盖不新增
        repo.save_subchain_quality([_row(chain="能源", mean_ic=0.2)])
        repo.save_subchain_quality([_row(chain="能源", mean_ic=0.5)])
        q = repo.query_subchain_quality("f1", "energy", chain="能源")
        assert len(q) == 1
        assert q[0]["mean_ic"] == pytest.approx(0.5)

    def test_query_ordered_by_chain_time(self, repo):
        repo.save_subchain_quality(
            [
                _row(chain="聚酯", ts="2026-08-17T00:00:00", mean_ic=0.1),
                _row(chain="聚酯", ts="2026-08-18T00:00:00", mean_ic=0.2),
                _row(chain="能源", ts="2026-08-18T00:00:00", mean_ic=0.3),
            ]
        )
        q = repo.query_subchain_quality("f1", "energy")
        # 按 chain（DuckDB UTF-8 字节序：聚酯<能源…）+ evaluated_at 升序
        assert [r["chain"] for r in q] == ["聚酯", "聚酯", "能源"]
        assert [r["mean_ic"] for r in q] == [0.1, 0.2, 0.3]

    def test_latest_per_chain(self, repo):
        repo.save_subchain_quality(
            [
                _row(chain="能源", ts="2026-08-17T00:00:00", mean_ic=0.1),
                _row(chain="能源", ts="2026-08-18T00:00:00", mean_ic=0.2),
                _row(chain="煤化工", ts="2026-08-17T00:00:00", mean_ic=0.05),
            ]
        )
        latest = repo.latest_subchain_quality("f1", "energy")
        assert latest["能源"]["mean_ic"] == pytest.approx(0.2)  # 后写覆盖
        assert latest["煤化工"]["mean_ic"] == pytest.approx(0.05)
        assert len(latest) == 2

    def test_list_recent_filter(self, repo):
        repo.save_subchain_quality(
            [
                _row(chain="能源", ts="2026-08-17T00:00:00"),
                _row(chain="聚酯", ts="2026-08-18T00:00:00"),
            ]
        )
        recent = repo.list_recent_quality("energy", min_evaluated_at="2026-08-18T00:00:00")
        assert len(recent) == 1
        assert recent[0]["chain"] == "聚酯"

    def test_save_empty_noop(self, repo):
        assert repo.save_subchain_quality([]) == 0
        assert repo.query_subchain_quality("f1", "energy") == []

    def test_save_without_init_database(self, tmp_path):
        # 仓储自建库（_get_conn 内部 init_database 兜底）
        db = tmp_path / "auto.duckdb"
        r = SubchainQualityRepository(db_path=db, market="energy")
        try:
            assert r.save_subchain_quality([_row()]) == 1
            assert len(r.query_subchain_quality("f1", "energy")) == 1
        finally:
            r.close()


class TestBuildRows:
    def test_each_chain_row(self):
        # 能源三品种 IC 一致 → effective；其余链近零 → 不 effective
        rows = build_subchain_quality_rows(
            "f1",
            "energy",
            {
                "SC0": 0.2, "FU0": 0.18, "BU0": 0.22,
                "PF0": 0.01, "TA0": 0.0, "EG0": -0.01,
                "L0": 0.02, "PP0": 0.0, "PG0": 0.01,
                "MA0": -0.02, "UR0": 0.01, "SA0": 0.0,
            },
            chain_symbols=CHAINS,
        )
        assert len(rows) == 4  # 每子链一行
        eff = {r["chain"]: r["effective"] for r in rows}
        assert eff["能源"] is True
        assert eff["聚酯"] is False
        assert all(r["source"] == "promotion" and r["decision"] == "keep" for r in rows)

    def test_t_inf_to_none(self):
        # 子链内 IC 完全一致 → t=inf → 序列化 None（DuckDB JSON 安全），effective 仍 True
        rows = build_subchain_quality_rows(
            "f1", "energy", {"SC0": 0.2, "FU0": 0.2, "BU0": 0.2}, chain_symbols=CHAINS
        )
        energy = next(r for r in rows if r["chain"] == "能源")
        assert energy["t_stat"] is None
        assert energy["effective"] is True
        assert energy["mean_ic"] == pytest.approx(0.2)

    def test_empty_symbol_ic(self):
        assert build_subchain_quality_rows("f1", "energy", {}) == []

    def test_timestamp_defaults(self):
        # 未传 evaluated_at/period_end → 默认当前 UTC/今天
        rows = build_subchain_quality_rows("f1", "energy", {"SC0": 0.2, "FU0": 0.2, "BU0": 0.2}, chain_symbols=CHAINS)
        assert rows[0]["evaluated_at"] is not None
        assert rows[0]["period_end"] == date.today().isoformat()

    def test_market_chain_passthrough(self):
        rows = build_subchain_quality_rows(
            "f9", "futures", {"SC0": 0.2, "FU0": 0.2, "BU0": 0.2}, chain_symbols=CHAINS
        )
        assert all(r["market"] == "futures" for r in rows)
        assert all(r["factor_id"] == "f9" for r in rows)
