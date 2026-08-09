"""tests/factor_engine/test_l2_elite_redundancy.py — GAP-I206 L2 准入去冗余测试。

覆盖:
    1. _check_elite_correlation 方法级: 高相关命中 / 负高相关(abs 判断) /
       低相关放行 / 空 elite 放行 / 索引文件跳过 / 容量护栏 / 执行失败容错
    2. _promote_to_elite 集成: 演化因子高相关被拦截 / 种子因子跳过检查 /
       演化因子低相关正常晋升
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorEvaluation
from fts.factor_engine.evolution_loop import EvolutionLoop


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_evolution_loop.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


# ─── 信号工厂: 按 code 内 marker 分发信号，控制相关性 ───

_CODE_P100 = "def factor_program(data, params):\n    return np.arange(len(data), dtype=float) + 100.0  # SIG_P100"
_CODE_NEG = "def factor_program(data, params):\n    return -(np.arange(len(data), dtype=float) + 100.0)  # SIG_NEG"
_CODE_NOISE = "def factor_program(data, params):\n    rng = np.random.default_rng(1)\n    return rng.normal(size=len(data))  # SIG_NOISE"
_CODE_BOOM = "def factor_program(data, params):\n    raise RuntimeError('boom')  # SIG_BOOM"


def _fake_execute(code: str, data, params):
    """模拟 BacktestPipeline._execute_factor_code: 按 code marker 返回信号。"""
    n = len(data)
    if "SIG_BOOM" in code:
        raise RuntimeError("boom")
    if "SIG_NEG" in code:
        return -(np.arange(n, dtype=float) + 100.0)
    if "SIG_NOISE" in code:
        return np.random.default_rng(1).normal(size=n)
    # SIG_P100（new 与 identical elite 同款信号，Pearson r=1）
    return np.arange(n, dtype=float) + 100.0


@pytest.fixture
def patched_execute():
    with patch(
        "fts.factor_engine.backtest_pipeline.BacktestPipeline._execute_factor_code",
        side_effect=_fake_execute,
    ) as m:
        yield m


def _make_factor(factor_id: str, code: str, name: str | None = None) -> dict:
    """构造最小演化因子 dict（含 contracts 必需字段）。"""
    return {
        "factor_id": factor_id,
        "name": name or factor_id,
        "code": code,
        "params": {"window": 10},
        "signature": {
            "input_fields": ["close"], "output_type": "signal",
            "frequency": "daily", "lookback": 1,
        },
        "economic_logic": {
            "theory": 3, "behavioral": 3, "microstructure": 3,
            "institutional": 3, "narrative": "测试因子",
        },
        "source": "evolved",
        "generation": 1,
    }


def _write_elite(tmp_elite_dir: Path, factor_id: str, code: str, name: str) -> None:
    """向 elite 目录写入一个既有 elite 因子 JSON 快照。"""
    tmp_elite_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "factor_id": factor_id,
        "name": name,
        "code": code,
        "params": {"window": 10},
    }
    (tmp_elite_dir / f"{factor_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8",
    )


def _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir) -> EvolutionLoop:
    """构造最小 EvolutionLoop（elite 目录为测试隔离路径）。"""
    return EvolutionLoop(
        data=sample_ohlcv,
        forward_returns=forward_returns,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        n_trials_micro=2,
    )


def _make_passing_evaluation(factor_id: str) -> FactorEvaluation:
    """构造通过 L3 多重检验的评估（照搬 test_promote_to_elite 模式）。"""
    return FactorEvaluation(
        factor_id=factor_id,
        trace_id="test_trace",
        passed=True,
        failure_reasons=[],
        level_3_multiple={"passed": True},
        evaluated_at="2026-08-10T00:00:00",
    )


def _mock_repo_clear(loop: EvolutionLoop) -> MagicMock:
    """Mock 去重检查（get_factor_by_name 返回 None），返回 mock repo。"""
    mock_repo = MagicMock()
    mock_repo.get_factor_by_name = MagicMock(return_value=None)
    loop._get_repo = MagicMock(return_value=mock_repo)
    return mock_repo


# ─── 方法级: _check_elite_correlation ────────────────────


class TestCheckEliteCorrelation:
    """GAP-I206: _check_elite_correlation 相关性检查方法。"""

    def test_high_corr_returns_pairs(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """既有 elite 与新因子信号 r=1 → 返回高相关对（含 factor_name_b/pearson）。"""
        _write_elite(tmp_elite_dir, "fct_elite_a", _CODE_P100, "elite_a")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_a", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is not None
        pairs = result["correlations"]
        assert len(pairs) == 1
        assert pairs[0]["factor_name_b"] == "elite_a"
        assert pairs[0]["factor_id_b"] == "fct_elite_a"
        assert abs(pairs[0]["pearson"]) >= loop._l2_elite_corr_threshold

    def test_negative_high_corr_blocks_by_abs(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """负高相关（r=-1）同样拦截（按绝对值判断）。"""
        _write_elite(tmp_elite_dir, "fct_elite_neg", _CODE_NEG, "elite_neg")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_neg", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is not None
        assert result["correlations"][0]["factor_name_b"] == "elite_neg"
        assert result["correlations"][0]["pearson"] < 0  # 原始 pearson 保留符号
        assert abs(result["correlations"][0]["pearson"]) >= 0.9

    def test_low_corr_returns_none(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """既有 elite 信号为噪声 → 低相关 → None（放行）。"""
        _write_elite(tmp_elite_dir, "fct_elite_noise", _CODE_NOISE, "elite_noise")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_noise", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is None

    def test_no_elite_returns_none(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """elite 目录为空/不存在 → None（首次晋升场景放行）。"""
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_first", _CODE_P100)

        assert loop._check_elite_correlation(factor) is None

    def test_skips_seed_correlation_index(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """_l2_seed_correlation_index.json 索引文件被跳过，不影响真实 elite 检查。"""
        _write_elite(tmp_elite_dir, "fct_elite_b", _CODE_P100, "elite_b")
        # 写入索引文件（无 code，若未跳过会因缺 code 被忽略，此处验证跳过逻辑）
        (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text(
            json.dumps({"entries": []}), encoding="utf-8",
        )
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_b", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is not None
        assert result["correlations"][0]["factor_name_b"] == "elite_b"

    def test_capacity_cap_limits_scan(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """容量护栏: max_scan=2 时最多扫描 2 个既有 elite。"""
        for i in range(3):
            _write_elite(tmp_elite_dir, f"fct_elite_{i}", _CODE_P100, f"elite_{i}")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        loop._l2_elite_corr_max_scan = 2
        factor = _make_factor("fct_new_cap", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is not None
        # 命中数 ≤ 扫描上限（3 个全高相关但只扫 2 个）
        assert len(result["correlations"]) <= 2

    def test_exec_failure_skipped(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """既有 elite 执行抛异常被跳过，其余命中仍返回。"""
        _write_elite(tmp_elite_dir, "fct_elite_boom", _CODE_BOOM, "elite_boom")
        _write_elite(tmp_elite_dir, "fct_elite_ok", _CODE_P100, "elite_ok")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_exec", _CODE_P100)

        result = loop._check_elite_correlation(factor)

        assert result is not None
        names = [p["factor_name_b"] for p in result["correlations"]]
        assert names == ["elite_ok"]  # boom 因子被跳过

    def test_new_factor_exec_failure_returns_none(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """新因子信号执行失败 → None（静默放行，不阻断晋升主流程）。"""
        _write_elite(tmp_elite_dir, "fct_elite_c", _CODE_P100, "elite_c")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_fail", _CODE_BOOM)

        assert loop._check_elite_correlation(factor) is None


# ─── 集成: _promote_to_elite 准入拦截 ────────────────────


class TestPromoteToEliteRedundancy:
    """GAP-I206: _promote_to_elite 中 L2 准入去冗余闭环。"""

    def test_shadow_high_corr_blocks_promotion(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """演化因子（shadow_observe=True）高相关 → 拒绝晋升且无 JSON 落盘。"""
        _write_elite(tmp_elite_dir, "fct_elite_x", _CODE_P100, "elite_x")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_blocked", _CODE_P100)
        evaluation = _make_passing_evaluation("fct_new_blocked")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=True)

        assert fp is None
        assert not (tmp_elite_dir / "fct_new_blocked.json").exists()

    def test_seed_skips_correlation_check(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """种子因子（shadow_observe=False）跳过相关性检查 → 即使高相关也正常晋升。"""
        _write_elite(tmp_elite_dir, "fct_elite_same", _CODE_P100, "elite_same")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_seed_import", _CODE_P100, name="fct_seed_import")
        factor["source"] = "seed"
        evaluation = _make_passing_evaluation("fct_seed_import")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=False)

        assert fp is not None and fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_seed_import"

    def test_shadow_low_corr_promotes(
        self, sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir,
        patched_execute,
    ):
        """演化因子低相关 → 相关性放行，正常晋升。"""
        _write_elite(tmp_elite_dir, "fct_elite_noise2", _CODE_NOISE, "elite_noise2")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_promote", _CODE_P100)
        evaluation = _make_passing_evaluation("fct_new_promote")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=True)

        assert fp is not None and fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_new_promote"
