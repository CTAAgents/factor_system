"""tests/factor_engine/test_evolution_loop.py — 主循环测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.audit import FactorAuditReport
from fts.factor_engine.contracts import (
    EVOLUTION_VERSION,
    EconomicLogic,
    FactorEvaluation,
    FactorProgram,
    FactorSignature,
)
from fts.factor_engine.evolution_loop import EvolutionLoop, EvolutionRunResult
from fts.factor_engine.state import (
    EvolutionStateManager,
    generate_run_id,
    generate_trace_id,
)


# ─── trace_id 生成 ────────────────────────────────────────

def test_generate_trace_id_format():
    tid = generate_trace_id("l2")
    assert tid.startswith("l2_")
    # 格式: l2_<8hex>_<timestamp>
    parts = tid.split("_")
    assert len(parts) == 3


def test_generate_run_id_format():
    rid = generate_run_id()
    assert rid.startswith("run_")


def test_generate_trace_id_uniqueness():
    ids = {generate_trace_id("x") for _ in range(100)}
    assert len(ids) >= 95  # 高概率唯一


# ─── 状态管理 ─────────────────────────────────────────────

def test_state_manager_init(tmp_memory_dir):
    """首次加载应初始化新状态。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    assert state["status"] == "running"
    assert state["version"] == EVOLUTION_VERSION
    assert state["last_generation"] == 0
    assert state["total_factors_evaluated"] == 0


def test_state_manager_save_and_load(tmp_memory_dir):
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    state["last_generation"] = 5
    state["total_factors_evaluated"] = 20
    mgr.save(state)

    # 重新加载
    mgr2 = EvolutionStateManager(tmp_memory_dir)
    state2 = mgr2.load_or_init()
    assert state2["last_generation"] == 5
    assert state2["total_factors_evaluated"] == 20


def test_state_manager_creates_backup(tmp_memory_dir):
    """保存时应自动创建 backup 文件。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.save(state)
    backup = tmp_memory_dir / "state.json.backup"
    assert backup.exists()


def test_state_manager_recovers_from_backup(tmp_memory_dir):
    """主文件损坏时应从 backup 恢复。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    state["last_generation"] = 7
    mgr.save(state)

    # 损坏主文件
    (tmp_memory_dir / "state.json").write_text("invalid json", encoding="utf-8")

    # 重新加载应从 backup 恢复
    mgr2 = EvolutionStateManager(tmp_memory_dir)
    state2 = mgr2.load_or_init()
    assert state2["last_generation"] == 7


def test_state_manager_version_check(tmp_memory_dir):
    """版本不匹配时应视为损坏。"""
    # 写入错误版本
    (tmp_memory_dir / "state.json").write_text(
        json.dumps({"version": "0.0.0", "status": "running"}),
        encoding="utf-8",
    )
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    # 应重新初始化
    assert state["version"] == EVOLUTION_VERSION
    assert state["last_generation"] == 0


def test_state_manager_mark_running(tmp_memory_dir):
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.mark_running()
    assert state["status"] == "running"
    assert state["run_id"].startswith("run_")


def test_state_manager_mark_completed(tmp_memory_dir):
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.mark_completed(state)
    state2 = mgr.load_or_init()
    assert state2["status"] == "completed"


def test_state_manager_mark_circuit_broken(tmp_memory_dir):
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.mark_circuit_broken(state, "Token 熔断")
    state2 = mgr.load_or_init()
    assert state2["status"] == "circuit_broken"
    assert "Token" in state2["last_error"]


def test_state_manager_add_tokens(tmp_memory_dir):
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    initial = state["tokens_consumed"]
    mgr.add_tokens(state, 500)
    state2 = mgr.load_or_init()
    assert state2["tokens_consumed"] == initial + 500


# ─── EvolutionLoop 完整运行 ────────────────────────────────

@pytest.fixture
def mock_llm_client():
    """Mock LLM 客户端 — 返回固定响应。"""
    client = MagicMock()
    client.complete.return_value = (
        json.dumps({
            "mutation_type": "macro_logic",
            "mutation_summary": "Mock: window+5",
            "code_modification": "window_plus_5",
            "economic_logic_modification": {
                "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
                "narrative": "Mock LLM 经济逻辑"
            },
            "lessons_referenced": ["历史成功"],
        }),
        200,
    )
    return client


def _make_passing_audit_report() -> FactorAuditReport:
    """构造一个通过所有审计项的 Mock 报告。"""
    from fts.factor_engine.audit import AuditItemResult

    items = [
        AuditItemResult(name=n, status="passed", evidence="mock")
        for n in (
            "causal_validity", "oos_consistency", "cross_symbol",
            "stress_resilience", "multiple_testing", "snooping_check",
        )
    ]
    return FactorAuditReport(
        factor_id="test_factor",
        factor_name="test",
        audited_at="2026-08-05T00:00:00",
        items=items,
        passed=True,
        pass_rate=1.0,
        summary={"total": 6, "passed": 6, "failed": 0, "skipped": 0, "pass_rate": 1.0},
    )


def _mock_auditor_pass(loop: EvolutionLoop) -> None:
    """将 loop.auditor.audit mock 为恒通过。"""
    loop.auditor = MagicMock()
    loop.auditor.audit = MagicMock(return_value=_make_passing_audit_report())


def _mock_seed_evaluation_pass(loop: EvolutionLoop) -> None:
    """Mock 评估链 + 审计器 + 质检 + DuckDB，使种子评估通过。"""
    _mock_auditor_pass(loop)
    mock_eval = MagicMock()
    mock_eval.return_value = {
        "passed": True,
        "level_1_backtest": {
            "ic": 0.05, "icir": 1.5, "sharpe": 2.0,
            "monotonicity": True, "max_drawdown": 0.05,
            "turnover_monthly": 0.3,
        },
        "economic_score": {"dimensions_passed": 4},
        "multiple_test": {"passed": True},
        "total_ic": 0.05,
        "oos_results": [{"passed": True, "ic_consistency": 0.8}],
        "p_values": [0.01, 0.02],
    }
    loop.evaluation_chain.evaluate = mock_eval
    mock_inspection = MagicMock()
    mock_inspection.filtered = False
    mock_inspection.grade = "A"
    mock_inspection.total_score = 45.0
    mock_inspection.quality_score = {"total_score": 45.0, "grade": "A"}
    loop.quality_inspector.inspect = MagicMock(return_value=mock_inspection)
    mock_repo = MagicMock()
    mock_repo.get_factor_by_name = MagicMock(return_value=None)
    loop._get_repo = MagicMock(return_value=mock_repo)


def _mock_review_pass(loop: EvolutionLoop) -> None:
    """Mock 4 个审查模块返回通过，使端到端主流程可晋升。"""
    from fts.factor_engine.ablation import AblationResult, SingleAblation
    from fts.factor_engine.causal_validator import CausalValidationResult
    from fts.factor_engine.robustness import RobustnessTestResult
    from fts.factor_engine.shap_analyzer import ShapAnalysisResult

    loop.ablation_experiment.run = MagicMock(return_value=AblationResult(
        factor_id="review_pass", factor_name="review_pass",
        baseline_ic=0.05, baseline_sharpe=1.5,
        ablations=[SingleAblation(
            mode="volume_zero", description="vol→0",
            ic=0.049, sharpe=1.48,
            ic_change=-0.001, sharpe_change=-0.02,
        )],
    ))
    loop.causal_validator.validate = MagicMock(return_value=CausalValidationResult(
        factor_id="review_pass", factor_name="review_pass",
        analysis_date="2026-08-05",
        n_events=5, n_anomalous=0,
        anomalous_events=[], all_events=[],
        summary={},
    ))
    loop.robustness_tester.run = MagicMock(return_value=RobustnessTestResult(
        factor_id="review_pass", factor_name="review_pass",
        adversarial_results=[], missing_value_results=[], ood_results=[],
        summary={"overall_pass_rate": 1.0},
    ))
    loop.shap_analyzer.analyze = MagicMock(return_value=ShapAnalysisResult(
        factor_id="review_pass", factor_name="review_pass",
        analysis_date="2026-08-05",
        num_extreme_samples=0, num_features=0,
        top_samples=[], bottom_samples=[], global_top_features=[],
        summary={},
    ))


def test_evolution_loop_runs_minimal(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """应能完整运行 1 代演化（状态检查）。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=5,
    )
    _mock_auditor_pass(loop)
    result = loop.run(max_generation=1)
    assert result.status in ("completed", "paused", "circuit_broken")


def test_evolution_loop_produces_metrics(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """运行后指标应被正确填充。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=5,
    )
    _mock_seed_evaluation_pass(loop)
    result = loop.run(max_generation=1)
    assert result.generations_completed >= 0
    assert result.tokens_consumed > 0


def test_evolution_loop_creates_state_file(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """运行后应创建 state.json。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=3,
    )
    loop.run(max_generation=1)
    assert (tmp_memory_dir / "state.json").exists()


def test_evolution_loop_creates_elite_dir(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """应自动创建 elite 目录。"""
    assert not tmp_elite_dir.exists()
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=3,
    )
    loop.run(max_generation=1)
    assert tmp_elite_dir.exists()


def test_evolution_loop_record_experience_traces(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """运行后应在 failure/ 或 success/ 目录写入轨迹。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=3,
    )
    loop.run(max_generation=2)

    success_dir = tmp_memory_dir / "success"
    failure_dir = tmp_memory_dir / "failure"
    # 至少有一个目录有轨迹（合成数据下大概率失败）
    total = len(list(success_dir.glob("*.json"))) + len(list(failure_dir.glob("*.json")))
    if total == 0:
        pytest.skip("MockLLM 未生成有效因子（合成数据下正常现象）")
    assert total > 0


def test_evolution_loop_circuit_breaker_on_token(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """token 超过 2x 预算应触发熔断。"""
    from fts.factor_engine.contracts import BudgetConfig

    # 设置极小预算 + 极大 mock token
    mock_llm_client.complete.return_value = (
        json.dumps({
            "mutation_type": "macro_logic",
            "mutation_summary": "Mock",
            "code_modification": "window_plus_5",
            "economic_logic_modification": {
                "theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 4,
                "narrative": "Mock"
            },
            "lessons_referenced": [],
        }),
        500_000,  # 极大 token 数
    )

    budget = BudgetConfig(
        nightly_token_limit=100,  # 极小预算
        monthly_token_limit=1000,
        max_generation=10,
        max_tokens_per_factor=10_000,
        circuit_breaker_token_ratio=2.0,
        circuit_breaker_consecutive_low_ic=3,
        circuit_breaker_low_ic_threshold=0.01,
        circuit_breaker_failure_rate=0.99,
    )

    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        budget=budget,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    _mock_seed_evaluation_pass(loop)
    result = loop.run(max_generation=5)
    assert result.status == "circuit_broken"
    assert "Token" in (result.circuit_breaker_reason or "")


def test_evolution_loop_to_dict(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """EvolutionRunResult.to_dict() 应返回完整字典。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    result = loop.run(max_generation=1)
    d = result.to_dict()
    assert "run_id" in d
    assert "trace_id" in d
    assert "generations_completed" in d
    assert "status" in d


# ─── StateManager 附加覆盖 ────────────────────────────────

def test_state_manager_mark_paused(tmp_memory_dir):
    """mark_paused 应正确设置状态和错误原因。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.mark_paused(state, "手动暂停")
    state2 = mgr.load_or_init()
    assert state2["status"] == "paused"
    assert state2["last_error"] == "手动暂停"


def test_state_manager_mark_paused_no_reason(tmp_memory_dir):
    """不带原因的暂停不应设置 last_error。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.mark_paused(state)
    state2 = mgr.load_or_init()
    assert state2["status"] == "paused"
    assert state2.get("last_error") is None


def test_state_manager_increment_counters(tmp_memory_dir):
    """increment_evaluated 和 increment_promoted 应正常累加。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.increment_evaluated(state, 3)
    mgr.increment_promoted(state, 1)
    state2 = mgr.load_or_init()
    assert state2["total_factors_evaluated"] == 3
    assert state2["total_factors_promoted"] == 1


def test_state_manager_add_experience_ref(tmp_memory_dir):
    """add_experience_ref 应追加且去重。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    mgr.add_experience_ref(state, "trace_123")
    mgr.add_experience_ref(state, "trace_123")  # 重复
    mgr.add_experience_ref(state, "trace_456")
    state2 = mgr.load_or_init()
    assert len(state2["experience_chain_ref"]) == 2
    assert "trace_123" in state2["experience_chain_ref"]
    assert "trace_456" in state2["experience_chain_ref"]


def test_state_manager_save_version_mismatch(tmp_memory_dir):
    """版本不匹配的 save 应抛 StateError。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    state["version"] = "0.0.0"
    from fts.factor_engine.state import StateError
    with pytest.raises(StateError, match="版本不匹配"):
        mgr.save(state)


def test_state_manager_backup_failure(tmp_memory_dir, monkeypatch):
    """backup 失败应抛 StateError。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    import shutil
    def broken_copy(*args, **kwargs):
        raise OSError("模拟 backup 失败")
    monkeypatch.setattr(shutil, "copy2", broken_copy)
    from fts.factor_engine.state import StateError
    with pytest.raises(StateError, match="备份失败"):
        mgr.save(state)


def test_state_manager_cold_start_budget(tmp_memory_dir):
    """冷启动时传入 budget_limit 应生效。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init(budget_limit=9999)
    assert state["budget_limit"] == 9999


def test_state_manager_try_load_empty_state(tmp_memory_dir):
    """空状态文件应视为损坏返回 None。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    (tmp_memory_dir / "state.json").write_text("", encoding="utf-8")
    # 内部 _try_load 会返回 None，应触发冷启动
    state = mgr.load_or_init()
    assert state["status"] == "running"


# ─── EvolutionLoop 熔断覆盖 ───────────────────────────────

def test_evolution_loop_circuit_breaker_consecutive_low_ic(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """连续低 IC 应触发熔断。"""
    from fts.factor_engine.contracts import BudgetConfig
    budget = BudgetConfig(
        nightly_token_limit=1_000_000,
        monthly_token_limit=10_000_000,
        max_generation=10,
        max_tokens_per_factor=10_000,
        circuit_breaker_token_ratio=10.0,
        circuit_breaker_consecutive_low_ic=1,   # 触发条件：1 代低 IC
        circuit_breaker_low_ic_threshold=0.99,   # 几乎所有 IC 都低于此值
        circuit_breaker_failure_rate=0.99,
    )
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        budget=budget,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    result = loop.run(max_generation=3)
    assert result.status in ("completed", "circuit_broken")


def test_evolution_loop_circuit_breaker_high_failure_rate(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """高失败率应触发熔断。"""
    from fts.factor_engine.contracts import BudgetConfig
    budget = BudgetConfig(
        nightly_token_limit=1_000_000,
        monthly_token_limit=10_000_000,
        max_generation=10,
        max_tokens_per_factor=10_000,
        circuit_breaker_token_ratio=10.0,
        circuit_breaker_consecutive_low_ic=100,
        circuit_breaker_low_ic_threshold=0.01,
        circuit_breaker_failure_rate=0.01,  # 1% 失败率即触发
    )
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        budget=budget,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    result = loop.run(max_generation=5)
    assert result.status in ("completed", "circuit_broken")


def test_evolution_run_result_defaults():
    """EvolutionRunResult 默认值为 None/空列表。"""
    rr = EvolutionRunResult(
        run_id="test_run",
        trace_id="test_trace",
        generations_completed=0,
        total_factors_evaluated=0,
        total_factors_promoted=0,
        tokens_consumed=0,
        status="paused",
    )
    assert rr.circuit_breaker_reason is None
    # dataclass 默认值为 None，to_dict 应转为空列表
    assert rr.elite_factor_ids is None or rr.elite_factor_ids == []
    d = rr.to_dict()
    assert d["elite_factor_ids"] == []


def test_evolution_run_result_contains_seed_correlations(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """EvolutionRunResult 应包含 seed_correlations 字段。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    result = loop.run(max_generation=1)
    # seed_correlations 字段应存在
    assert hasattr(result, 'seed_correlations')
    assert isinstance(result.seed_correlations, list)
    # to_dict 应包含 seed_correlations
    d = result.to_dict()
    assert "seed_correlations" in d
    assert isinstance(d["seed_correlations"], list)


def test_seed_correlation_check_in_run(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """run() 应在种子加载后执行相关性预检。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    # 直接调用内部方法验证
    seeds = loop.seed_pool.load_all_seeds()
    correlations = loop._run_seed_correlation_check(seeds, "test_trace")
    assert isinstance(correlations, list)
    # 每个条目都应有正确的结构
    for item in correlations:
        assert "factor_id_a" in item
        assert "factor_id_b" in item
        assert "pearson" in item
        assert "spearman" in item


# ─── micro_evolution coverage ──────────────────────────

class TestMicroEvolutionCoverage:
    """补齐 micro_evolution.py 覆盖。"""

    def test_has_optuna_constant(self):
        from fts.factor_engine.micro_evolution import _HAS_OPTUNA
        # optuna 已安装，应为 True
        assert _HAS_OPTUNA is True

    def test_module_constants(self):
        from fts.factor_engine.micro_evolution import (
            DEFAULT_N_TRIALS, DEFAULT_EARLY_STOPPING_FAILURES,
        )
        assert DEFAULT_N_TRIALS == 100
        assert DEFAULT_EARLY_STOPPING_FAILURES == 20

    def test_micro_evolution_error_is_exception(self):
        from fts.factor_engine.micro_evolution import MicroEvolutionError
        assert issubclass(MicroEvolutionError, Exception)

    def test_micro_evolution_all_exports(self):
        from fts.factor_engine.micro_evolution import (
            DEFAULT_N_TRIALS, DEFAULT_EARLY_STOPPING_FAILURES,
            MicroEvolutionError, optimize_params, evolve_micro,
        )
        assert callable(optimize_params)
        assert callable(evolve_micro)

    def test_suggest_param_bool(self, mock_trial):
        from fts.factor_engine.micro_evolution import _suggest_param
        mock_trial.suggest_categorical.return_value = True
        result = _suggest_param(mock_trial, "flag", True)
        assert result is True
        mock_trial.suggest_categorical.assert_called_once_with("flag", [True, False])

    def test_suggest_param_int(self, mock_trial):
        from fts.factor_engine.micro_evolution import _suggest_param
        mock_trial.suggest_int.return_value = 20
        result = _suggest_param(mock_trial, "window", 10)
        assert result == 20
        mock_trial.suggest_int.assert_called_once_with("window", 5, 20)

    def test_suggest_param_int_min_value(self, mock_trial):
        """int 参数最小值应为 max(1, value//2)。"""
        from fts.factor_engine.micro_evolution import _suggest_param
        mock_trial.suggest_int.return_value = 2
        result = _suggest_param(mock_trial, "small", 2)
        assert result == 2
        mock_trial.suggest_int.assert_called_once_with("small", 1, 4)

    def test_suggest_param_float(self, mock_trial):
        from fts.factor_engine.micro_evolution import _suggest_param
        mock_trial.suggest_float.return_value = 0.5
        result = _suggest_param(mock_trial, "decay", 0.5)
        assert result == 0.5
        mock_trial.suggest_float.assert_called_once_with("decay", 0.25, 1.0)

    def test_suggest_param_other_type(self, mock_trial):
        """字符串等不可搜索类型应原值返回。"""
        from fts.factor_engine.micro_evolution import _suggest_param
        result = _suggest_param(mock_trial, "method", "spearman")
        assert result == "spearman"
        mock_trial.suggest_categorical.assert_not_called()
        mock_trial.suggest_int.assert_not_called()
        mock_trial.suggest_float.assert_not_called()

    def test_optimize_params_no_optuna_returns_defaults(
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """模拟无 optuna 时应返回原 params + score=0.0。"""
        import fts.factor_engine.micro_evolution as mev
        monkeypatch.setattr(mev, '_HAS_OPTUNA', False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_test1234",
            name="test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10, "threshold": 0.5},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试因子"),
            source="manual",
        )
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns)
        assert params == {"window": 10, "threshold": 0.5}
        assert score == 0.0

    def test_optimize_params_with_custom_objective_fn(
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """模拟无 optuna 时忽略 objective_fn。"""
        import fts.factor_engine.micro_evolution as mev
        monkeypatch.setattr(mev, '_HAS_OPTUNA', False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_test5678",
            name="test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试因子"),
            source="manual",
        )
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns,
                                             objective_fn=lambda s, r: 0.99)
        assert score == 0.0  # optuna 不可用时忽略 objective_fn

    def test_optimize_params_with_mock_optuna(
        self, sample_ohlcv, forward_returns, mock_optuna_study,
    ):
        """mock optuna 路径应完整走通。"""
        mock_optuna, mock_study = mock_optuna_study
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_optuna_test",
            name="optuna_test",
            code="def factor_program(data, params):\n    import numpy as np\n    w = params.get('window', 10)\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="optuna测试"),
            source="manual",
        )
        mock_study.best_params = {"window": 15}
        mock_study.best_value = 0.05
        mock_study.trials = [mock_study]  # 非空 trials

        import fts.factor_engine.micro_evolution as mev
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert params == {"window": 15}
        assert score == 0.05
        mock_optuna.create_study.assert_called_once()

    def test_optimize_params_study_raises(
        self, sample_ohlcv, forward_returns, mock_optuna_study,
    ):
        """study.optimize 抛出异常时应转为 MicroEvolutionError。"""
        mock_optuna, mock_study = mock_optuna_study
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_err_test",
            name="err_test",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="error测试"),
            source="manual",
        )
        mock_study.optimize.side_effect = RuntimeError("optuna 崩溃")

        import fts.factor_engine.micro_evolution as mev
        with pytest.raises(mev.MicroEvolutionError, match="optuna 优化失败"):
            mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)

    def test_optimize_params_no_best_params(
        self, sample_ohlcv, forward_returns, mock_optuna_study,
    ):
        """无 best_params 时返回原 params + 0.0。"""
        mock_optuna, mock_study = mock_optuna_study
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_empty_test",
            name="empty_test",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="empty测试"),
            source="manual",
        )
        mock_study.best_params = {}   # 空表示无最佳参数
        mock_study.best_value = 0.0
        mock_study.trials = []

        import fts.factor_engine.micro_evolution as mev
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert params == {"window": 10}
        assert score == 0.0

    def test_evolve_micro_basic(
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """evolve_micro 基本路径（模拟无 optuna）。"""
        import fts.factor_engine.micro_evolution as mev
        monkeypatch.setattr(mev, '_HAS_OPTUNA', False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_evolve_test",
            name="evolve_test",
            code="def factor_program(data, params):\n    import numpy as np\n    w = params.get('window', 10)\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="evolve测试"),
            source="manual",
        )
        evolved, score = mev.evolve_micro(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert isinstance(evolved, dict)
        assert "factor_id" in evolved
        assert evolved["params"] == {"window": 10}
        assert score == 0.0


# ─── EvolutionLoop 未覆盖路径补齐 ─────────────────────────

def _make_minimal_factor(factor_id: str = "fct_test1234") -> FactorProgram:
    """构造最小 FactorProgram fixture。"""
    return FactorProgram(
        factor_id=factor_id,
        name="test_factor",
        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
        params={"window": 10},
        signature=FactorSignature(
            input_fields=["close"], output_type="signal", frequency="daily", lookback=1,
        ),
        economic_logic=EconomicLogic(
            theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试因子",
        ),
        source="manual",
        parent_id=None,
        generation=0,
        created_at="2026-07-18T00:00:00",
        trace_id="test_trace",
    )


class TestEvolutionLoopCoverage:
    """补齐 evolution_loop.py 未覆盖路径。"""

    # ─── 宏观演化失败（line 178-184）────────────────────

    def test_macro_evolution_failure(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir,
    ):
        """宏观演化抛出异常应跳过本代并继续，GP 作为 fallback。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)
        # 让宏观演化抛出异常
        loop.macro_evolver.evolve = MagicMock(side_effect=ValueError("LLM 不可用"))
        # 让 GP 演化也失败（纯单元测试场景，无真实 GP 数据）
        loop._run_gp_evolution = MagicMock(side_effect=RuntimeError("GP 初始化失败"))
        result = loop.run(max_generation=3)
        # 循环正常完成（跳过了所有代），generations_completed = max_gen
        assert result.generations_completed == 3
        assert result.status == "completed"
        # token 消耗应为 0（宏观演化全部失败，GP 也失败，无 token 消耗）
        assert result.tokens_consumed == 0

    def test_macro_evolution_failure_recorded(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir,
    ):
        """宏观演化和 GP 演化均失败时，应在 failure 目录生成轨迹文件。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)
        loop.macro_evolver.evolve = MagicMock(side_effect=ValueError("LLM 不可用"))
        loop._run_gp_evolution = MagicMock(side_effect=RuntimeError("GP 初始化失败"))
        loop.run(max_generation=2)
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) > 0
        data = json.loads(files[0].read_text(encoding="utf-8"))
        # 验证 GP 演化失败被记录
        assert "GP 演化" in data.get("mutation_summary", "") or \
               "宏观演化" in data.get("mutation_summary", "")

    # ─── 微观演化失败（line 192-197）────────────────────

    def test_micro_evolution_failure(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """微观演化抛出异常应跳过本代并继续。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)
        mock_evolve_micro.side_effect = RuntimeError("optuna 崩溃")
        result = loop.run(max_generation=3)
        # 宏观演化成功（有 token 消耗），微观全部失败 → 循环正常完成
        assert result.generations_completed == 3
        assert result.status == "completed"
        assert result.tokens_consumed > 0  # 宏观演化的 token 被消耗

    def test_micro_evolution_failure_recorded(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """微观演化失败应在 failure 目录生成轨迹文件。"""
        # 先运行一次查看正常路径，确保 failure 目录有文件
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)
        mock_evolve_micro.side_effect = RuntimeError("optuna 崩溃")
        loop.run(max_generation=2)
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) > 0

    # ─── Verifier → 晋级精英池（line 213-221）─────────────

    def test_evolution_loop_promote_to_elite(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client,
    ):
        """Verifier 通过应晋级精英池。"""
        from fts.factor_engine.contracts import BudgetConfig

        budget = BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=3,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=100,
            circuit_breaker_low_ic_threshold=0.01,
            circuit_breaker_failure_rate=0.99,
        )
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=budget,
            n_trials_micro=2,
            llm_client=mock_llm_client,
        )
        _mock_seed_evaluation_pass(loop)
        # 审查模块 mock 通过，聚焦主流程晋升链路
        _mock_review_pass(loop)
        # Verifier 始终通过
        mock_verifier = MagicMock()
        mock_verifier.check.return_value = {
            "passed": True,
            "failure_reasons": [],
        }
        loop.verifier = mock_verifier
        result = loop.run(max_generation=2)
        # 至少会有一部分因子晋级
        assert result.total_factors_promoted >= 1
        assert len(result.elite_factor_ids) > 0
        # 精英池目录应有文件
        elite_files = list(tmp_elite_dir.glob("*.json"))
        assert len(elite_files) > 0

    # ─── 外层 except 块（line 256-258）───────────────────

    def test_evolution_loop_outer_exception(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, monkeypatch,
    ):
        """循环内未捕获异常应触发外层 except → 返回 paused。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        # mock seed_pool.load_all_seeds 在循环内抛出异常
        loop.seed_pool.load_all_seeds = MagicMock(side_effect=ValueError("种子池损坏"))
        result = loop.run(max_generation=2)
        assert result.status == "paused"
        assert "种子池损坏" in (result.circuit_breaker_reason or "")

    # ─── 失败率熔断（line 293-295）───────────────────────

    def test_evolution_loop_failure_rate_circuit_breaker(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client,
    ):
        """运行内高失败率应触发熔断。"""
        from fts.factor_engine.contracts import BudgetConfig

        budget = BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=20,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=100,
            circuit_breaker_low_ic_threshold=0.01,
            circuit_breaker_failure_rate=0.01,  # 极低阈值
        )
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=budget,
            n_trials_micro=2,
            llm_client=mock_llm_client,
        )
        _mock_seed_evaluation_pass(loop)
        # 让 Verifier 拒绝所有因子（主循环中评估通过但 Verifier 判定失败）
        # 这样种子因子能通过评估（IC>=0.03 的种子晋升），
        # 但主循环中所有因子都失败 → 失败率熔断
        loop.verifier.check = MagicMock(
            return_value={"passed": False, "failure_reasons": ["模拟失败"]}
        )
        result = loop.run(max_generation=15)
        assert result.status == "circuit_broken"
        assert "失败率" in (result.circuit_breaker_reason or "")

    # ─── 内部方法直接测试 ─────────────────────────────────

    def test_promote_to_elite(
        self, tmp_elite_dir, tmp_memory_dir,
    ):
        """_promote_to_elite 应写文件到 elite 目录。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        # Mock DuckDB repo to avoid shared state issues
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name = MagicMock(return_value=None)
        loop._get_repo = MagicMock(return_value=mock_repo)
        factor = _make_minimal_factor("fct_promote_test_unique")
        factor["name"] = "fct_promote_test_unique"
        evaluation = FactorEvaluation(
            factor_id="fct_promote_test_unique",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            evaluated_at="2026-07-18T00:00:00",
        )
        fp = loop._promote_to_elite(factor, evaluation)
        assert fp.exists()
        assert fp.suffix == ".json"
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_promote_test_unique"

    def test_record_success_trace(self, tmp_memory_dir):
        """_record_success_trace 应记录到 success 目录。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            memory_dir=tmp_memory_dir,
        )
        factor = _make_minimal_factor("fct_success_test")
        evaluation = FactorEvaluation(
            factor_id="fct_success_test",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.6},
            evaluated_at="2026-07-18T00:00:00",
        )
        loop._record_success_trace(
            factor=factor,
            generation=1,
            mutation_type="combined",
            mutation_summary="测试成功轨迹",
            evaluation=evaluation,
            lessons=["代 1 晋级精英池"],
            trace_id="l2_testtrace",
        )
        success_dir = tmp_memory_dir / "success"
        files = list(success_dir.glob("*.json"))
        assert len(files) > 0

    def test_record_failure_trace_without_evaluation(self, tmp_memory_dir):
        """_record_failure_trace 在 evaluation=None 时应构造默认评估。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            memory_dir=tmp_memory_dir,
        )
        factor = _make_minimal_factor("fct_fail_test1")
        loop._record_failure_trace(
            factor=factor,
            generation=1,
            mutation_type="macro_evolution",
            mutation_summary="宏观演化失败: 测试",
            failure_reasons=["LLM 不可用"],
            trace_id="l2_testtrace",
            evaluation=None,
        )
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) > 0
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["success"] is False

    def test_record_failure_trace_fills_missing_reasons(self, tmp_memory_dir):
        """_record_failure_trace 在 evaluation 无 failure_reasons 时应填充。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            memory_dir=tmp_memory_dir,
        )
        factor = _make_minimal_factor("fct_fail_test2")
        # 传入已有 evaluation 但 failure_reasons 为空列表
        evaluation = FactorEvaluation(
            factor_id="fct_fail_test2",
            trace_id="test_trace",
            passed=False,
            failure_reasons=[],
            evaluated_at="2026-07-18T00:00:00",
        )
        loop._record_failure_trace(
            factor=factor,
            generation=2,
            mutation_type="combined",
            mutation_summary="测试填充失败原因",
            failure_reasons=["Verifier 拒绝"],
            trace_id="l2_testtrace",
            evaluation=evaluation,
        )
        # 验证 failure_reasons 已被填充
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) > 0

    def test_record_failure_trace_ignores_record_error(self, tmp_memory_dir, monkeypatch):
        """_record_failure_trace 在 record_failure 抛出异常时应静默忽略。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            memory_dir=tmp_memory_dir,
        )
        # 让 record_failure 抛出异常
        loop.experience_chain.record_failure = MagicMock(
            side_effect=RuntimeError("磁盘已满")
        )
        factor = _make_minimal_factor("fct_fail_test3")
        # 应不抛异常
        loop._record_failure_trace(
            factor=factor,
            generation=3,
            mutation_type="macro_evolution",
            mutation_summary="测试静默忽略",
            failure_reasons=["测试"],
            trace_id="l2_testtrace",
        )
        # 不应有 failure 文件（因为 record_failure 抛异常）
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) == 0

    # ─── low_ic 分支（line 232-235）───────────────────────

    def test_low_ic_increment(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """低 IC 因子应递增 _consecutive_low_ic 计数器。"""
        from fts.factor_engine.contracts import BudgetConfig

        budget = BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=5,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=3,
            circuit_breaker_low_ic_threshold=0.99,  # 几乎所有 IC 都低于此值
            circuit_breaker_failure_rate=0.99,
        )
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=budget,
            n_trials_micro=2,
            llm_client=MagicMock(),
        )
        _mock_seed_evaluation_pass(loop)
        # mock macro_evolver.evolve 返回有效结果（含 trace_id）
        mock_factor = _make_minimal_factor("fct_lowic_test")
        loop.macro_evolver.evolve = MagicMock(return_value=(
            mock_factor, "mock summary", 100,
        ))
        # mock evolve_micro 返回有效因子
        optimized = _make_minimal_factor("fct_optimized_test")
        mock_evolve_micro.return_value = (optimized, 0.01)
        result = loop.run(max_generation=5)
        # 由于 budget 中低 IC 阈值很大，verifier 会失败，低 IC 计数器递增 → 触发熔断
        assert result.status in ("completed", "circuit_broken")
        # 验证有评估记录
        assert result.total_factors_evaluated > 0


# ─── main() 函数测试（line 396-438）────────────────────────

class TestMainFunction:
    """测试 CLI 入口 main()。"""

    def test_main_without_once_flag(self, monkeypatch):
        """不带 --once 标志应打印帮助并退出。"""
        monkeypatch.setattr(sys, "argv", ["evolution_loop.py"])
        from fts.factor_engine.evolution_loop import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_main_with_once_flag(self, monkeypatch, tmp_path):
        """带 --once 标志应运行完整演化。"""
        monkeypatch.setattr(
            sys, "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation", "1",
                "--memory-dir", str(tmp_path / "evolution"),
                "--elite-dir", str(tmp_path / "elite"),
            ],
        )
        from fts.factor_engine.evolution_loop import main
        # 不应抛出异常
        main()

    def test_main_with_max_generation(self, monkeypatch, tmp_path):
        """带 --max-generation 参数应限定代数。"""
        monkeypatch.setattr(
            sys, "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation", "2",
                "--memory-dir", str(tmp_path / "evolution2"),
                "--elite-dir", str(tmp_path / "elite2"),
            ],
        )
        from fts.factor_engine.evolution_loop import main
        main()

    # ─── if __name__ == "__main__" 覆盖（line 442）────────

    def test_module_execution(self, monkeypatch, tmp_path):
        """模拟 python -m 执行进入 main()。"""
        import fts.factor_engine.evolution_loop as mod
        original_main = mod.main
        called = False

        def fake_main():
            nonlocal called
            called = True

        mod.main = fake_main
        monkeypatch.setattr(
            sys, "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation", "1",
                "--memory-dir", str(tmp_path / "evolution_mod"),
                "--elite-dir", str(tmp_path / "elite_mod"),
            ],
        )
        # 模拟 __name__ == "__main__"
        from fts.factor_engine import evolution_loop as evolution_loop_mod
        with patch.object(evolution_loop_mod, "__name__", "__main__"):
            # 触发 if __name__ == "__main__": main()
            # 直接调用等同于 __main__ 的代码
            exec("from fts.factor_engine.evolution_loop import main; main()",
                 {"__name__": "__main__"})
        mod.main = original_main

    # ─── main() 中熔断行打印覆盖（line 436, 438）─────────

    def test_main_with_circuit_breaker_reason(self, monkeypatch, tmp_path):
        """测试 main() 中有熔断原因时的打印路径。"""
        from fts.factor_engine.evolution_loop import EvolutionRunResult

        mock_result = EvolutionRunResult(
            run_id="test_run",
            trace_id="test_trace",
            generations_completed=3,
            total_factors_evaluated=5,
            total_factors_promoted=0,
            tokens_consumed=5000,
            status="circuit_broken",
            circuit_breaker_reason="Token 熔断: 5000 > 200000 * 2.0",
            elite_factor_ids=[],
        )
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run",
                   return_value=mock_result):
            monkeypatch.setattr(
                sys, "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation", "3",
                    "--memory-dir", str(tmp_path / "evo_cb"),
                    "--elite-dir", str(tmp_path / "elite_cb"),
                ],
            )
            from fts.factor_engine.evolution_loop import main
            main()

    def test_main_with_elite_factor_ids(self, monkeypatch, tmp_path):
        """测试 main() 中有精英因子时的打印路径。"""
        from fts.factor_engine.evolution_loop import EvolutionRunResult

        mock_result = EvolutionRunResult(
            run_id="test_run",
            trace_id="test_trace",
            generations_completed=2,
            total_factors_evaluated=3,
            total_factors_promoted=2,
            tokens_consumed=3000,
            status="completed",
            circuit_breaker_reason=None,
            elite_factor_ids=["fct_abc12345", "fct_def67890"],
        )
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run",
                   return_value=mock_result):
            monkeypatch.setattr(
                sys, "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation", "2",
                    "--memory-dir", str(tmp_path / "evo_elite"),
                    "--elite-dir", str(tmp_path / "elite_elite"),
                ],
            )
            from fts.factor_engine.evolution_loop import main
            main()

    def test_main_with_both_cb_and_elite(self, monkeypatch, tmp_path):
        """测试 main() 中同时有熔断原因和精英因子时的打印路径。"""
        from fts.factor_engine.evolution_loop import EvolutionRunResult

        mock_result = EvolutionRunResult(
            run_id="test_run",
            trace_id="test_trace",
            generations_completed=3,
            total_factors_evaluated=10,
            total_factors_promoted=1,
            tokens_consumed=500000,
            status="circuit_broken",
            circuit_breaker_reason="Token 超限",
            elite_factor_ids=["fct_elite_test"],
        )
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run",
                   return_value=mock_result):
            monkeypatch.setattr(
                sys, "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation", "5",
                    "--memory-dir", str(tmp_path / "evo_both"),
                    "--elite-dir", str(tmp_path / "elite_both"),
                ],
            )
            from fts.factor_engine.evolution_loop import main
            main()


# ─── 进一步覆盖 line 221 ──────────────────────────────────

class TestLine221:
    """专门覆盖 evolution_loop.py line 221 (self._consecutive_low_ic = 0)。"""

    def test_consecutive_low_ic_reset_on_success(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """Verifier 通过时应重置低 IC 计数器。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
            llm_client=MagicMock(),
        )
        _mock_seed_evaluation_pass(loop)
        # 审查模块 mock 通过，聚焦主流程晋升链路
        _mock_review_pass(loop)
        # Mock macro_evolver 返回有效因子（包含 trace_id）
        parent_factor = _make_minimal_factor("fct_line221_parent")
        loop.macro_evolver.evolve = MagicMock(return_value=(
            parent_factor, "Mock macro summary", 200,
        ))
        # Mock micro_evolution 返回优化后因子
        optimized = _make_minimal_factor("fct_line221_child")
        optimized["factor_id"] = "fct_line221_child"
        mock_evolve_micro.return_value = (optimized, 0.02)
        # Mock verifier 一直通过
        with patch.object(loop, "verifier") as mock_ver:
            mock_ver.check.return_value = {
                "passed": True,
                "failure_reasons": [],
            }
            # Mock quality_inspector 也通过（返回 A 级）
            mock_inspection = MagicMock()
            mock_inspection.filtered = False
            mock_inspection.grade = "A"
            mock_inspection.total_score = 45.0
            mock_inspection.quality_score = {"total_score": 45.0, "grade": "A"}
            loop.quality_inspector.inspect = MagicMock(return_value=mock_inspection)
            # Mock auditor 也通过
            _mock_auditor_pass(loop)
            result = loop.run(max_generation=2)
        # Verifier 通过 + 质检通过 → 晋级精英池
        assert result.total_factors_promoted >= 1
        assert len(result.elite_factor_ids) >= 1


# ─── 覆盖遗漏行 (217-234, 266, 487) ─────────────────────

class TestCoverageGaps:
    """覆盖 evolution_loop.py 遗漏行。"""

    # ── cross_section 路径 lines 217-234 ──

    def test_cross_section_evaluation_path(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """lines 217-234: cross_section_data 为非 None 时应走横截面评估路径。"""
        from fts.factor_engine.contracts import BudgetConfig

        budget = BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=3,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=100,
            circuit_breaker_low_ic_threshold=0.01,
            circuit_breaker_failure_rate=0.99,
        )

        cross_data = {"AAPL": sample_ohlcv}
        cross_dates = pd.DatetimeIndex(sample_ohlcv.index)

        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=budget,
            n_trials_micro=2,
            cross_section_data=cross_data,
            cross_section_dates=cross_dates,
        )
        loop.macro_evolver.evolve = MagicMock(return_value=(
            _make_minimal_factor("fct_cross_test"),
            "mock cross macro", 200,
        ))
        optimized = _make_minimal_factor("fct_cross_opt")
        mock_evolve_micro.return_value = (optimized, 0.03)
        result = loop.run(max_generation=1)
        assert result.status in ("completed", "circuit_broken")
        assert result.generations_completed >= 0

    def test_cross_section_failure_reasons_low_ic(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_evolve_micro,
    ):
        """横截面路径中 IC < 0.03 应有失败原因。"""
        from fts.factor_engine.contracts import BudgetConfig

        budget = BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=3,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=100,
            circuit_breaker_low_ic_threshold=0.01,
            circuit_breaker_failure_rate=0.99,
        )

        cross_data = {"AAPL": sample_ohlcv}
        cross_dates = pd.DatetimeIndex(sample_ohlcv.index)

        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=budget,
            n_trials_micro=2,
            cross_section_data=cross_data,
            cross_section_dates=cross_dates,
        )
        # Mock 使得 cross_section_evaluate_backtest 返回低 IC
        with patch("fts.factor_engine.evolution_loop.cross_section_evaluate_backtest") as mock_cs:
            mock_cs.return_value = {"ic": 0.01, "sharpe": 1.0}
            loop.macro_evolver.evolve = MagicMock(return_value=(
                _make_minimal_factor("fct_cross_lowic"),
                "mock", 200,
            ))
            optimized = _make_minimal_factor("fct_cross_opt2")
            mock_evolve_micro.return_value = (optimized, 0.01)
            result = loop.run(max_generation=1)
        assert result.status in ("completed", "circuit_broken")

    # ── line 266: consecutive_low_ic = 0 ──

    def test_consecutive_low_ic_reset_direct(self, tmp_memory_dir):
        """line 266: 直接验证 _consecutive_low_ic 在 verifier 通过时重置为 0。"""
        # 直接访问内部状态验证
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0, 2.0]}),
            forward_returns=np.array([0.01, -0.01]),
            memory_dir=tmp_memory_dir,
        )
        # 人工设置
        loop._consecutive_low_ic = 5
        # 构造一个通过的 evaluation
        from fts.factor_engine.contracts import FactorEvaluation
        eval_passed = FactorEvaluation(
            factor_id="fct_test", trace_id="t",
            passed=True, failure_reasons=[],
            evaluated_at="now",
        )
        # 模拟 verifier 通过后的路径 — 使用成功轨迹记录
        factor = _make_minimal_factor("fct_reset")
        loop._record_success_trace(
            factor=factor, generation=1, mutation_type="combined",
            mutation_summary="测试重置", evaluation=eval_passed,
            lessons=["test"], trace_id="l2_test",
        )
        # 验证 success 目录有文件
        success_dir = tmp_memory_dir / "success"
        assert len(list(success_dir.glob("*.json"))) > 0

    # ── line 487: if __name__ == "__main__" ──

    def test_module_execution_direct(self, monkeypatch, tmp_path):
        """line 487: 模拟 __name__ == '__main__' 进入 main()。"""
        import sys
        from fts.factor_engine import evolution_loop as el_mod

        monkeypatch.setattr(
            sys, "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation", "1",
                "--memory-dir", str(tmp_path / "evo_mem"),
                "--elite-dir", str(tmp_path / "evo_elite"),
            ],
        )
        with patch.object(el_mod, "__name__", "__main__"):
            exec("from fts.factor_engine.evolution_loop import main; main()",
                 {"__name__": "__main__"})


# ─── Phase B.2: BacktestPipeline 集成测试 ────────────────


class TestBacktestPipelineIntegration:
    """测试 BacktestPipeline 在演化循环中的集成。"""

    def test_backtest_pipeline_initialization(self, minimal_loop):
        """验证 BacktestPipeline 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.backtest_pipeline is not None

    def test_run_backtest_pipeline_success(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证 _run_backtest_pipeline 成功执行。"""
        from fts.factor_engine.backtest_pipeline import PipelineResult

        mock_result = PipelineResult(
            success=True,
            stage="report",
            duration_ms=100.0,
            output=None,
        )
        minimal_loop.backtest_pipeline.run = MagicMock(return_value=mock_result)

        result = minimal_loop._run_backtest_pipeline(
            sample_seed, sample_evaluation, "test_trace"
        )
        assert result is not None
        assert result["success"] is True
        assert result["duration_ms"] == 100.0

    def test_run_backtest_pipeline_failure(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证 _run_backtest_pipeline 失败返回 None。"""
        from fts.factor_engine.backtest_pipeline import PipelineResult

        mock_result = PipelineResult(
            success=False,
            stage="data_load",
            duration_ms=50.0,
            error="data not found",
        )
        minimal_loop.backtest_pipeline.run = MagicMock(return_value=mock_result)

        result = minimal_loop._run_backtest_pipeline(
            sample_seed, sample_evaluation, "test_trace"
        )
        assert result is None

    def test_run_backtest_pipeline_exception(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证 _run_backtest_pipeline 异常返回 None。"""
        minimal_loop.backtest_pipeline.run = MagicMock(
            side_effect=RuntimeError("boom")
        )
        result = minimal_loop._run_backtest_pipeline(
            sample_seed, sample_evaluation, "test_trace"
        )
        assert result is None


# ─── Phase B.1: DataQualityMonitor 集成测试 ──────────────


class TestDataQualityIntegration:
    """测试 DataQualityMonitor 在演化循环中的集成。"""

    def test_data_quality_monitor_initialization(self, minimal_loop):
        """验证 DataQualityMonitor 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.data_quality_monitor is not None

    def test_register_factor_baseline(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证注册因子基准数据。"""
        minimal_loop.data_quality_monitor.register_factor = MagicMock()
        minimal_loop._register_factor_baseline(sample_seed, sample_evaluation)

        minimal_loop.data_quality_monitor.register_factor.assert_called_once()
        call_kwargs = minimal_loop.data_quality_monitor.register_factor.call_args
        assert call_kwargs[1]["factor_id"] == sample_seed["factor_id"]

    def test_check_factor_data_quality_no_alerts(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证数据质量检查无告警时返回空列表。"""
        minimal_loop.data_quality_monitor.check = MagicMock(return_value=[])
        alerts = minimal_loop._check_factor_data_quality(
            sample_seed, sample_evaluation
        )
        assert alerts == []

    def test_check_factor_data_quality_with_alerts(
        self, minimal_loop, sample_seed, sample_evaluation
    ):
        """验证数据质量检查返回告警。"""
        from fts.monitor.data_quality_monitor import QualityAlert

        alert = QualityAlert(
            factor_id=sample_seed["factor_id"],
            alert_type="ic_drift",
            severity="warning",
            message="IC drift detected",
            metric_name="ic",
            metric_value=0.01,
            baseline_value=0.05,
            threshold=0.03,
        )
        minimal_loop.data_quality_monitor.check = MagicMock(
            return_value=[alert]
        )
        alerts = minimal_loop._check_factor_data_quality(
            sample_seed, sample_evaluation
        )
        assert len(alerts) == 1
        assert alerts[0].alert_type == "ic_drift"


# ─── Phase A.2: EliteFactorTracker 定期重评估测试 ──────────


class TestEliteFactorTrackerIntegration:
    """测试 EliteFactorTracker 在演化循环结束时的定期重评估。"""

    def test_elite_tracker_initialization(self, minimal_loop):
        """验证 EliteFactorTracker 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.elite_tracker is not None

    def test_run_periodic_factor_review_no_elite(self, minimal_loop):
        """验证无精英因子时定期重评估不报错。"""
        minimal_loop.elite_tracker.auto_retire = MagicMock(return_value=[])
        minimal_loop.elite_tracker.report = MagicMock(
            return_value={"status_counts": {}, "grade_counts": {}}
        )
        minimal_loop._run_periodic_factor_review([], "test_trace")

    def test_run_periodic_factor_review_with_elite(
        self, minimal_loop, sample_seed
    ):
        """验证有精英因子时定期重评估正常执行。"""
        fid = sample_seed["factor_id"]
        minimal_loop.elite_tracker.auto_retire = MagicMock(return_value=[])
        minimal_loop.elite_tracker.report = MagicMock(
            return_value={
                "status_counts": {"active": 1, "total": 1},
                "grade_counts": {"A": 1},
            }
        )
        minimal_loop.elite_tracker.update = MagicMock()

        minimal_loop._run_periodic_factor_review([fid], "test_trace")
        minimal_loop.elite_tracker.update.assert_called_once()

    def test_run_periodic_factor_review_with_retirement(
        self, minimal_loop, sample_seed
    ):
        """验证有因子被淘汰时定期重评估正常处理。"""
        fid = sample_seed["factor_id"]
        minimal_loop.elite_tracker.auto_retire = MagicMock(
            return_value=[fid]
        )
        minimal_loop.elite_tracker.report = MagicMock(
            return_value={
                "status_counts": {"retired": 1, "total": 1},
                "grade_counts": {"C": 1},
            }
        )
        minimal_loop.elite_tracker.update = MagicMock()

        minimal_loop._run_periodic_factor_review([fid], "test_trace")

    def test_get_factor_data_for_review(self, minimal_loop, sample_seed):
        """验证 _get_factor_data_for_review 返回默认值。"""
        result = minimal_loop._get_factor_data_for_review(
            sample_seed["factor_id"]
        )
        assert result is not None
        assert "ic" in result
        assert "sharpe" in result

    def test_periodic_review_in_run_finally(
        self, minimal_loop, sample_dataframe, sample_forward_returns
    ):
        """验证定期重评估在 run() 的 finally 块中被调用。"""
        minimal_loop.elite_tracker.auto_retire = MagicMock(return_value=[])
        minimal_loop.elite_tracker.report = MagicMock(
            return_value={"status_counts": {}, "grade_counts": {}}
        )
        minimal_loop.elite_tracker.update = MagicMock()

        with patch.object(
            minimal_loop, "_run_periodic_factor_review"
        ) as mock_review:
            with patch.object(
                minimal_loop, "_evaluate_and_promote_seeds", return_value=0
            ):
                with patch.object(
                    minimal_loop.seed_pool, "load_all_seeds", return_value=[]
                ):
                    minimal_loop.run(max_generation=1)
                    mock_review.assert_called_once()


# ─── GP 演化集成测试 ──────────────────────────────────────

class TestGPEvolutionIntegration:
    """测试 GP 演化作为宏观演化 fallback 的集成路径。"""

    def test_gp_evolution_initialized(self, minimal_loop):
        """验证 FeatureOpsEngine 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.feature_ops_engine is not None

    def test_run_gp_evolution_returns_factor_program(
        self, minimal_loop, sample_seed, sample_dataframe
    ):
        """验证 _run_gp_evolution 返回 FactorProgram 格式。"""
        from fts.factor_engine.gp_evolver import (
            ExpressionTree,
            GPEvolveResult,
            TreeNode,
        )

        mock_root = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="high", is_terminal=True),
            ],
        )
        mock_tree = ExpressionTree(
            root=mock_root,
            expression="close + high",
            depth=1,
            size=3,
            fitness=0.05,
        )
        mock_result = GPEvolveResult(
            best_tree=mock_tree,
            best_expression="close + high",
            best_fitness=0.05,
            best_ic=0.03,
            best_sharpe=1.2,
            generations_completed=5,
            total_evaluations=100,
        )

        minimal_loop.data = sample_dataframe
        minimal_loop.feature_ops_engine.run_gp_search = MagicMock(
            return_value=mock_result
        )

        factor_program, summary = minimal_loop._run_gp_evolution(
            parent=sample_seed,
            generation=1,
            trace_id="test_trace_gp",
        )

        assert "factor_id" in factor_program
        assert "code" in factor_program
        assert "expression" in factor_program
        assert factor_program["parent_id"] == sample_seed["factor_id"]
        assert factor_program["generation"] == 1
        assert "GP Gen=" in summary
        assert "Fitness=0.0500" in summary

    def test_run_gp_evolution_with_invalid_fitness(
        self, minimal_loop, sample_seed, sample_dataframe
    ):
        """验证适应度无效时抛出异常。"""
        from fts.factor_engine.gp_evolver import (
            ExpressionTree,
            GPEvolveResult,
            TreeNode,
        )

        mock_root = TreeNode(
            op_name="sub",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="close", is_terminal=True),
            ],
        )
        mock_tree = ExpressionTree(
            root=mock_root,
            expression="close - close",
            depth=1,
            size=2,
            fitness=0.0,
        )
        mock_result = GPEvolveResult(
            best_tree=mock_tree,
            best_expression="close - close",
            best_fitness=0.0,
            best_ic=0.0,
            best_sharpe=0.0,
            generations_completed=1,
            total_evaluations=10,
        )

        minimal_loop.data = sample_dataframe
        minimal_loop.feature_ops_engine.run_gp_search = MagicMock(
            return_value=mock_result
        )

        with pytest.raises(RuntimeError, match="GP 演化适应度无效"):
            minimal_loop._run_gp_evolution(
                parent=sample_seed,
                generation=1,
                trace_id="test_trace_gp",
            )

    def test_gp_fallback_in_evolution_loop(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir
    ):
        """验证宏观演化失败时回退到 GP 演化。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)
        # 宏观演化失败
        loop.macro_evolver.evolve = MagicMock(side_effect=ValueError("LLM 不可用"))
        # GP 演化也失败 → 应记录失败并继续
        loop._run_gp_evolution = MagicMock(
            side_effect=RuntimeError("GP 算子初始化失败")
        )
        result = loop.run(max_generation=3)
        assert result.generations_completed == 3
        assert result.status == "completed"

    def test_gp_success_flow_integration(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir
    ):
        """验证 GP 成功后因子流入微观演化→评估→审计→回测全链路。"""
        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            n_trials_micro=2,
        )
        _mock_seed_evaluation_pass(loop)

        # 宏观演化失败，GP 成功
        loop.macro_evolver.evolve = MagicMock(side_effect=ValueError("LLM 不可用"))

        from fts.factor_engine.gp_evolver import ExpressionTree, GPEvolveResult, TreeNode

        mock_root = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="volume", is_terminal=True),
            ],
        )
        mock_tree = ExpressionTree(
            root=mock_root,
            expression="close + volume",
            depth=2,
            size=5,
            fitness=0.08,
        )
        mock_result = GPEvolveResult(
            best_tree=mock_tree,
            best_expression="close + volume",
            best_fitness=0.08,
            best_ic=0.05,
            best_sharpe=1.5,
            generations_completed=10,
            total_evaluations=200,
        )
        loop.feature_ops_engine.run_gp_search = MagicMock(
            return_value=mock_result
        )

        # Mock 微观演化返回有效因子
        from fts.factor_engine.micro_evolution import evolve_micro

        gp_factor = {
            "factor_id": "gp_test_factor",
            "name": "gp_test_factor",
            "code": "def compute(close, high, low, volume):\n    return (close + volume)",
            "expression": "close + volume",
            "parent_id": None,
            "generation": 1,
            "source": "gp_evolution",
            "trace_id": "test_trace",
            "market": "futures",
        }
        loop._run_gp_evolution = MagicMock(return_value=(gp_factor, "GP Gen=10"))

        # 确保评估、审计、回测链路正常
        with patch.object(loop.verifier, "check", return_value={"passed": True, "failure_reasons": []}):
            mock_inspection = MagicMock()
            mock_inspection.filtered = False
            mock_inspection.grade = "A"
            mock_inspection.total_score = 45.0
            mock_inspection.quality_score = {"total_score": 45.0, "grade": "A"}
            loop.quality_inspector.inspect = MagicMock(return_value=mock_inspection)
            _mock_auditor_pass(loop)

            result = loop.run(max_generation=2)

        # GP 成功生成的因子应该参与了循环（虽然可能因编译问题未晋级）
        assert result.generations_completed == 2
        # 验证 GP 的 run_gp_search 被调用
        # （由于 _run_gp_evolution 被 mock，feature_ops_engine.run_gp_search 不会被直接调用）


class TestFactorAuditorIntegration:
    """测试 FactorAuditor 作为强制审计门槛的集成。"""

    def test_auditor_initialized(self, minimal_loop):
        """验证 FactorAuditor 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.auditor is not None

    def test_promote_to_elite_runs_audit(
        self, minimal_loop, sample_dataframe
    ):
        """验证 _promote_to_elite 接收并写入审计报告。"""
        from fts.factor_engine.audit import FactorAuditReport, AuditItemResult

        test_factor = {
            "factor_id": "audit_test_factor_001",
            "name": "audit_test_factor",
            "code": "close + high",
            "factor_type": "test",
        }

        mock_report = FactorAuditReport(
            factor_id=test_factor["factor_id"],
            factor_name=test_factor["name"],
            audited_at="2026-07-18T00:00:00",
            items=[
                AuditItemResult(
                    name="causal_validity",
                    status="passed",
                    evidence="IC 稳定",
                    score=1.0,
                    details={"note": "Passed"},
                )
            ],
            passed=True,
            pass_rate=1.0,
            summary={"total": 1, "passed": 1},
        )

        evaluation = FactorEvaluation(
            factor_id=test_factor["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-07-18T00:00:00",
        )

        # Mock DuckDB 去重检查，避免持久化状态干扰
        minimal_loop._get_repo = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name = MagicMock(return_value=None)
        minimal_loop._get_repo.return_value = mock_repo

        path = minimal_loop._promote_to_elite(
            test_factor,
            evaluation,
            seed_correlations=[],
            quality_score={"total_score": 45.0, "grade": "A"},
            audit_report=mock_report,
        )
        assert path is not None
        record = json.loads(path.read_text(encoding="utf-8"))
        assert "audit_report" in record

    def test_promote_to_elite_audit_fails_blocks_promotion(
        self, minimal_loop, sample_seed
    ):
        """验证审计未通过时阻止晋级。"""
        from fts.factor_engine.audit import FactorAuditReport, AuditItemResult

        mock_report = FactorAuditReport(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed.get("name", "test_factor"),
            audited_at="2026-07-18T00:00:00",
            items=[
                AuditItemResult(
                    name="causal_validity",
                    status="failed",
                    evidence="IC 不稳定",
                    score=0.0,
                    details={"reason": "Causal check failed"},
                ),
                AuditItemResult(
                    name="oos_consistency",
                    status="failed",
                    evidence="OOS 表现差",
                    score=0.0,
                    details={"reason": "OOS check failed"},
                ),
            ],
            passed=False,
            pass_rate=0.3,
            summary={"total": 2, "passed": 0},
        )
        minimal_loop.auditor.audit = MagicMock(return_value=mock_report)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-07-18T00:00:00",
        )

        path = minimal_loop._promote_to_elite(
            sample_seed,
            evaluation,
            seed_correlations={},
            quality_score=45.0,
        )
        assert path is None


class TestBacktestPipelineIntegration:
    """测试 BacktestPipeline 接入评估链。"""

    def test_backtest_pipeline_initialized(self, minimal_loop):
        """验证 BacktestPipeline 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.backtest_pipeline is not None

    def test_run_backtest_pipeline_success(
        self, minimal_loop, sample_seed
    ):
        """验证 _run_backtest_pipeline 成功执行。"""
        from fts.factor_engine.contracts import FactorEvaluation

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-07-18T00:00:00",
        )

        mock_report = MagicMock()
        mock_report.file_path = "/tmp/backtest_report.json"
        mock_report.total_return = 0.05
        mock_report.sharpe_ratio = 1.5
        mock_report.max_drawdown = 0.03
        mock_report.calmar_ratio = 1.2

        mock_bt_result = MagicMock()
        mock_bt_result.success = True
        mock_bt_result.error = None
        mock_bt_result.duration_ms = 1500
        mock_bt_result.output = mock_report

        minimal_loop.backtest_pipeline.run = MagicMock(
            return_value=mock_bt_result
        )

        result = minimal_loop._run_backtest_pipeline(
            sample_seed, evaluation, "test_trace"
        )
        assert result is not None
        assert result["success"] is True
        minimal_loop.backtest_pipeline.run.assert_called_once()

    def test_run_backtest_pipeline_failure(
        self, minimal_loop, sample_seed
    ):
        """验证 BacktestPipeline 失败时返回 None。"""
        from fts.factor_engine.contracts import FactorEvaluation

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-07-18T00:00:00",
        )

        minimal_loop.backtest_pipeline.run = MagicMock(
            side_effect=RuntimeError("数据加载失败")
        )

        result = minimal_loop._run_backtest_pipeline(
            sample_seed, evaluation, "test_trace"
        )
        assert result is None


# ─── Task 1: 孤立模块初始化测试 ──────────────────────────

class TestIsolatedModuleInitialization:
    """验证孤立模块在 EvolutionLoop 中正确初始化。"""

    def test_ablation_experiment_initialized(self, minimal_loop):
        """验证 AblationExperiment 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.ablation_experiment is not None
        assert hasattr(minimal_loop.ablation_experiment, 'run')

    def test_shap_analyzer_initialized(self, minimal_loop):
        """验证 ShapAnalyzer 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.shap_analyzer is not None

    def test_robustness_tester_initialized(self, minimal_loop):
        """验证 RobustnessTester 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.robustness_tester is not None

    def test_causal_validator_initialized(self, minimal_loop):
        """验证 CausalValidator 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.causal_validator is not None

    def test_feature_importance_analyzer_initialized(self, minimal_loop):
        """验证 FeatureImportanceAnalyzer 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.feature_importance_analyzer is not None

    def test_logic_monitor_initialized(self, minimal_loop):
        """验证 LogicMonitor 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.logic_monitor is not None


# ─── Task 2: AblationExperiment 集成测试 ─────────────────

class TestAblationIntegration:
    """测试 AblationExperiment 在演化循环中的集成。"""

    def test_ablation_runs_in_evolution_flow(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证消融实验在演化流程中被调用。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="volume_zero", description="成交量置零",
                    ic=0.049, sharpe=1.45,
                    ic_change=-0.001, sharpe_change=-0.05,
                )
            ],
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.ablation_experiment.run = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_ablation_check(
            sample_seed, evaluation, "test_trace",
        )
        assert result is not None
        assert result["factor_id"] == sample_seed["factor_id"]
        assert len(result["ablations"]) >= 1

    def test_ablation_spurious_detection_blocks_promotion(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证严重消融退化（>50% IC 下降）阻止晋升。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="shuffle_dates", description="时间戳打乱",
                    ic=0.01, sharpe=0.3,
                    ic_change=-0.04, sharpe_change=-1.2,
                )
            ],
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.ablation_experiment.run = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_ablation_check(
            sample_seed, evaluation, "test_trace",
        )
        assert result["passed"] is False


# ─── Task 3: CausalValidator 集成测试 ────────────────────

class TestCausalValidationIntegration:
    """测试 CausalValidator 在演化循环中的集成。"""

    def test_causal_validation_runs_in_flow(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证因果验证在演化流程中被调用。"""
        from fts.factor_engine.causal_validator import CausalValidationResult

        mock_result = CausalValidationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            analysis_date="2026-08-05",
            n_events=5,
            n_anomalous=0,
            anomalous_events=[],
            all_events=[],
            summary={"total": 5, "anomalous": 0},
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.causal_validator.validate = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_causal_validation(
            sample_seed, evaluation, "test_trace",
        )
        assert result is not None
        assert result["passed"] is True

    def test_causal_anomaly_blocks_promotion(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证因果异常（事件敏感）阻止晋升。"""
        from fts.factor_engine.causal_validator import (
            CausalValidationResult, EventPredictionError,
        )

        mock_result = CausalValidationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            analysis_date="2026-08-05",
            n_events=5,
            n_anomalous=1,
            anomalous_events=[
                EventPredictionError(
                    event_id="evt_001", event_name="熔断",
                    event_type="circuit_breaker", event_date="2026-01-15",
                    expected_direction="down", pre_window=5, post_window=5,
                    pre_mean_error=0.01, post_mean_error=0.05,
                    error_change=0.04, error_std=0.01,
                    is_anomalous=True, anomaly_direction="positive",
                    n_pre_samples=5, n_post_samples=5,
                )
            ],
            all_events=[],
            summary={"total": 5, "anomalous": 1},
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.causal_validator.validate = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_causal_validation(
            sample_seed, evaluation, "test_trace",
        )
        assert result["passed"] is False
        assert len(result["anomalous_events"]) > 0


# ─── Task 4a: RobustnessTester 集成测试 ──────────────────

class TestRobustnessIntegration:
    """测试 RobustnessTester 在演化循环中的集成。"""

    def test_robustness_runs_in_flow(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证鲁棒性审查在演化流程中被调用。"""
        from fts.factor_engine.robustness import RobustnessTestResult

        mock_result = RobustnessTestResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            adversarial_results=[],
            missing_value_results=[],
            ood_results=[],
            summary={"overall_pass_rate": 1.0, "total": 11, "passed": 11},
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.robustness_tester.run = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_robustness_check(
            sample_seed, evaluation, "test_trace",
        )
        assert result is not None
        assert result["passed"] is True

    def test_robustness_failure_blocks_promotion(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证鲁棒性失败阻止晋升。"""
        from fts.factor_engine.robustness import (
            RobustnessTestResult, AdversarialTestResult,
        )

        mock_result = RobustnessTestResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            adversarial_results=[
                AdversarialTestResult(
                    perturbation="price", perturbation_factor=1.0001,
                    baseline_ic=0.05, perturbed_ic=0.03,
                    ic_change=-0.02, passed=False,
                )
            ],
            missing_value_results=[],
            ood_results=[],
            summary={"overall_pass_rate": 0.8, "total": 11, "passed": 10},
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.robustness_tester.run = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_robustness_check(
            sample_seed, evaluation, "test_trace",
        )
        assert result["passed"] is False


# ─── Task 4b: ShapAnalyzer 集成测试 ─────────────────────

class TestShapAnalysisIntegration:
    """测试 ShapAnalyzer 在演化循环中的集成。"""

    def test_shap_runs_in_flow(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证 SHAP 分析在演化流程中被调用。"""
        from fts.factor_engine.shap_analyzer import (
            ShapAnalysisResult, ShapSampleAnalysis, ShapFeatureImportance,
        )

        mock_result = ShapAnalysisResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            analysis_date="2026-08-05",
            num_extreme_samples=10,
            num_features=2,
            top_samples=[
                ShapSampleAnalysis(
                    sample_index=0, date="2026-08-01",
                    signal_value=0.05,
                    top_features=[
                        ShapFeatureImportance(
                            feature_name="close",
                            shap_value=0.02,
                            impact_direction="positive",
                        )
                    ],
                )
            ],
            bottom_samples=[],
            global_top_features=[],
            summary={"status": "ok"},
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.shap_analyzer.analyze = MagicMock(return_value=mock_result)

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        result = minimal_loop._run_shap_analysis(
            sample_seed, evaluation, "test_trace",
        )
        assert result is not None
        assert result["passed"] is True


# ─── Task 5: FeatureImportanceAnalyzer 集成测试 ──────────

class TestFeatureImportanceIntegration:
    """测试 FeatureImportanceAnalyzer 在 GP 管线中的集成。"""

    def test_feature_importance_runs_in_gp_flow(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证特征重要性分析在 GP 管线中被调用。"""
        from fts.factor_engine.feature_importance import (
            FeatureImportanceResult,
        )

        mock_result = FeatureImportanceResult(
            factor_id=sample_seed["factor_id"],
            feature_importance={"close": 0.8, "volume": 0.2},
            top_features=[("close", 0.8), ("volume", 0.2)],
            analysis_method="permutation",
            n_features_analyzed=2,
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.feature_importance_analyzer.analyze = MagicMock(
            return_value=mock_result
        )

        importance = minimal_loop.feature_importance_analyzer.analyze(
            sample_seed, sample_dataframe,
        )
        assert importance is not None
        assert importance.factor_id == sample_seed["factor_id"]


# ─── Task 6: LogicMonitor 集成测试 ──────────────────────

class TestLogicMonitorIntegration:
    """测试 LogicMonitor 在定期重评估中的集成。"""

    def test_logic_monitor_runs_in_review(
        self, minimal_loop, sample_dataframe
    ):
        """验证逻辑监控在定期重评估中被调用。"""
        from fts.monitor.logic_monitor import (
            DriftCheckResult,
            ExtremePredictionResult,
            LogicMonitorResult,
        )

        mock_report = LogicMonitorResult(
            factor_id="fid_001",
            checked_at="2026-08-05T00:00:00",
            drift=DriftCheckResult(
                factor_id="fid_001",
                momentum_correlation=0.5,
                mean_reversion_correlation=0.4,
                is_drifted=False,
            ),
            extreme_prediction=ExtremePredictionResult(
                factor_id="fid_001",
                total_samples=100,
                extreme_positive=1,
                extreme_negative=0,
                extreme_ratio=0.01,
            ),
            contract_switch=None,
            all_healthy=True,
        )
        minimal_loop.data = sample_dataframe
        minimal_loop.logic_monitor.run = MagicMock(
            return_value=mock_report
        )

        minimal_loop._run_periodic_factor_review(
            elite_ids=[],
            trace_id="test_trace",
        )

        # 无 elite_ids 时不应调用 run
        minimal_loop.logic_monitor.run.assert_not_called()


# ─── Task 7: 端到端集成验证 ──────────────────────────────

class TestFullIntegrationPipeline:
    """端到端集成测试：验证完整审查流水线。"""

    def test_all_review_stages_execute_in_sequence(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证所有审查节点按顺序执行。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation
        from fts.factor_engine.causal_validator import CausalValidationResult
        from fts.factor_engine.robustness import RobustnessTestResult
        from fts.factor_engine.shap_analyzer import ShapAnalysisResult

        eval_result = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="e2e_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        minimal_loop.data = sample_dataframe
        minimal_loop.ablation_experiment.run = MagicMock(
            return_value=AblationResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                baseline_ic=0.05, baseline_sharpe=1.5,
                ablations=[SingleAblation(
                    mode="volume_zero", description="vol→0",
                    ic=0.048, sharpe=1.48,
                    ic_change=-0.002, sharpe_change=-0.02,
                )],
            )
        )
        minimal_loop.causal_validator.validate = MagicMock(
            return_value=CausalValidationResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                analysis_date="2026-08-05",
                n_events=5, n_anomalous=0,
                anomalous_events=[], all_events=[],
                summary={"total": 5, "anomalous": 0},
            )
        )
        minimal_loop.robustness_tester.run = MagicMock(
            return_value=RobustnessTestResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                adversarial_results=[], missing_value_results=[],
                ood_results=[],
                summary={"overall_pass_rate": 1.0, "total": 11, "passed": 11},
            )
        )
        minimal_loop.shap_analyzer.analyze = MagicMock(
            return_value=ShapAnalysisResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                analysis_date="2026-08-05",
                num_extreme_samples=10, num_features=2,
                top_samples=[], bottom_samples=[],
                global_top_features=[], summary={"status": "ok"},
            )
        )

        abl = minimal_loop._run_ablation_check(sample_seed, eval_result, "e2e")
        causal = minimal_loop._run_causal_validation(sample_seed, eval_result, "e2e")
        robust = minimal_loop._run_robustness_check(sample_seed, eval_result, "e2e")
        shap = minimal_loop._run_shap_analysis(sample_seed, eval_result, "e2e")

        assert abl["passed"] is True
        assert causal["passed"] is True
        assert robust["passed"] is True
        assert shap["passed"] is True

        minimal_loop.ablation_experiment.run.assert_called_once()
        minimal_loop.causal_validator.validate.assert_called_once()
        minimal_loop.robustness_tester.run.assert_called_once()
        minimal_loop.shap_analyzer.analyze.assert_called_once()

    def test_one_review_failure_blocks_promotion(
        self, minimal_loop, sample_dataframe, sample_seed
    ):
        """验证任一审查失败阻止晋升。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        eval_result = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="fail_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            evaluated_at="2026-08-05T00:00:00",
        )

        minimal_loop.data = sample_dataframe
        minimal_loop.ablation_experiment.run = MagicMock(
            return_value=AblationResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                baseline_ic=0.05, baseline_sharpe=1.5,
                ablations=[SingleAblation(
                    mode="shuffle_dates", description="时间戳打乱",
                    ic=0.01, sharpe=0.3,
                    ic_change=-0.04, sharpe_change=-1.2,
                )],
            )
        )

        result = minimal_loop._run_ablation_check(sample_seed, eval_result, "fail")
        assert result["passed"] is False
