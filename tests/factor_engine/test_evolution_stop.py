"""tests/factor_engine/test_evolution_stop.py — Phase 3 P1-3 提前达标停止测试。

覆盖（26 号计划 §8.5）:
    1. _maybe_early_stop: 连续零晋升 K 代触发 / 中断后恢复不触发 / 开关关闭不触发
    2. run() 集成: 连续零晋升 K 代提前结束（early_stopped 标记 + 正常收尾）/
       开关关闭跑满 / 实验日志仍导出
    3. FTSConfig: 默认关闭（保守）+ env 覆盖
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _mocks(loop, *, parent=None):
    """构造 run() 集成 mock：种子空 + 恒零晋升演化路径。"""
    fake_factor = {"factor_id": "fct_es001", "name": "es_cand", "code": "x", "params": {}}
    fake_parent = parent or {"factor_id": "fct_es_parent", "name": "es_parent", "code": "x", "params": {}}
    loop.seed_pool.load_all_seeds = MagicMock(return_value=[])
    loop._evaluate_and_promote_seeds = MagicMock(return_value=0)
    loop._load_elite_parent_factors = MagicMock(return_value=[fake_parent])
    loop._evolve_one = MagicMock(return_value=(fake_factor, "operator_evolution", "s", 0))
    loop._check_factor_runtime = MagicMock(return_value=(True, ""))
    loop._quick_prefilter = MagicMock(return_value=(True, "", 0.05))
    loop._process_candidate = MagicMock(return_value=False)


# ─── 1. _maybe_early_stop 方法级 ──────────────────────────


class TestMaybeEarlyStop:
    def test_triggers_after_k_consecutive_empty(self, minimal_loop):
        """连续 K 代零晋升 → 第 K 次调用返回 True + reason 正确。"""
        minimal_loop._evolution_stop_enabled = True
        minimal_loop._evolution_stop_k = 3
        minimal_loop._consecutive_empty_generations = 0
        minimal_loop._early_stop_last_count = 0
        state = {"total_factors_promoted": 0}

        assert minimal_loop._maybe_early_stop(state) is False
        assert minimal_loop._maybe_early_stop(state) is False
        assert minimal_loop._maybe_early_stop(state) is True
        assert "连续 3 代" in minimal_loop._early_stop_reason
        assert minimal_loop._consecutive_empty_generations == 3

    def test_resets_on_promotion(self, minimal_loop):
        """中断后恢复（中途晋升）→ 计数归零，需重新累计 K 次才触发。"""
        minimal_loop._evolution_stop_enabled = True
        minimal_loop._evolution_stop_k = 3
        minimal_loop._consecutive_empty_generations = 0
        minimal_loop._early_stop_last_count = 0

        assert minimal_loop._maybe_early_stop({"total_factors_promoted": 0}) is False  # 累计 1
        assert minimal_loop._maybe_early_stop({"total_factors_promoted": 1}) is False  # 晋升 → 归零
        assert minimal_loop._maybe_early_stop({"total_factors_promoted": 1}) is False  # 累计 1
        assert minimal_loop._maybe_early_stop({"total_factors_promoted": 1}) is False  # 累计 2
        assert minimal_loop._maybe_early_stop({"total_factors_promoted": 1}) is True  # 累计 3 → 触发

    def test_disabled_never_triggers(self, minimal_loop):
        """开关关闭 → 恒 False 且计数不增长。"""
        minimal_loop._evolution_stop_enabled = False
        minimal_loop._evolution_stop_k = 3
        minimal_loop._consecutive_empty_generations = 0
        minimal_loop._early_stop_last_count = 0

        for _ in range(10):
            assert minimal_loop._maybe_early_stop({"total_factors_promoted": 0}) is False
        assert minimal_loop._consecutive_empty_generations == 0
        assert minimal_loop._early_stop_reason is None


# ─── 2. run() 集成 ────────────────────────────────────────


class TestRunEarlyStop:
    def test_run_early_stops_after_k_empty_generations(self, minimal_loop):
        """连续 K 代零晋升 → 提前结束（early_stopped=True + status=completed + 正确代数）。"""
        minimal_loop._evolution_stop_enabled = True
        minimal_loop._evolution_stop_k = 3
        _mocks(minimal_loop)

        result = minimal_loop.run(max_generation=10)

        assert result.status == "completed"
        assert result.early_stopped is True
        assert "连续 3 代" in (result.early_stop_reason or "")
        assert result.generations_completed == 3
        assert minimal_loop._process_candidate.call_count == 3

    def test_run_not_early_stopped_when_disabled(self, minimal_loop):
        """开关关闭 → 跑满 max_generation，early_stopped=False。"""
        minimal_loop._evolution_stop_enabled = False
        minimal_loop._evolution_stop_k = 3
        _mocks(minimal_loop)

        result = minimal_loop.run(max_generation=5)

        assert result.status == "completed"
        assert result.early_stopped is False
        assert result.early_stop_reason is None
        assert result.generations_completed == 5
        assert minimal_loop._process_candidate.call_count == 5

    def test_run_early_stop_still_exports_experiment_log(self, minimal_loop, tmp_path):
        """提前停止后 finally 仍导出实验日志（Phase 2 兼容）。"""
        minimal_loop._experiment_log_dir = str(tmp_path / "data")
        minimal_loop._evolution_stop_enabled = True
        minimal_loop._evolution_stop_k = 2
        _mocks(minimal_loop)

        minimal_loop.run(max_generation=10)

        files = list((tmp_path / "data").glob("experiments-*.json"))
        assert len(files) == 1


# ─── 3. FTSConfig 配置 ────────────────────────────────────


class TestEvolutionStopConfig:
    def test_defaults_disabled(self):
        """保守默认：关闭 + K=5。"""
        from fts.config.settings import FTSConfig

        cfg = FTSConfig()
        assert cfg.evolution_stop_enabled is False
        assert cfg.evolution_stop_consecutive_empty_generations == 5

    def test_env_override(self, monkeypatch):
        """env 覆盖：FTS_EVOLUTION_STOP_* 生效。"""
        monkeypatch.setenv("FTS_EVOLUTION_STOP_ENABLED", "1")
        monkeypatch.setenv("FTS_EVOLUTION_STOP_EMPTY_GENS", "7")
        from fts.config.settings import FTSConfig

        cfg = FTSConfig()
        assert cfg.evolution_stop_enabled is True
        assert cfg.evolution_stop_consecutive_empty_generations == 7
