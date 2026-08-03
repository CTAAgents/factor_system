"""
tests/test_data_futures_panel.py — 期货面板 common_dates 多数对齐 + 方向校正日期定位测试。

覆盖目标:
  1. get_futures_panel: 全品种日期交集为空时，common_dates 仍返回多数共有日期
  2. get_futures_panel: 全部品种数据失败时降级合成数据
  3. 方向校正按日期定位（df.index.get_loc），品种日期错位不污染 IC
  4. 品种名称映射 FUTURES_SYMBOL_NAMES（82 品种全量）
  5. 主力合约判定 get_dominant_contracts（contract_kline 最新交易日最大成交量）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.data_futures import (
    FUTURES_SYMBOL_NAMES,
    FUTURES_SUBSET,
    FuturesDataProvider,
    get_dominant_contracts,
)
from scripts.futures_signal_pipeline import _compute_factor_sign_flips


def _make_df(dates: list[str], base: float = 100.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    close = pd.Series(np.arange(len(idx)) * 0.1 + base, index=idx)
    return pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
        "hold": 5000.0,
        "settle": close,
    })


# ═══════════════════════════════════════════════════════════
# 1. common_dates 多数对齐
# ═══════════════════════════════════════════════════════════

class TestCommonDatesMajorityAlignment:
    def test_common_dates_not_empty_with_stale_symbol(self, mocker):
        """个别停更品种（日期完全陈旧）不清空 common_dates。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        # 5 个品种：4 个日期较新，1 个数据止于 2022（WH0 场景）
        new_dates = [f"2026-{m:02d}-0{d}" for m in range(1, 6) for d in (1, 3)]
        stale_dates = [f"2022-{m:02d}-0{d}" for m in range(1, 5) for d in (1, 3)]
        panel_input = {
            f"S{i}": _make_df(new_dates, base=100 * i) for i in range(1, 5)
        }
        panel_input["STALE"] = _make_df(stale_dates, base=999.0)

        mocker.patch.object(
            provider, "get_ohlcv",
            side_effect=lambda sym, days=500, trace_id="": panel_input[sym],
        )
        panel, common_dates = provider.get_futures_panel(
            list(panel_input.keys()), days=120,
        )
        assert len(panel) == 5
        # 全交集为空（STALE 与新品零交集），但多数对齐应返回新品共有日期
        assert len(common_dates) > 0
        assert all(d >= pd.Timestamp("2026-01-01") for d in common_dates)

    def test_common_dates_requires_majority(self, mocker):
        """多数对齐阈值 = max(2, 品种数//2)。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        dates_common = [f"2026-0{m}-01" for m in range(1, 4)]
        panel_input = {
            "A": _make_df(dates_common),
            "B": _make_df(dates_common),
            # C 只有 1 天与 A/B 重叠
            "C": _make_df(dates_common + ["2025-12-31"], base=200.0),
        }
        mocker.patch.object(
            provider, "get_ohlcv",
            side_effect=lambda sym, days=500, trace_id="": panel_input[sym],
        )
        panel, common_dates = provider.get_futures_panel(
            list(panel_input.keys()), days=120,
        )
        # 阈值 = max(2, 3//2) = 2 → 3 个共同日期全部保留
        assert len(common_dates) == 3

    def test_all_fail_fallback_synthetic(self, mocker):
        """全部品种获取失败 → 合成数据降级。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        mocker.patch.object(provider, "get_ohlcv", side_effect=RuntimeError("boom"))
        panel, common_dates = provider.get_futures_panel(["A", "B"], days=120)
        assert list(panel.keys()) == ["SYNTHETIC"]
        assert len(common_dates) == 120


# ═══════════════════════════════════════════════════════════
# 2. 方向校正按日期定位
# ═══════════════════════════════════════════════════════════

class TestSignFlipDateAlignment:
    def _signal_matrix(self):
        """构造 2 品种信号矩阵 + 面板，B 品种少 1 天（日期错位）。"""
        dates_a = [f"2026-0{m}-01" for m in range(1, 9)]  # 8 天
        dates_b = dates_a[:4] + dates_a[5:]  # B 缺第 5 天
        panel = {
            "A": _make_df(dates_a, base=100.0),
            "B": _make_df(dates_b, base=1000.0),
        }
        sig_a = np.linspace(-0.8, 0.8, len(dates_a))
        sig_b = np.linspace(-0.6, 0.6, len(dates_b))
        signal_matrix = {
            "A": {"f": sig_a},
            "B": {"f": sig_b},
        }
        return signal_matrix, panel

    def test_flip_computed_with_date_lookup(self, mocker):
        """品种日期错位时仍能按日期定位计算截面 IC，不抛异常。"""
        signal_matrix, panel = self._signal_matrix()
        common_dates = panel["A"].index.intersection(panel["B"].index)
        flips = _compute_factor_sign_flips(signal_matrix, panel, common_dates)
        assert "f" in flips
        assert flips["f"] in (1.0, -1.0)


# ═══════════════════════════════════════════════════════════
# 3. 品种名称映射
# ═══════════════════════════════════════════════════════════

class TestSymbolNames:
    def test_names_cover_all_subset(self):
        """FUTURES_SYMBOL_NAMES 覆盖 FUTURES_SUBSET 全部品种。"""
        missing = [s for s in FUTURES_SUBSET if s not in FUTURES_SYMBOL_NAMES]
        assert missing == []

    def test_known_names(self):
        """核心品种名称正确。"""
        assert FUTURES_SYMBOL_NAMES["RB0"] == "螺纹钢"
        assert FUTURES_SYMBOL_NAMES["CU0"] == "铜"
        assert FUTURES_SYMBOL_NAMES["SC0"] == "原油"
        assert FUTURES_SYMBOL_NAMES["AU0"] == "黄金"
        assert FUTURES_SYMBOL_NAMES["SA0"] == "纯碱"


# ═══════════════════════════════════════════════════════════
# 4. 主力合约判定
# ═══════════════════════════════════════════════════════════

class TestDominantContracts:
    def test_returns_mapping(self, mocker):
        """正常返回 {symbol: contract} 映射。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            ("RB", "RB2610"),
            ("CU", "CU2609"),
        ]
        mocker.patch("fts.data_futures._get_db", return_value=mock_db)
        result = get_dominant_contracts(["RB0", "CU0"])
        assert result == {"RB0": "RB2610", "CU0": "CU2609"}

    def test_missing_symbols_empty(self, mocker):
        """无数据品种返回空串。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RB", "RB2610")]
        mocker.patch("fts.data_futures._get_db", return_value=mock_db)
        mocker.patch(
            "fts.data_futures._fetch_dominant_akshare", return_value={},
        )
        result = get_dominant_contracts(["RB0", "CU0"])
        assert result["RB0"] == "RB2610"
        assert result["CU0"] == ""

    def test_db_error_returns_empty(self, mocker):
        """数据库不可用时返回全空映射（降级不抛异常）。"""
        from fts.data_futures import FuturesDataError
        mocker.patch(
            "fts.data_futures._get_db",
            side_effect=FuturesDataError("boom"),
        )
        mocker.patch(
            "fts.data_futures._fetch_dominant_akshare", return_value={},
        )
        result = get_dominant_contracts(["RB0"])
        assert result == {"RB0": ""}

    def test_akshare_fallback_fills_missing(self, mocker):
        """DB 缺失品种由 AKShare fallback 补全。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RB", "RB2610")]
        mocker.patch("fts.data_futures._get_db", return_value=mock_db)
        mocker.patch(
            "fts.data_futures._fetch_dominant_akshare",
            return_value={"RU0": "RU2609"},
        )
        result = get_dominant_contracts(["RB0", "RU0"])
        assert result == {"RB0": "RB2610", "RU0": "RU2609"}


class TestFetchDominantAkshare:
    def test_picks_max_position_contract(self, mocker):
        """按持仓量排序取最大具体合约（排除连续合约）。"""
        import pandas as pd
        df = pd.DataFrame({
            "symbol": ["RU0", "RU2609", "RU2610", "RU2608"],
            "position": [100, 500, 300, 200],
        })
        mocker.patch("akshare.futures_zh_realtime", return_value=df)
        from fts.data_futures import _fetch_dominant_akshare
        result = _fetch_dominant_akshare(["RU0"])
        assert result == {"RU0": "RU2609"}

    def test_returns_empty_on_failure(self, mocker):
        """查询失败返回空 dict（降级不抛异常）。"""
        mocker.patch(
            "akshare.futures_zh_realtime",
            side_effect=RuntimeError("boom"),
        )
        from fts.data_futures import _fetch_dominant_akshare
        result = _fetch_dominant_akshare(["RU0"])
        assert result == {}
