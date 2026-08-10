"""tests/factor_engine/test_barra_l2_integration.py — L2 Barra 风格暴露控制测试（GAP-I304）。

覆盖:
    1. _build_barra_exposures: 横截面模式自动构建 10 风格暴露
    2. 缓存复用（避免每因子重复计算）
    3. 配置开关 l2_barra_style_neutral 关闭时不构建
    4. 面板字段缺失 / 非横截面 / 构建异常降级不阻断
    5. _evaluate_cross_section 端到端接入 style_exposures
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from fts.factor_engine.barra.barra_style import STYLE_FACTOR_NAMES

if TYPE_CHECKING:
    from fts.factor_engine.evolution_loop import EvolutionLoop


def _make_fundamental_panel(
    n_stocks: int = 10,
    n_dates: int = 200,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """构造带基本面字段的股票面板（与 test_barra 同构）。"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        close = 50 + np.cumsum(np.random.randn(n_dates) * 0.3)
        df = pd.DataFrame(
            {
                "open": close + np.random.randn(n_dates) * 0.1,
                "high": close + np.abs(np.random.randn(n_dates)) * 0.3,
                "low": close - np.abs(np.random.randn(n_dates)) * 0.3,
                "close": close,
                "volume": np.random.randint(1000, 10000, n_dates).astype(float),
                "total_market_cap": np.full(n_dates, 1e10 + i * 1e9),
                "pb": np.full(n_dates, 1.0 + (i % 5) * 0.5),
                "turnover_rate": np.full(n_dates, 0.01 + (i % 7) * 0.005),
                "pe_ttm": np.full(n_dates, 8.0 + (i % 10) * 3.0),
                "roe": np.full(n_dates, 0.08 + (i % 6) * 0.02),
                "revenue_growth": np.full(n_dates, 0.05 + (i % 8) * 0.03),
                "profit_growth": np.full(n_dates, 0.03 + (i % 8) * 0.02),
                "debt_to_equity": np.full(n_dates, 0.3 + (i % 9) * 0.1),
            },
            index=dates,
        )
        panel[f"STK_{i}"] = df
    return panel, dates


def _make_loop(
    panel: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
    tmp_memory_dir,
    tmp_elite_dir,
) -> EvolutionLoop:
    """构造横截面模式 EvolutionLoop（股票市场）。"""
    from fts.factor_engine.evolution_loop import EvolutionLoop

    return EvolutionLoop(
        data=panel["STK_0"],
        forward_returns=None,
        elite_dir=tmp_elite_dir,
        memory_dir=tmp_memory_dir,
        cross_section_data=panel,
        cross_section_dates=dates,
        market="stock",
    )


class TestBuildBarraExposures:
    def test_non_cross_section_returns_none(self, tmp_memory_dir, tmp_elite_dir, sample_ohlcv, forward_returns):
        """非横截面模式（cross_section_data=None）返回 None，不构建。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        loop = EvolutionLoop(
            data=sample_ohlcv,
            forward_returns=forward_returns,
            elite_dir=tmp_elite_dir,
            memory_dir=tmp_memory_dir,
        )
        assert loop._build_barra_exposures() is None

    def test_cross_section_builds_exposures(self, tmp_memory_dir, tmp_elite_dir):
        """横截面模式自动构建 10 风格暴露（含可用风格，键与 STYLE_FACTOR_NAMES 对齐）。"""
        panel, dates = _make_fundamental_panel()
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        exposures = loop._build_barra_exposures()
        assert exposures is not None
        assert set(exposures.keys()) == set(STYLE_FACTOR_NAMES)
        # 基本面字段齐全 → 至少部分风格可用（非全 NaN）
        n_available = sum(1 for s in exposures.values() if not s.isna().all().all())
        assert n_available >= 1

    def test_result_cached_single_build(self, tmp_memory_dir, tmp_elite_dir, monkeypatch):
        """暴露结果缓存：二次调用不重复计算（同一 dict 对象）。"""
        panel, dates = _make_fundamental_panel()
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        first = loop._build_barra_exposures()

        called = {"n": 0}

        def _fake_engine(*args, **kwargs):
            called["n"] += 1
            return object()  # 若被调用将破坏 dict 契约 → 证明未走真实路径

        # _build_barra_exposures 内部 `from .barra.barra_style import BarraStyleEngine`
        monkeypatch.setattr(
            "fts.factor_engine.barra.barra_style.BarraStyleEngine",
            _fake_engine,
        )
        second = loop._build_barra_exposures()
        assert first is second
        assert called["n"] == 0  # 缓存命中，未重建

    def test_config_disabled_returns_none(self, tmp_memory_dir, tmp_elite_dir, monkeypatch):
        """l2_barra_style_neutral=False 时返回 None（不构建、不阻断）。"""
        from fts.config.settings import FTSConfig

        panel, dates = _make_fundamental_panel()
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(l2_barra_style_neutral=False),
        )
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        assert loop._build_barra_exposures() is None

    def test_build_failure_degrades_gracefully(self, tmp_memory_dir, tmp_elite_dir, monkeypatch):
        """构建异常降级返回 None（不阻断评估链）。"""
        from fts.config.settings import FTSConfig

        panel, dates = _make_fundamental_panel()
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: FTSConfig(l2_barra_style_neutral=True),
        )
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        monkeypatch.setattr(
            "fts.factor_engine.barra.barra_style.BarraStyleEngine",
            MagicMockSideEffect,
        )
        assert loop._build_barra_exposures() is None

    def test_ohlcv_only_panel_no_crash(self, tmp_memory_dir, tmp_elite_dir):
        """仅 OHLCV（无基本面字段）面板：构建不抛异常（风格多 NaN，由中性化器跳过）。"""
        n_dates = 200
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        rng = np.random.default_rng(3)
        panel: dict[str, pd.DataFrame] = {}
        for i in range(10):
            close = 100 + np.cumsum(rng.normal(0, 0.5, n_dates))
            panel[f"STK_{i}"] = pd.DataFrame(
                {
                    "open": close,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": np.full(n_dates, 1e6),
                },
                index=dates,
            )
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        exposures = loop._build_barra_exposures()
        # 无基本面字段 → 不崩溃，返回 dict（风格多为 NaN）
        assert exposures is not None


class MagicMockSideEffect:
    """模拟 BarraStyleEngine 构造失败。"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("模拟引擎失败")


class TestCrossSectionEvaluateBarra:
    def test_evaluate_cross_section_injects_style_exposures(self, tmp_memory_dir, tmp_elite_dir):
        """_evaluate_cross_section 接入 style_exposures 后评估仍正常产出指标。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        panel, dates = _make_fundamental_panel()
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        factor = create_factor_program(
            name="l2_barra_integ",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(
                theory=4, behavioral=3, microstructure=3, institutional=4, narrative="L2 Barra 集成"
            ),
            source="manual",
        )
        ev = loop._evaluate_cross_section(factor, "l2_test_trace")
        assert ev["passed"] in (True, False)  # 评估正常完成
        assert "ic" in ev["level_1_backtest"]

    def test_barra_build_used_by_evaluate(self, tmp_memory_dir, tmp_elite_dir, monkeypatch):
        """_evaluate_cross_section 调用 _build_barra_exposures（断言实际被消费）。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        panel, dates = _make_fundamental_panel()
        loop = _make_loop(panel, dates, tmp_memory_dir, tmp_elite_dir)
        calls = {"n": 0}
        orig = loop._build_barra_exposures

        def _spy():
            calls["n"] += 1
            return orig()

        loop._build_barra_exposures = _spy
        factor = create_factor_program(
            name="l2_barra_spy",
            code=(
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="spy"),
            source="manual",
        )
        loop._evaluate_cross_section(factor, "l2_spy_trace")
        assert calls["n"] >= 1
