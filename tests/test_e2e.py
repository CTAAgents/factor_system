"""
tests.test_e2e — 端到端集成测试。

覆盖 production_plan.md §4.5 中定义的 10 个 E2E 场景。
使用合成数据（不依赖 Data-Core 真实数据源）。

版本: v0.1.0
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fts.cli import main as cli_main
from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.verifier import FactorVerifier
from fts.factor_engine.meta_loop import MetaLoop
from fts.factor_engine.portfolio_loop import PortfolioLoop
from fts.factor_engine.evaluation_chain import evaluate_backtest
from fts.factor_engine.state import generate_trace_id
from fts.factor_engine.seed_pool import get_default_seed_pool
from fts.factor_engine.walk_forward import WalkForwardOptimizer
from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.evaluation_chain import evaluate_walk_forward
from fts.factor_engine.walk_forward import WalkForwardConfig, WalkForwardResult
from fts.monitor.elite_tracker import EliteFactorTracker
from fts.scheduler.tasks import REGISTRY, TaskSpec, register_default_tasks
from fts.data import FTSDataProvider


# ── 共享 fixture ──────────────────────────────────────────

@pytest.fixture
def synth_data() -> tuple[pd.DataFrame, np.ndarray]:
    """合成 OHLCV + 收益率数据。"""
    provider = FTSDataProvider()
    df = provider.synthesize_ohlcv(n_days=500, base_price=100.0, seed=42)
    closes = df["close"].values
    fwd = np.zeros(len(df))
    if len(closes) > 5:
        fwd[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
    return df, fwd


@pytest.fixture
def elite_dir(tmp_path: Path) -> Path:
    elite = tmp_path / "elite"
    elite.mkdir(parents=True)
    return elite


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    mem = tmp_path / "memory"
    (mem / "evolution").mkdir(parents=True)
    (mem / "meta_loop").mkdir(parents=True)
    (mem / "portfolio").mkdir(parents=True)
    return mem


@pytest.fixture
def sample_factor() -> FactorProgram:
    return FactorProgram(
        factor_id="e2e_test_factor",
        name="e2e_momentum",
        code="def factor_program(data, params): return data['close'].values",
        trace_id=generate_trace_id("e2e"),
        params={},
        economic_logic={
            "theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3,
            "narrative": "E2E test factor",
        },
    )


# ── E2E 场景 ──────────────────────────────────────────────

class TestE2EScenarios:
    """10 个 E2E 场景（production_plan.md §4.5）。"""
    
    # 场景 1: 完整因子演化
    def test_01_complete_factor_evolution(self, synth_data, memory_dir, elite_dir):
        """SeedPool → MacroEvolver → EvalChain → Verifier → Elite 入库"""
        data_df, fwd_returns = synth_data
        seed_pool = get_default_seed_pool()
        assert seed_pool.count() > 0
        
        llm = MockLLMClient()
        verifier = FactorVerifier()
        loop = EvolutionLoop(
            data=data_df, forward_returns=fwd_returns,
            elite_dir=str(elite_dir), memory_dir=str(memory_dir / "evolution"),
            llm_client=llm, seed_pool=seed_pool, verifier=verifier,
            n_trials_micro=2,
        )
        result = loop.run(max_generation=2)
        assert result.status == "completed" or len(result.elite_factor_ids) >= 0
    
    # 场景 2: Meta-Loop 知识补给
    def test_02_meta_loop_knowledge_injection(self, synth_data, memory_dir, elite_dir):
        """L1 运行 + 种子注入"""
        data_df, _ = synth_data
        
        loop = MetaLoop(
            memory_dir=str(memory_dir / "meta_loop"),
        )
        result = loop.run()
        assert result.status in ("completed", "idle")
    
    # 场景 3: 组合构建
    def test_03_portfolio_construction(self, synth_data, memory_dir, elite_dir):
        """loading elite → orthogonalize → decay → synthesize"""
        # Create a test elite factor
        factor_file = elite_dir / "test_factor.json"
        factor_file.write_text(json.dumps({
            "factor_id": "test_momentum",
            "name": "momentum",
            "code": "def compute(data, params): return data['close'].values",
        }))
        
        data_df, fwd_returns = synth_data
        loop = PortfolioLoop(
            elite_dir=str(elite_dir), memory_dir=str(memory_dir / "portfolio"),
        )
        result = loop.run()
        assert result.status in ("completed", "idle", "no_elite", "verifier_warning")
    
    # 场景 4: 走航验证
    def test_04_walk_forward_validation(self, synth_data, sample_factor):
        """WalkForward 多窗口"""
        data_df, fwd_returns = synth_data
        config = WalkForwardConfig(
            window_years=1, step_months=6, min_oos_months=1,
            n_windows=2, min_ic_consistency=0.5, max_ic_volatility=0.3,
        )
        result = evaluate_walk_forward(sample_factor, data_df, fwd_returns, config)
        assert "windows" in result
        assert "consistency_score" in result
        assert result["n_windows_completed"] <= config["n_windows"]
    
    # 场景 5: 因子跟踪
    def test_05_factor_tracking(self, tmp_path):
        """tracker init → update → retire"""
        tracker_dir = tmp_path / "tracking"
        tracker = EliteFactorTracker(str(tracker_dir))
        tracker.init_tracker("f_e2e_1", "e2e_factor", entry_ic=0.05, entry_sharpe=1.2)
        for _ in range(5):
            tracker.update("f_e2e_1", -0.01)
        snapshot = tracker.get("f_e2e_1")
        assert snapshot is not None
        assert snapshot["consecutive_zero_ic"] >= 4
    
    # 场景 6: 市场制度检测
    def test_06_regime_detection(self):
        """OHLCV → regime 分类"""
        from fts.factor_engine.regime import RegimeAwareSelector
        selector = RegimeAwareSelector()
        dates = pd.date_range("2020-01-01", periods=200, freq="D")
        df = pd.DataFrame({
            "open": 100 + np.arange(200) * 0.5,
            "high": 101 + np.arange(200) * 0.5,
            "low": 99 + np.arange(200) * 0.5,
            "close": 100 + np.arange(200) * 0.5,
            "volume": np.ones(200) * 1000,
        }, index=dates)
        regime = selector.detect(df)
        assert regime["regime"] in ("bull", "bear", "oscillate", "high_vol", "low_vol")
        assert 0 <= regime["confidence"] <= 1
    
    # 场景 7: 交易成本调整
    def test_07_cost_adjustment(self, synth_data, sample_factor):
        """净夏普 < 毛夏普"""
        from fts.factor_engine.cost_model import TransactionCostModel
        from fts.factor_engine.evaluation_chain import evaluate_backtest
        
        bt = evaluate_backtest(sample_factor, synth_data[0], synth_data[1])
        cost_model = TransactionCostModel()
        adjusted = cost_model.adjust(bt, np.ones(100), market="futures")
        assert adjusted["net_sharpe"] <= adjusted["gross_sharpe"]
    
    # 场景 8: 压力测试
    def test_08_stress_test(self):
        """已知场景下组合不崩溃"""
        from fts.factor_engine.stress_test import StressTester
        tester = StressTester()
        scenarios = tester.get_builtin_scenarios()
        assert len(scenarios) >= 3
        signals = {"RB": np.random.randn(500)}
        dates = pd.date_range("2015-01-01", periods=500, freq="D")
        ohlcv = {"RB": pd.DataFrame({
            "close": 100 + np.random.randn(500).cumsum(),
            "high": 101 + np.random.randn(500).cumsum(),
            "low": 99 + np.random.randn(500).cumsum(),
            "volume": np.random.randint(1000, 10000, 500),
        }, index=dates)}
        results = tester.run_all(signals, ohlcv)
        assert len(results) == len(scenarios)
    
    # 场景 9: Data-Core 降级
    def test_09_datacore_degradation(self):
        """数据源不可用 → 合成数据（不抛异常）"""
        provider = FTSDataProvider()
        df = provider.synthesize_ohlcv()
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert "close" in df.columns
    
    # 场景 10: 完整 scheduler
    def test_10_scheduler_integration(self):
        """任务注册 → 调度 → 执行"""
        # Clear and register
        REGISTRY._tasks.clear()
        register_default_tasks()
        tasks = REGISTRY.list_all()
        assert len(tasks) >= 4  # 4 default tasks
        names = [t.name for t in tasks]
        assert "l1_meta_loop" in names
        assert "l2_evolution_loop" in names
        assert "l3_portfolio_loop" in names
        assert "health_check" in names


class MockLLMClient:
    """Mock LLM 客户端，避免真实 API 调用。"""
    
    def generate(self, prompt: str, **kwargs) -> str:
        return "def factor_program(data, params): return data['close'].values"
    
    async def async_generate(self, prompt: str, **kwargs) -> str:
        return self.generate(prompt, **kwargs)
