"""tests/factor_engine/test_microstructure_promotion.py — C1 评估晋升接线测试。

覆盖（C1 实施设计延续，2026-08-11）:
    1. run_microstructure_promotion：候选 → L2 评估链 → 审计 → elite 全链路
    2. 无候选（tick 数据不足）全 skipped、limit 截断
    3. 评估未过门槛不晋升、评估异常单候选跳过（不阻断整批）
    4. 审计异常降级不拦截晋升
    5. CLI parser 含 micro-evaluate 子命令
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.microstructure_generator import MicrostructureFactorCandidate

RNG = np.random.default_rng(11)


def _mock_eval(passed: bool = True):
    """mock 评估结果（避免真实编译候选 code）。返回 FactorEvaluation 形状 dict。"""
    return {
        "factor_id": "fct_micro_x",
        "trace_id": "t",
        "passed": passed,
        "failure_reasons": [] if passed else ["ic 低质"],
        "evaluated_at": "",
    }


def _make_candidate(factor_id: str, symbol: str = "RB0", kind: str = "ofi_mean") -> MicrostructureFactorCandidate:
    """最小合法 microstructure 候选（对齐 test_microstructure_generator.py）。"""
    return MicrostructureFactorCandidate(
        factor={
            "factor_id": factor_id,
            "name": f"micro_{symbol}_{kind}",
            "code": "close - close.shift(1)",
            "params": {"dates": ["2026-01-05", "2026-01-06"], "values": [1.0, 2.0], "symbol": symbol},
            "signature": "sig",
            "economic_logic": {"microstructure": 3},
            "family": "microstructure",
        },
        symbol=symbol,
        kind=kind,
        n_days=2,
        generated_at="2026-08-11",
    )


def _make_loop(tmp_path) -> EvolutionLoop:
    """最小 EvolutionLoop 实例（对齐 test_evolution_loop.py）。"""
    n = 120
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    data = pd.DataFrame(
        {"open": np.full(n, 1.0), "high": np.full(n, 1.0), "low": np.full(n, 1.0), "close": np.arange(1, n + 1, dtype=float)},
        index=idx,
    )
    fwd = RNG.normal(0, 0.01, n)
    loop = EvolutionLoop(
        data=data,
        forward_returns=fwd,
        elite_dir=str(tmp_path / "elite"),
        memory_dir=str(tmp_path / "memory"),
        llm_client=None,
        n_trials_micro=3,
        market="futures",
    )
    # 横截面模式最小数据（_evaluate_cross_section 依赖）
    loop.cross_section_data = {"RB0": data}
    loop.cross_section_dates = idx
    return loop


class TestRunMicrostructurePromotion:
    """C1 评估晋升链路。"""

    def test_no_candidates_all_skipped(self, tmp_path, monkeypatch):
        """tick 数据不足（generate_batch 返回空）→ generated=0 不阻断。"""
        loop = _make_loop(tmp_path)

        class _EmptyGen:
            def generate_batch(self, symbols=None, trace_id=""):
                return []

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _EmptyGen)
        res = loop.run_microstructure_promotion(trace_id="t-c1-empty")
        assert res["generated"] == 0
        assert res["evaluated"] == 0 and res["promoted"] == 0

    def test_passed_candidate_promoted(self, tmp_path, monkeypatch):
        """候选通过评估门槛 → 晋升 elite（promoted=1，id 记录）。"""
        loop = _make_loop(tmp_path)

        class _Gen:
            def generate_batch(self, symbols=None, trace_id=""):
                return [_make_candidate("fct_micro_1")]

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _Gen)

        ev = _mock_eval(passed=True)
        monkeypatch.setattr(loop, "_evaluate_cross_section", lambda factor, tid: ev)
        monkeypatch.setattr(
            loop,
            "_promote_to_elite",
            lambda factor, evaluation, audit_report=None, **kw: tmp_path / "elite" / "fct_micro_1.json",
        )
        res = loop.run_microstructure_promotion(trace_id="t-c1-pass")
        assert res["generated"] == 1 and res["evaluated"] == 1
        assert res["passed"] == 1 and res["promoted"] == 1
        assert res["promoted_ids"] == ["fct_micro_1"]

    def test_failed_candidate_not_promoted(self, tmp_path, monkeypatch):
        """评估未过门槛 → evaluated 但不 passed 不 promoted。"""
        loop = _make_loop(tmp_path)

        class _Gen:
            def generate_batch(self, symbols=None, trace_id=""):
                return [_make_candidate("fct_micro_low")]

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _Gen)

        ev = _mock_eval(passed=False)
        monkeypatch.setattr(loop, "_evaluate_cross_section", lambda factor, tid: ev)
        monkeypatch.setattr(loop, "_promote_to_elite", lambda factor, evaluation, audit_report=None, **kw: Path("x"))
        res = loop.run_microstructure_promotion(trace_id="t-c1-fail")
        assert res["evaluated"] == 1 and res["passed"] == 0 and res["promoted"] == 0

    def test_eval_exception_single_skipped(self, tmp_path, monkeypatch):
        """单候选评估异常 → skipped，其余继续。"""
        loop = _make_loop(tmp_path)

        class _Gen:
            def generate_batch(self, symbols=None, trace_id=""):
                return [_make_candidate("fct_micro_bad"), _make_candidate("fct_micro_ok")]

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _Gen)

        def _flaky(factor, tid):
            if factor["factor_id"] == "fct_micro_bad":
                raise RuntimeError("boom")
            return _mock_eval(passed=True)

        monkeypatch.setattr(loop, "_evaluate_cross_section", _flaky)
        monkeypatch.setattr(loop, "_promote_to_elite", lambda factor, evaluation, audit_report=None, **kw: Path("ok.json"))
        res = loop.run_microstructure_promotion(trace_id="t-c1-flaky")
        assert res["generated"] == 2
        assert res["skipped"] == 1 and res["promoted"] == 1

    def test_audit_exception_degrades(self, tmp_path, monkeypatch):
        """审计异常降级 → 仍晋升（audit=None 不拦截）。"""
        loop = _make_loop(tmp_path)

        class _Gen:
            def generate_batch(self, symbols=None, trace_id=""):
                return [_make_candidate("fct_micro_aud")]

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _Gen)
        ev = _mock_eval(passed=True)
        monkeypatch.setattr(loop, "_evaluate_cross_section", lambda factor, tid: ev)

        class _BadAuditor:
            def audit(self, **kw):
                raise RuntimeError("audit boom")

        monkeypatch.setattr("fts.factor_engine.audit.FactorAuditor", lambda: _BadAuditor())
        monkeypatch.setattr(loop, "_promote_to_elite", lambda factor, evaluation, audit_report=None, **kw: Path("aud.json"))
        res = loop.run_microstructure_promotion(trace_id="t-c1-audit")
        assert res["promoted"] == 1

    def test_limit_truncates(self, tmp_path, monkeypatch):
        """limit 截断候选数。"""
        loop = _make_loop(tmp_path)

        class _Gen:
            def generate_batch(self, symbols=None, trace_id=""):
                return [_make_candidate(f"fct_micro_{i}") for i in range(3)]

        monkeypatch.setattr("fts.factor_engine.microstructure_generator.MicrostructureFactorGenerator", _Gen)
        ev = _mock_eval(passed=True)
        monkeypatch.setattr(loop, "_evaluate_cross_section", lambda factor, tid: ev)
        monkeypatch.setattr(
            loop, "_promote_to_elite", lambda factor, evaluation, audit_report=None, **kw: Path("x.json")
        )
        res = loop.run_microstructure_promotion(limit=2, trace_id="t-c1-limit")
        assert res["generated"] == 2


def test_cli_parser_has_micro_evaluate():
    """CLI parser 含 factor micro-evaluate 子命令。"""
    from fts.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")  # noqa: SLF001
    factor_sub = next(a for a in sub.choices["factor"]._actions if a.dest == "subcommand")  # noqa: SLF001
    assert "micro-evaluate" in factor_sub.choices
