"""tests/factor_engine/test_structure_cluster_quota.py — 结构性聚类配额测试。

覆盖（Phase 1.3，GAP-XXX v2.102.0）:
    1. _count_cluster_members 方法级: 同类计数 / 混合信号 / 空 elite / 扫描上限 /
       新因子执行失败 / 索引文件跳过
    2. _promote_to_elite 集成: 配额触发拒绝 / 配额未触发放行 / 低相关簇不计数
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fts.factor_engine.contracts import FactorEvaluation
from fts.factor_engine.evolution_loop import EvolutionLoop


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_l2_elite_redundancy.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


@pytest.fixture(autouse=True)
def _disable_qa_gate(monkeypatch):
    """GAP-135/140：本文件测晋升路径（聚类配额），不测质检门禁——关闭门禁开关
    （mock 因子无审计数据会被一票否决拦截；门禁由 test_gap135_qa_gate.py 独立覆盖）。"""
    from fts.config.settings import get_config as _qc

    monkeypatch.setattr(_qc(), "l2_qa_gate_enabled", False)


# ─── 信号工厂: 按 code 内 marker 分发信号，控制相关性 ───

_CODE_P100 = "def factor_program(data, params):\n    return np.arange(len(data), dtype=float) + 100.0  # SIG_P100"
# 信号与 SIG_P100 逐位相同（供配额同类计数），但代码字符串可区分，
# 避免 GAP-135 晋升期同表达式去重误拦截配额放行路径（配额逻辑测信号聚类、非表达式）。
_CODE_P100_ALT = "def factor_program(data, params):\n    return np.arange(len(data), dtype=float) + 100.0  # SIG_P100 alt"
_CODE_NOISE = "def factor_program(data, params):\n    rng = np.random.default_rng(1)\n    return rng.normal(size=len(data))  # SIG_NOISE"
_CODE_BOOM = "def factor_program(data, params):\n    raise RuntimeError('boom')  # SIG_BOOM"


def _fake_execute(code: str, data, params):
    """模拟 BacktestPipeline._execute_factor_code: 按 code marker 返回信号。"""
    n = len(data)
    if "SIG_BOOM" in code:
        raise RuntimeError("boom")
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
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 1,
        },
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "测试因子",
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
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_elites(tmp_elite_dir: Path, n: int, code: str, prefix: str = "fct_elite") -> None:
    """批量写入 n 个同款信号 elite。"""
    for i in range(n):
        _write_elite(tmp_elite_dir, f"{prefix}_{i}", code, f"elite_{i}")


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
    """构造通过 L3 多重检验的评估。"""
    return FactorEvaluation(
        factor_id=factor_id,
        trace_id="test_trace",
        passed=True,
        failure_reasons=[],
        level_3_multiple={"passed": True},
        # GAP-121: 晋升需携带 ≥2 窗口走航结果（WalkForward 强制门）
        walk_forward={"n_windows_completed": 4, "ic_consistency": 0.75, "passed": True},
        evaluated_at="2026-08-11T00:00:00",
    )


def _mock_repo_clear(loop: EvolutionLoop) -> MagicMock:
    """Mock 去重检查（get_factor_by_name 返回 None），返回 mock repo。"""
    mock_repo = MagicMock()
    mock_repo.get_factor_by_name = MagicMock(return_value=None)
    loop._get_repo = MagicMock(return_value=mock_repo)
    return mock_repo


# ─── 方法级: _count_cluster_members ─────────────────────


class TestCountClusterMembers:
    """结构簇规模代理：与既有 elite |corr| ≥ corr_threshold 的成员数。"""

    def test_counts_same_cluster_members(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """3 个同信号 elite + 1 个噪声 → 同类计数 3（噪声不计）。"""
        _write_elites(tmp_elite_dir, 3, _CODE_P100)
        _write_elite(tmp_elite_dir, "fct_elite_noise", _CODE_NOISE, "elite_noise")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_a", _CODE_P100)

        assert loop._count_cluster_members(factor) == 3

    def test_zero_when_no_elite(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """elite 目录为空/不存在 → 0（首次晋升场景放行）。"""
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_first", _CODE_P100)

        assert loop._count_cluster_members(factor) == 0

    def test_respects_scan_cap(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """容量护栏: max_scan=2 时同类计数 ≤ 2。"""
        _write_elites(tmp_elite_dir, 5, _CODE_P100)
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        loop._cluster_max_scan = 2
        factor = _make_factor("fct_new_cap", _CODE_P100)

        assert loop._count_cluster_members(factor) <= 2

    def test_new_factor_exec_failure_returns_zero(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """新因子信号执行失败 → 0（静默放行，不阻断晋升主流程）。"""
        _write_elites(tmp_elite_dir, 2, _CODE_P100)
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_fail", _CODE_BOOM)

        assert loop._count_cluster_members(factor) == 0

    def test_skips_seed_correlation_index(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """_l2_seed_correlation_index.json 索引文件被跳过，不影响计数。"""
        _write_elite(tmp_elite_dir, "fct_elite_b", _CODE_P100, "elite_b")
        (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text(
            json.dumps({"entries": []}),
            encoding="utf-8",
        )
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_b", _CODE_P100)

        assert loop._count_cluster_members(factor) == 1


# ─── 集成: _promote_to_elite 结构簇配额 ─────────────────


class TestPromoteToEliteClusterQuota:
    """_promote_to_elite 中结构簇配额检查。

    集成测试统一用 shadow_observe=False（种子路径）以隔离配额逻辑——
    GAP-I206 相关性检查仅在 shadow_observe=True 时执行，避免交叉干扰。
    """

    def test_quota_rejects_when_cluster_full(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """同类成员 ≥ max_per_cluster(15) → 拒绝晋升且无 JSON 落盘。"""
        _write_elites(tmp_elite_dir, 15, _CODE_P100)
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_full", _CODE_P100)
        evaluation = _make_passing_evaluation("fct_new_full")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=False)

        assert fp is None
        assert not (tmp_elite_dir / "fct_new_full.json").exists()

    def test_quota_allows_below_cap(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """同类成员 14 < max_per_cluster(15) → 放行晋升。"""
        _write_elites(tmp_elite_dir, 14, _CODE_P100)
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_ok", _CODE_P100_ALT)
        evaluation = _make_passing_evaluation("fct_new_ok")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=False)

        assert fp is not None and fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_new_ok"

    def test_quota_ignores_low_corr_cluster(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """既有 elite 全部为噪声（低相关）→ 计数 0 → 放行晋升。"""
        _write_elites(tmp_elite_dir, 20, _CODE_NOISE)
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_noise", _CODE_P100)
        evaluation = _make_passing_evaluation("fct_new_noise")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=False)

        assert fp is not None and fp.exists()

