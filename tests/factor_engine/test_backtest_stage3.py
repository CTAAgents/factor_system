"""tests/factor_engine/test_backtest_stage3.py — B.2 回测流水线增强测试。

覆盖:
1. FactorScreener 筛选（等级/总分/状态/风格）
2. SignalGenerator 时序/横截面信号
3. PortfolioConstructor 等权/Sharpe/自适应加权
4. CostSimulator 成本模拟（品种差异化）
5. RiskAttributor 归因（贡献/VaR/ES）
6. ReportGenerator 报告生成
7. CapitalAllocator 四种资金模式
8. BacktestPipeline.run_batch 批量排名
9. BacktestPipelineBuilder 构建器
10. CLI backtest 子命令
"""

import numpy as np
import pandas as pd
from pathlib import Path

from fts.factor_engine import (
    BacktestInput,
    BacktestPipeline,
    BacktestPipelineBuilder,
    CapitalAllocator,
    CostSimulator,
    FactorScreener,
    PortfolioConstructor,
    ReportGenerator,
    RiskAttributor,
    SignalGenerator,
)
from fts.factor_engine.backtest_pipeline import BacktestResult


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """构造 OHLCV DataFrame（DatetimeIndex 风格）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 3000 + np.cumsum(rng.normal(0, 10, n))
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + rng.uniform(0, 3, n)
    low = np.minimum(open_, close) - rng.uniform(0, 3, n)
    volume = rng.uniform(1e4, 1e6, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_factor(code: str, factor_id: str = "test_fct", **extra) -> dict:
    factor = {
        "factor_id": factor_id,
        "name": factor_id,
        "code": code,
        "params": {},
    }
    factor.update(extra)
    return factor


_MOMENTUM_CODE = (
    "def factor_program(data, params):\n"
    "    import numpy as np\n"
    "    close = data['close']\n"
    "    n = len(close)\n"
    "    ret = np.zeros(n)\n"
    "    if n > 5:\n"
    "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
    "    return np.tanh(ret * 10)\n"
)


# ─── 1. FactorScreener ──────────────────────────────────


def test_screener_filters_by_grade():
    factors = [
        _make_factor("x", "f_a", grade="A", total_score=90.0),
        _make_factor("x", "f_b", grade="B", total_score=70.0),
        _make_factor("x", "f_c", grade="C", total_score=50.0),
    ]
    passed = FactorScreener().screen(factors=factors, min_grade="B")
    assert [f["factor_id"] for f in passed] == ["f_a", "f_b"]


def test_screener_filters_by_total_score_and_status():
    factors = [
        _make_factor("x", "f1", grade="A", total_score=80.0, status="active"),
        _make_factor("x", "f2", grade="A", total_score=30.0, status="active"),
        _make_factor("x", "f3", grade="A", total_score=90.0, status="deprecated"),
    ]
    passed = FactorScreener().screen(
        factors=factors,
        min_total_score=50.0,
        status=["active"],
    )
    assert [f["factor_id"] for f in passed] == ["f1"]


def test_screener_style_filter():
    factors = [
        _make_factor("x", "f1", grade="A", style_tags=["momentum"]),
        _make_factor("x", "f2", grade="A", style_tags=["value"]),
    ]
    passed = FactorScreener().screen(factors=factors, style_filter=["momentum"])
    assert [f["factor_id"] for f in passed] == ["f1"]


def test_screener_empty_graceful():
    """无匹配时优雅返回空列表。"""
    assert FactorScreener().screen(factors=[]) == []
    assert FactorScreener().screen(factors=None) == []


# ─── 2. SignalGenerator ─────────────────────────────────


def test_signal_generator_time_series():
    data = _make_ohlcv()
    factor = _make_factor(_MOMENTUM_CODE, "momentum")
    signal = SignalGenerator().generate(factor, data, "time_series")
    assert isinstance(signal, pd.Series)
    assert len(signal) == len(data)
    assert signal.min() >= -1.0 and signal.max() <= 1.0


def test_signal_generator_cross_section():
    panel = {f"sym{i}": _make_ohlcv(seed=i) for i in range(1, 6)}
    factor = _make_factor(
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    close = data['close']\n"
        "    n = len(close)\n"
        "    ret = np.zeros(n)\n"
        "    if n > 5:\n"
        "        ret[5:] = close[5:] / np.maximum(close[:-5], 1e-10) - 1.0\n"
        "    return ret\n",
        "momentum",
    )
    signals = SignalGenerator().generate_cross_section(factor, panel)
    assert len(signals) == 5
    for s in signals.values():
        assert s.min() >= -1.0 and s.max() <= 1.0


# ─── 3. PortfolioConstructor ────────────────────────────


def test_portfolio_equal_weight():
    idx = pd.date_range("2024-01-01", periods=50, freq="B")
    signals = {
        "a": pd.Series(np.full(50, 0.1), index=idx),
        "b": pd.Series(np.full(50, 0.1), index=idx),
    }
    pc = PortfolioConstructor()
    result = pc.construct(signals, weight_method="equal")
    assert set(result.weights.keys()) == {"a", "b"}
    assert abs(result.weights["a"] - 0.5) < 1e-9
    assert len(result.portfolio_returns) > 0
    assert abs(result.portfolio_returns.iloc[0] - 0.1) < 1e-9


def test_portfolio_sharpe_weight():
    idx = pd.date_range("2024-01-01", periods=50, freq="B")
    signals = {
        "a": pd.Series(np.full(50, 0.1), index=idx),
        "b": pd.Series(np.full(50, 0.1), index=idx),
    }
    metrics = {"a": {"sharpe": 3.0}, "b": {"sharpe": 1.0}}
    pc = PortfolioConstructor()
    result = pc.construct(signals, weight_method="sharpe", factor_metrics=metrics)
    assert result.weights["a"] > result.weights["b"]


def test_portfolio_adaptive_weight():
    idx = pd.date_range("2024-01-01", periods=50, freq="B")
    signals = {
        "a": pd.Series(np.full(50, 0.1), index=idx),
        "b": pd.Series(np.full(50, 0.1), index=idx),
    }
    metrics = {"a": {"sharpe": 2.0}, "b": {"sharpe": 1.0}}
    regime = {"regime": "bull"}
    pc = PortfolioConstructor()
    result = pc.construct(signals, weight_method="adaptive", factor_metrics=metrics, regime=regime)
    assert set(result.weights.keys()) == {"a", "b"}


def test_portfolio_empty_signals():
    pc = PortfolioConstructor()
    result = pc.construct({})
    assert result.weights == {}
    assert len(result.portfolio_returns) == 0


# ─── 4. CostSimulator ───────────────────────────────────


def test_cost_simulator_basic():
    sim = CostSimulator()
    signal = np.array([0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0])
    result = sim.simulate(signal, market="futures")
    assert result.total_cost_bps > 0
    assert result.turnover >= 0
    assert set(result.cost_by_type.keys()) == {"commission", "slippage", "impact"}


def test_cost_simulator_symbol_diff():
    sim = CostSimulator(
        symbol_commission={"RB0": 0.5, "TA0": 2.0},
        symbol_slippage={"RB0": 0.3},
    )
    assert sim.get_commission("RB0") == 0.5
    assert sim.get_commission("TA0") == 2.0
    assert sim.get_commission("OTHER") > 0  # 回退默认
    assert sim.get_slippage("RB0") == 0.3


# ─── 5. RiskAttributor ──────────────────────────────────


def test_risk_attributor_basic():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=120, freq="B")
    returns = pd.Series(rng.normal(0.001, 0.02, 120), index=idx)
    factor_returns = pd.DataFrame(
        {
            "f1": rng.normal(0.001, 0.02, 120),
            "f2": rng.normal(0.0005, 0.01, 120),
        },
        index=idx,
    )
    attr = RiskAttributor()
    report = attr.attribute(returns, factor_returns=factor_returns)
    assert set(report.factor_contributions.keys()) == {"f1", "f2"}
    assert report.var_95 < 0
    assert report.es_95 <= report.var_95
    assert report.realized_vol > 0


def test_risk_attributor_empty():
    report = RiskAttributor().attribute(pd.Series(dtype=float))
    assert report.factor_contributions == {}


# ─── 6. ReportGenerator ─────────────────────────────────


def test_report_generator_markdown(tmp_path):
    data = _make_ohlcv()
    factor = _make_factor(_MOMENTUM_CODE, "momentum")
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.success
    gen = ReportGenerator()
    path = Path(str(tmp_path)) / gen.generate(result.output, output_dir=str(tmp_path)).split("/")[-1]
    content = path.read_text(encoding="utf-8")
    assert "回测摘要" in content
    assert "净值曲线" in content
    assert "回撤" in content
    assert "IC" in content


def test_report_generator_missing_data(tmp_path):
    """无净值数据时优雅降级。"""
    gen = ReportGenerator()
    path = gen.generate(
        {"factor_id": "x", "metrics": {}, "equity_curve": None},
        output_dir=str(tmp_path),
    )
    content = Path(path).read_text(encoding="utf-8")
    assert "无净值数据" in content


# ─── 7. CapitalAllocator ────────────────────────────────


def test_capital_fixed():
    alloc = CapitalAllocator()
    result = alloc.allocate(pd.Series(dtype=float), total_capital=1_000_000, mode="fixed")
    assert result.leverage == 1.0
    assert result.allocated_capital["portfolio"] == 1_000_000


def test_capital_vol_target():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0, 0.01, 252))
    alloc = CapitalAllocator()
    result = alloc.allocate(returns, total_capital=1_000_000, mode="vol_target", target_volatility=0.15)
    assert 0.0 < result.leverage <= 2.0


def test_capital_risk_parity_multi_asset():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    df = pd.DataFrame(
        {
            "a": rng.normal(0.001, 0.02, 100),
            "b": rng.normal(0.0005, 0.01, 100),
            "c": rng.normal(0.0002, 0.008, 100),
        },
        index=idx,
    )
    alloc = CapitalAllocator()
    result = alloc.allocate(df, total_capital=1_000_000, mode="risk_parity")
    assert set(result.weights.keys()) == {"a", "b", "c"}
    assert abs(sum(result.weights.values()) - 1.0) < 1e-6


def test_capital_kelly():
    alloc = CapitalAllocator()
    result = alloc.allocate(
        pd.Series(dtype=float), total_capital=1_000_000, mode="kelly", win_rate=0.6, payoff_ratio=1.5
    )
    f = result.details["fraction"]
    assert 0.0 < f <= 2.0
    assert result.allocated_capital["portfolio"] == 1_000_000 * f


def test_capital_unknown_mode_fallback():
    alloc = CapitalAllocator()
    result = alloc.allocate(pd.Series(dtype=float), total_capital=100, mode="unknown")
    assert result.mode == "vol_target"


# ─── 8. BacktestPipeline.run_batch ──────────────────────


def test_run_batch_ranking():
    data = _make_ohlcv()
    factors = [
        _make_factor(_MOMENTUM_CODE, "f_strong"),
        _make_factor("output = close / np.maximum(open, 1e-10) - 1.0", "f_weak"),
    ]
    results = BacktestPipeline().run_batch(factors, data)
    assert len(results) == 2
    ranks = [r.rank for r in results]
    assert sorted(ranks) == [1, 2]
    assert all(r.report is not None for r in results)


def test_run_batch_with_failure():
    data = _make_ohlcv()
    factors = [
        _make_factor(_MOMENTUM_CODE, "good"),
        _make_factor("", "bad"),  # 无 code → 失败
    ]
    results = BacktestPipeline().run_batch(factors, data)
    assert len(results) == 2
    by_id = {r.factor_id: r for r in results}
    assert by_id["good"].report is not None
    assert by_id["bad"].report is None
    assert by_id["bad"].error is not None
    # 失败因子排最后
    assert by_id["bad"].rank == 2


def test_backtest_result_to_dict():
    r = BacktestResult(factor_id="x", rank=1)
    assert r.to_dict()["rank"] == 1
    assert r.sharpe == 0.0


# ─── 9. BacktestPipelineBuilder ─────────────────────────


def test_builder_chain_config():
    builder = (
        BacktestPipelineBuilder()
        .set_period("2020-01-01", "2024-12-31")
        .set_signal_type("cross_section")
        .set_weight_method("adaptive")
        .set_capital_mode("vol_target", target_vol=0.15)
        .enable_cost_model(True)
        .enable_risk_attribution(True)
        .set_forward_period(5)
        .set_initial_capital(2_000_000)
    )
    cfg = builder.get_config()
    assert cfg["start"] == "2020-01-01"
    assert cfg["end"] == "2024-12-31"
    assert cfg["signal_type"] == "cross_section"
    assert cfg["weight_method"] == "adaptive"
    assert cfg["capital_mode"] == "vol_target"
    assert cfg["capital_kwargs"] == {"target_vol": 0.15}
    assert cfg["forward_period"] == 5
    assert cfg["initialization_capital"] == 2_000_000


def test_builder_build_pipeline_runs():
    pipeline = BacktestPipelineBuilder().build()
    assert isinstance(pipeline, BacktestPipeline)
    data = _make_ohlcv()
    factor = _make_factor(_MOMENTUM_CODE, "momentum")
    result = pipeline.run(BacktestInput(factor=factor, data=data))
    assert result.success


# ─── 10. CLI backtest ───────────────────────────────────


def test_cli_has_backtest_subcommands():
    from fts.cli import build_parser

    parser = build_parser()
    for sub in ["run", "batch", "compare"]:
        argv = ["backtest", sub]
        if sub == "compare":
            argv.append("--factor-ids")
            argv.append("a,b")
        elif sub == "run":
            argv.append("--factor-id")
            argv.append("x")
        args = parser.parse_args(argv)
        assert args.subcommand == sub
        assert callable(args.func)
