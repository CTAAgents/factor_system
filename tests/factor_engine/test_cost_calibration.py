"""成本实证化测试（C7，v2.100.1）。

覆盖:
    - load_market_cost_config（env 覆盖 / overrides 优先 / 缺省回落 / 非法 env 忽略）
    - 融资成本项（开启后成本↑净夏普↓ / 利率单调 / margin 差异化 / 默认关闭）
    - AdjustedMetrics 分项字段（financing_cost_bps / cost_breakdown）
    - 标定 fit_impact_curve（log-log 幂回归还原 / 样本不足降级）
    - collect_slippage_samples（缺列 / 行数不足 / 正常）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.cost_model import (  # noqa: E402
    AdjustedMetrics,
    TransactionCostModel,
    load_market_cost_config,
)

sys.path.insert(0, str(_FTS_ROOT / "scripts"))
import calibrate_impact_cost as calib  # noqa: E402


def _make_signal(n: int = 100) -> np.ndarray:
    rng = np.random.default_rng(0)
    sig = np.clip(rng.normal(0.0, 0.3, n), -1.0, 1.0)
    sig[0] = 0.0
    return sig


def _make_metrics() -> dict:
    return {"sharpe": 2.0, "ic": 0.06}


@pytest.fixture(autouse=True)
def _clean_cost_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理 FTS_COST_* 环境变量，保证测试隔离。"""
    for key in (
        "FTS_COST_SLIPPAGE_BPS", "FTS_COST_COMMISSION_BPS", "FTS_COST_IMPACT_BPS_PER_PCT",
        "FTS_COST_MIN_COST_BPS", "FTS_COST_ROLL_COST_BPS", "FTS_COST_MARGIN_RATE",
        "FTS_COST_FINANCING_RATE_ANNUAL",
    ):
        monkeypatch.delenv(key, raising=False)


class TestLoadMarketCostConfig:
    def test_default_futures(self) -> None:
        cfg = load_market_cost_config("futures")
        assert cfg["slippage_bps"] == 0.5
        assert cfg["margin_rate"] == 0.12
        assert cfg["financing_rate_annual"] == 0.0

    def test_stock_margin_full(self) -> None:
        cfg = load_market_cost_config("stock")
        assert cfg["margin_rate"] == 1.0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_COST_SLIPPAGE_BPS", "5.0")
        cfg = load_market_cost_config("futures")
        assert cfg["slippage_bps"] == 5.0

    def test_overrides_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_COST_SLIPPAGE_BPS", "5.0")
        cfg = load_market_cost_config("futures", overrides={"slippage_bps": 9.0})
        assert cfg["slippage_bps"] == 9.0

    def test_invalid_env_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_COST_SLIPPAGE_BPS", "abc")
        cfg = load_market_cost_config("futures")
        assert cfg["slippage_bps"] == 0.5  # 回落默认

    def test_unknown_market_falls_back_futures(self) -> None:
        cfg = load_market_cost_config("bogus")
        assert cfg["market"] == "futures"


class TestFinancingCost:
    def _adjusted(self, **cfg_overrides) -> AdjustedMetrics:
        config = load_market_cost_config("futures", overrides=cfg_overrides)
        model = TransactionCostModel(config=config, market_configs={})
        return model.adjust(_make_metrics(), _make_signal(), volume=np.ones(100), market="futures")

    def test_disabled_by_default(self) -> None:
        adj = self._adjusted()
        assert adj["financing_cost_bps"] == 0.0
        assert adj["cost_breakdown"]["financing_bps"] == 0.0

    def test_enabled_increases_cost_and_lowers_net(self) -> None:
        base = self._adjusted()
        fin = self._adjusted(financing_rate_annual=0.05, margin_rate=0.12)
        assert fin["total_cost_bps"] > base["total_cost_bps"]
        assert fin["financing_cost_bps"] > 0
        assert fin["net_sharpe"] < base["net_sharpe"]

    def test_rate_monotonic(self) -> None:
        low = self._adjusted(financing_rate_annual=0.03, margin_rate=0.12)
        high = self._adjusted(financing_rate_annual=0.10, margin_rate=0.12)
        assert high["total_cost_bps"] > low["total_cost_bps"]

    def test_margin_differentiated(self) -> None:
        futures = self._adjusted(financing_rate_annual=0.05, margin_rate=0.12)
        stock = self._adjusted(financing_rate_annual=0.05, margin_rate=1.0)
        assert stock["financing_cost_bps"] > futures["financing_cost_bps"]

    def test_breakdown_fields(self) -> None:
        adj = self._adjusted(financing_rate_annual=0.05, margin_rate=0.12)
        bd = adj["cost_breakdown"]
        assert set(bd) == {"slippage_bps", "commission_bps", "impact_bps", "financing_bps", "roll_bps"}
        assert all(v >= 0 for v in bd.values())


class TestFitImpactCurve:
    def test_recovers_power_law(self) -> None:
        rng = np.random.default_rng(7)
        a_true, b_true = 1.5, 0.5
        xs = np.linspace(0.01, 0.5, 50)
        ys = a_true * xs ** b_true * (1 + rng.normal(0, 0.02, len(xs)))
        samples = [(float(x), float(y)) for x, y in zip(xs, ys)]
        curve = calib.fit_impact_curve(samples)
        assert curve is not None
        assert abs(curve["a"] - a_true) < 0.15
        assert abs(curve["b"] - b_true) < 0.1
        assert curve["n"] == 50

    def test_insufficient_samples(self) -> None:
        assert calib.fit_impact_curve([(0.01, 1.0), (0.02, 1.5)]) is None

    def test_flat_input_none(self) -> None:
        assert calib.fit_impact_curve([(0.01, 1.0)] * 10) is None

    def test_impact_at_1pct(self) -> None:
        # 单位幂：impact = 2.0 × pct → 1% 处 = 0.02
        samples = [(p, 2.0 * p) for p in np.linspace(0.005, 0.3, 40)]
        curve = calib.fit_impact_curve(samples)
        assert curve is not None
        assert abs(curve["impact_at_1pct"] - 0.02) < 0.01


class TestCollectSlippageSamples:
    def _df(self, n: int = 60) -> pd.DataFrame:
        rng = np.random.default_rng(3)
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame(
            {
                "close": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "volume": rng.integers(1000, 5000, n),
            }
        )

    def test_missing_columns(self) -> None:
        assert calib.collect_slippage_samples(pd.DataFrame({"close": [1.0, 2.0]})) == []

    def test_too_short(self) -> None:
        assert calib.collect_slippage_samples(self._df(5)) == []

    def test_normal(self) -> None:
        samples = calib.collect_slippage_samples(self._df(60))
        assert len(samples) > 0
        for pct, bps in samples:
            assert pct > 0 and bps > 0

    def test_none_df(self) -> None:
        assert calib.collect_slippage_samples(None) == []


class TestRunCalibration:
    def test_integration_with_fake_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fts import data_futures as df_mod

        class _FakeProvider:
            def get_ohlcv(self, symbol: str, days: int = 120) -> pd.DataFrame:
                rng = np.random.default_rng(hash(symbol) % 1000)
                n = 60
                close = 100 + np.cumsum(rng.normal(0, 1, n))
                return pd.DataFrame(
                    {
                        "close": close,
                        "high": close + 0.5,
                        "low": close - 0.5,
                        "volume": rng.integers(1000, 5000, n),
                    }
                )

        monkeypatch.setattr(df_mod, "FuturesDataProvider", _FakeProvider)
        report = calib.run_calibration(["RB0", "CU0"], days=60, use_dynamic_pool=False)
        assert report["n_samples"] > 0
        assert report["curve"] is not None
        assert report["recommendation"] is not None
        assert "impact_bps_per_pct" in report["recommendation"]
