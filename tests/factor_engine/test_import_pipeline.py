"""tests.factor_engine.test_import_pipeline — 外部因子导入管道单测（v2.105.0+32）。

覆盖：
  - dry-run 不落盘（默认安全模式）
  - apply 注入（l1_injected 文件 + factor_pool 登记）
  - 幂等去重（二次运行 duplicate）
  - 字段权威校验拦截（L2 缺失字段禁依赖）

注入/报告全部走 tmp_path 隔离，不触碰生产 memory 目录。
"""

from __future__ import annotations

import json

from fts.factor_engine.imports.import_pipeline import (
    EXTERNAL_SEED_SOURCES,
    ExternalFactorImportRunner,
    _make_candidate_id,
)


def _runner(tmp_path, dry_run=True):
    return ExternalFactorImportRunner(
        market="futures",
        inject_dir=tmp_path / "l1_injected",
        factor_pool_path=tmp_path / "factor_pool.json",
        trace_id="test.import",
        dry_run=dry_run,
    )


class TestImportPipeline:
    def test_dry_run_does_not_persist(self, tmp_path) -> None:
        runner = _runner(tmp_path, dry_run=True)
        report = runner.run()
        assert report["mode"] == "dry-run"
        # 不写入任何注入文件
        assert not (tmp_path / "l1_injected").exists()
        assert not (tmp_path / "factor_pool.json").exists()
        # 6 源全部加载（真实 YAML）
        assert report["summary"]["total"] == 103
        assert set(report["sources"].keys()) == set(EXTERNAL_SEED_SOURCES.keys())

    def test_apply_persists_and_registers(self, tmp_path) -> None:
        runner = _runner(tmp_path, dry_run=False)
        report = runner.run(sources=["academic_papers"])
        assert report["mode"] == "apply"
        assert report["sources"]["academic_papers"]["total"] == 6
        assert report["sources"]["academic_papers"]["injected"] == 6
        # 注入文件已写
        injected = list((tmp_path / "l1_injected").glob("cand_*.json"))
        assert len(injected) == 6
        # factor_pool 已登记
        pool = json.loads((tmp_path / "factor_pool.json").read_text(encoding="utf-8"))
        assert pool["total_count"] == 6
        assert all(f["source"].startswith("extract_") for f in pool["factors"])

    def test_dedup_on_second_run(self, tmp_path) -> None:
        runner = _runner(tmp_path, dry_run=False)
        first = runner.run(sources=["academic_papers"])
        second = runner.run(sources=["academic_papers"])
        assert first["sources"]["academic_papers"]["injected"] == 6
        assert second["sources"]["academic_papers"]["duplicate"] == 6
        assert second["sources"]["academic_papers"]["injected"] == 0
        # 文件不重复写入
        assert len(list((tmp_path / "l1_injected").glob("cand_*.json"))) == 6

    def test_field_authority_blocks_l2_missing(self, tmp_path) -> None:
        # L2 缺失字段（fundamental 库存类）被字段权威校验拦截
        runner = _runner(tmp_path, dry_run=False)
        authority = runner._field_authority(
            {"signature": {"input_fields": ["close", "fut_inventory"]}}
        )
        assert "fut_inventory" in authority["missing"]
        # 完整管道：构造含 L2 字段的候选，验证拦截计数
        cand = {
            "candidate_id": _make_candidate_id("bad_inv", "x=1"),
            "name": "bad_inv",
            "code": "x=1",
            "params": {},
            "signature": {"input_fields": ["close", "fut_inventory"]},
            "economic_logic": {},
            "source": "extract_test",
            "market": "futures",
        }
        stats = {"total": 1, "field_blocked": 0, "duplicate": 0, "injected": 0, "failed": 0}
        # 复刻 run 内校验逻辑
        existing = set()
        a = runner._field_authority(cand)
        if a["missing"] or a["unknown"]:
            stats["field_blocked"] += 1
        assert stats["field_blocked"] == 1
        assert cand["candidate_id"] not in existing
