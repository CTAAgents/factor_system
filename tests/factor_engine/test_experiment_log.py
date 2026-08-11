"""tests/factor_engine/test_experiment_log.py — Phase 2 P1-2 结构化实验日志测试。

覆盖（26 号计划 §7）:
    1. ExperimentLogWriter: schema 导出（rounds 分组 + summary 汇总）/ 幂等覆盖 /
       非法 payload 跳过（warning 不阻断）/ 输出目录自动创建
    2. extract_scores: level_1_backtest 指标映射 / 空评估默认
    3. EvolutionLoop 集成: _record_experiment_variant 记录 / _export_experiment_log 落盘 /
       run() finally 自动导出
"""

from __future__ import annotations

import json

import pytest


# ─── 1. ExperimentLogWriter ───────────────────────────────


def _variant(candidate_id: str, method: str, outcome: str, generation: int = 1, parent_id: str = "p1") -> dict:
    return {
        "generation": generation,
        "parent_id": parent_id,
        "candidate_id": candidate_id,
        "method": method,
        "summary": f"summary {candidate_id}",
        "scores": {"ic": 0.05} if outcome == "promoted" else {},
        "outcome": outcome,
    }


class TestExperimentLogWriter:
    def test_export_creates_json_with_schema(self, tmp_path):
        """导出 JSON：rounds 按 (generation, parent_id) 分组 + summary 汇总。"""
        from fts.factor_engine.experiment_log import ExperimentLogWriter

        writer = ExperimentLogWriter(tmp_path / "data")
        variants = [
            _variant("c1", "operator_evolution", "promoted", 1),
            _variant("c2", "macro_evolution", "audit_failed", 1),
            _variant("c3", "gp_evolution", "prefilter_rejected", 2),
        ]
        path = writer.export("run_1", "trace_1", "futures", "2026-08-11T00:00:00", 2, variants)

        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run_1"
        assert data["trace_id"] == "trace_1"
        assert data["market"] == "futures"
        assert data["generations_completed"] == 2

        assert len(data["rounds"]) == 2
        round1 = data["rounds"][0]
        assert round1["generation"] == 1
        assert round1["parent_id"] == "p1"
        assert len(round1["variants"]) == 2
        assert round1["promoted_count"] == 1

        summary = data["summary"]
        assert summary["total_evaluated"] == 3
        assert summary["total_promoted"] == 1
        assert summary["promote_rate"] == pytest.approx(1 / 3)
        assert summary["by_method"]["operator_evolution"]["promoted"] == 1
        assert summary["by_method"]["operator_evolution"]["rate"] == pytest.approx(1.0)

    def test_export_idempotent_overwrite(self, tmp_path):
        """同 run_id 重复导出 → 覆盖（仅 1 个文件，内容为最新）。"""
        from fts.factor_engine.experiment_log import ExperimentLogWriter

        writer = ExperimentLogWriter(tmp_path / "data")
        writer.export("run_x", "t", "futures", "s", 1, [_variant("c1", "macro_evolution", "promoted")])
        writer.export("run_x", "t", "futures", "s", 1, [_variant("c1", "macro_evolution", "audit_failed")])

        files = list((tmp_path / "data").glob("experiments-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["summary"]["total_promoted"] == 0  # 最新覆盖

    def test_export_invalid_payload_skips(self, tmp_path, caplog):
        """非法 payload（outcome 不在枚举）→ 跳过写入 + warning，不抛异常。"""
        from fts.factor_engine.experiment_log import ExperimentLogWriter

        writer = ExperimentLogWriter(tmp_path / "data")
        bad_variant = {
            "generation": 1,
            "parent_id": "p1",
            "candidate_id": "c1",
            "method": "macro_evolution",
            "summary": "s",
            "scores": {},
            "outcome": "not_allowed_outcome",
        }
        path = writer.export("run_bad", "t", "futures", "s", 1, [bad_variant])

        assert path is None
        assert not list((tmp_path / "data").glob("*.json"))
        assert "实验日志" in caplog.text

    def test_output_dir_auto_created(self, tmp_path):
        """嵌套输出目录自动创建。"""
        from fts.factor_engine.experiment_log import ExperimentLogWriter

        writer = ExperimentLogWriter(tmp_path / "a" / "b")
        path = writer.export("run_1", "t", "futures", "s", 1, [])

        assert path is not None and path.exists()
        assert path.parent == tmp_path / "a" / "b"


# ─── 2. extract_scores ────────────────────────────────────


class TestExtractScores:
    def test_extracts_level1_metrics(self):
        """level_1_backtest 指标映射（turnover ← turnover_monthly）。"""
        from fts.factor_engine.experiment_log import extract_scores

        evaluation = {
            "level_1_backtest": {
                "ic": 0.05,
                "icir": 1.2,
                "sharpe": 2.0,
                "max_drawdown": 0.2,
                "turnover_monthly": 0.3,
                "monotonicity": True,
            }
        }
        scores = extract_scores(evaluation)
        assert scores["ic"] == 0.05
        assert scores["icir"] == 1.2
        assert scores["sharpe"] == 2.0
        assert scores["max_drawdown"] == 0.2
        assert scores["turnover"] == 0.3
        assert scores["monotonicity"] is True

    def test_empty_evaluation_defaults(self):
        """空/None 评估 → 空 dict。"""
        from fts.factor_engine.experiment_log import extract_scores

        assert extract_scores(None) == {}
        assert extract_scores({}) == {}


# ─── 3. EvolutionLoop 集成 ────────────────────────────────


class TestEvolveLoopRecording:
    def test_record_variant_appends(self, minimal_loop):
        """_record_experiment_variant 记录完整字段（含 scores 提取 + quality_grade 合并）。"""
        parent = {"factor_id": "p1"}
        factor = {"factor_id": "c1"}
        evaluation = {"level_1_backtest": {"ic": 0.05, "turnover_monthly": 0.3}}

        minimal_loop._record_experiment_variant(
            factor, parent, 1, "operator_evolution", "s", evaluation, "promoted", quality_grade="B"
        )

        assert len(minimal_loop._experiment_variants) == 1
        v = minimal_loop._experiment_variants[0]
        assert v["candidate_id"] == "c1"
        assert v["parent_id"] == "p1"
        assert v["generation"] == 1
        assert v["method"] == "operator_evolution"
        assert v["outcome"] == "promoted"
        assert v["scores"]["ic"] == 0.05
        assert v["scores"]["turnover"] == 0.3
        assert v["scores"]["quality_grade"] == "B"

    def test_export_experiment_log_writes(self, minimal_loop, tmp_path):
        """_export_experiment_log 落盘 JSON（含 summary）。"""
        minimal_loop._experiment_log_dir = str(tmp_path / "data")
        minimal_loop._record_experiment_variant(
            {"factor_id": "c1"}, {"factor_id": "p1"}, 1, "macro_evolution", "s", None, "prefilter_rejected"
        )

        path = minimal_loop._export_experiment_log("run_1", "trace_1", 1)

        assert path is not None and path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run_1"
        assert data["summary"]["total_evaluated"] == 1

    def test_run_finally_exports(self, minimal_loop, tmp_path):
        """run() finally 自动导出实验日志（空种子早期返回路径仍落盘）。"""
        minimal_loop._experiment_log_dir = str(tmp_path / "data")

        minimal_loop.run(max_generation=1)

        files = list((tmp_path / "data").glob("experiments-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "rounds" in data
        assert "summary" in data
