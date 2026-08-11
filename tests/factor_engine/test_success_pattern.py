"""tests/factor_engine/test_success_pattern.py — Phase 1.2 P0-1 成功模式定向演化测试。

覆盖（26 号计划 §6）:
    1. analyze_success_patterns: 空链空报告 / 窗口截断 / 时间衰减 / by_method 晋升率 /
       top_operators 提取 / top_window_bins 分箱 / min_sample 不足空报告 / 坏数据降级
    2. MacroEvolver prompt 注入: 有 report 含"近期成功模式"段落 / 无 report 不含 /
       空 report 不含（现有行为不变）
    3. EvolutionLoop._evolve_one: macro 分支把 success_pattern 传给 evolver（进程内缓存）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from fts.factor_engine.contracts import FactorEvaluation
from fts.factor_engine.experience_chain import ExperienceChain


# ─── 工具 ─────────────────────────────────────────────────


def _trace(
    trace_id: str,
    method: str,
    summary: str,
    recorded_at: str,
    success: bool,
) -> dict:
    """构造经验链轨迹 dict。"""
    return {
        "trace_id": trace_id,
        "factor_id": f"fct_{trace_id}",
        "parent_id": "p",
        "generation": 1,
        "mutation_type": method,
        "mutation_summary": summary,
        "evaluation": FactorEvaluation(
            factor_id=f"fct_{trace_id}",
            trace_id=trace_id,
            passed=success,
            failure_reasons=[] if success else ["IC 过低"],
            evaluated_at=recorded_at,
        ),
        "success": success,
        "lessons": ["ok" if success else "fail"],
        "recorded_at": recorded_at,
    }


def _now_iso() -> str:
    return datetime.now().isoformat()


def _days_ago_iso(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def _parent_factor(fid: str = "fct_parent") -> dict:
    """构造最小父因子。"""
    return {
        "factor_id": fid,
        "name": "parent_momentum",
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    return np.tanh(np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10) * 10)\n"
        ),
        "params": {"window": 10},
        "signature": {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 1},
        "economic_logic": {"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "t"},
    }


def _mock_llm_ok() -> MagicMock:
    """返回返回合法 JSON 的 mock LLM。"""
    llm = MagicMock()
    llm.complete.return_value = (
        json.dumps(
            {
                "mutation_type": "macro_logic",
                "mutation_summary": "mutation",
                "full_code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    close = data['close']\n"
                    "    return np.zeros(len(close))\n"
                ),
                "economic_logic_modification": {
                    "theory": 3,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "n",
                },
                "lessons_referenced": [],
            }
        ),
        100,
    )
    return llm


def _default_config(**overrides):
    from fts.factor_engine.success_pattern import SuccessPatternConfig

    params = dict(enabled=True, window_days=14, decay=0.9, min_sample=10, max_operators=5)
    params.update(overrides)
    return SuccessPatternConfig(**params)


# ─── 1. analyze_success_patterns ──────────────────────────


class TestAnalyzeSuccessPatterns:
    def test_empty_chain_returns_empty_report(self, tmp_path):
        """空经验链 → 空报告（sample_count=0，字段全空，不注入）。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        report = analyze_success_patterns(chain, _default_config())

        assert report.sample_count == 0
        assert report.by_method == {}
        assert report.top_operators == []
        assert report.top_window_bins == []

    def test_window_truncates_old_traces(self, tmp_path):
        """窗口外（> window_days）的轨迹不参与统计。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        chain.record_success(_trace("s_recent", "macro_evolution", "OpEvolve: ts_mean(close, 10)", _days_ago_iso(2), True))
        chain.record_success(_trace("s_old", "macro_evolution", "OpEvolve: ts_std(close, 20)", _days_ago_iso(20), True))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        # 20 天前被窗口排除
        assert report.sample_count == 1
        assert "ts_std" not in report.top_operators

    def test_by_method_rate(self, tmp_path):
        """by_method: rate = promoted/(promoted+failed)，同日轨迹权重一致。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        now = _now_iso()
        chain.record_success(_trace("s1", "macro_evolution", "m", now, True))
        chain.record_failure(_trace("f1", "macro_evolution", "m", now, False))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        stat = report.by_method["macro_evolution"]
        assert stat.promoted == pytest.approx(1, abs=1e-6)
        assert stat.evaluated == pytest.approx(2, abs=1e-6)
        assert stat.rate == pytest.approx(0.5)

    def test_time_decay_weights_recent(self, tmp_path):
        """时间衰减：近期轨迹权重高于远期（decay=0.9）。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        # 1 天前 vs 10 天前：weight = 0.9 vs 0.9**10 ≈ 0.3487
        chain.record_success(_trace("s1", "macro_evolution", "m", _days_ago_iso(1), True))
        chain.record_success(_trace("s2", "macro_evolution", "m", _days_ago_iso(10), True))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        assert report.sample_count == 2
        stat = report.by_method["macro_evolution"]
        assert stat.promoted == pytest.approx(0.9 + 0.9**10, abs=1e-6)

    def test_top_operators_extraction(self, tmp_path):
        """top_operators: 从 mutation_summary 提取算子名（`identifier(` 模式），按权重计频。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        now = _now_iso()
        chain.record_success(_trace("s1", "operator_evolution", "OpEvolve: ts_mean(close, 10)", now, True))
        chain.record_success(_trace("s2", "operator_evolution", "OpEvolve: ts_std(close, 20)", now, True))
        chain.record_success(_trace("s3", "operator_evolution", "OpEvolve: ts_mean(volume, 5)", now, True))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        assert report.top_operators[0] == "ts_mean"
        assert set(report.top_operators) >= {"ts_mean", "ts_std"}

    def test_top_window_bins(self, tmp_path):
        """top_window_bins: 从 summary 提取整数窗口参数并分箱。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        now = _now_iso()
        chain.record_success(_trace("s1", "operator_evolution", "OpEvolve: ts_mean(close, 10)", now, True))
        chain.record_success(_trace("s2", "operator_evolution", "OpEvolve: ts_std(close, 20)", now, True))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        assert "6-10" in report.top_window_bins
        assert "11-20" in report.top_window_bins

    def test_min_sample_below_threshold(self, tmp_path):
        """成功样本 < min_sample → 空报告（不注入，防过拟合）。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        now = _now_iso()
        chain.record_success(_trace("s1", "macro_evolution", "m", now, True))
        chain.record_success(_trace("s2", "macro_evolution", "m", now, True))

        report = analyze_success_patterns(chain, _default_config(min_sample=10))

        assert report.sample_count == 0
        assert report.by_method == {}
        assert report.top_operators == []

    def test_bad_recorded_at_degrades(self, tmp_path):
        """非法 recorded_at 的轨迹跳过，不抛异常（降级）。"""
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        now = _now_iso()
        chain.record_success(_trace("s1", "macro_evolution", "m", now, True))
        chain.record_success(_trace("s_bad", "macro_evolution", "m", "not-a-date", True))

        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        assert report.sample_count == 1
        assert report.by_method["macro_evolution"].promoted == pytest.approx(1, abs=1e-6)


# ─── 2. MacroEvolver prompt 注入 ──────────────────────────


class TestMacroEvolverSuccessPattern:
    def test_prompt_injects_success_pattern(self, tmp_path):
        """有非空 report → prompt 含"近期成功模式"段落。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        chain.record_success(_trace("s1", "macro_evolution", "OpEvolve: ts_mean(close, 10)", _now_iso(), True))
        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=chain)
        evolver.evolve(_parent_factor(), generation=1, success_pattern=report)

        prompt = evolver.llm.complete.call_args.args[0]
        assert "近期成功模式" in prompt
        assert "参考，非硬性约束" in prompt

    def test_prompt_without_report_unchanged(self):
        """无 success_pattern → prompt 不含该段落（现有行为回归）。"""
        from fts.factor_engine.macro_evolution import MacroEvolver

        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=None)
        evolver.evolve(_parent_factor(), generation=1)

        prompt = evolver.llm.complete.call_args.args[0]
        assert "近期成功模式" not in prompt

    def test_prompt_empty_report_not_injected(self, tmp_path):
        """空 report（sample_count=0）→ 不注入段落。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        report = analyze_success_patterns(chain, _default_config())  # 空链 → 空报告

        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=chain)
        evolver.evolve(_parent_factor(), generation=1, success_pattern=report)

        prompt = evolver.llm.complete.call_args.args[0]
        assert "近期成功模式" not in prompt

    def test_evolve_with_report_returns_factor(self, tmp_path):
        """带 report 的 evolve 仍正常产出新因子。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.success_pattern import analyze_success_patterns

        chain = ExperienceChain(tmp_path / "evo")
        chain.record_success(_trace("s1", "macro_evolution", "m", _now_iso(), True))
        report = analyze_success_patterns(chain, _default_config(min_sample=1))

        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=chain)
        new_factor, summary, tokens = evolver.evolve(_parent_factor(), generation=1, success_pattern=report)

        assert new_factor.get("factor_id")
        assert new_factor.get("parent_id") == "fct_parent"
        assert tokens == 100


# ─── 3. EvolutionLoop._evolve_one 传递 ────────────────────


class TestEvolveOnePassesSuccessPattern:
    def test_macro_branch_passes_report(self, minimal_loop):
        """macro 分支把 success_pattern 传给 evolver（进程内缓存复用）。"""
        mock_evolve = MagicMock(return_value=(dict(_parent_factor()), "summary", 0))
        minimal_loop.macro_evolver.evolve = mock_evolve

        parent = _parent_factor()
        minimal_loop._evolve_one(parent, generation=1, trace_id="trace_x", method_hint="macro")

        kwargs = mock_evolve.call_args.kwargs
        # 空经验链 → 空 report 对象（sample_count=0），prompt 层不注入
        assert kwargs.get("success_pattern") is not None
        assert kwargs["success_pattern"].sample_count == 0

    def test_report_cached_across_calls(self, minimal_loop):
        """进程内缓存：第二次调用不再重读经验链。"""
        read_all_success = MagicMock(wraps=minimal_loop.experience_chain.read_all_success)
        minimal_loop.experience_chain.read_all_success = read_all_success

        mock_evolve = MagicMock(return_value=(dict(_parent_factor()), "summary", 0))
        minimal_loop.macro_evolver.evolve = mock_evolve

        parent = _parent_factor()
        minimal_loop._evolve_one(parent, generation=1, trace_id="trace_x", method_hint="macro")
        minimal_loop._evolve_one(parent, generation=2, trace_id="trace_y", method_hint="macro")

        # 首次构造 report 读取一次，第二次命中缓存不再读取
        assert read_all_success.call_count == 1
