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
from typing import Any

import numpy as np
import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import BacktestMetrics
from fts.factor_engine.cost_model import (
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
        """股票默认成本配置：主系统剥离后回退到期货默认。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("stock")
        assert cfg["slippage_bps"] == 0.5  # 回退期货默认
        assert cfg["commission_bps"] == 0.2

    def test_default_etf_config(self) -> None:
        """ETF 默认成本配置：主系统剥离后回退到期货默认。"""
        model = TransactionCostModel()
        cfg = model.get_cost_bps("etf")
        assert cfg["slippage_bps"] == 0.5  # 回退期货默认
        assert cfg["commission_bps"] == 0.2

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
        """主系统剥离后：stock/etf 回退期货配置，成本一致。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.tile([0.5, -0.5], 126)

        result_futures = model.adjust(metrics, signal, market="futures")
        result_stock = model.adjust(metrics, signal, market="stock")
        result_etf = model.adjust(metrics, signal, market="etf")

        # 剥离后 stock/etf 回退期货默认，成本一致
        assert result_stock["total_cost_bps"] == result_futures["total_cost_bps"]
        assert result_etf["total_cost_bps"] == result_futures["total_cost_bps"]

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


# ─── 展期成本测试（v2.58.0 GAP-046） ─────────────────────


class TestRollCost:
    """展期成本项（持仓穿越换月日扣 |position| × roll_cost_bps）。"""

    def test_futures_default_roll_cost_bps(self) -> None:
        """期货默认配置应含 roll_cost_bps=2.0（主系统剥离后 stock/etf 回退期货）。"""
        model = TransactionCostModel()
        assert model.get_cost_bps("futures")["roll_cost_bps"] == 2.0
        assert model.get_cost_bps("stock")["roll_cost_bps"] == 2.0
        assert model.get_cost_bps("etf")["roll_cost_bps"] == 2.0

    def test_roll_cost_added_to_total(self) -> None:
        """持仓穿越换月日时，总成本应包含展期成本。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5  # 满仓 0.5 持仓
        roll_dates = {str(dates[10].date()), str(dates[20].date())}

        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates=roll_dates,
        )
        # 展期成本 = |0.5| × 2.0 × 2 次 = 2.0 bps
        assert result["roll_cost_bps"] == pytest.approx(2.0, abs=1e-9)
        # 总成本 = min_cost(0.5) + 展期(2.0)
        assert result["total_cost_bps"] == pytest.approx(2.5, abs=1e-9)

    def test_no_roll_dates_no_roll_cost(self) -> None:
        """无换月日期时，展期成本为 0。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5

        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates=None,
        )
        assert result["roll_cost_bps"] == 0.0
        assert result["total_cost_bps"] == pytest.approx(0.5, abs=1e-9)

    def test_zero_position_no_roll_cost(self) -> None:
        """换月日无持仓时，不扣展期成本。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.zeros(n)  # 空仓
        roll_dates = {str(dates[10].date())}

        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates=roll_dates,
        )
        assert result["roll_cost_bps"] == 0.0

    def test_dates_length_mismatch_returns_zero(self) -> None:
        """dates 与 signal 长度不一致时，展期成本为 0（防越界）。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        signal = np.ones(30) * 0.5

        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates={str(dates[5].date())},
        )
        assert result["roll_cost_bps"] == 0.0

    def test_roll_cost_reduces_net_sharpe(self) -> None:
        """展期成本应降低 net_sharpe（成本惩罚计入）。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5
        roll_dates = {str(dates[10].date())}

        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates=roll_dates,
        )
        assert result["net_sharpe"] < metrics["sharpe"]

    def test_roll_cost_in_adjusted_metrics_fields(self) -> None:
        """AdjustedMetrics 应包含 roll_cost_bps 字段。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_dates={str(dates[10].date())},
        )
        assert "roll_cost_bps" in result


# ─── GAP-F11 展期成本联动换月日历（v2.67.0） ──────────────


class TestRollCostWithRollEvents:
    """展期成本联动换月日历 — 基于 RollEvent 实际价差。"""

    @staticmethod
    def _make_roll_event(date_str: str, old_close: float, new_close: float) -> Any:
        """构造一个简化的 RollEvent 模拟对象。"""
        from datetime import date
        from types import SimpleNamespace

        year, month, day = [int(x) for x in date_str.split("-")]
        return SimpleNamespace(
            date=date(year, month, day),
            old_close=old_close,
            new_close=new_close,
        )

    def test_roll_events_to_spread_map(self) -> None:
        """_roll_events_to_spread_map 应正确转换价差为 bps。"""
        events = [
            self._make_roll_event("2024-06-10", 3000.0, 3050.0),  # +1.67% → 166.7 bps
            self._make_roll_event("2024-09-10", 3100.0, 3080.0),  # -0.65% → 64.5 bps
        ]
        result = TransactionCostModel._roll_events_to_spread_map(events)
        assert "2024-06-10" in result
        assert "2024-09-10" in result
        # 166.7 bps ≈ 1.67%
        assert result["2024-06-10"] == pytest.approx(166.6667, abs=0.1)
        # 64.5 bps ≈ 0.65%
        assert result["2024-09-10"] == pytest.approx(64.5161, abs=0.1)

    def test_roll_events_to_spread_map_skip_bad_close(self) -> None:
        """old_close=0 或缺失时应跳过该事件。"""
        events = [
            self._make_roll_event("2024-06-10", 0.0, 3050.0),  # old_close=0
            self._make_roll_event("2024-09-10", 3100.0, 3080.0),  # 正常
        ]
        from types import SimpleNamespace
        from datetime import date

        events.append(SimpleNamespace(date=date(2024, 12, 10), old_close=3000.0))  # 缺 new_close
        result = TransactionCostModel._roll_events_to_spread_map(events)
        assert "2024-06-10" not in result  # 跳过
        assert "2024-09-10" in result  # 正常

    def test_adjust_roll_events_uses_actual_spread(self) -> None:
        """roll_events 提供时，实际价差 > 固定 bps 则用价差。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5  # 满仓 0.5
        # 价差 166.7 bps > 固定 2.0 bps → 用实际价差
        roll_events = [self._make_roll_event("2024-01-10", 3000.0, 3050.0)]
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_events=roll_events,
        )
        # 展期成本 = |0.5| × 166.7 = 83.35 bps（远高于固定 2.0）
        assert result["roll_cost_bps"] > 80.0
        assert result["roll_cost_bps"] > 2.0 * 10  # 远大于固定 bps 路径

    def test_adjust_roll_events_fallback_when_spread_low(self) -> None:
        """实际价差 < 固定 bps 时，仍用固定 bps（不高于固定）。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5
        # 价差 0.5 bps 远小于固定 2.0 bps → 用固定 2.0 bps
        roll_events = [self._make_roll_event("2024-01-10", 3000.0, 3000.15)]
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_events=roll_events,
        )
        # 展期成本 = |0.5| × 2.0 = 1.0 bps（固定 bps 兜底）
        assert result["roll_cost_bps"] == pytest.approx(1.0, abs=0.01)

    def test_adjust_roll_events_fallback_to_roll_dates(self) -> None:
        """roll_events 为空时，回退到 roll_dates + 固定 bps。"""
        import pandas as pd

        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        n = 30
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        signal = np.ones(n) * 0.5
        # 空列表
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            dates=dates.to_numpy(),
            roll_events=[],
            roll_dates={"2024-01-10"},
        )
        # 应回退到 roll_dates + 固定 bps
        assert result["roll_cost_bps"] == pytest.approx(1.0, abs=0.01)

    def test_adjust_roll_events_empty_events_no_roll_dates(self) -> None:
        """roll_events 为空且无 roll_dates 时，展期成本为 0。"""
        model = TransactionCostModel()
        metrics = _make_metrics(sharpe=2.0)
        signal = np.ones(30) * 0.5
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            roll_events=[],
            dates=None,
        )
        assert result["roll_cost_bps"] == 0.0

    def test_adjust_roll_events_no_roll_cost_bps(self) -> None:
        """roll_cost_bps=0 时，即使有 roll_events 也不扣展期成本。"""
        custom = CostConfig(
            slippage_bps=0.5,
            commission_bps=0.2,
            impact_bps_per_pct=1.0,
            min_cost_bps=0.5,
            roll_cost_bps=0.0,
            market="futures",
        )
        model = TransactionCostModel(config=custom)
        metrics = _make_metrics(sharpe=2.0)
        signal = np.ones(30) * 0.5
        roll_events = [self._make_roll_event("2024-01-10", 3000.0, 3050.0)]
        result = model.adjust(
            metrics,
            signal,
            market="futures",
            roll_events=roll_events,
        )
        assert result["roll_cost_bps"] == 0.0


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


# ─── 冲击成本 square-root 模型（GAP-L305） ───────────────


class TestImpactCost:
    """square-root 冲击成本函数（GAP-L305，衔接 GAP-I501/I303）。"""

    def test_zero_volume_zero_cost(self) -> None:
        """零成交量占比 → 冲击成本为 0。"""
        assert TransactionCostModel.impact_cost(0.0, 2.0) == 0.0

    def test_reference_pct_returns_coeff(self) -> None:
        """占比 = 参考占比（1%）时，成本 = impact_bps_per_pct。"""
        assert TransactionCostModel.impact_cost(0.01, 2.0) == pytest.approx(2.0)

    def test_monotonic_increasing(self) -> None:
        """冲击成本随成交量占比单调递增（square-root 模型）。"""
        costs = [TransactionCostModel.impact_cost(p, 2.0) for p in (0.001, 0.01, 0.05, 0.10, 0.20)]
        assert all(costs[i] < costs[i + 1] for i in range(len(costs) - 1))

    def test_square_root_sublinear(self) -> None:
        """square-root 特性：4 倍占比 → 2 倍成本（亚线性）。"""
        c1 = TransactionCostModel.impact_cost(0.01, 2.0)
        c4 = TransactionCostModel.impact_cost(0.04, 2.0)
        assert c4 == pytest.approx(2.0 * c1)

    def test_negative_coeff_zero(self) -> None:
        """impact_bps_per_pct<=0 → 成本为 0。"""
        assert TransactionCostModel.impact_cost(0.05, 0.0) == 0.0
        assert TransactionCostModel.impact_cost(0.05, -1.0) == 0.0

    def test_custom_reference_pct(self) -> None:
        """自定义参考占比生效。"""
        # ref_pct=0.05 时，占比 5% 对应成本 = impact_bps_per_pct
        assert TransactionCostModel.impact_cost(0.05, 3.0, ref_pct=0.05) == pytest.approx(3.0)
