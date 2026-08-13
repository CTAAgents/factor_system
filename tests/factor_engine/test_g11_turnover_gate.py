"""tests/factor_engine/test_g11_turnover_gate.py — G11 日换手硬剔除测试（35-gap-closure-plan §5.4）。

覆盖: turnover_daily 字段注入 / 配置门槛开启时高换手因子拒收 / 默认关闭不改变准入。
HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import EconomicLogic, FactorSignature
from fts.factor_engine.evaluation_chain import EvaluationChain, evaluate_backtest
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
