"""评审质检门禁测试（v2.104.0+89）：完整质检门禁 / Q1-Q10 映射 / 就地审核。

覆盖:
- AutoReviewPolicy.classify 完整质检门禁（qa_meta 参数）：通过/缺失转人审/各未通过项 rejected
- _extract_qa_meta：dict 与 DuckDB JSON 字符串解析
- build_qa_review：Q1-Q10 由评估/审计结果映射 + qa_review 结构
- FactorReviewWorkflow.review_inplace：就地审核（approved/rejected/质检缺失撤销）
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fts.factor_engine.audit import AuditItemResult, FactorAuditReport
from fts.factor_engine.evolution_promote import build_qa_review
from fts.factor_engine.factor_inspector import (
    AutoReviewPolicy,
    FactorReviewWorkflow,
    _extract_qa_meta,
)


# ─── AutoReviewPolicy 完整质检门禁 ─────────────────────────


class TestAutoReviewQaGate:
    def _policy(self) -> AutoReviewPolicy:
        return AutoReviewPolicy()

    def _full_qa(self) -> dict:
        return {
            "audit_passed": True,
            "quality_grade": "B",
            "high_ic_grade": "B",
            "multiple_passed": True,
            "walk_forward_windows": 3,
            "q1_q10_passed": True,
        }

    def test_approved_full_qa(self):
        """全部质检通过 + IC/Sharpe 正常 → approved。"""
        dec, reason = self._policy().classify(0.05, 2.0, qa_meta=self._full_qa())
        assert dec is not None and dec.value == "approved"

    def test_missing_qa_meta_needs_human(self):
        """任一质检项缺失 → 转人审（宁缺毋滥）。"""
        qa = self._full_qa()
        qa["quality_grade"] = None
        dec, reason = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is None
        assert "质检记录缺失" in reason

    def test_missing_all_qa_meta_needs_human(self):
        """qa_meta 全缺失（未评审/字符串为空）→ 转人审。"""
        dec, reason = self._policy().classify(0.05, 2.0, qa_meta={})
        assert dec is None
        assert "质检记录缺失" in reason

    def test_audit_failed_rejected(self):
        qa = self._full_qa()
        qa["audit_passed"] = False
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_multiple_failed_rejected(self):
        qa = self._full_qa()
        qa["multiple_passed"] = False
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_walkforward_lt2_rejected(self):
        qa = self._full_qa()
        qa["walk_forward_windows"] = 1
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_quality_c_rejected(self):
        qa = self._full_qa()
        qa["quality_grade"] = "C"
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_highic_c_rejected(self):
        qa = self._full_qa()
        qa["high_ic_grade"] = "C"
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_q1q10_failed_rejected(self):
        qa = self._full_qa()
        qa["q1_q10_passed"] = False
        dec, _ = self._policy().classify(0.05, 2.0, qa_meta=qa)
        assert dec is not None and dec.value == "rejected"

    def test_low_ic_rejected_kept(self):
        """IC/Sharpe 低于下限仍 rejected（基础校验保留）。"""
        dec, _ = self._policy().classify(0.01, 2.0, qa_meta=self._full_qa())
        assert dec is not None and dec.value == "rejected"

    def test_high_sharpe_needs_human(self):
        """IC/Sharpe 超上限（疑过拟合）→ 转人审。"""
        dec, _ = self._policy().classify(0.05, 40.0, qa_meta=self._full_qa())
        assert dec is None


# ─── _extract_qa_meta（含 DuckDB JSON 字符串） ─────────────


class TestExtractQaMeta:
    def test_from_dict(self):
        meta = {"qa_review": {"audit_passed": True, "quality_grade": "B"}}
        out = _extract_qa_meta(meta)
        assert out["audit_passed"] is True
        assert out["quality_grade"] == "B"
        assert out["high_ic_grade"] is None

    def test_from_json_str(self):
        """DuckDB JSON 列返回字符串需正确解析。"""
        meta = json.dumps({"qa_review": {"audit_passed": True, "walk_forward_windows": 3}})
        out = _extract_qa_meta(meta)
        assert out["audit_passed"] is True
        assert out["walk_forward_windows"] == 3

    def test_empty_and_none(self):
        assert _extract_qa_meta(None) == {k: None for k in _extract_qa_meta(None)}
        assert _extract_qa_meta({})["audit_passed"] is None
        assert _extract_qa_meta("not-json")["audit_passed"] is None


# ─── build_qa_review（Q1-Q10 映射） ────────────────────────


def _mk_audit(passed: bool = True) -> FactorAuditReport:
    return FactorAuditReport(
        factor_id="f1",
        factor_name="n1",
        audited_at="2026-08-17",
        items=[
            AuditItemResult(name="snooping_check", status="passed" if passed else "failed"),
            AuditItemResult(name="stress_resilience", status="passed" if passed else "failed"),
        ],
        passed=passed,
        pass_rate=1.0 if passed else 0.5,
        summary={"failed_items": [] if passed else ["snooping_check"]},
    )


class TestBuildQaReview:
    def _factor(self) -> dict:
        return {
            "economic_logic": {"logic": "动量延续", "formula": "close.pct_change(20)"},
            "params": {"N": 20},
            "style_tags": ["momentum"],
        }

    def _evaluation(self) -> dict:
        return {
            "level_1_backtest": {"ic": 0.05, "icir": 1.5, "monotonicity": True},
            "level_3_multiple": {"passed": True, "bonferroni_p": 0.01},
            "walk_forward": {"n_windows_completed": 3},
            "robustness_check": {"passed": True},
            "cross_symbol_positive_ratio": 0.7,
        }

    def test_qa_review_structure(self):
        qr = build_qa_review(self._factor(), self._evaluation(), _mk_audit(), {"grade": "B"},
                             SimpleNamespace(grade="A"))
        assert qr["audit_passed"] is True
        assert qr["quality_grade"] == "B"
        assert qr["high_ic_grade"] == "A"
        assert qr["multiple_passed"] is True
        assert qr["walk_forward_windows"] == 3
        assert qr["q1_q10_passed"] is True
        assert qr["q1_q10"]["passed"] is True
        assert len(qr["q1_q10"]["items"]) == 10

    def test_q1q10_fails_when_audit_missing(self):
        """audit 缺失 → Q1/Q8 失败 → Q1 一票否决 → q1_q10_passed=False。"""
        qr = build_qa_review(self._factor(), self._evaluation(), None, {"grade": "B"},
                             SimpleNamespace(grade="A"))
        assert qr["audit_passed"] is False
        assert qr["q1_q10_passed"] is False

    def test_audit_item_fallback_to_overall(self):
        """audit items 为空但整体 passed → Q1/Q8 回退整体通过。"""
        audit = FactorAuditReport(
            factor_id="f1", factor_name="n1", audited_at="", items=[], passed=True,
            pass_rate=1.0, summary={},
        )
        qr = build_qa_review(self._factor(), self._evaluation(), audit, {"grade": "B"},
                             SimpleNamespace(grade="A"))
        assert qr["audit_passed"] is True
        assert qr["q1_q10_passed"] is True

    def test_high_ic_grade_from_screen(self):
        qr = build_qa_review(self._factor(), self._evaluation(), _mk_audit(), {"grade": "B"},
                             SimpleNamespace(grade="C"))
        assert qr["high_ic_grade"] == "C"


# ─── FactorReviewWorkflow.review_inplace（就地审核） ───────


class TestReviewInplace:
    def _mk_meta(self, qa_review: dict) -> str:
        return json.dumps({"qa_review": qa_review})

    def test_approved_writes_review(self):
        wf = FactorReviewWorkflow(db_path=":memory:")
        with patch.object(wf, "_conn") as m_conn, patch.object(wf, "_decide") as m_decide:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = (
                0.05, 2.0, self._mk_meta(
                    {"audit_passed": True, "quality_grade": "B", "high_ic_grade": "B",
                     "multiple_passed": True, "walk_forward_windows": 3, "q1_q10_passed": True}
                ),
            )
            m_conn.return_value = conn
            res = wf.review_inplace("f1")
        assert res["decision"] == "approved"
        m_decide.assert_called_once()

    def test_missing_qa_deletes_review(self):
        """质检记录缺失 → 撤销 approved（退回 L2 待审队列）。"""
        wf = FactorReviewWorkflow(db_path=":memory:")
        with patch.object(wf, "_conn") as m_conn, patch.object(wf, "_delete_review") as m_del, patch.object(wf, "_decide") as m_decide:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = (0.05, 2.0, "{}")
            m_conn.return_value = conn
            res = wf.review_inplace("f1")
        assert res["decision"] is None
        m_del.assert_called_once_with("f1")
        m_decide.assert_not_called()

    def test_factor_not_found(self):
        wf = FactorReviewWorkflow(db_path=":memory:")
        with patch.object(wf, "_conn") as m_conn:
            conn = MagicMock()
            conn.execute.return_value.fetchone.return_value = None
            m_conn.return_value = conn
            res = wf.review_inplace("missing")
        assert res["decision"] is None
        assert res["reason"] == "因子不存在"


class TestReviewL3Pool:
    """L3 池周度巡检（阀门功能 2）：不合格/质检失效因子退回 L2 冷却池。"""

    def test_demotes_unqualified(self):
        """approved 因子复核后不合格 → 退回（demoted）。"""
        wf = FactorReviewWorkflow(db_path=":memory:")
        with (
            patch.object(wf, "_conn") as m_conn,
            patch.object(wf, "review_inplace", side_effect=[
                {"factor_id": "a", "decision": "approved", "reason": "ok"},
                {"factor_id": "b", "decision": "rejected", "reason": "低质"},
                {"factor_id": "c", "decision": None, "reason": "质检记录缺失"},
            ]),
        ):
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = [("a",), ("b",), ("c",)]
            m_conn.return_value = conn
            res = wf.review_l3_pool(market="futures")
        assert res["scanned"] == 3
        assert len(res["demoted"]) == 2
        assert res["demoted"][0]["factor_id"] == "b"
        assert res["demoted"][1]["decision"] == "needs_human"

    def test_empty_pool(self):
        wf = FactorReviewWorkflow(db_path=":memory:")
        with patch.object(wf, "_conn") as m_conn:
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = []
            m_conn.return_value = conn
            res = wf.review_l3_pool(market="futures")
        assert res == {"scanned": 0, "demoted": []}
