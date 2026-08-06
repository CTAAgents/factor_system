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
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

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
    FactorCorrelation,
    L3MetaLoopState,
    L3VerifierConfig,
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


# ─── 共享 fixtures ────────────────────────────────────────

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
            factor_id="fct_001", name="momentum", weight=0.5,
            sharpe=2.5, ic=0.05, turnover=0.3, decay_6m=0.1,
            orthogonalized=False, retained=True,
        ),
        PortfolioSignal(
            factor_id="fct_002", name="reversion", weight=0.3,
            sharpe=2.0, ic=0.04, turnover=0.4, decay_6m=0.2,
            orthogonalized=False, retained=True,
        ),
        PortfolioSignal(
            factor_id="fct_003", name="volatility", weight=0.2,
            sharpe=1.8, ic=0.03, turnover=0.2, decay_6m=0.15,
            orthogonalized=False, retained=True,
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

    def make_combo(self, sharpe: float = 2.5, corr: float = 0.2,
                   turnover: float = 0.3, signals: list | None = None,
                   n_factors: int = 3) -> PortfolioCombo:
        """快速构建组合 fixture。"""
        if signals is None:
            signals = [
                PortfolioSignal(
                    factor_id=f"fct_{i}", name=f"f{i}", weight=1.0/n_factors,
                    sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                    orthogonalized=True, retained=True,
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
        """相关性 0.5 > 0.3 应失败。"""
        v = L3Verifier(DEFAULT_L3_VERIFIER_CONFIG)
        combo = self.make_combo(sharpe=2.5, corr=0.5, turnover=0.3)
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("相关性" in r for r in reasons)
        assert any("0.50" in r for r in reasons)

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
    """L3 状态管理器 — 持久化 + backup 恢复。"""

    def test_init_creates_file(self, tmp_portfolio_dir):
        """load_or_init 后 state.json 存在。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        psm.load_or_init()
        assert psm.state_file.exists()

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

    def test_backup_recovery(self, tmp_portfolio_dir):
        """主文件损坏后从 backup 恢复。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()
        state["total_signals_processed"] = 7
        state["total_proposals_generated"] = 3
        psm.save(state)

        # 损坏主文件
        psm.state_file.write_text("invalid json content", encoding="utf-8")

        # 重新加载应从 backup 恢复
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
            proposal_id="prop_draft", trace_id="t", agent_name="闫判官",
            created_at="2026-07-18T00:00:00", current_prompt_summary="p",
            suggested_changes="c", debate_round_ref=None, rationale="r",
            priority="medium", status="draft",
        )
        pm.save_proposal(draft)
        # 保存一个已应用的
        applied = AgentOptimizationProposal(
            proposal_id="prop_applied", trace_id="t", agent_name="闫判官",
            created_at="2026-07-18T00:00:00", current_prompt_summary="p",
            suggested_changes="c", debate_round_ref=None, rationale="r",
            priority="medium", status="applied",
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
        """Sharpe > 3.0 的因子按 3.0 计算权重（P0 过拟合修复）。"""
        factors = [
            {"factor_id": "fct_a", "name": "factor_a", "sharpe": 5.0, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1},
            {"factor_id": "fct_b", "name": "factor_b", "sharpe": 2.0, "ic": 0.04, "turnover": 0.4, "decay_6m": 0.2},
        ]
        signals, _, _ = synthesize_signals(factors, mode="sharpe_weight")
        # factor_a sharpe 5.0 被截断为 3.0, factor_b 保持 2.0
        total = 3.0 + 2.0
        assert signals[0]["sharpe"] == 3.0  # 被截断
        assert signals[0]["weight"] == pytest.approx(3.0 / total)
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
# 5. Orthogonalize 测试
# ════════════════════════════════════════════════════════════

class TestOrthogonalize:
    """因子正交化 — 高相关性剔除。"""

    def make_signals(self) -> list[PortfolioSignal]:
        """3 个信号，夏普依次递减。"""
        return [
            PortfolioSignal(
                factor_id="fct_high", name="high_sharpe", weight=0.4,
                sharpe=3.0, ic=0.06, turnover=0.2, decay_6m=0.1,
                orthogonalized=False, retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_mid", name="mid_sharpe", weight=0.3,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.15,
                orthogonalized=False, retained=True,
            ),
            PortfolioSignal(
                factor_id="fct_low", name="low_sharpe", weight=0.3,
                sharpe=1.5, ic=0.03, turnover=0.4, decay_6m=0.2,
                orthogonalized=False, retained=True,
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
                factor_id_a="fct_high", factor_id_b="fct_low",
                pearson=0.85, spearman=0.80,
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
                factor_id="fct_d1", name="decayed", weight=0.5,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.5,
                orthogonalized=True, retained=True,
            ),
        ]
        result = decay_test(signals, max_decay_rate=0.30)
        assert result[0]["retained"] is False

    def test_low_decay_retained(self):
        """衰减 <= 0.3 的因子 retained=True。"""
        signals = [
            PortfolioSignal(
                factor_id="fct_d2", name="stable", weight=0.5,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.2,
                orthogonalized=True, retained=True,
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
                factor_id="f_a", name="a", weight=5.0,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b", name="b", weight=5.0,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
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
                factor_id="f_a", name="a", weight=0.9,
                sharpe=3.0, ic=0.06, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b", name="b", weight=0.1,
                sharpe=1.0, ic=0.02, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
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
        f1.write_text(json.dumps({
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
        }), encoding="utf-8")

        f2 = tmp_elite_dir / "factor_beta.json"
        f2.write_text(json.dumps({
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
        }), encoding="utf-8")

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
        factor_file.write_text(json.dumps({
            "factor_id": "fct_mock",
            "name": "mock_momentum",
            "sharpe": 2.5,
            "ic": 0.05,
            "turnover": 0.3,
            "decay_6m": 0.1,
        }), encoding="utf-8")

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
        factor_file.write_text(json.dumps({
            "factor_id": "fct_mock", "name": "mock_momentum",
            "sharpe": 2.5, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")

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
                factor_id="fct_d", name="decayed", weight=1.0,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.5,
                orthogonalized=True, retained=True,
            ),
        ]
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION, updated_at="now",
            combo_id="cmb_test", trace_id="l3_test",
            synthesis_mode="equal_weight", signals=signals,
            combo_sharpe=2.5, combo_turnover=0.3, max_correlation=0.2,
            n_factors=1, status="pending", created_at="now",
        )
        passed, reasons = v.check(combo)
        assert passed is False
        assert any("衰减率" in r for r in reasons)

    # ── PortfolioStateManager lines 154-155 ──

    def test_state_manager_backup_oserror(self, tmp_portfolio_dir, monkeypatch):
        """lines 154-155: backup 备份失败应抛 L3Error。"""
        import shutil
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()

        def broken_copy2(*args, **kwargs):
            raise OSError("备份失败")

        monkeypatch.setattr(shutil, "copy2", broken_copy2)
        with pytest.raises(L3Error, match="备份失败"):
            psm.save(state)

    # ── PortfolioStateManager line 181 ──

    def test_state_manager_try_load_version_mismatch(self, tmp_portfolio_dir):
        """line 181: _try_load 发现 schema 版本不匹配返回 None。"""
        # 写入 schema 版本不匹配的 state
        state_file = tmp_portfolio_dir / "state.json"
        state_file.write_text(
            json.dumps({"schema_version": "0", "status": "completed"}),
            encoding="utf-8",
        )
        psm = PortfolioStateManager(tmp_portfolio_dir)
        # load_or_init 应重新初始化（因为 _try_load 返回 None）
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
            proposal_id="prop_good", trace_id="t", agent_name="闫判官",
            created_at="now", current_prompt_summary="p",
            suggested_changes="c", debate_round_ref=None, rationale="r",
            priority="medium", status="draft",
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
        good.write_text(json.dumps({
            "factor_id": "fct_good", "name": "good", "sharpe": 2.0,
            "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")
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
        (elite_dir / "test.json").write_text(json.dumps({
            "factor_id": "fct_main", "name": "main", "sharpe": 2.5,
            "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")
        memory_dir = tmp_path / "portfolio"
        memory_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            sys, "argv",
            [
                "portfolio_loop.py", "--once",
                "--mode", "equal_weight",
                "--memory-dir", str(memory_dir),
                "--elite-dir", str(elite_dir),
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
        (elite_dir / "bad.json").write_text(json.dumps({
            "factor_id": "fct_bad", "name": "bad", "sharpe": 1.0,
            "ic": 0.01, "turnover": 0.8, "decay_6m": 0.5,
        }), encoding="utf-8")
        memory_dir = tmp_path / "portfolio_warn"
        memory_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            sys, "argv",
            [
                "portfolio_loop.py", "--once",
                "--memory-dir", str(memory_dir),
                "--elite-dir", str(elite_dir),
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
            sys, "argv",
            [
                "portfolio_loop.py", "--once",
                "--memory-dir", str(memory_dir),
                "--elite-dir", str(elite_dir),
            ],
        )
        with patch.object(pl_mod, "__name__", "__main__"):
            with pytest.raises(SystemExit):
                exec("from fts.factor_engine.portfolio_loop import main; main()",
                     {"__name__": "__main__"})

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
            PortfolioSignal(factor_id="f_a", name="a", weight=0.5, sharpe=2.0,
                            ic=0.04, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
            PortfolioSignal(factor_id="f_b", name="b", weight=0.5, sharpe=1.5,
                            ic=0.03, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_few_signals_skips_test(self):
        """因子数 < 3 时跳过随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test
        signals = [
            PortfolioSignal(factor_id="f_a", name="a", weight=1.0, sharpe=3.0,
                            ic=0.06, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_zero_total_weight_skips(self):
        """总权重为 0 时跳过随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test
        signals = [
            PortfolioSignal(factor_id="f_a", name="a", weight=0.0, sharpe=3.0,
                            ic=0.06, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
        ]
        assert _run_sharpe_randomization_test(signals, n_shuffle=10) is True

    def test_high_sharpe_with_retained_only(self):
        """只使用 retained=True 的信号计算随机化测试。"""
        from fts.factor_engine.portfolio_loop import _run_sharpe_randomization_test
        signals = [
            PortfolioSignal(factor_id="f_a", name="a", weight=0.4, sharpe=3.5,
                            ic=0.07, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
            PortfolioSignal(factor_id="f_b", name="b", weight=0.3, sharpe=3.0,
                            ic=0.06, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
            PortfolioSignal(factor_id="f_c", name="c", weight=0.3, sharpe=2.8,
                            ic=0.05, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=True),
            PortfolioSignal(factor_id="f_d", name="d", weight=0.0, sharpe=0.5,
                            ic=0.01, turnover=0.3, decay_6m=0.1,
                            orthogonalized=True, retained=False),
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
            {"factor_id": "fct_trend", "name": "momentum_trend", "sharpe": 2.5,
             "ic": 0.05, "turnover": 0.3, "decay_6m": 0.05, "family": "trend"},
            {"factor_id": "fct_reversion", "name": "mean_reversion", "sharpe": 2.0,
             "ic": 0.04, "turnover": 0.4, "decay_6m": 0.1, "family": "mean_reversion"},
            {"factor_id": "fct_vol", "name": "volatility_screener", "sharpe": 1.8,
             "ic": 0.03, "turnover": 0.2, "decay_6m": 0.08, "family": "volatility"},
            {"factor_id": "fct_carry", "name": "carry_spread", "sharpe": 1.5,
             "ic": 0.03, "turnover": 0.15, "decay_6m": 0.12, "family": "carry"},
        ]
        signals = [
            PortfolioSignal(factor_id="fct_trend", name="momentum_trend",
                            weight=0.25, sharpe=2.5, ic=0.05, turnover=0.3,
                            decay_6m=0.05, orthogonalized=False, retained=True),
            PortfolioSignal(factor_id="fct_reversion", name="mean_reversion",
                            weight=0.25, sharpe=2.0, ic=0.04, turnover=0.4,
                            decay_6m=0.1, orthogonalized=False, retained=True),
            PortfolioSignal(factor_id="fct_vol", name="volatility_screener",
                            weight=0.25, sharpe=1.8, ic=0.03, turnover=0.2,
                            decay_6m=0.08, orthogonalized=False, retained=True),
            PortfolioSignal(factor_id="fct_carry", name="carry_spread",
                            weight=0.25, sharpe=1.5, ic=0.03, turnover=0.15,
                            decay_6m=0.12, orthogonalized=False, retained=True),
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
            {"factor_id": "fct_decay", "name": "decaying_factor", "sharpe": 1.5,
             "ic": 0.02, "turnover": 0.5, "decay_6m": 0.25, "family": "trend"},
        ]
        signals = [
            PortfolioSignal(factor_id="fct_decay", name="decaying_factor",
                            weight=0.3, sharpe=1.5, ic=0.02, turnover=0.5,
                            decay_6m=0.25, orthogonalized=False, retained=True),
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
            {"factor_id": "fct_1", "name": "momentum_factor", "sharpe": 2.0,
             "ic": 0.04, "turnover": 0.3, "decay_6m": 0.1},
        ]
        signals = [
            PortfolioSignal(factor_id="fct_1", name="momentum_factor",
                            weight=0.5, sharpe=2.0, ic=0.04, turnover=0.3,
                            decay_6m=0.1, orthogonalized=False, retained=True),
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
        (tmp_elite_dir / "test.json").write_text(json.dumps({
            "factor_id": "fct_test", "name": "test_factor", "sharpe": 2.5,
            "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")

        # 构造市场数据（牛市趋势）
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = pd.DataFrame({
            "open": close * 1.001,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.random.randint(800, 1200, n).astype(float),
        }, index=dates)

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

        (tmp_elite_dir / "test.json").write_text(json.dumps({
            "factor_id": "fct_test", "name": "test_factor", "sharpe": 2.5,
            "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")

        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = pd.DataFrame({
            "open": close * 1.001, "high": close * 1.005, "low": close * 0.995,
            "close": close, "volume": np.random.randint(800, 1200, n).astype(float),
        }, index=dates)

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

        (tmp_elite_dir / "test.json").write_text(json.dumps({
            "factor_id": "fct_test", "name": "test_factor", "sharpe": 2.5,
            "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
        }), encoding="utf-8")

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
                factor_id=fid, name=fid, weight=w,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
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
                factor_id="f_a", name="a", weight=0.30,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
            ),
            PortfolioSignal(
                factor_id="f_b", name="b", weight=0.70,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
            ),
        ]
        prev_weights = {"f_a": 0.05, "f_b": 0.95}
        config = StickyConfig(enabled=True, max_delta=0.30, new_factor_cap=0.10)
        combo = build_combo(signals, mode="equal_weight", trace_id="l3_sticky",
                            prev_weights=prev_weights, sticky_config=config)
        w_map = {s["factor_id"]: s["weight"] for s in combo["signals"]}
        # f_a: 0.30 → clamp 上限 0.05 * 1.3 = 0.065
        # f_b: 0.70 在 [0.665, 1.235] 内，不变
        total = 0.065 + 0.70
        assert w_map["f_a"] == pytest.approx(0.065 / total)
        assert w_map["f_b"] == pytest.approx(0.70 / total)
        assert sum(w_map.values()) == pytest.approx(1.0)

    def test_portfolio_loop_sticky_default_enabled(self, tmp_portfolio_dir, tmp_elite_dir):
        """PortfolioLoop 未传 sticky_config 时默认启用（DEFAULT_STICKY_CONFIG）。"""
        from fts.factor_engine.contracts import DEFAULT_STICKY_CONFIG

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        assert loop.sticky_config is not None
        assert loop.sticky_config.get("enabled", True) is True
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

    def _make_combo(self, combo_id: str, weights: dict[str, float],
                    trace_id: str = "l3_drift") -> PortfolioCombo:
        signals = [
            PortfolioSignal(
                factor_id=fid, name=fid, weight=w,
                sharpe=2.0, ic=0.04, turnover=0.3, decay_6m=0.1,
                orthogonalized=True, retained=True,
            )
            for fid, w in weights.items()
        ]
        return PortfolioCombo(
            version=EVOLUTION_VERSION, updated_at="now",
            combo_id=combo_id, trace_id=trace_id,
            synthesis_mode="equal_weight", signals=signals,
            combo_sharpe=2.5, combo_turnover=0.3, max_correlation=0.2,
            n_factors=len(weights), status="active", created_at="now",
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
        from datetime import datetime, timedelta

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
        (tmp_elite_dir / "f_normal.json").write_text(json.dumps({
            "factor_id": "fct_normal", "name": "normal",
            "sharpe": 2.5, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
            "market": "stock",
        }), encoding="utf-8")
        # 影子因子（观察期内）
        (tmp_elite_dir / "f_shadow.json").write_text(json.dumps({
            "factor_id": "fct_shadow", "name": "shadowed",
            "sharpe": 2.5, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.1,
            "market": "stock",
            "shadow_pool": {
                "promoted_at": today.isoformat(),
                "observe_trading_days": 5,
                "observe_until": (today + timedelta(days=5)).isoformat(),
            },
        }), encoding="utf-8")

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
            data=pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0, 5.0]},
                              index=pd.date_range("2024-01-01", periods=5)),
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
        """_promote_to_elite 默认给新因子写入 shadow_pool 标记。"""
        loop, elite_dir = self._make_evolution_loop(tmp_path)

        factor = {
            "factor_id": "fct_shadow1", "name": "shadow_factor",
            "code": "code", "market": "futures", "family": "trend",
        }
        evaluation = {"level_1_backtest": {"sharpe": 2.0, "ic": 0.05}, "level_3_multiple": {"passed": True}, "passed": True}
        # 直接调用 _promote_to_elite（repo 已 mock）
        path = loop._promote_to_elite(factor, evaluation)
        assert path is not None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "shadow_pool" in data
        assert data["shadow_pool"]["observe_trading_days"] == 5
        assert "observe_until" in data["shadow_pool"]

    def test_promote_seed_skips_shadow_pool(self, tmp_path):
        """种子因子（shadow_observe=False）不写 shadow_pool。"""
        loop, elite_dir = self._make_evolution_loop(tmp_path)

        factor = {
            "factor_id": "fct_seed1", "name": "seed_factor",
            "code": "code", "market": "futures", "family": "trend",
        }
        evaluation = {"level_1_backtest": {"sharpe": 2.0, "ic": 0.05}, "level_3_multiple": {"passed": True}, "passed": True}
        path = loop._promote_to_elite(factor, evaluation, shadow_observe=False)
        assert path is not None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "shadow_pool" not in data
