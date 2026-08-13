"""tests/factor_engine/test_g11_turnover_gate.py — G11 日换手硬剔除测试（35-gap-closure-plan §5.4）。

覆盖: turnover_daily 字段注入 / 配置门槛开启时高换手因子拒收 / 默认关闭不改变准入。
HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import EconomicLogic, FactorSignature
from fts.factor_engine.evaluation_chain import EvaluationChain, cross_section_evaluate_backtest, evaluate_backtest
from fts.factor_engine.factor_program import FactorProgram, create_factor_program


def _factor_with_signal(fn_signal: str) -> FactorProgram:
    """构造信号由 fn_signal(close, n) 决定的因子。"""
    code = f"""
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    n = len(close)
    return np.asarray({fn_signal}, dtype=float)
"""
    return create_factor_program(
        name="g11_sig",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="G11 测试"),
        source="manual",
    )


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """500 行合成 OHLCV（带日期索引）。"""
    rng = np.random.default_rng(42)
    n = 500
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": close + np.abs(rng.normal(0, 0.2, n)),
            "low": close - np.abs(rng.normal(0, 0.2, n)),
            "close": close,
            "volume": rng.integers(1000, 10000, n).astype(float),
        },
        index=dates,
    )


def _forward_returns(close: np.ndarray) -> np.ndarray:
    fwd = np.zeros(len(close))
    fwd[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    return fwd


def _reasons(result) -> str:
    """兼容 dict / 对象两种返回形态的 failure_reasons 提取。"""
    reasons = result.get("failure_reasons") if isinstance(result, dict) else getattr(result, "failure_reasons", None)
    return " ".join(reasons or [])


def test_turnover_daily_field_injected(sample_ohlcv):
    """评估链注入 turnover_daily 字段（信号翻转率 = mean(|Δsign|)/2）。"""
    factor = _factor_with_signal("(np.arange(n) % 2) * 2 - 1")  # 每日翻转 → 日换手 1.0
    bt = evaluate_backtest(factor, sample_ohlcv, _forward_returns(sample_ohlcv["close"].values))
    assert "turnover_daily" in bt
    assert abs(float(bt["turnover_daily"]) - 1.0) < 1e-6


def test_turnover_gate_disabled_by_default(sample_ohlcv, monkeypatch):
    """默认（factor_turnover_daily_max=None）→ 高换手因子不被换手门槛拒收。"""
    monkeypatch.setattr(
        "fts.config.get_config",
        lambda: type("Cfg", (), {"factor_turnover_daily_max": None})(),
    )
    factor = _factor_with_signal("(np.arange(n) % 2) * 2 - 1")
    result = EvaluationChain().evaluate(factor, sample_ohlcv, _forward_returns(sample_ohlcv["close"].values))
    assert "日换手" not in _reasons(result)


def test_turnover_gate_rejects_when_enabled(sample_ohlcv, monkeypatch):
    """配置 factor_turnover_daily_max=0.20 → 高换手因子失败原因含日换手项。"""
    monkeypatch.setattr(
        "fts.config.get_config",
        lambda: type("Cfg", (), {"factor_turnover_daily_max": 0.20})(),
    )
    factor = _factor_with_signal("(np.arange(n) % 2) * 2 - 1")  # 日换手 1.0 > 0.20
    result = EvaluationChain().evaluate(factor, sample_ohlcv, _forward_returns(sample_ohlcv["close"].values))
    assert "日换手" in _reasons(result)


def test_turnover_gate_passes_low_turnover(sample_ohlcv, monkeypatch):
    """配置启用时低换手因子不被换手门槛拒收。"""
    monkeypatch.setattr(
        "fts.config.get_config",
        lambda: type("Cfg", (), {"factor_turnover_daily_max": 0.20})(),
    )
    factor = _factor_with_signal(
        "np.clip((close - np.roll(close, 20)) / np.maximum(np.roll(close, 20), 1e-10) * 10, -1, 1)"
    )
    result = EvaluationChain().evaluate(factor, sample_ohlcv, _forward_returns(sample_ohlcv["close"].values))
    assert "日换手" not in _reasons(result)


# ── 横截面路径（v2.104.0+1 补：cross_section_evaluate_backtest 曾硬编码 turnover=0）──


def _cs_panel(n_symbols: int = 20, n_dates: int = 120, seed: int = 7) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        signal = rng.normal(0, 1, n_dates)
        rets = np.zeros(n_dates)
        rets[1:] = 0.02 * signal[:-1] + rng.normal(0, 0.005, n_dates - 1)
        close = 100 * np.exp(np.cumsum(rets))
        panel[f"S{i:03d}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000,
            },
            index=dates,
        )
    common = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in panel.values()])))
    return panel, common


def test_cross_section_turnover_injected():
    """横截面评估返回非零 turnover_daily/turnover_monthly（信号非恒定、有品种差异）。"""
    panel, dates = _cs_panel()
    # 动量信号（依赖 close，横截面有区分度）；日换手 = 信号翻转率，必然 > 0
    factor = _factor_with_signal(
        "np.clip((close - np.roll(close, 5)) / np.maximum(np.roll(close, 5), 1e-10) * 10, -1, 1)"
    )
    bt = cross_section_evaluate_backtest(factor, panel, dates)
    assert "turnover_daily" in bt
    assert float(bt["turnover_daily"]) > 0
    assert abs(float(bt["turnover_monthly"]) - float(bt["turnover_daily"]) * 42.0) < 1e-6


def test_cross_section_turnover_zero_for_constant_signal():
    """横截面评估对常数信号返回 turnover_daily=0（缺省兜底）。"""
    panel, dates = _cs_panel()
    factor = _factor_with_signal("np.sign(np.roll(close, 10) - close) * 0.0")  # 恒定 0
    bt = cross_section_evaluate_backtest(factor, panel, dates)
    assert float(bt.get("turnover_daily", 0.0) or 0.0) == 0.0


def test_cross_section_gate_rejects_when_enabled(monkeypatch):
    """横截面晋升判定（_evaluate_cross_section）在阈值开启时拒绝高换手因子。"""
    from fts.factor_engine.evolution_futures import EvolutionLoop

    loop = object.__new__(EvolutionLoop)
    panel, dates = _cs_panel()
    loop.cross_section_data = panel
    loop.cross_section_dates = dates
    loop.industry_map = None
    loop.cap_map = None
    monkeypatch.setattr(loop, "_build_barra_exposures", lambda: None)
    monkeypatch.setattr(loop, "_build_vol_map", lambda: None)
    monkeypatch.setattr(
        "fts.config.get_config",
        lambda: type("Cfg", (), {"factor_turnover_daily_max": 0.05})(),
    )
    # 日收益符号信号：随机游走下符号翻转频繁 → 日换手高（> 0.05）
    factor = _factor_with_signal("np.sign(np.diff(close, prepend=close[0]))")
    ev = loop._evaluate_cross_section(factor, "trace-g11-cs")
    assert "截面日换手" in " ".join(ev.get("failure_reasons") or [])
    assert ev.get("passed") is False


def test_cross_section_gate_disabled_by_default(monkeypatch):
    """默认（factor_turnover_daily_max=None）→ 高换手横截面因子不被换手门槛拒收。"""
    from fts.factor_engine.evolution_futures import EvolutionLoop

    loop = object.__new__(EvolutionLoop)
    panel, dates = _cs_panel()
    loop.cross_section_data = panel
    loop.cross_section_dates = dates
    loop.industry_map = None
    loop.cap_map = None
    monkeypatch.setattr(loop, "_build_barra_exposures", lambda: None)
    monkeypatch.setattr(loop, "_build_vol_map", lambda: None)
    monkeypatch.setattr(
        "fts.config.get_config",
        lambda: type("Cfg", (), {"factor_turnover_daily_max": None})(),
    )
    factor = _factor_with_signal("np.sign(np.diff(close, prepend=close[0]))")
    ev = loop._evaluate_cross_section(factor, "trace-g11-cs-off")
    assert "截面日换手" not in " ".join(ev.get("failure_reasons") or [])
