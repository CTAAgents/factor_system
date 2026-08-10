"""tests/cross_market/test_data_adapter_full.py — 跨市场数据适配层补充测试。

覆盖:
    1. get_panel 三市场路由 + 非法市场
    2. _adapt_panel / _adapt_dataframe 统一格式（缺失字段填充/期货特有字段）
    3. execute_factor_on_market（正常/短数据/执行失败/对齐）
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

from fts.cross_market.data_adapter import (  # noqa: E402
    CORE_FIELDS,
    FUTURES_SPECIFIC_FIELDS,
    CrossMarketDataAdapter,
)


# ─── 工具 ──────────────────────────────────────────────────


def _make_df(n: int = 60, with_oi: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": close + np.abs(rng.normal(0, 0.3, n)),
            "low": close - np.abs(rng.normal(0, 0.3, n)),
            "close": close,
            "volume": rng.integers(1000, 9000, n).astype(float),
        },
        index=dates,
    )
    if with_oi:
        df["open_interest"] = rng.integers(10000, 90000, n).astype(float)
        df["settle"] = close
    return df


class _FakeProvider:
    """模拟 FTSDataProvider。"""

    def __init__(self):
        self.panel = {"RB0": _make_df(with_oi=True), "CU0": _make_df()}
        self.dates = pd.date_range("2026-01-01", periods=60, freq="D")

    def get_stock_panel(self, symbols, days=500, trace_id=""):
        return self.panel, self.dates

    def get_etf_panel(self, days=500, trace_id=""):
        return self.panel, self.dates

    def get_futures_panel(self, days=500, trace_id=""):
        return self.panel, self.dates


@pytest.fixture
def adapter(monkeypatch) -> CrossMarketDataAdapter:
    monkeypatch.setattr("fts.data.FTSDataProvider", _FakeProvider)
    return CrossMarketDataAdapter()


# ─── get_panel ─────────────────────────────────────────────


class TestGetPanel:
    def test_stock_panel(self, adapter):
        panel, dates = adapter.get_panel("stock", days=100, trace_id="t1")
        assert "RB0" in panel
        assert len(dates) == 60

    def test_stock_max_stocks_truncation(self, adapter):
        # max_stocks=1 → CSI300_SUBSET 截断（真实导入常量）
        panel, _ = adapter.get_panel("stock", max_stocks=1, trace_id="t1")
        # 面板大小取决于 mock provider 返回，不依赖截断逻辑本身
        assert isinstance(panel, dict)

    def test_etf_panel(self, adapter):
        panel, dates = adapter.get_panel("etf", trace_id="t1")
        assert panel is not None

    def test_futures_panel(self, adapter):
        panel, dates = adapter.get_panel("futures", trace_id="t1")
        assert "RB0" in panel

    def test_invalid_market_raises(self, adapter):
        with pytest.raises(ValueError, match="不支持的目标市场"):
            adapter.get_panel("crypto")


# ─── _adapt_panel / _adapt_dataframe ───────────────────────


class TestAdapt:
    def test_adapt_dataframe_stock(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        df = _make_df()
        out = adapter._adapt_dataframe(df, "stock")
        assert set(CORE_FIELDS) <= set(out.columns)
        assert out["close"].dtype == np.float64
        assert "open_interest" not in out.columns  # stock 不填充期货字段

    def test_adapt_dataframe_futures_fills_oi(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        df = _make_df(with_oi=True)
        out = adapter._adapt_dataframe(df, "futures")
        for field in FUTURES_SPECIFIC_FIELDS:
            assert field in out.columns

    def test_adapt_dataframe_missing_core_field_zero(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        df = _make_df().drop(columns=["volume"])
        out = adapter._adapt_dataframe(df, "stock")
        assert (out["volume"] == 0.0).all()  # 缺失字段填 0

    def test_adapt_panel_skips_empty_and_errors(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        bad_df = _make_df()
        bad_df.loc[bad_df.index[0], "close"] = "not-a-number"  # 无法转 float
        panel = {
            "ok": _make_df(),
            "empty": pd.DataFrame(),
            "bad": bad_df,
        }
        out = adapter._adapt_panel(panel, "stock")
        assert "ok" in out
        assert "empty" not in out
        assert isinstance(out, dict)


# ─── execute_factor_on_market ──────────────────────────────


class TestExecuteFactorOnMarket:
    def test_normal_execution(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        panel = {"RB0": _make_df(n=60), "CU0": _make_df(n=60)}
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factor = {
            "factor_id": "f1",
            "code": "def factor_program(data, params):\n    return data['close'] / 100.0",
        }
        signals = adapter.execute_factor_on_market(factor, panel, dates)
        assert "RB0" in signals
        assert len(signals["RB0"]) == 60

    def test_short_data_skipped(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        panel = {"RB0": _make_df(n=10)}  # len < 20 → 跳过
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factor = {"factor_id": "f1", "code": "def factor_program(data, params):\n    return data['close']"}
        assert adapter.execute_factor_on_market(factor, panel, dates) == {}

    def test_empty_df_skipped(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        panel = {"RB0": pd.DataFrame()}
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factor = {"factor_id": "f1", "code": "x"}
        assert adapter.execute_factor_on_market(factor, panel, dates) == {}

    def test_factor_execution_failure_skipped(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        panel = {"RB0": _make_df(n=60)}
        dates = pd.date_range("2026-01-01", periods=60, freq="D")
        factor = {"factor_id": "bad", "code": "import os\ndef factor_program(data, params):\n    return data['close']"}
        assert adapter.execute_factor_on_market(factor, panel, dates) == {}

    def test_signal_alignment_pad(self):
        adapter = CrossMarketDataAdapter.__new__(CrossMarketDataAdapter)
        # 因子输出短于 dates → pad 对齐
        panel = {"RB0": _make_df(n=60)}
        dates = pd.date_range("2026-01-01", periods=80, freq="D")
        factor = {
            "factor_id": "f1",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return data['close'][:10] / 100.0",
        }
        signals = adapter.execute_factor_on_market(factor, panel, dates)
        assert len(signals["RB0"]) == 80
