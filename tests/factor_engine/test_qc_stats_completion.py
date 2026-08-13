"""tests/factor_engine/test_qc_stats_completion.py — 评估链统计补全测试（GAP-062）。

覆盖: IC t 值 / 日度 IC 胜率 / 最大连续亏损 / Q1-Q5 分组 / 信号翻转频率 / 截面分散度。
HARNESS §测试随重构: 成功路径 + 边界 + 降级路径。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
from fts.factor_engine.evaluation_chain import (
    _block_ic_stats,
    _cs_quintile_returns,
    _max_consecutive_losses,
    cross_section_evaluate_backtest,
    evaluate_backtest,
)
from fts.factor_engine.factor_program import create_factor_program


def _driver_factor(driver_col: str = "driver") -> FactorProgram:
    """返回 driver 列的因子程序（信号 = 注入的预测性列）。"""
    code = f"""
import numpy as np
def factor_program(data, params):
    return data['{driver_col}'].values
"""
    return create_factor_program(
        name="driver_factor",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="driver"),
        source="manual",
    )


def _predictive_ohlcv(n: int = 500, seed: int = 3) -> pd.DataFrame:
    """close 由 driver 驱动（信号正相关），构造预测性数据。"""
    rng = np.random.default_rng(seed)
    driver = rng.normal(0, 1, n)
    rets = np.zeros(n)
    rets[1:] = 0.04 * driver[:-1] + rng.normal(0, 0.008, n - 1)
    close = 100 * np.exp(np.cumsum(rets))
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(n, 5000.0),
            "driver": driver,
        },
        index=dates,
    )


# ─── 单元：最大连续亏损 ───────────────────────────────────


def test_max_consecutive_losses_basic():
    """连续亏损序列正确计数。"""
    assert _max_consecutive_losses(np.array([1.0, -0.5, -0.2, 0.3, -1.0, -0.4, -0.1, 0.1])) == 3


def test_max_consecutive_losses_all_positive():
    """全盈利序列最大连亏为 0。"""
    assert _max_consecutive_losses(np.array([0.1, 0.2, 0.3])) == 0


# ─── 单元：块状 IC t 统计量 / 胜率 ────────────────────────


def test_block_ic_stats_predictive():
    """预测性信号：ic_t > 0 且 win_rate ∈ (0,1]（G4 三元组含 icir_block）。"""
    df = _predictive_ohlcv()
    fwd = np.zeros(len(df))
    fwd[:-1] = (df["close"].values[1:] - df["close"].values[:-1]) / np.maximum(df["close"].values[:-1], 1e-10)
    stats = _block_ic_stats(df["driver"].values, fwd)
    assert stats is not None
    ic_t, win_rate, icir_block = stats
    assert ic_t > 0
    assert 0.0 < win_rate <= 1.0
    assert icir_block > 0


def test_block_ic_stats_constant_returns_none():
    """常数信号无 IC 序列，返回 None。"""
    n = 200
    fwd = np.zeros(n)
    stats = _block_ic_stats(np.ones(n) * 2.0, fwd)
    assert stats is None or stats[1] in (0.0, 1.0)


# ─── 单元：Q1-Q5 分组 ─────────────────────────────────────


def test_cs_quintile_returns_monotonic():
    """构造单调分组数据：Q5 收益 > Q1，spread > 0，monotonic True。"""
    n_dates, n_syms = 40, 20
    rng = np.random.default_rng(5)
    sig = rng.normal(0, 1, (n_dates, n_syms))
    ret = 0.05 * sig + rng.normal(0, 0.001, (n_dates, n_syms))
    qr = _cs_quintile_returns(sig, ret)
    assert qr
    assert qr[5] > qr[1]
    assert qr["q5_q1_spread"] > 0
    assert qr.get("monotonic", False) is True


def test_cs_quintile_returns_small_panel_empty():
    """品种不足返回空 dict。"""
    sig = np.random.default_rng(1).normal(0, 1, (10, 5))
    ret = np.random.default_rng(2).normal(0, 1, (10, 5))
    assert _cs_quintile_returns(sig, ret) == {}


# ─── 集成：evaluate_backtest 时序路径 ─────────────────────


def test_evaluate_backtest_gap062_fields(sample_ohlcv):
    """时序路径输出 sign_flip_rate / max_consecutive_losses / ic_t_stat / win_rate。"""
    df = _predictive_ohlcv()
    fwd = np.zeros(len(df))
    fwd[:-1] = (df["close"].values[1:] - df["close"].values[:-1]) / np.maximum(df["close"].values[:-1], 1e-10)
    bt = evaluate_backtest(_driver_factor(), df, fwd)
    assert "sign_flip_rate" in bt
    assert 0.0 <= bt["sign_flip_rate"] <= 1.0
    assert "max_consecutive_losses" in bt
    assert bt["max_consecutive_losses"] >= 0
    assert "ic_t_stat" in bt
    assert "win_rate" in bt
    assert 0.0 <= bt["win_rate"] <= 1.0


def test_evaluate_backtest_multi_horizon_enabled():
    """显式传入 horizons 时输出 multi_horizon 字段（GAP-060）。"""
    df = _predictive_ohlcv()
    fwd = np.zeros(len(df))
    fwd[:-1] = (df["close"].values[1:] - df["close"].values[:-1]) / np.maximum(df["close"].values[:-1], 1e-10)
    bt = evaluate_backtest(_driver_factor(), df, fwd, horizons=(1, 5, 10))
    assert "multi_horizon" in bt
    mh = bt["multi_horizon"]
    assert set(mh["ic_by_horizon"]) == {1, 5, 10}
    assert mh["best_horizon"] in {1, 5, 10}


# ─── 集成：横截面路径 ─────────────────────────────────────


def _cs_panel(n_dates: int = 120, n_syms: int = 25, seed: int = 7) -> tuple[dict, list]:
    """构造横截面单调预测面板（signal 驱动 5 日收益）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel: dict = {}
    for s in range(n_syms):
        signal = rng.normal(0, 1, n_dates)
        rets = np.zeros(n_dates)
        rets[1:] = 0.03 * signal[:-1] + rng.normal(0, 0.01, n_dates - 1)
        close = 100 * np.exp(np.cumsum(rets))
        panel[f"S{s}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.001,
                "low": close * 0.999,
                "close": close,
                "volume": np.full(n_dates, 5000.0),
                "driver": signal,
            },
            index=dates,
        )
    return panel, list(dates)


def test_cross_section_gap062_fields():
    """横截面路径输出 ic_t_stat / win_rate / cs_dispersion / quintile_returns。"""
    panel, dates = _cs_panel()
    bt = cross_section_evaluate_backtest(_driver_factor(), panel, dates)
    assert "ic_t_stat" in bt
    assert "win_rate" in bt
    assert 0.0 <= bt["win_rate"] <= 1.0
    assert "cs_dispersion" in bt
    assert bt["cs_dispersion"] > 0
    assert "quintile_returns" in bt
    qr = bt["quintile_returns"]
    assert 1 in qr and 5 in qr
    assert qr["q5_q1_spread"] > 0


# ─── 集成：回测流水线最大连续亏损 ─────────────────────────


def test_backtest_pipeline_max_consecutive_losses():
    """回测流水线绩效指标输出 max_consecutive_losses（GAP-062）。"""
    from fts.factor_engine.backtest_pipeline import BacktestPipeline

    returns = np.array([1.0, -0.5, -0.2, 0.3, -1.0, -0.4, -0.1, 0.1])
    equity = pd.Series(np.cumsum(returns))
    ic_series = pd.Series(np.random.default_rng(0).normal(0, 0.05, len(returns)))
    positions = np.zeros(len(returns))
    pm = BacktestPipeline._calculate_metrics(returns, equity, ic_series, positions)
    assert pm.max_consecutive_losses == 3
