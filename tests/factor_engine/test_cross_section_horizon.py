"""tests/factor_engine/test_cross_section_horizon.py — 横截面多持有期 IC 测试（GAP-060 股票路径接入）。

HARNESS §测试随重构: 覆盖成功路径 / 边界 / 降级路径。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.evaluation_chain import (
    compute_cs_multi_horizon_ic,
    cross_section_evaluate_backtest,
)
from fts.factor_engine.horizon_analysis import HorizonAnalysisResult


def _make_panel(n_stocks: int = 30, n_dates: int = 300, seed: int = 7) -> dict[str, pd.DataFrame]:
    """构造确定性面板：信号驱动未来收益（趋势延续），各股票独立。

    Returns:
        {symbol: OHLCV DataFrame}（common index = dates）
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        signal = rng.normal(0, 1, n_dates)
        rets = np.zeros(n_dates)
        rets[1:] = 0.02 * signal[:-1] + rng.normal(0, 0.005, n_dates - 1)
        close = 100 * np.exp(np.cumsum(rets))
        panel[f"S{i:03d}"] = pd.DataFrame(
            {
                "open": close + rng.normal(0, 0.1, n_dates),
                "high": close + np.abs(rng.normal(0, 0.3, n_dates)),
                "low": close - np.abs(rng.normal(0, 0.3, n_dates)),
                "close": close,
                "volume": rng.integers(1000, 10000, n_dates).astype(float),
            },
            index=dates,
        )
    return panel


def _minimal_factor() -> dict:
    """最小因子程序（5 日动量，与未来收益正相关，FactorExecutor 可执行）。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature
    from fts.factor_engine.factor_program import create_factor_program

    code = (
        "import numpy as np\n"
        "def factor_program(data, params):\n"
        "    close = data['close'].values\n"
        "    n = len(close)\n"
        "    sig = np.zeros(n)\n"
        "    for i in range(5, n):\n"
        "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
        "    return sig\n"
    )
    return create_factor_program(
        name="test_cs_horizon",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=5),
        economic_logic=EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4, narrative="测试动量因子"
        ),
        source="manual",
        market="stock",
    )


# ─── 成功路径 ─────────────────────────────────────────────


def test_cs_multi_horizon_result_shape():
    """横截面多持有期结果字段齐全，best_horizon 在 horizons 内。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    n_stocks = len(symbols)
    # 信号矩阵：直接使用面板 close 差分（近似因子信号）
    oos_n = 90
    signal = np.zeros((oos_n, n_stocks))
    for j, sym in enumerate(symbols):
        diff = panel[sym]["close"].diff().reindex(dates).values
        signal[:, j] = diff[-oos_n:]
    result = compute_cs_multi_horizon_ic(
        signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10, 20)
    )
    assert result is not None
    assert isinstance(result, HorizonAnalysisResult)
    assert result.horizons == [1, 5, 10, 20]
    assert set(result.ic_by_horizon) == {1, 5, 10, 20}
    assert set(result.icir_by_horizon) == {1, 5, 10, 20}
    assert set(result.win_rate_by_horizon) == {1, 5, 10, 20}
    assert result.best_horizon in {1, 5, 10, 20}
    assert set(result.decay_curve) == {1, 5, 10, 20}
    assert result.decay_curve[1] == pytest.approx(1.0)


def test_cs_to_dict_serializable():
    """to_dict 输出可 JSON 序列化。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    n_stocks = len(symbols)
    oos_n = 90
    signal = np.zeros((oos_n, n_stocks))
    for j, sym in enumerate(symbols):
        signal[:, j] = panel[sym]["close"].diff().reindex(dates).values[-oos_n:]
    result = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10))
    assert result is not None
    d = result.to_dict()
    json.dumps(d)
    assert d["best_horizon"] in {1, 5, 10}
    assert len(d["ic_by_horizon"]) == 3


# ─── 边界与降级路径 ───────────────────────────────────────


def test_cs_short_sample_returns_none():
    """样本外期数不足 min_dates 返回 None。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 5
    signal = np.zeros((oos_n, len(symbols)))
    assert compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, min_dates=10) is None


def test_cs_invalid_horizons_returns_none():
    """全部无效持有期返回 None。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 90
    signal = np.zeros((oos_n, len(symbols)))
    assert compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(0, -1)) is None


def test_cs_nan_close_handled():
    """缺失 close 的标的不崩溃（close 矩阵置 NaN，_cs_compute_ics 过滤）。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 90
    signal = np.zeros((oos_n, len(symbols)))
    panel["S000"] = panel["S000"].iloc[:10]  # 截短 → reindex 后 close 全 NaN
    result = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5))
    assert result is None or isinstance(result, HorizonAnalysisResult)


def test_cs_deterministic_reproducible():
    """同输入两次计算完全一致。"""
    panel = _make_panel()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 90
    signal = np.zeros((oos_n, len(symbols)))
    for j, sym in enumerate(symbols):
        signal[:, j] = panel[sym]["close"].diff().reindex(dates).values[-oos_n:]
    r1 = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10))
    r2 = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10))
    assert r1 is not None and r2 is not None
    assert r1.to_dict() == r2.to_dict()


# ─── 评估链集成 ───────────────────────────────────────────


def test_cross_section_evaluate_emits_multi_horizon():
    """cross_section_evaluate_backtest 显式传入 horizons 时输出 multi_horizon 字段。"""
    panel = _make_panel(n_stocks=30, n_dates=300)
    dates = list(panel.values())[0].index
    metrics = cross_section_evaluate_backtest(
        _minimal_factor(),
        panel,
        dates,
        oos_ratio=0.3,
        horizons=(1, 5, 10, 20),
    )
    assert "multi_horizon" in metrics
    mh = metrics["multi_horizon"]
    assert mh["best_horizon"] in {1, 5, 10, 20}
    assert set(mh["ic_by_horizon"]) == {1, 5, 10, 20}
    assert "decay_curve" in mh


def test_cross_section_evaluate_empty_horizons_no_multi_horizon():
    """显式传入空持有期元组时不产生 multi_horizon 字段（关闭路径）。"""
    panel = _make_panel(n_stocks=30, n_dates=300)
    dates = list(panel.values())[0].index
    metrics = cross_section_evaluate_backtest(
        _minimal_factor(),
        panel,
        dates,
        oos_ratio=0.3,
        horizons=(),
    )
    assert "multi_horizon" not in metrics
