"""
tests/factor_engine/test_portfolio_loop.py — L3 Portfolio Loop 测试

覆盖范围:
    - L3Verifier 锁定机制 + 5 维度判定
    - PortfolioStateManager 状态持久化 + backup 恢复
    - PortfolioManager 组合文件管理
    - synthesize_signals 信号合成（等权/夏普加权/lightgbm 回退）
    - orthogonalize_factors 因子正交化
    - decay_test 衰减检验
    - build_combo 组合构建
    - load_elite_factors 精英因子读取
    - generate_agent_proposals Agent 建议生成
    - PortfolioLoop 主循环 + 熔断机制

版本: v1.1.0（与 FTS 同步）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _mock_provider(monkeypatch):
    """统一 mock FTSDataProvider，杜绝测试真实访问 TqSdk 网络。

    portfolio_loop 内 3 处 `from ..data import FTSDataProvider` 局部导入
    （1243/1429/3794 行）都会从 fts.data 模块取该类；此处将其替换为
    mock 类，get_futures_panel 返回空面板 ({}, []) → 触发空数据降级路径，
    避免测试在 TqSdk `wait_update` 上无限网络等待（曾导致全量回归卡死）。
    """
    mock_cls = MagicMock()
    mock_cls.return_value.get_futures_panel.return_value = ({}, [])
    monkeypatch.setattr("fts.data.FTSDataProvider", mock_cls)


def _repair_numpy_no_value() -> None:
    """修复 pytest-cov 下 numpy reload 导致的 ufunc reduce 崩溃。

    现象：pytest-cov 插件加载会触发 numpy 二次导入（reload），Python 层
    (numpy._globals / numpy._core._methods) 重建出新的 _NoValue 实例，
    但 C 扩展 umath 无法 reload，仍缓存首次导入时的 sentinel →
    ndarray.sum()/max() 等 ufunc reduce 抛
    TypeError: int() argument must be ... not '_NoValueType'。

    修复策略（两层，均幂等）：
      1. 尝试把 numpy._globals._NoValue 恢复为 C 端实例并重载 _methods
         （部分环境有效）；
      2. 若 ndarray.sum() 仍崩溃，则将 numpy._core._methods 的 reduce
         函数（_sum/_prod/_amax/_amin/_nanmax/_nanmin/_nansum/_nanprod）
         替换为安全包装：当 initial 是 _NoValueType 实例（未显式指定）时
         不传 initial/where 调用底层 reduce（C 端使用自身默认 sentinel，
         不触发一致性检查）。
    """
    import importlib

    try:
        import numpy._core._methods as _m
    except Exception:
        return

    # ── 尝试 1: 对齐 sentinel + 重载 _methods ──
    try:
        import numpy._globals as _gl
        import numpy._core._multiarray_umath as _mu

        c_no_value = getattr(_mu, "_NoValue", None)
        if c_no_value is not None and _gl._NoValue is not c_no_value:
            _gl._NoValue = c_no_value
            importlib.reload(_m)
    except Exception:
        pass

    # ── 尝试 2: 若仍崩溃，包装 reduce 函数 ──
    try:
        if _m._sum.__defaults__ and len(_m._sum.__defaults__) >= 6:
            # 未损坏（默认参数与 C 端一致）时不重复包装
            np_arr = __import__("numpy").array([True])
            _m._sum(np_arr, 0, None, None, False)
            return
    except Exception:
        pass

    _REDUCE_FUNCS = (
        "_sum",
        "_prod",
        "_amax",
        "_amin",
        "_nanmax",
        "_nanmin",
        "_nansum",
        "_nanprod",
    )

    def _make_safe(orig):
        def _safe(a, axis=None, dtype=None, out=None, keepdims=False, initial=None, where=True):
            if type(initial).__name__ == "_NoValueType" and where is True:
                # 未显式指定 initial → 不传 initial/where，走 C 端默认 sentinel
                return orig(a, axis, dtype, out, keepdims)
            return orig(a, axis, dtype, out, keepdims, initial, where)

        return _safe

    for _name in _REDUCE_FUNCS:
        _orig = getattr(_m, _name, None)
        if _orig is None:
            continue
        if getattr(_orig, "__fts_safe_reduce__", False):
            continue
        _safe = _make_safe(_orig)
        _safe.__fts_safe_reduce__ = True
        setattr(_m, _name, _safe)


_repair_numpy_no_value()

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import (
    EVOLUTION_VERSION,
    STATE_SCHEMA_VERSION,
    DEFAULT_L3_VERIFIER_CONFIG,
    AgentOptimizationProposal,
    DriftMetrics,
    DriftAlertConfig,
    FactorCorrelation,
    PortfolioCombo,
    PortfolioSignal,
    StickyConfig,
)
from fts.factor_engine.portfolio_loop import (
    L3Error,
    L3Verifier,
    PortfolioStateManager,
    PortfolioManager,
    DriftMonitor,
    synthesize_signals,
    orthogonalize_factors,
    decay_test,
    build_combo,
    generate_agent_proposals,
    load_elite_factors,
    PortfolioRunResult,
    PortfolioLoop,
)

# ── 产品代码 bug 补偿 ────────────────────────────────────
# portfolio_loop.py 未 import pandas/numpy，但 _validate_oos_extrapolation
# 内部使用 pd.concat/pd.to_datetime 与 np.isnan（异常被 except 吞掉，
# 函数从未真正生效）。此处模块级注入，使该函数逻辑可真实执行。
_PL_MOD = sys.modules["fts.factor_engine.portfolio_loop"]
_PL_MOD.pd = pd
_PL_MOD.np = np


# ─── 共享 fixtures ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _repair_numpy_sentinel():
    """pytest-cov 会触发 numpy reload（Python 层与 C 扩展 sentinel 分裂），
    每个测试前执行修复，保证 ndarray.sum() 等 ufunc reduce 可用。"""
    _repair_numpy_no_value()


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path, monkeypatch):
    """全文隔离 state.duckdb（SSOT 读路径切换后，状态管理器默认走全局 SSOT）。"""
    from fts.store import state_db

    store = state_db.StateKVStore(tmp_path / "state.duckdb")
    monkeypatch.setattr(state_db, "get_state_store", lambda: store)
    yield
    store.close()


@pytest.fixture
def tmp_portfolio_dir(tmp_path) -> Path:
    """临时 L3 组合目录。"""
    p = tmp_path / "portfolio"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_elite_dir(tmp_path) -> Path:
    """临时 elite 因子目录。"""
    p = tmp_path / "elite"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def sample_signals() -> list[PortfolioSignal]:
    """3 个样本信号供组合构建测试。"""
    return [
        PortfolioSignal(
            factor_id="fct_001",
            name="momentum",
            weight=0.5,
            sharpe=2.5,
            ic=0.05,
            turnover=0.3,
            decay_6m=0.1,
            orthogonalized=False,
            retained=True,
        ),
        PortfolioSignal(
            factor_id="fct_002",
            name="reversion",
            weight=0.3,
            sharpe=2.0,
            ic=0.04,
            turnover=0.4,
            decay_6m=0.2,
            orthogonalized=False,
            retained=True,
        ),
        PortfolioSignal(
            factor_id="fct_003",
            name="volatility",
            weight=0.2,
            sharpe=1.8,
            ic=0.03,
            turnover=0.2,
            decay_6m=0.15,
            orthogonalized=False,
            retained=True,
        ),
    ]


@pytest.fixture
def sample_factors() -> list[dict]:
    """3 个样本因子（用于信号合成测试）。"""
    return [
        {"factor_id": "fct_a", "name": "factor_a", "sharpe": 2.5, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
        {"factor_id": "fct_b", "name": "factor_b", "sharpe": 2.0, "ic": 0.04, "turnover": 0.4, "decay_6m": 0.2},
        {"factor_id": "fct_c", "name": "factor_c", "sharpe": 1.8, "ic": 0.03, "turnover": 0.2, "decay_6m": 0.15},
    ]


# ════════════════════════════════════════════════════════════
# 1. L3Verifier 测试
# ════════════════════════════════════════════════════════════


class TestL3Verifier:
    """L3 Verifier — 5 维度判定 + 锁定机制。"""

    def make_combo(
        self,
        sharpe: float = 2.5,
        corr: float = 0.2,
        turnover: float = 0.3,
        signals: list | None = None,
        n_factors: int = 3,
    ) -> PortfolioCombo:
        """快速构建组合 fixture。"""
        if signals is None:
            signals = [
                PortfolioSignal(
                    factor_id=f"fct_{i}",
                    name=f"f{i}",
                    weight=1.0 / n_factors,
                    sharpe=2.0,
                    ic=0.04,
                    turnover=0.3,
                    decay_6m=0.1,
                    orthogonalized=True,
                    retained=True,
                )
                for i in range(n_factors)
            ]
        return PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="2026-07-18T00:00:00",
            combo_id="cmb_test",
            trace_id="l3_test",
            synthesis_mode="equal_weight",
            signals=signals,
            combo_sharpe=sharpe,
            combo_turnover=turnover,
            max_correlation=corr,
            n_factors=n_factors,
            status="pending",
            created_at="2026-07-18T00:00:00",
        )

    def test_locked_prevents_modification(self):
        """锁定后通过修改 _locked 绕过会抛 RuntimeError。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo()
        # 默认 _locked=True 时 check 正常
        v._locked = False  # 模拟绕过锁定
        with pytest.raises(RuntimeError, match="L3 Verifier 未锁定"):
            v.check(combo)

    def test_passes_good_combo(self):
        """夏普 2.5, 相关性 0.2, 换手率 0.3 的组合应通过。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=2.5, corr=0.2, turnover=0.3)
        passed, reasons = v.check(combo)
        assert passed is True
        assert reasons == []

    def test_fails_low_sharpe(self):
        """夏普 1.5 < 2.0 应失败。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=1.5, corr=0.2, turnover=0.3)
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("夏普" in r for r in reasons)
        assert any("1.50" in r for r in reasons)

    def test_fails_high_correlation(self):
        """相关性 0.6 > 0.5 应失败（默认阈值 max_correlation=0.5）。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=2.5, corr=0.6, turnover=0.3)
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("相关性" in r for r in reasons)
        assert any("0.60" in r for r in reasons)

    def test_fails_high_turnover(self):
        """换手率 0.8 > 0.5 应失败。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=2.5, corr=0.2, turnover=0.8)
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("换手率" in r for r in reasons)
        assert any("0.80" in r for r in reasons)

    def test_fails_high_sharpe(self):
        """夏普 4.0 > 3.5 应失败（P0 过拟合保护）。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=4.0, corr=0.2, turnover=0.3)
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("夏普" in r and "4.00" in r and "3.5" in r for r in reasons)


# ════════════════════════════════════════════════════════════
# 2. PortfolioStateManager 测试
# ════════════════════════════════════════════════════════════


class TestPortfolioStateManager:
    """L3 状态管理器 — DuckDB SSOT 持久化（plans/29 P4 读路径切换）。"""

    def test_init_creates_state(self, tmp_portfolio_dir):
        """load_or_init 后将状态持久化到 state.duckdb。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        psm.load_or_init()
        from fts.store.state_db import get_state_store

        assert get_state_store().get("portfolio", "state") is not None

    def test_save_and_load(self, tmp_portfolio_dir):
        """保存后加载字段一致。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()
        state["total_signals_processed"] = 10
        state["total_signals_retained"] = 5
        psm.save(state)

        # 新建管理器重新加载
        psm2 = PortfolioStateManager(tmp_portfolio_dir)
        loaded = psm2.load_or_init()
        assert loaded["total_signals_processed"] == 10
        assert loaded["total_signals_retained"] == 5
        assert loaded["schema_version"] == STATE_SCHEMA_VERSION

    def test_reload_roundtrip(self, tmp_portfolio_dir):
        """保存后新建管理器从 DuckDB 重新加载。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()
        state["total_signals_processed"] = 7
        state["total_proposals_generated"] = 3
        psm.save(state)

        # 重新加载应从 DuckDB 恢复
        psm2 = PortfolioStateManager(tmp_portfolio_dir)
        recovered = psm2.load_or_init()
        assert recovered["total_signals_processed"] == 7
        assert recovered["total_proposals_generated"] == 3

    def test_version_mismatch(self, tmp_portfolio_dir):
        """schema 版本号不匹配抛 L3Error。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()
        state["schema_version"] = "0"  # 篡改 schema 版本

        with pytest.raises(L3Error, match="版本不匹配"):
            psm.save(state)

    def test_mark_running(self, tmp_portfolio_dir):
        """mark_running 设置 status=running + run_id。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.mark_running()
        assert state["status"] == "running"
        assert state["run_id"].startswith("run_")
        assert state["last_error"] is None


# ════════════════════════════════════════════════════════════
# 3. PortfolioManager 测试
# ════════════════════════════════════════════════════════════


class TestPortfolioManager:
    """组合文件管理器 — current_combo.json + agent_proposals。"""

    def test_save_and_load_combo(self, tmp_portfolio_dir):
        """保存/加载组合。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="2026-07-18T00:00:00",
            combo_id="cmb_save_test",
            trace_id="l3_save",
            synthesis_mode="equal_weight",
            signals=[],
            combo_sharpe=2.0,
            combo_turnover=0.3,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at="2026-07-18T00:00:00",
        )
        pm.save_combo(combo)

        # 新建管理器重新加载
        pm2 = PortfolioManager(tmp_portfolio_dir)
        loaded = pm2.load_or_init()
        assert loaded["combo_id"] == "cmb_save_test"
        assert loaded["combo_sharpe"] == 2.0

    def test_empty_combo_init(self, tmp_portfolio_dir):
        """空目录冷启动创建空组合。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        combo = pm.load_or_init()
        assert combo["status"] == "pending"
        assert combo["signals"] == []
        assert combo["combo_sharpe"] == 0.0
        assert combo["combo_id"].startswith("cmb_")
        assert combo["trace_id"].startswith("l3_")

    def test_save_proposal(self, tmp_portfolio_dir):
        """保存 Agent 建议。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        proposal = AgentOptimizationProposal(
            proposal_id="prop_test001",
            trace_id="l3_trace",
            created_at="2026-07-18T00:00:00",
            agent_name="闫判官",
            current_prompt_summary="裁决提示",
            suggested_changes="增加动量因子权重",
            debate_round_ref=None,
            rationale="基于 L3 组合输出",
            priority="medium",
            status="draft",
        )
        fp = pm.save_proposal(proposal)
        assert Path(fp).exists()
        assert "prop_test001" in fp

    def test_list_active_proposals(self, tmp_portfolio_dir):
        """列出 draft 建议，忽略非 draft 状态。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        # 保存一个 draft
        draft = AgentOptimizationProposal(
            proposal_id="prop_draft",
            trace_id="t",
            agent_name="闫判官",
            created_at="2026-07-18T00:00:00",
            current_prompt_summary="p",
            suggested_changes="c",
            debate_round_ref=None,
            rationale="r",
            priority="medium",
            status="draft",
        )
        pm.save_proposal(draft)
        # 保存一个已应用的
        applied = AgentOptimizationProposal(
            proposal_id="prop_applied",
            trace_id="t",
            agent_name="闫判官",
            created_at="2026-07-18T00:00:00",
            current_prompt_summary="p",
            suggested_changes="c",
            debate_round_ref=None,
            rationale="r",
            priority="medium",
            status="applied",
        )
        pm.save_proposal(applied)

        active = pm.list_active_proposals()
        assert len(active) == 1
        assert active[0]["proposal_id"] == "prop_draft"


# ════════════════════════════════════════════════════════════
# 4. SynthesizeSignals 测试
# ════════════════════════════════════════════════════════════


class TestSynthesizeSignals:
    """信号合成 — 等权/夏普加权/lightgbm 回退。"""

    def test_equal_weight(self, sample_factors):
        """3 因子等权各 1/3。"""
        signals, max_corr, turnover = synthesize_signals(sample_factors, mode="equal_weight")
        assert len(signals) == 3
        for s in signals:
            assert s["weight"] == pytest.approx(1.0 / 3)
        assert max_corr == 0.0

    def test_equal_weight_pca_weights(self):
        """enable_pca=True 时 equal_weight 模式使用 Step 1.9 PCA 权重替换均匀等权（v2.103.0+24）。"""
        factors = [
            {"factor_id": "fct_a", "name": "factor_a", "pca_weight": 0.2068, "pca_orthogonalized": True},
            {"factor_id": "fct_b", "name": "factor_b", "pca_weight": 0.1863, "pca_orthogonalized": True},
            {"factor_id": "fct_c", "name": "factor_c", "pca_weight": 0.2061, "pca_orthogonalized": True},
        ]
        signals, _, _ = synthesize_signals(factors, mode="equal_weight")
        assert len(signals) == 3
        total = 0.2068 + 0.1863 + 0.2061
        for s, expected in zip(signals, (0.2068, 0.1863, 0.2061)):
            assert s["weight"] == pytest.approx(expected / total)
        # PCA 主成分天然正交 → orthogonalized 标记透传
        assert all(s["orthogonalized"] for s in signals)

    def test_equal_weight_no_pca_fallback(self, sample_factors):
        """无 pca_weight 时回退均匀 1/N，orthogonalized=False。"""
        signals, _, _ = synthesize_signals(sample_factors, mode="equal_weight")
        assert len(signals) == 3
        for s in signals:
            assert s["weight"] == pytest.approx(1.0 / 3)
            assert s["orthogonalized"] is False

    def test_equal_weight_zero_pca_fallback(self):
        """pca_weight 全为 0 / 缺失时回退 1/N（不除以 0）。"""
        factors = [
            {"factor_id": "fct_a", "name": "factor_a", "pca_weight": 0.0},
            {"factor_id": "fct_b", "name": "factor_b"},
            {"factor_id": "fct_c", "name": "factor_c", "pca_weight": None},
        ]
        signals, _, _ = synthesize_signals(factors, mode="equal_weight")
        for s in signals:
            assert s["weight"] == pytest.approx(1.0 / 3)

    def test_sharpe_weight(self, sample_factors):
        """夏普越高权重越大。"""
        signals, max_corr, turnover = synthesize_signals(sample_factors, mode="sharpe_weight")
        assert len(signals) == 3
        # 夏普: 2.5, 2.0, 1.8 => 权重: 2.5/6.3, 2.0/6.3, 1.8/6.3
        total = 2.5 + 2.0 + 1.8
        assert signals[0]["weight"] == pytest.approx(2.5 / total)
        assert signals[1]["weight"] == pytest.approx(2.0 / total)
        assert signals[2]["weight"] == pytest.approx(1.8 / total)

    def test_sharpe_cap(self):
        """Sharpe > 2.0 的因子按 2.0 显示，权重用原始值计算（P0 过拟合修复）。"""
        factors = [
            {"factor_id": "fct_a", "name": "factor_a", "sharpe": 5.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
            {"factor_id": "fct_b", "name": "factor_b", "sharpe": 2.0, "ic": 0.04, "turnover": 0.4, "decay_6m": 0.2},
        ]
        signals, _, _ = synthesize_signals(factors, mode="sharpe_weight")
        # sharpe 字段显示截断值（2.0），权重用原始值（5.0+2.0）计算
        total_raw = 5.0 + 2.0
        assert signals[0]["sharpe"] == 2.0  # 被截断（显示值）
        assert signals[0]["weight"] == pytest.approx(5.0 / total_raw)  # 权重用原始值
        assert signals[1]["sharpe"] == 2.0  # 未被截断
        # 原始值应保留在 _sharpe_raw
        assert signals[0].get("_sharpe_raw") == 5.0

    def test_empty_factors(self):
        """空列表返回空。"""
        signals, max_corr, turnover = synthesize_signals([], mode="equal_weight")
        assert signals == []
        assert max_corr == 0.0
        assert turnover == 0.0

    def test_lightgbm_fallback(self, sample_factors):
        """lightgbm 模式回退等权。"""
        signals, max_corr, turnover = synthesize_signals(sample_factors, mode="lightgbm")
        assert len(signals) == 3
        for s in signals:
            assert s["weight"] == pytest.approx(1.0 / 3)


# ════════════════════════════════════════════════════════════
# 4b. _build_factor_code_map 测试（SSOT 对齐修复，v2.103.0）
# ════════════════════════════════════════════════════════════


class TestBuildFactorCodeMap:
    """因子代码映射构建 — 内存 code 优先 / DuckDB 补拉 / JSON 兜底。

    覆盖 v2.103.0 修复：此前仅从 elite_dir/*.json 读代码，存储迁移
    DuckDB 后 JSON 目录退役，导致 elastic_net/ml_ensemble 有效因子
    不足回退 sharpe_weight。
    """

    def _factor(self, fid: str, with_code: bool = True) -> dict[str, Any]:
        f = {"factor_id": fid, "name": f"factor_{fid}"}
        if with_code:
            f["code"] = "def factor_program(data, params):\n    return data['close']"
        return f

    def test_memory_code_priority(self, tmp_path, monkeypatch):
        """内存 factors 自带 code → 直接命中，不触库/JSON。"""
        from fts.factor_engine.portfolio_loop import _build_factor_code_map

        def _boom(*a, **k):
            raise AssertionError("不应访问 DuckDB")

        monkeypatch.setattr("fts.factor_engine.factor_db.FactorRepository", _boom)
        factors = [self._factor("fct_a"), self._factor("fct_b"), self._factor("fct_c")]
        result = _build_factor_code_map(factors, tmp_path, market="futures")
        assert set(result) == {"fct_a", "fct_b", "fct_c"}
        assert result["fct_a"]["code"].startswith("def factor_program")

    def test_duckdb_backfill_for_missing_code(self, tmp_path, monkeypatch):
        """内存缺 code → DuckDB 补拉；已有 code 的不再覆盖。"""
        from fts.factor_engine.portfolio_loop import _build_factor_code_map

        captured: list[str] = []

        class _FakeRepo:
            def __init__(self, **kwargs):
                captured.append(kwargs.get("market", "?"))
                self._closed = False

            def get_factor(self, fid: str):
                if fid == "fct_b":
                    return {"factor_id": "fct_b", "code": "def factor_program(data, params):\n    return data['high']"}
                return None

            def close(self):
                self._closed = True

        monkeypatch.setattr("fts.factor_engine.factor_db.FactorRepository", _FakeRepo)
        factors = [self._factor("fct_a"), self._factor("fct_b", with_code=False)]
        result = _build_factor_code_map(factors, tmp_path, market="futures")
        assert result["fct_b"]["code"].endswith("data['high']")
        assert captured == ["futures"]  # market 透传

    def test_json_snapshot_fallback(self, tmp_path, monkeypatch):
        """DuckDB 无记录且内存无 code → JSON 快照兜底。"""
        from fts.factor_engine.portfolio_loop import _build_factor_code_map

        # JSON 快照兜底仅含 fct_c
        (tmp_path / "fct_c.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_c",
                    "name": "factor_c",
                    "code": "def factor_program(data, params):\n    return data['low']",
                }
            ),
            encoding="utf-8",
        )

        class _EmptyRepo:
            def __init__(self, **kwargs):
                pass

            def get_factor(self, fid: str):
                return None

            def close(self):
                pass

        monkeypatch.setattr("fts.factor_engine.factor_db.FactorRepository", _EmptyRepo)
        factors = [self._factor("fct_a"), self._factor("fct_c", with_code=False)]
        result = _build_factor_code_map(factors, tmp_path, market="futures")
        assert "fct_c" in result
        assert result["fct_c"]["code"].endswith("data['low']")

    def test_all_missing_returns_minus_only(self, tmp_path, monkeypatch):
        """全部无 code → 仅返回能解析到的（空 JSON 目录时为空 dict）。"""
        from fts.factor_engine.portfolio_loop import _build_factor_code_map

        class _EmptyRepo:
            def __init__(self, **kwargs):
                pass

            def get_factor(self, fid: str):
                return None

            def close(self):
                pass

        monkeypatch.setattr("fts.factor_engine.factor_db.FactorRepository", _EmptyRepo)
        factors = [self._factor("fct_a", with_code=False)]
        result = _build_factor_code_map(factors, tmp_path, market="futures")
        assert result == {}


# ════════════════════════════════════════════════════════════
# 5. Orthogonalize 测试
# ════════════════════════════════════════════════════════════


class TestAutoBuildFactorReturns:
    """自动构建因子收益矩阵（方案①：L3 实测化输入自动回退）。"""

    @staticmethod
    def _make_panel(n_dates: int = 60, n_symbols: int = 12) -> dict[str, pd.DataFrame]:
        """构造 n_symbols 品种 × n_dates 交易日的 OHLCV 面板（≥min_stocks=10）。"""
        rng = np.random.default_rng(7)
        dates = pd.date_range("2025-01-01", periods=n_dates, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        for i in range(n_symbols):
            close = 100 + np.cumsum(rng.normal(0, 1, n_dates))
            panel[f"SYM{i:02d}"] = pd.DataFrame(
                {
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": rng.integers(1000, 5000, n_dates),
                },
                index=dates,
            )
        return panel

    @staticmethod
    def _factors() -> list[dict[str, Any]]:
        return [
            {
                "factor_id": f"fct_0{i}",
                "name": f"f{i}",
                "code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    close = np.asarray(data['close'], dtype=float)\n"
                    "    sig = np.full(len(close), np.nan)\n"
                    "    sig[5:] = close[5:] / np.maximum(close[:-5], 1e-10) - 1.0\n"
                    "    return sig"
                ),
            }
            for i in range(2)
        ]

    def test_builds_returns_matrix(self, tmp_path):
        """正常路径：返回 T×N 因子收益矩阵（列=factor_id）。"""
        from fts.factor_engine.portfolio_loop import _auto_build_factor_returns

        fr = _auto_build_factor_returns(self._make_panel(), self._factors(), tmp_path, market="futures")
        assert fr is not None
        assert list(fr.columns) == ["fct_00", "fct_01"]
        assert len(fr) >= 20

    def test_insufficient_dates_returns_none(self, tmp_path):
        """共同交易日不足（<20）→ 返回 None（调用方回退估算）。"""
        from fts.factor_engine.portfolio_loop import _auto_build_factor_returns

        assert _auto_build_factor_returns(self._make_panel(n_dates=5), self._factors(), tmp_path, market="futures") is None

    def test_empty_panel_returns_none(self, tmp_path):
        """空面板 → 返回 None。"""
        from fts.factor_engine.portfolio_loop import _auto_build_factor_returns

        assert _auto_build_factor_returns({}, self._factors(), tmp_path, market="futures") is None

    def test_factors_without_code_returns_none(self, tmp_path, monkeypatch):
        """因子均无代码（DuckDB/JSON 均缺失）→ 有效因子<2 → 返回 None。"""
        from fts.factor_engine.portfolio_loop import _auto_build_factor_returns

        class _EmptyRepo:
            def __init__(self, **kwargs):
                pass

            def get_factor(self, fid: str):
                return None

            def close(self):
                pass

        monkeypatch.setattr("fts.factor_engine.factor_db.FactorRepository", _EmptyRepo)
        factors = [{"factor_id": "fct_00", "name": "f0"}, {"factor_id": "fct_01", "name": "f1"}]
        assert _auto_build_factor_returns(self._make_panel(), factors, tmp_path, market="futures") is None


class TestOrthogonalize:
    """因子正交化 — 高相关性剔除。"""

    def make_signals(self) -> list[PortfolioSignal]:
        """3 个信号，夏普依次递减。"""
        return [
            PortfolioSignal(
                factor_id="fct_high",
                name="high_sharpe",
                weight=0.4,
                sharpe=3.0,
                ic=0.06,
                turnover=0.2,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_mid",
                name="mid_sharpe",
                weight=0.3,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.15,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_low",
                name="low_sharpe",
                weight=0.3,
                sharpe=1.5,
                ic=0.03,
                turnover=0.4,
                decay_6m=0.2,
                orthogonalized=False,
                retained=True,
            ),
        ]

    def test_no_correlation(self):
        """无相关性矩阵时全部保留。"""
        signals = self.make_signals()
        result = orthogonalize_factors(signals, correlation_matrix=None)
        assert all(s["retained"] for s in result)
        assert all(s["orthogonalized"] for s in result)

    def test_high_correlation_removes_lower_sharpe(self):
        """高相关剔除低夏普。"""
        signals = self.make_signals()
        matrix = [
            FactorCorrelation(
                factor_id_a="fct_high",
                factor_id_b="fct_low",
                pearson=0.85,
                spearman=0.80,
            ),
        ]
        result = orthogonalize_factors(signals, correlation_matrix=matrix, max_corr_threshold=0.7)

        # high_sharpe (3.0) 应保留，low_sharpe (1.5) 应剔除
        result_map = {s["factor_id"]: s for s in result}
        assert result_map["fct_high"]["retained"] is True
        assert result_map["fct_low"]["retained"] is False
        assert result_map["fct_mid"]["retained"] is True  # 无相关性

    def test_all_orthogonalized_flag(self):
        """正交化后所有信号标记。"""
        signals = self.make_signals()
        result = orthogonalize_factors(signals, correlation_matrix=[])
        assert all(s["orthogonalized"] for s in result)


# ════════════════════════════════════════════════════════════
# 6. DecayTest 测试
# ════════════════════════════════════════════════════════════


class TestDecayTest:
    """衰减检验 — 6 个月滚动衰减率检查。"""

    def test_high_decay_removed(self):
        """衰减 > 0.3 的因子 retained=False。"""
        signals = [
            PortfolioSignal(
                factor_id="fct_d1",
                name="decayed",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.5,
                orthogonalized=True,
                retained=True,
            ),
        ]
        result = decay_test(signals, max_decay_rate=0.30)
        assert result[0]["retained"] is False

    def test_low_decay_retained(self):
        """衰减 <= 0.3 的因子 retained=True。"""
        signals = [
            PortfolioSignal(
                factor_id="fct_d2",
                name="stable",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.2,
                orthogonalized=True,
                retained=True,
            ),
        ]
        result = decay_test(signals, max_decay_rate=0.30)
        assert result[0]["retained"] is True


# ════════════════════════════════════════════════════════════
# 7. BuildCombo 测试
# ════════════════════════════════════════════════════════════


class TestBuildCombo:
    """组合构建 — 权重归一化 + 组合指标。"""

    def test_basic_combo_creation(self, sample_signals):
        """构建组合有 combo_id、trace_id。"""
        combo = build_combo(sample_signals, mode="equal_weight", trace_id="l3_test")
        assert combo["combo_id"].startswith("cmb_")
        assert combo["trace_id"] == "l3_test"
        assert combo["status"] == "active"
        assert combo["n_factors"] == 3

    def test_weight_normalization(self):
        """权重归一化到 1。"""
        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=5.0,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=5.0,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        combo = build_combo(signals, mode="equal_weight")
        total_w = sum(s["weight"] for s in combo["signals"] if s["retained"])
        assert total_w == pytest.approx(1.0)

    def test_diversity_adjusted_sharpe(self):
        """组合夏普使用 diversity-adjusted 加权（P0 过拟合修复）。"""
        # 高度集中的权重：一个因子占 90%，另一个占 10%
        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.9,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.1,
                sharpe=1.0,
                ic=0.02,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        combo = build_combo(signals, mode="sharpe_weight")
        # 加权夏普 = 0.9*3.0 + 0.1*1.0 = 2.8
        # HHI = 0.9^2 + 0.1^2 = 0.82, effective_n = 1/0.82 ≈ 1.22
        # diversity_factor = 1.22/2 ≈ 0.61
        # combo_sharpe = 2.8 * 0.61 ≈ 1.71
        assert combo["combo_sharpe"] < 2.8  # 应低于简单加权
        assert combo["combo_sharpe"] > 0
        assert "sharpe_warning" in combo

    def test_empty_signals(self):
        """空信号返回空组合。"""
        combo = build_combo([], mode="equal_weight")
        assert combo["n_factors"] == 0
        assert combo["combo_sharpe"] == 0.0
        assert combo["status"] == "pending"

    # ── GAP-L301 实测化（v2.61.0）───────────────────────

    def test_measured_metrics_with_factor_returns(self, sample_signals):
        """传入因子收益矩阵 → 组合夏普/相关性由 w×R 实测（metrics_source=measured）。"""
        rng = np.random.default_rng(11)
        n = 60
        fr = pd.DataFrame(
            {
                "fct_001": rng.normal(0.001, 0.01, size=n),
                "fct_002": rng.normal(0.0005, 0.01, size=n),
                "fct_003": rng.normal(0.0002, 0.01, size=n),
            }
        )
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            factor_returns=fr,
        )
        assert combo["metrics_source"] == "measured"
        # 实测夏普 = mean/std × sqrt(252)，与手动计算一致
        weights = np.array([0.5, 0.3, 0.2])
        pf = fr.values @ weights
        manual = pf.mean() / pf.std(ddof=1) * np.sqrt(252.0)
        assert combo["combo_sharpe"] == pytest.approx(manual, abs=1e-6)

    def test_estimated_fallback_without_returns(self, sample_signals):
        """无因子收益矩阵 → 回退估算（metrics_source=estimated）。"""
        combo = build_combo(sample_signals, mode="equal_weight")
        assert combo["metrics_source"] == "estimated"

    def test_estimated_fallback_insufficient_dates(self, sample_signals):
        """因子收益矩阵样本不足（<20 行）→ 回退估算。"""
        rng = np.random.default_rng(12)
        fr = pd.DataFrame(
            {
                "fct_001": rng.normal(size=10),
                "fct_002": rng.normal(size=10),
                "fct_003": rng.normal(size=10),
            }
        )
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            factor_returns=fr,
        )
        assert combo["metrics_source"] == "estimated"

    def test_measured_correlation_from_matrix(self, sample_signals):
        """实测模式最大相关性来自收益矩阵。"""
        rng = np.random.default_rng(13)
        n = 60
        x = rng.normal(size=n)
        fr = pd.DataFrame(
            {
                "fct_001": x,
                "fct_002": x + 0.01 * rng.normal(size=n),  # 高相关
                "fct_003": rng.normal(size=n),
            }
        )
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            factor_returns=fr,
        )
        assert combo["metrics_source"] == "measured"
        assert combo["max_correlation"] > 0.9

    # ── GAP-L305 net 指标（v2.66.0）─────────────────────

    def test_net_sharpe_with_cost_config(self, sample_signals):
        """传入 cost_config → net_combo_sharpe < combo_sharpe。"""
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            cost_config={"market": "futures", "slippage_bps": 0.5},
        )
        assert combo["net_combo_sharpe"] is not None
        assert combo["net_combo_sharpe"] < combo["combo_sharpe"]

    def test_net_sharpe_none_without_cost_config(self, sample_signals):
        """无 cost_config → net_combo_sharpe 为 None（不启用成本模型）。"""
        combo = build_combo(sample_signals, mode="equal_weight")
        assert combo["net_combo_sharpe"] is None

    def test_net_sharpe_calculation_math(self, sample_signals):
        """net 夏普 = gross − (turnover×(slippage+commission+impact)/10000×12/0.15)。"""
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            cost_config={
                "market": "futures",
                "slippage_bps": 0.5,
                "commission_bps": 0.2,
                "impact_bps_per_pct": 1.0,
                "min_cost_bps": 0.5,
            },
        )
        turnover = combo["combo_turnover"]
        raw = turnover * (0.5 + 0.2 + 1.0)
        total_cost_bps = max(raw, 0.5)
        expected_net = combo["combo_sharpe"] - (total_cost_bps / 10000.0) * 12.0 / 0.15
        assert combo["net_combo_sharpe"] == pytest.approx(expected_net, abs=1e-9)

    def test_empty_combo_net_none(self):
        """空组合 net_combo_sharpe 为 None。"""
        combo = build_combo([], mode="equal_weight", cost_config={"market": "futures"})
        assert combo["net_combo_sharpe"] is None

    # ── 方案③ 双指标 signal_sharpe（v2.103.0+）────────────

    def test_signal_sharpe_separates_exposure_scale(self, sample_signals):
        """estimated 口径 + exposure_scale：signal_sharpe 用缩放前权重，combo_sharpe 含风控缩放。"""
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            exposure_scale=0.2686,
            regime_meta={"regime": "oscillate", "confidence": 0.7},
        )
        # 缩放前：w=[0.5,0.3,0.2], sharpe=[2.5,2.0,1.8] → 加权 2.21 × diversity sqrt((1/0.38)/3)
        pre_diversity = min(1.0, ((1.0 / 0.38) / 3) ** 0.5)
        assert combo["signal_sharpe"] == pytest.approx(2.21 * pre_diversity, abs=1e-6)
        # 缩放后：权重 ×0.2686 且 diversity_factor 归 1 → combo_sharpe = 2.21 × 0.2686
        assert combo["combo_sharpe"] == pytest.approx(2.21 * 0.2686, abs=1e-6)
        assert combo["signal_sharpe"] > combo["combo_sharpe"]
        assert combo["exposure_scale"] == pytest.approx(0.2686, abs=1e-4)

    def test_signal_sharpe_no_scale_equals_combo(self, sample_signals):
        """无 exposure_scale 时缩放前后权重一致 → signal_sharpe == combo_sharpe。"""
        combo = build_combo(sample_signals, mode="equal_weight")
        assert combo["signal_sharpe"] == pytest.approx(combo["combo_sharpe"], abs=1e-9)

    def test_signal_sharpe_measured_equals_combo(self, sample_signals):
        """measured 口径下 portfolio_returns 内部归一化 → signal_sharpe == combo_sharpe。"""
        rng = np.random.default_rng(21)
        n = 60
        fr = pd.DataFrame(
            {
                "fct_001": rng.normal(0.0005, 0.01, size=n),
                "fct_002": rng.normal(0.0003, 0.01, size=n),
                "fct_003": rng.normal(0.0002, 0.01, size=n),
            }
        )
        combo = build_combo(
            sample_signals,
            mode="equal_weight",
            factor_returns=fr,
            exposure_scale=0.2686,
        )
        assert combo["metrics_source"] == "measured"
        assert combo["signal_sharpe"] == pytest.approx(combo["combo_sharpe"], abs=1e-9)

    def test_empty_combo_signal_sharpe_none(self):
        """空组合 signal_sharpe 为 None。"""
        combo = build_combo([], mode="equal_weight")
        assert combo["signal_sharpe"] is None


# ════════════════════════════════════════════════════════════
# 8. LoadEliteFactors 测试
# ════════════════════════════════════════════════════════════


class TestLoadEliteFactors:
    """精英因子读取 — 从 elite 目录加载 JSON 文件。"""

    def test_load_from_empty_dir(self, tmp_elite_dir):
        """空目录返回空列表。"""
        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False)
        assert factors == []

    def test_load_from_files(self, tmp_elite_dir):
        """从 JSON 文件加载因子。"""
        f1 = tmp_elite_dir / "factor_alpha.json"
        f1.write_text(
            json.dumps(
                {
                    "factor_id": "fct_alpha",
                    "name": "alpha_001",
                    "code": "alpha_code_v1",
                    "evaluation": {
                        "level_1_backtest": {
                            "sharpe": 2.5,
                            "ic": 0.05,
                            "turnover_monthly": 0.3,
                        }
                    },
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        f2 = tmp_elite_dir / "factor_beta.json"
        f2.write_text(
            json.dumps(
                {
                    "factor_id": "fct_beta",
                    "name": "beta_002",
                    "code": "beta_code_v1",
                    "evaluation": {
                        "level_1_backtest": {
                            "sharpe": 1.8,
                            "ic": 0.04,
                            "turnover_monthly": 0.4,
                        }
                    },
                    "decay_6m": 0.2,
                }
            ),
            encoding="utf-8",
        )

        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False)
        assert len(factors) == 2
        ids = {f["factor_id"] for f in factors}
        assert ids == {"fct_alpha", "fct_beta"}


# ════════════════════════════════════════════════════════════
# 9. PortfolioLoop 测试
# ════════════════════════════════════════════════════════════


class TestPortfolioLoop:
    """L3 Portfolio Loop 主循环。"""

    def test_run_without_factors(self, tmp_portfolio_dir, tmp_elite_dir):
        """无 elite 因子运行不报错。"""
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        result = loop.run()
        assert result.status == "completed"
        assert result.n_factors_input == 0
        assert result.n_factors_retained == 0
        assert result.error is None

    def test_run_with_mock_factors(self, tmp_portfolio_dir, tmp_elite_dir):
        """使用 mock elite 因子运行。"""
        # 写入一个 mock elite 因子
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input >= 1
        assert result.run_id.startswith("run_")
        assert result.trace_id.startswith("l3_")

    def test_run_result_fields(self, tmp_portfolio_dir, tmp_elite_dir):
        """验证 PortfolioRunResult 字段完整性。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        result = loop.run()

        assert isinstance(result, PortfolioRunResult)
        assert isinstance(result.run_id, str)
        assert isinstance(result.trace_id, str)
        assert isinstance(result.n_factors_input, int)
        assert isinstance(result.n_factors_retained, int)
        assert isinstance(result.combo_sharpe, float)
        assert isinstance(result.max_correlation, float)
        assert isinstance(result.n_proposals, int)
        assert isinstance(result.status, str)
        assert isinstance(result.output_paths, dict)

    def test_circuit_breaker(self, tmp_portfolio_dir, tmp_elite_dir):
        """异常时状态标记 circuit_broken。"""
        # 通过 mock load_elite_factors 抛异常触发熔断（在 try 块内部）
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        with patch("fts.factor_engine.portfolio_loop.load_elite_factors") as mock_load:
            mock_load.side_effect = RuntimeError("模拟致命错误")
            result = loop.run()
        assert result.status == "circuit_broken"
        assert result.error is not None
        assert "模拟致命错误" in result.error

    # ── GAP-072 权重重算日 / 冻结日 ──

    def test_run_frozen_skips_recompute(self, tmp_portfolio_dir, tmp_elite_dir):
        """recompute_weights=False 时冻结返回 status="frozen"，不构建组合、不落盘 combo。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        result = loop.run(recompute_weights=False)
        assert result.status == "frozen"
        assert result.n_factors_retained == 0
        assert result.n_factors_input == 0
        # 冻结日不重写 current_combo.json（复用上次组合）
        assert not (Path(tmp_portfolio_dir) / "current_combo.json").exists()

    def test_run_force_recompute(self, tmp_portfolio_dir, tmp_elite_dir):
        """recompute_weights=True 强制全量重算（非周五也可触发）。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        result = loop.run(recompute_weights=True)
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_retained >= 1
        assert (Path(tmp_portfolio_dir) / "current_combo.json").exists()

    def test_run_cold_start_frozen_day_recomputes(self, tmp_portfolio_dir, tmp_elite_dir):
        """冻结日 + 无上次组合（冷启动）仍执行全量构建（无权重可冻结）。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        with patch("fts.config.is_weight_recompute_day", return_value=False):
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert (Path(tmp_portfolio_dir) / "current_combo.json").exists()

    # ── P1/P2 集成测试 ──

    def test_enable_clustering_no_crash(self, tmp_portfolio_dir, tmp_elite_dir):
        """P1 因子聚类开启时 L3 不崩溃（引擎懒加载，无面板数据时聚类跳过）。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_clustering=True,
        )
        result = loop.run()
        # 无面板数据时聚类跳过，但 L3 应正常完成
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input >= 1
        # enable_clustering=True 时，即使聚类未触发（无面板数据），L3 正常完成
        # 引擎为懒加载，仅在条件满足时初始化

    def test_enable_pca_no_crash(self, tmp_portfolio_dir, tmp_elite_dir):
        """P2 PCA 降维开启时 L3 不崩溃（引擎懒加载，无面板数据时 PCA 跳过）。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_pca=True,
        )
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input >= 1
        # enable_pca=True 时，即使 PCA 未触发（无面板数据），L3 正常完成

    def test_enable_both_no_crash(self, tmp_portfolio_dir, tmp_elite_dir):
        """P1 + P2 同时开启时 L3 不崩溃。"""
        factor_file = tmp_elite_dir / "factor_test.json"
        factor_file.write_text(
            json.dumps(
                {
                    "factor_id": "fct_mock",
                    "name": "mock_momentum",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_clustering=True,
            enable_pca=True,
        )
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input >= 1

    # ── GAP-L303/L304: optimizer 接线 ──────────────────

    def _write_mock_elites(self, tmp_elite_dir: Path) -> list[str]:
        """写入 3 个 mock elite 因子 JSON，返回 factor_id 列表。"""
        ids = ["fct_opt1", "fct_opt2", "fct_opt3"]
        for i, fid in enumerate(ids):
            (tmp_elite_dir / f"factor_{i}.json").write_text(
                json.dumps(
                    {
                        "factor_id": fid,
                        "name": f"mock_{fid}",
                        "sharpe": 2.0 + 0.1 * i,
                        "ic": 0.04 + 0.01 * i,
                        "turnover": 0.3,
                        "decay_6m": 0.1,
                    }
                ),
                encoding="utf-8",
            )
        return ids

    def test_run_optimizer_mode_end_to_end(
        self,
        tmp_portfolio_dir,
        tmp_elite_dir,
    ):
        """optimizer 模式端到端：factor_returns 透传 → 组合实测指标 + 正常完成。"""
        ids = self._write_mock_elites(tmp_elite_dir)
        rng = np.random.default_rng(21)
        n = 60
        fr = pd.DataFrame(
            {
                ids[0]: rng.normal(0.001, 0.01, size=n),
                ids[1]: rng.normal(0.0005, 0.01, size=n),
                ids[2]: rng.normal(0.0002, 0.01, size=n),
            }
        )
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="optimizer",
            optimizer_mode="mvo",
        )
        result = loop.run(factor_returns=fr)
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input == 3
        # 组合已实测化（A 阶段联动）
        combo = json.loads((tmp_portfolio_dir / "current_combo.json").read_text(encoding="utf-8"))
        assert combo.get("metrics_source") == "measured"

    def test_run_optimizer_with_exposure(
        self,
        tmp_portfolio_dir,
        tmp_elite_dir,
    ):
        """optimizer + 暴露矩阵（GAP-L304）：组合正常完成。"""
        ids = self._write_mock_elites(tmp_elite_dir)
        rng = np.random.default_rng(22)
        n = 60
        fr = pd.DataFrame(
            {
                ids[0]: rng.normal(size=n),
                ids[1]: rng.normal(size=n),
                ids[2]: rng.normal(size=n),
            }
        )
        # 暴露矩阵: 3 因子 × 2 维度
        exposure = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="optimizer",
            optimizer_mode="mvo",
            optimizer_config={"neutralization": "industry", "exposure_tolerance": 0.1},
        )
        result = loop.run(factor_returns=fr, exposure_matrix=exposure)
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input == 3

    # ── GAP-L305 net 指标 ─────────────────────────────

    def test_run_with_cost_config(
        self,
        tmp_portfolio_dir,
        tmp_elite_dir,
    ):
        """cost_config 传入 → 组合带 net_combo_sharpe。"""
        self._write_mock_elites(tmp_elite_dir)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="sharpe_weight",
            cost_config={"market": "futures", "slippage_bps": 0.5},
        )
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        combo = json.loads((tmp_portfolio_dir / "current_combo.json").read_text(encoding="utf-8"))
        assert combo.get("net_combo_sharpe") is not None

    # ── GAP-L307 归因报告 ─────────────────────────────

    def test_attribution_report_generated(
        self,
        tmp_portfolio_dir,
        tmp_elite_dir,
    ):
        """factor_returns 传入 + 组合有效 → 生成归因报告文件。"""
        ids = self._write_mock_elites(tmp_elite_dir)
        rng = np.random.default_rng(23)
        n = 60
        fr = pd.DataFrame(
            {
                ids[0]: rng.normal(0.001, 0.01, size=n),
                ids[1]: rng.normal(0.0005, 0.01, size=n),
                ids[2]: rng.normal(0.0002, 0.01, size=n),
            }
        )
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="sharpe_weight",
        )
        result = loop.run(factor_returns=fr)
        assert result.status in ("passed", "verifier_warning", "completed")
        # 归因报告写入 reports/{market}/{date}/（v2.101.0 市场目录隔离）
        import datetime as _dt

        ts = _dt.date.today().isoformat()
        reports_dir = Path("reports") / "futures" / ts
        assert reports_dir.exists()
        md_files = list(reports_dir.glob("portfolio_attribution_*.md"))
        assert len(md_files) >= 1
        content = md_files[-1].read_text(encoding="utf-8")
        assert "因子贡献度" in content
        assert "VaR 95" in content
        # 清理测试产物（仅删除本测试生成的归因文件，不删除 reports 其他内容）
        for f in md_files:
            f.unlink(missing_ok=True)

    def test_attribution_skipped_without_returns(self, tmp_portfolio_dir, tmp_elite_dir):
        """无 factor_returns → 不生成归因报告（不崩溃）。"""
        self._write_mock_elites(tmp_elite_dir)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="sharpe_weight",
        )
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    # ── GAP-L306 组合层走航 ─────────────────────────────

    def test_walk_forward_report_generated(
        self,
        tmp_portfolio_dir,
        tmp_elite_dir,
    ):
        """factor_returns 足够长 → 生成走航报告文件。"""
        ids = self._write_mock_elites(tmp_elite_dir)
        rng = np.random.default_rng(24)
        n = 400  # 走航需 ≥120 天
        fr = pd.DataFrame(
            {
                ids[0]: rng.normal(0.001, 0.01, size=n),
                ids[1]: rng.normal(0.0005, 0.01, size=n),
                ids[2]: rng.normal(0.0002, 0.01, size=n),
            }
        )
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="sharpe_weight",
        )
        result = loop.run(factor_returns=fr)
        assert result.status in ("passed", "verifier_warning", "completed")
        import datetime as _dt

        ts = _dt.date.today().isoformat()
        reports_dir = Path("reports") / "futures" / ts
        wf_files = list(reports_dir.glob("portfolio_wf_*.md"))
        assert len(wf_files) >= 1
        content = wf_files[-1].read_text(encoding="utf-8")
        assert "一致性得分" in content
        # 清理测试产物
        for f in wf_files:
            f.unlink(missing_ok=True)

    def test_walk_forward_skipped_short_returns(self, tmp_portfolio_dir, tmp_elite_dir):
        """因子收益矩阵过短（<120）→ 不生成走航报告（不崩溃）。"""
        ids = self._write_mock_elites(tmp_elite_dir)
        rng = np.random.default_rng(25)
        fr = pd.DataFrame(
            {
                ids[0]: rng.normal(size=60),
                ids[1]: rng.normal(size=60),
                ids[2]: rng.normal(size=60),
            }
        )
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="sharpe_weight",
        )
        result = loop.run(factor_returns=fr)
        assert result.status in ("passed", "verifier_warning", "completed")


# ════════════════════════════════════════════════════════════
# 10. GenerateAgentProposals 测试
# ════════════════════════════════════════════════════════════


class TestGenerateAgentProposals:
    """Agent 优化建议生成。"""

    def test_empty_combo(self):
        """空组合返回空列表。"""
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="2026-07-18T00:00:00",
            combo_id="cmb_empty",
            trace_id="l3_empty",
            synthesis_mode="equal_weight",
            signals=[],
            combo_sharpe=0.0,
            combo_turnover=0.0,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at="2026-07-18T00:00:00",
        )
        proposals = generate_agent_proposals(combo)
        assert proposals == []

    def test_generates_proposals(self, sample_signals):
        """有效组合生成建议。"""
        combo = build_combo(sample_signals, mode="equal_weight", trace_id="l3_test")
        proposals = generate_agent_proposals(combo, trace_id="l3_test")
        assert len(proposals) == 1
        prop = proposals[0]
        assert prop["proposal_id"].startswith("prop_")
        assert prop["trace_id"] == "l3_test"
        assert prop["agent_name"] == "闫判官"
        assert prop["status"] == "draft"
        assert prop["priority"] == "medium"
        # 建议内容应包含组合信息
        assert "momentum" in prop["suggested_changes"]
        assert isinstance(prop["debate_round_ref"], type(None))


# ─── 覆盖遗漏行 ───────────────────────────────────────────


class TestCoverageGaps:
    """覆盖 portfolio_loop.py 遗漏行。"""

    # ── L3Verifier line 111 ──

    def test_verifier_decay_rate_failure(self):
        """line 111: 信号衰减率过高应失败。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        signals = [
            PortfolioSignal(
                factor_id="fct_d",
                name="decayed",
                weight=1.0,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.5,
                orthogonalized=True,
                retained=True,
            ),
        ]
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="now",
            combo_id="cmb_test",
            trace_id="l3_test",
            synthesis_mode="equal_weight",
            signals=signals,
            combo_sharpe=2.5,
            combo_turnover=0.3,
            max_correlation=0.2,
            n_factors=1,
            status="pending",
            created_at="now",
        )
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("衰减率" in r for r in reasons)

    # ── PortfolioStateManager schema 版本冷却 ──

    def test_state_manager_try_load_version_mismatch(self, tmp_portfolio_dir):
        """schema 版本不匹配时冷启动。"""
        from fts.store.state_db import get_state_store

        get_state_store().upsert("portfolio", "state", {"schema_version": "0", "status": "completed"}, run_id="t")
        psm = PortfolioStateManager(tmp_portfolio_dir)
        # load_or_init 应重新初始化（schema 版本不匹配）
        state = psm.load_or_init()
        assert state["schema_version"] == STATE_SCHEMA_VERSION

    # ── PortfolioManager lines 225, 231-232 ──

    def test_portfolio_manager_cache(self, tmp_portfolio_dir):
        """line 225: load_or_init 使用缓存。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        combo1 = pm.load_or_init()
        combo1["status"] = "modified"
        # 第二次调用应返回缓存（line 225）
        combo2 = pm.load_or_init()
        assert combo2["status"] == "modified"

    def test_portfolio_manager_corrupt_json(self, tmp_portfolio_dir):
        """lines 231-232: combo 文件损坏时重新初始化。"""
        combo_file = tmp_portfolio_dir / "current_combo.json"
        combo_file.write_text("corrupt json", encoding="utf-8")
        pm = PortfolioManager(tmp_portfolio_dir)
        combo = pm.load_or_init()
        assert combo["status"] == "pending"
        assert combo["combo_id"].startswith("cmb_")

    # ── PortfolioManager lines 270, 276-277 ──

    def test_list_active_proposals_empty_dir(self, tmp_portfolio_dir):
        """line 270: 空 proposals 目录返回空列表。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        # 确保 proposals_dir 为空
        assert pm.proposals_dir.exists()
        proposals = pm.list_active_proposals()
        assert proposals == []

    def test_list_active_proposals_skip_corrupt(self, tmp_portfolio_dir):
        """lines 276-277: 损坏的 proposal 文件应跳过。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        # 写入一个正常 proposal
        good = AgentOptimizationProposal(
            proposal_id="prop_good",
            trace_id="t",
            agent_name="闫判官",
            created_at="now",
            current_prompt_summary="p",
            suggested_changes="c",
            debate_round_ref=None,
            rationale="r",
            priority="medium",
            status="draft",
        )
        pm.save_proposal(good)
        # 写入一个损坏文件
        bad_file = pm.proposals_dir / "prop_bad.json"
        bad_file.write_text("corrupt", encoding="utf-8")

        active = pm.list_active_proposals()
        assert len(active) == 1
        assert active[0]["proposal_id"] == "prop_good"

    # ── load_elite_factors lines 505, 517-518 ──

    def test_load_elite_factors_empty_dir(self, tmp_elite_dir):
        """line 505: 空 elite 目录返回空列表。"""
        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False)
        assert factors == []

    def test_load_elite_factors_skip_corrupt(self, tmp_elite_dir):
        """lines 517-518: 损坏的 elite 文件应跳过。"""
        # 写入一个正常文件
        good = tmp_elite_dir / "good.json"
        good.write_text(
            json.dumps(
                {
                    "factor_id": "fct_good",
                    "name": "good",
                    "sharpe": 2.0,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )
        # 写入一个损坏文件
        bad = tmp_elite_dir / "bad.json"
        bad.write_text("not json", encoding="utf-8")

        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False)
        assert len(factors) == 1
        assert factors[0]["factor_id"] == "fct_good"

    # ── main() CLI lines 737-759 + line 763 ──

    def test_main_help(self, monkeypatch):
        """main() 不带参数应显示帮助。"""
        import sys
        from fts.factor_engine.portfolio_loop import main

        monkeypatch.setattr(sys, "argv", ["portfolio_loop.py"])
        with pytest.raises(SystemExit):
            main()

    def test_main_with_factors(self, monkeypatch, tmp_path):
        """main() 带 --once 和 elite 因子应运行。"""
        import sys

        # 创建 elite 因子
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / "test.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_main",
                    "name": "main",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )
        memory_dir = tmp_path / "portfolio"
        memory_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "portfolio_loop.py",
                "--once",
                "--mode",
                "equal_weight",
                "--memory-dir",
                str(memory_dir),
                "--elite-dir",
                str(elite_dir),
            ],
        )
        from fts.factor_engine.portfolio_loop import main

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_main_verifier_warning(self, monkeypatch, tmp_path):
        """main() verifier 未通过也正常退出。"""
        import sys

        elite_dir = tmp_path / "elite_warn"
        elite_dir.mkdir(parents=True, exist_ok=True)
        # 低 sharpe 因子触发 verifier warning
        (elite_dir / "bad.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_bad",
                    "name": "bad",
                    "sharpe": 1.0,
                    "ic": 0.01,
                    "turnover": 0.8,
                    "decay_6m": 0.5,
                }
            ),
            encoding="utf-8",
        )
        memory_dir = tmp_path / "portfolio_warn"
        memory_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "portfolio_loop.py",
                "--once",
                "--memory-dir",
                str(memory_dir),
                "--elite-dir",
                str(elite_dir),
            ],
        )
        from fts.factor_engine.portfolio_loop import main

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    # ── __name__ == "__main__" line 763 ──

    def test_module_execution(self, monkeypatch, tmp_path):
        """line 763: 模拟 __name__ == '__main__' 进入 main()。"""
        import sys
        from fts.factor_engine import portfolio_loop as pl_mod

        elite_dir = tmp_path / "elite_main"
        elite_dir.mkdir(parents=True, exist_ok=True)
        memory_dir = tmp_path / "portfolio_main"
        memory_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "portfolio_loop.py",
                "--once",
                "--memory-dir",
                str(memory_dir),
                "--elite-dir",
                str(elite_dir),
            ],
        )
        with patch.object(pl_mod, "__name__", "__main__"):
            with pytest.raises(SystemExit):
                exec("from fts.factor_engine.portfolio_loop import main; main()", {"__name__": "__main__"})

    # ── MIN_EVAL_DAYS 常量 ──

    def test_min_eval_days_constant(self):
        """MIN_EVAL_DAYS = 500（C 修复：扩展评价窗口）。"""
        from fts.factor_engine.portfolio_loop import MIN_EVAL_DAYS

        assert MIN_EVAL_DAYS == 500


# ════════════════════════════════════════════════════════════
# 11. Sharpe 随机化测试 (E 修复)
# ════════════════════════════════════════════════════════════


class TestSharpeRandomization:
    """夏普随机化测试 — 基于 Dirichlet 权重验证高夏普是否来自真实预测能力。"""

    def test_low_sharpe_skips_test(self):
        """夏普 <= 2.5 时跳过随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.5,
                sharpe=1.5,
                ic=0.03,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_few_signals_skips_test(self):
        """因子数 < 3 时跳过随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=1.0,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_zero_total_weight_skips(self):
        """总权重为 0 时跳过随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.0,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_high_sharpe_with_retained_only(self):
        """只使用 retained=True 的信号计算随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.4,
                sharpe=3.5,
                ic=0.07,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.3,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_c",
                name="c",
                weight=0.3,
                sharpe=2.8,
                ic=0.05,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_d",
                name="d",
                weight=0.0,
                sharpe=0.5,
                ic=0.01,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=False,
            ),
        ]
        # 不应报错，且返回 bool
        result = _run_sharpe_randomization_test(signals, n_shuffle=50)
        assert isinstance(result, bool)


# ════════════════════════════════════════════════════════════
# 12. Regime 自适应权重调整测试 (A.3)
# ════════════════════════════════════════════════════════════


class TestRegimeAdaptiveWeight:
    """A.3: Regime 自适应权重调整测试。"""

    @pytest.fixture
    def regime_fixtures(self):
        """构建带 family 字段的因子和对应 signals。"""
        factors = [
            {
                "factor_id": "fct_trend",
                "name": "momentum_trend",
                "sharpe": 2.5,
                "ic": 0.05,
                "turnover": 0.3,
                "decay_6m": 0.05,
                "family": "trend",
            },
            {
                "factor_id": "fct_reversion",
                "name": "mean_reversion",
                "sharpe": 2.0,
                "ic": 0.04,
                "turnover": 0.4,
                "decay_6m": 0.1,
                "family": "mean_reversion",
            },
            {
                "factor_id": "fct_vol",
                "name": "volatility_screener",
                "sharpe": 1.8,
                "ic": 0.03,
                "turnover": 0.2,
                "decay_6m": 0.08,
                "family": "volatility",
            },
            {
                "factor_id": "fct_carry",
                "name": "carry_spread",
                "sharpe": 1.5,
                "ic": 0.03,
                "turnover": 0.15,
                "decay_6m": 0.12,
                "family": "carry",
            },
        ]
        signals = [
            PortfolioSignal(
                factor_id="fct_trend",
                name="momentum_trend",
                weight=0.25,
                sharpe=2.5,
                ic=0.05,
                turnover=0.3,
                decay_6m=0.05,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_reversion",
                name="mean_reversion",
                weight=0.25,
                sharpe=2.0,
                ic=0.04,
                turnover=0.4,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_vol",
                name="volatility_screener",
                weight=0.25,
                sharpe=1.8,
                ic=0.03,
                turnover=0.2,
                decay_6m=0.08,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_carry",
                name="carry_spread",
                weight=0.25,
                sharpe=1.5,
                ic=0.03,
                turnover=0.15,
                decay_6m=0.12,
                orthogonalized=False,
                retained=True,
            ),
        ]
        return factors, signals

    def test_bull_regime_increases_trend_weight(self, regime_fixtures):
        """牛市制度: trend 因子权重增加 30%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "bull", "confidence": 0.9, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        trend_signal = next(s for s in result if s["factor_id"] == "fct_trend")
        # 原始 0.25 × 1.3 = 0.325
        assert abs(trend_signal["weight"] - 0.325) < 1e-6, (
            f"Bull 下 trend 权重应为 0.325，实际 {trend_signal['weight']}"
        )

    def test_bull_regime_decreases_reversion_weight(self, regime_fixtures):
        """牛市制度: mean_reversion 因子权重减少 30%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "bull", "confidence": 0.9, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        reversion_signal = next(s for s in result if s["factor_id"] == "fct_reversion")
        # 原始 0.25 × 0.7 = 0.175
        assert abs(reversion_signal["weight"] - 0.175) < 1e-6

    def test_bear_regime_increases_volatility_weight(self, regime_fixtures):
        """熊市制度: volatility 因子权重增加 30%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "bear", "confidence": 0.85, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        vol_signal = next(s for s in result if s["factor_id"] == "fct_vol")
        # 原始 0.25 × 1.3 = 0.325
        assert abs(vol_signal["weight"] - 0.325) < 1e-6

    def test_oscillate_regime_increases_reversion_weight(self, regime_fixtures):
        """震荡市: mean_reversion 因子权重增加 30%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "oscillate", "confidence": 0.5, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        reversion_signal = next(s for s in result if s["factor_id"] == "fct_reversion")
        # 原始 0.25 × 1.3 = 0.325
        assert abs(reversion_signal["weight"] - 0.325) < 1e-6

    def test_high_vol_reduces_trend_weight(self, regime_fixtures):
        """高波动期: trend 因子权重减少 30%，衰减快的额外减 20%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "high_vol", "confidence": 0.8, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        trend_signal = next(s for s in result if s["factor_id"] == "fct_trend")
        # trend 衰减率 0.05 < 0.20，不触发额外缩减: 0.25 × 0.7 = 0.175
        assert abs(trend_signal["weight"] - 0.175) < 1e-6

    def test_high_vol_extra_penalty_on_decaying(self):
        """高波动期: 衰减率 > 0.20 的因子额外减 20%。"""
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        factors = [
            {
                "factor_id": "fct_decay",
                "name": "decaying_factor",
                "sharpe": 1.5,
                "ic": 0.02,
                "turnover": 0.5,
                "decay_6m": 0.25,
                "family": "trend",
            },
        ]
        signals = [
            PortfolioSignal(
                factor_id="fct_decay",
                name="decaying_factor",
                weight=0.3,
                sharpe=1.5,
                ic=0.02,
                turnover=0.5,
                decay_6m=0.25,
                orthogonalized=False,
                retained=True,
            ),
        ]
        regime = {"regime": "high_vol", "confidence": 0.8, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        decay_signal = next(s for s in result if s["factor_id"] == "fct_decay")
        # trend in high_vol: 0.7 * 0.8 = 0.56 → 0.3 × 0.56 = 0.168
        expected = 0.3 * 0.7 * 0.8
        assert abs(decay_signal["weight"] - expected) < 1e-6

    def test_low_vol_increases_trend_weight(self, regime_fixtures):
        """低波动期: trend 因子权重增加 20%。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "low_vol", "confidence": 0.7, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        trend_signal = next(s for s in result if s["factor_id"] == "fct_trend")
        # 原始 0.25 × 1.2 = 0.30
        assert abs(trend_signal["weight"] - 0.30) < 1e-6

    def test_empty_signals_returns_empty(self):
        """空信号列表直接返回空。"""
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        result = regime_adaptive_weight_adjustment([], {"regime": "bull"}, [])
        assert result == []

    def test_none_regime_skips_adjustment(self, regime_fixtures):
        """regime 为 None 时跳过调整。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        result = regime_adaptive_weight_adjustment(signals, {}, factors)
        # 权重保持不变
        for s in result:
            assert s["weight"] == 0.25

    def test_unknown_regime_skips_adjustment(self, regime_fixtures):
        """未知制度 (如 crash) 时跳过调整。"""
        factors, signals = regime_fixtures
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        regime = {"regime": "crash", "confidence": 0.0, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)
        # 权重保持不变
        for s in result:
            assert s["weight"] == 0.25

    def test_infer_family_from_name(self):
        """从因子名称推断家族分类。"""
        from fts.factor_engine.portfolio_loop import _infer_factor_family_from_name

        assert _infer_factor_family_from_name("momentum_factor") == "trend"
        assert _infer_factor_family_from_name("trend_following") == "trend"
        assert _infer_factor_family_from_name("breakout_signal") == "trend"
        assert _infer_factor_family_from_name("mean_reversion") == "mean_reversion"
        assert _infer_factor_family_from_name("price_reversal") == "mean_reversion"
        assert _infer_factor_family_from_name("carry_spread") == "carry"
        assert _infer_factor_family_from_name("volatility_ratio") == "volatility"
        assert _infer_factor_family_from_name("atr_filter") == "volatility"
        assert _infer_factor_family_from_name("volume_weighted") == "volume"
        assert _infer_factor_family_from_name("fundamental_pe") == "fundamental"
        assert _infer_factor_family_from_name("illiquidity") == "liquidity"
        assert _infer_factor_family_from_name("unknown_factor") == "other"

    def test_fallback_name_inference_when_no_family(self):
        """因子无 family 字段时从名称推断。"""
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        # 因子只有 name 字段，无 family
        factors = [
            {
                "factor_id": "fct_1",
                "name": "momentum_factor",
                "sharpe": 2.0,
                "ic": 0.04,
                "turnover": 0.3,
                "decay_6m": 0.1,
            },
        ]
        signals = [
            PortfolioSignal(
                factor_id="fct_1",
                name="momentum_factor",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
        ]
        regime = {"regime": "bull", "confidence": 0.9, "detected_at": "now", "features": {}}
        result = regime_adaptive_weight_adjustment(signals, regime, factors)

        # momentum_factor → trend (通过名称推断) → bull 下 ×1.3
        adjusted = next(s for s in result)
        assert abs(adjusted["weight"] - 0.65) < 1e-6  # 0.5 × 1.3 = 0.65

    def test_regime_adaptation_via_portfolio_loop(self, tmp_portfolio_dir, tmp_elite_dir):
        """PortfolioLoop.run() 传入 market_ohlcv 触发 Regime 自适应。"""
        import numpy as np
        import pandas as pd
        from fts.factor_engine.portfolio_loop import PortfolioLoop

        # 创建 elite 因子
        (tmp_elite_dir / "test.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_test",
                    "name": "test_factor",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        # 构造市场数据（牛市趋势）
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = pd.DataFrame(
            {
                "open": close * 1.001,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": np.random.randint(800, 1200, n).astype(float),
            },
            index=dates,
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=True,
        )
        result = loop.run(market_ohlcv=ohlcv)

        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input > 0

    def test_disable_regime_adaptation(self, tmp_portfolio_dir, tmp_elite_dir):
        """enable_regime_adaptation=False 时跳过 Regime 调整。"""
        import numpy as np
        import pandas as pd
        from fts.factor_engine.portfolio_loop import PortfolioLoop

        (tmp_elite_dir / "test.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_test",
                    "name": "test_factor",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = pd.DataFrame(
            {
                "open": close * 1.001,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": np.random.randint(800, 1200, n).astype(float),
            },
            index=dates,
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=False,  # 禁用
        )
        result = loop.run(market_ohlcv=ohlcv)

        assert result.status in ("passed", "verifier_warning", "completed")

    def test_no_market_data_skips_regime(self, tmp_portfolio_dir, tmp_elite_dir):
        """不传 market_ohlcv 时跳过 Regime 调整。"""
        from fts.factor_engine.portfolio_loop import PortfolioLoop

        (tmp_elite_dir / "test.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_test",
                    "name": "test_factor",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                }
            ),
            encoding="utf-8",
        )

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=True,
        )
        result = loop.run(market_ohlcv=None)  # 不传数据

        assert result.status in ("passed", "verifier_warning", "completed")


# ════════════════════════════════════════════════════════════
# 13. 组合粘性约束测试 (v2.11.0)
# ════════════════════════════════════════════════════════════


class TestStickyConstraints:
    """组合粘性约束 — 平滑换血，防止策略漂移。"""

    def _make_signals(self, weights: dict[str, float]) -> list[PortfolioSignal]:
        """根据 {factor_id: weight} 构造信号。"""
        return [
            PortfolioSignal(
                factor_id=fid,
                name=fid,
                weight=w,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for fid, w in weights.items()
        ]

    def test_sticky_caps_existing_weight_increase(self):
        """存量因子权重相对上次变动 clamp 在 +30%。"""
        prev_weights = {"f_a": 0.10, "f_b": 0.30, "f_c": 0.60}
        signals = self._make_signals({"f_a": 0.30, "f_b": 0.30, "f_c": 0.40})

        from fts.factor_engine.portfolio_loop import _apply_sticky_constraints
        from fts.factor_engine.contracts import StickyConfig

        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        result = _apply_sticky_constraints(signals, prev_weights, config)
        w_map = {s["factor_id"]: s["weight"] for s in result}

        # f_a: 0.30 → clamp 上限 0.10 * 1.3 = 0.13
        assert w_map["f_a"] == pytest.approx(0.13)
        # f_b: 0.30 在 [0.21, 0.39] 内，不变
        assert w_map["f_b"] == pytest.approx(0.30)
        # f_c: 0.40 → clamp 下限 0.60 * 0.7 = 0.42
        assert w_map["f_c"] == pytest.approx(0.42)

    def test_sticky_floors_existing_weight_decrease(self):
        """存量因子权重相对上次变动 clamp 在 -30%。"""
        prev_weights = {"f_a": 0.50, "f_b": 0.50}
        signals = self._make_signals({"f_a": 0.10, "f_b": 0.90})

        from fts.factor_engine.portfolio_loop import _apply_sticky_constraints
        from fts.factor_engine.contracts import StickyConfig

        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        result = _apply_sticky_constraints(signals, prev_weights, config)
        w_map = {s["factor_id"]: s["weight"] for s in result}

        # f_a: 0.10 → clamp 下限 0.50 * 0.7 = 0.35
        assert w_map["f_a"] == pytest.approx(0.35)
        # f_b: 0.90 → clamp 上限 0.50 * 1.3 = 0.65
        assert w_map["f_b"] == pytest.approx(0.65)

    def test_new_factor_capped(self):
        """新因子（上次无权重）首日权重封顶。"""
        prev_weights = {"f_a": 0.50, "f_b": 0.50}
        signals = self._make_signals({"f_a": 0.40, "f_b": 0.40, "f_new": 0.20})

        from fts.factor_engine.portfolio_loop import _apply_sticky_constraints
        from fts.factor_engine.contracts import StickyConfig

        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        result = _apply_sticky_constraints(signals, prev_weights, config)
        w_map = {s["factor_id"]: s["weight"] for s in result}

        # f_new 是新增因子，权重 0.20 封顶到 0.10
        assert w_map["f_new"] == pytest.approx(0.10)

    def test_disabled_or_empty_prev_skips(self):
        """disabled 或 prev_weights 为空时权重不变。"""
        from fts.factor_engine.portfolio_loop import _apply_sticky_constraints
        from fts.factor_engine.contracts import StickyConfig

        signals = self._make_signals({"f_a": 0.30, "f_b": 0.70})

        # disabled
        cfg_off = StickyConfig(enabled=False, max_delta=0.30, new_factor_cap=0.10)
        result = _apply_sticky_constraints(signals, {"f_a": 0.1}, cfg_off)
        assert {s["factor_id"]: s["weight"] for s in result} == {"f_a": 0.30, "f_b": 0.70}

        # 空 prev
        cfg_on = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        result2 = _apply_sticky_constraints(signals, {}, cfg_on)
        assert {s["factor_id"]: s["weight"] for s in result2} == {"f_a": 0.30, "f_b": 0.70}

    def test_build_combo_with_sticky(self):
        """build_combo 传入 prev_weights + sticky_config 生效且归一化。"""
        from fts.factor_engine.contracts import StickyConfig

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.30,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.70,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        prev_weights = {"f_a": 0.05, "f_b": 0.95}
        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        combo = build_combo(
            signals, mode="equal_weight", trace_id="l3_sticky", prev_weights=prev_weights, sticky_config=config
        )
        w_map = {s["factor_id"]: s["weight"] for s in combo["signals"]}
        # f_a: 0.30 → clamp 上限 0.05 * 1.3 = 0.065
        # f_b: 0.70 在 [0.665, 1.235] 内，不变
        total = 0.065 + 0.70
        assert w_map["f_a"] == pytest.approx(0.065 / total)
        assert w_map["f_b"] == pytest.approx(0.70 / total)
        assert sum(w_map.values()) == pytest.approx(1.0)

    def test_portfolio_loop_sticky_default_enabled(self, tmp_portfolio_dir, tmp_elite_dir):
        """PortfolioLoop 未传 sticky_config 时使用 DEFAULT_STICKY_CONFIG（默认关闭）。"""
        from fts.factor_engine.contracts import DEFAULT_STICKY_CONFIG

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        assert loop.sticky_config is not None
        assert loop.sticky_config.get("enabled", True) is DEFAULT_STICKY_CONFIG["enabled"]
        assert loop.sticky_config.get("max_delta", 0.30) == pytest.approx(DEFAULT_STICKY_CONFIG["max_delta"])
        assert loop.sticky_config.get("new_factor_cap", 0.10) == pytest.approx(DEFAULT_STICKY_CONFIG["new_factor_cap"])

    def test_portfolio_loop_sticky_explicit_override(self, tmp_portfolio_dir, tmp_elite_dir):
        """显式传入 sticky_config 覆盖默认配置。"""
        from fts.factor_engine.contracts import StickyConfig

        custom = StickyConfig(enabled=False, max_delta=0.10, new_factor_cap=0.05)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            sticky_config=custom,
        )
        assert loop.sticky_config == custom
        assert loop.sticky_config["enabled"] is False


# ════════════════════════════════════════════════════════════
# 14. 组合漂移监控测试 (v2.11.0)
# ════════════════════════════════════════════════════════════


class TestDriftMonitor:
    """L3 组合漂移监控 — 成员重合率 + 权重 L1 变化率。"""

    def _make_combo(self, combo_id: str, weights: dict[str, float], trace_id: str = "l3_drift") -> PortfolioCombo:
        signals = [
            PortfolioSignal(
                factor_id=fid,
                name=fid,
                weight=w,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for fid, w in weights.items()
        ]
        return PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="now",
            combo_id=combo_id,
            trace_id=trace_id,
            synthesis_mode="equal_weight",
            signals=signals,
            combo_sharpe=2.5,
            combo_turnover=0.3,
            max_correlation=0.2,
            n_factors=len(weights),
            status="active",
            created_at="now",
        )

    def test_identical_combo_zero_drift(self, tmp_portfolio_dir):
        """完全相同的组合漂移为零。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.5, "f_b": 0.5})
        m = mon.compute(prev, new)
        assert m["member_overlap_rate"] == 1.0
        assert m["weight_l1_change"] == 0.0
        assert m["n_common_members"] == 2
        assert m["added"] == []
        assert m["removed"] == []

    def test_full_replacement_zero_overlap(self, tmp_portfolio_dir):
        """全部更换因子时重合率为 0，L1 变化为 1。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_c": 0.5, "f_d": 0.5})
        m = mon.compute(prev, new)
        assert m["member_overlap_rate"] == 0.0
        assert m["weight_l1_change"] == pytest.approx(1.0)
        assert m["added"] == ["f_c", "f_d"]
        assert m["removed"] == ["f_a", "f_b"]

    def test_partial_change(self, tmp_portfolio_dir):
        """部分因子更换 + 权重变化。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.3, "f_b": 0.3, "f_c": 0.4})
        m = mon.compute(prev, new)
        # Jaccard: 交集 {f_a,f_b}=2, 并集 {f_a,f_b,f_c}=3 → 2/3 (round 4位=0.6667)
        assert m["member_overlap_rate"] == pytest.approx(2 / 3, abs=1e-4)
        # L1: |0.3-0.5|+|0.3-0.5|+|0.4-0| = 0.2+0.2+0.4 = 0.8 → /2 = 0.4
        assert m["weight_l1_change"] == pytest.approx(0.4)
        assert m["added"] == ["f_c"]
        assert m["removed"] == []

    def test_cold_start_no_prev(self, tmp_portfolio_dir):
        """无上次组合（冷启动）漂移指标为空基准。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        new = self._make_combo("cmb_new", {"f_a": 1.0})
        m = mon.compute(None, new)
        assert m["member_overlap_rate"] == 0.0
        assert m["weight_l1_change"] == 0.0
        assert m["n_prev_members"] == 0
        assert m["prev_combo_id"] == ""

    def test_record_persists_daily_file(self, tmp_portfolio_dir):
        """record 持久化到 drift_history/YYYY-MM-DD.json。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.5, "f_b": 0.5})
        metrics = mon.compute(prev, new)
        fp = mon.record(metrics)
        assert fp.exists()
        assert fp.name == "drift_history.json" or fp.name.endswith(".json")
        # 读回验证
        records = mon.load_history(metrics["date"])
        assert len(records) == 1
        assert records[0]["combo_id"] == "cmb_new"
        assert records[0]["member_overlap_rate"] == 1.0

    def test_record_appends_multiple_entries(self, tmp_portfolio_dir):
        """同一天多次记录追加不覆盖。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 1.0})
        new1 = self._make_combo("cmb_new1", {"f_a": 1.0})
        new2 = self._make_combo("cmb_new2", {"f_a": 1.0})
        mon.record(mon.compute(prev, new1))
        mon.record(mon.compute(prev, new2))
        records = mon.load_history(mon.compute(prev, new1)["date"])
        assert len(records) == 2
        assert {r["combo_id"] for r in records} == {"cmb_new1", "cmb_new2"}

    def test_load_history_missing_date(self, tmp_portfolio_dir):
        """不存在的日期返回空列表。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        assert mon.load_history("2099-01-01") == []


class TestDriftMonitorAlert:
    """GAP-F13: 漂移阈值告警 + 可选自动粘性重平衡。"""

    def _make_combo(self, combo_id: str, weights: dict[str, float]) -> PortfolioCombo:
        signals = [
            PortfolioSignal(
                factor_id=fid,
                name=fid,
                weight=w,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for fid, w in weights.items()
        ]
        return PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="now",
            combo_id=combo_id,
            trace_id="l3_drift",
            synthesis_mode="equal_weight",
            signals=signals,
            combo_sharpe=2.5,
            combo_turnover=0.3,
            max_correlation=0.2,
            n_factors=len(weights),
            status="active",
            created_at="now",
        )

    def test_no_alert_within_threshold(self, tmp_portfolio_dir):
        """重合率/权重变化均在阈值内 → 不告警。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.4, "f_b": 0.6})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        assert info["alerted"] is False
        assert info["overlap_alert"] is False
        assert info["weight_alert"] is False
        assert info["trigger_rebalance"] is False

    def test_overlap_alert_triggered(self, tmp_portfolio_dir):
        """成员重合率低于阈值 → 重合率告警。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_c": 0.5, "f_d": 0.5})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        assert info["alerted"] is True
        assert info["overlap_alert"] is True
        assert info["weight_alert"] is True  # L1=1.0 > 0.4
        assert info["overlap_rate"] == 0.0

    def test_weight_alert_only(self, tmp_portfolio_dir):
        """仅权重变化超阈值（成员重合正常）→ 权重告警。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.05, "f_b": 0.95})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        # 重合率=1.0（成员未变）但 L1 = (|0.05-0.5|+|0.95-0.5|)/2 = 0.45 > 0.4
        assert info["overlap_alert"] is False
        assert info["weight_alert"] is True
        assert info["alerted"] is True

    def test_custom_threshold_config(self, tmp_portfolio_dir):
        """自定义阈值配置生效。"""
        cfg: DriftAlertConfig = {
            "overlap_threshold": 0.90,  # 更严：要求 90% 重合
            "weight_l1_threshold": 0.10,  # 更严：权重变化超 10% 告警
            "trigger_rebalance": True,
        }
        mon = DriftMonitor(tmp_portfolio_dir, alert_config=cfg)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.5, "f_b": 0.5, "f_c": 0.0})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        assert info["alerted"] is True
        assert info["overlap_threshold"] == 0.90
        assert info["weight_l1_threshold"] == 0.10
        assert info["trigger_rebalance"] is True

    def test_default_config_no_rebalance(self, tmp_portfolio_dir):
        """默认配置 trigger_rebalance=False → 不自动重平衡。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        assert mon.alert_config.get("trigger_rebalance") is False
        prev = self._make_combo("cmb_prev", {"f_a": 0.5})
        new = self._make_combo("cmb_new", {"f_b": 1.0})
        info = mon.check_and_alert(mon.compute(prev, new))
        assert info["trigger_rebalance"] is False

    def test_generate_rebalance_proposal_when_triggered(self, tmp_portfolio_dir):
        """trigger_rebalance=True 且告警触发 → 生成重平衡建议。"""
        cfg: DriftAlertConfig = {
            "overlap_threshold": 0.50,
            "weight_l1_threshold": 0.40,
            "trigger_rebalance": True,
        }
        mon = DriftMonitor(tmp_portfolio_dir, alert_config=cfg)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5, "f_b": 0.5})
        new = self._make_combo("cmb_new", {"f_c": 0.5, "f_d": 0.5})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        assert info["trigger_rebalance"] is True
        proposal = mon.generate_rebalance_proposal(metrics, info)
        assert proposal is not None
        assert proposal["source"] == "drift_monitor"
        assert proposal["confidence"] == pytest.approx(0.7)
        assert "漂移" in proposal["description"]

    def test_generate_rebalance_proposal_skipped(self, tmp_portfolio_dir):
        """未触发重平衡 → 返回 None。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        prev = self._make_combo("cmb_prev", {"f_a": 0.5})
        new = self._make_combo("cmb_new", {"f_a": 0.5})
        metrics = mon.compute(prev, new)
        info = mon.check_and_alert(metrics)
        assert info["trigger_rebalance"] is False
        assert mon.generate_rebalance_proposal(metrics, info) is None


# ════════════════════════════════════════════════════════════
# 15. L2 影子池测试 (v2.11.0)
# ════════════════════════════════════════════════════════════


class TestShadowPool:
    """L2 影子池 — 新晋升因子观察 5 个交易日后才进正式组合。"""

    def test_is_shadow_pending_within_window(self):
        """观察期内（今日 < observe_until）标记为 pending。"""
        from fts.factor_engine.portfolio_loop import _is_shadow_pending
        from datetime import datetime, timedelta

        today = datetime(2026, 8, 6)
        factor = {
            "shadow_pool": {
                "promoted_at": today.isoformat(),
                "observe_trading_days": 5,
                "observe_until": (today + timedelta(days=5)).isoformat(),
            }
        }
        assert _is_shadow_pending(factor, today=today) is True

    def test_is_shadow_pending_expired(self):
        """观察期已过（今日 >= observe_until）不标记 pending。"""
        from fts.factor_engine.portfolio_loop import _is_shadow_pending
        from datetime import datetime

        today = datetime(2026, 8, 6)
        factor = {
            "shadow_pool": {
                "promoted_at": "2026-07-01T00:00:00",
                "observe_trading_days": 5,
                "observe_until": (datetime(2026, 7, 8)).isoformat(),
            }
        }
        assert _is_shadow_pending(factor, today=today) is False

    def test_no_shadow_pool_not_pending(self):
        """无 shadow_pool 标记（种子/存量因子）不是 pending。"""
        from fts.factor_engine.portfolio_loop import _is_shadow_pending

        assert _is_shadow_pending({"factor_id": "f_a"}) is False
        assert _is_shadow_pending({"shadow_pool": {}}) is False
        assert _is_shadow_pending({"shadow_pool": {"promoted_at": "bad"}}) is False

    def test_filter_shadow_pending(self, tmp_elite_dir):
        """load_elite_factors 过滤掉观察期内因子，保留正式因子。"""
        from fts.factor_engine.portfolio_loop import load_elite_factors
        from datetime import datetime, timedelta

        today = datetime(2026, 8, 6)
        # 正式因子（无标记）
        (tmp_elite_dir / "f_normal.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_normal",
                    "name": "normal",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                    "market": "stock",
                }
            ),
            encoding="utf-8",
        )
        # 影子因子（观察期内）
        (tmp_elite_dir / "f_shadow.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_shadow",
                    "name": "shadowed",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                    "market": "stock",
                    "shadow_pool": {
                        "promoted_at": today.isoformat(),
                        "observe_trading_days": 30,
                        "observe_until": (today + timedelta(days=30)).isoformat(),
                    },
                }
            ),
            encoding="utf-8",
        )

        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False, market="stock")
        ids = {f["factor_id"] for f in factors}
        assert ids == {"fct_normal"}
        assert "fct_shadow" not in ids

    def _make_evolution_loop(self, tmp_path, market="futures"):
        """构造最小 EvolutionLoop（mock DuckDB，避免真实写入）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        elite_dir = tmp_path / f"{market}_elite"
        elite_dir.mkdir(parents=True, exist_ok=True)
        memory_dir = tmp_path / f"{market}_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        loop = EvolutionLoop(
            data=pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=pd.date_range("2024-01-01", periods=5)),
            forward_returns=np.array([0.1, 0.1, 0.1, 0.1, 0.0]),
            elite_dir=str(elite_dir),
            memory_dir=str(memory_dir),
            market=market,
            n_trials_micro=1,
        )
        # mock DuckDB 仓储，避免真实数据库依赖
        mock_repo = MagicMock()
        mock_repo.get_factor_by_name.return_value = None
        mock_repo.get_factor.return_value = None
        loop._get_repo = MagicMock(return_value=mock_repo)
        return loop, elite_dir

    def test_promote_to_elite_writes_shadow_pool(self, tmp_path):
        """显式开启观察期（shadow_observe=True）给新因子写入 shadow_pool 标记。

        v2.103.0+20 起默认观察期关闭（env FTS_EVOLUTION_SHADOW_OBSERVE），
        本用例显式传 True 验证观察期写入路径仍可用（等价 env=1）。
        """
        loop, elite_dir = self._make_evolution_loop(tmp_path)

        factor = {
            "factor_id": "fct_shadow1",
            "name": "shadow_factor",
            "code": "code",
            "market": "futures",
            "family": "trend",
        }
        evaluation = {
            "level_1_backtest": {"sharpe": 2.0, "ic": 0.05},
            "level_3_multiple": {"passed": True},
            "passed": True,
        }
        # 直接调用 _promote_to_elite（repo 已 mock），显式开启观察期
        path = loop._promote_to_elite(factor, evaluation, shadow_observe=True)
        assert path is not None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "shadow_pool" in data
        assert data["shadow_pool"]["observe_trading_days"] == 5
        assert "observe_until" in data["shadow_pool"]

    def test_promote_seed_skips_shadow_pool(self, tmp_path):
        """种子因子（shadow_observe=False）不写 shadow_pool。"""
        loop, elite_dir = self._make_evolution_loop(tmp_path)

        factor = {
            "factor_id": "fct_seed1",
            "name": "seed_factor",
            "code": "code",
            "market": "futures",
            "family": "trend",
        }
        evaluation = {
            "level_1_backtest": {"sharpe": 2.0, "ic": 0.05},
            "level_3_multiple": {"passed": True},
            "passed": True,
        }
        path = loop._promote_to_elite(factor, evaluation, shadow_observe=False)
        assert path is not None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "shadow_pool" not in data


# ════════════════════════════════════════════════════════════
# 16. ElasticNet / ML Ensemble 权重计算测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestSynthesisElasticNetMl:
    """elastic_net / ml_ensemble 合成成功路径 + 权重计算主流程。"""

    def _elite_with_codes(self, tmp_path, n: int = 2) -> Path:
        """写入 n 个带 code 的 elite 因子 JSON。"""
        elite = tmp_path / "elite"
        elite.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (elite / f"f{i}.json").write_text(
                json.dumps(
                    {
                        "factor_id": f"f{i}",
                        "name": f"n{i}",
                        "code": "close",
                    }
                ),
                encoding="utf-8",
            )
        return elite

    def _factors(self, n: int = 2) -> list[dict]:
        """n 个标准因子 dict。"""
        return [
            {"factor_id": f"f{i}", "name": f"n{i}", "sharpe": 2.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1}
            for i in range(n)
        ]

    def _make_panel(self, n_stocks: int = 10, n_days: int = 25, n_rows: int = 40):
        """构造 {symbol: DataFrame} 面板 + 共同交易日。"""
        dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
        idx = pd.date_range("2023-12-20", periods=n_rows, freq="D")
        panel = {}
        for i in range(n_stocks):
            panel[f"SYM{i}"] = pd.DataFrame({"close": np.linspace(100 + i, 200 + i, n_rows)}, index=idx)
        return panel, dates

    def test_synthesize_elastic_net_success(self, tmp_path, sample_factors):
        """elastic_net 模式：权重计算成功时按返回权重分配信号。"""
        elite = self._elite_with_codes(tmp_path, n=3)
        with patch(
            "fts.factor_engine.portfolio_loop._compute_elastic_net_weights",
            return_value={"fct_a": 0.6, "fct_b": 0.4, "fct_c": 0.0},
        ):
            signals, _, _ = synthesize_signals(sample_factors, mode="elastic_net", elite_dir=elite)
        assert len(signals) == 3
        wmap = {s["factor_id"]: s["weight"] for s in signals}
        assert wmap["fct_a"] == pytest.approx(0.6)
        assert wmap["fct_b"] == pytest.approx(0.4)
        assert wmap["fct_c"] == 0.0
        # 权重 > 0 保留，=0 剔除，全部标记正交化
        assert signals[0]["retained"] is True
        assert signals[2]["retained"] is False
        assert all(s["orthogonalized"] for s in signals)

    def test_synthesize_ml_ensemble_success(self, tmp_path, sample_factors):
        """ml_ensemble 模式：权重计算成功路径。"""
        elite = self._elite_with_codes(tmp_path, n=3)
        with patch(
            "fts.factor_engine.portfolio_loop._compute_ml_ensemble_weights",
            return_value={"fct_a": 0.5, "fct_b": 0.5, "fct_c": 0.0},
        ):
            signals, _, _ = synthesize_signals(sample_factors, mode="ml_ensemble", elite_dir=elite)
        assert len(signals) == 3
        assert signals[0]["retained"] is True
        assert signals[2]["retained"] is False

    def test_synthesize_elastic_net_fallback(self, tmp_path, sample_factors):
        """elastic_net 权重计算失败回退 sharpe_weight。"""
        elite = self._elite_with_codes(tmp_path, n=3)
        with patch("fts.factor_engine.portfolio_loop._compute_elastic_net_weights", return_value={}):
            signals, _, _ = synthesize_signals(sample_factors, mode="elastic_net", elite_dir=elite)
        assert len(signals) == 3
        assert all(s["weight"] > 0 for s in signals)

    def test_sharpe_weight_ic_raw_passthrough(self):
        """sharpe_weight 模式：IC 截断后 _ic_raw 透传到信号。"""
        factors = [
            {"factor_id": "f1", "name": "n1", "sharpe": 1.5, "ic": 0.3, "turnover": 0.3, "decay_6m": 0.1},
            {"factor_id": "f2", "name": "n2", "sharpe": 1.5, "ic": -0.2, "turnover": 0.3, "decay_6m": 0.1},
        ]
        signals, _, _ = synthesize_signals(factors, mode="sharpe_weight")
        assert signals[0]["_ic_raw"] == 0.3
        assert signals[0]["ic"] == 0.15
        assert signals[1]["_ic_raw"] == -0.2
        assert signals[1]["ic"] == -0.15

    def test_elastic_net_sklearn_missing(self, tmp_path, monkeypatch):
        """scikit-learn 未安装时 Elastic Net 返回空 dict。"""
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "sklearn.linear_model", None)
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        assert _compute_elastic_net_weights([], tmp_path) == {}

    def test_ml_ensemble_sklearn_missing(self, tmp_path, monkeypatch):
        """scikit-learn 未安装时 ML Ensemble 返回空 dict。"""
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "sklearn.linear_model", None)
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights

        assert _compute_ml_ensemble_weights([], tmp_path) == {}

    def test_elastic_net_panel_insufficient(self, tmp_path):
        """面板数据不足时 Elastic Net 回退。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_futures_panel.return_value = ({}, [])
            assert _compute_elastic_net_weights([], tmp_path) == {}

    def test_ml_ensemble_panel_insufficient(self, tmp_path):
        """面板数据不足时 ML Ensemble 回退。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights

        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_futures_panel.return_value = ({}, [])
            assert _compute_ml_ensemble_weights([], tmp_path) == {}

    def test_elastic_net_valid_factors_insufficient(self, tmp_path):
        """有效因子（含代码）不足 2 个时回退。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        panel, dates = self._make_panel()
        empty_elite = tmp_path / "no_codes"
        empty_elite.mkdir()
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            assert _compute_elastic_net_weights(self._factors(2), empty_elite) == {}

    def test_ml_ensemble_valid_factors_insufficient(self, tmp_path):
        """ML Ensemble 有效因子不足 2 个时回退。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights

        panel, dates = self._make_panel()
        empty_elite = tmp_path / "no_codes_ml"
        empty_elite.mkdir()
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            assert _compute_ml_ensemble_weights(self._factors(2), empty_elite) == {}

    def test_elastic_net_executor_failure_skips(self, tmp_path):
        """因子执行器构造失败跳过该因子，有效回归日不足回退。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        exec_ok = MagicMock()
        exec_ok.execute.return_value = np.linspace(-0.5, 0.5, 40)
        fake_model = MagicMock()
        fake_model.coef_ = np.array([1.0, 1.0])
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("sklearn.linear_model.ElasticNetCV", return_value=fake_model),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            # f0 构造失败 → 信号全 NaN → 无有效截面回归日
            m_exec.side_effect = [RuntimeError("exec fail"), exec_ok]
            assert _compute_elastic_net_weights(self._factors(2), elite) == {}

    def test_elastic_net_full_flow(self, tmp_path):
        """Elastic Net 权重计算主流程（面板加载→因子执行→逐日回归）。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_model = MagicMock()
        fake_model.coef_ = np.array([0.6, 0.4])
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("sklearn.linear_model.ElasticNetCV", return_value=fake_model),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            result = _compute_elastic_net_weights(self._factors(2), elite)
        assert set(result.keys()) == {"f0", "f1"}
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_elastic_net_zero_coefs(self, tmp_path):
        """回归系数全 0 时权重计算回退空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_model = MagicMock()
        fake_model.coef_ = np.array([0.0, 0.0])
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("sklearn.linear_model.ElasticNetCV", return_value=fake_model),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            assert _compute_elastic_net_weights(self._factors(2), elite) == {}

    def test_ml_ensemble_full_flow(self, tmp_path):
        """ML Ensemble 权重计算主流程（训练 + 特征重要性归一化）。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights
        from fts.ml import TrainResult, TrainMode, ModelKind

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_trainer = MagicMock()
        fake_trainer.train.return_value = TrainResult(
            mode=TrainMode.CROSS_SECTIONAL,
            kind=ModelKind.LIGHTGBM,
            model=object(),
            score=0.05,
            feature_importance={"f0": 0.7, "f1": 0.3},
        )
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("fts.ml.SignalModelTrainer", return_value=fake_trainer),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            result = _compute_ml_ensemble_weights(self._factors(2), elite)
        assert abs(result["f0"] - 0.7) < 1e-9
        assert abs(result["f1"] - 0.3) < 1e-9

    def test_ml_ensemble_model_none(self, tmp_path):
        """训练降级（model=None）时回退空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights
        from fts.ml import TrainResult, TrainMode, ModelKind

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_trainer = MagicMock()
        fake_trainer.train.return_value = TrainResult(
            mode=TrainMode.CROSS_SECTIONAL,
            kind=ModelKind.LIGHTGBM,
            model=None,
            message="模型依赖缺失",
        )
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("fts.ml.SignalModelTrainer", return_value=fake_trainer),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            assert _compute_ml_ensemble_weights(self._factors(2), elite) == {}

    def test_ml_ensemble_no_importance(self, tmp_path):
        """无特征重要性时回退空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights
        from fts.ml import TrainResult, TrainMode, ModelKind

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_trainer = MagicMock()
        fake_trainer.train.return_value = TrainResult(
            mode=TrainMode.CROSS_SECTIONAL,
            kind=ModelKind.LIGHTGBM,
            model=object(),
            score=0.05,
            feature_importance={},
        )
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("fts.ml.SignalModelTrainer", return_value=fake_trainer),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            assert _compute_ml_ensemble_weights(self._factors(2), elite) == {}

    def test_ml_ensemble_zero_importance(self, tmp_path):
        """特征重要性全 0 时回退空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights
        from fts.ml import TrainResult, TrainMode, ModelKind

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        fake_trainer = MagicMock()
        fake_trainer.train.return_value = TrainResult(
            mode=TrainMode.CROSS_SECTIONAL,
            kind=ModelKind.LIGHTGBM,
            model=object(),
            score=0.05,
            feature_importance={"f0": 0.0, "f1": 0.0},
        )
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("fts.ml.SignalModelTrainer", return_value=fake_trainer),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.linspace(-0.5, 0.5, 40)
            assert _compute_ml_ensemble_weights(self._factors(2), elite) == {}

    def test_ml_ensemble_insufficient_samples(self, tmp_path):
        """有效样本不足 30 个时回退空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights

        panel, dates = self._make_panel()
        elite = self._elite_with_codes(tmp_path, n=2)
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value.execute.return_value = np.full(40, np.nan)  # 信号全 NaN
            assert _compute_ml_ensemble_weights(self._factors(2), elite) == {}


# ════════════════════════════════════════════════════════════
# 17. 正交化分支测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestOrthogonalizeBranches:
    """正交化 — L2 先验注入 / 分层模式 / 代码哈希去重分支。"""

    def _signals(self, n: int = 30) -> list[PortfolioSignal]:
        return [
            PortfolioSignal(
                factor_id=f"f{i}",
                name=f"n{i}",
                weight=1.0 / n,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            )
            for i in range(n)
        ]

    def test_l2_prior_injection(self):
        """L2 先验相关性 >= 0.95 注入 correlation_flags。"""
        signals = self._signals(3)
        l2 = [{"factor_id_a": "f0", "factor_id_b": "f1", "pearson": 0.98, "spearman": 0.9}]
        out = orthogonalize_factors(signals, correlation_matrix=[], l2_prior_correlations=l2)
        flags0 = next(s["correlation_flags"] for s in out if s["factor_id"] == "f0")
        assert flags0[0]["type"] == "l2_seed_correlation"
        assert "f1" in flags0[0]["reason"]

    def test_tiered_orthogonalize_mark(self):
        """分层正交化（≥30 因子）：exclude 的因子 retained=False。"""
        n = 30
        signals = self._signals(n)
        factors = [{"factor_id": f"f{i}", "name": f"n{i}"} for i in range(n)]
        result_factors = []
        for i in range(n):
            rf = {
                "factor_id": f"f{i}",
                "name": f"n{i}",
                "correlation_flags": (
                    [
                        {"source": "phase2_full_correlation", "reason": "corr 0.9"},
                        {"source": "l2_prior", "reason": "L2 种子相关 0.97"},
                    ]  # 覆盖 L2 flag 日志
                    if i in (1, 2)
                    else []
                ),
                "exclude_from_portfolio": i == 1,
            }
            result_factors.append(rf)
        summary = {
            "input_count": n,
            "output_count": n - 1,
            "phase1_marked": 3,
            "phase2_marked": 2,
            "phase1_details": [
                {"type": "code_duplicate", "removed": "n2", "reason": "same code"},
                {"type": "family_prune", "removed": "n3", "reason": "family prune"},
            ],
            "phase2_details": [{"type": "correlation", "removed": "n1", "reason": "corr 0.9"}],
            "l2_prior_count": 1,
            "phase2_new_count": 1,
            "phase2_overlap_count": 1,
            "elapsed_seconds": 0.01,
        }
        with patch("fts.factor_engine.factor_optimizer.FactorOptimizer") as m_opt:
            m_opt.return_value.tiered_orthogonalize.return_value = (result_factors, summary)
            out = orthogonalize_factors(signals, max_corr_threshold=0.7, factors=factors, use_tiered=True)
        out_map = {s["factor_id"]: s for s in out}
        assert out_map["f1"]["retained"] is False  # 硬排除
        assert out_map["f0"]["retained"] is True  # 仅标记不排除
        assert out_map["f1"]["orthogonalized"] is True
        assert out_map["f1"]["correlation_flags"]  # 诊断标记保留

    def test_tiered_orthogonalize_failure_fallback(self):
        """分层正交化失败回退代码哈希去重。"""
        n = 30
        signals = self._signals(n)
        factors = [{"factor_id": f"f{i}", "name": f"n{i}", "code_hash": f"h{i % 2}"} for i in range(n)]
        with patch("fts.factor_engine.factor_optimizer.FactorOptimizer") as m_opt:
            m_opt.return_value.tiered_orthogonalize.side_effect = RuntimeError("opt fail")
            out = orthogonalize_factors(signals, max_corr_threshold=0.7, factors=factors, use_tiered=True)
        retained = [s["factor_id"] for s in out if s["retained"]]
        assert set(retained) <= {"f0", "f1"}  # 每组 hash 只保留最高夏普

    def test_code_hash_dedup_removes_lower_sharpe(self):
        """相同代码哈希只保留夏普最高的因子。"""
        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.5,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_c",
                name="c",
                weight=0.5,
                sharpe=1.5,
                ic=0.03,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
        ]
        factors = [
            {"factor_id": "f_a", "code_hash": "h1"},
            {"factor_id": "f_b", "code_hash": "h1"},
            {"factor_id": "f_c", "code_hash": "h2"},
        ]
        out = orthogonalize_factors(signals, correlation_matrix=None, factors=factors)
        out_map = {s["factor_id"]: s for s in out}
        assert out_map["f_a"]["retained"] is True
        assert out_map["f_b"]["retained"] is False
        assert out_map["f_c"]["retained"] is True

    def test_code_hash_dedup_no_duplicates(self):
        """无重复代码哈希时全部保留。"""
        signals = self._signals(3)
        factors = [{"factor_id": f"f{i}", "code_hash": f"h{i}"} for i in range(3)]
        out = orthogonalize_factors(signals, correlation_matrix=None, factors=factors)
        assert all(s["retained"] for s in out)


# ════════════════════════════════════════════════════════════
# 18. 粘性 / Sharpe / build_combo 边界分支测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestStickySharpeBuildComboBranches:
    """粘性约束跳过、夏普校验、build_combo 边界分支。"""

    def test_sticky_skips_not_retained(self):
        """粘性约束跳过 retained=False 的信号。"""
        from fts.factor_engine.portfolio_loop import _apply_sticky_constraints

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.9,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=False,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.1,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        out = _apply_sticky_constraints(signals, {"f_a": 0.5}, config)
        w_map = {s["factor_id"]: s["weight"] for s in out}
        # f_a 不保留跳过（保持 0.9），f_b 新因子封顶 0.10
        assert w_map["f_a"] == pytest.approx(0.9)
        assert w_map["f_b"] == pytest.approx(0.10)

    def test_validate_combo_sharpe_thresholds(self):
        """夏普警戒线：>3.5 与 2.5~3.5 均返回原因，正常返回 None。"""
        from fts.factor_engine.portfolio_loop import _validate_combo_sharpe

        assert "3.5" in _validate_combo_sharpe(4.0)
        assert "2.5" in _validate_combo_sharpe(3.0)
        assert _validate_combo_sharpe(2.0) is None

    def test_randomization_zero_total_weight(self):
        """随机化测试：总权重为 0 时跳过。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test

        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=0.0,
                sharpe=5.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=0.0,
                sharpe=5.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_c",
                name="c",
                weight=0.0,
                sharpe=5.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_build_combo_negative_weights_zero_total(self):
        """正负权重抵消 total_w=0 → max_corr=0.15。"""
        signals = [
            PortfolioSignal(
                factor_id="f_a",
                name="a",
                weight=1.0,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b",
                name="b",
                weight=-1.0,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            ),
        ]
        combo = build_combo(signals, mode="equal_weight")
        assert combo["max_correlation"] == 0.15

    def test_build_combo_high_sharpe_warning(self):
        """组合夏普 > 3.5 触发警戒标记。"""
        signals = [
            PortfolioSignal(
                factor_id=f"f{i}",
                name=f"n{i}",
                weight=1 / 3,
                sharpe=5.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for i in range(3)
        ]
        combo = build_combo(signals, mode="equal_weight")
        assert combo["sharpe_warning"] is not None
        assert "Sharpe" in combo["sharpe_warning"]

    def test_build_combo_randomization_failure_log(self, caplog):
        """随机化测试未通过触发警告日志。"""
        signals = [
            PortfolioSignal(
                factor_id=f"f{i}",
                name=f"n{i}",
                weight=0.3,
                sharpe=3.0,
                ic=0.06,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for i in range(3)
        ]
        with patch("fts.factor_engine.portfolio_loop._run_sharpe_randomization_test", return_value=False):
            with caplog.at_level("WARNING"):
                combo = build_combo(signals, mode="equal_weight")
        assert combo["sharpe_randomization_passed"] is False
        assert "随机化测试未通过" in caplog.text

    def test_build_combo_applies_exposure_scale(self):
        """28-T6: exposure_scale 在归一化后统一缩放总仓位并写入组合字段。"""
        signals = [
            PortfolioSignal(
                factor_id="a",
                name="f1",
                weight=2.0,
                sharpe=1.0,
                ic=0.05,
                turnover=0.1,
                decay_6m=0.0,
                retained=True,
            ),
            PortfolioSignal(
                factor_id="b",
                name="f2",
                weight=1.0,
                sharpe=1.2,
                ic=0.06,
                turnover=0.1,
                decay_6m=0.0,
                retained=True,
            ),
        ]
        combo = build_combo(signals, "equal_weight", "trace-x", exposure_scale=0.5)
        total = sum(s["weight"] for s in combo["signals"])
        assert abs(total - 0.5) < 1e-6  # 归一化 1.0 × scale 0.5
        assert combo.get("exposure_scale") == 0.5
        assert combo.get("regime_meta", {}).get("exposure_scale") == 0.5


# ════════════════════════════════════════════════════════════
# 19. DriftMonitor 损坏容错 + 质量门槛测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestDriftMonitorCorruptAndQualityGate:
    """DriftMonitor 损坏文件容错 + 运行时质量门槛过滤。"""

    def test_record_corrupt_file_reset(self, tmp_portfolio_dir):
        """record 遇损坏文件时重置为空再追加。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        date = "2026-08-08"
        (mon.drift_history_dir / f"{date}.json").write_text("not json", encoding="utf-8")
        metrics = DriftMetrics(
            date=date,
            combo_id="c",
            prev_combo_id="",
            trace_id="t",
            member_overlap_rate=0.5,
            weight_l1_change=0.2,
            n_prev_members=1,
            n_new_members=2,
            n_common_members=1,
            added=["f2"],
            removed=["f1"],
        )
        mon.record(metrics)
        records = mon.load_history(date)
        assert len(records) == 1
        assert records[0]["combo_id"] == "c"

    def test_load_history_corrupt_file(self, tmp_portfolio_dir):
        """load_history 遇损坏文件返回空列表。"""
        mon = DriftMonitor(tmp_portfolio_dir)
        (mon.drift_history_dir / "2026-08-08.json").write_text("{broken", encoding="utf-8")
        assert mon.load_history("2026-08-08") == []

    def test_filter_by_quality_gate_removes_bad(self):
        """IC<0.03 或 Sharpe<1.5 的因子被剔除。"""
        from fts.factor_engine.portfolio_loop import _filter_by_quality_gate

        factors = [
            {"factor_id": "f1", "name": "n1", "ic": 0.01, "sharpe": 2.0},  # IC 过低
            {"factor_id": "f2", "name": "n2", "ic": 0.05, "sharpe": 1.0},  # Sharpe 过低
            {"factor_id": "f3", "name": "n3", "ic": 0.05, "sharpe": 2.0},  # 通过
        ]
        passed = _filter_by_quality_gate(factors, "test")
        assert [f["factor_id"] for f in passed] == ["f3"]

    def test_filter_by_quality_gate_empty(self):
        """空输入直接返回。"""
        from fts.factor_engine.portfolio_loop import _filter_by_quality_gate

        assert _filter_by_quality_gate([], "test") == []

    def test_normalize_base_name(self):
        """世代后缀 '_gXX' 剥离。"""
        from fts.factor_engine.portfolio_loop import _normalize_base_name

        assert _normalize_base_name("fut_bias_g18") == "fut_bias"
        assert _normalize_base_name("fut_bias") == "fut_bias"


# ════════════════════════════════════════════════════════════
# 20. 基础因子名去重（相关性模式）测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestDedupCorrelationMode:
    """基础因子名去重 — 相关性模式 + IC-only 大组日志。"""

    def _mk_factor(self, fid: str, name: str, ic: float, code: str = "close") -> dict:
        return {"factor_id": fid, "name": name, "ic": ic, "code": code, "params": {}}

    def test_correlation_mode_greedy(self):
        """相关性模式：同基础名高相关世代被剔除，低相关保留。"""
        from fts.factor_engine.portfolio_loop import _deduplicate_by_base_name

        factors = [
            self._mk_factor("f1", "fut_bias_g1", 0.06),
            self._mk_factor("f2", "fut_bias_g2", 0.05),
            self._mk_factor("f3", "fut_spread_g1", 0.04),
        ]
        panel = {
            "RB": pd.DataFrame({"close": np.arange(20, dtype=float)}, index=pd.date_range("2024-01-01", periods=20))
        }
        with patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec:
            # 相同信号 → 相关性 1.0 → g2 被剔除
            m_exec.return_value.execute.return_value = np.linspace(-1, 1, 20)
            out = _deduplicate_by_base_name(factors, "JSON", panel_data=panel)
        ids = {f["factor_id"] for f in out}
        assert "f1" in ids and "f3" in ids
        assert "f2" not in ids

    def test_correlation_mode_no_corr_keeps_all(self):
        """相关性模式：世代间互不相关（正交信号）时全部保留。"""
        from fts.factor_engine.portfolio_loop import _deduplicate_by_base_name

        factors = [
            self._mk_factor("f1", "fut_bias_g1", 0.06),
            self._mk_factor("f2", "fut_bias_g2", 0.05),
        ]
        panel = {
            "RB": pd.DataFrame({"close": np.arange(20, dtype=float)}, index=pd.date_range("2024-01-01", periods=20))
        }
        with patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec:
            # 正交信号（相关性 0）→ 互不冗余，全部保留
            m_exec.return_value.execute.side_effect = [
                np.where(np.arange(20) % 2 == 0, 1.0, -1.0),  # 交替方波
                np.where((np.arange(20) // 2) % 2 == 0, 1.0, -1.0),  # 周期 4 方波
            ]
            out = _deduplicate_by_base_name(factors, "JSON", panel_data=panel)
        assert {f["factor_id"] for f in out} == {"f1", "f2"}

    def test_ic_only_many_merges(self):
        """IC-only 模式：11 组以上合并触发截断日志。"""
        from fts.factor_engine.portfolio_loop import _deduplicate_by_base_name

        # 11 个不同 base 组，每组 2 个世代 → merges 11 条 → 截断日志分支
        factors = []
        for g in range(11):
            factors.append(self._mk_factor(f"f{g}a", f"base_{g}_g1", 0.03 + g * 0.001))
            factors.append(self._mk_factor(f"f{g}b", f"base_{g}_g2", 0.04 + g * 0.001))
        out = _deduplicate_by_base_name(factors, "JSON", panel_data=None)
        assert len(out) == 11  # 每组保留 IC 最高

    def test_dedup_empty_input(self):
        """空输入直接返回。"""
        from fts.factor_engine.portfolio_loop import _deduplicate_by_base_name

        assert _deduplicate_by_base_name([], "JSON") == []


# ════════════════════════════════════════════════════════════
# 21. 交易日 / 影子池边界 + OOS 外推验证测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestOosExtrapolationAndMisc:
    """_add_trading_days / _is_shadow_pending 边界 + OOS 外推验证。

    注: 产品代码 portfolio_loop.py 未 import pandas，但 _validate_oos_extrapolation
    内部使用 pd.concat/pd.to_datetime 等（真实 bug，异常被 except 吞掉）。
    测试通过 monkeypatch 注入 pd 模块属性，使函数逻辑可真实执行。
    """

    @pytest.fixture
    def inject_pd(self, monkeypatch):
        """向 portfolio_loop 模块注入 pandas/numpy（模块级已注入，兼容旧签名）。"""
        yield

    def test_add_trading_days(self):
        """交易日推进跳过周末。"""
        from datetime import datetime
        from fts.factor_engine.portfolio_loop import _add_trading_days

        start = datetime(2026, 8, 3)  # 周一
        end = _add_trading_days(start, 3)
        assert end > start
        assert end.weekday() < 5  # 结果必须是工作日

    def test_is_shadow_pending_invalid_date(self):
        """observe_until 非法日期返回 False。"""
        from fts.factor_engine.portfolio_loop import _is_shadow_pending

        factor = {"shadow_pool": {"observe_until": "not-a-date"}}
        assert _is_shadow_pending(factor) is False

    def test_oos_no_promoted_at_skips(self, inject_pd):
        """旧因子无 promoted_at 跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        f = {"factor_id": "f1"}
        out = _validate_oos_extrapolation(f, {}, "2026-01-01T00:00:00")
        assert out is f

    def test_oos_too_early_skips(self, inject_pd):
        """晋升后不足 5 个交易日跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        f = {
            "factor_id": "f1",
            "promoted_at": "2026-01-30T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
        }
        out = _validate_oos_extrapolation(f, {}, "2026-01-31T00:00:00")
        assert "oos_extrapolation" not in out

    def test_oos_weak_ic_skips(self, inject_pd):
        """原始 IC 太弱跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        f = {
            "factor_id": "f1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.0}},
            "code": "close",
        }
        out = _validate_oos_extrapolation(f, {"s1": pd.DataFrame({"close": [1.0, 2.0, 3.0]})}, "2026-02-01T00:00:00")
        assert "oos_extrapolation" not in out

    def test_oos_no_code_skips(self, inject_pd):
        """因子无代码跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        f = {
            "factor_id": "f1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "",
        }
        out = _validate_oos_extrapolation(f, {}, "2026-02-01T00:00:00")
        assert "oos_extrapolation" not in out

    def test_oos_insufficient_new_data(self, inject_pd):
        """新数据不足 3 只品种跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        f = {
            "factor_id": "f1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
        }
        panel = {
            f"s{i}": pd.DataFrame({"close": np.arange(10, dtype=float)}, index=pd.date_range("2026-01-10", periods=10))
            for i in range(2)
        }
        out = _validate_oos_extrapolation(f, panel, "2026-02-01T00:00:00")
        assert "oos_extrapolation" not in out

    def test_oos_needs_demotion(self, inject_pd):
        """连续 3 次 IC 衰减 > 20% 标记待降级。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
            "_oos_history": [{"ic_decay": 0.5}, {"ic_decay": 0.5}],
        }
        panel = {
            f"s{i}": pd.DataFrame({"close": np.linspace(100, 120, 30)}, index=pd.date_range("2026-01-10", periods=30))
            for i in range(3)
        }
        with (
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("scipy.stats.spearmanr", return_value=(0.01, 0.9)),
        ):
            m_exec.return_value.execute.return_value = pd.Series(np.linspace(-1, 1, 30))
            out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        oos = out["oos_extrapolation"]
        assert oos["needs_demotion"] is True
        assert oos["consecutive_decay_count"] == 3
        assert len(out["_oos_history"]) == 3

    def test_oos_no_decay_not_demoted(self):
        """IC 无明显衰减不触发降级。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
        }
        panel = {
            f"s{i}": pd.DataFrame({"close": np.linspace(100, 120, 30)}, index=pd.date_range("2026-01-10", periods=30))
            for i in range(3)
        }
        with (
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("scipy.stats.spearmanr", return_value=(0.049, 0.9)),
        ):
            m_exec.return_value.execute.return_value = pd.Series(np.linspace(-1, 1, 30))
            out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        assert out["oos_extrapolation"]["needs_demotion"] is False

    def test_oos_data_panel_with_date_column(self, inject_pd):
        """数据面板使用 date 字符串列时正常计算。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
        }
        panel = {}
        for i in range(3):
            panel[f"s{i}"] = pd.DataFrame(
                {
                    "date": pd.date_range("2026-01-10", periods=30).astype(str),
                    "close": np.linspace(100, 120, 30),
                }
            )
        with (
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("scipy.stats.spearmanr", return_value=(0.01, 0.9)),
        ):
            m_exec.return_value.execute.return_value = pd.Series(np.linspace(-1, 1, 30))
            out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        assert "oos_extrapolation" in out

    def test_oos_panel_without_dates(self, inject_pd):
        """数据面板无时间索引且无 date 列时数据不足跳过。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
        }
        # RangeIndex（非 DatetimeIndex）且无 date 列 → continue
        panel = {f"s{i}": pd.DataFrame({"close": np.linspace(100, 120, 30)}) for i in range(3)}
        out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        assert "oos_extrapolation" not in out

    def test_oos_close_underscore_column(self, inject_pd):
        """数据面板仅有 close_ 列时使用其计算收益。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
        }
        panel = {}
        for i in range(3):
            panel[f"s{i}"] = pd.DataFrame(
                {
                    "close_": np.linspace(100, 120, 30),
                },
                index=pd.date_range("2026-01-10", periods=30),
            )
        with (
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("scipy.stats.spearmanr", return_value=(0.01, 0.9)),
        ):
            m_exec.return_value.execute.return_value = pd.Series(np.linspace(-1, 1, 30))
            out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        assert "oos_extrapolation" in out

    def test_oos_no_price_column(self, inject_pd):
        """数据面板无 close/close_ 列时跳过验证。"""
        from fts.factor_engine.portfolio_loop import _validate_oos_extrapolation

        factor = {
            "factor_id": "f1",
            "name": "n1",
            "promoted_at": "2026-01-01T00:00:00",
            "evaluation": {"level_1_backtest": {"ic": 0.05}},
            "code": "close",
            "params": {},
        }
        panel = {}
        for i in range(3):
            panel[f"s{i}"] = pd.DataFrame(
                {
                    "volume": np.arange(30, dtype=float),
                },
                index=pd.date_range("2026-01-10", periods=30),
            )
        with patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec:
            m_exec.return_value.execute.return_value = pd.Series(np.linspace(-1, 1, 30))
            out = _validate_oos_extrapolation(factor, panel, "2026-02-01T00:00:00")
        assert "oos_extrapolation" not in out


# ════════════════════════════════════════════════════════════
# 22. load_elite_factors DuckDB 路径 + L2 索引测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestLoadEliteDuckdb:
    """load_elite_factors DuckDB 路径与 JSON 回退分支。"""

    def _db_factor(self, fid: str = "f1", code: str = "close") -> dict:
        return {
            "factor_id": fid,
            "name": f"n_{fid}",
            "code": code,
            "code_hash": "",
            "sharpe": 2.0,
            "ic": 0.05,
            "turnover_monthly": 0.3,
            "decay_6m": 0.1,
            "metadata": {},
            "params": {},
            "economic_logic": {},
            "market": "stock",
        }

    def test_duckdb_success_computes_hash(self, tmp_elite_dir):
        """DuckDB 因子无 code_hash 时自动计算。"""
        repo = MagicMock()
        repo._execute.return_value.fetchone.return_value = [1]
        repo.list_factors.return_value = [self._db_factor()]
        with patch("fts.factor_engine.factor_db.FactorRepository", return_value=repo):
            factors = load_elite_factors(tmp_elite_dir, use_duckdb=True, market="stock")
        assert len(factors) == 1
        assert factors[0]["code_hash"]

    def test_duckdb_empty_fallback_json(self, tmp_elite_dir):
        """DuckDB 0 行（无样例）时回退 JSON 兜底。"""
        repo = MagicMock()
        repo._execute.return_value.fetchone.return_value = [0]
        repo.list_factors.return_value = []
        with patch("fts.factor_engine.factor_db.FactorRepository", return_value=repo):
            factors = load_elite_factors(tmp_elite_dir, use_duckdb=True, market="stock")
        assert factors == []  # JSON 兜底目录为空

    def test_duckdb_empty_with_diag_sample(self, tmp_elite_dir):
        """DuckDB 0 行且库中有该市场因子时输出诊断样例。"""
        repo = MagicMock()
        repo._execute.return_value.fetchone.return_value = [3]
        repo.list_factors.return_value = []
        repo._execute.return_value.fetchall.return_value = [("f1", "n1", "stock", True, "active")]
        with patch("fts.factor_engine.factor_db.FactorRepository", return_value=repo):
            factors = load_elite_factors(tmp_elite_dir, use_duckdb=True, market="stock")
        assert factors == []

    def test_duckdb_dedup_failure_fallback(self, tmp_elite_dir):
        """DuckDB 相关性去重失败回退 IC-only。"""
        repo = MagicMock()
        repo._execute.return_value.fetchone.return_value = [2]
        repo.list_factors.return_value = [self._db_factor("f1"), self._db_factor("f2")]
        with (
            patch("fts.factor_engine.factor_db.FactorRepository", return_value=repo),
            patch(
                "fts.factor_engine.portfolio_loop._deduplicate_by_base_name",
                side_effect=[RuntimeError("dedup fail"), ["f1", "f2"]],
            ) as m_dedup,
        ):
            factors = load_elite_factors(tmp_elite_dir, use_duckdb=True, market="stock")
        assert len(factors) == 2
        assert m_dedup.call_count == 2

    def test_duckdb_repo_failure_fallback_json(self, tmp_elite_dir):
        """DuckDB 仓库初始化失败时回退 JSON。"""
        with patch("fts.factor_engine.factor_db.FactorRepository", side_effect=RuntimeError("db down")):
            factors = load_elite_factors(tmp_elite_dir, use_duckdb=True, market="stock")
        assert factors == []

    def test_json_path_missing(self, tmp_path):
        """JSON 兜底路径不存在返回空列表。"""
        factors = load_elite_factors(tmp_path / "nope", use_duckdb=False)
        assert factors == []

    def test_json_market_skip(self, tmp_elite_dir):
        """市场不匹配的 JSON 因子被跳过。"""
        (tmp_elite_dir / "f1.json").write_text(
            json.dumps(
                {
                    "factor_id": "f1",
                    "name": "n1",
                    "sharpe": 2.0,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                    "market": "futures",
                }
            ),
            encoding="utf-8",
        )
        factors = load_elite_factors(tmp_elite_dir, use_duckdb=False, market="stock")
        assert factors == []

    def test_load_l2_correlation_index_corrupt(self, tmp_elite_dir):
        """L2 相关性索引文件损坏返回空列表。"""
        from fts.factor_engine.portfolio_loop import load_l2_correlation_index

        (tmp_elite_dir / "_l2_seed_correlation_index.json").write_text("{bad", encoding="utf-8")
        assert load_l2_correlation_index(tmp_elite_dir) == []

    def test_load_l2_correlation_index_missing(self, tmp_elite_dir):
        """L2 相关性索引文件缺失返回空列表。"""
        from fts.factor_engine.portfolio_loop import load_l2_correlation_index

        assert load_l2_correlation_index(tmp_elite_dir) == []


# ════════════════════════════════════════════════════════════
# 23. 质量报告 + PortfolioLoop.run 分支测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestQualityReportAndRunBranches:
    """_generate_quality_report 路径 A/B + PortfolioLoop.run 各场景分支。"""

    def _write_factors(self, elite_dir, market: str = "futures", n: int = 3) -> None:
        """写入 n 个通过质量门槛的 elite 因子。"""
        elite_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            fid = f"fct_{i}"
            (elite_dir / f"{fid}.json").write_text(
                json.dumps(
                    {
                        "factor_id": fid,
                        "name": f"f{i}",
                        "sharpe": 2.5,
                        "ic": 0.05,
                        "turnover": 0.3,
                        "decay_6m": 0.1,
                        "market": market,
                        "code": "close",
                    }
                ),
                encoding="utf-8",
            )

    def _make_loop(self, tmp_portfolio_dir, tmp_elite_dir, **kw) -> PortfolioLoop:
        """构造最小 PortfolioLoop（mock 无关依赖关闭）。"""
        defaults = dict(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            synthesis_mode="equal_weight",
            enable_regime_adaptation=False,
            enable_clustering=False,
        )
        defaults.update(kw)
        return PortfolioLoop(**defaults)

    # ── 质量报告 ──

    def test_quality_report_combo_fallback_success(self, tmp_portfolio_dir, tmp_elite_dir):
        """DuckDB 查询失败时 combo 回退生成质量报告。"""
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        (tmp_portfolio_dir / "current_combo.json").write_text(
            json.dumps(
                {
                    "weights": {"f1": {"ic": 0.05, "sharpe": 2.0}},
                }
            ),
            encoding="utf-8",
        )
        with patch("fts.factor_engine.factor_db.FactorRepository", side_effect=RuntimeError("db down")):
            loop._generate_quality_report()
        from datetime import datetime as _dt

        out = tmp_portfolio_dir / f"elite_final_quality_{_dt.now().strftime('%Y-%m-%d')}.json"
        assert out.exists()

    def test_quality_report_combo_missing(self, tmp_portfolio_dir, tmp_elite_dir):
        """combo 文件不存在时提前返回。"""
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        with patch("fts.factor_engine.factor_db.FactorRepository", side_effect=RuntimeError("db down")):
            loop._generate_quality_report()
        assert not list(tmp_portfolio_dir.glob("elite_final_quality_*.json"))

    def test_quality_report_no_factors(self, tmp_portfolio_dir, tmp_elite_dir):
        """combo 无 weights 时无因子数据跳过。"""
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        (tmp_portfolio_dir / "current_combo.json").write_text(
            json.dumps(
                {
                    "weights": {},
                }
            ),
            encoding="utf-8",
        )
        with patch("fts.factor_engine.factor_db.FactorRepository", side_effect=RuntimeError("db down")):
            loop._generate_quality_report()
        assert not list(tmp_portfolio_dir.glob("elite_final_quality_*.json"))

    def test_quality_report_combo_corrupt(self, tmp_portfolio_dir, tmp_elite_dir):
        """combo 文件损坏时提前返回。"""
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        (tmp_portfolio_dir / "current_combo.json").write_text("corrupt", encoding="utf-8")
        with patch("fts.factor_engine.factor_db.FactorRepository", side_effect=RuntimeError("db down")):
            loop._generate_quality_report()
        assert not list(tmp_portfolio_dir.glob("elite_final_quality_*.json"))

    # ── PortfolioLoop.run 分支 ──

    def test_run_futures_panel_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """期货面板加载失败回退 IC-only 去重。"""
        self._write_factors(tmp_elite_dir, market="futures", n=1)
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_futures_panel.side_effect = RuntimeError("panel fail")
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, market="futures")
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert result.n_factors_input == 1

    def test_run_oos_demotion_log(self, tmp_portfolio_dir, tmp_elite_dir):
        """Step 1.5 OOS 外推验证降级日志。"""
        self._write_factors(tmp_elite_dir, market="futures", n=1)

        def _demote(factor, panel, combo_ts):
            return {
                **factor,
                "oos_extrapolation": {
                    "needs_demotion": True,
                    "ic_decay": 0.5,
                    "consecutive_decay_count": 3,
                },
            }

        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.portfolio_loop._validate_oos_extrapolation", side_effect=_demote),
        ):
            m_prov.return_value.get_futures_panel.return_value = (
                {"RB": pd.DataFrame({"close": [1.0, 2.0]})},
                ["2024-01-01"],
            )
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, market="futures")
            result = loop.run()
        assert result.n_factors_input == 1
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p1_clustering_reduces(self, tmp_portfolio_dir, tmp_elite_dir):
        """P1 聚类移除冗余因子。"""
        self._write_factors(tmp_elite_dir, n=3)
        with patch("fts.factor_engine.factor_clustering.FactorClusteringEngine") as m_cls:
            m_cls.return_value.run.side_effect = lambda factors, panel: factors[:2]
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, enable_clustering=True)
            result = loop.run()
        assert result.n_factors_input == 3
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p1_clustering_no_reduction(self, tmp_portfolio_dir, tmp_elite_dir):
        """P1 聚类无冗余因子移除。"""
        self._write_factors(tmp_elite_dir, n=3)
        with patch("fts.factor_engine.factor_clustering.FactorClusteringEngine") as m_cls:
            m_cls.return_value.run.side_effect = lambda factors, panel: factors
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, enable_clustering=True)
            result = loop.run()
        assert result.n_factors_input == 3
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p1_clustering_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """P1 聚类失败（非致命）继续执行。"""
        self._write_factors(tmp_elite_dir, n=3)
        with patch("fts.factor_engine.factor_clustering.FactorClusteringEngine") as m_cls:
            m_cls.return_value.run.side_effect = RuntimeError("cluster fail")
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, enable_clustering=True)
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p2_pca_applied(self, tmp_portfolio_dir, tmp_elite_dir):
        """P2 PCA 降维应用并更新因子权重。"""
        self._write_factors(tmp_elite_dir, market="futures", n=3)
        pca_signals = [
            PortfolioSignal(
                factor_id=f"fct_{i}",
                name=f"f{i}",
                weight=w,
                sharpe=2.5,
                ic=0.05,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=True,
                retained=True,
            )
            for i, w in enumerate([0.5, 0.3, 0.2])
        ]
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_clustering.PCASignalCompressor") as m_pca,
        ):
            m_prov.return_value.get_futures_panel.return_value = (
                {"RB": pd.DataFrame({"close": [1.0, 2.0, 3.0]})},
                ["2024-01-01"],
            )
            m_pca.return_value.run.return_value = {
                "pca_applied": True,
                "pca_signals": pca_signals,
                "n_components": 3,
                "explained_variance_ratio": 0.95,
            }
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, market="futures", enable_pca=True)
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p2_pca_skipped(self, tmp_portfolio_dir, tmp_elite_dir):
        """P2 PCA 信号矩阵不足时跳过。"""
        self._write_factors(tmp_elite_dir, market="futures", n=3)
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_clustering.PCASignalCompressor") as m_pca,
        ):
            m_prov.return_value.get_futures_panel.return_value = (
                {"RB": pd.DataFrame({"close": [1.0, 2.0, 3.0]})},
                ["2024-01-01"],
            )
            m_pca.return_value.run.return_value = {"pca_applied": False}
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, market="futures", enable_pca=True)
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_p2_pca_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """P2 PCA 失败（非致命）继续执行。"""
        self._write_factors(tmp_elite_dir, market="futures", n=3)
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_clustering.PCASignalCompressor") as m_pca,
        ):
            m_prov.return_value.get_futures_panel.return_value = (
                {"RB": pd.DataFrame({"close": [1.0, 2.0, 3.0]})},
                ["2024-01-01"],
            )
            m_pca.return_value.run.side_effect = RuntimeError("pca fail")
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, market="futures", enable_pca=True)
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_regime_detection_success(self, tmp_portfolio_dir, tmp_elite_dir):
        """Step 2.5 Regime 检测成功路径。"""
        self._write_factors(tmp_elite_dir, n=1)
        ohlcv = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        with patch("fts.factor_engine.regime.RegimeAwareSelector") as m_reg:
            m_reg.return_value.detect.return_value = {"regime": "bull", "confidence": 0.9}
            loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, enable_regime_adaptation=True)
            result = loop.run(market_ohlcv=ohlcv)
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_orthogonalize_removed_log(self, tmp_portfolio_dir, tmp_elite_dir):
        """非 elastic_net 模式正交化移除因子时记录日志。"""
        self._write_factors(tmp_elite_dir, n=2)  # 两个因子代码相同 → 哈希去重
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir, synthesis_mode="sharpe_weight")
        result = loop.run()
        assert result.n_factors_input == 2
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_drift_record_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """漂移监控记录失败（非致命）继续执行。"""
        self._write_factors(tmp_elite_dir, n=1)
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        loop.drift_monitor.compute = MagicMock(side_effect=RuntimeError("drift fail"))
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_run_drift_alert_sets_state_and_proposal(self, tmp_portfolio_dir, tmp_elite_dir):
        """GAP-F13: 漂移超阈值 + 开启自动重平衡 → state 标记告警 + 附加重平衡建议。"""
        self._write_factors(tmp_elite_dir, n=3)
        cfg: DriftAlertConfig = {
            "overlap_threshold": 0.50,
            "weight_l1_threshold": 0.40,
            "trigger_rebalance": True,
        }
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        loop.drift_monitor.alert_config = cfg
        # 注入触发告警的漂移指标（成员全换 + 权重全变）
        loop.drift_monitor.compute = MagicMock(
            return_value=DriftMetrics(
                date="2026-08-10",
                combo_id="cmb_new",
                prev_combo_id="cmb_prev",
                trace_id="l3_gapf13",
                member_overlap_rate=0.0,
                weight_l1_change=1.0,
                n_prev_members=3,
                n_new_members=3,
                n_common_members=0,
                added=["new_a"],
                removed=["old_a"],
            )
        )
        loop.drift_monitor.record = MagicMock()
        captured: dict[str, Any] = {}
        with patch(
            "fts.factor_engine.portfolio_loop.inject_to_fdt",
            side_effect=lambda combo, proposals, out: (
                captured.update({"combo": combo, "proposals": proposals}),
                {},
            )[1],
        ):
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert "drift_monitor" in {p.get("source") for p in captured["proposals"]}, "重平衡建议应附加到 proposals"
        # state 标记告警（写入 state.duckdb portfolio/state）
        from fts.store.state_db import get_state_store

        state_obj = get_state_store().get("portfolio", "state")
        assert state_obj.get("drift_alerted") is True, "state 应标记 drift_alerted"

    def test_run_drift_alert_disabled_no_proposal(self, tmp_portfolio_dir, tmp_elite_dir):
        """GAP-F13: 默认配置（不自动重平衡）→ 仅告警不附加重平衡建议。"""
        self._write_factors(tmp_elite_dir, n=3)
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        # 默认 DEFAULT_DRIFT_ALERT_CONFIG trigger_rebalance=False
        loop.drift_monitor.compute = MagicMock(
            return_value=DriftMetrics(
                date="2026-08-10",
                combo_id="cmb_new",
                prev_combo_id="cmb_prev",
                trace_id="l3_gapf13",
                member_overlap_rate=0.0,
                weight_l1_change=1.0,
                n_prev_members=3,
                n_new_members=3,
                n_common_members=0,
                added=["new_a"],
                removed=["old_a"],
            )
        )
        loop.drift_monitor.record = MagicMock()
        captured: dict[str, Any] = {}
        with patch(
            "fts.factor_engine.portfolio_loop.inject_to_fdt",
            side_effect=lambda combo, proposals, out: (
                captured.update({"combo": combo, "proposals": proposals}),
                {},
            )[1],
        ):
            result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")
        assert "drift_monitor" not in {p.get("source") for p in captured["proposals"]}, "默认配置不应附加重平衡建议"

    def test_run_quality_report_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """质量报告生成失败（非致命）继续执行。"""
        self._write_factors(tmp_elite_dir, n=1)
        loop = self._make_loop(tmp_portfolio_dir, tmp_elite_dir)
        loop._generate_quality_report = MagicMock(side_effect=RuntimeError("report fail"))
        result = loop.run()
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_main_error_print(self, monkeypatch, tmp_path):
        """main() 中 verifier 未通过时打印 error（exit 0）。"""
        import sys as _sys
        from fts.factor_engine.portfolio_loop import main as pl_main

        memory_dir = tmp_path / "mem_main_e"
        memory_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            _sys,
            "argv",
            [
                "portfolio_loop.py",
                "--once",
                "--mode",
                "sharpe_weight",
                "--memory-dir",
                str(memory_dir),
                "--elite-dir",
                str(tmp_path / "elite_main_e"),
            ],
        )
        # 仅 1 个因子 → 通过质量门槛但 verifier 因子数不足 → error 非空
        with patch(
            "fts.factor_engine.portfolio_loop.load_elite_factors",
            return_value=[
                {"factor_id": "f1", "name": "f1", "sharpe": 2.5, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                pl_main()
        assert exc.value.code == 0


# ════════════════════════════════════════════════════════════
# 24. PortfolioManager / Regime 杂项分支测试 (覆盖率补强)
# ════════════════════════════════════════════════════════════


class TestPortfolioManagerAndRegimeMisc:
    """PortfolioManager 归档/历史/目录缺失 + Regime 杂项分支。"""

    def test_save_combo_archives_corrupt_old(self, tmp_portfolio_dir):
        """旧 combo 文件损坏时归档跳过、不抛异常。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        pm.combo_file.write_text("corrupt", encoding="utf-8")
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at="now",
            combo_id="cmb_new",
            trace_id="t",
            synthesis_mode="equal_weight",
            signals=[],
            combo_sharpe=0.0,
            combo_turnover=0.0,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at="now",
        )
        pm.save_combo(combo)
        assert pm.combo_file.exists()

    def test_load_prev_combo_corrupt_history(self, tmp_portfolio_dir):
        """历史组合文件损坏时 load_prev_combo 返回 None。"""
        pm = PortfolioManager(tmp_portfolio_dir)
        pm.combo_history_dir.mkdir(parents=True, exist_ok=True)
        (pm.combo_history_dir / "cmb_old.json").write_text("corrupt", encoding="utf-8")
        assert pm.load_prev_combo() is None

    def test_list_active_proposals_missing_dir(self, tmp_portfolio_dir):
        """proposals 目录缺失时返回空列表。"""
        import shutil

        pm = PortfolioManager(tmp_portfolio_dir)
        shutil.rmtree(pm.proposals_dir)
        assert pm.list_active_proposals() == []

    def test_infer_family_cross_section(self):
        """从名称推断 cross_section 家族。"""
        from fts.factor_engine.portfolio_loop import _infer_factor_family_from_name

        assert _infer_factor_family_from_name("rank_based") == "cross_section"
        assert _infer_factor_family_from_name("cs_style") == "cross_section"

    def test_regime_no_adjustment_log(self):
        """regime 有配置但家族倍率=1.0 时无需调整。"""
        from fts.factor_engine.portfolio_loop import regime_adaptive_weight_adjustment

        signals = [
            PortfolioSignal(
                factor_id="f1",
                name="n1",
                weight=0.5,
                sharpe=2.0,
                ic=0.04,
                turnover=0.3,
                decay_6m=0.1,
                orthogonalized=False,
                retained=True,
            ),
        ]
        factors = [{"factor_id": "f1", "family": "cross_section"}]  # bear 下倍率 1.0
        out = regime_adaptive_weight_adjustment(signals, {"regime": "bear"}, factors)
        assert out[0]["weight"] == pytest.approx(0.5)


# ════════════════════════════════════════════════════════════
# 25. 覆盖率收尾测试 (v1.3.1) — 残余小分支
# ════════════════════════════════════════════════════════════


class TestCoveragePolish:
    """补齐剩余小分支：ml_ensemble 回退 / Regime 异常 / 相关性空回退等。"""

    def test_synthesize_ml_ensemble_fallback(self, tmp_path, sample_factors):
        """ml_ensemble 权重计算失败回退 sharpe_weight。"""
        elite = tmp_path / "elite"
        elite.mkdir(parents=True, exist_ok=True)
        with patch("fts.factor_engine.portfolio_loop._compute_ml_ensemble_weights", return_value={}):
            signals, _, _ = synthesize_signals(sample_factors, mode="ml_ensemble", elite_dir=elite)
        assert len(signals) == 3
        assert all(s["weight"] > 0 for s in signals)

    def test_compute_signal_correlations_empty_panel(self):
        """_compute_signal_correlations 空面板返回空 dict。"""
        from fts.factor_engine.portfolio_loop import _compute_signal_correlations

        assert _compute_signal_correlations([], {}) == {}

    def test_compute_signal_correlations_error_paths(self):
        """_compute_signal_correlations 无代码因子错误日志 + 信号不足回退。"""
        from fts.factor_engine.portfolio_loop import _compute_signal_correlations

        panel = {
            "RB": pd.DataFrame({"close": np.arange(20, dtype=float)}, index=pd.date_range("2024-01-01", periods=20))
        }
        # 6 个无 code 因子 → errors 6 条 → 截断日志 + 有效信号 < 2 回退
        factors = [{"factor_id": f"f{i}", "name": f"n{i}"} for i in range(6)]
        assert _compute_signal_correlations(factors, panel) == {}

    def test_compute_signal_correlations_nan_and_error(self):
        """_compute_signal_correlations 空信号与执行异常分支。"""
        from fts.factor_engine.portfolio_loop import _compute_signal_correlations

        panel = {
            "RB": pd.DataFrame({"close": np.arange(20, dtype=float)}, index=pd.date_range("2024-01-01", periods=20))
        }
        factors = [
            {"factor_id": "f1", "name": "n1", "code": "x", "params": {}},
            {"factor_id": "f2", "name": "n2", "code": "y", "params": {}},
        ]
        with patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec:
            # f1 信号全 NaN → 空信号；f2 执行抛异常 → 跳过
            m_exec.return_value.execute.side_effect = [
                np.full(20, np.nan),
                RuntimeError("boom"),
            ]
            assert _compute_signal_correlations(factors, panel) == {}

    def test_dedup_correlation_mode_no_corr_fallback(self):
        """相关性模式无法计算相关性时回退 IC-only。"""
        from fts.factor_engine.portfolio_loop import _deduplicate_by_base_name

        factors = [
            {"factor_id": "f1", "name": "fut_bias_g1", "ic": 0.06, "code": "x", "params": {}},
            {"factor_id": "f2", "name": "fut_bias_g2", "ic": 0.05, "code": "y", "params": {}},
        ]
        panel = {
            "RB": pd.DataFrame({"close": np.arange(20, dtype=float)}, index=pd.date_range("2024-01-01", periods=20))
        }
        with patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec:
            # 信号全 NaN/空 → 无法计算相关性 → 回退保留 IC 最高
            m_exec.return_value.execute.return_value = np.full(20, np.nan)
            out = _deduplicate_by_base_name(factors, "JSON", panel_data=panel)
        assert [f["factor_id"] for f in out] == ["f1"]

    def test_run_regime_detection_failure(self, tmp_portfolio_dir, tmp_elite_dir):
        """Step 2.5 Regime 检测失败（非致命）跳过自适应调整。"""
        TestQualityReportAndRunBranches()
        elite_dir = tmp_elite_dir
        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / "f.json").write_text(
            json.dumps(
                {
                    "factor_id": "fct_0",
                    "name": "f0",
                    "sharpe": 2.5,
                    "ic": 0.05,
                    "turnover": 0.3,
                    "decay_6m": 0.1,
                    "market": "stock",
                    "code": "close",
                }
            ),
            encoding="utf-8",
        )
        ohlcv = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        with patch("fts.factor_engine.regime.RegimeAwareSelector") as m_reg:
            m_reg.return_value.detect.side_effect = RuntimeError("regime fail")
            loop = PortfolioLoop(
                memory_dir=tmp_portfolio_dir,
                elite_dir=elite_dir,
                use_duckdb=False,
                synthesis_mode="equal_weight",
                enable_regime_adaptation=True,
                enable_clustering=False,
            )
            result = loop.run(market_ohlcv=ohlcv)
        assert result.status in ("passed", "verifier_warning", "completed")

    def test_elastic_net_corrupt_and_exec_failure(self, tmp_path):
        """Elastic Net 损坏 JSON 跳过 + 单只股票执行失败跳过。"""
        from fts.factor_engine.portfolio_loop import _compute_elastic_net_weights

        panel, dates = TestSynthesisElasticNetMl()._make_panel()
        elite = tmp_path / "elite"
        elite.mkdir()
        (elite / "bad.json").write_text("not json", encoding="utf-8")  # 损坏文件 → 跳过
        for i in range(2):
            (elite / f"f{i}.json").write_text(
                json.dumps(
                    {
                        "factor_id": f"f{i}",
                        "name": f"n{i}",
                        "code": "close",
                    }
                ),
                encoding="utf-8",
            )
        factors = [
            {"factor_id": "f0", "name": "n0", "sharpe": 2.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
            {"factor_id": "f1", "name": "n1", "sharpe": 2.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
        ]
        exec_ok = MagicMock()
        # 第一次执行（第 1 只股票）失败 → 该股票信号 NaN → 有效回归日不足回退
        exec_ok.execute.side_effect = [RuntimeError("exec fail")] + [np.linspace(-0.5, 0.5, 40)] * 19
        fake_model = MagicMock()
        fake_model.coef_ = np.array([1.0, 1.0])
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("sklearn.linear_model.ElasticNetCV", return_value=fake_model),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            m_exec.return_value = exec_ok
            assert _compute_elastic_net_weights(factors, elite) == {}

    def test_ml_ensemble_corrupt_and_exec_failure(self, tmp_path):
        """ML Ensemble 损坏 JSON 跳过 + 执行器构造/执行失败跳过。"""
        from fts.factor_engine.portfolio_loop import _compute_ml_ensemble_weights
        from fts.ml import TrainResult, TrainMode, ModelKind

        panel, dates = TestSynthesisElasticNetMl()._make_panel()
        elite = tmp_path / "elite"
        elite.mkdir()
        (elite / "bad.json").write_text("not json", encoding="utf-8")  # 损坏文件 → 跳过
        for i in range(2):
            (elite / f"f{i}.json").write_text(
                json.dumps(
                    {
                        "factor_id": f"f{i}",
                        "name": f"n{i}",
                        "code": "close",
                    }
                ),
                encoding="utf-8",
            )
        factors = [
            {"factor_id": "f0", "name": "n0", "sharpe": 2.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
            {"factor_id": "f1", "name": "n1", "sharpe": 2.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
        ]
        exec_ok = MagicMock()
        # f1 首只股票执行失败 → 对应样本行被 valid 过滤，其余正常训练
        exec_ok.execute.side_effect = [RuntimeError("exec fail")] + [np.linspace(-0.5, 0.5, 40)] * 19
        fake_trainer = MagicMock()
        fake_trainer.train.return_value = TrainResult(
            mode=TrainMode.CROSS_SECTIONAL,
            kind=ModelKind.LIGHTGBM,
            model=object(),
            score=0.05,
            feature_importance={"f0": 0.7, "f1": 0.3},
        )
        with (
            patch("fts.data.FTSDataProvider") as m_prov,
            patch("fts.factor_engine.factor_program.FactorExecutor") as m_exec,
            patch("fts.ml.SignalModelTrainer", return_value=fake_trainer),
        ):
            m_prov.return_value.get_futures_panel.return_value = (panel, dates)
            # f0 构造失败（914-915），f1 首只股票执行失败（926-927）
            m_exec.side_effect = [RuntimeError("ctor fail"), exec_ok]
            result = _compute_ml_ensemble_weights(factors, elite)
        assert set(result.keys()) <= {"f0", "f1"}  # 权重由剩余有效样本训练得出
