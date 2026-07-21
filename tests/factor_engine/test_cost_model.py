"""
tests/factor_engine/test_cost_model.py — 交易成本模型测试

覆盖范围:
    - 默认配置值
    - 市场专属配置
    - adjust 零换手率（最低成本）
    - adjust 正换手率
    - adjust 含成交量冲击
    - net_sharpe < gross_sharpe（成本正确降低夏普）
    - 不同市场不同成本
    - 信号无变化（零换手率）
    - 自定义配置覆盖默认值

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import BacktestMetrics
from fts.factor_engine.cost_model import (
    AdjustedMetrics,
    CostConfig,
    TransactionCostModel,
)


# ─── 辅助函数 ─────────────────────────────────────────────

def _make_metrics(sharpe: float = 2.0, ic: float = 0.05) -> BacktestMetrics:
    """创建带默认值的 BacktestMetrics。"""
    return BacktestMetrics(
        ic=ic,
        icir=0.8,
        sharpe=sharpe,
        max_drawdown=0.1,
        monotonicity=True,
        oos_ratio=0.3,
        t_stat=3.5,
        turnover_monthly=0.3,
    )


# ─── 默认配置测试 ─────────────────────────────────────────

class TestDefaultConfig:
    """测试默认配置值。"""

    def test_default_futures_config(self) -> None:
        """期货默认成本配置应正确。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("futures")
        assert cfg["slippage_bps"] == 0.5
        assert cfg["commission_bps"] == 0.2
        assert cfg["impact_bps_per_pct"] == 1.0
        assert cfg["min_cost_bps"] == 0.5

    def test_default_stock_config(self) -> None:
        """股票默认成本配置应正确。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("stock")
        assert cfg["slippage_bps"] == 1.0
        assert cfg["commission_bps"] == 0.8
        assert cfg["impact_bps_per_pct"] == 2.0
        assert cfg["min_cost_bps"] == 0.5

    def test_default_etf_config(self) -> None:
        """ETF 默认成本配置应正确。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("etf")
        assert cfg["slippage_bps"] == 0.5
        assert cfg["commission_bps"] == 0.3
        assert cfg["impact_bps_per_pct"] == 1.0
        assert cfg["min_cost_bps"] == 0.5

    def test_unknown_market_falls_back(self) -> None:
        """未知市场应回退到全局默认配置。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("unknown_market")
        assert cfg["slippage_bps"] == 0.5  # 回退到 futures 默认
        assert cfg["commission_bps"] == 0.2

    def test_market_configs_override_defaults(self) -> None:
        """外部 market_configs 应覆盖内置默认值。"""
        custom = CostConfig(
            slippage_bps=2.0,
            commission_bps=1.0,
            impact_bps_per_pct=3.0,
            min_cost_bps=1.0,
            market="futures",
        )
        model = TransactionCostModel(market_configs={"futures": custom})
        cfg = model.get_cost_bps("futures")
        assert cfg["slippage_bps"] == 2.0
        assert cfg["commission_bps"] == 1.0
        assert cfg["impact_bps_per_pct"] == 3.0
        assert cfg["min_cost_bps"] == 1.0


# ─── adjust 方法测试 ──────────────────────────────────────

class TestAdjust:
    """测试 adjust 方法的各种场景。"""

    def test_zero_turnover_min_cost_applied(self) -> None:
        """信号无变化时，总成本应等于 min_cost_bps。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        # 常量信号 → 零换手率
        signal = np.ones(100) * 0.5
        result = model.adjust(metrics, signal, market="futures")
        assert result["turnover"] == pytest.approx(0.0, abs=1e-6)
        # 零换手率下 raw_cost=0，应被 min_cost=0.5 兜底
        assert result["total_cost_bps"] == pytest.approx(0.5, abs=1e-6)

    def test_positive_turnover_calculates_cost(self) -> None:
        """正换手率应产生正成本。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        # 交替信号 → 高频变化
        signal = np.tile([0.5, -0.5], 126)  # 252 天
        result = model.adjust(metrics, signal, market="futures")
        assert result["turnover"] > 0
        assert result["total_cost_bps"] > 0.5

    def test_net_sharpe_less_than_gross_sharpe(self) -> None:
        """成本应正确降低夏普比率。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        # 温和信号变化（0.1 ↔ -0.1），换手率适中
        signal = np.tile([0.1, -0.1], 126)
        result = model.adjust(metrics, signal, market="futures")
        assert result["net_sharpe"] < result["gross_sharpe"]
        assert result["net_sharpe"] > 0  # 仍应为正

    def test_adjust_with_volume_impact(self) -> None:
        """传入成交量时应增加冲击成本。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.tile([1.0, -1.0], 126)
        volume = np.ones(252) * 10000
        result_with_vol = model.adjust(metrics, signal, volume=volume, market="futures")
        result_no_vol = model.adjust(metrics, signal, market="futures")
        # 有成交量冲击时总成本应更高
        assert result_with_vol["total_cost_bps"] >= result_no_vol["total_cost_bps"]

    def test_different_markets_different_costs(self) -> None:
        """不同市场的成本应不同。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.tile([0.5, -0.5], 126)

        result_futures = model.adjust(metrics, signal, market="futures")
        result_stock = model.adjust(metrics, signal, market="stock")
        result_etf = model.adjust(metrics, signal, market="etf")

        # 股票成本最高（手续费 0.8 > 期货 0.2）
        assert result_stock["total_cost_bps"] > result_futures["total_cost_bps"]
        assert result_etf["total_cost_bps"] <= result_stock["total_cost_bps"]

    def test_constant_signal_zero_turnover(self) -> None:
        """完全恒定的信号应产生零换手率。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.zeros(200)
        result = model.adjust(metrics, signal, market="futures")
        assert result["turnover"] == pytest.approx(0.0, abs=1e-6)
        # 零换手率下，总成本 = min_cost (0.5)
        assert result["total_cost_bps"] == pytest.approx(0.5, abs=1e-6)

    def test_single_element_signal(self) -> None:
        """单元素信号应正确处理（零换手率）。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=1.0)
        signal = np.array([0.5])
        result = model.adjust(metrics, signal, market="futures")
        assert result["turnover"] == pytest.approx(0.0, abs=1e-6)

    def test_cost_adjusted_ic_scaled(self) -> None:
        """成本调整后的 IC 应随夏普等比例缩放。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0, ic=0.05)
        # 温和信号变化
        signal = np.tile([0.1, -0.1], 126)
        result = model.adjust(metrics, signal, market="futures")
        # net_sharpe < gross_sharpe → cost_adjusted_ic < gross_ic
        assert result["cost_adjusted_ic"] < metrics["ic"]
        assert result["cost_adjusted_ic"] >= 0

    def test_adjust_returns_all_fields(self) -> None:
        """adjust 应返回完整的 AdjustedMetrics 字段。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.tile([0.5, -0.5], 126)
        result = model.adjust(metrics, signal, market="futures")
        assert "gross_sharpe" in result
        assert "net_sharpe" in result
        assert "total_cost_bps" in result
        assert "turnover" in result
        assert "cost_adjusted_ic" in result


# ─── 自定义配置测试 ───────────────────────────────────────

class TestCustomConfig:
    """测试自定义配置。"""

    def test_custom_config_overrides_default(self) -> None:
        """自定义全局配置应覆盖默认值。"""
        custom = CostConfig(
            slippage_bps=3.0,
            commission_bps=1.5,
            impact_bps_per_pct=5.0,
            min_cost_bps=2.0,
            market="futures",
        )
        model = TransactionCostModel(config=custom)
        cfg = model.get_cost_bps("futures")
        assert cfg["slippage_bps"] == 3.0
        assert cfg["commission_bps"] == 1.5

    def test_custom_config_affects_cost(self) -> None:
        """自定义配置应影响总成本计算。"""
        custom = CostConfig(
            slippage_bps=10.0,
            commission_bps=5.0,
            impact_bps_per_pct=20.0,
            min_cost_bps=5.0,
            market="futures",
        )
        model = TransactionCostModel(config=custom)
        metrics = _make_metrics(sharpe=2.0)
        signal = np.tile([1.0, -1.0], 126)
        result = model.adjust(metrics, signal, market="futures")
        # 高成本配置应产生比默认更低的 net_sharpe
        default_model = TransactionCostModel()
        default_result = default_model.adjust(metrics, signal, market="futures")
        assert result["net_sharpe"] < default_result["net_sharpe"]
        assert result["total_cost_bps"] > default_result["total_cost_bps"]

    def test_custom_market_config_preserves_others(self) -> None:
        """只覆盖一个市场时，其他市场应保留默认值。"""
        custom_stock = CostConfig(
            slippage_bps=2.0,
            commission_bps=1.0,
            impact_bps_per_pct=3.0,
            min_cost_bps=1.0,
            market="stock",
        )
        model = TransactionCostModel(market_configs={"stock": custom_stock})
        # stock 应使用自定义值
        stock_cfg = model.get_cost_bps("stock")
        assert stock_cfg["slippage_bps"] == 2.0
        # futures 应保留默认值
        futures_cfg = model.get_cost_bps("futures")
        assert futures_cfg["slippage_bps"] == 0.5


# ─── 边缘情况测试 ─────────────────────────────────────────

class TestEdgeCases:
    """测试边缘情况。"""

    def test_empty_signal_array(self) -> None:
        """空信号数组应返回零换手率。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=1.0)
        signal = np.array([])
        result = model.adjust(metrics, signal, market="futures")
        assert result["turnover"] == pytest.approx(0.0, abs=1e-6)
        assert result["total_cost_bps"] == pytest.approx(0.5, abs=1e-6)

    def test_negative_sharpe_preserved(self) -> None:
        """负夏普比率在成本调整后应更低。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=-0.5)
        signal = np.tile([0.5, -0.5], 126)
        result = model.adjust(metrics, signal, market="futures")
        assert result["net_sharpe"] < result["gross_sharpe"]
        assert result["net_sharpe"] < 0

    def test_zero_ic_handling(self) -> None:
        """IC 为零时 cost_adjusted_ic 应为零。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0, ic=0.0)
        signal = np.tile([1.0, -1.0], 126)
        result = model.adjust(metrics, signal, market="futures")
        assert result["cost_adjusted_ic"] == pytest.approx(0.0, abs=1e-6)
