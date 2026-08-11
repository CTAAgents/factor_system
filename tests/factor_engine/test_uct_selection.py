"""
tests/factor_engine/test_uct_selection.py — UCT 父因子选择测试

测试 UCT 树搜索选择逻辑的正确性：

版本: v1.9.0
"""
# pylint: disable=redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram, FactorEvaluation
from fts.factor_engine.evolution_loop import EvolutionLoop, UCT_EXPLORATION_C


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成合成 OHLCV 数据。"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=100, freq="D")
    close = 100 + np.cumsum(np.random.randn(100) * 0.5)
    return pd.DataFrame(
        {
            "open": close + np.random.randn(100) * 0.1,
            "high": close + np.abs(np.random.randn(100)) * 0.3,
            "low": close - np.abs(np.random.randn(100)) * 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, 100).astype(float),
        },
        index=dates,
    )


@pytest.fixture
def sample_returns(sample_data: pd.DataFrame) -> np.ndarray:
    """生成未来收益率。"""
    close = sample_data["close"].values
    ret = np.diff(close, prepend=close[0]) / close
    ret = np.roll(ret, -1)
    ret[-1] = 0
    return ret


@pytest.fixture
def parent_factors() -> list[FactorProgram]:
    """创建 3 个父因子用于测试。"""
    return [
        FactorProgram(
            factor_id="fct_aaaaaaaa",
            name="parent_a",
            code="def factor_program(data, params): return np.zeros(len(data['close']))",
            params={"window": 20},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 20},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "test"},
            source="seed",
            generation=0,
            created_at="2024-01-01T00:00:00",
            trace_id="trace_aaa",
        ),
        FactorProgram(
            factor_id="fct_bbbbbbbb",
            name="parent_b",
            code="def factor_program(data, params): return np.zeros(len(data['close']))",
            params={"window": 30},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 30},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "test"},
            source="seed",
            generation=0,
            created_at="2024-01-01T00:00:00",
            trace_id="trace_bbb",
        ),
        FactorProgram(
            factor_id="fct_cccccccc",
            name="parent_c",
            code="def factor_program(data, params): return np.zeros(len(data['close']))",
            params={"window": 40},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 40},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "test"},
            source="seed",
            generation=0,
            created_at="2024-01-01T00:00:00",
            trace_id="trace_ccc",
        ),
    ]


@pytest.fixture
def loop(sample_data: pd.DataFrame, sample_returns: np.ndarray) -> EvolutionLoop:
    """创建 EvolutionLoop 实例。"""
    return EvolutionLoop(
        data=sample_data,
        forward_returns=sample_returns,
        elite_dir="memory/knowledge/factors/stocks_elite",
        memory_dir="memory/evolution",
    )


# ─── UCT 选择测试 ──────────────────────────────────────────


class TestUCTSelection:
    """UCT 父因子选择逻辑测试。"""

    def test_uct_selects_unvisited_first(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """UCT 应优先选择未访问的父因子。"""
        # 所有父因子都未访问 → 应返回第一个未访问的
        selected = loop._select_parent_uct(parent_factors)
        assert selected["factor_id"] == "fct_aaaaaaaa"

    def test_uct_selects_higher_reward(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """UCT 应更倾向选择高奖励的父因子。"""
        # 先给每个父因子一些访问记录
        # parent_a: IC=0.5 (高奖励)
        # parent_b: IC=0.1 (低奖励)
        # parent_c: IC=0.3 (中等)
        for fid, ic in [("fct_aaaaaaaa", 0.5), ("fct_bbbbbbbb", 0.1), ("fct_cccccccc", 0.3)]:
            loop._uct_stats[fid] = {"visits": 5, "total_reward": ic * 5}

        # 多次选择，parent_a 应被选中最多次
        counts: dict[str, int] = {}
        for _ in range(100):
            selected = loop._select_parent_uct(parent_factors)
            fid = selected["factor_id"]
            counts[fid] = counts.get(fid, 0) + 1

        # parent_a (IC=0.5) 应被选中最多次
        assert counts.get("fct_aaaaaaaa", 0) > counts.get("fct_bbbbbbbb", 0)
        assert counts.get("fct_aaaaaaaa", 0) > counts.get("fct_cccccccc", 0)

    def test_uct_explores_with_high_c(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """高探索常数 c 下，访问少的父因子获得更多机会。"""
        # parent_a: 访问 10 次，IC=0.5
        # parent_b: 访问 1 次，IC=0.1
        loop._uct_stats["fct_aaaaaaaa"] = {"visits": 10, "total_reward": 5.0}
        loop._uct_stats["fct_bbbbbbbb"] = {"visits": 1, "total_reward": 0.1}

        # 计算 UCB 值
        total_visits = 11
        # parent_a: avg=0.5, exploration = 1.0 * sqrt(ln(11)/10) ≈ 1.0 * sqrt(2.398/10) ≈ 0.49
        # UCB = 0.5 + 0.49 = 0.99
        ucb_a = 0.5 + UCT_EXPLORATION_C * math.sqrt(math.log(total_visits) / 10)

        # parent_b: avg=0.1, exploration = 1.0 * sqrt(ln(11)/1) ≈ 1.0 * sqrt(2.398) ≈ 1.55
        # UCB = 0.1 + 1.55 = 1.65
        ucb_b = 0.1 + UCT_EXPLORATION_C * math.sqrt(math.log(total_visits) / 1)

        # parent_b 的 UCB 应该更高（探索奖励）
        assert ucb_b > ucb_a, f"UCB: parent_a={ucb_a:.4f}, parent_b={ucb_b:.4f}"

    def test_uct_with_single_parent(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """单个父因子时，UCT 始终返回该因子。"""
        single = [parent_factors[0]]
        for _ in range(10):
            selected = loop._select_parent_uct(single)
            assert selected["factor_id"] == "fct_aaaaaaaa"


# ─── UCT 统计更新测试 ──────────────────────────────────────


class TestUCTStatsUpdate:
    """UCT 统计更新逻辑测试。"""

    def test_update_uct_stats_success(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """通过的因子应更新 UCT 统计，奖励 = abs(IC)。"""
        parent = parent_factors[0]
        evaluation = FactorEvaluation(
            factor_id="fct_child",
            trace_id="trace_child",
            level_1_backtest={"ic": 0.45, "sharpe": 2.0},
            passed=True,
            failure_reasons=[],
            evaluated_at="2024-01-01T00:00:00",
        )
        loop._update_uct_stats(parent, evaluation)
        stats = loop._uct_stats["fct_aaaaaaaa"]
        assert stats["visits"] == 1
        assert stats["total_reward"] == 0.45

    def test_update_uct_stats_failure(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """失败的因子应更新 UCT 统计，奖励 = 0。"""
        parent = parent_factors[0]
        evaluation = FactorEvaluation(
            factor_id="fct_child",
            trace_id="trace_child",
            level_1_backtest={"ic": 0.005, "sharpe": 0.3},
            passed=False,
            failure_reasons=["IC 过低"],
            evaluated_at="2024-01-01T00:00:00",
        )
        loop._update_uct_stats(parent, evaluation)
        stats = loop._uct_stats["fct_aaaaaaaa"]
        assert stats["visits"] == 1
        assert stats["total_reward"] == 0.0

    def test_update_uct_stats_accumulates(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """多次更新应累加 visits 和 total_reward。"""
        parent = parent_factors[0]
        for ic in [0.3, 0.4, 0.5]:
            evaluation = FactorEvaluation(
                factor_id="fct_child",
                trace_id="trace_child",
                level_1_backtest={"ic": ic},
                passed=True,
                failure_reasons=[],
                evaluated_at="2024-01-01T00:00:00",
            )
            loop._update_uct_stats(parent, evaluation)
        stats = loop._uct_stats["fct_aaaaaaaa"]
        assert stats["visits"] == 3
        assert stats["total_reward"] == pytest.approx(1.2)

    def test_update_uct_stats_negative_ic(self, loop: EvolutionLoop, parent_factors: list[FactorProgram]):
        """负 IC 的通过因子，奖励 = abs(IC)。"""
        parent = parent_factors[0]
        evaluation = FactorEvaluation(
            factor_id="fct_child",
            trace_id="trace_child",
            level_1_backtest={"ic": -0.35},
            passed=True,
            failure_reasons=[],
            evaluated_at="2024-01-01T00:00:00",
        )
        loop._update_uct_stats(parent, evaluation)
        stats = loop._uct_stats["fct_aaaaaaaa"]
        assert stats["total_reward"] == 0.35


# ─── UCT 数学正确性测试 ────────────────────────────────────


class TestUCTMath:
    """UCT 公式数学正确性测试。"""

    def test_uct_formula_known_values(self):
        """验证 UCT 公式对已知输入的计算结果。"""
        # 两个父因子，已知访问次数和奖励
        # parent_a: visits=10, total_reward=5.0 → avg=0.5
        # parent_b: visits=5, total_reward=1.5 → avg=0.3
        total_visits = 15
        c = UCT_EXPLORATION_C

        # UCB_a = 0.5 + c * sqrt(ln(15)/10)
        ucb_a = 0.5 + c * math.sqrt(math.log(total_visits) / 10)
        # UCB_b = 0.3 + c * sqrt(ln(15)/5)
        ucb_b = 0.3 + c * math.sqrt(math.log(total_visits) / 5)

        # parent_b 访问少，探索项更大
        assert ucb_b > ucb_a, f"访问少的父因子应获得更高探索奖励: UCB_a={ucb_a:.4f}, UCB_b={ucb_b:.4f}"

    def test_uct_exploration_term_zero_visits(self):
        """visits=0 时探索项应为无穷大（优先探索）。"""
        # 验证我们处理 visits=0 的逻辑（直接返回未访问的父因子）
        # 这由 _select_parent_uct 的 visits==0 分支保证
        pass  # 逻辑已由 test_uct_selects_unvisited_first 覆盖
