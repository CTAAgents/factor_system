"""
tests/test_data_futures_panel.py — 期货面板 common_dates 多数对齐 + 方向校正日期定位测试。

覆盖目标:
  1. get_futures_panel: 全品种日期交集为空时，common_dates 仍返回多数共有日期
  2. get_futures_panel: 全部品种数据失败时降级合成数据
  3. 方向校正按日期定位（df.index.get_loc），品种日期错位不污染 IC
  4. 品种名称映射 FUTURES_SYMBOL_NAMES（84 品种全量）
  5. 主力合约判定 get_dominant_contracts（contract_kline 最新交易日最大成交量）
  6. 全期货覆盖规划 FUTURES_COVERAGE_PLAN（plans/57 §9 步骤0，P0-P3 84 品种）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.data_futures import (
    FUTURES_COVERAGE_PLAN,
    FUTURES_SYMBOL_NAMES,
    FUTURES_SUBSET,
    FuturesDataProvider,
    get_dominant_contracts,
)


def _make_df(dates: list[str], base: float = 100.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    close = pd.Series(np.arange(len(idx)) * 0.1 + base, index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000.0,
            "hold": 5000.0,
            "settle": close,
        }
    )


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
        panel_input = {f"S{i}": _make_df(new_dates, base=100 * i) for i in range(1, 5)}
        panel_input["STALE"] = _make_df(stale_dates, base=999.0)

        mocker.patch.object(
            provider,
            "get_ohlcv",
            side_effect=lambda sym, days=500, trace_id="": panel_input[sym],
        )
        panel, common_dates = provider.get_futures_panel(
            list(panel_input.keys()),
            days=120,
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
            provider,
            "get_ohlcv",
            side_effect=lambda sym, days=500, trace_id="": panel_input[sym],
        )
        panel, common_dates = provider.get_futures_panel(
            list(panel_input.keys()),
            days=120,
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
# 6. 全期货覆盖规划（plans/57 §9 步骤0）
# ═══════════════════════════════════════════════════════════


class TestCoveragePlan:
    def test_coverage_union_equals_universe(self):
        """P0-P3 并集 = FUTURES_SUBSET（84 品种全覆盖，无遗漏无越界）。"""
        cov_syms = {s for v in FUTURES_COVERAGE_PLAN.values() for s in v["symbols"]}
        assert cov_syms == set(FUTURES_SUBSET)
        assert len(cov_syms) == 84

    def test_coverage_levels_disjoint(self):
        """四优先级级别间品种互不重叠。"""
        sets = [set(v["symbols"]) for v in FUTURES_COVERAGE_PLAN.values()]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                assert not (sets[i] & sets[j])

    def test_p0_energy_24(self):
        """P0 能源化工 24 品种先行（存量因子重审衔接对象）。"""
        p0 = set(FUTURES_COVERAGE_PLAN["P0_energy_chemicals"]["symbols"])
        assert len(p0) == 24
        assert {"SC0", "TA0", "L0", "MA0", "RU0"} <= p0

    def test_coverage_chains_in_sector_map(self):
        """覆盖级别声明的产业链名均存在于 sector_map（17 产业链细化）。"""
        from fts.data_futures import FUTURES_SECTOR_MAP

        for v in FUTURES_COVERAGE_PLAN.values():
            for chain in v["chains"]:
                assert chain in FUTURES_SECTOR_MAP, f"覆盖链 {chain} 不在 sector_map"


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
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")
        result = get_dominant_contracts(["RB0", "CU0"])
        assert result == {"RB0": "RB2610", "CU0": "CU2609"}

    def test_missing_symbols_empty(self, mocker):
        """无数据品种返回空串。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RB", "RB2610")]
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")
        mocker.patch(
            "fts.data_futures._fetch_dominant_akshare",
            return_value={},
        )
        result = get_dominant_contracts(["RB0", "CU0"])
        assert result["RB0"] == "RB2610"
        assert result["CU0"] == ""

    def test_db_error_returns_empty(self, mocker):
        """数据库不可用时返回全空映射（降级不抛异常）。"""
        from fts.data_futures import FuturesDataError

        mocker.patch(
            "fts.data_futures._get_reader",
            side_effect=FuturesDataError("boom"),
        )
        mocker.patch("fts.data_futures._release_reader")
        mocker.patch(
            "fts.data_futures._fetch_dominant_akshare",
            return_value={},
        )
        result = get_dominant_contracts(["RB0"])
        assert result == {"RB0": ""}

    def test_akshare_fallback_fills_missing(self, mocker):
        """DB 缺失品种由 AKShare fallback 补全。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RB", "RB2610")]
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")
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

        df = pd.DataFrame(
            {
                "symbol": ["RU0", "RU2609", "RU2610", "RU2608"],
                "position": [100, 500, 300, 200],
            }
        )
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


# ═══════════════════════════════════════════════════════════
# GAP-151 主加载路径字段完整性校验（_from_kline_cache 接入）
# ═══════════════════════════════════════════════════════════

_KLINE_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "hold", "settle", "vwap", "symbol"]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeReader:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _FakeResult(self._rows)


def _kline_row(**kw):
    """构造单行 kline_cache 查询结果（11 列，缺省取默认值）。"""
    base = {
        "date": "2026-01-05", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "volume": 100.0, "amount": 0.0, "hold": 10.0, "settle": 1.5,
        "vwap": 2.0, "symbol": "RB0",
    }
    base.update(kw)
    return tuple(base[c] for c in _KLINE_COLS)


class TestFromKlineCacheFieldIntegrity:
    def test_extended_hold_missing_warns_and_proxy(self, mocker, caplog):
        """增强字段 hold 全空（GAP-083 代理场景）→ warning + 代理填充后返回 df。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        mocker.patch("fts.data_futures._get_reader", return_value=_FakeReader([_kline_row(hold=None)]))
        mocker.patch("fts.data_futures._release_reader", return_value=None)
        with caplog.at_level("WARNING", logger="fts.data_futures"):
            df = provider._from_kline_cache("RB0", 60)
        assert df is not None
        assert any("增强字段缺失" in r.message and "hold" in r.message for r in caplog.records)
        # hold 代理填充（20 日滚动均量退化为单行自身 volume）
        assert df["hold"].iloc[-1] == pytest.approx(100.0)

    def test_core_missing_returns_none(self, mocker, caplog):
        """核心字段全空（close=None）→ error + 返回 None（宁缺毋滥）。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        mocker.patch("fts.data_futures._get_reader", return_value=_FakeReader([_kline_row(close=None)]))
        mocker.patch("fts.data_futures._release_reader", return_value=None)
        with caplog.at_level("ERROR", logger="fts.data_futures"):
            df = provider._from_kline_cache("RB0", 60)
        assert df is None
        assert any("核心字段缺失" in r.message and "close" in r.message for r in caplog.records)

    def test_complete_fields_no_warning(self, mocker, caplog):
        """核心+增强字段齐全 → 返回 df 无告警。"""
        provider = FuturesDataProvider(use_akshare_fallback=False)
        mocker.patch("fts.data_futures._get_reader", return_value=_FakeReader([_kline_row()]))
        mocker.patch("fts.data_futures._release_reader", return_value=None)
        with caplog.at_level("WARNING", logger="fts.data_futures"):
            df = provider._from_kline_cache("RB0", 60)
        assert df is not None
        assert not any("字段缺失" in r.message for r in caplog.records)
