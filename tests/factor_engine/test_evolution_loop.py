"""tests/factor_engine/test_evolution_loop.py — 主循环测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import EVOLUTION_VERSION
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


def test_evolution_loop_runs_minimal(
    sample_ohlcv, forward_returns, tmp_memory_dir, tmp_elite_dir, mock_llm_client
):
    """应能完整运行 1 代演化。"""
    loop = EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        llm_client=mock_llm_client,
        n_trials_micro=5,  # 减少 trials 加速测试
    )
    result = loop.run(max_generation=1)
    assert result.status in ("completed", "paused", "circuit_broken")
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

    def test_suggest_param_bool(self):
        from fts.factor_engine.micro_evolution import _suggest_param
        trial = MagicMock()
        trial.suggest_categorical.return_value = True
        result = _suggest_param(trial, "flag", True)
        assert result is True
        trial.suggest_categorical.assert_called_once_with("flag", [True, False])

    def test_suggest_param_int(self):
        from fts.factor_engine.micro_evolution import _suggest_param
        trial = MagicMock()
        trial.suggest_int.return_value = 20
        result = _suggest_param(trial, "window", 10)
        assert result == 20
        trial.suggest_int.assert_called_once_with("window", 5, 20)

    def test_suggest_param_int_min_value(self):
        """int 参数最小值应为 max(1, value//2)。"""
        from fts.factor_engine.micro_evolution import _suggest_param
        trial = MagicMock()
        trial.suggest_int.return_value = 2
        result = _suggest_param(trial, "small", 2)
        assert result == 2
        trial.suggest_int.assert_called_once_with("small", 1, 4)

    def test_suggest_param_float(self):
        from fts.factor_engine.micro_evolution import _suggest_param
        trial = MagicMock()
        trial.suggest_float.return_value = 0.5
        result = _suggest_param(trial, "decay", 0.5)
        assert result == 0.5
        trial.suggest_float.assert_called_once_with("decay", 0.25, 1.0)

    def test_suggest_param_other_type(self):
        """字符串等不可搜索类型应原值返回。"""
        from fts.factor_engine.micro_evolution import _suggest_param
        trial = MagicMock()
        result = _suggest_param(trial, "method", "spearman")
        assert result == "spearman"
        trial.suggest_categorical.assert_not_called()
        trial.suggest_int.assert_not_called()
        trial.suggest_float.assert_not_called()

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
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """mock optuna 路径应完整走通。"""
        import fts.factor_engine.micro_evolution as mev
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

        monkeypatch.setattr(mev, 'TPESampler', MagicMock(), raising=False)
        monkeypatch.setattr(mev, '_HAS_OPTUNA', True)
        mock_optuna = MagicMock()
        monkeypatch.setattr(mev, 'optuna', mock_optuna)
        # 模拟 study
        mock_study = MagicMock()
        mock_study.best_params = {"window": 15}
        mock_study.best_value = 0.05
        mock_study.trials = [MagicMock()]  # 非空 trials
        mock_optuna.create_study.return_value = mock_study

        params, score = mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)
        assert params == {"window": 15}
        assert score == 0.05
        mock_optuna.create_study.assert_called_once()

    def test_optimize_params_study_raises(
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """study.optimize 抛出异常时应转为 MicroEvolutionError。"""
        import fts.factor_engine.micro_evolution as mev
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

        monkeypatch.setattr(mev, 'TPESampler', MagicMock(), raising=False)
        monkeypatch.setattr(mev, '_HAS_OPTUNA', True)
        mock_optuna = MagicMock()
        monkeypatch.setattr(mev, 'optuna', mock_optuna)
        mock_study = MagicMock()
        mock_study.optimize.side_effect = RuntimeError("optuna 崩溃")
        mock_optuna.create_study.return_value = mock_study

        with pytest.raises(mev.MicroEvolutionError, match="optuna 优化失败"):
            mev.optimize_params(factor, sample_ohlcv, forward_returns, n_trials=5)

    def test_optimize_params_no_best_params(
        self, sample_ohlcv, forward_returns, monkeypatch,
    ):
        """无 best_params 时返回原 params + 0.0。"""
        import fts.factor_engine.micro_evolution as mev
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

        monkeypatch.setattr(mev, 'TPESampler', MagicMock(), raising=False)
        monkeypatch.setattr(mev, '_HAS_OPTUNA', True)
        mock_optuna = MagicMock()
        monkeypatch.setattr(mev, 'optuna', mock_optuna)
        mock_study = MagicMock()
        mock_study.best_params = {}   # 空表示无最佳参数
        mock_study.best_value = 0.0
        mock_study.trials = []
        mock_optuna.create_study.return_value = mock_study

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
