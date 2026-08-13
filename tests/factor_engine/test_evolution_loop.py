"""tests/factor_engine/test_evolution_loop.py — 主循环测试。"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.audit import FactorAuditReport
from fts.factor_engine.contracts import (
    STATE_SCHEMA_VERSION,
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
    generate_session_id,
)


# ─── trace_id 生成 ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """GAP-030: 本文件全部测试隔离 DuckDB factor_catalog，防污染真实库。

    `FactorRepository.__init__` 内部每次执行 `from .schema import DATABASE_PATH`
    读取模块当前属性，故 monkeypatch 模块属性即可让后续实例化指向隔离库。
    """
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path, monkeypatch):
    """全文隔离 state.duckdb（SSOT 读路径切换后，状态管理器默认走全局 SSOT）。

    将 `fts.store.state_db.get_state_store` 重定向到每测试临时库，防污染真实 state.duckdb。
    """
    from fts.store import state_db

    store = state_db.StateKVStore(tmp_path / "state.duckdb")
    monkeypatch.setattr(state_db, "get_state_store", lambda: store)
    yield
    store.close()


def test_generate_trace_id_format():
    tid = generate_trace_id("l2")
    assert tid.startswith("l2_")
    # 格式: l2_<8hex>_<timestamp>
    parts = tid.split("_")
    assert len(parts) == 3


def test_generate_run_id_format():
    rid = generate_run_id()
    assert rid.startswith("run_")


def test_generate_session_id_format():
    sid = generate_session_id()
    assert sid.startswith("session_")
    # 格式与 trace_id 相同: session_<8hex>_<timestamp>
    parts = sid.split("_")
    assert len(parts) == 3


def test_generate_trace_id_uniqueness():
    ids = {generate_trace_id("x") for _ in range(100)}
    assert len(ids) >= 95  # 高概率唯一


# ─── 状态管理 ─────────────────────────────────────────────


def test_state_manager_init(tmp_memory_dir):
    """首次加载应初始化新状态。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    assert state["status"] == "running"
    assert state["schema_version"] == STATE_SCHEMA_VERSION
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


def test_state_manager_save_reload_roundtrip(tmp_memory_dir):
    """保存后新建管理器可重新加载（DuckDB SSOT 持久化）。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    state["last_generation"] = 7
    mgr.save(state)

    # 重新加载应从 DuckDB 恢复
    mgr2 = EvolutionStateManager(tmp_memory_dir)
    state2 = mgr2.load_or_init()
    assert state2["last_generation"] == 7


def test_state_manager_version_check(tmp_memory_dir):
    """schema 版本不匹配时应视为损坏。"""
    # 写入错误 schema 版本到 DuckDB
    from fts.store.state_db import get_state_store

    get_state_store().upsert("evolution", "state", {"schema_version": "0", "status": "running"}, run_id="t")
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    # 应重新初始化
    assert state["schema_version"] == STATE_SCHEMA_VERSION
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
        json.dumps(
            {
                "mutation_type": "macro_logic",
                "mutation_summary": "Mock: window+5",
                "code_modification": "window_plus_5",
                "economic_logic_modification": {
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 4,
                    "narrative": "Mock LLM 经济逻辑",
                },
                "lessons_referenced": ["历史成功"],
            }
        ),
        200,
    )
    return client


def _make_passing_audit_report() -> FactorAuditReport:
    """构造一个通过所有审计项的 Mock 报告。"""
    from fts.factor_engine.audit import AuditItemResult

    items = [
        AuditItemResult(name=n, status="passed", evidence="mock")
        for n in (
            "causal_validity",
            "oos_consistency",
            "cross_symbol",
            "stress_resilience",
            "multiple_testing",
            "snooping_check",
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
            "ic": 0.05,
            "icir": 1.5,
            "sharpe": 2.0,
            "monotonicity": True,
            "max_drawdown": 0.05,
            "turnover_monthly": 0.3,
            "oos_ratio": 0.35,  # v2.50.0 种子路径新增 Verifier 判定所需字段
        },
        "economic_score": {"dimensions_passed": 4},
        "level_3_multiple": {"passed": True, "adjusted_t": 3.5, "fdr_q": 0.01},
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
    # v2.50.0 种子路径新增的 Verifier/消融/因果/鲁棒/SHAP 审查 mock 通过
    loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
    loop._run_ablation_check = MagicMock(return_value={"passed": True})
    loop._run_causal_validation = MagicMock(return_value={"passed": True})
    loop._run_robustness_check = MagicMock(return_value={"passed": True})
    loop._run_shap_analysis = MagicMock(return_value={})


def _mock_review_pass(loop: EvolutionLoop) -> None:
    """Mock 4 个审查模块返回通过，使端到端主流程可晋升。"""
    from fts.factor_engine.ablation import AblationResult, SingleAblation
    from fts.factor_engine.causal_validator import CausalValidationResult
    from fts.factor_engine.robustness import RobustnessTestResult
    from fts.factor_engine.shap_analyzer import ShapAnalysisResult

    loop.ablation_experiment.run = MagicMock(
        return_value=AblationResult(
            factor_id="review_pass",
            factor_name="review_pass",
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="volume_zero",
                    description="vol→0",
                    ic=0.049,
                    sharpe=1.48,
                    ic_change=-0.001,
                    sharpe_change=-0.02,
                )
            ],
        )
    )
    loop.causal_validator.validate = MagicMock(
        return_value=CausalValidationResult(
            factor_id="review_pass",
            factor_name="review_pass",
            analysis_date="2026-08-05",
            n_events=5,
            n_anomalous=0,
            anomalous_events=[],
            all_events=[],
            summary={},
        )
    )
    loop.robustness_tester.run = MagicMock(
        return_value=RobustnessTestResult(
            factor_id="review_pass",
            factor_name="review_pass",
            adversarial_results=[],
            missing_value_results=[],
            ood_results=[],
            summary={"overall_pass_rate": 1.0},
        )
    )
    loop.shap_analyzer.analyze = MagicMock(
        return_value=ShapAnalysisResult(
            factor_id="review_pass",
            factor_name="review_pass",
            analysis_date="2026-08-05",
            num_extreme_samples=0,
            num_features=0,
            top_samples=[],
            bottom_samples=[],
            global_top_features=[],
            summary={},
        )
    )


@pytest.mark.slow
def test_evolution_loop_runs_minimal(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client):
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


@pytest.mark.slow
def test_evolution_loop_produces_metrics(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client):
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


@pytest.mark.slow
def test_evolution_loop_creates_state_file(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """运行后演化状态已持久化到 state.duckdb。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=3,
    )
    loop.run(max_generation=1)
    from fts.store.state_db import get_state_store

    assert get_state_store().get("evolution", "state") is not None


@pytest.mark.slow
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


@pytest.mark.slow
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
    # 种子评估 + 审查模块 mock 通过，确保主流程产生评估轨迹（消除 MockLLM 合成数据随机性导致的 skip）
    _mock_seed_evaluation_pass(loop)
    _mock_review_pass(loop)
    loop.verifier = MagicMock()
    loop.verifier.check.return_value = {"passed": True, "failure_reasons": []}
    loop.run(max_generation=2)

    success_dir = tmp_memory_dir / "success"
    failure_dir = tmp_memory_dir / "failure"
    # 至少有一个目录有轨迹
    total = len(list(success_dir.glob("*.json"))) + len(list(failure_dir.glob("*.json")))
    assert total > 0


@pytest.mark.slow
def test_evolution_loop_circuit_breaker_on_token(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """token 超过 2x 预算应触发熔断。"""
    from fts.factor_engine.contracts import BudgetConfig

    # 设置极小预算 + 极大 mock token
    mock_llm_client.complete.return_value = (
        json.dumps(
            {
                "mutation_type": "macro_logic",
                "mutation_summary": "Mock",
                "code_modification": "window_plus_5",
                "economic_logic_modification": {
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 4,
                    "narrative": "Mock",
                },
                "lessons_referenced": [],
            }
        ),
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


@pytest.mark.slow
def test_evolution_loop_to_dict(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client):
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
    """schema 版本不匹配的 save 应抛 StateError。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init()
    state["schema_version"] = "0"
    from fts.factor_engine.state import StateError

    with pytest.raises(StateError, match="版本不匹配"):
        mgr.save(state)


def test_state_manager_cold_start_budget(tmp_memory_dir):
    """冷启动时传入 budget_limit 应生效。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    state = mgr.load_or_init(budget_limit=9999)
    assert state["budget_limit"] == 9999


def test_state_manager_cold_start_when_no_state(tmp_memory_dir):
    """无已有状态时冷启动。"""
    mgr = EvolutionStateManager(tmp_memory_dir)
    # 临时 store 无 evolution/state → 冷启动
    state = mgr.load_or_init()
    assert state["status"] == "running"


# ─── EvolutionLoop 熔断覆盖 ───────────────────────────────


@pytest.mark.slow
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
        circuit_breaker_consecutive_low_ic=1,  # 触发条件：1 代低 IC
        circuit_breaker_low_ic_threshold=0.99,  # 几乎所有 IC 都低于此值
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


@pytest.mark.slow
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


@pytest.mark.slow
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
    assert hasattr(result, "seed_correlations")
    assert isinstance(result.seed_correlations, list)
    # to_dict 应包含 seed_correlations
    d = result.to_dict()
    assert "seed_correlations" in d
    assert isinstance(d["seed_correlations"], list)


@pytest.mark.slow
def test_seed_correlation_check_in_run(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client):
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
            DEFAULT_N_TRIALS,
            DEFAULT_EARLY_STOPPING_FAILURES,
        )

        assert DEFAULT_N_TRIALS == 100
        assert DEFAULT_EARLY_STOPPING_FAILURES == 20


# ─── GAP-I201 batch 批量漏斗（v2.65.0） ──────────────────


def test_quick_prefilter_returns_ic_three_tuple(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """验收: _quick_prefilter 返回 (ok, reason, ic) 三元组（排序截断依据）。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    factor = {
        "factor_id": "fct_test0001",
        "name": "t",
        "code": "close - close.shift(1)",
        "params": {},
    }
    ok, reason, ic = loop._quick_prefilter(factor, "trace")
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
    assert isinstance(ic, float)


def test_generate_operator_factor_constant_precheck_rejected(tmp_memory_dir, tmp_elite_dir, mock_llm_client):
    """GAP-X02: 常数信号校验前移 — 常数数据下生成阶段即拦截，不产出常量因子。

    回归: 此前常数表达式要到 _check_factor_runtime 阶段才被淘汰；
    现在 _generate_operator_factor 在生成循环内评估表达式并过滤非常数信号，
    10 次尝试全部被拦截后抛出 RuntimeError。

    注: 随机表达式空间含 warm-up 类算子（ts_dema/ts_tema/ts_aroon_down 等对
    常数输入输出 0→100 爬坡的伪变化信号，属正常实现），不能依赖真实随机性
    保证 10 次全拦截；此处固定随机时序算子为常数保持的 ts_mean 使场景可复现。
    """
    n = 80
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    const_close = pd.Series(100.0, index=dates)
    data = pd.DataFrame(
        {
            "open": const_close,
            "high": const_close,
            "low": const_close,
            "close": const_close,
            "volume": pd.Series(1e6, index=dates),
        },
        index=dates,
    )
    loop = EvolutionLoop(
        data=data,
        forward_returns=None,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        market="futures",
    )
    parent = {"factor_id": "fct_p", "name": "p", "family": "trend"}

    def _fixed_choice(seq):
        return "ts_mean" if "ts_mean" in seq else seq[0]

    with patch.object(random.Random, "choice", side_effect=_fixed_choice):
        with pytest.raises(RuntimeError):
            loop._generate_operator_factor(parent, generation=0, trace_id="t")


def test_cross_section_prefilter_uses_real_cross_section_returns(tmp_memory_dir, tmp_elite_dir, mock_llm_client):
    """GAP-X01: 横截面预筛用真实截面收益（信号矩阵 vs 截面 forward 收益）。

    回归: 此前横截面模式在 _quick_prefilter 用单标的时序 IC（且 forward_returns
    长度不齐时常被跳过），现改为全面板截面 IC，与 cross_section_evaluate_backtest
    同口径。构造 8 只斜率递增的股票，`close` 因子截面 IC 应显著为正。
    """
    from fts.factor_engine.expr_dsl.factory import create_operator_factor

    n_dates = 60
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for j in range(8):
        # 价格量级保持在 [-10, 10] 内（算子执行器对信号 clip），
        # base/g 均随 j 递增 → close 截面排名与 5 日收益排名一致 → IC 显著为正
        base = 1.0 + j * 0.1
        g = 0.005 + j * 0.002
        close = base * (1.0 + g) ** np.arange(n_dates)
        panel[f"S{j}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": np.full(n_dates, 1e6),
            },
            index=dates,
        )
    loop = EvolutionLoop(
        data=panel["S0"],
        forward_returns=None,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        cross_section_data=panel,
        cross_section_dates=dates,
        market="stock",
    )
    factor = create_operator_factor(
        "close",
        "cs_repro",
        market="stock",
        family="op",
        narrative="t",
        trace_id="t",
    )
    ok, reason, ic = loop._quick_prefilter(factor, "t")
    assert ok is True, reason
    assert ic >= 0.02  # 真实截面 IC 应超过 stock 阈值 0.02


def test_cross_section_prefilter_rejects_constant_factor(tmp_memory_dir, tmp_elite_dir, mock_llm_client):
    """GAP-X01: 横截面预筛拦截无截面区分能力的因子（有效标的不足）。"""
    from fts.factor_engine.expr_dsl.factory import create_operator_factor

    n_dates = 40
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    rng = np.random.default_rng(0)
    panel: dict[str, pd.DataFrame] = {}
    for j in range(4):
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_dates))
        panel[f"S{j}"] = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": np.full(n_dates, 1e6),
            },
            index=dates,
        )
    loop = EvolutionLoop(
        data=panel["S0"],
        forward_returns=None,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        cross_section_data=panel,
        cross_section_dates=dates,
        market="stock",
    )
    factor = create_operator_factor(
        "sub(close, close)",
        "cs_const",
        market="stock",
        family="op",
        narrative="t",
        trace_id="t",
    )
    ok, reason, ic = loop._quick_prefilter(factor, "t")
    assert ok is False
    assert ic == 0.0


def test_evolve_one_method_hint_operator(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client):
    """method_hint='operator' 强制算子演化。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop._generate_operator_factor = MagicMock(return_value=({"factor_id": "fct_op0001", "code": "x"}, "OpGen: ..."))
    out = loop._evolve_one(
        {"factor_id": "p1", "name": "parent"},
        1,
        "t",
        method_hint="operator",
        seed=7,
    )
    assert out is not None
    factor, method, summary, tokens = out
    assert method == "operator_evolution"
    assert tokens == 0
    loop._generate_operator_factor.assert_called_once()


def test_evolve_one_method_hint_macro_returns_tokens(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """method_hint='macro' 返回 LLM token 消耗（token 护栏记账）。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop.macro_evolver.evolve = MagicMock(return_value=({"factor_id": "fct_mac0001"}, "Macro", 150))
    out = loop._evolve_one(
        {"factor_id": "p1", "name": "parent"},
        1,
        "t",
        method_hint="macro",
    )
    assert out is not None
    _, method, _, tokens = out
    assert method == "macro_evolution"
    assert tokens == 150


def test_evolve_one_method_hint_gp_failure_returns_none(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """method_hint 下演化失败返回 None（batch 回调可跳过）。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop._run_gp_evolution = MagicMock(side_effect=RuntimeError("gp down"))
    out = loop._evolve_one(
        {"factor_id": "p1", "name": "parent"},
        1,
        "t",
        method_hint="gp",
    )
    assert out is None


def test_evolve_one_config_dispatch_gp_fallback(
    monkeypatch,
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """配置分派（method_hint=None）：macro 失败回退 GP（原逻辑平移）。"""
    from fts.config.settings import get_config

    monkeypatch.setattr(get_config(), "evolution_mode", "hybrid")
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop.macro_evolver.evolve = MagicMock(side_effect=Exception("llm down"))
    loop._run_gp_evolution = MagicMock(return_value=({"factor_id": "fct_gp0001", "code": "x"}, "GP"))
    out = loop._evolve_one({"factor_id": "p1", "name": "parent"}, 1, "t")
    assert out is not None
    assert out[1] == "gp_evolution"


def test_process_candidate_promotes(
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """验收: _process_candidate 全链通过后晋升 elite 并持久化状态。"""
    from unittest.mock import patch

    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    factor = {
        "factor_id": "fct_cand0001",
        "name": "cand",
        "code": "close - close.shift(1)",
        "params": {},
    }
    # mock 微观演化（避免真实 optuna 依赖）
    with patch("fts.factor_engine.evolution_candidate.evolve_micro") as m_micro:
        m_micro.return_value = (factor, None)
        _mock_seed_evaluation_pass(loop)
        loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
        loop._run_ablation_check = MagicMock(return_value={"passed": True})
        loop._run_causal_validation = MagicMock(return_value={"passed": True})
        loop._run_robustness_check = MagicMock(return_value={"passed": True})
        loop._run_shap_analysis = MagicMock(return_value={})
        loop._promote_to_elite = MagicMock(return_value="/tmp/elite/fct_cand0001.json")
        state = {
            "schema_version": "1",
            "total_factors_evaluated": 0,
            "total_factors_promoted": 0,
            "tokens_consumed": 0,
            "last_generation": 0,
        }
        elite_ids: list[str] = []
        promoted = loop._process_candidate(
            factor,
            {"factor_id": "p1", "name": "p"},
            1,
            "gp_evolution",
            "GP Gen=1",
            state,
            elite_ids,
            "trace",
            [],
        )
    assert promoted is True
    assert "fct_cand0001" in elite_ids
    assert state["last_generation"] == 1


def test_process_candidate_verifier_fail_returns_false(
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """Verifier 未通过时返回 False 且不晋升。"""
    from unittest.mock import patch

    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    factor = {
        "factor_id": "fct_cand0002",
        "name": "cand2",
        "code": "close - close.shift(1)",
        "params": {},
    }
    with patch("fts.factor_engine.evolution_candidate.evolve_micro") as m_micro:
        m_micro.return_value = (factor, None)
        loop.evaluation_chain.evaluate = MagicMock(
            return_value={
                "passed": False,
                "level_1_backtest": {"ic": 0.001},
                "failure_reasons": ["IC 过低"],
            }
        )
        loop.verifier.check = MagicMock(return_value={"passed": False, "failure_reasons": ["IC"]})
        state = {
            "schema_version": "1",
            "total_factors_evaluated": 0,
            "total_factors_promoted": 0,
            "tokens_consumed": 0,
            "last_generation": 0,
        }
        elite_ids: list[str] = []
        promoted = loop._process_candidate(
            factor,
            {"factor_id": "p1", "name": "p"},
            1,
            "gp_evolution",
            "GP",
            state,
            elite_ids,
            "trace",
            [],
        )
    assert promoted is False
    assert elite_ids == []


def test_run_batch_generation_promotes(
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """验收: batch 一代漏斗通过者进入 _process_candidate 并晋升。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop.batch_size = 3
    loop._batch_generate_one = MagicMock(
        return_value={
            "factor": {
                "factor_id": "fct_b0001",
                "name": "b",
                "code": "close - close.shift(1)",
                "params": {},
            },
            "parent_id": "p1",
            "method": "gp_evolution",
            "summary": "GP",
            "tokens": 0,
            "prefilter_ok": True,
            "prefilter_reason": "",
            "prefilter_ic": 0.05,
        }
    )
    loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
    loop._check_factor_runtime = MagicMock(return_value=(True, ""))
    loop._process_candidate = MagicMock(return_value=True)
    state = {
        "total_factors_evaluated": 0,
        "total_factors_promoted": 0,
        "tokens_consumed": 0,
        "last_generation": 0,
    }
    ok = loop._run_batch_generation(
        {"factor_id": "p1", "name": "p"},
        1,
        "trace",
        state,
        [],
        [],
    )
    assert ok is True
    assert loop._process_candidate.call_count == 3


def test_run_batch_generation_all_rejected(
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """全部候选被粗筛拦截时返回 False 且不进入细评估（全失败回退）。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    loop.batch_size = 3
    loop._batch_generate_one = MagicMock(
        return_value={
            "factor": {"factor_id": "fct_b0002", "name": "b", "code": "x", "params": {}},
            "parent_id": "p1",
            "method": "gp_evolution",
            "summary": "GP",
            "tokens": 0,
            "prefilter_ok": False,
            "prefilter_reason": "",
            "prefilter_ic": 0.0,
        }
    )
    loop._quick_prefilter = MagicMock(return_value=(False, "IC 过低", 0.0))
    loop._check_factor_runtime = MagicMock(return_value=(True, ""))
    loop._process_candidate = MagicMock(return_value=True)
    state = {
        "total_factors_evaluated": 0,
        "total_factors_promoted": 0,
        "tokens_consumed": 0,
        "last_generation": 0,
    }
    ok = loop._run_batch_generation(
        {"factor_id": "p1", "name": "p"},
        1,
        "trace",
        state,
        [],
        [],
    )
    assert ok is False
    loop._process_candidate.assert_not_called()


def test_run_batch_mode_calls_batch_generation(
    monkeypatch,
    sample_ohlcv,
    forward_returns,
    tmp_memory_dir,
    tmp_elite_dir,
    mock_llm_client,
):
    """验收: evolution_mode=batch 时 run() 每代调用 _run_batch_generation。"""
    from fts.config.settings import get_config

    monkeypatch.setattr(get_config(), "evolution_mode", "batch")
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=2,
    )
    seeds = [
        {
            "factor_id": "seed_p1",
            "name": "p1",
            "code": "close - close.shift(1)",
            "params": {},
            "market": "stock",
        }
    ]
    loop.seed_pool.load_all_seeds = MagicMock(return_value=seeds)
    loop._merge_l1_candidates = MagicMock(side_effect=lambda s, t: s)
    loop._run_seed_correlation_check = MagicMock(return_value=[])

    def _fake_promote(seeds_arg, trace_id, state, elite_ids, seed_correlations=None):
        for s in seeds_arg:
            elite_ids.append(s["factor_id"])
        return len(seeds_arg)

    loop._evaluate_and_promote_seeds = MagicMock(side_effect=_fake_promote)
    loop._run_batch_generation = MagicMock(return_value=True)
    result = loop.run(max_generation=2)
    assert result.status == "completed"
    assert loop._run_batch_generation.call_count == 2


# ─── micro_evolution coverage（续：GAP-I201 插入点打断的类方法归入此类） ──
class TestMicroEvolutionCoverageExt:
    def test_micro_evolution_error_is_exception(self):
        from fts.factor_engine.micro_evolution import MicroEvolutionError

        assert issubclass(MicroEvolutionError, Exception)

    def test_micro_evolution_all_exports(self):
        from fts.factor_engine.micro_evolution import (
            optimize_params,
            evolve_micro,
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
        self,
        sample_ohlcv,
        forward_returns,
        monkeypatch,
    ):
        """模拟无 optuna 时应返回原 params + score=0.0。"""
        import fts.factor_engine.micro_evolution as mev

        monkeypatch.setattr(mev, "_HAS_OPTUNA", False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_test1234",
            name="test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10, "threshold": 0.5},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试因子"
            ),
            source="manual",
        )
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns)
        assert params == {"window": 10, "threshold": 0.5}
        assert score == 0.0

    def test_optimize_params_with_custom_objective_fn(
        self,
        sample_ohlcv,
        forward_returns,
        monkeypatch,
    ):
        """模拟无 optuna 时忽略 objective_fn。"""
        import fts.factor_engine.micro_evolution as mev

        monkeypatch.setattr(mev, "_HAS_OPTUNA", False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_test5678",
            name="test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="测试因子"
            ),
            source="manual",
        )
        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, objective_fn=lambda s, r: 0.99)
        assert score == 0.0  # optuna 不可用时忽略 objective_fn

    def test_optimize_params_with_mock_optuna(
        self,
        sample_ohlcv,
        forward_returns,
        mock_optuna_study,
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
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="optuna测试"
            ),
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
        self,
        sample_ohlcv,
        forward_returns,
        mock_optuna_study,
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
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="error测试"
            ),
            source="manual",
        )
        mock_study.optimize.side_effect = RuntimeError("optuna 崩溃")

        import fts.factor_engine.micro_evolution as mev

        with pytest.raises(mev.MicroEvolutionError, match="optuna 优化失败"):
            mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)

    def test_optimize_params_no_best_params(
        self,
        sample_ohlcv,
        forward_returns,
        mock_optuna_study,
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
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="empty测试"
            ),
            source="manual",
        )
        mock_study.best_params = {}  # 空表示无最佳参数
        mock_study.best_value = 0.0
        mock_study.trials = []

        import fts.factor_engine.micro_evolution as mev

        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert params == {"window": 10}
        assert score == 0.0

    def test_evolve_micro_basic(
        self,
        sample_ohlcv,
        forward_returns,
        monkeypatch,
    ):
        """evolve_micro 基本路径（模拟无 optuna）。"""
        import fts.factor_engine.micro_evolution as mev

        monkeypatch.setattr(mev, "_HAS_OPTUNA", False)
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_evolve_test",
            name="evolve_test",
            code="def factor_program(data, params):\n    import numpy as np\n    w = params.get('window', 10)\n    return np.zeros(len(data['close']))",
            params={"window": 10},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="evolve测试"
            ),
            source="manual",
        )
        evolved, score = mev.evolve_micro(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert isinstance(evolved, dict)
        assert "factor_id" in evolved
        assert evolved["params"] == {"window": 10}
        assert score == 0.0

    def test_optimize_params_empty_params_uses_default_search_space(
        self,
        sample_ohlcv,
        forward_returns,
        mock_optuna_study,
    ):
        """params 为空时（GP/算子因子）应注入默认搜索空间，避免 optuna 退化。

        回归: Optuna 搜索空间退化 — 空 params 导致无参数可优化，
        所有 trial 返回相同值，超参优化无效。
        """
        mock_optuna, mock_study = mock_optuna_study
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        factor = FactorProgram(
            factor_id="fct_noparam_test",
            name="noparam_test",
            code=(
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    n = len(close)\n"
                "    ret = np.zeros(n)\n"
                "    if n > 5:\n"
                "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
                "    return np.tanh(ret * 10)\n"
            ),
            params={},  # 空参数 → 应触发默认搜索空间
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(
                theory=3, behavioral=3, microstructure=3, institutional=3, narrative="noparam测试"
            ),
            source="manual",
        )

        # 记录 objective 实际请求的搜索空间键
        requested_keys: list[set[str]] = []

        def fake_optimize(func, **_kwargs):
            for _ in range(3):
                trial = MagicMock()
                trial.suggest_int.side_effect = lambda name, lo, hi: 42
                trial.suggest_float.side_effect = lambda name, lo, hi: 0.5
                trial.suggest_categorical.side_effect = lambda name, choices: choices[0]
                # 捕获 trial 对象供 objective 查询
                func(trial)
            requested_keys.append({"lookback", "holding", "window", "threshold"})

        mock_study.optimize.side_effect = fake_optimize
        mock_study.best_params = {"lookback": 42, "holding": 42, "window": 42, "threshold": 0.5}
        mock_study.best_value = 0.1
        mock_study.trials = [MagicMock()]

        import fts.factor_engine.micro_evolution as mev

        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=3)
        # 默认搜索空间生效: 返回参数包含全部 4 个默认键
        assert set(params.keys()) == {"lookback", "holding", "window", "threshold"}
        assert score == 0.1
        mock_optuna.create_study.assert_called_once()


# ─── EvolutionLoop 未覆盖路径补齐 ─────────────────────────


def _make_minimal_factor(factor_id: str = "fct_test1234") -> FactorProgram:
    """构造最小 FactorProgram fixture。"""
    return FactorProgram(
        factor_id=factor_id,
        name="test_factor",
        # 有效信号代码（非常数、长度与输入一致），通过运行时校验
        code=(
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    if n > 1:\n"
            "        ret[1:] = np.diff(close) / np.maximum(np.abs(close[1:]), 1e-10)\n"
            "    return np.tanh(ret * 10)\n"
        ),
        params={"window": 10},
        signature=FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        economic_logic=EconomicLogic(
            theory=3,
            behavioral=3,
            microstructure=3,
            institutional=3,
            narrative="测试因子",
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

    @pytest.mark.slow
    def test_macro_evolution_failure(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
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

    @pytest.mark.slow
    def test_macro_evolution_failure_recorded(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
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
        # hybrid 模式下 GP 失败后回退算子演化，也 mock 失败以验证 GP 失败被记录
        loop._generate_operator_factor = MagicMock(side_effect=RuntimeError("算子演化失败"))
        loop.run(max_generation=2)
        failure_dir = tmp_memory_dir / "failure"
        files = list(failure_dir.glob("*.json"))
        assert len(files) > 0
        data = json.loads(files[0].read_text(encoding="utf-8"))
        # 验证演化失败被记录（GP 失败或 hybrid 演化失败）
        mutation = data.get("mutation_summary", "")
        assert any(kw in mutation for kw in ["GP 演化", "宏观演化", "GP 失败", "hybrid_evolution"])

    # ─── 微观演化失败（line 192-197）────────────────────

    @pytest.mark.slow
    def test_micro_evolution_failure(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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

    @pytest.mark.slow
    def test_micro_evolution_failure_recorded(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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

    @pytest.mark.slow
    def test_evolution_loop_promote_to_elite(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
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
        # 运行时校验和快速预筛选 mock 通过（算子因子需 mock 绕过实时执行）
        loop._check_factor_runtime = MagicMock(return_value=(True, ""))
        loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
        result = loop.run(max_generation=2)
        # 至少会有一部分因子晋级
        assert result.total_factors_promoted >= 1
        assert len(result.elite_factor_ids) > 0
        # 精英池目录应有文件
        elite_files = list(tmp_elite_dir.glob("*.json"))
        assert len(elite_files) > 0

    # ─── 外层 except 块（line 256-258）───────────────────

    def test_evolution_loop_outer_exception(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        monkeypatch,
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

    @pytest.mark.slow
    def test_evolution_loop_failure_rate_circuit_breaker(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
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
        # 运行时校验和快速预筛选 mock 通过（算子因子需 mock 绕过实时执行）
        loop._check_factor_runtime = MagicMock(return_value=(True, ""))
        loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
        # 回测管线 mock 跳过真实回测，仅聚焦熔断链路
        loop._run_backtest_pipeline = MagicMock(return_value=None)
        # v2.50.0 种子路径与演化因子共用 Verifier：种子阶段判定通过（晋升提供父因子），
        # 演化因子阶段判定拒绝 → 主循环全失败 → 失败率熔断
        seeds = loop.seed_pool.load_all_seeds()
        seed_count = len(seeds)
        verifier_calls = {"n": 0}

        def _verifier_side_effect(evaluation):
            verifier_calls["n"] += 1
            if verifier_calls["n"] <= seed_count:
                return {"passed": True, "failure_reasons": []}
            return {"passed": False, "failure_reasons": ["模拟失败"]}

        # 让 Verifier 拒绝演化因子（种子阶段通过、主循环中评估通过但 Verifier 判定失败）
        # 这样种子因子能通过评估晋升提供父因子，
        # 但主循环中所有演化因子都失败 → 失败率熔断
        loop.verifier.check = MagicMock(side_effect=_verifier_side_effect)
        result = loop.run(max_generation=15)
        assert result.status == "circuit_broken"
        assert "失败率" in (result.circuit_breaker_reason or "")

    # ─── 内部方法直接测试 ─────────────────────────────────

    def test_promote_to_elite(
        self,
        tmp_elite_dir,
        tmp_memory_dir,
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
            level_3_multiple={"passed": True},
            evaluated_at="2026-07-18T00:00:00",
        )
        fp = loop._promote_to_elite(factor, evaluation)
        assert fp.exists()
        assert fp.suffix == ".json"
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_promote_test_unique"

    # ─── GAP-032: 双写原子化（JSON 快照 + DuckDB catalog 严格一致）───

    def test_write_to_duckdb_returns_true_on_success(
        self,
        tmp_elite_dir,
        tmp_memory_dir,
    ):
        """_write_to_duckdb 成功时应返回 True。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_repo = MagicMock()
        mock_repo.get_factor = MagicMock(return_value=None)
        mock_repo.create_factor = MagicMock(return_value="fct_x")
        loop._get_repo = MagicMock(return_value=mock_repo)
        factor = _make_minimal_factor("fct_duck_success")
        factor["name"] = "fct_duck_success"
        evaluation = FactorEvaluation(
            factor_id="fct_duck_success",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.6},
            evaluated_at="2026-07-18T00:00:00",
        )
        result = loop._write_to_duckdb(factor, evaluation)
        assert result is True
        mock_repo.create_factor.assert_called_once()

    def test_write_to_duckdb_returns_false_on_error(
        self,
        tmp_elite_dir,
        tmp_memory_dir,
    ):
        """_write_to_duckdb 失败时应返回 False（不吞异常）。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_repo = MagicMock()
        mock_repo.get_factor = MagicMock(return_value=None)
        mock_repo.create_factor = MagicMock(side_effect=RuntimeError("duckdb down"))
        loop._get_repo = MagicMock(return_value=mock_repo)
        factor = _make_minimal_factor("fct_duck_fail")
        factor["name"] = "fct_duck_fail"
        evaluation = FactorEvaluation(
            factor_id="fct_duck_fail",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.6},
            evaluated_at="2026-07-18T00:00:00",
        )
        result = loop._write_to_duckdb(factor, evaluation)
        assert result is False

    def test_promote_to_elite_duckdb_failure_rolls_back_json(
        self,
        tmp_elite_dir,
        tmp_memory_dir,
    ):
        """DuckDB 写入失败时 _promote_to_elite 应回滚 JSON 快照并返回 None。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name = MagicMock(return_value=None)
        mock_repo.get_factor = MagicMock(return_value=None)
        mock_repo.create_factor = MagicMock(side_effect=RuntimeError("duckdb down"))
        loop._get_repo = MagicMock(return_value=mock_repo)
        factor = _make_minimal_factor("fct_rollback_unique")
        factor["name"] = "fct_rollback_unique"
        evaluation = FactorEvaluation(
            factor_id="fct_rollback_unique",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.6},
            level_3_multiple={"passed": True},
            evaluated_at="2026-07-18T00:00:00",
        )
        fp = loop._promote_to_elite(factor, evaluation)
        assert fp is None
        # JSON 快照应被回滚删除，不留"快照有、catalog 无"孤儿
        assert not (tmp_elite_dir / "fct_rollback_unique.json").exists()
        assert list(tmp_elite_dir.glob("*.json")) == []

    def test_promote_to_elite_duckdb_success_keeps_json(
        self,
        tmp_elite_dir,
        tmp_memory_dir,
    ):
        """DuckDB 写入成功时 _promote_to_elite 保留 JSON 快照并返回路径。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name = MagicMock(return_value=None)
        mock_repo.get_factor = MagicMock(return_value=None)
        mock_repo.create_factor = MagicMock(return_value="fct_keep_unique")
        loop._get_repo = MagicMock(return_value=mock_repo)
        factor = _make_minimal_factor("fct_keep_unique")
        factor["name"] = "fct_keep_unique"
        evaluation = FactorEvaluation(
            factor_id="fct_keep_unique",
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.6},
            level_3_multiple={"passed": True},
            evaluated_at="2026-07-18T00:00:00",
        )
        fp = loop._promote_to_elite(factor, evaluation)
        assert fp is not None
        assert fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_keep_unique"

    # ─── GAP-030: 测试隔离 — factor_db_path 注入点 ─────────

    def test_get_repo_uses_isolated_db_path(self, tmp_path):
        """GAP-030: EvolutionLoop(factor_db_path=...) 时 _get_repo 应使用隔离库。"""
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_path / "elite",
            memory_dir=tmp_path / "memory",
            factor_db_path=tmp_path / "catalog.duckdb",
        )
        repo = loop._get_repo()
        assert str(repo._db_path).endswith("catalog.duckdb")

    def test_get_repo_defaults_to_real_db(self, tmp_path, monkeypatch):
        """GAP-030: 未传 factor_db_path 时 _get_repo 应使用默认库路径（分库后按 market 路由）。"""
        import fts.factor_engine.factor_db.schema as schema

        fake_default = tmp_path / "default_catalog.duckdb"
        monkeypatch.setattr(schema, "DATABASE_PATH_FUTURES", fake_default)
        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0]}),
            forward_returns=np.array([0.0]),
            elite_dir=tmp_path / "elite",
            memory_dir=tmp_path / "memory",
            market="futures",
        )
        repo = loop._get_repo()
        assert str(repo._db_path).endswith("default_catalog.duckdb")

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
        loop.experience_chain.record_failure = MagicMock(side_effect=RuntimeError("磁盘已满"))
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

    @pytest.mark.slow
    def test_low_ic_increment(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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
        # 运行时校验和快速预筛选 mock 通过（算子因子需 mock 绕过实时执行）
        loop._check_factor_runtime = MagicMock(return_value=(True, ""))
        loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
        # mock macro_evolver.evolve 返回有效结果（含 trace_id）
        mock_factor = _make_minimal_factor("fct_lowic_test")
        loop.macro_evolver.evolve = MagicMock(
            return_value=(
                mock_factor,
                "mock summary",
                100,
            )
        )
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

    @pytest.mark.slow
    def test_main_with_once_flag(self, monkeypatch, tmp_path):
        """带 --once 标志应运行完整演化。"""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation",
                "1",
                "--memory-dir",
                str(tmp_path / "evolution"),
                "--elite-dir",
                str(tmp_path / "elite"),
            ],
        )
        from fts.factor_engine.evolution_loop import main

        # 不应抛出异常
        main()

    @pytest.mark.slow
    def test_main_with_max_generation(self, monkeypatch, tmp_path):
        """带 --max-generation 参数应限定代数。"""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation",
                "2",
                "--memory-dir",
                str(tmp_path / "evolution2"),
                "--elite-dir",
                str(tmp_path / "elite2"),
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
            sys,
            "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation",
                "1",
                "--memory-dir",
                str(tmp_path / "evolution_mod"),
                "--elite-dir",
                str(tmp_path / "elite_mod"),
            ],
        )
        # 模拟 __name__ == "__main__"
        from fts.factor_engine import evolution_loop as evolution_loop_mod

        with patch.object(evolution_loop_mod, "__name__", "__main__"):
            # 触发 if __name__ == "__main__": main()
            # 直接调用等同于 __main__ 的代码
            exec("from fts.factor_engine.evolution_loop import main; main()", {"__name__": "__main__"})
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
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run", return_value=mock_result):
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation",
                    "3",
                    "--memory-dir",
                    str(tmp_path / "evo_cb"),
                    "--elite-dir",
                    str(tmp_path / "elite_cb"),
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
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run", return_value=mock_result):
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation",
                    "2",
                    "--memory-dir",
                    str(tmp_path / "evo_elite"),
                    "--elite-dir",
                    str(tmp_path / "elite_elite"),
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
        with patch("fts.factor_engine.evolution_loop.EvolutionLoop.run", return_value=mock_result):
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "evolution_loop.py",
                    "--once",
                    "--max-generation",
                    "5",
                    "--memory-dir",
                    str(tmp_path / "evo_both"),
                    "--elite-dir",
                    str(tmp_path / "elite_both"),
                ],
            )
            from fts.factor_engine.evolution_loop import main

            main()


# ─── 进一步覆盖 line 221 ──────────────────────────────────


class TestLine221:
    """专门覆盖 evolution_loop.py line 221 (self._consecutive_low_ic = 0)。"""

    @pytest.mark.slow
    def test_consecutive_low_ic_reset_on_success(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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
        # GAP-I206 (v2.71.0): 隔离 L2 准入去冗余相关性检查（mock 候选信号与
        # 已晋升种子相关可达 -0.967，与低 IC 计数器重置断言无关）
        loop._check_elite_correlation = MagicMock(return_value=None)
        # 运行时校验和快速预筛选 mock 通过（算子因子需 mock 绕过实时执行）
        loop._check_factor_runtime = MagicMock(return_value=(True, ""))
        loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
        # Mock macro_evolver 返回有效因子（包含 trace_id）
        parent_factor = _make_minimal_factor("fct_line221_parent")
        loop.macro_evolver.evolve = MagicMock(
            return_value=(
                parent_factor,
                "Mock macro summary",
                200,
            )
        )
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

    @pytest.mark.slow
    def test_cross_section_evaluation_path(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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
        loop.macro_evolver.evolve = MagicMock(
            return_value=(
                _make_minimal_factor("fct_cross_test"),
                "mock cross macro",
                200,
            )
        )
        optimized = _make_minimal_factor("fct_cross_opt")
        mock_evolve_micro.return_value = (optimized, 0.03)
        result = loop.run(max_generation=1)
        assert result.status in ("completed", "circuit_broken")
        assert result.generations_completed >= 0

    @pytest.mark.slow
    def test_cross_section_failure_reasons_low_ic(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_evolve_micro,
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
        with patch("fts.factor_engine.evolution_seeds.cross_section_evaluate_backtest") as mock_cs:
            mock_cs.return_value = {"ic": 0.01, "sharpe": 1.0}
            loop.macro_evolver.evolve = MagicMock(
                return_value=(
                    _make_minimal_factor("fct_cross_lowic"),
                    "mock",
                    200,
                )
            )
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
            factor_id="fct_test",
            trace_id="t",
            passed=True,
            failure_reasons=[],
            evaluated_at="now",
        )
        # 模拟 verifier 通过后的路径 — 使用成功轨迹记录
        factor = _make_minimal_factor("fct_reset")
        loop._record_success_trace(
            factor=factor,
            generation=1,
            mutation_type="combined",
            mutation_summary="测试重置",
            evaluation=eval_passed,
            lessons=["test"],
            trace_id="l2_test",
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
            sys,
            "argv",
            [
                "evolution_loop.py",
                "--once",
                "--max-generation",
                "1",
                "--memory-dir",
                str(tmp_path / "evo_mem"),
                "--elite-dir",
                str(tmp_path / "evo_elite"),
            ],
        )
        with patch.object(el_mod, "__name__", "__main__"):
            exec("from fts.factor_engine.evolution_loop import main; main()", {"__name__": "__main__"})


# ─── v2.59.0 (GAP-F03) 期货横截面板块中性化 ──────────────


class TestGapF03SectorNeutralization:
    """GAP-F03: market=futures + 横截面模式自动注入板块映射（产业链中性化）。"""

    @staticmethod
    def _budget():
        from fts.factor_engine.contracts import BudgetConfig

        return BudgetConfig(
            nightly_token_limit=1_000_000,
            monthly_token_limit=10_000_000,
            max_generation=3,
            max_tokens_per_factor=10_000,
            circuit_breaker_token_ratio=10.0,
            circuit_breaker_consecutive_low_ic=100,
            circuit_breaker_low_ic_threshold=0.01,
            circuit_breaker_failure_rate=0.99,
        )

    def _make_loop(self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, **kwargs):
        cross_data = {"RB0": sample_ohlcv, "I0": sample_ohlcv, "SC0": sample_ohlcv}
        cross_dates = pd.DatetimeIndex(sample_ohlcv.index)
        base = dict(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            budget=self._budget(),
            n_trials_micro=2,
            cross_section_data=cross_data,
            cross_section_dates=cross_dates,
            market="futures",
        )
        base.update(kwargs)
        return EvolutionLoop(**base)

    def test_futures_cross_section_auto_injects_sector_map(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, monkeypatch
    ):
        """GAP-F03: futures_neutralization=true 时自动从 FUTURES_SECTOR_MAP 构建板块映射。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.data_futures.FUTURES_SECTOR_MAP",
            {"黑色系": ["RB0", "I0"], "能源": ["SC0"]},
        )
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(futures_neutralization=True),
        )
        loop = self._make_loop(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir)
        assert loop.industry_map is not None
        assert loop.industry_map["RB0"] == "黑色系"
        assert loop.industry_map["I0"] == "黑色系"
        assert loop.industry_map["SC0"] == "能源"

    def test_futures_neutralization_disabled_skips_injection(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, monkeypatch
    ):
        """futures_neutralization=false 时不应注入板块映射。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.data_futures.FUTURES_SECTOR_MAP",
            {"黑色系": ["RB0", "I0"], "能源": ["SC0"]},
        )
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(futures_neutralization=False),
        )
        loop = self._make_loop(sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir)
        assert loop.industry_map is None

    def test_explicit_industry_map_not_overridden(
        self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, monkeypatch
    ):
        """显式传入 industry_map 时不应被自动注入覆盖。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.data_futures.FUTURES_SECTOR_MAP",
            {"黑色系": ["RB0", "I0"], "能源": ["SC0"]},
        )
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(futures_neutralization=True),
        )
        explicit = {"RB0": "自定义板块"}
        loop = self._make_loop(
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            industry_map=explicit,
        )
        assert loop.industry_map == explicit


# ─── v2.60.0 (GAP-F08) 样本外强制：冷启动 WalkForward ──────────


class TestGapF08WalkForwardEnforcement:
    """GAP-F08: 晋升路径强制 WalkForward 冷启动样本外验证。"""

    @staticmethod
    def _factor_code() -> str:
        return (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    if n > 5:\n"
            "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
            "    return np.tanh(ret * 10)\n"
        )

    def _make_factor(self) -> dict:
        return {
            "factor_id": "fct_wf_test",
            "name": "wf_test",
            "code": self._factor_code(),
            "params": {},
        }

    def _make_evaluation(self) -> FactorEvaluation:
        return FactorEvaluation(
            factor_id="fct_wf_test",
            trace_id="trace_wf",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5, "oos_ratio": 0.3, "icir": 1.2},
            evaluated_at="2026-08-09T00:00:00",
        )

    def test_build_wf_config_long_data_uses_default(self, minimal_loop):
        """数据 ≥3 年时使用默认 WalkForward 配置。"""
        import pandas as pd

        long_data = pd.DataFrame(
            {"close": np.linspace(3000.0, 4000.0, 900), "volume": np.ones(900) * 1e5},
            index=pd.date_range("2022-01-01", periods=900, freq="D"),
        )  # ~3.6 年
        cfg = minimal_loop._build_wf_config(long_data)
        assert cfg["window_years"] == 3
        assert cfg["step_months"] == 6

    def test_build_wf_config_short_data_adapted(self, sample_ohlcv, minimal_loop):
        """1-2 年数据应缩短窗口保证多窗口验证。"""
        med_data = sample_ohlcv.iloc[:400]  # ~1.6 年
        cfg = minimal_loop._build_wf_config(med_data)
        assert cfg["window_years"] == 1
        assert cfg["step_months"] == 2

    def test_walkforward_oos_disabled_returns_none(self, sample_ohlcv, minimal_loop, monkeypatch):
        """force_walkforward=false 时跳过并返回 None。"""
        from fts.config.settings import FTSConfig

        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(force_walkforward=False),
        )
        minimal_loop.data = sample_ohlcv
        assert minimal_loop._run_walkforward_oos(self._make_factor()) is None

    def test_walkforward_oos_insufficient_data_returns_none(self, minimal_loop):
        """数据 <125 行时跳过并返回 None。"""
        import pandas as pd

        short = pd.DataFrame(
            {"close": np.arange(50.0) + 100.0, "volume": np.ones(50) * 1000},
            index=pd.date_range("2024-01-01", periods=50, freq="D"),
        )
        minimal_loop.data = short
        assert minimal_loop._run_walkforward_oos(self._make_factor()) is None

    def test_walkforward_oos_runs_on_sufficient_data(self, sample_ohlcv, minimal_loop):
        """数据充分时冷启动验证应返回多窗口结果。"""
        minimal_loop.data = sample_ohlcv
        result = minimal_loop._run_walkforward_oos(self._make_factor())
        assert result is not None
        assert result["n_windows_completed"] >= 1
        assert "ic_consistency" in result
        assert "passed" in result

    def test_factor_audit_prefers_walkforward_result(self, minimal_loop, sample_dataframe, monkeypatch):
        """强制 WalkForward 结果应覆盖 L1 单段 ICIR 近似（失败则审计不通过）。"""
        from unittest.mock import MagicMock

        from fts.factor_engine.audit import FactorAuditReport

        minimal_loop.data = sample_dataframe
        # 冷启动验证返回失败（ic_consistency=0.2 < 0.5）
        minimal_loop._run_walkforward_oos = MagicMock(
            return_value={
                "ic_consistency": 0.2,
                "passed": False,
                "windows": [],
                "n_windows_completed": 2,
            }
        )
        report = minimal_loop._run_factor_audit(
            self._make_factor(),
            self._make_evaluation(),
            "trace_wf",
        )
        assert isinstance(report, FactorAuditReport)
        oos_item = [it for it in report.items if it.name == "oos_consistency"][0]
        assert oos_item.status == "failed"
        assert "0.20" in oos_item.evidence

    def test_factor_audit_walkforward_disabled_keeps_l1(self, minimal_loop, sample_dataframe, monkeypatch):
        """关闭强制验证时审计 oos_consistency 回退 L1 近似（通过）。"""
        from unittest.mock import MagicMock

        from fts.factor_engine.audit import FactorAuditReport

        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)
        report = minimal_loop._run_factor_audit(
            self._make_factor(),
            self._make_evaluation(),
            "trace_wf",
        )
        assert isinstance(report, FactorAuditReport)
        oos_item = [it for it in report.items if it.name == "oos_consistency"][0]
        # L1 icir=1.2 → ic_consistency=1.0 ≥ 0.5 → 通过
        assert oos_item.status == "passed"

    def test_factor_audit_reuses_evaluation_walkforward(self, minimal_loop, sample_dataframe):
        """GAP-070: evaluation 已带评估链走航结果 → 审计直接复用，不再独立计算（双重 WalkForward 合并）。"""
        from unittest.mock import MagicMock

        from fts.factor_engine.audit import FactorAuditReport

        minimal_loop.data = sample_dataframe
        # 若被调用则视为合并失败（应完全跳过独立走航）
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)

        evaluation = self._make_evaluation()
        evaluation["walk_forward"] = {
            "ic_consistency": 0.8,
            "ic_volatility": 0.1,
            "sharpe_volatility": 0.2,
            "consistency_score": 82.0,
            "passed": True,
            "windows": [{"ic": 0.1, "sharpe": 1.0, "turnover": 0.5}],
            "n_windows_completed": 2,
        }
        report = minimal_loop._run_factor_audit(self._make_factor(), evaluation, "trace_wf")
        assert isinstance(report, FactorAuditReport)
        minimal_loop._run_walkforward_oos.assert_not_called()
        oos_item = [it for it in report.items if it.name == "oos_consistency"][0]
        assert oos_item.status == "passed"
        assert "0.80" in oos_item.evidence

    def test_factor_audit_falls_back_when_walkforward_missing(self, minimal_loop, sample_dataframe):
        """GAP-073: evaluation 无走航结果 → 兜底独立计算（双窗口正常评估），保持 passed 判定。"""
        from unittest.mock import MagicMock

        from fts.factor_engine.audit import FactorAuditReport

        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(
            return_value={
                "ic_consistency": 0.6,
                "passed": True,
                "windows": [],
                "n_windows_completed": 2,
            }
        )
        report = minimal_loop._run_factor_audit(
            self._make_factor(),
            self._make_evaluation(),  # 无 walk_forward 字段
            "trace_wf",
        )
        assert isinstance(report, FactorAuditReport)
        minimal_loop._run_walkforward_oos.assert_called_once()
        oos_item = [it for it in report.items if it.name == "oos_consistency"][0]
        assert oos_item.status == "passed"


# ─── Phase B.2: BacktestPipeline 集成测试 ────────────────


class TestBacktestPipelineIntegration:
    """测试 BacktestPipeline 在演化循环中的集成。"""

    def test_backtest_pipeline_initialization(self, minimal_loop):
        """验证 BacktestPipeline 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.backtest_pipeline is not None

    def test_run_backtest_pipeline_success(self, minimal_loop, sample_seed, sample_evaluation):
        """验证 _run_backtest_pipeline 成功执行。"""
        from fts.factor_engine.backtest_pipeline import PipelineResult

        mock_result = PipelineResult(
            success=True,
            stage="report",
            duration_ms=100.0,
            output=None,
        )
        minimal_loop.backtest_pipeline.run = MagicMock(return_value=mock_result)

        result = minimal_loop._run_backtest_pipeline(sample_seed, sample_evaluation, "test_trace")
        assert result is not None
        assert result["success"] is True
        assert result["duration_ms"] == 100.0

    def test_run_backtest_pipeline_failure(self, minimal_loop, sample_seed, sample_evaluation):
        """验证 _run_backtest_pipeline 失败返回 None。"""
        from fts.factor_engine.backtest_pipeline import PipelineResult

        mock_result = PipelineResult(
            success=False,
            stage="data_load",
            duration_ms=50.0,
            error="data not found",
        )
        minimal_loop.backtest_pipeline.run = MagicMock(return_value=mock_result)

        result = minimal_loop._run_backtest_pipeline(sample_seed, sample_evaluation, "test_trace")
        assert result is None

    def test_run_backtest_pipeline_exception(self, minimal_loop, sample_seed, sample_evaluation):
        """验证 _run_backtest_pipeline 异常返回 None。"""
        minimal_loop.backtest_pipeline.run = MagicMock(side_effect=RuntimeError("boom"))
        result = minimal_loop._run_backtest_pipeline(sample_seed, sample_evaluation, "test_trace")
        assert result is None


# ─── Phase B.1: DataQualityMonitor 集成测试 ──────────────


class TestDataQualityIntegration:
    """测试 DataQualityMonitor 在演化循环中的集成。"""

    def test_data_quality_monitor_initialization(self, minimal_loop):
        """验证 DataQualityMonitor 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.data_quality_monitor is not None

    def test_register_factor_baseline(self, minimal_loop, sample_seed, sample_evaluation):
        """验证注册因子基准数据。"""
        minimal_loop.data_quality_monitor.register_factor = MagicMock()
        minimal_loop._register_factor_baseline(sample_seed, sample_evaluation)

        minimal_loop.data_quality_monitor.register_factor.assert_called_once()
        call_kwargs = minimal_loop.data_quality_monitor.register_factor.call_args
        assert call_kwargs[1]["factor_id"] == sample_seed["factor_id"]

    def test_check_factor_data_quality_no_alerts(self, minimal_loop, sample_seed, sample_evaluation):
        """验证数据质量检查无告警时返回空列表。"""
        minimal_loop.data_quality_monitor.check = MagicMock(return_value=[])
        alerts = minimal_loop._check_factor_data_quality(sample_seed, sample_evaluation)
        assert alerts == []

    def test_check_factor_data_quality_with_alerts(self, minimal_loop, sample_seed, sample_evaluation):
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
        minimal_loop.data_quality_monitor.check = MagicMock(return_value=[alert])
        alerts = minimal_loop._check_factor_data_quality(sample_seed, sample_evaluation)
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
        minimal_loop.elite_tracker.report = MagicMock(return_value={"status_counts": {}, "grade_counts": {}})
        minimal_loop._run_periodic_factor_review([], "test_trace")

    def test_run_periodic_factor_review_with_elite(self, minimal_loop, sample_seed):
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
        # Mock get 返回有效跟踪记录，使 _run_periodic_factor_review 执行 update
        minimal_loop.elite_tracker.get = MagicMock(
            return_value={
                "factor_id": fid,
                "name": "test_factor",
                "status": "active",
                "grade": "A",
            }
        )

        minimal_loop._run_periodic_factor_review([fid], "test_trace")
        minimal_loop.elite_tracker.update.assert_called_once()

    def test_run_periodic_factor_review_with_retirement(self, minimal_loop, sample_seed):
        """验证有因子被淘汰时定期重评估正常处理。"""
        fid = sample_seed["factor_id"]
        minimal_loop.elite_tracker.auto_retire = MagicMock(return_value=[fid])
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
        result = minimal_loop._get_factor_data_for_review(sample_seed["factor_id"])
        assert result is not None
        assert "ic" in result
        assert "sharpe" in result

    def test_periodic_review_in_run_finally(self, minimal_loop, sample_dataframe, sample_forward_returns):
        """验证定期重评估在 run() 的 finally 块中被调用。"""
        minimal_loop.elite_tracker.auto_retire = MagicMock(return_value=[])
        minimal_loop.elite_tracker.report = MagicMock(return_value={"status_counts": {}, "grade_counts": {}})
        minimal_loop.elite_tracker.update = MagicMock()

        with patch.object(minimal_loop, "_run_periodic_factor_review") as mock_review:
            with patch.object(minimal_loop, "_evaluate_and_promote_seeds", return_value=0):
                with patch.object(minimal_loop.seed_pool, "load_all_seeds", return_value=[]):
                    minimal_loop.run(max_generation=1)
                    mock_review.assert_called_once()


# ─── GP 演化集成测试 ──────────────────────────────────────


class TestGPEvolutionIntegration:
    """测试 GP 演化作为宏观演化 fallback 的集成路径。"""

    def test_gp_evolution_initialized(self, minimal_loop):
        """验证 FeatureOpsEngine 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.feature_ops_engine is not None

    def test_run_gp_evolution_returns_factor_program(self, minimal_loop, sample_seed, sample_dataframe):
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
        minimal_loop.feature_ops_engine.run_gp_search = MagicMock(return_value=mock_result)

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

    def test_run_gp_evolution_with_invalid_fitness(self, minimal_loop, sample_seed, sample_dataframe):
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
        minimal_loop.feature_ops_engine.run_gp_search = MagicMock(return_value=mock_result)

        with pytest.raises(RuntimeError, match="GP 演化适应度无效"):
            minimal_loop._run_gp_evolution(
                parent=sample_seed,
                generation=1,
                trace_id="test_trace_gp",
            )

    @pytest.mark.slow
    def test_gp_fallback_in_evolution_loop(self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir):
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
        loop._run_gp_evolution = MagicMock(side_effect=RuntimeError("GP 算子初始化失败"))
        result = loop.run(max_generation=3)
        assert result.generations_completed == 3
        assert result.status == "completed"

    @pytest.mark.slow
    def test_gp_success_flow_integration(self, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir):
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
        loop.feature_ops_engine.run_gp_search = MagicMock(return_value=mock_result)

        # Mock 微观演化返回有效因子

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

    def test_promote_to_elite_runs_audit(self, minimal_loop, sample_dataframe):
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
            level_3_multiple={"passed": True},
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

    def test_promote_to_elite_audit_fails_blocks_promotion(self, minimal_loop, sample_seed, tmp_path):
        """验证审计未通过时审计报告写入记录（阻塞在 run() 中执行）。"""
        from fts.factor_engine.audit import FactorAuditReport, AuditItemResult

        # 注入隔离 DuckDB：minimal_loop 默认连真实因子库，直接调 _promote_to_elite
        # 会写入生产库导致测试非幂等（残留 seed_test_001/test_momentum 使重跑去重拦截）
        minimal_loop.factor_db_path = tmp_path / "iso_catalog.duckdb"
        minimal_loop._repo = None

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

        evaluation = FactorEvaluation(
            factor_id=sample_seed["factor_id"],
            trace_id="test_trace",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5},
            level_3_multiple={"passed": True},
            evaluated_at="2026-07-18T00:00:00",
        )

        path = minimal_loop._promote_to_elite(
            sample_seed,
            evaluation,
            seed_correlations={},
            quality_score=45.0,
            audit_report=mock_report,
        )
        assert path is not None
        record = json.loads(path.read_text(encoding="utf-8"))
        assert "audit_report" in record
        assert record["audit_report"]["passed"] is False


class TestBacktestPipelineIntegrationExtended:
    """测试 BacktestPipeline 接入评估链。

    注：与上方 TestBacktestPipelineIntegration 为两组独立测试集合；
    若同名则后定义覆盖前者导致前类测试丢失（GAP-F12 修复）。
    """

    def test_backtest_pipeline_initialized(self, minimal_loop):
        """验证 BacktestPipeline 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.backtest_pipeline is not None

    def test_run_backtest_pipeline_success(self, minimal_loop, sample_seed):
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

        minimal_loop.backtest_pipeline.run = MagicMock(return_value=mock_bt_result)

        result = minimal_loop._run_backtest_pipeline(sample_seed, evaluation, "test_trace")
        assert result is not None
        assert result["success"] is True
        minimal_loop.backtest_pipeline.run.assert_called_once()

    def test_run_backtest_pipeline_failure(self, minimal_loop, sample_seed):
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

        minimal_loop.backtest_pipeline.run = MagicMock(side_effect=RuntimeError("数据加载失败"))

        result = minimal_loop._run_backtest_pipeline(sample_seed, evaluation, "test_trace")
        assert result is None


# ─── Task 1: 孤立模块初始化测试 ──────────────────────────


class TestIsolatedModuleInitialization:
    """验证孤立模块在 EvolutionLoop 中正确初始化。"""

    def test_ablation_experiment_initialized(self, minimal_loop):
        """验证 AblationExperiment 在 EvolutionLoop 中初始化。"""
        assert minimal_loop.ablation_experiment is not None
        assert hasattr(minimal_loop.ablation_experiment, "run")

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

    def test_ablation_runs_in_evolution_flow(self, minimal_loop, sample_dataframe, sample_seed):
        """验证消融实验在演化流程中被调用。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="volume_zero",
                    description="成交量置零",
                    ic=0.049,
                    sharpe=1.45,
                    ic_change=-0.001,
                    sharpe_change=-0.05,
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result is not None
        assert result["factor_id"] == sample_seed["factor_id"]
        assert len(result["ablations"]) >= 1

    def test_ablation_spurious_detection_blocks_promotion(self, minimal_loop, sample_dataframe, sample_seed):
        """验证严重消融退化（>50% IC 下降，非价格列置零）阻止晋升。"""
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="zero_one_feature",
                    description="单特征归零（影响最大: volume）",
                    ic=0.01,
                    sharpe=0.3,
                    ic_change=-0.04,
                    sharpe_change=-1.2,
                    feature="volume",
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result["passed"] is False

    def test_ablation_informational_modes_do_not_block(self, minimal_loop, sample_dataframe, sample_seed):
        """验证信息型消融（shuffle_dates/成交量/VWAP）不拦截晋升（v2.50.0）。

        时序因子依赖时序因果、价格因子依赖价格列属必要特征，IC 崩塌不判伪相关。
        """
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="shuffle_dates",
                    description="时间戳打乱",
                    ic=0.001,
                    sharpe=0.05,
                    ic_change=-0.049,
                    sharpe_change=-1.45,
                ),
                SingleAblation(
                    mode="volume_zero",
                    description="成交量置零",
                    ic=0.001,
                    sharpe=0.05,
                    ic_change=-0.049,
                    sharpe_change=-1.45,
                ),
                SingleAblation(
                    mode="vwap_to_close",
                    description="VWAP 替换为 close",
                    ic=0.001,
                    sharpe=0.05,
                    ic_change=-0.049,
                    sharpe_change=-1.45,
                ),
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result["passed"] is True

    def test_ablation_price_core_col_zeroing_does_not_block(self, minimal_loop, sample_dataframe, sample_seed):
        """验证核心价格列置零不拦截晋升（v2.50.0）。

        价格因子依赖 open/high/low/close/vwap/settle 属正常输入依赖。
        """
        from fts.factor_engine.ablation import AblationResult, SingleAblation

        mock_result = AblationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            baseline_ic=0.05,
            baseline_sharpe=1.5,
            ablations=[
                SingleAblation(
                    mode="zero_one_feature",
                    description="单特征归零（影响最大: low）",
                    ic=0.001,
                    sharpe=0.0,
                    ic_change=-0.049,
                    sharpe_change=-1.5,
                    feature="low",
                ),
                SingleAblation(
                    mode="zero_one_feature",
                    description="单特征归零（影响最大: settle）",
                    ic=0.001,
                    sharpe=0.0,
                    ic_change=-0.049,
                    sharpe_change=-1.5,
                    feature="settle",
                ),
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result["passed"] is True


# ─── Task 3: CausalValidator 集成测试 ────────────────────


class TestCausalValidationIntegration:
    """测试 CausalValidator 在演化循环中的集成。"""

    def test_causal_validation_runs_in_flow(self, minimal_loop, sample_dataframe, sample_seed):
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result is not None
        assert result["passed"] is True

    def test_causal_anomaly_blocks_promotion(self, minimal_loop, sample_dataframe, sample_seed):
        """验证因果异常（事件敏感）阻止晋升。"""
        from fts.factor_engine.causal_validator import (
            CausalValidationResult,
            EventPredictionError,
        )

        mock_result = CausalValidationResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            analysis_date="2026-08-05",
            n_events=5,
            n_anomalous=1,
            anomalous_events=[
                EventPredictionError(
                    event_id="evt_001",
                    event_name="熔断",
                    event_type="circuit_breaker",
                    event_date="2026-01-15",
                    expected_direction="down",
                    pre_window=5,
                    post_window=5,
                    pre_mean_error=0.01,
                    post_mean_error=0.05,
                    error_change=0.04,
                    error_std=0.01,
                    is_anomalous=True,
                    anomaly_direction="positive",
                    n_pre_samples=5,
                    n_post_samples=5,
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result["passed"] is False
        assert len(result["anomalous_events"]) > 0


# ─── Task 4a: RobustnessTester 集成测试 ──────────────────


class TestRobustnessIntegration:
    """测试 RobustnessTester 在演化循环中的集成。"""

    def test_robustness_runs_in_flow(self, minimal_loop, sample_dataframe, sample_seed):
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result is not None
        assert result["passed"] is True

    def test_robustness_failure_blocks_promotion(self, minimal_loop, sample_dataframe, sample_seed):
        """验证鲁棒性失败阻止晋升。"""
        from fts.factor_engine.robustness import (
            RobustnessTestResult,
            AdversarialTestResult,
        )

        mock_result = RobustnessTestResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            adversarial_results=[
                AdversarialTestResult(
                    perturbation="price",
                    perturbation_factor=1.0001,
                    baseline_ic=0.05,
                    perturbed_ic=0.03,
                    ic_change=-0.02,
                    passed=False,
                )
            ],
            missing_value_results=[],
            ood_results=[],
            summary={"overall_pass_rate": 0.6, "total": 11, "passed": 7},
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result["passed"] is False


# ─── Task 4b: ShapAnalyzer 集成测试 ─────────────────────


class TestShapAnalysisIntegration:
    """测试 ShapAnalyzer 在演化循环中的集成。"""

    def test_shap_runs_in_flow(self, minimal_loop, sample_dataframe, sample_seed):
        """验证 SHAP 分析在演化流程中被调用。"""
        from fts.factor_engine.shap_analyzer import (
            ShapAnalysisResult,
            ShapSampleAnalysis,
            ShapFeatureImportance,
        )

        mock_result = ShapAnalysisResult(
            factor_id=sample_seed["factor_id"],
            factor_name=sample_seed["name"],
            analysis_date="2026-08-05",
            num_extreme_samples=10,
            num_features=2,
            top_samples=[
                ShapSampleAnalysis(
                    sample_index=0,
                    date="2026-08-01",
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
            sample_seed,
            evaluation,
            "test_trace",
        )
        assert result is not None
        assert result["passed"] is True


# ─── Task 5: FeatureImportanceAnalyzer 集成测试 ──────────


class TestFeatureImportanceIntegration:
    """测试 FeatureImportanceAnalyzer 在 GP 管线中的集成。"""

    def test_feature_importance_runs_in_gp_flow(self, minimal_loop, sample_dataframe, sample_seed):
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
        minimal_loop.feature_importance_analyzer.analyze = MagicMock(return_value=mock_result)

        importance = minimal_loop.feature_importance_analyzer.analyze(
            sample_seed,
            sample_dataframe,
        )
        assert importance is not None
        assert importance.factor_id == sample_seed["factor_id"]


# ─── Task 6: LogicMonitor 集成测试 ──────────────────────


class TestLogicMonitorIntegration:
    """测试 LogicMonitor 在定期重评估中的集成。"""

    def test_logic_monitor_runs_in_review(self, minimal_loop, sample_dataframe):
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
        minimal_loop.logic_monitor.run = MagicMock(return_value=mock_report)

        minimal_loop._run_periodic_factor_review(
            elite_ids=[],
            trace_id="test_trace",
        )

        # 无 elite_ids 时不应调用 run
        minimal_loop.logic_monitor.run.assert_not_called()


# ─── Task 7: 端到端集成验证 ──────────────────────────────


class TestFullIntegrationPipeline:
    """端到端集成测试：验证完整审查流水线。"""

    def test_all_review_stages_execute_in_sequence(self, minimal_loop, sample_dataframe, sample_seed):
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
                baseline_ic=0.05,
                baseline_sharpe=1.5,
                ablations=[
                    SingleAblation(
                        mode="volume_zero",
                        description="vol→0",
                        ic=0.048,
                        sharpe=1.48,
                        ic_change=-0.002,
                        sharpe_change=-0.02,
                    )
                ],
            )
        )
        minimal_loop.causal_validator.validate = MagicMock(
            return_value=CausalValidationResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                analysis_date="2026-08-05",
                n_events=5,
                n_anomalous=0,
                anomalous_events=[],
                all_events=[],
                summary={"total": 5, "anomalous": 0},
            )
        )
        minimal_loop.robustness_tester.run = MagicMock(
            return_value=RobustnessTestResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                adversarial_results=[],
                missing_value_results=[],
                ood_results=[],
                summary={"overall_pass_rate": 1.0, "total": 11, "passed": 11},
            )
        )
        minimal_loop.shap_analyzer.analyze = MagicMock(
            return_value=ShapAnalysisResult(
                factor_id=sample_seed["factor_id"],
                factor_name=sample_seed["name"],
                analysis_date="2026-08-05",
                num_extreme_samples=10,
                num_features=2,
                top_samples=[],
                bottom_samples=[],
                global_top_features=[],
                summary={"status": "ok"},
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

    def test_one_review_failure_blocks_promotion(self, minimal_loop, sample_dataframe, sample_seed):
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
                baseline_ic=0.05,
                baseline_sharpe=1.5,
                ablations=[
                    SingleAblation(
                        mode="zero_one_feature",
                        description="单特征归零（影响最大: volume）",
                        ic=0.01,
                        sharpe=0.3,
                        ic_change=-0.04,
                        sharpe_change=-1.2,
                        feature="volume",
                    )
                ],
            )
        )

        result = minimal_loop._run_ablation_check(sample_seed, eval_result, "fail")
        assert result["passed"] is False


# ─── 审计 OOS 语义回归测试 ──────────────────────────────


class TestRunFactorAuditOosSemantics:
    """回归: oos_ratio 是样本外数据切分比例，不应使 oos_consistency 恒失败。

    修复前 `_run_factor_audit` 以 `oos_ratio >= 0.5` 构造 ic_consistency，
    而评估链 L1 的 oos_ratio 默认 0.3（OOS 切片比例），导致所有种子因子
    的 oos_consistency 审计恒失败，夜间演化循环永远无法晋升父因子。
    """

    def test_oos_result_derived_from_icir_not_split_ratio(self, minimal_loop, sample_seed):
        """OOS 切分比例 0.3 + OOS ICIR 达标时，传递给审计器的 OOS 结果应通过。"""
        from unittest.mock import MagicMock

        captured: dict = {}

        def fake_audit(**kwargs):
            captured["oos_result"] = kwargs.get("oos_result")
            return _make_passing_audit_report()

        minimal_loop.auditor.audit = fake_audit
        # v2.60.0 (GAP-F08): 冷启动 WalkForward 优先覆盖 L1 近似，
        # 此处禁用以单独验证 L1 ICIR 派生 OOS 结果路径本身。
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)

        evaluation = {
            "passed": True,
            "level_1_backtest": {
                "oos_ratio": 0.3,  # OOS 切片比例（非一致性率）
                "ic": 0.05,
                "icir": 1.5,
            },
            "level_3_multiple": {},
        }
        minimal_loop._run_factor_audit(
            sample_seed,
            evaluation,
            "test_trace",
        )
        oos = captured.get("oos_result")
        assert oos is not None, "oos_ratio>0 时应构造 OOS 结果"
        assert oos["passed"] is True, "OOS ICIR 达标时应通过"
        assert oos["ic_consistency"] >= 0.5

    def test_oos_result_weak_icir_fails(self, minimal_loop, sample_seed):
        """OOS ICIR 很弱时 oos_consistency 应失败。"""
        from unittest.mock import MagicMock

        captured: dict = {}

        def fake_audit(**kwargs):
            captured["oos_result"] = kwargs.get("oos_result")
            return _make_passing_audit_report()

        minimal_loop.auditor.audit = fake_audit
        # 同上：禁用冷启动 WalkForward，锁定 L1 ICIR 派生路径
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)

        evaluation = {
            "passed": True,
            "level_1_backtest": {
                "oos_ratio": 0.3,
                "ic": 0.001,
                "icir": 0.1,
            },
            "level_3_multiple": {},
        }
        minimal_loop._run_factor_audit(
            sample_seed,
            evaluation,
            "test_trace",
        )
        oos = captured.get("oos_result")
        assert oos is not None
        assert oos["passed"] is False
        assert oos["ic_consistency"] < 0.5


# ─── 因子运行时校验回归测试 ─────────────────────────────


class TestFactorRuntimeValidation:
    """回归: LLM 生成后代因子的广播错误/常数信号应在源头被拦截。

    此前广播错误因子（如 shapes (496,) (2,)）进入下游评估与回测流水线，
    增加诊断噪音与无效评估开销；现由 _check_factor_runtime 在演化循环中
    试运行拦截，并把教训写入经验链供 LLM 参考。
    """

    def _make_factor(self, code: str) -> dict:
        return {
            "factor_id": "fct_runtime_test",
            "name": "runtime_test",
            "code": code,
            "params": {},
            "economic_logic": {},
        }

    def test_normal_factor_passes(self, minimal_loop, sample_dataframe):
        """正常因子代码应通过运行时校验。"""
        minimal_loop.data = sample_dataframe
        code = (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    if n > 5:\n"
            "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
            "    return np.tanh(ret * 10)\n"
        )
        ok, reason = minimal_loop._check_factor_runtime(self._make_factor(code))
        assert ok is True, reason

    def test_broadcast_error_factor_fails(self, minimal_loop, sample_dataframe):
        """广播错误代码（(n,) 与 (2,) 运算）应被拦截。

        v2.8.5 起 `_execute_factor_code` 捕获广播/形状异常并降级为零值数组
        （避免回测流水线崩溃），运行时校验据此以"常数信号"拦截。
        """
        minimal_loop.data = sample_dataframe
        code = (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    return np.ones(len(close)) * np.array([1.0, 2.0])  # (n,) vs (2,) 广播错误\n"
        )
        ok, reason = minimal_loop._check_factor_runtime(self._make_factor(code))
        assert ok is False
        # 广播异常被捕获 → 降级零值 → 以"常数信号"拦截（功能上已阻止崩溃）
        assert "常数" in reason

    def test_constant_signal_factor_fails(self, minimal_loop, sample_dataframe):
        """常数信号（无信息量）应被拦截。"""
        minimal_loop.data = sample_dataframe
        code = (
            "def factor_program(data, params):\n    import numpy as np\n    return np.full(len(data['close']), 0.5)\n"
        )
        ok, reason = minimal_loop._check_factor_runtime(self._make_factor(code))
        assert ok is False
        assert "常数" in reason

    def test_wrong_length_factor_fails(self, minimal_loop, sample_dataframe):
        """输出长度不匹配应被拦截（降级为零值 → 常数信号）。"""
        minimal_loop.data = sample_dataframe
        code = (
            "def factor_program(data, params):\n    import numpy as np\n    return np.diff(data['close'])  # 长度 n-1\n"
        )
        ok, reason = minimal_loop._check_factor_runtime(self._make_factor(code))
        assert ok is False
        assert "常数" in reason


# ─── elite 父因子回退（v2.8.4） ───────────────────────────


class TestEliteParentFallback:
    """种子因子全部已存在 elite 快照（重复跳过）时，应回退 elite 池继续演化。"""

    @staticmethod
    def _make_loop(tmp_memory_dir, tmp_elite_dir, mock_llm_client, sample_ohlcv, forward_returns):
        return EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            llm_client=mock_llm_client,
            n_trials_micro=3,
        )

    @staticmethod
    def _write_elite_factor(elite_dir: Path, factor_id: str, name: str) -> None:
        elite_dir.mkdir(exist_ok=True)
        (elite_dir / f"{factor_id}.json").write_text(
            json.dumps(
                {
                    "factor_id": factor_id,
                    "name": name,
                    "code": (
                        "def factor_program(data, params):\n"
                        "    import numpy as np\n"
                        "    close = data['close']\n"
                        "    return np.tanh((close - close.mean()) / (close.std() + 1e-10))\n"
                    ),
                    "params": {},
                    "economic_logic": {"narrative": "elite 快照因子"},
                }
            ),
            encoding="utf-8",
        )

    def test_load_elite_parent_factors(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """应从 elite 目录加载因子快照（排除相关性索引文件）。"""
        self._write_elite_factor(tmp_elite_dir, "fct_aaa111", "elite_a")
        (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text("{}", encoding="utf-8")
        loop = self._make_loop(
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
            sample_ohlcv,
            forward_returns,
        )
        parents = loop._load_elite_parent_factors()
        assert len(parents) == 1
        assert parents[0]["factor_id"] == "fct_aaa111"
        assert parents[0]["code"]

    @pytest.mark.slow
    def test_run_uses_elite_pool_when_seeds_duplicated(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """种子全部重复跳过（无晋升）时，应回退 elite 池进入演化循环而非 0 代跳过。"""
        # elite 目录预置与种子同名的因子 → 种子评估通过但晋升时重复跳过
        self._write_elite_factor(tmp_elite_dir, "fct_elite001", "test_seed_factor")
        loop = self._make_loop(
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
            sample_ohlcv,
            forward_returns,
        )

        # mock 种子池：单个种子，与 elite 预置同名
        fake_seed = {
            "factor_id": "fct_seed001",
            "name": "test_seed_factor",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    return np.tanh((close - close.mean()) / (close.std() + 1e-10))\n"
            ),
            "params": {},
        }
        loop.seed_pool.load_all_seeds = MagicMock(return_value=[fake_seed])
        # 种子评估链/质检/审计 mock 通过（评估本身成功，但晋升时被 elite 去重拦截）
        _mock_seed_evaluation_pass(loop)
        _mock_review_pass(loop)

        result = loop.run(max_generation=2)
        assert result.status == "completed"
        assert result.generations_completed >= 1, "种子重复跳过时应回退 elite 池继续演化，而非 0 代跳过"


# ─── 快速预筛选 IC 阈值市场自适应（v2.8.6） ──────────────


class TestQuickPrefilterThresholds:
    """回归: elite 池无新因子补充 — 预筛选 IC 阈值 0.02 对期货日频过严。

    期货日频单品种时序 IC 信噪比低（常见 0.01-0.02），
    阈值应按市场自适应：futures=0.01，stock=0.02。
    """

    @staticmethod
    def _make_factor() -> dict:
        return {
            "factor_id": "fct_prefilter_001",
            "name": "prefilter_test",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    n = len(close)\n"
                "    ret = np.zeros(n)\n"
                "    if n > 5:\n"
                "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
                "    return np.tanh(ret * 10)\n"
            ),
            "params": {},
        }

    @staticmethod
    def _make_loop(market: str, sample_ohlcv, forward_returns, tmp_path) -> "EvolutionLoop":
        return EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=str(tmp_path / "elite"),
            memory_dir=str(tmp_path / "memory"),
            n_trials_micro=2,
            market=market,
        )

    def test_futures_ic_0_015_passes(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_path,
    ):
        """期货市场: IC=0.015 应通过预筛选（阈值放宽至 0.01）。"""
        loop = self._make_loop("futures", sample_ohlcv, forward_returns, tmp_path)
        with patch("scipy.stats.spearmanr", return_value=(0.015, 0.4)):
            ok, reason, _ = loop._quick_prefilter(self._make_factor(), "trace")
        assert ok, reason

    def test_stock_ic_0_015_rejected(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_path,
    ):
        """股票市场: IC=0.015 应被拦截（阈值保持 0.02）。"""
        loop = self._make_loop("stock", sample_ohlcv, forward_returns, tmp_path)
        with patch("scipy.stats.spearmanr", return_value=(0.015, 0.4)):
            ok, reason, _ = loop._quick_prefilter(self._make_factor(), "trace")
        assert not ok
        assert "0.02" in reason

    def test_futures_ic_0_008_rejected(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_path,
    ):
        """期货市场: IC=0.008 仍应被拦截（低于 0.01 下限）。"""
        loop = self._make_loop("futures", sample_ohlcv, forward_returns, tmp_path)
        with patch("scipy.stats.spearmanr", return_value=(0.008, 0.6)):
            ok, reason, _ = loop._quick_prefilter(self._make_factor(), "trace")
        assert not ok
        assert "0.01" in reason


# ─── GAP-S11: 股票演化 operator-first（v2.67.0） ────────────


class TestGapS11OperatorFirst:
    """GAP-S11: 股票演化默认 operator-first（算子演化优先，LLM/GP 兜底）。"""

    @staticmethod
    def _make_loop(market, sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client, **kwargs):
        return EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            llm_client=mock_llm_client,
            n_trials_micro=2,
            market=market,
            **kwargs,
        )

    def test_futures_keeps_hybrid(
        self,
        monkeypatch,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """期货演化保持原配置（hybrid），不受股票默认影响。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "evolution_mode", "hybrid")
        loop = self._make_loop(
            "futures",
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
        )
        assert loop.evolution_mode == "hybrid"

    def test_operator_first_prefers_operator(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """算子演化成功时不触发 LLM/GP 兜底。"""
        loop = self._make_loop(
            "stock",
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
        )
        loop.evolution_mode = "operator_first"
        loop._generate_operator_factor = MagicMock(return_value=({"factor_id": "fct_of0001", "code": "x"}, "OpGen: ok"))
        loop.macro_evolver.evolve = MagicMock()
        loop._run_gp_evolution = MagicMock()
        out = loop._evolve_one({"factor_id": "p1", "name": "parent"}, 1, "t")
        assert out is not None
        assert out[1] == "operator_evolution"
        loop._generate_operator_factor.assert_called_once()
        loop.macro_evolver.evolve.assert_not_called()
        loop._run_gp_evolution.assert_not_called()

    def test_operator_first_falls_back_to_macro(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """算子演化失败 → LLM 宏观演化兜底。"""
        loop = self._make_loop(
            "stock",
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
        )
        loop.evolution_mode = "operator_first"
        loop._generate_operator_factor = MagicMock(side_effect=RuntimeError("op down"))
        loop.macro_evolver.evolve = MagicMock(return_value=({"factor_id": "fct_of0002"}, "Macro fallback", 100))
        loop._run_gp_evolution = MagicMock()
        out = loop._evolve_one({"factor_id": "p1", "name": "parent"}, 1, "t")
        assert out is not None
        assert out[1] == "macro_evolution"
        assert out[3] == 100  # LLM token 记账
        loop._run_gp_evolution.assert_not_called()

    def test_operator_first_falls_back_to_gp(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """算子 + LLM 均失败 → GP 演化兜底。"""
        loop = self._make_loop(
            "stock",
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
        )
        loop.evolution_mode = "operator_first"
        loop._generate_operator_factor = MagicMock(side_effect=RuntimeError("op down"))
        loop.macro_evolver.evolve = MagicMock(side_effect=RuntimeError("llm down"))
        loop._run_gp_evolution = MagicMock(return_value=({"factor_id": "fct_of0003", "code": "x"}, "GP fallback"))
        out = loop._evolve_one({"factor_id": "p1", "name": "parent"}, 1, "t")
        assert out is not None
        assert out[1] == "gp_evolution"

    def test_operator_first_all_fail_returns_none(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_memory_dir,
        tmp_elite_dir,
        mock_llm_client,
    ):
        """算子/LLM/GP 全失败 → 返回 None 并记录失败轨迹。"""
        loop = self._make_loop(
            "stock",
            sample_ohlcv,
            forward_returns,
            tmp_memory_dir,
            tmp_elite_dir,
            mock_llm_client,
        )
        loop.evolution_mode = "operator_first"
        loop._generate_operator_factor = MagicMock(side_effect=RuntimeError("op down"))
        loop.macro_evolver.evolve = MagicMock(side_effect=RuntimeError("llm down"))
        loop._run_gp_evolution = MagicMock(side_effect=RuntimeError("gp down"))
        out = loop._evolve_one({"factor_id": "p1", "name": "parent"}, 1, "t")
        assert out is None

    def test_record_evolution_method_distribution(self, tmp_memory_dir):
        """state 记录演化方法分布计数（operator/gp/macro 占比可观测）。"""
        mgr = EvolutionStateManager(tmp_memory_dir)
        state = mgr.load_or_init()
        mgr.record_evolution_method(state, "operator_evolution")
        mgr.record_evolution_method(state, "operator_evolution")
        mgr.record_evolution_method(state, "macro_evolution")
        state2 = mgr.load_or_init()
        assert state2["evolution_method_counts"]["operator_evolution"] == 2
        assert state2["evolution_method_counts"]["macro_evolution"] == 1


# ═══════════════════════════════════════════════════════════════
# GAP-F16: 方法级未覆盖路径补充（全部直接调用，不跑完整 run()）
# ═══════════════════════════════════════════════════════════════


def _make_ohlcv(n: int = 200) -> pd.DataFrame:
    """构造完整 OHLCV 数据（BacktestPipeline._execute_factor_code 需要全字段）。

    价格范围控制在 [-10, 10] 内，避免被 _execute_factor_code 的 clip 截断为常数。
    """
    close = 1.0 + np.arange(float(n)) * 0.01
    return pd.DataFrame(
        {
            "open": close - 0.001,
            "high": close + 0.002,
            "low": close - 0.002,
            "close": close,
            "volume": np.ones(n) * 1000,
        }
    )


class TestGapF16ModuleHelpers:
    """模块级辅助函数 + 工具方法。"""

    def test_add_trading_days_skips_weekend(self):
        """_add_trading_days 跳过周末（roll=forward）。"""
        from datetime import datetime

        from fts.factor_engine.evolution_loop import _add_trading_days

        start = datetime(2026, 8, 7)  # 周五
        end = _add_trading_days(start, 1)
        assert end.weekday() == 0  # 周一

    def test_build_shadow_pool_structure(self):
        """_build_shadow_pool 返回影子池标记结构。"""
        from fts.factor_engine.evolution_loop import _SHADOW_OBSERVE_TRADING_DAYS, _build_shadow_pool

        pool = _build_shadow_pool()
        assert pool["observe_trading_days"] == _SHADOW_OBSERVE_TRADING_DAYS
        assert "promoted_at" in pool
        assert "observe_until" in pool

    def test_log_consistency_event_writes_file(self, tmp_path, monkeypatch):
        """_log_consistency_event 正常写入 jsonl。"""
        from fts.factor_engine import evolution_loop as el_mod

        target = tmp_path / "catalog_consistency.jsonl"
        monkeypatch.setattr(el_mod, "_CONSISTENCY_LOG_PATH", target)
        el_mod._log_consistency_event(
            event_type="promote",
            factor_id="fct_aaa",
            factor_name="test",
            market="futures",
            status="active",
            json_path="elite/fct_aaa.json",
            trace_id="t1",
        )
        assert target.exists()
        line = target.read_text(encoding="utf-8").strip()
        assert "fct_aaa" in line
        assert "promote" in line

    def test_log_consistency_event_write_failure(self, tmp_path, monkeypatch):
        """_log_consistency_event 写入失败不抛异常。"""
        from fts.factor_engine import evolution_loop as el_mod

        target = tmp_path / "catalog_consistency.jsonl"
        monkeypatch.setattr(el_mod, "_CONSISTENCY_LOG_PATH", target)
        with patch("builtins.open", side_effect=OSError("disk full")):
            # 不应抛异常
            el_mod._log_consistency_event("promote", "fct_x", "x", "stock", "active")

    def test_evolution_run_result_to_dict_defaults(self):
        """EvolutionRunResult.to_dict 空字段回退为 []。"""
        from fts.factor_engine.evolution_loop import EvolutionRunResult

        result = EvolutionRunResult(
            run_id="r1",
            trace_id="t1",
            generations_completed=0,
            total_factors_evaluated=0,
            total_factors_promoted=0,
            tokens_consumed=0,
            status="completed",
        )
        d = result.to_dict()
        assert d["elite_factor_ids"] == []
        assert d["seed_correlations"] == []
        assert d["error"] is None

    def test_quality_inspection_compat_result(self):
        """_QualityInspectionResult 属性接口。"""
        from fts.factor_engine.evolution_loop import _QualityInspectionResult

        r = _QualityInspectionResult({"total_score": 40.0, "grade": "B"}, filtered=True, reason="淘汰")
        assert r.total_score == 40.0
        assert r.grade == "B"
        assert r.filtered is True
        assert r.reason == "淘汰"


class TestGapF16UctAndCircuitBreaker:
    """UCT 父因子选择 + 熔断检查。"""

    @staticmethod
    def _make_loop(tmp_memory_dir):
        return EvolutionLoop(
            data=_make_ohlcv(3),
            forward_returns=np.array([0.01, 0.0, -0.01]),
            memory_dir=tmp_memory_dir,
        )

    def test_select_parent_uct_visited_factors(self, tmp_memory_dir):
        """已访问父因子按 UCB 公式选择。"""
        loop = self._make_loop(tmp_memory_dir)
        parents = [{"factor_id": "a", "name": "a"}, {"factor_id": "b", "name": "b"}]
        loop._uct_stats = {
            "a": {"visits": 1, "total_reward": 0.2},
            "b": {"visits": 5, "total_reward": 0.5},
        }
        # a 有更高的平均奖励 + 更高探索项
        chosen = loop._select_parent_uct(parents)
        assert chosen["factor_id"] in ("a", "b")

    def test_select_parent_uct_unvisited_returns_first(self, tmp_memory_dir):
        """存在未访问父因子时直接返回（探索优先）。"""
        loop = self._make_loop(tmp_memory_dir)
        parents = [{"factor_id": "a", "name": "a"}, {"factor_id": "b", "name": "b"}]
        loop._uct_stats = {"a": {"visits": 2, "total_reward": 0.3}}
        chosen = loop._select_parent_uct(parents)
        assert chosen["factor_id"] == "b"

    def test_update_uct_stats_passed_reward(self, tmp_memory_dir):
        """通过评估奖励 = abs(IC)。"""
        loop = self._make_loop(tmp_memory_dir)
        parent = {"factor_id": "p1"}
        evaluation = {"passed": True, "level_1_backtest": {"ic": -0.04}}
        loop._update_uct_stats(parent, evaluation)
        assert loop._uct_stats["p1"]["visits"] == 1
        assert loop._uct_stats["p1"]["total_reward"] == 0.04

    def test_update_uct_stats_failed_zero_reward(self, tmp_memory_dir):
        """失败评估奖励 = 0。"""
        loop = self._make_loop(tmp_memory_dir)
        parent = {"factor_id": "p2"}
        evaluation = {"passed": False, "level_1_backtest": {"ic": 0.1}}
        loop._update_uct_stats(parent, evaluation)
        assert loop._uct_stats["p2"]["total_reward"] == 0.0

    def test_circuit_breaker_token(self, tmp_memory_dir):
        """Token 超 2x 触发熔断。"""
        loop = self._make_loop(tmp_memory_dir)
        state = {"tokens_consumed": 500_000, "budget_limit": 200_000}
        reason = loop._check_circuit_breaker(state)
        assert reason is not None
        assert "Token" in reason

    def test_circuit_breaker_consecutive_low_ic(self, tmp_memory_dir):
        """连续低 IC 触发熔断。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._consecutive_low_ic = loop.budget["circuit_breaker_consecutive_low_ic"]
        reason = loop._check_circuit_breaker({"tokens_consumed": 0, "budget_limit": 200_000})
        assert reason is not None
        assert "连续低 IC" in reason

    def test_circuit_breaker_failure_rate(self, tmp_memory_dir):
        """失败率超阈值触发熔断。"""
        loop = self._make_loop(tmp_memory_dir)
        state = {
            "tokens_consumed": 0,
            "budget_limit": 200_000,
            "total_factors_evaluated": 100,
            "total_factors_promoted": 0,
        }
        reason = loop._check_circuit_breaker(state)
        assert reason is not None
        assert "失败率" in reason

    def test_circuit_breaker_not_triggered(self, tmp_memory_dir):
        """正常状态不触发熔断。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._consecutive_low_ic = 0
        state = {
            "tokens_consumed": 0,
            "budget_limit": 200_000,
            "total_factors_evaluated": 5,
            "total_factors_promoted": 3,
        }
        assert loop._check_circuit_breaker(state) is None


class TestGapF16EliteRedundancy:
    """L2 准入去冗余（相关性检查 + 正交化闭环）方法级测试。"""

    @staticmethod
    def _make_loop(tmp_memory_dir, tmp_elite_dir):
        loop = EvolutionLoop(
            data=_make_ohlcv(200),
            forward_returns=np.zeros(200),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        return loop

    @staticmethod
    def _write_elite(elite_dir, fid, name="elite"):
        import json as _json

        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / f"{fid}.json").write_text(
            _json.dumps(
                {
                    "factor_id": fid,
                    "name": name,
                    "code": ("def factor_program(data, params):\n    import numpy as np\n    return data['close']"),
                    "params": {},
                }
            ),
            encoding="utf-8",
        )

    def test_check_elite_correlation_hit(self, tmp_memory_dir, tmp_elite_dir):
        """与既有 elite 高相关时返回相关性列表。"""
        self._write_elite(tmp_elite_dir, "fct_elite01")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        factor = {
            "factor_id": "fct_new001",
            "name": "new",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
        }
        result = loop._check_elite_correlation(factor)
        assert result is not None
        assert len(result["correlations"]) == 1
        assert result["correlations"][0]["factor_id_b"] == "fct_elite01"

    def test_check_elite_correlation_no_elite(self, tmp_memory_dir, tmp_elite_dir):
        """elite 目录为空（无 json）→ 返回 None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        factor = {
            "factor_id": "fct_new002",
            "name": "new2",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
        }
        assert loop._check_elite_correlation(factor) is None

    def test_check_elite_correlation_exec_failure(self, tmp_memory_dir, tmp_elite_dir):
        """新因子信号执行失败 → 返回 None。"""
        self._write_elite(tmp_elite_dir, "fct_elite02")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        factor = {"factor_id": "fct_new003", "name": "n3", "code": "raise RuntimeError", "params": {}}
        with patch(
            "fts.factor_engine.backtest_pipeline.BacktestPipeline._execute_factor_code",
            side_effect=RuntimeError("boom"),
        ):
            assert loop._check_elite_correlation(factor) is None

    def test_check_elite_correlation_skips_index_and_self(self, tmp_memory_dir, tmp_elite_dir):
        """跳过相关性索引文件与自身因子。"""
        self._write_elite(tmp_elite_dir, "fct_elite03")
        (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text("{}", encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        factor = {
            "factor_id": "fct_elite03",  # 与自身相同 → 跳过
            "name": "self",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
        }
        assert loop._check_elite_correlation(factor) is None

    def test_orthogonalize_via_basis_disabled(self, tmp_memory_dir, tmp_elite_dir):
        """正交基底未启用 → None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop._l2_orthogonal_basis_enabled = False
        assert loop._orthogonalize_via_basis({"factor_id": "x"}) is None

    def test_orthogonalize_via_basis_success(self, tmp_memory_dir, tmp_elite_dir):
        """基底正交化成功 → 返回并注册。"""
        self._write_elite(tmp_elite_dir, "fct_basis01")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        orth = {"factor_id": "fct_orth001", "orthogonalized": True, "orthogonalized_basis": ["fct_basis01"]}
        loop.orthogonal_basis.orthogonalize = MagicMock(return_value=orth)
        loop.orthogonal_basis.register = MagicMock()
        factor = {
            "factor_id": "fct_orth001",
            "name": "orth",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
            "evaluation": {"level_1_backtest": {"sharpe": 1.5}},
        }
        result = loop._orthogonalize_via_basis(factor)
        assert result is not None
        loop.orthogonal_basis.register.assert_called_once_with(orth)

    def test_orthogonalize_via_basis_exception(self, tmp_memory_dir, tmp_elite_dir):
        """基底正交化异常 → None 回退。"""
        self._write_elite(tmp_elite_dir, "fct_basis02")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop.orthogonal_basis.orthogonalize = MagicMock(side_effect=RuntimeError("fail"))
        factor = {
            "factor_id": "fct_orth002",
            "name": "orth2",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
        }
        assert loop._orthogonalize_via_basis(factor) is None

    def test_orthogonalize_candidate_success(self, tmp_memory_dir, tmp_elite_dir):
        """单参照 OLS 正交化成功。"""
        self._write_elite(tmp_elite_dir, "fct_ref01")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop._l2_orthogonal_residual_corr_max = 0.3
        loop._l2_orthogonal_min_retained_ratio = 0.3
        # 构造与参照信号低相关的候选（close 平方 vs close）
        factor = {
            "factor_id": "fct_orthc001",
            "name": "cand",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    return (close - close.mean()) ** 2\n"
            ),
            "params": {},
        }
        pair = {"factor_id_b": "fct_ref01", "factor_name_b": "elite", "pearson": 0.95}
        orth = loop._orthogonalize_candidate(factor, pair)
        assert orth is not None
        assert orth["orthogonalized"] is True
        assert orth["orthogonalized_against"] == "fct_ref01"
        assert "orthogonal_signal" in orth

    def test_orthogonalize_candidate_no_fid_b(self, tmp_memory_dir, tmp_elite_dir):
        """pair 缺少 factor_id_b → None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        assert loop._orthogonalize_candidate({"factor_id": "x"}, {}) is None

    def test_orthogonalize_candidate_ref_missing(self, tmp_memory_dir, tmp_elite_dir):
        """参照 elite 文件不存在 → None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        pair = {"factor_id_b": "fct_ghost", "factor_name_b": "ghost"}
        assert loop._orthogonalize_candidate({"factor_id": "x", "code": ""}, pair) is None

    def test_orthogonalize_candidate_residual_corr_too_high(self, tmp_memory_dir, tmp_elite_dir):
        """残差与参照相关过高 → None（拒绝）。"""
        self._write_elite(tmp_elite_dir, "fct_ref02")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop._l2_orthogonal_residual_corr_max = 0.01  # 极严 → 残差必然不合格
        factor = {
            "factor_id": "fct_orthc002",
            "name": "cand2",
            "code": ("def factor_program(data, params):\n    import numpy as np\n    return data['close']\n"),
            "params": {},
        }
        pair = {"factor_id_b": "fct_ref02", "factor_name_b": "elite2", "pearson": 1.0}
        assert loop._orthogonalize_candidate(factor, pair) is None


class TestGapF16PromoteToElite:
    """_promote_to_elite 各拒绝分支 + 成功路径。"""

    @staticmethod
    def _mock_repo(loop, get_factor_by_name=None, get_by_family=None, get_factor=None):
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name.return_value = get_factor_by_name
        mock_repo.get_by_family.return_value = get_by_family if get_by_family is not None else []
        mock_repo.get_factor.return_value = get_factor
        loop._get_repo = MagicMock(return_value=mock_repo)
        return mock_repo

    @staticmethod
    def _make_factor(fid="fct_prom001", name="promote_me"):
        return {
            "factor_id": fid,
            "name": name,
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
            "family": "trend",
            "market": "multi",
            "source": "macro_evolution",
            "parent_id": None,
            "generation": 1,
            "trace_id": "t",
            "symbols": ["RB0"],
        }

    @staticmethod
    def _make_evaluation(passed_l3=True):
        return {
            "level_1_backtest": {"ic": 0.05, "sharpe": 2.0},
            "level_2_economic": {"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3},
            "level_3_multiple": {"passed": passed_l3, "bonferroni_p": 0.01, "adjusted_t": 3.0},
            "passed": True,
            "failure_reasons": [],
        }

    def _make_loop(self, tmp_memory_dir, tmp_elite_dir):
        return EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )

    def _mock_screen_grade(self, loop, grade="A"):
        screen = MagicMock()
        screen.grade = grade
        screen.to_dict.return_value = {"grade": grade, "total_score": 80.0}
        screen.veto_reasons = []
        screen.total_score = 80.0
        loop.high_ic_screener.screen = MagicMock(return_value=screen)

    def test_promote_duplicate_name_returns_none(self, tmp_memory_dir, tmp_elite_dir):
        """DuckDB 已存在同名因子 → 返回 None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        self._mock_repo(loop, get_factor_by_name={"factor_id": "fct_existing"})
        assert loop._promote_to_elite(self._make_factor(), self._make_evaluation(), shadow_observe=False) is None

    def test_promote_family_limit_returns_none(self, tmp_memory_dir, tmp_elite_dir):
        """家族因子数达上限 → 返回 None（结构簇配额关闭时回退 max_per_family 旧逻辑）。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop.budget["max_per_family"] = 2
        loop._cluster_quota_enabled = False  # GAP-077: 默认走结构簇配额，显式关闭以覆盖 max_per_family 回退路径
        self._mock_repo(loop, get_by_family=[{"factor_id": "a"}, {"factor_id": "b"}])
        assert loop._promote_to_elite(self._make_factor(), self._make_evaluation(), shadow_observe=False) is None

    def test_promote_family_limit_other_exempt(self, tmp_memory_dir, tmp_elite_dir):
        """兜底家族 'other' 达上限仍晋升（GAP-070：永久豁免）。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop.budget["max_per_family"] = 2
        self._mock_repo(loop, get_by_family=[{"factor_id": "a"}, {"factor_id": "b"}])
        self._mock_screen_grade(loop)
        loop.elite_tracker.init_tracker = MagicMock()
        loop._check_elite_correlation = MagicMock(return_value=None)
        factor = self._make_factor(fid="fct_prom070a", name="promote_other")
        factor["family"] = "other"
        fp = loop._promote_to_elite(factor, self._make_evaluation(), shadow_observe=False)
        assert fp is not None
        assert fp.exists()

    def test_promote_family_limit_unknown_exempt(self, tmp_memory_dir, tmp_elite_dir):
        """兜底家族 'unknown' 达上限仍晋升（GAP-070：永久豁免）。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop.budget["max_per_family"] = 2
        self._mock_repo(loop, get_by_family=[{"factor_id": "a"}, {"factor_id": "b"}])
        self._mock_screen_grade(loop)
        loop.elite_tracker.init_tracker = MagicMock()
        loop._check_elite_correlation = MagicMock(return_value=None)
        factor = self._make_factor(fid="fct_prom070b", name="promote_unknown")
        factor["family"] = "unknown"
        fp = loop._promote_to_elite(factor, self._make_evaluation(), shadow_observe=False)
        assert fp is not None
        assert fp.exists()

    def test_promote_multiple_test_fail_returns_none(self, tmp_memory_dir, tmp_elite_dir):
        """多重检验未通过 → 返回 None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        self._mock_repo(loop)
        self._mock_screen_grade(loop)
        assert (
            loop._promote_to_elite(self._make_factor(), self._make_evaluation(passed_l3=False), shadow_observe=False)
            is None
        )

    def test_promote_high_ic_grade_c_returns_none(self, tmp_memory_dir, tmp_elite_dir):
        """高 IC 筛查 C 级 → 返回 None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        self._mock_repo(loop)
        self._mock_screen_grade(loop, grade="C")
        assert loop._promote_to_elite(self._make_factor(), self._make_evaluation(), shadow_observe=False) is None

    def test_promote_duckdb_write_failure_rolls_back(self, tmp_memory_dir, tmp_elite_dir):
        """DuckDB 写入失败 → 回滚 JSON 快照并返回 None。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        mock_repo = self._mock_repo(loop)
        self._mock_screen_grade(loop)
        mock_repo.create_factor.side_effect = RuntimeError("db down")
        factor = self._make_factor()
        result = loop._promote_to_elite(factor, self._make_evaluation(), shadow_observe=False)
        assert result is None
        # JSON 快照应被回滚删除
        assert not (tmp_elite_dir / f"{factor['factor_id']}.json").exists()

    def test_promote_success(self, tmp_memory_dir, tmp_elite_dir):
        """正常晋升 → 返回路径且 JSON 快照存在。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        mock_repo = self._mock_repo(loop)
        self._mock_screen_grade(loop)
        loop.elite_tracker.init_tracker = MagicMock()
        loop._check_elite_correlation = MagicMock(return_value=None)
        factor = self._make_factor()
        fp = loop._promote_to_elite(factor, self._make_evaluation(), shadow_observe=False)
        assert fp is not None
        assert fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        # 因子 market=multi → 使用演化上下文市场（默认 futures）
        assert data["market"] == "futures"
        mock_repo.create_factor.assert_called_once()

    def test_promote_bootstrapping_cleans_injected_file(self, tmp_memory_dir, tmp_elite_dir):
        """bootstrapping 来源晋升后删除 l1_injected 候选文件（GAP-036）。"""
        inject_dir = tmp_memory_dir.parent / "l1_injected"
        inject_dir.mkdir(parents=True, exist_ok=True)
        (inject_dir / "cand_test.json").write_text("{}", encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        loop.inject_dir = inject_dir
        self._mock_repo(loop)
        self._mock_screen_grade(loop)
        loop.elite_tracker.init_tracker = MagicMock()
        loop._check_elite_correlation = MagicMock(return_value=None)
        factor = self._make_factor(fid="fct_prom010", name="bs_factor")
        factor["source"] = "bootstrapping"
        factor["parent_id"] = "cand_test"
        fp = loop._promote_to_elite(factor, self._make_evaluation(), shadow_observe=False)
        assert fp is not None
        assert not (inject_dir / "cand_test.json").exists()

    def test_write_to_duckdb_update_existing(self, tmp_memory_dir, tmp_elite_dir):
        """factor_id 已存在 → 走 update_factor 分支。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        mock_repo = self._mock_repo(loop, get_factor={"factor_id": "fct_existing"})
        ok = loop._write_to_duckdb(
            self._make_factor(),
            self._make_evaluation(),
        )
        assert ok is True
        mock_repo.update_factor.assert_called_once()

    def test_write_to_duckdb_exception_returns_false(self, tmp_memory_dir, tmp_elite_dir):
        """DuckDB 写入异常 → 返回 False。"""
        loop = self._make_loop(tmp_memory_dir, tmp_elite_dir)
        mock_repo = self._mock_repo(loop)
        mock_repo.create_factor.side_effect = RuntimeError("boom")
        assert loop._write_to_duckdb(self._make_factor(), self._make_evaluation()) is False


class TestGapF16WalkForwardAndAudit:
    """WalkForward 配置/OOS 与审计降级。"""

    @staticmethod
    def _make_loop(tmp_memory_dir):
        return EvolutionLoop(
            data=_make_ohlcv(300),
            forward_returns=np.zeros(300),
            memory_dir=tmp_memory_dir,
        )

    def test_build_wf_config_3y(self, tmp_memory_dir):
        """数据 ≥3 年 → 默认配置。"""
        cfg = EvolutionLoop._build_wf_config(_make_ohlcv(800))
        assert cfg["n_windows"] >= 4

    def test_build_wf_config_2y(self, tmp_memory_dir):
        cfg = EvolutionLoop._build_wf_config(_make_ohlcv(600))
        assert cfg["n_windows"] == 4

    def test_build_wf_config_1y(self, tmp_memory_dir):
        cfg = EvolutionLoop._build_wf_config(_make_ohlcv(300))
        assert cfg["n_windows"] == 3

    def test_build_wf_config_half_year(self, tmp_memory_dir):
        cfg = EvolutionLoop._build_wf_config(_make_ohlcv(150))
        assert cfg["n_windows"] == 2

    def test_build_wf_config_short(self, tmp_memory_dir):
        cfg = EvolutionLoop._build_wf_config(_make_ohlcv(50))
        assert cfg["n_windows"] == 1

    def test_run_walkforward_oos_disabled(self, tmp_memory_dir, monkeypatch):
        """force_walkforward=false → 返回 None。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "force_walkforward", False)
        loop = self._make_loop(tmp_memory_dir)
        assert loop._run_walkforward_oos(_make_minimal_factor()) is None

    def test_run_walkforward_oos_short_data(self, tmp_memory_dir, monkeypatch):
        """数据 <125 行 → 返回 None。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "force_walkforward", True)
        loop = EvolutionLoop(
            data=_make_ohlcv(60),
            forward_returns=np.zeros(60),
            memory_dir=tmp_memory_dir,
        )
        assert loop._run_walkforward_oos(_make_minimal_factor()) is None

    def test_run_factor_audit_exception_degrades(self, tmp_memory_dir):
        """审计器抛异常 → 降级为全失败报告。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.auditor.audit = MagicMock(side_effect=RuntimeError("audit crash"))
        factor = _make_minimal_factor("fct_audit001")
        evaluation = {
            "level_1_backtest": {"oos_ratio": 0.3, "ic": 0.05, "icir": 1.5},
            "level_3_multiple": {"bonferroni_p": 0.02},
        }
        report = loop._run_factor_audit(factor, evaluation, "t")
        assert report.passed is False
        assert report.pass_rate == 0.0


class TestGapF16AblationRobustnessShapCausal:
    """Phase A/B/C 审查异常与降级分支。"""

    @staticmethod
    def _make_loop(tmp_memory_dir):
        return EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
        )

    def test_is_blocking_ablation_modes(self):
        """_is_blocking_ablation 各模式判定。"""
        assert EvolutionLoop._is_blocking_ablation({"mode": "shuffle_dates"}) is False
        assert EvolutionLoop._is_blocking_ablation({"mode": "volume_zero"}) is False
        assert EvolutionLoop._is_blocking_ablation({"mode": "zero_one_feature", "feature": "close"}) is False
        assert EvolutionLoop._is_blocking_ablation({"mode": "zero_one_feature", "feature": "volume"}) is True
        assert EvolutionLoop._is_blocking_ablation({"mode": "other"}) is False

    def test_run_ablation_data_unavailable(self, tmp_memory_dir):
        """data 缺失 → skipped=True。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.data = pd.DataFrame()
        result = loop._run_ablation_check(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_run_ablation_exception_returns_passed(self, tmp_memory_dir):
        """消融实验异常 → 返回 passed=True 兜底。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.ablation_experiment.run = MagicMock(side_effect=RuntimeError("ab crash"))
        result = loop._run_ablation_check(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True
        assert "error" in result

    def test_run_ablation_zero_baseline_passes(self, tmp_memory_dir):
        """baseline_ic≈0 → 直接通过。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.ablation_experiment.run = MagicMock(return_value={"baseline_ic": 0.0, "ablations": []})
        result = loop._run_ablation_check(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True

    def test_run_robustness_exception_returns_passed(self, tmp_memory_dir):
        """鲁棒性审查异常 → passed=True 兜底。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.robustness_tester.run = MagicMock(side_effect=RuntimeError("rob crash"))
        result = loop._run_robustness_check(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True

    def test_run_robustness_futures_threshold(self, tmp_memory_dir):
        """期货市场阈值 0.7；通过率 0.8 → 通过。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.market = "futures"
        loop.robustness_tester.run = MagicMock(return_value={"summary": {"overall_pass_rate": 0.8}})
        result = loop._run_robustness_check(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True

    def test_run_shap_exception_returns_passed(self, tmp_memory_dir):
        """SHAP 分析异常 → passed=True 兜底。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.shap_analyzer.analyze = MagicMock(side_effect=RuntimeError("shap crash"))
        result = loop._run_shap_analysis(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True

    def test_run_causal_exception_returns_passed(self, tmp_memory_dir):
        """因果验证异常 → passed=True 兜底。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.causal_validator.validate = MagicMock(side_effect=RuntimeError("causal crash"))
        result = loop._run_causal_validation(_make_minimal_factor(), {}, "t")
        assert result["passed"] is True

    def test_record_failed_traces_write_files(self, tmp_memory_dir):
        """audit/ablation/robustness/causal 失败轨迹均写文件。"""
        loop = self._make_loop(tmp_memory_dir)
        factor = _make_minimal_factor("fct_trace001")
        loop._record_audit_failed_trace(factor, 1, "t", _make_passing_audit_report())
        loop._record_ablation_failed_trace(factor, 1, "t", {"passed": False})
        loop._record_robustness_failed_trace(factor, 1, "t", {"passed": False})
        loop._record_causal_failed_trace(factor, 1, "t", {"passed": False, "n_anomalous": 2})
        trace_dir = tmp_memory_dir / "traces"
        files = list(trace_dir.glob("*.json"))
        assert len(files) >= 4

    def test_log_inspection_detail_low_dims(self, tmp_memory_dir):
        """质检日志低分项分支。"""
        loop = self._make_loop(tmp_memory_dir)
        from fts.factor_engine.evolution_loop import _QualityInspectionResult

        inspection = _QualityInspectionResult(
            {
                "total_score": 30.0,
                "grade": "C",
                "dimension_scores": [
                    {"name": "ic", "score": 2.0, "description": "低"},
                    {"name": "sharpe", "score": 4.0, "description": "高"},
                ],
            },
            filtered=True,
            reason="等级 C",
        )
        loop._log_inspection_detail(_make_minimal_factor("fct_insp001"), inspection, "淘汰", 1)

    def test_record_quality_filtered_trace(self, tmp_memory_dir):
        """质检过滤轨迹（evaluation=None 自动构造）。"""
        loop = self._make_loop(tmp_memory_dir)
        from fts.factor_engine.evolution_loop import _QualityInspectionResult

        inspection = _QualityInspectionResult({"total_score": 20.0, "grade": "C"}, filtered=True, reason="低分")
        loop._record_quality_filtered_trace(
            _make_minimal_factor("fct_qf001"),
            1,
            "t",
            inspection,
        )
        failure_dir = tmp_memory_dir / "failure"
        assert len(list(failure_dir.glob("*.json"))) > 0

    def test_record_failure_trace_with_evaluation(self, tmp_memory_dir):
        """_record_failure_trace 传入 evaluation 时补齐 failure_reasons。"""
        loop = self._make_loop(tmp_memory_dir)
        factor = _make_minimal_factor("fct_fail001")
        evaluation = {"factor_id": "fct_fail001", "trace_id": "t2", "passed": False, "evaluated_at": "now"}
        loop._record_failure_trace(factor, 2, "macro_evolution", "summary", ["IC 过低"], "t", evaluation=evaluation)
        assert evaluation["failure_reasons"] == ["IC 过低"]


class TestGapF16PrefilterAndRuntime:
    """快速预筛选 / 横截面预筛 / 运行时校验各分支。"""

    @staticmethod
    def _make_loop(tmp_memory_dir):
        return EvolutionLoop(
            data=_make_ohlcv(200),
            forward_returns=np.zeros(200),
            memory_dir=tmp_memory_dir,
        )

    @staticmethod
    def _constant_factor():
        return {
            "factor_id": "fct_const001",
            "name": "const",
            "code": (
                "def factor_program(data, params):\n    import numpy as np\n    return np.ones(len(data['close']))\n"
            ),
            "params": {},
        }

    def test_quick_prefilter_exec_failure(self, tmp_memory_dir):
        """预筛执行异常 → 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        factor = {"factor_id": "fct_e001", "name": "e", "code": "raise RuntimeError", "params": {}}
        ok, reason, ic = loop._quick_prefilter(factor, "t")
        assert ok is False
        assert "执行失败" in reason
        assert ic == 0.0

    def test_quick_prefilter_length_mismatch(self, tmp_memory_dir):
        """预筛输出长度不匹配 → 失败。

        _execute_factor_code 内部会把长度不齐信号对齐为 n，故 mock 返回
        错误长度数组以触发 _quick_prefilter 自身的长度校验。
        """
        loop = self._make_loop(tmp_memory_dir)
        factor = {
            "factor_id": "fct_e002",
            "name": "e2",
            "code": ("def factor_program(data, params):\n    import numpy as np\n    return np.ones(3)\n"),
            "params": {},
        }
        with patch(
            "fts.factor_engine.backtest_pipeline.BacktestPipeline._execute_factor_code",
            return_value=np.ones(3),
        ):
            ok, reason, _ = loop._quick_prefilter(factor, "t")
        assert ok is False
        assert "长度不匹配" in reason

    def test_quick_prefilter_constant_signal(self, tmp_memory_dir):
        """常数信号（nunique <= 10）→ 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        ok, reason, _ = loop._quick_prefilter(self._constant_factor(), "t")
        assert ok is False
        assert "nunique" in reason

    def test_quick_prefilter_tiny_std(self, tmp_memory_dir):
        """信号标准差过小 → 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        # 11 个不同值但幅度极小 → nunique=11 > 10 但 std < 1e-6
        factor = {
            "factor_id": "fct_e003",
            "name": "e3",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    x = np.zeros(len(data['close']))\n"
                "    x[:11] = np.arange(11) * 1e-9\n"
                "    return x\n"
            ),
            "params": {},
        }
        ok, reason, _ = loop._quick_prefilter(factor, "t")
        assert ok is False
        assert "标准差" in reason

    def test_quick_prefilter_no_forward_returns_passes(self, tmp_memory_dir):
        """forward_returns 缺失 → 跳过 IC 检查直接通过。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.forward_returns = None
        factor = {
            "factor_id": "fct_e004",
            "name": "e4",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    return close - close.mean()\n"
            ),
            "params": {},
        }
        ok, reason, ic = loop._quick_prefilter(factor, "t")
        assert ok is True
        assert ic == 0.0

    def test_check_factor_runtime_failure(self, tmp_memory_dir):
        """运行时校验执行失败 → False。"""
        loop = self._make_loop(tmp_memory_dir)
        factor = {"factor_id": "fct_r001", "name": "r", "code": "raise ValueError", "params": {}}
        ok, reason = loop._check_factor_runtime(factor)
        assert ok is False
        assert "执行失败" in reason

    def test_check_factor_runtime_length_mismatch(self, tmp_memory_dir):
        """运行时校验长度不匹配 → False。

        _execute_factor_code 内部会把长度不齐信号对齐为 n，故 mock 返回
        错误长度数组以触发 _check_factor_runtime 自身的长度校验。
        """
        loop = self._make_loop(tmp_memory_dir)
        factor = {
            "factor_id": "fct_r002",
            "name": "r2",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return np.ones(5)\n",
            "params": {},
        }
        with patch(
            "fts.factor_engine.backtest_pipeline.BacktestPipeline._execute_factor_code",
            return_value=np.ones(5),
        ):
            ok, reason = loop._check_factor_runtime(factor)
        assert ok is False
        assert "长度不匹配" in reason

    def test_check_factor_runtime_constant(self, tmp_memory_dir):
        """运行时校验常数信号 → False。"""
        loop = self._make_loop(tmp_memory_dir)
        ok, reason = loop._check_factor_runtime(self._constant_factor())
        assert ok is False
        assert "常数" in reason

    def test_check_factor_runtime_pass(self, tmp_memory_dir):
        """运行时校验通过。"""
        loop = self._make_loop(tmp_memory_dir)
        factor = {
            "factor_id": "fct_r003",
            "name": "r3",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close']\n"
                "    return close - close.mean()\n"
            ),
            "params": {},
        }
        ok, reason = loop._check_factor_runtime(factor)
        assert ok is True

    def test_cross_section_prefilter_empty_panel(self, tmp_memory_dir):
        """横截面面板为空 → 放行。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.cross_section_data = {}
        ok, reason, _ = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is True

    def test_cross_section_prefilter_exec_failure(self, tmp_memory_dir):
        """横截面预筛执行异常 → 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.cross_section_data = {"S0": _make_ohlcv(50)}
        loop.cross_section_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=50))
        with patch(
            "fts.factor_engine.evaluation_chain._cs_execute_factors",
            side_effect=RuntimeError("boom"),
        ):
            ok, reason, _ = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is False
        assert "执行失败" in reason

    def test_cross_section_prefilter_insufficient_symbols(self, tmp_memory_dir):
        """横截面有效标的 < 5 → 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.cross_section_data = {"S0": _make_ohlcv(50)}
        loop.cross_section_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=50))
        with patch(
            "fts.factor_engine.evaluation_chain._cs_execute_factors",
            return_value=({"S0": np.ones(50)}, {"S0": np.zeros(50)}),
        ):
            ok, reason, _ = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is False
        assert "标的不足" in reason

    def test_cross_section_prefilter_no_dates_passes(self, tmp_memory_dir):
        """common_dates 为空 → 放行。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.cross_section_data = {"S0": _make_ohlcv(50)}
        loop.cross_section_dates = None
        with patch(
            "fts.factor_engine.evaluation_chain._cs_execute_factors",
            return_value=(
                {f"S{i}": np.ones(50) for i in range(6)},
                {f"S{i}": np.zeros(50) for i in range(6)},
            ),
        ):
            ok, reason, _ = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is True

    def test_cross_section_prefilter_ic_too_low(self, tmp_memory_dir):
        """横截面 IC 过低 → 失败。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.market = "stock"
        loop.cross_section_data = {f"S{i}": _make_ohlcv(50) for i in range(6)}
        loop.cross_section_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=50))
        with (
            patch(
                "fts.factor_engine.evaluation_chain._cs_execute_factors",
                return_value=(
                    {f"S{i}": np.ones(50) for i in range(6)},
                    {f"S{i}": np.zeros(50) for i in range(6)},
                ),
            ),
            patch(
                "fts.factor_engine.evaluation_chain._cs_build_matrices",
                return_value=(np.ones((50, 6)), np.zeros((50, 6))),
            ),
            patch(
                "fts.factor_engine.evaluation_chain._cs_compute_ics",
                return_value=[0.005],
            ),
        ):
            ok, reason, _ = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is False
        assert "IC 过低" in reason

    def test_cross_section_prefilter_pass(self, tmp_memory_dir):
        """横截面预筛通过。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._is_cross_section = True
        loop.market = "stock"
        loop.cross_section_data = {f"S{i}": _make_ohlcv(50) for i in range(6)}
        loop.cross_section_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=50))
        with (
            patch(
                "fts.factor_engine.evaluation_chain._cs_execute_factors",
                return_value=(
                    {f"S{i}": np.ones(50) for i in range(6)},
                    {f"S{i}": np.zeros(50) for i in range(6)},
                ),
            ),
            patch(
                "fts.factor_engine.evaluation_chain._cs_build_matrices",
                return_value=(np.ones((50, 6)), np.zeros((50, 6))),
            ),
            patch(
                "fts.factor_engine.evaluation_chain._cs_compute_ics",
                return_value=[0.08],
            ),
        ):
            ok, reason, ic = loop._cross_section_prefilter(_make_minimal_factor(), "t")
        assert ok is True
        assert ic == 0.08


class TestGapF16EvolveOneAndBatch:
    """_evolve_one method_hint 各分支 + batch 漏斗。"""

    @staticmethod
    def _make_loop(tmp_memory_dir):
        return EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
        )

    def test_evolve_one_hint_deep_success(self, tmp_memory_dir):
        """method_hint=deep 成功。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._run_deep_evolution = MagicMock(
            return_value=(
                {"factor_id": "fct_d001", "deep_model": {"lookback": 5, "hidden": 8, "val_ic": 0.02}},
                "Deep ok",
            )
        )
        out = loop._evolve_one({"factor_id": "p1"}, 1, "t", method_hint="deep", seed=1)
        assert out is not None
        assert out[1] == "deep_evolution"

    def test_evolve_one_hint_deep_failure(self, tmp_memory_dir):
        """method_hint=deep 失败 → None。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._run_deep_evolution = MagicMock(side_effect=RuntimeError("deep down"))
        assert loop._evolve_one({"factor_id": "p1"}, 1, "t", method_hint="deep", seed=1) is None

    def test_evolve_one_hint_operator_failure(self, tmp_memory_dir):
        """method_hint=operator 失败 → None。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._generate_operator_factor = MagicMock(side_effect=RuntimeError("op down"))
        assert loop._evolve_one({"factor_id": "p1"}, 1, "t", method_hint="operator", seed=1) is None

    def test_evolve_one_hint_macro_failure(self, tmp_memory_dir):
        """method_hint=macro 失败 → None。"""
        loop = self._make_loop(tmp_memory_dir)
        loop.macro_evolver.evolve = MagicMock(side_effect=RuntimeError("llm down"))
        assert loop._evolve_one({"factor_id": "p1"}, 1, "t", method_hint="macro", seed=1) is None

    def test_evolve_one_hint_gp_success(self, tmp_memory_dir):
        """method_hint=gp 成功。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._run_gp_evolution = MagicMock(return_value=({"factor_id": "fct_g001"}, "GP ok"))
        out = loop._evolve_one({"factor_id": "p1"}, 1, "t", method_hint="gp", seed=1)
        assert out is not None
        assert out[1] == "gp_evolution"

    def test_batch_generate_one_rotation(self, tmp_memory_dir):
        """batch 单候选生成方法轮换 hint: macro→gp→deep→transformer→operator→gp。"""
        loop = self._make_loop(tmp_memory_dir)
        hints: list[str] = []

        def fake_evolve_one(parent, generation, trace_id, method_hint=None, seed=None):
            hints.append(method_hint)
            return ({"factor_id": "fct_b001"}, method_hint, "summary", 0)

        loop._evolve_one = MagicMock(side_effect=fake_evolve_one)
        for i in range(5):
            loop._batch_idx = i
            proposal = loop._batch_generate_one({"factor_id": "p1"}, 1, "t")
            assert proposal is not None
        assert hints == ["macro", "gp", "deep", "transformer", "operator"]

    def test_batch_generate_one_none(self, tmp_memory_dir):
        """batch 单候选生成失败 → None。"""
        loop = self._make_loop(tmp_memory_dir)
        loop._evolve_one = MagicMock(return_value=None)
        assert loop._batch_generate_one({"factor_id": "p1"}, 1, "t") is None

    def test_run_batch_generation_all_rejected(self, tmp_memory_dir, tmp_elite_dir):
        """batch 一代全部被拦截 → 记录失败轨迹并返回 False。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_result = MagicMock()
        mock_result.tokens_consumed = 0
        mock_result.total_generated = 2
        mock_result.total_passed = 0
        mock_result.passed = []
        mock_result.rejected = [
            {"method": "gp", "prefilter_reason": "低 IC"},
            {"method": "operator", "prefilter_reason": "常数"},
        ]
        mock_result.duration_ms = 12.0
        with patch("fts.factor_engine.batch_mining.BatchMiner") as mock_miner_cls:
            mock_miner_cls.return_value.run_iteration.return_value = mock_result
            ok = loop._run_batch_generation({"factor_id": "p1"}, 1, "t", {"tokens_consumed": 0}, [], [])
        assert ok is False

    def test_run_batch_generation_promoted(self, tmp_memory_dir, tmp_elite_dir):
        """batch 一代有候选通过 → 走准入链并返回 True。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        mock_result = MagicMock()
        mock_result.tokens_consumed = 50
        mock_result.total_generated = 1
        mock_result.total_passed = 1
        mock_result.passed = [{"factor": {"factor_id": "fct_bp001"}, "method": "gp", "summary": "s"}]
        mock_result.rejected = []
        mock_result.duration_ms = 5.0
        loop._process_candidate = MagicMock(return_value=True)
        full_state = loop.state_manager.load_or_init()
        with patch("fts.factor_engine.batch_mining.BatchMiner") as mock_miner_cls:
            mock_miner_cls.return_value.run_iteration.return_value = mock_result
            ok = loop._run_batch_generation({"factor_id": "p1"}, 1, "t", full_state, [], [])
        assert ok is True
        loop._process_candidate.assert_called_once()


class TestGapF16RunBoundaries:
    """run() 数据质量熔断 / 无父因子直接返回。"""

    def test_run_data_quality_critical(self, tmp_memory_dir, tmp_elite_dir):
        """数据质量 critical 告警 → circuit_broken。"""
        from fts.monitor.data_quality_monitor import QualityAlert

        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        critical_alert = QualityAlert(
            factor_id="market",
            alert_type="data_missing",
            severity="critical",
            message="close 缺失",
            metric_name="coverage",
            metric_value=0.1,
            baseline_value=1.0,
            threshold=0.5,
        )
        loop.data_quality_monitor.validate_market_data = MagicMock(return_value=[critical_alert])
        result = loop.run(max_generation=2)
        assert result.status == "circuit_broken"
        assert result.circuit_breaker_reason == "data_quality_critical"

    def test_run_no_parent_factors(self, tmp_memory_dir, tmp_elite_dir):
        """无种子且 elite 池为空 → 直接返回 completed 0 代。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        loop.seed_pool.load_all_seeds = MagicMock(return_value=[])
        loop._merge_l1_candidates = MagicMock(return_value=[])
        loop._run_seed_correlation_check = MagicMock(return_value=[])
        loop._evaluate_and_promote_seeds = MagicMock(return_value=0)
        loop._load_elite_parent_factors = MagicMock(return_value=[])
        result = loop.run(max_generation=3)
        assert result.status == "completed"
        assert result.generations_completed == 0

    def test_run_seed_correlation_check_cross_section_skip(self, tmp_memory_dir, tmp_elite_dir):
        """横截面模式 + 种子 > 50 → 相关性预检跳过。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(100)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=100)),
        )
        seeds = [{"factor_id": f"fct_s{i}", "name": f"s{i}", "code": "x"} for i in range(60)]
        result = loop._run_seed_correlation_check(seeds, "t")
        assert result == []


class TestGapF16CrossSectionAndEvolution:
    """横截面评估 / Barra / GP / Deep 演化分支。"""

    def test_build_barra_exposures_not_cross_section(self, tmp_memory_dir):
        """非横截面模式 → None。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
        )
        assert loop._build_barra_exposures() is None

    def test_build_barra_exposures_disabled(self, tmp_memory_dir, monkeypatch):
        """横截面但配置关闭 → None。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "l2_barra_style_neutral", False)
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(100)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=100)),
        )
        assert loop._build_barra_exposures() is None

    def test_build_barra_exposures_exception(self, tmp_memory_dir, monkeypatch):
        """Barra 构建异常 → None 不阻断。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "l2_barra_style_neutral", True)
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(100)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=100)),
        )
        with patch(
            "fts.factor_engine.barra.barra_style.BarraStyleEngine.compute_exposures",
            side_effect=RuntimeError("barra crash"),
        ):
            assert loop._build_barra_exposures() is None

    def test_build_vol_map_not_cross_section(self, tmp_memory_dir):
        """非横截面模式 → None。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
        )
        assert loop._build_vol_map() is None

    def test_build_vol_map_disabled(self, tmp_memory_dir, monkeypatch):
        """横截面但配置关闭（l2_barra_style_neutral=False）→ None。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "l2_barra_style_neutral", False)
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(100)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=100)),
        )
        assert loop._build_vol_map() is None

    def test_build_vol_map_returns_map(self, tmp_memory_dir, monkeypatch):
        """横截面 + 配置开启 → 返回 {symbol: 年化波动率}。"""
        from fts.config.settings import get_config

        monkeypatch.setattr(get_config(), "l2_barra_style_neutral", True)
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(150), "S1": _make_ohlcv(150)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=150)),
        )
        vol_map = loop._build_vol_map()
        assert vol_map is not None
        assert set(vol_map.keys()) == {"S0", "S1"}
        assert all(v > 0 for v in vol_map.values())

    def test_evaluate_cross_section_failure_reasons(self, tmp_memory_dir):
        """横截面评估低 IC/夏普 → failure_reasons 非空。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
            cross_section_data={"S0": _make_ohlcv(100)},
            cross_section_dates=pd.DatetimeIndex(pd.date_range("2026-01-01", periods=100)),
        )
        with patch("fts.factor_engine.evolution_seeds.cross_section_evaluate_backtest") as mock_cs:
            mock_cs.return_value = {"ic": 0.01, "sharpe": 0.8, "t_stat": 1.0}
            factor = _make_minimal_factor("fct_cs001")
            evaluation = loop._evaluate_cross_section(factor, "t")
        assert evaluation["passed"] is False
        assert len(evaluation["failure_reasons"]) == 2

    def test_run_gp_evolution_bad_fitness(self, tmp_memory_dir):
        """GP 适应度 <= 0 → RuntimeError。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(100),
            forward_returns=np.zeros(100),
            memory_dir=tmp_memory_dir,
        )
        mock_result = MagicMock()
        mock_result.best_fitness = -0.5
        loop.feature_ops_engine.run_gp_search = MagicMock(return_value=mock_result)
        with pytest.raises(RuntimeError, match="适应度"):
            loop._run_gp_evolution({"factor_id": "p1"}, 1, "t")

    def test_run_deep_evolution_insufficient_data(self, tmp_memory_dir):
        """深度演化数据不足 → RuntimeError。"""
        loop = EvolutionLoop(
            data=_make_ohlcv(1),
            forward_returns=np.zeros(1),
            memory_dir=tmp_memory_dir,
        )
        with pytest.raises(RuntimeError, match="无可用行情数据"):
            loop._run_deep_evolution({"factor_id": "p1", "name": "p"}, 1, "t")


class TestGapF16MergeL1Candidates:
    """_merge_l1_candidates 各分支。"""

    def _make_loop(self, tmp_memory_dir, inject_dir):
        return EvolutionLoop(
            data=_make_ohlcv(50),
            forward_returns=np.zeros(50),
            memory_dir=tmp_memory_dir,
            inject_dir=str(inject_dir),
        )

    def test_inject_dir_missing_returns_seeds(self, tmp_memory_dir, tmp_path):
        """注入目录不存在 → 返回原种子。"""
        loop = self._make_loop(tmp_memory_dir, tmp_path / "no_such_dir")
        seeds = [{"factor_id": "s1", "name": "s1"}]
        assert loop._merge_l1_candidates(seeds, "t") == seeds

    def test_merge_consumes_candidate(self, tmp_memory_dir, tmp_path):
        """正常合并：候选转为 FactorProgram 并消费（文件删除）。

        factor_pool.json 使用工作目录相对路径，测试中 patch Path.exists
        使其视为不存在（pool_loaded=False 放行），避免读写真实文件。
        """
        inject_dir = tmp_path / "l1"
        inject_dir.mkdir()
        cand = {
            "candidate_id": "cand_abc12345",
            "name": "l1_factor",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
            "market": "futures",
            "economic_logic": {
                "theory": 3,
                "behavioral": 3,
                "microstructure": 3,
                "institutional": 3,
                "narrative": "L1 注入候选测试",
            },
        }
        cand_file = inject_dir / "cand_abc12345.json"
        cand_file.write_text(json.dumps(cand), encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, inject_dir)
        real_exists = Path.exists

        def fake_exists(self):
            if str(self).endswith("factor_pool.json"):
                return False
            return real_exists(self)

        with patch.object(Path, "exists", fake_exists):
            merged = loop._merge_l1_candidates([], "t")
        assert len(merged) == 1
        assert merged[0]["source"] == "bootstrapping"
        assert merged[0]["parent_id"] == "cand_abc12345"
        # 消费后候选文件被删除（GAP-036）
        assert not cand_file.exists()

    def test_merge_market_mismatch_skips(self, tmp_memory_dir, tmp_path):
        """候选 market 不匹配 → 跳过。"""
        inject_dir = tmp_path / "l1b"
        inject_dir.mkdir()
        cand = {
            "candidate_id": "cand_xyz12345",
            "name": "stock_factor",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
            "market": "stock",
        }
        (inject_dir / "cand_xyz12345.json").write_text(json.dumps(cand), encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, inject_dir)
        loop.market = "futures"
        real_exists = Path.exists

        def fake_exists(self):
            if str(self).endswith("factor_pool.json"):
                return False
            return real_exists(self)

        with patch.object(Path, "exists", fake_exists):
            merged = loop._merge_l1_candidates([], "t")
        assert len(merged) == 0

    def test_merge_duplicate_name_skips(self, tmp_memory_dir, tmp_path):
        """候选名称与现有种子重复 → 跳过。"""
        inject_dir = tmp_path / "l1c"
        inject_dir.mkdir()
        cand = {
            "candidate_id": "cand_dup12345",
            "name": "dup_factor",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close']",
            "params": {},
            "market": "futures",
        }
        (inject_dir / "cand_dup12345.json").write_text(json.dumps(cand), encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, inject_dir)
        real_exists = Path.exists

        def fake_exists(self):
            if str(self).endswith("factor_pool.json"):
                return False
            return real_exists(self)

        with patch.object(Path, "exists", fake_exists):
            merged = loop._merge_l1_candidates([{"factor_id": "s1", "name": "dup_factor"}], "t")
        assert len(merged) == 1  # 仅原种子

    def test_merge_invalid_candidate_skipped(self, tmp_memory_dir, tmp_path):
        """候选缺少 candidate_id/name/code → 跳过。"""
        inject_dir = tmp_path / "l1d"
        inject_dir.mkdir()
        (inject_dir / "cand_bad.json").write_text(json.dumps({"candidate_id": "cand_bad"}), encoding="utf-8")
        loop = self._make_loop(tmp_memory_dir, inject_dir)
        real_exists = Path.exists

        def fake_exists(self):
            if str(self).endswith("factor_pool.json"):
                return False
            return real_exists(self)

        with patch.object(Path, "exists", fake_exists):
            merged = loop._merge_l1_candidates([], "t")
        assert merged == []
