"""plans/29 P1：scripts/migrate_elite_json_to_catalog.py 测试。

覆盖:
- 差量补齐：catalog 缺失的归档/退役 JSON → 入库（status 映射 + 评估记录）
- 幂等：重复运行 migrated=0
- 一致性校验：已存在因子逐字段比对（零差异 / 字段差异报告）
- dry-run 不写入 / verify-only 只读
- 坏 JSON 跳过不阻断 / 孤儿（catalog 有 JSON 无）报告
- 市场路由（stock/futures 分库）
- CLI dry-run 入口
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.migrate_elite_json_to_catalog import (
    build_eval_dict,
    build_factor_dict,
    migrate_market,
    scan_elite_jsons,
    verify_factor,
)

ACTIVE_CODE = "def factor_program(data, params):\n    import numpy as np\n    close = data['close']\n    return np.clip(close / close.mean() - 1.0, -1.0, 1.0)"


def _make_factor_json(
    factor_id: str,
    name: str,
    ic: float = 0.05,
    sharpe: float = 1.6,
    source: str = "macro_evolution",
    generation: int = 2,
    family: str = "momentum",
) -> dict:
    return {
        "factor_id": factor_id,
        "name": name,
        "code": ACTIVE_CODE,
        "params": {"window": 13},
        "signature": {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 30},
        "economic_logic": {"theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 5, "narrative": "t"},
        "source": source,
        "parent_id": None,
        "generation": generation,
        "family": family,
        "created_at": "2026-08-03T07:21:41.974865",
        "trace_id": f"l2_{factor_id}",
        "evaluation": {
            "factor_id": factor_id,
            "trace_id": f"l2_{factor_id}",
            "level_1_backtest": {
                "ic": ic,
                "icir": 1.2,
                "sharpe": sharpe,
                "max_drawdown": 0.1,
                "monotonicity": True,
                "oos_ratio": 0.3,
                "t_stat": 3.0,
                "turnover_monthly": 0.5,
            },
            "level_2_economic": {
                "theory": 3,
                "behavioral": 2,
                "microstructure": 1,
                "institutional": 2,
                "dimensions_passed": 3,
            },
            "level_3_multiple": {
                "bonferroni_p": 1.0,
                "fdr_q": 0.05,
                "effective_n_factors": 1,
                "adjusted_t": 3.0,
                "passed": True,
            },
            "passed": True,
            "failure_reasons": [],
            "evaluated_at": "2026-08-03T07:21:43.050917",
        },
    }


def _write_json(dir_path: Path, data: dict) -> Path:
    fp = dir_path / f"{data['factor_id']}.json"
    fp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return fp


def _build_elite_tree(tmp_path: Path) -> tuple[Path, Path]:
    """构造测试 elite 目录 + 隔离 DuckDB 路径。

    elite 树: active 2 + _archive 1 + _retired 1
    """
    elite = tmp_path / "stocks_elite"
    (elite / "_archive").mkdir(parents=True)
    (elite / "_retired").mkdir(parents=True)
    _write_json(elite, _make_factor_json("fct_active_a", "act_a", ic=0.06))
    _write_json(elite, _make_factor_json("fct_active_b", "act_b", ic=0.07))
    _write_json(elite / "_archive", _make_factor_json("fct_arch_x", "arch_x", ic=0.04, source="seed", generation=0))
    _write_json(elite / "_retired", _make_factor_json("fct_ret_y", "ret_y", ic=0.03))
    return elite, tmp_path / "isolated_stock.duckdb"


class TestScanElite:
    def test_scan_covers_all_subdirs(self, tmp_path):
        elite, _ = _build_elite_tree(tmp_path)
        files = scan_elite_jsons(elite)
        by_status = {s for _, s in files}
        assert by_status == {"active", "archived", "retired"}
        assert len(files) == 4
        assert all(f.name.startswith("fct_") for f, _ in files)

    def test_scan_skips_metadata_files(self, tmp_path):
        elite, _ = _build_elite_tree(tmp_path)
        (elite / "_elite_index.json").write_text("{}", encoding="utf-8")
        files = scan_elite_jsons(elite)
        assert not any(f.name == "_elite_index.json" for f, _ in files)


class TestMigrateMarket:
    def test_backfill_with_status_mapping(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        assert stats["migrated"] == 4
        assert stats["failed"] == 0
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="stock")
        status_map = {f["factor_id"]: f["status"] for f in repo.list_factors()}
        assert status_map["fct_active_a"] == "active"
        assert status_map["fct_arch_x"] == "archived"
        assert status_map["fct_ret_y"] == "retired"
        # market 强制目标市场
        assert all(f["market"] == "stock" for f in repo.list_factors())
        # 评估记录已写入
        evals = repo.get_evaluations("fct_active_a")
        assert len(evals) == 1
        repo.close()

    def test_idempotent_rerun(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        stats2 = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t2")
        assert stats2["migrated"] == 0
        assert stats2["skipped"] == 4
        assert stats2["verified"] == 4
        assert stats2["field_mismatches"] == 0

    def test_existing_consistent_verifies_clean(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        # 第二次 = 校验路径：零不一致
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t2")
        assert stats["field_mismatches"] == 0
        assert stats["verified"] == 4

    def test_field_mismatch_reported(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        # 预置一个 name 不同的 catalog 记录（人为制造不一致）
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="stock")
        data = _make_factor_json("fct_active_a", "act_a", ic=0.06)
        factor = build_factor_dict(data, "stock", "active")
        factor["name"] = "wrong_name"
        repo.create_factor(factor)
        repo.close()
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        assert stats["field_mismatches"] >= 1
        assert any(s["factor_id"] == "fct_active_a" for s in stats["mismatch_samples"])

    def test_sync_updates_drifted_factor(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        # 预置 code 旧版本的 catalog 记录（模拟 JSON 在 catalog 之后被加固更新）
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="stock")
        data = _make_factor_json("fct_active_a", "act_a", ic=0.06)
        factor = build_factor_dict(data, "stock", "active")
        factor["code"] = "OLD_CODE_VERSION"
        repo.create_factor(factor)
        repo.close()
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, sync=True, trace_id="t1")
        assert stats["synced"] == 1
        assert stats["field_mismatches"] == 0
        repo2 = FactorRepository(db_path, market="stock")
        row = repo2.get_factor("fct_active_a")
        assert row["code"] == data["code"]
        repo2.close()

    def test_dry_run_no_write(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, dry_run=True, trace_id="t1")
        assert stats["migrated"] == 4  # 预估需补齐数
        assert not db_path.exists()

    def test_verify_only_no_write(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, verify_only=True, trace_id="t1")
        assert stats["migrated"] == 0
        # verify-only 会初始化空库表结构（读既有记录所需），但不得写入任何因子
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="stock")
        assert repo.list_factors() == []
        repo.close()

    def test_malformed_json_skipped(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        (elite / "fct_broken.json").write_text("{ not json", encoding="utf-8")
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        assert stats["failed"] == 1
        assert stats["migrated"] == 4
        assert any("解析失败" in e for e in stats["errors"])

    def test_orphans_reported(self, tmp_path):
        elite, db_path = _build_elite_tree(tmp_path)
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="stock")
        repo.create_factor(build_factor_dict(_make_factor_json("fct_orphan", "orphan"), "stock", "active"))
        repo.close()
        stats = migrate_market("stock", elite_dir=elite, db_path=db_path, trace_id="t1")
        assert "fct_orphan" in stats["orphans"]

    def test_futures_market_route(self, tmp_path):
        elite = tmp_path / "futures_elite"
        elite.mkdir()
        _write_json(elite, _make_factor_json("fct_fut_a", "fut_a", ic=0.06, family="trend"))
        db_path = tmp_path / "isolated_futures.duckdb"
        stats = migrate_market("futures", elite_dir=elite, db_path=db_path, trace_id="t1")
        assert stats["migrated"] == 1
        from fts.factor_engine.factor_db.repository import FactorRepository

        repo = FactorRepository(db_path, market="futures")
        factors = repo.list_factors()
        assert factors[0]["market"] == "futures"
        repo.close()


class TestBuilders:
    def test_build_factor_dict_metrics_from_evaluation(self, tmp_path):
        data = _make_factor_json("fct_x", "x", ic=0.05, sharpe=1.6)
        factor = build_factor_dict(data, "stock", "archived")
        assert factor["ic"] == 0.05
        assert factor["sharpe"] == 1.6
        assert factor["is_elite"] is True
        assert factor["status"] == "archived"
        assert factor["market"] == "stock"

    def test_build_eval_dict_shape(self, tmp_path):
        data = _make_factor_json("fct_x", "x")
        ev = build_eval_dict(data)
        assert ev["ic"] == 0.05
        assert ev["overall_passed"] is True

    def test_verify_factor_consistent(self, tmp_path):
        data = _make_factor_json("fct_x", "x")
        # catalog row 形态近似 _row_to_dict 输出
        row = {
            "name": "x",
            "code": ACTIVE_CODE,
            "params": {"window": 13},
            "signature": data["signature"],
            "economic_logic": data["economic_logic"],
            "source": "macro_evolution",
            "generation": 2,
            "ic": 0.05,
            "sharpe": 1.6,
            "icir": 1.2,
        }
        assert verify_factor(row, data) == []

    def test_verify_factor_detects_mismatch(self, tmp_path):
        data = _make_factor_json("fct_x", "x")
        row = {
            "name": "x",
            "code": "DIFFERENT",
            "params": {},
            "signature": {},
            "economic_logic": {},
            "source": "seed",
            "generation": 0,
            "ic": 0.0,
            "sharpe": 0.0,
            "icir": 0.0,
        }
        issues = verify_factor(row, data)
        assert any("code" in i for i in issues)
        assert any("generation" in i for i in issues)


class TestCli:
    def test_main_dry_run_stock(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["migrate_elite_json_to_catalog", "--market", "stock", "--dry-run"])
        import scripts.migrate_elite_json_to_catalog as mod

        # 隔离默认目录
        monkeypatch.setattr(
            mod, "DEFAULT_ELITE_DIRS", {"stock": tmp_path / "stocks_elite", "futures": tmp_path / "futures_elite"}
        )
        (tmp_path / "stocks_elite").mkdir(exist_ok=True)
        _write_json(tmp_path / "stocks_elite", _make_factor_json("fct_cli_a", "cli_a"))
        rc = mod.main()
        assert rc == 0
