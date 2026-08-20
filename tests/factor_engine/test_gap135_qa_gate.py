# -*- coding: utf-8 -*-
"""GAP-135 测试：晋升期 Q1-Q10 一票否决门禁 + 同表达式去重 + 表达式规范化。

覆盖（v2.105.0）：
1. normalize_expression 规范化（空白压缩）
2. find_duplicate_expression 同表达式去重（命中/自因子跳过/空目录/坏 JSON/无 code）
3. EliteStore._promote_to_elite Q1-Q10 一票否决门禁（economic_logic 缺失 → Q2 一票否决 → 拒绝）
4. 门禁开关 l2_qa_gate_enabled=false 时放行（向后兼容）
5. 表达式去重门禁：既有 elite 同表达式 → 拒绝晋升
6. 审计 causal_validity 对缺 code 有 expr 的因子从 expr 编译补齐（GAP-135 ③）
"""

import json

import numpy as np
import pandas as pd
from unittest.mock import MagicMock

from fts.factor_engine.contracts import FactorEvaluation
from fts.factor_engine.evolution_promote import (
    find_duplicate_expression,
    normalize_expression,
)
from fts.factor_engine.evolution_loop import EvolutionLoop


def _make_factor(factor_id: str = "fct_gap135") -> dict:
    """最小晋升因子（含经济逻辑，Q2 通过）。"""
    return {
        "factor_id": factor_id,
        "name": factor_id,
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    if n > 1:\n"
            "        ret[1:] = np.diff(close) / np.maximum(np.abs(close[1:]), 1e-10)\n"
            "    return np.tanh(ret * 10)\n"
        ),
        "params": {"window": 10},
        "economic_logic": {
            "theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3,
            "narrative": "测试因子",
        },
    }


def _make_evaluation(factor_id: str = "fct_gap135") -> FactorEvaluation:
    return FactorEvaluation(
        factor_id=factor_id,
        trace_id="trace_gap135",
        passed=True,
        failure_reasons=[],
        level_1_backtest={"ic": 0.05, "sharpe": 2.0},
        level_3_multiple={"passed": True},
        walk_forward={"n_windows_completed": 4, "ic_consistency": 0.75, "passed": True},
        evaluated_at="2026-08-17T00:00:00",
    )


def _make_passing_audit():
    from fts.factor_engine.audit import AuditItemResult, FactorAuditReport

    items = [
        AuditItemResult(name=n, status="passed", evidence="mock")
        for n in ("snooping_check", "stress_resilience")
    ]
    return FactorAuditReport(
        factor_id="test", factor_name="test", audited_at="2026-08-17",
        items=items, passed=True, pass_rate=1.0,
        summary={"failed_items": []},
    )


def _make_loop(tmp_path):
    loop = EvolutionLoop(
        data=pd.DataFrame({"close": [1.0]}),
        forward_returns=np.array([0.0]),
        elite_dir=tmp_path / "elite",
        memory_dir=tmp_path / "memory",
    )
    mock_repo = MagicMock()
    mock_repo.get_factor_by_name = MagicMock(return_value=None)
    mock_repo.get_factor = MagicMock(return_value=None)
    mock_repo.create_factor = MagicMock(return_value="fct_gap135")
    loop._get_repo = MagicMock(return_value=mock_repo)
    return loop


# ─── 1. normalize_expression ────────────────────────────────────────────

class TestNormalizeExpression:
    def test_compresses_whitespace(self):
        assert normalize_expression("rank( low )\n\n") == "rank( low )"
        assert normalize_expression("  a  b   c ") == "a b c"

    def test_empty_and_none(self):
        assert normalize_expression("") == ""
        assert normalize_expression(None) == ""


# ─── 2. find_duplicate_expression ───────────────────────────────────────

class TestFindDuplicateExpression:
    def test_finds_duplicate(self, tmp_path):
        (tmp_path / "fct_existing.json").write_text(
            json.dumps({"factor_id": "fct_existing", "name": "n1", "code": "rank( low )"}),
            encoding="utf-8",
        )
        dup = find_duplicate_expression(
            tmp_path, {"factor_id": "fct_new", "code": "rank( low )"}
        )
        assert dup == {"factor_id": "fct_existing", "name": "n1"}

    def test_skips_self(self, tmp_path):
        (tmp_path / "fct_self.json").write_text(
            json.dumps({"factor_id": "fct_self", "name": "n1", "code": "rank( low )"}),
            encoding="utf-8",
        )
        assert find_duplicate_expression(
            tmp_path, {"factor_id": "fct_self", "code": "rank( low )"}
        ) is None

    def test_empty_dir_returns_none(self, tmp_path):
        assert find_duplicate_expression(
            tmp_path, {"factor_id": "x", "code": "rank( low )"}
        ) is None

    def test_no_code_returns_none(self, tmp_path):
        assert find_duplicate_expression(tmp_path, {"factor_id": "x"}) is None

    def test_bad_json_skipped(self, tmp_path):
        (tmp_path / "fct_bad.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "fct_good.json").write_text(
            json.dumps({"factor_id": "fct_good", "name": "n", "code": "other_expr"}),
            encoding="utf-8",
        )
        assert find_duplicate_expression(
            tmp_path, {"factor_id": "x", "code": "rank( low )"}
        ) is None

    def test_different_expression_returns_none(self, tmp_path):
        (tmp_path / "fct_a.json").write_text(
            json.dumps({"factor_id": "fct_a", "name": "n", "code": "ts_rank(close, 5)"}),
            encoding="utf-8",
        )
        assert find_duplicate_expression(
            tmp_path, {"factor_id": "x", "code": "rank( low )"}
        ) is None


# ─── 3. Q1-Q10 一票否决门禁（GAP-135 ①）────────────────────────────────

class TestQaGateOnPromotion:
    def test_promote_rejected_when_q2_veto(self, tmp_path):
        """economic_logic 缺失 → Q2 一票否决 → 结论禁止入库 → 晋升拒绝。"""
        loop = _make_loop(tmp_path)
        factor = _make_factor()
        factor["economic_logic"] = None  # Q2 逻辑文档化未过（GAP-135 实测场景）
        assert loop._promote_to_elite(
            factor, _make_evaluation(), audit_report=_make_passing_audit()
        ) is None

    def test_promote_allowed_when_no_veto(self, tmp_path):
        """Q1-Q3 全过（含 audit 携带 snooping_check + economic_logic + params）→ 晋升成功。"""
        loop = _make_loop(tmp_path)
        factor = _make_factor("fct_gap135_ok")
        evaluation = _make_evaluation("fct_gap135_ok")
        fp = loop._promote_to_elite(
            factor, evaluation, audit_report=_make_passing_audit()
        )
        assert fp is not None and fp.exists()

    def test_gate_disabled_allows_veto_factor(self, tmp_path, monkeypatch):
        """l2_qa_gate_enabled=false 回退旧行为（v2.104.0 前：结论不拦截晋升）。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(l2_qa_gate_enabled=False),
        )
        loop = _make_loop(tmp_path)
        factor = _make_factor("fct_gap135_off")
        factor["economic_logic"] = None
        # 门禁关闭 → 走后续逻辑（DuckDB mock 写入成功即晋升）
        fp = loop._promote_to_elite(
            factor, _make_evaluation("fct_gap135_off"),
            audit_report=_make_passing_audit(),
        )
        assert fp is not None and fp.exists()


# ─── 4. 同表达式去重门禁（GAP-135 ②）──────────────────────────────────

class TestExpressionDedupOnPromotion:
    def test_promote_rejected_when_duplicate_expression(self, tmp_path):
        """既有 elite 存在同表达式（rank(low) 风格）→ 拒绝晋升（GAP-135 实测场景）。"""
        (tmp_path / "elite" / "fct_existing_dup.json").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "elite" / "fct_existing_dup.json").write_text(
            json.dumps({"factor_id": "fct_existing_dup", "name": "dup", "code": "rank( low )"}),
            encoding="utf-8",
        )
        loop = _make_loop(tmp_path)
        factor = _make_factor("fct_gap135_dup")
        factor["code"] = "rank( low )"
        assert loop._promote_to_elite(
            factor, _make_evaluation("fct_gap135_dup"),
            audit_report=_make_passing_audit(),
        ) is None


# ─── 5. 审计 causal_validity 代码补齐（GAP-135 ③）──────────────────────

class TestCausalValidityCodeResolution:
    def test_expr_factor_code_compiled_in_audit(self):
        """缺 code 但含 expr 的因子：审计 causal_validity 应从 expr 编译补齐 code。"""

        from fts.factor_engine.audit import FactorAuditor

        auditor = FactorAuditor()
        auditor._causal_validator = MagicMock()
        auditor._causal_validator.validate.return_value = {
            "summary": {"anomaly_rate": 0.05},
        }
        factor = {
            "factor_id": "fct_expr_only",
            "name": "expr_only",
            "expr": "ts_rank(close, 5)",
            "params": {},
        }
        # _check_causal_validity 直接调用：code 为空 → expr 编译补齐 → validate 收到含 code 的 prog
        data = pd.DataFrame(
            {"close": np.arange(10.0)},
            index=pd.date_range("2026-01-01", periods=10, freq="D"),
        )
        result = auditor._check_causal_validity(
            factor, data, np.zeros(10)
        )
        assert result.status == "passed"
        # 校验 validate 收到的 prog["code"] 已由 expr 编译补齐
        called_prog = auditor._causal_validator.validate.call_args[0][0]
        assert str(called_prog.get("code") or "").strip()
