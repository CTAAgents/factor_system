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

import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import (
    EVOLUTION_VERSION,
    DEFAULT_L3_VERIFIER_CONFIG,
    AgentOptimizationProposal,
    FactorCorrelation,
    L3MetaLoopState,
    L3VerifierConfig,
    PortfolioCombo,
    PortfolioSignal,
)
from fts.factor_engine.portfolio_loop import (
    L3Error,
    L3Verifier,
    PortfolioStateManager,
    PortfolioManager,
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
        assert loaded["version"] == EVOLUTION_VERSION

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
        """版本号不匹配抛 L3Error。"""
        psm = PortfolioStateManager(tmp_portfolio_dir)
        state = psm.load_or_init()
        state["version"] = "0.0.0"  # 篡改版本

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
        """line 181: _try_load 发现版本不匹配返回 None。"""
        # 写入版本不匹配的 state
        state_file = tmp_portfolio_dir / "state.json"
        state_file.write_text(
            json.dumps({"version": "0.0.0", "status": "completed"}),
            encoding="utf-8",
        )
        psm = PortfolioStateManager(tmp_portfolio_dir)
        # load_or_init 应重新初始化（因为 _try_load 返回 None）
        state = psm.load_or_init()
        assert state["version"] == EVOLUTION_VERSION

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
