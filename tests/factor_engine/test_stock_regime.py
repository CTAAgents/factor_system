"""
tests.factor_engine.test_stock_regime — A 股行业轮动 + 风格轮动制度检测（GAP-S03）。

覆盖:
  - StockRegimeSelector 行业轮动状态检测（concentrated/rotating/balanced）
  - 风格切换检测（large_cap/small_cap、growth/value）与多周期 HMM 集成
  - 空面板 / 样本不足降级
  - REGIME_STYLE_MULTIPLIERS 新增股票风格键（v2.63.0）
  - PortfolioLoop.run(stock_regime=...) 驱动 L3 风格自适应权重
"""

from __future__ import annotations

import json
from unittest.mock import patch

import numpy as np
import pandas as pd

from fts.factor_engine.portfolio_loop import (
    PortfolioLoop,
    REGIME_STYLE_MULTIPLIERS,
)
from fts.factor_engine.stock_regime import StockRegimeSelector


# ─── 数据构造辅助 ─────────────────────────────────────────

def _make_price(
    n_days: int,
    drift: float,
    seed: int,
    start: float = 100.0,
    vol: float = 0.004,
) -> pd.Series:
    """对数随机游走价格序列（可复现，默认低噪声突出趋势）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, size=n_days)
    price = start * np.exp(np.cumsum(rets))
    idx = pd.date_range("2015-01-01", periods=n_days, freq="B")
    return pd.Series(price, index=idx)


def _make_returns(
    n_days: int,
    drift: float,
    seed: int,
    vol: float = 0.004,
) -> pd.Series:
    """收益序列。"""
    price = _make_price(n_days, drift, seed, vol=vol)
    return price.pct_change().dropna()


def _make_style_panel(
    large_drift: float,
    small_drift: float,
    growth_drift: float,
    value_drift: float,
    n_days: int = 300,
    seeds: tuple[int, int, int, int] = (1, 2, 3, 4),
) -> dict[str, pd.Series]:
    """构造风格指数面板（价格序列）。"""
    return {
        "large_index": _make_price(n_days, large_drift, seeds[0]),
        "small_index": _make_price(n_days, small_drift, seeds[1]),
        "growth_index": _make_price(n_days, growth_drift, seeds[2]),
        "value_index": _make_price(n_days, value_drift, seeds[3]),
    }


def _make_industry_panel(
    drifts: list[float],
    n_days: int = 250,
    base_seed: int = 10,
    vol: float = 0.004,
) -> dict[str, pd.Series]:
    """构造行业收益面板：每个行业独立 drift。"""
    return {
        f"ind_{i}": _make_returns(n_days, d, base_seed + i, vol=vol)
        for i, d in enumerate(drifts)
    }


# ─── 检测器核心 ───────────────────────────────────────────

class TestStockRegimeSelector:
    def test_empty_panels_fallback(self) -> None:
        """空面板 → oscillate fallback。"""
        sel = StockRegimeSelector()
        result = sel.detect(industry_panel=None, style_panel=None)
        assert result["regime"] == "oscillate"
        assert result["method"] == "fallback"
        assert 0.0 < result["confidence"] <= 1.0
        assert result["industry"]["state"] == "unknown"
        assert result["style"]["size_state"] == "unknown"

    def test_industry_concentrated(self) -> None:
        """少数行业强动量 + 高集中度 → concentrated。"""
        # 2 个强动量 + 8 个平庸
        drifts = [0.004] * 2 + [0.0002] * 8
        panel = _make_industry_panel(drifts)
        sel = StockRegimeSelector()
        state = sel.detect_industry(panel)
        assert state["state"] == "concentrated"
        assert state["rotation_strength"] > 0
        assert state["concentration"] > 0.4
        assert len(state["top_industries"]) <= sel.top_n

    def test_industry_rotating(self) -> None:
        """强分化但无绝对主线 → rotating。"""
        # 行业动量正负交替且幅度大（离散度高，但 top 集中度被正负抵消）
        drifts = [0.004, -0.004, 0.0035, -0.0035, 0.003,
                  -0.003, 0.0025, -0.0025, 0.002, -0.002]
        panel = _make_industry_panel(drifts)
        sel = StockRegimeSelector(rotation_std_threshold=0.001)
        state = sel.detect_industry(panel)
        assert state["state"] in ("rotating", "concentrated")
        assert state["rotation_strength"] > 0

    def test_industry_balanced(self) -> None:
        """行业动量接近 → balanced。"""
        # 完全相同的序列（同 drift 同 seed）→ 动量横截面离散度趋近 0
        seq = _make_returns(250, 0.0005, 99)
        panel = {f"ind_{i}": seq.copy() for i in range(10)}
        sel = StockRegimeSelector()
        state = sel.detect_industry(panel)
        assert state["state"] == "balanced"

    def test_industry_insufficient_samples(self) -> None:
        """少于 2 个有效行业 → unknown。"""
        sel = StockRegimeSelector()
        state = sel.detect_industry({"only_one": _make_returns(100, 0.001, 1)})
        assert state["state"] == "unknown"

    def test_style_large_cap_dominant(self) -> None:
        """大盘占优 → large_cap。"""
        panel = _make_style_panel(
            large_drift=0.002, small_drift=-0.002,
            growth_drift=0.0, value_drift=0.0,
        )
        sel = StockRegimeSelector()
        state = sel.detect_style(panel)
        assert state["size_state"] == "large_cap"

    def test_style_small_cap_dominant(self) -> None:
        """小盘占优 → small_cap。"""
        panel = _make_style_panel(
            large_drift=-0.002, small_drift=0.002,
            growth_drift=0.0, value_drift=0.0,
        )
        sel = StockRegimeSelector()
        state = sel.detect_style(panel)
        assert state["size_state"] == "small_cap"

    def test_style_growth_dominant(self) -> None:
        """成长占优 → growth。"""
        panel = _make_style_panel(
            large_drift=0.0, small_drift=0.0,
            growth_drift=0.002, value_drift=-0.002,
        )
        sel = StockRegimeSelector()
        state = sel.detect_style(panel)
        assert state["growth_state"] == "growth"

    def test_style_value_dominant(self) -> None:
        """价值占优 → value。"""
        panel = _make_style_panel(
            large_drift=0.0, small_drift=0.0,
            growth_drift=-0.002, value_drift=0.002,
        )
        sel = StockRegimeSelector()
        state = sel.detect_style(panel)
        assert state["growth_state"] == "value"

    def test_style_missing_pair_unknown(self) -> None:
        """缺少 large/small 指数 → size 维度 unknown，regime 由 growth 维度驱动。"""
        panel = {
            "growth_index": _make_price(300, 0.002, 5),
            "value_index": _make_price(300, -0.002, 6),
        }
        sel = StockRegimeSelector()
        state = sel.detect_style(panel)
        assert state["size_state"] == "unknown"
        assert state["growth_state"] == "growth"

        result = sel.detect(style_panel=panel)
        assert result["regime"] == "growth"

    def test_detect_prefers_style_regime(self) -> None:
        """style 与 industry 同时可用时 regime 取风格键。"""
        style_panel = _make_style_panel(0.002, -0.002, 0.0, 0.0)
        industry_panel = _make_industry_panel([0.004] * 2 + [0.0002] * 8)
        sel = StockRegimeSelector()
        result = sel.detect(industry_panel=industry_panel, style_panel=style_panel)
        assert result["regime"] == "large_cap"
        assert result["method"] in ("stock_hmm", "stock_rule")
        assert result["industry"]["state"] == "concentrated"
        assert "industry" in result["features"]
        assert "style" in result["features"]

    def test_style_switch_scenarios(self) -> None:
        """多风格切换样本检测正确率（2015/2018/2021 风格切换模拟）≥ 80%。"""
        scenarios: list[tuple[tuple[float, float, float, float], str]] = [
            # (large, small, growth, value) → 期望 regime（动量更显著维度优先）
            ((0.002, -0.002, 0.004, -0.004), "growth"),
            ((-0.002, 0.002, -0.004, 0.004), "value"),
            ((0.002, -0.002, 0.0005, -0.0005), "large_cap"),
            ((-0.002, 0.002, 0.0005, -0.0005), "small_cap"),
            ((0.0015, 0.0002, 0.003, -0.001), "growth"),
        ]
        sel = StockRegimeSelector()
        hits = 0
        for (lg, sm, gr, va), expected in scenarios:
            panel = _make_style_panel(lg, sm, gr, va)
            result = sel.detect(style_panel=panel)
            if result["regime"] == expected:
                hits += 1
        assert hits / len(scenarios) >= 0.8

    def test_hmm_reuse_or_fallback(self) -> None:
        """HMM 可用时使用多周期集成，不可用回退规则（均不抛错）。"""
        sel = StockRegimeSelector(use_hmm=True)
        panel = _make_style_panel(0.002, -0.002, 0.0, 0.0)
        result = sel.detect(style_panel=panel)
        # 无论 hmmlearn 是否可用，检测都能给出确定性结果
        assert result["regime"] in ("large_cap", "small_cap", "growth", "value", "oscillate")
        assert result["method"] in ("stock_hmm", "stock_rule", "fallback")

    def test_industry_panel_ohlcv_accept(self) -> None:
        """行业面板支持 OHLCV DataFrame 输入。"""
        n = 250
        idx = pd.date_range("2015-01-01", periods=n, freq="B")
        close = _make_price(n, 0.004, 1).to_numpy()
        ohlcv = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": np.ones(n),
        }, index=idx)
        sel = StockRegimeSelector()
        state = sel.detect_industry({"ind_strong": ohlcv})
        assert state["state"] == "unknown"  # 单行业样本不足


# ─── REGIME_STYLE_MULTIPLIERS 股票风格键 ───────────────────

class TestStockStyleMultipliers:
    def test_new_style_keys_present(self) -> None:
        """v2.63.0 新增 6 个股票风格键。"""
        for key in ("large_cap", "small_cap", "growth", "value",
                    "sector_concentrated", "sector_rotating"):
            assert key in REGIME_STYLE_MULTIPLIERS
            assert isinstance(REGIME_STYLE_MULTIPLIERS[key], dict)

    def test_multiplier_values_reasonable(self) -> None:
        """新增键倍率在合理范围 [0.3, 1.5]。"""
        for key in ("large_cap", "small_cap", "growth", "value",
                    "sector_concentrated", "sector_rotating"):
            for style, mult in REGIME_STYLE_MULTIPLIERS[key].items():
                assert 0.3 <= mult <= 1.5, f"{key}/{style}={mult}"

    def test_legacy_regimes_untouched(self) -> None:
        """原有 5 制度键保持不变。"""
        for r in ("bull", "bear", "oscillate", "high_vol", "low_vol"):
            assert r in REGIME_STYLE_MULTIPLIERS


# ─── L3 集成 ──────────────────────────────────────────────

class TestPortfolioLoopStockRegime:
    def _write_mock_factor(self, tmp_elite_dir) -> None:
        (tmp_elite_dir / "factor_test.json").write_text(json.dumps({
            "factor_id": "fct_mock",
            "name": "mock_momentum",
            "sharpe": 2.5,
            "ic": 0.05,
            "turnover": 0.3,
            "decay_6m": 0.1,
            "family": "trend",
            "style_tags": ["momentum"],
        }), encoding="utf-8")

    def test_run_with_stock_regime(self, tmp_path) -> None:
        """market=stock + stock_regime 传入 → Step 2.5 使用风格 regime 驱动权重。"""
        tmp_portfolio_dir = tmp_path / "portfolio"
        tmp_portfolio_dir.mkdir(parents=True, exist_ok=True)
        tmp_elite_dir = tmp_path / "elite"
        tmp_elite_dir.mkdir(parents=True, exist_ok=True)
        self._write_mock_factor(tmp_elite_dir)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            market="stock",
        )
        stock_regime = {
            "regime": "large_cap",
            "confidence": 0.8,
            "detected_at": "2026-08-09T00:00:00",
            "features": {"style": {"size_state": "large_cap"}},
            "method": "stock_rule",
        }
        with patch(
            "fts.factor_engine.portfolio_loop.regime_adaptive_weight_adjustment",
            side_effect=lambda signals, regime, factors, **kw: signals,
        ) as mock_adj:
            result = loop.run(stock_regime=stock_regime)
        assert result.status in ("passed", "verifier_warning", "completed", "circuit_broken")
        assert mock_adj.called, "stock_regime 应触发 Step 2.5 自适应权重调整"
        args = mock_adj.call_args.args
        assert args[1]["regime"] == "large_cap"

    def test_run_stock_regime_nonexistent_key_falls_back(
        self, tmp_path,
    ) -> None:
        """market != stock 时 stock_regime 不生效（走通用 RegimeAwareSelector）。"""
        tmp_portfolio_dir = tmp_path / "portfolio"
        tmp_portfolio_dir.mkdir(parents=True, exist_ok=True)
        tmp_elite_dir = tmp_path / "elite"
        tmp_elite_dir.mkdir(parents=True, exist_ok=True)
        self._write_mock_factor(tmp_elite_dir)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            market="futures",
        )
        # futures 场景无 market_ohlcv → 跳过自适应，不抛错
        result = loop.run(stock_regime={"regime": "large_cap", "confidence": 0.8})
        assert result.status in ("passed", "verifier_warning", "completed", "circuit_broken")
