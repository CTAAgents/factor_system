"""
tests/factor_engine/test_portfolio_loop_market_ohlcv.py — L3 期货路径市场合成 OHLCV 自动构建（v2.98.1）。

覆盖（方案 B：run() 未传 market_ohlcv 时，由 Step 0.5 加载的期货面板自动构建市场级
合成 OHLCV，激活 Step 2.5 Regime 自适应权重调整）:
    - 期货路径未传 market_ohlcv → 自动构建 → Step 2.5 regime 调整执行
    - 面板数据不足（空）→ market_ohlcv 保持 None → Step 2.5 跳过不报错
    - 显式传入 market_ohlcv → 优先使用，不被面板构建覆盖
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.portfolio_loop import PortfolioLoop


@pytest.fixture(autouse=True)
def _no_real_data_source(monkeypatch):
    """统一隔离真实数据源（TqSdk/DuckDB），防止 run() 内任何路径触碰网络。

    拦截 fts.data.FTSDataProvider 与 fts.data_futures.FuturesDataProvider，
    返回合成期货面板。测试体内的 with patch 优先级更高，不受影响。
    """
    panel = _make_panel()
    dates = pd.DatetimeIndex(panel["SYM0"].index)
    mock_provider = MagicMock()
    mock_provider.get_futures_panel.return_value = (panel, dates)
    mock_cls = MagicMock(return_value=mock_provider)
    monkeypatch.setattr("fts.data.FTSDataProvider", mock_cls)
    # 双保险：任何直接实例化 FuturesDataProvider 的路径也返回 mock
    monkeypatch.setattr("fts.data_futures.FuturesDataProvider", MagicMock())
    return mock_provider


def _factor(fid: str, name: str, sharpe: float = 1.8) -> dict:
    return {
        "factor_id": fid,
        "name": name,
        "sharpe": sharpe,
        "ic": 0.05,
        "turnover": 0.3,
        "decay_6m": 0.05,
        "style_tags": ["momentum"],
        "code": "def f(data, params):\n    return data['close']",
    }


def _factors() -> list[dict]:
    return [_factor("f1", "fut_trend_a", 2.0), _factor("f2", "fut_carry_b", 1.5)]


def _make_panel(n_syms: int = 3, n_days: int = 120) -> dict[str, pd.DataFrame]:
    """合成期货面板（多品种 × 交易日，含 close/volume 列）。"""
    idx = pd.date_range("2026-01-01", periods=n_days, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_syms):
        close = np.linspace(100 + i * 10, 100 + i * 10 + 50, n_days)
        panel[f"SYM{i}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": [1000.0] * n_days,
            },
            index=idx,
        )
    return panel


class _FakeRegimeSelector:
    """记录 detect 入参的 fake RegimeAwareSelector。"""

    def __init__(self, regime: str = "bull") -> None:
        self.seen_ohlcv: list = []
        self._regime = {
            "regime": regime,
            "confidence": 0.8,
            "detected_at": "2026-08-10T00:00:00",
            "features": {},
            "method": "mock",
        }

    def detect(self, ohlcv) -> dict:
        self.seen_ohlcv.append(ohlcv)
        return self._regime


def _make_loop(tmp_path) -> PortfolioLoop:
    return PortfolioLoop(
        memory_dir=str(tmp_path / "portfolio"),
        elite_dir=str(tmp_path / "elite"),
        synthesis_mode="sharpe_weight",
        use_duckdb=False,
        enable_regime_adaptation=True,
        market="futures",
    )


def test_futures_run_auto_builds_market_ohlcv_and_triggers_regime(tmp_path) -> None:
    """期货路径未传 market_ohlcv：自动构建合成 OHLCV，Step 2.5 regime 调整执行。"""
    panel = _make_panel()
    dates = pd.DatetimeIndex(panel["SYM0"].index)
    provider = MagicMock()
    provider.get_futures_panel.return_value = (panel, dates)

    loop = _make_loop(tmp_path)
    fake_selector = _FakeRegimeSelector("bull")
    adjust_calls: list[tuple] = []

    def _fake_adjust(signals, regime, factors, **kwargs):
        adjust_calls.append((signals, regime, factors))
        return signals

    with (
        patch("fts.data.FTSDataProvider", return_value=provider),
        patch(
            "fts.factor_engine.portfolio_loop.load_elite_factors",
            return_value=_factors(),
        ),
        patch(
            "fts.factor_engine.regime.RegimeAwareSelector",
            return_value=fake_selector,
        ),
        patch(
            "fts.factor_engine.portfolio_loop.regime_adaptive_weight_adjustment",
            side_effect=_fake_adjust,
        ),
    ):
        result = loop.run()

    assert result.status in ("passed", "verifier_warning")
    assert result.n_factors_input == 2
    # Step 2.5 被执行：market_ohlcv 由面板自动构建并触发 regime 调整
    assert len(fake_selector.seen_ohlcv) == 1
    assert fake_selector.seen_ohlcv[0] is not None and len(fake_selector.seen_ohlcv[0]) >= 20
    assert len(adjust_calls) == 1
    assert adjust_calls[0][1]["regime"] == "bull"
    assert adjust_calls[0][2] == _factors()


def test_futures_run_insufficient_panel_skips_regime(tmp_path) -> None:
    """面板为空：不构建 market_ohlcv，Step 2.5 跳过且不报错。"""
    provider = MagicMock()
    provider.get_futures_panel.return_value = ({}, pd.DatetimeIndex([]))

    loop = _make_loop(tmp_path)
    adjust_calls: list[tuple] = []

    def _fake_adjust(signals, regime, factors, **kwargs):
        adjust_calls.append((signals, regime, factors))
        return signals

    with (
        patch("fts.data.FTSDataProvider", return_value=provider),
        patch(
            "fts.factor_engine.portfolio_loop.load_elite_factors",
            return_value=_factors(),
        ),
        patch(
            "fts.factor_engine.portfolio_loop.regime_adaptive_weight_adjustment",
            side_effect=_fake_adjust,
        ),
    ):
        result = loop.run()

    assert result.status in ("passed", "verifier_warning")
    assert adjust_calls == []  # Step 2.5 未触发


def test_explicit_market_ohlcv_takes_priority(tmp_path) -> None:
    """显式传入 market_ohlcv：优先使用，不被面板自动构建覆盖。"""
    panel = _make_panel()
    dates = pd.DatetimeIndex(panel["SYM0"].index)
    provider = MagicMock()
    provider.get_futures_panel.return_value = (panel, dates)

    explicit_df = pd.DataFrame(
        {
            "open": [10.0] * 60,
            "high": [10.1] * 60,
            "low": [9.9] * 60,
            "close": [10.0] * 60,
            "volume": [500.0] * 60,
        },
        index=pd.date_range("2026-06-01", periods=60, freq="D"),
    )

    loop = _make_loop(tmp_path)
    fake_selector = _FakeRegimeSelector("oscillate")

    def _fake_adjust(signals, regime, factors, **kwargs):
        return signals

    with (
        patch("fts.data.FTSDataProvider", return_value=provider),
        patch(
            "fts.factor_engine.portfolio_loop.load_elite_factors",
            return_value=_factors(),
        ),
        patch(
            "fts.factor_engine.regime.RegimeAwareSelector",
            return_value=fake_selector,
        ),
        patch(
            "fts.factor_engine.portfolio_loop.regime_adaptive_weight_adjustment",
            side_effect=_fake_adjust,
        ),
    ):
        result = loop.run(market_ohlcv=explicit_df)

    assert result.status in ("passed", "verifier_warning")
    # detect 收到的是显式传入的同一对象
    assert fake_selector.seen_ohlcv and fake_selector.seen_ohlcv[0] is explicit_df
