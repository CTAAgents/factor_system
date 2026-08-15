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


# ─── P0 多持有期 IC 面板向量化零漂移对照（plans/37 Phase 1 延伸） ───


def _assert_zero_drift(a: dict, b: dict) -> None:
    """多持有期结果逐字段零漂移：标量精确一致 + 逐持有期浮点容差一致。"""
    assert set(a) == set(b), f"字段不一致: {set(a)} vs {set(b)}"
    for key in ("horizons", "best_horizon", "monotonic_decay"):
        assert a[key] == b[key], f"字段不一致: {key} {a[key]} vs {b[key]}"
    for key in ("ic_by_horizon", "icir_by_horizon", "win_rate_by_horizon", "decay_curve"):
        assert set(a[key]) == set(b[key]), f"{key} 持有期不一致"
        for h in a[key]:
            np.testing.assert_allclose(a[key][h], b[key][h], atol=1e-12, rtol=1e-9, err_msg=f"{key}[{h}]")


def _make_nan_panel(n_stocks: int = 40, n_dates: int = 400, seed: int = 23) -> dict[str, pd.DataFrame]:
    """带缺口面板：部分标的中段日期缺失（reindex 后 close 含 NaN）。"""
    panel = _make_panel(n_stocks=n_stocks, n_dates=n_dates, seed=seed)
    dates = list(panel.values())[0].index
    # 2 个标的中段缺 15 日（模拟上市/停牌缺口）
    for idx, sym in enumerate(list(panel.keys())[-2:]):
        drop = dates[idx * 30 + 40 : idx * 30 + 55]
        panel[sym] = panel[sym].drop(index=drop)
    return panel


@pytest.mark.parametrize(
    "panel_fn,desc",
    [
        (_make_panel, "全有限面板"),
        (_make_nan_panel, "缺口面板（close 含 NaN）"),
    ],
)
def test_cs_multi_horizon_panel_vector_zero_drift(panel_fn, desc: str) -> None:
    """P0 验收：多持有期 IC 面板向量化 vs 旧路径逐字段零漂移。"""
    panel = panel_fn()
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 120
    signal = np.zeros((oos_n, len(symbols)))
    for j, sym in enumerate(symbols):
        signal[:, j] = panel[sym]["close"].diff().reindex(dates).values[-oos_n:]
    old = compute_cs_multi_horizon_ic(
        signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10, 20), use_panel_vector=False
    )
    new = compute_cs_multi_horizon_ic(
        signal, panel, symbols, dates, oos_n, horizons=(1, 5, 10, 20), use_panel_vector=True
    )
    assert old is not None and new is not None
    _assert_zero_drift(old.to_dict(), new.to_dict())


def test_cs_multi_horizon_panel_vector_short_sample_zero_drift() -> None:
    """短样本（块数=1 退化路径）on/off 仍逐位一致。"""
    panel = _make_panel(n_stocks=30, n_dates=120, seed=5)
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 15
    signal = np.zeros((oos_n, len(symbols)))
    for j, sym in enumerate(symbols):
        signal[:, j] = panel[sym]["close"].diff().reindex(dates).values[-oos_n:]
    old = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5), use_panel_vector=False)
    new = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 5), use_panel_vector=True)
    assert old is not None and new is not None
    _assert_zero_drift(old.to_dict(), new.to_dict())


def test_cs_multi_horizon_panel_vector_invalid_horizon_both_none() -> None:
    """h >= oos_n（fwd 全 NaN）退化路径：on/off 均返回 None 或同结构。"""
    panel = _make_panel(n_stocks=30, n_dates=120, seed=6)
    dates = list(panel.values())[0].index
    symbols = list(panel.keys())
    oos_n = 20
    signal = np.zeros((oos_n, len(symbols)))
    for j, sym in enumerate(symbols):
        signal[:, j] = panel[sym]["close"].diff().reindex(dates).values[-oos_n:]
    old = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 30), use_panel_vector=False)
    new = compute_cs_multi_horizon_ic(signal, panel, symbols, dates, oos_n, horizons=(1, 30), use_panel_vector=True)
    if old is None or new is None:
        assert old is None and new is None
    else:
        _assert_zero_drift(old.to_dict(), new.to_dict())


def test_cs_eval_multi_horizon_switch_identical() -> None:
    """评估链集成：cross_section_evaluate_backtest 显式传入 horizons 时，
    use_panel_vector on/off 的 multi_horizon 字段逐位一致（主链路口径统一）。"""
    panel = _make_panel(n_stocks=30, n_dates=300)
    dates = list(panel.values())[0].index
    kw = dict(factor=_minimal_factor(), panel_data=panel, common_dates=dates, oos_ratio=0.3, horizons=(1, 5, 10))
    bt_old = cross_section_evaluate_backtest(**kw, use_panel_vector=False)
    bt_new = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    assert "multi_horizon" in bt_old and "multi_horizon" in bt_new
    _assert_zero_drift(bt_old["multi_horizon"], bt_new["multi_horizon"])
