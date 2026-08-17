"""tests/factor_engine/test_l2_orthogonalize.py — GAP-I206 正交化闭环测试。

覆盖:
    1. _orthogonalize_candidate 方法级: OLS 残差生成 / 残差与参照因子正交性 /
       残差保留比不足拒绝 / 参照 elite 缺失降级
    2. _promote_to_elite 集成: 高相关+残差合格 → 正交化版本入库 /
       高相关+残差不合格 → 拒绝兜底 / 正交化开关关闭 → 原拒绝路径
    3. 配置默认值: l2_elite_orthogonalize 等新配置加载
    4. L3 消费: orthogonalize_factors 对已正交化因子不重复剔除
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fts.factor_engine.contracts import FactorEvaluation
from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.portfolio_loop import orthogonalize_factors


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_evolution_loop.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


@pytest.fixture(autouse=True)
def _disable_qa_gate(monkeypatch):
    """GAP-135/140：本文件测晋升路径（正交化），不测质检门禁——关闭门禁开关
    （mock 因子无审计数据会被一票否决拦截；门禁由 test_gap135_qa_gate.py 独立覆盖）。"""
    from fts.config.settings import get_config as _qc

    monkeypatch.setattr(_qc(), "l2_qa_gate_enabled", False)


# ─── 信号工厂: 控制候选与参照 elite 的相关性与残差保留比 ───
#
# 参照 elite: SIG_ELITE_BASE（arange+100）
# 候选 OK:   SIG_ORTH_OK   = 标准化 arange + 0.4·noise → 与参照 corr≈0.93>0.9，
#            残差保留比≈0.37>0.3 → 正交化成功
# 候选 WEAK: SIG_ORTH_WEAK = 标准化 arange + 0.05·noise → corr≈0.999>0.9，
#            残差保留比≈0.05<0.3 → 残差不合格拒绝
# 候选 BOOM: SIG_BOOM      = 执行抛异常 → 正交化降级 None

_CODE_ELITE_BASE = (
    "def factor_program(data, params):\n    return np.arange(len(data), dtype=float) + 100.0  # SIG_ELITE_BASE"
)
_CODE_ORTH_OK = (
    "def factor_program(data, params):\n"
    "    x = np.arange(len(data), dtype=float)\n"
    "    x = (x - x.mean()) / x.std()\n"
    "    rng = np.random.default_rng(7)\n"
    "    return x + 0.4 * rng.normal(size=len(data))  # SIG_ORTH_OK"
)
_CODE_ORTH_WEAK = (
    "def factor_program(data, params):\n"
    "    x = np.arange(len(data), dtype=float)\n"
    "    x = (x - x.mean()) / x.std()\n"
    "    rng = np.random.default_rng(8)\n"
    "    return x + 0.05 * rng.normal(size=len(data))  # SIG_ORTH_WEAK"
)
_CODE_BOOM = "def factor_program(data, params):\n    raise RuntimeError('boom')  # SIG_BOOM"


def _fake_execute(code: str, data, params):
    """模拟 BacktestPipeline._execute_factor_code: 按 code marker 返回信号。"""
    n = len(data)
    if "SIG_BOOM" in code:
        raise RuntimeError("boom")
    if "SIG_ELITE_BASE" in code:
        return np.arange(n, dtype=float) + 100.0
    if "SIG_ORTH_OK" in code:
        x = np.arange(n, dtype=float)
        x = (x - x.mean()) / x.std()
        return x + 0.4 * np.random.default_rng(7).normal(size=n)
    if "SIG_ORTH_WEAK" in code:
        x = np.arange(n, dtype=float)
        x = (x - x.mean()) / x.std()
        return x + 0.05 * np.random.default_rng(8).normal(size=n)
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
    """构造通过 L3 多重检验的评估（照搬 test_l2_elite_redundancy 模式）。"""
    return FactorEvaluation(
        factor_id=factor_id,
        trace_id="test_trace",
        passed=True,
        failure_reasons=[],
        level_3_multiple={"passed": True},
        # GAP-121: 晋升需携带 ≥2 窗口走航结果（WalkForward 强制门）
        walk_forward={"n_windows_completed": 4, "ic_consistency": 0.75, "passed": True},
        evaluated_at="2026-08-10T00:00:00",
    )


def _mock_repo_clear(loop: EvolutionLoop) -> MagicMock:
    """Mock 去重检查（get_factor_by_name 返回 None），返回 mock repo。"""
    mock_repo = MagicMock()
    mock_repo.get_factor_by_name = MagicMock(return_value=None)
    loop._get_repo = MagicMock(return_value=mock_repo)
    return mock_repo


# ─── 方法级: _orthogonalize_candidate ─────────────────────


class TestOrthogonalizeCandidate:
    """GAP-I206 补充: 正交化残差候选生成。"""

    def test_high_corr_returns_orthogonalized_factor(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """候选与参照 elite 高相关且残差保留独立信息 → 返回正交化因子 dict。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_orth", _CODE_ORTH_OK)
        pair = {"factor_id_b": "fct_elite_ref", "factor_name_b": "elite_ref", "pearson": 0.93}

        orth = loop._orthogonalize_candidate(factor, pair)

        assert orth is not None
        assert orth["orthogonalized"] is True
        assert orth["orthogonalized_against"] == "fct_elite_ref"
        assert orth["orthogonalized_pearson"] > 0.9
        assert len(orth["orthogonal_signal"]) == len(sample_ohlcv)

    def test_residual_orthogonal_to_reference(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """正交化残差与参照 elite 信号相关性应接近 0（< residual_corr_max）。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_orth", _CODE_ORTH_OK)
        pair = {"factor_id_b": "fct_elite_ref", "factor_name_b": "elite_ref", "pearson": 0.93}

        orth = loop._orthogonalize_candidate(factor, pair)
        ref_signal = _fake_execute(_CODE_ELITE_BASE, sample_ohlcv, {})

        resid = np.array([v if v is not None else np.nan for v in orth["orthogonal_signal"]])
        valid = ~(np.isnan(resid) | np.isnan(ref_signal))
        corr = abs(float(np.corrcoef(resid[valid], ref_signal[valid])[0, 1]))

        assert corr < loop._l2_orthogonal_residual_corr_max

    def test_weak_residual_rejected(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """残差保留比不足（独立信息过少）→ None（拒绝兜底）。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_weak", _CODE_ORTH_WEAK)
        pair = {"factor_id_b": "fct_elite_ref", "factor_name_b": "elite_ref", "pearson": 0.999}

        assert loop._orthogonalize_candidate(factor, pair) is None

    def test_missing_ref_elite_returns_none(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """参照 elite JSON 缺失 → None（降级，不阻断晋升主流程）。"""
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        factor = _make_factor("fct_new_miss", _CODE_ORTH_OK)
        pair = {"factor_id_b": "fct_nonexist", "factor_name_b": "?", "pearson": 0.9}

        assert loop._orthogonalize_candidate(factor, pair) is None


# ─── 集成: _promote_to_elite 正交化闭环 ────────────────────


class TestPromoteToEliteOrthogonalize:
    """GAP-I206 补充: 正交化版本入库 / 拒绝兜底。"""

    def test_high_corr_orthogonalized_promotes(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """高相关 + 残差合格 → 正交化版本晋升成功，JSON 含正交化元数据。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_orth", _CODE_ORTH_OK)
        evaluation = _make_passing_evaluation("fct_new_orth")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=True)

        assert fp is not None and fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["orthogonalized"] is True
        assert data["orthogonalized_against"] == "fct_elite_ref"
        assert data["orthogonalized_pearson"] > 0.9
        assert len(data["orthogonal_signal"]) == len(sample_ohlcv)

    def test_high_corr_weak_residual_rejected(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """高相关 + 残差不合格 → 拒绝晋升且无 JSON 落盘（拒绝兜底）。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_weak", _CODE_ORTH_WEAK)
        evaluation = _make_passing_evaluation("fct_new_weak")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=True)

        assert fp is None
        assert not (tmp_elite_dir / "fct_new_weak.json").exists()

    def test_orthogonalize_disabled_rejects(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
        patched_execute,
    ):
        """正交化开关关闭（l2_elite_orthogonalize=False）→ 高相关直接拒绝。"""
        _write_elite(tmp_elite_dir, "fct_elite_ref", _CODE_ELITE_BASE, "elite_ref")
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)
        loop._l2_elite_orthogonalize = False
        _mock_repo_clear(loop)
        factor = _make_factor("fct_new_dis", _CODE_ORTH_OK)
        evaluation = _make_passing_evaluation("fct_new_dis")

        fp = loop._promote_to_elite(factor, evaluation, shadow_observe=True)

        assert fp is None
        assert not (tmp_elite_dir / "fct_new_dis.json").exists()


# ─── 配置默认值 ────────────────────────────────────────────


class TestOrthogonalizeConfig:
    """正交化闭环配置加载。"""

    def test_config_defaults_loaded(
        self,
        sample_ohlcv,
        forward_returns,
        tmp_elite_dir,
        tmp_memory_dir,
    ):
        """新配置默认值正确加载到实例属性。"""
        loop = _make_loop(sample_ohlcv, forward_returns, tmp_elite_dir, tmp_memory_dir)

        assert loop._l2_elite_orthogonalize is True
        assert loop._l2_orthogonal_residual_corr_max == 0.3
        assert loop._l2_orthogonal_min_retained_ratio == 0.3


# ─── L3 消费: 正交化因子不重复剔除 ─────────────────────────


class TestL3Consumption:
    """正交化闭环 L3 端：已正交化因子在相关性正交化中放行。"""

    def test_orthogonalize_factors_skips_orthogonalized(self):
        """correlation_matrix 高相关对中的正交化因子不被剔除（保留 retained=True）。"""
        signals = [
            {"factor_id": "fct_a", "name": "a", "sharpe": 2.0, "retained": True},
            # 正交化因子：L2 已剥离相关成分，L3 不应重复剔除
            {
                "factor_id": "fct_b",
                "name": "b",
                "sharpe": 1.8,
                "retained": True,
                "orthogonalized": True,
                "orthogonalized_against": "fct_a",
            },
        ]
        corr_matrix = [
            {"factor_id_a": "fct_a", "factor_id_b": "fct_b", "pearson": 0.85},
        ]

        out = orthogonalize_factors(signals, correlation_matrix=corr_matrix, max_corr_threshold=0.7)

        retained_ids = {s["factor_id"] for s in out if s.get("retained", True)}
        assert "fct_b" in retained_ids  # 正交化因子放行

    def test_orthogonalize_factors_removes_non_orthogonalized(self):
        """非正交化高相关因子仍被剔除（原逻辑不受影响）。"""
        signals = [
            {"factor_id": "fct_a", "name": "a", "sharpe": 2.0, "retained": True},
            {"factor_id": "fct_c", "name": "c", "sharpe": 1.8, "retained": True},
        ]
        corr_matrix = [
            {"factor_id_a": "fct_a", "factor_id_b": "fct_c", "pearson": 0.85},
        ]

        out = orthogonalize_factors(signals, correlation_matrix=corr_matrix, max_corr_threshold=0.7)

        retained_ids = {s["factor_id"] for s in out if s.get("retained", True)}
        assert "fct_c" not in retained_ids  # 非正交化高相关因子被剔除
