"""factor_db 数据层补齐测试 — A.1/A.2/B.3 新表与仓储（Stage 1）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fts.factor_engine.factor_db import (  # noqa: E402
    FactorAuditReportRepository,
    FactorQualityScoreRepository,
    FactorRepository,
    FactorStatusRepository,
    init_database,
)
from fts.factor_engine.factor_db.schema import get_connection  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """临时数据库路径（已初始化）。"""
    path = tmp_path / "test_factor_catalog.duckdb"
    init_database(path)
    return path


# ─── Schema 层 ──────────────────────────────────────────────


class TestSchemaTables:
    """Schema 新增表与幂等性。"""

    def test_new_tables_created(self, db_path: Path):
        conn = get_connection(db_path)
        try:
            tables = {
                t[0]
                for t in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "factor_quality_scores" in tables
        assert "factor_status_history" in tables
        assert "factor_audit_reports" in tables

    def test_init_idempotent(self, db_path: Path):
        """重复 init_database 不报错（幂等）。"""
        init_database(db_path)  # 第二次调用
        init_database(db_path)  # 第三次调用

    def test_catalog_status_extensions(self, db_path: Path):
        """factor_catalog 生命周期扩展字段存在。"""
        conn = get_connection(db_path)
        try:
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='factor_catalog'"
                ).fetchall()
            }
        finally:
            conn.close()
        for col in (
            "status_updated_at",
            "consecutive_ic_negative_months",
            "consecutive_sharpe_drop_months",
            "last_incremental_eval_at",
            "decay_rate_3m",
            "decay_rate_6m",
        ):
            assert col in cols, f"缺少列: {col}"


# ─── A.1 评分卡仓储 ─────────────────────────────────────────


class TestQualityScoreRepository:
    """FactorQualityScoreRepository CRUD。"""

    def test_save_and_get_latest(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            score = {
                "factor_id": "fct_a1",
                "total_score": 42.0,
                "grade": "A",
                "dimension_scores": [
                    {"name": "ic_score", "raw_value": 0.05, "score": 3.0},
                    {"name": "sharpe_score", "raw_value": 2.1, "score": 4.0},
                ],
                "evaluated_at": "2026-08-06T00:00:00+00:00",
            }
            repo.save_score(score)
            latest = repo.get_latest_score("fct_a1")
            assert latest is not None
            assert latest["total_score"] == 42.0
            assert latest["grade"] == "A"
            assert latest["ic_score"] == 3.0  # 维度快捷列
            assert latest["sharpe_score"] == 4.0
            assert isinstance(latest["dimension_scores"], list)
            assert latest["dimension_scores"][0]["name"] == "ic_score"
        finally:
            repo.close()

    def test_history_kept(self, db_path: Path):
        """多次保存保留历史，get_latest 取最新。"""
        repo = FactorQualityScoreRepository(db_path)
        try:
            for i, total in enumerate((30.0, 45.0)):
                repo.save_score(
                    {
                        "factor_id": "fct_a1",
                        "total_score": total,
                        "grade": "B" if total < 40 else "A",
                        "dimension_scores": [],
                        "evaluated_at": f"2026-08-0{i + 1}T00:00:00+00:00",
                    }
                )
            history = repo.get_score_history("fct_a1")
            assert len(history) == 2
            latest = repo.get_latest_score("fct_a1")
            assert latest["total_score"] == 45.0
        finally:
            repo.close()

    def test_list_top_scores_takes_latest_only(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            # fct_a: 两期评分（30 → 45），fct_b: 一期（40）
            repo.save_score(
                {
                    "factor_id": "fct_a",
                    "total_score": 30.0,
                    "grade": "B",
                    "dimension_scores": [],
                    "evaluated_at": "2026-08-01T00:00:00+00:00",
                }
            )
            repo.save_score(
                {
                    "factor_id": "fct_a",
                    "total_score": 45.0,
                    "grade": "A",
                    "dimension_scores": [],
                    "evaluated_at": "2026-08-02T00:00:00+00:00",
                }
            )
            repo.save_score(
                {
                    "factor_id": "fct_b",
                    "total_score": 40.0,
                    "grade": "A",
                    "dimension_scores": [],
                    "evaluated_at": "2026-08-01T00:00:00+00:00",
                }
            )
            top = repo.list_top_scores(limit=10)
            ids = [s["factor_id"] for s in top]
            assert ids == ["fct_a", "fct_b"]  # 45 > 40，且 fct_a 只出现一次
            a_scores = [s for s in top if s["factor_id"] == "fct_a"]
            assert len(a_scores) == 1
            assert a_scores[0]["total_score"] == 45.0

            top_a = repo.list_top_scores(limit=10, grade="A")
            assert {s["factor_id"] for s in top_a} == {"fct_a", "fct_b"}
        finally:
            repo.close()

    def test_delete_scores(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            repo.save_score({"factor_id": "fct_x", "total_score": 10.0, "grade": "C", "dimension_scores": []})
            deleted = repo.delete_scores_for_factor("fct_x")
            assert deleted >= 1
            assert repo.get_latest_score("fct_x") is None
        finally:
            repo.close()


# ─── A.2 状态变迁仓储 ───────────────────────────────────────


class TestStatusRepository:
    """FactorStatusRepository CRUD。"""

    def test_log_and_get_history(self, db_path: Path):
        repo = FactorStatusRepository(db_path)
        try:
            history_id = repo.log_transition(
                "fct_s1",
                "active",
                "decaying",
                "连续 3 月 IC < 0",
                {"consecutive_ic_negative_months": 3},
            )
            assert history_id
            history = repo.get_history("fct_s1")
            assert len(history) == 1
            assert history[0]["from_status"] == "active"
            assert history[0]["to_status"] == "decaying"
            assert history[0]["snapshot"]["consecutive_ic_negative_months"] == 3
        finally:
            repo.close()

    def test_update_factor_status(self, db_path: Path):
        """更新 factor_catalog 状态字段（需先有因子）。"""
        factor_repo = FactorRepository(db_path)
        status_repo = FactorStatusRepository(db_path)
        try:
            factor_repo.create_factor(
                {
                    "factor_id": "fct_s2",
                    "name": "status_test",
                    "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
                    "market": "futures",
                }
            )
            ok = status_repo.update_factor_status(
                "fct_s2",
                "decaying",
                consecutive_ic_negative_months=3,
                decay_rate_3m=0.25,
            )
            assert ok is True
            factor = factor_repo.get_factor("fct_s2")
            assert factor["status"] == "decaying"
            assert factor["consecutive_ic_negative_months"] == 3
            assert factor["decay_rate_3m"] == 0.25
            assert factor["status_updated_at"] is not None
        finally:
            factor_repo.close()
            status_repo.close()

    def test_update_factor_status_old_db_no_column(self, tmp_path: Path):
        """旧库缺 status_updated_at 列时，update_factor_status 幂等补列并成功。"""
        import duckdb

        db_path = tmp_path / "old_catalog.duckdb"
        conn = duckdb.connect(str(db_path))
        try:
            # 构造旧库：仅基础列，无 A.2 扩展列
            conn.execute("""
                CREATE TABLE factor_catalog (
                    factor_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    code TEXT,
                    market VARCHAR,
                    status VARCHAR DEFAULT 'active',
                    sharpe DOUBLE DEFAULT 0.0,
                    ic DOUBLE DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO factor_catalog (factor_id, name, code, market) "
                "VALUES ('fct_old1', 'old_factor', 'pass', 'futures')"
            )
            # 建二级索引（触发 ART workaround 路径）
            conn.execute("CREATE INDEX idx_factor_catalog_status ON factor_catalog(status)")
        finally:
            conn.close()

        status_repo = FactorStatusRepository(db_path)
        try:
            ok = status_repo.update_factor_status("fct_old1", "retired")
            assert ok is True
        finally:
            status_repo.close()

        # 验证补列成功
        factor_repo = FactorRepository(db_path)
        try:
            factor = factor_repo.get_factor("fct_old1")
            assert factor["status"] == "retired"
            assert factor["status_updated_at"] is not None
        finally:
            factor_repo.close()

    def test_get_factor_releases_read_txn(self, db_path: Path):
        """get_factor 完整消费 result，不残留读事务阻塞其他连接的 DDL。"""
        factor_repo = FactorRepository(db_path)
        status_repo = FactorStatusRepository(db_path)
        try:
            factor_repo.create_factor(
                {
                    "factor_id": "fct_txn1",
                    "name": "txn_test",
                    "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
                    "market": "futures",
                }
            )
            # 主连接执行 get_factor（读查询），随后新连接做 DROP→UPDATE→CREATE→CHECKPOINT
            factor = factor_repo.get_factor("fct_txn1")
            assert factor is not None
            ok = status_repo.update_factor_status("fct_txn1", "retired")
            assert ok is True
            # 主连接仍可用
            assert factor_repo.get_factor("fct_txn1")["status"] == "retired"
        finally:
            factor_repo.close()
            status_repo.close()

    def test_retire_factor_moves_json(self, db_path: Path, tmp_path: Path):
        """retire_factor 更新 DuckDB 状态并移动 JSON 到 _retired/。"""

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "fct_ret1.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_ret1",
                    "name": "retire_me",
                    "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
                    "market": "futures",
                }
            ),
            encoding="utf-8",
        )

        factor_repo = FactorRepository(db_path)
        try:
            factor_repo.create_factor(
                {
                    "factor_id": "fct_ret1",
                    "name": "retire_me",
                    "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
                    "market": "futures",
                }
            )
            ok = factor_repo.retire_factor(
                "fct_ret1",
                reason="test",
                elite_dir=str(elite_dir),
            )
            assert ok is True
            assert factor_repo.get_factor("fct_ret1")["status"] == "retired"
            assert not (elite_dir / "fct_ret1.json").exists()
            assert (elite_dir / "_retired" / "fct_ret1.json").exists()
        finally:
            factor_repo.close()


# ─── B.3 审计报告仓储 ───────────────────────────────────────


class TestAuditReportRepository:
    """FactorAuditReportRepository CRUD。"""

    def test_save_and_get_latest(self, db_path: Path):
        repo = FactorAuditReportRepository(db_path)
        try:
            report = {
                "report_id": "far_test1",
                "factor_id": "fct_b3",
                "passed": True,
                "overall_score": 85.0,
                "total_checks": 6,
                "passed_checks": 6,
                "results": [{"name": "causal_validity", "status": "passed"}],
                "summary": {"total": 6, "passed": 6, "skipped": 0},
                "recommendations": [],
                "audited_at": "2026-08-06T00:00:00+00:00",
            }
            repo.save_report(report)
            latest = repo.get_latest_report("fct_b3")
            assert latest is not None
            assert latest["passed"] is True
            assert latest["overall_score"] == 85.0
            assert latest["total_checks"] == 6
            assert isinstance(latest["results_json"], list)
            assert latest["results_json"][0]["name"] == "causal_validity"
        finally:
            repo.close()

    def test_get_statistics(self, db_path: Path):
        repo = FactorAuditReportRepository(db_path)
        try:
            for i, passed in enumerate((True, False, True)):
                repo.save_report(
                    {
                        "factor_id": f"fct_st{i}",
                        "passed": passed,
                        "overall_score": 80.0 if passed else 40.0,
                        "total_checks": 6,
                        "passed_checks": 6 if passed else 2,
                        "results": [],
                        "summary": {"total": 6},
                        "recommendations": [],
                    }
                )
            stats = repo.get_statistics()
            assert stats["total_audits"] == 3
            assert stats["passed_audits"] == 2
            assert stats["distinct_factors"] == 3
            assert 0 < stats["pass_rate"] < 1.0
        finally:
            repo.close()

# ─── FactorRepository 补充缺口（GAP-F16）────────────────────


def _mk_factor(name: str = "gap_factor", market: str = "stock", **kw) -> dict:
    base = {
        "name": name,
        "code": "def factor_program(data, params):\n    return data['close']",
        "market": market,
    }
    base.update(kw)
    return base


class TestRepositoryGap:
    """FactorRepository 未覆盖路径补充。"""

    def test_auto_init_on_missing_db(self, tmp_path: Path):
        """db 文件不存在时首次连接自动建表（_get_conn 惰性 init）。"""
        db = tmp_path / "fresh_repo.duckdb"
        assert not db.exists()
        repo = FactorRepository(db)
        try:
            fid = repo.create_factor(_mk_factor())
            assert repo.get_factor(fid) is not None
        finally:
            repo.close()

    def test_execute_without_params(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            repo._execute("SELECT 42 AS answer")
            assert repo._last_columns == ["answer"]
        finally:
            repo.close()

    def test_get_factor_by_name_with_market(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            repo.create_factor(_mk_factor("dup_name", market="stock"))
            repo.create_factor(_mk_factor("dup_name", market="futures"))
            stock = repo.get_factor_by_name("dup_name", market="stock")
            assert stock is not None and stock["market"] == "stock"
            futures = repo.get_factor_by_name("dup_name", market="futures")
            assert futures is not None and futures["market"] == "futures"
        finally:
            repo.close()

    def test_get_factor_by_name_missing(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            assert repo.get_factor_by_name("no_such_name") is None
            assert repo.get_factor_by_name("no_such_name", market="stock") is None
        finally:
            repo.close()

    def test_update_factor_dict_values_and_ignored_keys(self, db_path: Path):
        """dict/list 值序列化为 JSON；factor_id/created_at 键被跳过。"""
        repo = FactorRepository(db_path)
        try:
            fid = repo.create_factor(_mk_factor())
            ok = repo.update_factor(
                fid,
                {
                    "params": {"window": 99},
                    "signature": ["a", "b"],
                    "factor_id": "hacked",
                    "created_at": "2020-01-01T00:00:00",
                },
            )
            assert ok is True
            fetched = repo.get_factor(fid)
            assert fetched["factor_id"] == fid  # factor_id 未被修改
            assert fetched["params"] == {"window": 99}
            assert fetched["signature"] == ["a", "b"]
        finally:
            repo.close()

    def test_retire_factor_missing_returns_false(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            assert repo.retire_factor("no_such_factor") is False
        finally:
            repo.close()

    def test_retire_factor_already_retired(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            fid = repo.create_factor(_mk_factor())
            repo.delete_factor(fid)
            # status=deleted → 先更新为 retired
            repo.update_factor(fid, {"status": "retired"})
            assert repo.retire_factor(fid) is True  # 已 retired → 幂等返回 True
        finally:
            repo.close()

    def test_retire_factor_json_already_in_retired_dir(self, db_path: Path, tmp_path: Path, monkeypatch):
        """JSON 已在 _retired/ 中 → json_moved_path 指向原位置。"""
        monkeypatch.chdir(tmp_path)
        elite_dir = tmp_path / "elite"
        retired = elite_dir / "_retired"
        retired.mkdir(parents=True)
        (retired / "fct_r1.json").write_text("{}", encoding="utf-8")

        repo = FactorRepository(db_path)
        try:
            fid = repo.create_factor(_mk_factor("fct_r1", factor_id="fct_r1"))
            ok = repo.retire_factor(fid, elite_dir=str(elite_dir))
            assert ok is True
            assert (retired / "fct_r1.json").exists()
        finally:
            repo.close()

    def test_retire_factor_json_missing_warns(self, db_path: Path, tmp_path: Path, monkeypatch):
        """elite_dir 下与 _retired/ 均无 JSON → warning 路径。"""
        monkeypatch.chdir(tmp_path)
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        repo = FactorRepository(db_path)
        try:
            fid = repo.create_factor(_mk_factor("fct_r2", factor_id="fct_r2"))
            ok = repo.retire_factor(fid, elite_dir=str(elite_dir))
            assert ok is True  # JSON 移动失败不影响 DuckDB 状态
            assert repo.get_factor(fid)["status"] == "retired"
        finally:
            repo.close()

    def test_retire_consistency_log_failure_still_ok(self, db_path: Path, tmp_path: Path, monkeypatch):
        """一致性日志写入失败（open 异常）不影响淘汰主流程。"""
        monkeypatch.chdir(tmp_path)
        repo = FactorRepository(db_path)
        try:
            fid = repo.create_factor(_mk_factor("fct_r3", factor_id="fct_r3"))

            def _bad_open(*args, **kwargs):
                raise OSError("disk full")

            monkeypatch.setattr("builtins.open", _bad_open)
            assert repo.retire_factor(fid) is True
            assert repo.get_factor(fid)["status"] == "retired"
        finally:
            repo.close()

    def test_write_seed_lineage_success(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            ok = repo.write_seed_lineage(
                factor_id="fct_l2_1",
                factor_name="l2_name",
                seed_name="seed_a",
                seed_family="momentum",
                seed_market="futures",
                generation=2,
                parent_id="fct_l1_1",
                trace_id="tr_lineage_1",
            )
            assert ok is True
        finally:
            repo.close()

    def test_write_seed_lineage_failure_returns_false(self, db_path: Path, monkeypatch):
        repo = FactorRepository(db_path)
        try:
            def _boom(*args, **kwargs):
                raise RuntimeError("db down")

            monkeypatch.setattr(repo, "_get_conn", _boom)
            assert (
                repo.write_seed_lineage(
                    factor_id="fct_x",
                    factor_name="x",
                    seed_name="s",
                    seed_family="f",
                    seed_market="stock",
                )
                is False
            )
        finally:
            repo.close()

    def test_resolve_seed_lineage_self_seed(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            result = repo.resolve_seed_lineage("f1", "name1", "seed", 3, "fam", None, "futures")
            assert result == {
                "seed_name": "name1",
                "seed_family": "fam",
                "seed_market": "futures",
                "generation": 3,
            }
        finally:
            repo.close()

    def test_resolve_seed_lineage_generation_zero(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            result = repo.resolve_seed_lineage("f1", "name1", "evolution", 0, "fam", "parent_x")
            assert result["generation"] == 0
            assert result["seed_name"] == "name1"
        finally:
            repo.close()

    def test_resolve_seed_lineage_walk_chain(self, db_path: Path):
        """沿 parent_id 链回溯到种子因子。"""
        repo = FactorRepository(db_path)
        try:
            seed_id = repo.create_factor(
                _mk_factor("seedA", source="seed", generation=0, family="famA")
            )
            mid_id = repo.create_factor(
                _mk_factor("midB", source="evolution", generation=1, family="famB", parent_id=seed_id)
            )
            _ = mid_id
            result = repo.resolve_seed_lineage(
                "child",
                "childC",
                "evolution",
                2,
                "famC",
                factor_parent_id=mid_id,
                market="stock",
            )
            assert result["seed_name"] == "seedA"
            assert result["seed_family"] == "famA"
            assert result["seed_market"] == "stock"
            assert result["generation"] == 2 + 1  # child gen + mid gen
        finally:
            repo.close()

    def test_resolve_seed_lineage_parent_missing_fallback(self, db_path: Path):
        """父因子不存在 → break → 自身 fallback。"""
        repo = FactorRepository(db_path)
        try:
            result = repo.resolve_seed_lineage(
                "child", "childC", "evolution", 3, "famC", factor_parent_id="missing_parent"
            )
            assert result["seed_name"] == "childC"
            assert result["generation"] == 3
        finally:
            repo.close()

    def test_resolve_seed_lineage_exception_fallback(self, db_path: Path, monkeypatch):
        """get_factor 抛异常 → except → break → fallback。"""
        repo = FactorRepository(db_path)
        try:
            def _boom(factor_id):
                raise RuntimeError("db error")

            monkeypatch.setattr(repo, "get_factor", _boom)
            result = repo.resolve_seed_lineage(
                "child", "childC", "evolution", 3, "famC", factor_parent_id="p1"
            )
            assert result["seed_name"] == "childC"
        finally:
            repo.close()

    def test_count_factors_with_filters(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            repo.create_factor(_mk_factor("c1", market="stock", status="active", is_elite=True))
            repo.create_factor(_mk_factor("c2", market="futures", status="failed", is_elite=False))
            assert repo.count_factors() == 2
            assert repo.count_factors(status="active") == 1
            assert repo.count_factors(is_elite=True) == 1
            assert repo.count_factors(market="futures", status="failed") == 1
        finally:
            repo.close()

    def test_search_factors_with_market(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            repo.create_factor(_mk_factor("mom_stock", market="stock"))
            repo.create_factor(_mk_factor("mom_futures", market="futures"))
            stock = repo.search_factors("mom", market="stock")
            assert len(stock) == 1
            assert stock[0]["market"] == "stock"
        finally:
            repo.close()

    def test_get_by_family_with_all_filters(self, db_path: Path):
        repo = FactorRepository(db_path)
        try:
            repo.create_factor(_mk_factor("fam_a1", family="fam_x", ic=0.10, sharpe=2.0, market="stock"))
            repo.create_factor(_mk_factor("fam_a2", family="fam_x", ic=0.01, sharpe=0.2, market="futures"))
            out = repo.get_by_family("fam_x", market="stock", min_sharpe=1.0, min_ic=0.05)
            assert len(out) == 1
            assert out[0]["name"] == "fam_a1"
        finally:
            repo.close()

    def test_get_diverse_factors_small_pool_return_all(self, db_path: Path):
        """eligible 数 ≤ total_count → 直接全量返回（不进入轮流选择）。"""
        repo = FactorRepository(db_path)
        try:
            for i in range(3):
                repo.create_factor(
                    _mk_factor(
                        f"small_{i}",
                        market="stock",
                        status="active",
                        is_elite=True,
                        family=f"fam_{i}",
                        ic=0.05,
                        sharpe=1.0,
                    )
                )
            diverse = repo.get_diverse_factors(market="stock", total_count=10)
            assert len(diverse) == 3
        finally:
            repo.close()

    def test_get_diverse_factors_round_robin(self, db_path: Path):
        """eligible 多于 total_count → 家族轮流选择；max_per_family 与 added=False break 路径。"""
        repo = FactorRepository(db_path)
        try:
            for i in range(12):
                repo.create_factor(
                    _mk_factor(
                        f"div_{i}",
                        market="stock",
                        status="active",
                        is_elite=True,
                        family=f"fam_{i % 3}",
                        ic=0.05,
                        sharpe=1.0 + i * 0.1,
                    )
                )
            # 12 个合格因子 > total_count=10；max_per_family=3 → 每族取 3 → 共 9 个后 added=False break
            diverse = repo.get_diverse_factors(
                market="stock",
                total_count=10,
                max_per_family=3,
                min_ic=0.02,
                min_sharpe=0.5,
            )
            assert len(diverse) == 9
            fam_counts: dict[str, int] = {}
            for f in diverse:
                fam_counts[f["family"]] = fam_counts.get(f["family"], 0) + 1
            assert set(fam_counts.values()) == {3}
        finally:
            repo.close()

    def test_get_diverse_factors_round_robin_cap(self, db_path: Path):
        """total_count 恰好覆盖部分家族（每族 2 个封顶）。"""
        repo = FactorRepository(db_path)
        try:
            for i in range(10):
                repo.create_factor(
                    _mk_factor(
                        f"cap_{i}",
                        market="stock",
                        status="active",
                        is_elite=True,
                        family=f"fam_{i % 3}",
                        ic=0.05,
                        sharpe=1.0 + i * 0.1,
                    )
                )
            diverse = repo.get_diverse_factors(
                market="stock",
                total_count=5,
                max_per_family=2,
                min_ic=0.02,
                min_sharpe=0.5,
            )
            assert len(diverse) == 5
            fam_counts = {}
            for f in diverse:
                fam_counts[f["family"]] = fam_counts.get(f["family"], 0) + 1
            assert max(fam_counts.values()) <= 2
        finally:
            repo.close()
# ─── 内部工具与子仓储缺口（GAP-F16）────────────────────────


class TestInternalTools:
    """_row_to_dict 等内部工具边界分支。"""

    def test_row_to_dict_extra_columns_break(self, db_path: Path):
        """row 列数多于 _last_columns → 超长部分截断。"""
        repo = FactorRepository(db_path)
        try:
            repo._last_columns = ["factor_id"]
            out = repo._row_to_dict(("f1", "extra1", "extra2"))
            assert out == {"factor_id": "f1"}
        finally:
            repo.close()

    def test_row_to_dict_invalid_json_keeps_raw(self, db_path: Path):
        """params 列无效 JSON → 返回原始字符串。"""
        repo = FactorRepository(db_path)
        try:
            repo._last_columns = ["params"]
            out = repo._row_to_dict(("not-json",))
            assert out["params"] == "not-json"
        finally:
            repo.close()

    def test_row_to_dict_invalid_failure_reasons(self, db_path: Path):
        """failure_reasons 列无效 JSON → 返回原始字符串。"""
        repo = FactorRepository(db_path)
        try:
            repo._last_columns = ["failure_reasons"]
            out = repo._row_to_dict(("{bad",))
            assert out["failure_reasons"] == "{bad"
        finally:
            repo.close()

    def test_row_to_dict_timestamp_columns(self, db_path: Path):
        """_at 结尾列转为字符串。"""
        repo = FactorRepository(db_path)
        try:
            repo._last_columns = ["created_at", "evaluated_at"]
            out = repo._row_to_dict((None, None))
            assert out["created_at"] is None
            assert out["evaluated_at"] is None
        finally:
            repo.close()

    def test_row_to_dict_json_empty_value(self, db_path: Path):
        """JSON 列为空 → 默认 {}（style_tags → []）。"""
        repo = FactorRepository(db_path)
        try:
            repo._last_columns = ["params", "signature", "style_tags"]
            out = repo._row_to_dict((None, "", ""))
            assert out["params"] == {}
            assert out["signature"] == {}
            assert out["style_tags"] == []
        finally:
            repo.close()


class TestQualityScoreRepoGap:
    def test_context_manager(self, db_path: Path):
        with FactorQualityScoreRepository(db_path) as repo:
            repo._get_conn()  # 触发惰性连接
            assert repo._conn is not None
        assert repo._conn is None

    def test_row_to_dict_invalid_dimension_scores(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            row = (
                "q1", "fct", 1.0, "not-json", "A", "2026-01-01", "v1",
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
            )
            out = repo._row_to_dict(row)
            assert out["dimension_scores"] == "not-json"
        finally:
            repo.close()

    def test_row_to_dict_extra_columns_break(self, db_path: Path):
        """row 列数超过 cols（17）→ 超长部分截断。"""
        repo = FactorQualityScoreRepository(db_path)
        try:
            row = ("q1",) * 19
            out = repo._row_to_dict(row)
            assert out["score_id"] == "q1"
            assert len(out) == 17  # 只保留前 17 列
        finally:
            repo.close()


class TestStatusRepoGap:
    def test_update_factor_status_empty_returns_false(self, db_path: Path):
        """status 为空且无其他字段 → 返回 False。"""
        repo = FactorStatusRepository(db_path)
        try:
            assert repo.update_factor_status("whatever", "") is False
        finally:
            repo.close()

    def test_get_history_invalid_snapshot(self, db_path: Path, monkeypatch):
        """snapshot 解析失败 → 保留原始字符串。"""
        repo = FactorStatusRepository(db_path)
        try:
            repo.log_transition("fct_sh1", "active", "retired", "reason")

            def _bad_loads(*args, **kwargs):
                raise json.JSONDecodeError("boom", "doc", 0)

            monkeypatch.setattr("fts.factor_engine.factor_db.repository.json.loads", _bad_loads)
            history = repo.get_history("fct_sh1")
            assert len(history) == 1
            assert history[0]["to_status"] == "retired"
        finally:
            repo.close()


class TestAuditRepoGap:
    def test_get_history_asc(self, db_path: Path):
        repo = FactorAuditReportRepository(db_path)
        try:
            for passed in (True, False):
                repo.save_report(
                    {
                        "factor_id": "fct_aud_hist",
                        "passed": passed,
                        "overall_score": 90.0,
                        "total_checks": 6,
                        "passed_checks": 6,
                        "results": [],
                        "summary": {},
                        "recommendations": [],
                    }
                )
            history = repo.get_history("fct_aud_hist")
            assert len(history) == 2
            assert history[0]["passed"] is True
            assert history[1]["passed"] is False
        finally:
            repo.close()

    def test_row_to_dict_invalid_json(self, db_path: Path):
        """results_json 等 JSON 列解析失败 → 保留原始字符串。"""
        repo = FactorAuditReportRepository(db_path)
        try:
            row = (
                "r1", "fct", "v1", True, 90.0, 6, 6, "bad-json", "{}", "[]",
                "2026-01-01", "v1",
            )
            out = repo._row_to_dict(row)
            assert out["results_json"] == "bad-json"
            assert out["summary_json"] == {}
        finally:
            repo.close()

    def test_row_to_dict_extra_columns_break(self, db_path: Path):
        """row 列数超过 cols（12）→ 超长部分截断。"""
        repo = FactorAuditReportRepository(db_path)
        try:
            row = ("r1",) * 15
            out = repo._row_to_dict(row)
            assert out["report_id"] == "r1"
            assert len(out) == 12
        finally:
            repo.close()