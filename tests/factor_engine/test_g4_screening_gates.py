"""tests/factor_engine/test_g4_screening_gates.py — G4 筛选硬门槛测试（35-gap-closure-plan §4.1）。

覆盖: walk_forward 跨窗口 ICIR 门槛（恒定 IC 通过 / 高波动 IC 拒绝）/ evaluation 链 G4 字段注入。
HARNESS §测试随重构。
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from fts.factor_engine.contracts import EconomicLogic, FactorSignature
from fts.factor_engine.factor_program import FactorProgram, create_factor_program
from fts.factor_engine.walk_forward import WalkForwardOptimizer


def make_data(n_rows: int, seed: int = 42) -> pd.DataFrame:
    """生成 n_rows 行的合成 DataFrame（带 DatetimeIndex）。"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    return pd.DataFrame(
        {
            "close": 100 + np.cumsum(np.random.randn(n_rows) * 0.5),
            "volume": np.random.randint(1_000, 10_000, n_rows).astype(float),
        },
        index=dates,
    )


def make_evaluate_fn(
    ic: float = 0.0,
    sharpe: float = 0.0,
    turnover: float = 0.0,
) -> Callable[[pd.DataFrame, pd.DataFrame], dict[str, float]]:
    """固定 IC 的 evaluate_fn。"""

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        return {"ic": ic, "sharpe": sharpe, "turnover": turnover}

    return _fn


def _opt(**overrides: float) -> WalkForwardOptimizer:
    """3 窗口走航配置（与 test_walk_forward 同口径）。"""
    cfg: dict[str, float] = {
        "window_years": 1,
        "step_months": 3,
        "min_oos_months": 3,
        "n_windows": 3,
        "min_ic_consistency": 0.5,
        "max_ic_volatility": 0.3,
        "min_oos_icir": 0.25,
    }
    cfg.update(overrides)
    return WalkForwardOptimizer(cfg)


# ─── walk_forward 跨窗口 ICIR 门槛 ─────────────────────────


def test_wf_constant_ic_passes_gate():
    """IC 跨窗口恒定（零波动）→ 完全稳定，ICIR=999 通过门槛。"""
    opt = _opt()
    res = opt.evaluate(make_data(1000), make_evaluate_fn(ic=0.05, sharpe=1.5, turnover=0.1))
    assert res["n_windows_completed"] == 3
    assert res["icir"] == 999.0
    assert res["passed"] is True


def test_wf_stable_ic_passes_gate():
    """IC 稳定但略有波动 → ICIR 高 → 通过。"""
    ic_list = [0.05, 0.04, 0.05]
    call: list[int] = [0]

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        i = call[0]
        call[0] += 1
        ic = ic_list[i] if i < len(ic_list) else 0.0
        return {"ic": ic, "sharpe": 1.0, "turnover": 0.1}

    res = _opt().evaluate(make_data(1000), _fn)
    assert res["passed"] is True
    assert abs(res["icir"]) >= 0.25


def test_wf_high_ic_volatility_rejected_by_gate():
    """IC 交替正负（均值小/波动大）→ ICIR<0.25 → 即使一致性与波动达标也被门槛拦截。"""
    ic_list = [0.05, -0.05, 0.04]  # mean≈0.013, std≈0.055 → icir≈0.24 <0.25
    call: list[int] = [0]

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        i = call[0]
        call[0] += 1
        ic = ic_list[i] if i < len(ic_list) else 0.0
        return {"ic": ic, "sharpe": 1.0, "turnover": 0.1}

    res = _opt().evaluate(make_data(1000), _fn)
    assert res["n_windows_completed"] == 3
    # 一致性 2/3=0.667 ≥0.5 ✓，vol≈0.055 ≤0.3 ✓ —— 仅 ICIR 门槛拦截
    assert res["ic_consistency"] >= 0.5
    assert res["ic_volatility"] <= 0.3
    assert abs(res["icir"]) < 0.25
    assert res["passed"] is False


def test_wf_icir_gate_configurable():
    """min_oos_icir 可配置：放宽到 0.10 后同一序列通过。"""
    ic_list = [0.05, -0.05, 0.04]  # icir≈0.24
    call: list[int] = [0]

    def _fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict[str, float]:
        i = call[0]
        call[0] += 1
        ic = ic_list[i] if i < len(ic_list) else 0.0
        return {"ic": ic, "sharpe": 1.0, "turnover": 0.1}

    res = _opt(min_oos_icir=0.10).evaluate(make_data(1000), _fn)
    assert res["passed"] is True


def _good_factor() -> FactorProgram:
    """与未来收益率正相关的动量因子（与 test_evaluation_chain.good_factor 等价）。"""
    code = """
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    n = len(close)
    signal = np.zeros(n)
    for i in range(5, n):
        signal[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)
    return np.clip(signal * 10, -1.0, 1.0)
"""
    return create_factor_program(
        name="momentum_5d_g4",
        code=code,
        params={"window": 5},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        economic_logic=EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4, narrative="5日动量因子（G4 测试）"
        ),
        source="manual",
    )


# ─── evaluation 链 G4 字段（sign_flip_half_split / icir_block）──


def test_backtest_metrics_include_icir_block(sample_ohlcv, forward_returns):
    """时序评估链写入块级 ICIR（G4 硬门槛口径）。"""
    from fts.factor_engine.evaluation_chain import evaluate_backtest

    bt = evaluate_backtest(_good_factor(), sample_ohlcv, forward_returns)
    assert "icir_block" in bt
    assert abs(float(bt["icir_block"])) > 0
    assert "sign_flip_half_split" in bt


def test_backtest_metrics_sign_flip_field_type(sample_ohlcv, forward_returns):
    """sign_flip_half_split 为布尔类型。"""
    from fts.factor_engine.evaluation_chain import evaluate_backtest

    bt = evaluate_backtest(_good_factor(), sample_ohlcv, forward_returns)
    assert isinstance(bt["sign_flip_half_split"], (bool, np.bool_))


# ─── 横截面路径 G4（v2.104.0+5 补：_evaluate_cross_section 曾缺 ICIR 硬门槛）──


def _cs_panel(n_symbols: int = 20, n_dates: int = 120, seed: int = 7, coef: float = 0.02) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """合成横截面面板；coef 为收益对信号系数（越大信号预测性越强 → ICIR 越高）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        signal = rng.normal(0, 1, n_dates)
        rets = np.zeros(n_dates)
        rets[1:] = coef * signal[:-1] + rng.normal(0, 0.005, n_dates - 1)
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


def _cs_momentum(lookback: int = 5) -> FactorProgram:
    code = """
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    n = len(close)
    signal = np.zeros(n)
    for i in range(%d, n):
        signal[i] = (close[i] - close[i-%d]) / max(close[i-%d], 1e-10)
    return np.clip(signal * 10, -1.0, 1.0)
""" % (lookback, lookback, lookback)
    return create_factor_program(
        name="cs_momentum_g4",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=lookback + 5),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="横截面动量（G4 测试）"),
        source="manual",
    )


def _cs_loop(monkeypatch, panel: dict[str, pd.DataFrame], dates: pd.DatetimeIndex):
    """构造 object.__new__ 的 EvolutionLoop（横截面评估最小装配）。"""
    from fts.factor_engine.evolution_futures import EvolutionLoop

    loop = object.__new__(EvolutionLoop)
    loop.cross_section_data = panel
    loop.cross_section_dates = dates
    loop.industry_map = None
    loop.cap_map = None
    loop.holdout_panel = None  # GAP-160: 最小装配补盲测池属性（__init__ 才设置）
    loop._prior_evaluations = []
    monkeypatch.setattr(loop, "_build_barra_exposures", lambda: None)
    monkeypatch.setattr(loop, "_build_vol_map", lambda: None)
    return loop


def test_cross_section_metrics_include_icir():
    """横截面评估返回非零 icir（日度 IC 序列 ICIR，G4 硬门槛口径）。"""
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    panel, dates = _cs_panel(coef=0.08)
    bt = cross_section_evaluate_backtest(_cs_momentum(), panel, dates)
    assert "icir" in bt
    assert abs(float(bt["icir"])) > 0


def test_cross_section_g4_rejects_low_icir(monkeypatch):
    """横截面晋升判定（_evaluate_cross_section）拒绝低显著性因子（|ic_t|<1.65）。"""
    panel, dates = _cs_panel()
    loop = _cs_loop(monkeypatch, panel, dates)
    # 1 日反转信号：横截面 IC≈0、ICIR≈0 → ic_t≈0 < 1.65 被拒
    code = """
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    n = len(close)
    signal = np.zeros(n)
    for i in range(1, n):
        signal[i] = np.sign(close[i-1] - close[i])
    return signal
"""
    factor = create_factor_program(
        name="cs_rev_g4",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=2),
        economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="横截面反转（G4 测试）"),
        source="manual",
    )
    ev = loop._evaluate_cross_section(factor, "trace-g4-cs-low")
    assert "截面|ic_t|" in " ".join(ev.get("failure_reasons") or [])
    assert ev.get("passed") is False


def test_cross_section_g4_passes_high_icir(monkeypatch):
    """横截面晋升判定放行高显著性因子（强预测动量，|ic_t|≥1.65）。"""
    panel, dates = _cs_panel(coef=0.08)  # 强预测面板 → 动量 ICIR≈0.45、ic_t≈4.7
    loop = _cs_loop(monkeypatch, panel, dates)
    ev = loop._evaluate_cross_section(_cs_momentum(), "trace-g4-cs-high")
    reasons = " ".join(ev.get("failure_reasons") or [])
    assert "截面|ic_t|" not in reasons


def test_cross_section_g4_t_stat_block_count_aware(monkeypatch):
    """G4 块数感知：同信号短样本 |ICIR|≥0.30 但 |ic_t|<1.65 → t 门槛拦截；
    长样本有效截面期数足够 → ic_t≥1.65 放行。"""
    # 短面板（25 期）：IC=0.14/sharpe=9.1 均过既有门槛，|ICIR|=0.66≥0.30（旧口径会放行），
    # 但截面期数少 → |ic_t|=0.94<1.65 → 新 t 门槛拒绝
    panel, dates = _cs_panel(n_dates=25, coef=0.03)
    loop = _cs_loop(monkeypatch, panel, dates)
    ev = loop._evaluate_cross_section(_cs_momentum(), "trace-g4-cs-short")
    reasons = " ".join(ev.get("failure_reasons") or [])
    assert "截面|ic_t|" in reasons
    assert "截面|ICIR|" not in reasons  # 旧 ICIR 口径本身不触发——拒绝完全来自 t 门槛
    assert ev.get("passed") is False
    # 长面板（120 期）：同因子 |ic_t|≈2.05≥1.65 → t 门槛不触发
    panel2, dates2 = _cs_panel(n_dates=120, coef=0.03)
    loop2 = _cs_loop(monkeypatch, panel2, dates2)
    ev2 = loop2._evaluate_cross_section(_cs_momentum(), "trace-g4-cs-long")
    assert "截面|ic_t|" not in " ".join(ev2.get("failure_reasons") or [])
