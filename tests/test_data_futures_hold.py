"""GAP-083 阶段 A：_from_kline_cache 读路径修复（真实优先/代理兜底 + 双格式对齐）。

覆盖:
- 库内真实 hold/settle（>0）优先保留
- 0.0 占位（TQ 同步写入）→ 代理兜底（settle 典型价 / hold 20 日均量）
- NULL 值 → 代理兜底
- 双格式 symbol（RB vs RB0）→ 优先 RB0（TQ 15 年）
- 混合行：真实保留、无效行代理
- 无数据 → None
"""

import pandas as pd
import pytest

from fts.data_futures import FuturesDataProvider


def _provider(mocker) -> FuturesDataProvider:
    mocker.patch.object(FuturesDataProvider, "_init_default_aggregator")
    return FuturesDataProvider(use_akshare_fallback=False, aggregator=None)


def _mock_db(mocker, rows: list[tuple], columns: list[str]):
    """Mock _get_reader 返回给定行（按查询列顺序）。"""
    mock_db = mocker.MagicMock()
    mock_result = mocker.MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db.execute.return_value = mock_result
    mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
    mocker.patch("fts.data_futures._release_reader")
    return mock_db


# 11 列: date, open, high, low, close, volume, amount, hold, settle, vwap, symbol
_COLS10 = ["date", "open", "high", "low", "close", "volume", "amount", "hold", "settle", "vwap", "symbol"]


class TestRealHoldSettlePreferred:
    def test_real_values_kept(self, mocker):
        """库内真实 hold/settle（>0）原样保留，不替换为代理。"""
        rows = [
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 5000.0, 3100.0, 11.0, "RB0"),
            ("2026-01-01", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0, 4800.0, 3150.0, 10.5, "RB0"),
        ]
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        # 升序索引：iloc[0]=2026-01-01, iloc[1]=2026-01-02（真实值保留）
        assert df["hold"].iloc[0] == 4800.0
        assert df["settle"].iloc[0] == 3150.0
        assert df["hold"].iloc[1] == 5000.0
        assert df["settle"].iloc[1] == 3100.0

    def test_zero_placeholder_falls_back(self, mocker):
        """0.0 占位（TQ 同步写入的 hold/settle=0.0）→ 代理兜底。"""
        rows = [
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 0.0, 0.0, 11.0, "RB0"),
        ]
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        # settle 代理 = (H+L+C)/3
        assert df["settle"].iloc[0] == pytest.approx((11.5 + 9.5 + 11.0) / 3.0)
        # hold 代理 = 20 日滚动均量（单行 min_periods=1 = volume 自身）
        assert df["hold"].iloc[0] == 2000.0

    def test_null_falls_back(self, mocker):
        """NULL 值（AKShare 老数据未写 hold/settle）→ 代理兜底。"""
        rows = [
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, None, None, 11.0, "RB"),
        ]
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        assert df["settle"].iloc[0] == pytest.approx((11.5 + 9.5 + 11.0) / 3.0)
        assert df["hold"].iloc[0] == 2000.0
        assert pd.notna(df["settle"].iloc[0]) and pd.notna(df["hold"].iloc[0])

    def test_mixed_rows(self, mocker):
        """混合：真实行保留真实值、0.0 行走代理。"""
        rows = [
            ("2026-01-03", 11.0, 12.0, 10.0, 11.5, 3000.0, 34000.0, 0.0, 0.0, 11.5, "RB0"),
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 5000.0, 3100.0, 11.0, "RB0"),
        ]
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        # 升序索引：iloc[0]=2026-01-02（真实），iloc[1]=2026-01-03（0 占位 → 代理）
        assert df["hold"].iloc[0] == 5000.0
        assert df["settle"].iloc[0] == 3100.0
        assert df["settle"].iloc[1] == pytest.approx((12.0 + 10.0 + 11.5) / 3.0)
        # hold 代理 = 20 日滚动均量（两行时 (3000+2000)/2）
        assert df["hold"].iloc[1] == pytest.approx(2500.0)


class TestDualFormatAlignment:
    def test_prefers_suffix0_duplicate_dates(self, mocker):
        """双格式同日期：优先保留 RB0（TQ 15 年）记录，RB 老数据去重丢弃。"""
        rows = [
            # RB0（带 0 后缀，真实 hold/settle）
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 5000.0, 3100.0, 11.0, "RB0"),
            # RB（老数据，hold/settle NULL）
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, None, None, 11.0, "RB"),
            ("2026-01-01", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0, 4800.0, 3150.0, 10.5, "RB0"),
        ]
        # _COLS10 已含 symbol 列（11 列）
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        assert len(df) == 2  # 2026-01-02 去重后仅 1 行
        # 2026-01-02 保留 RB0（hold=5000）而非 RB（None）
        assert df.loc["2026-01-02", "hold"] == 5000.0
        assert df.loc["2026-01-02", "settle"] == 3100.0
        # 2026-01-01 保留 RB0 真实值
        assert df.loc["2026-01-01", "hold"] == 4800.0


class TestFromKlineCacheEdge:
    def test_empty_rows_returns_none(self, mocker):
        """无数据行 → None（回归）。"""
        _mock_db(mocker, [], _COLS10)
        assert _provider(mocker)._from_kline_cache("RB0", days=10) is None

    def test_output_columns_contract(self, mocker):
        """输出列契约：9 列标准顺序（GAP-083 补充 amount）。"""
        rows = [
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 5000.0, 3100.0, 11.0, "RB0"),
        ]
        _mock_db(mocker, rows, _COLS10)
        df = _provider(mocker)._from_kline_cache("RB0", days=10)
        assert list(df.columns) == [
            "open", "high", "low", "close", "volume", "amount", "vwap", "hold", "settle",
        ]
        assert df.index.is_monotonic_increasing


# ═══════════════════════════════════════════════════════════
# GAP-083 阶段 C：字段增强层（iFinD/Wind）注册
# ═══════════════════════════════════════════════════════════


class TestEnhancersRegistration:
    def _build_provider(self, mocker, enhance_enabled: bool, tqsdk_enabled: bool = False):
        """构造 provider 并返回 aggregator 构造 kwargs。

        Args:
            enhance_enabled: futures_enhance_enabled 开关（控制 iFinD SDK 追加）
            tqsdk_enabled: tqsdk_sources_enabled 开关（控制 TQSDKEnhanceSource 注册，v3.0.0+2 opt-in）
        """
        from types import SimpleNamespace

        mocker.patch(
            "fts.config.settings.get_config",
            return_value=SimpleNamespace(
                futures_enhance_enabled=enhance_enabled,
                tqsdk_sources_enabled=tqsdk_enabled,
                minute_cache_max_age_days=1,
            ),
        )
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mocker.MagicMock())
        fake_tq = mocker.MagicMock()
        mocker.patch(
            "fts.data_sources.tqsdk_enhance_source.TQSDKEnhanceSource",
            return_value=fake_tq,
        )
        agg_mock = mocker.patch(
            "fts.data_sources.aggregator.FuturesDataAggregator",
            return_value=mocker.MagicMock(),
        )
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        return provider, agg_mock, fake_tq

    def test_default_no_tqsdk_enhancer(self, mocker):
        """默认 tqsdk_sources_enabled=False → enhancers 为空（天勤增强源不注册，GAP-159 opt-in）。"""
        _, agg_mock, _fake_tq = self._build_provider(mocker, enhance_enabled=False)
        kwargs = agg_mock.call_args.kwargs
        assert len(kwargs["enhancers"]) == 0

    def test_tqsdk_enabled_registers_enhance_source(self, mocker):
        """tqsdk_sources_enabled=True → TQSDKEnhanceSource 被注册（opt-in 恢复旧行为）。"""
        _, agg_mock, fake_tq = self._build_provider(mocker, enhance_enabled=False, tqsdk_enabled=True)
        kwargs = agg_mock.call_args.kwargs
        assert len(kwargs["enhancers"]) == 1
        assert kwargs["enhancers"][0] is fake_tq

    def test_tqsdk_enabled_plus_ifind_sdk(self, mocker):
        """tqsdk_sources_enabled=True + futures_enhance_enabled=True → TQSDK + iFinD SDK 两个增强源。"""
        fake_sdk = mocker.MagicMock()
        mocker.patch("fts.data_sources.ifind_sdk_source.IFindSDKSource", return_value=fake_sdk)
        _, agg_mock, fake_tq = self._build_provider(mocker, enhance_enabled=True, tqsdk_enabled=True)
        kwargs = agg_mock.call_args.kwargs
        assert len(kwargs["enhancers"]) == 2
        assert kwargs["enhancers"][0] is fake_tq
        assert kwargs["enhancers"][1] is fake_sdk

    def test_ifind_sdk_instantiation_failure_skips(self, mocker):
        """tqsdk_enabled + IFindSDKSource 实例化失败 → 跳过 iFinD，TQSDK 仍注册。"""
        mocker.patch(
            "fts.data_sources.ifind_sdk_source.IFindSDKSource",
            side_effect=RuntimeError("init fail"),
        )
        _, agg_mock, fake_tq = self._build_provider(mocker, enhance_enabled=True, tqsdk_enabled=True)
        kwargs = agg_mock.call_args.kwargs
        assert len(kwargs["enhancers"]) == 1
        assert kwargs["enhancers"][0] is fake_tq

    def test_ifind_sdk_import_failure_degrades(self, mocker):
        """tqsdk_enabled + IFindSDKSource 模块导入失败 → 仅 TQSDK 注册，不抛异常。"""
        mocker.patch(
            "fts.data_sources.ifind_sdk_source.IFindSDKSource",
            side_effect=ImportError("no ifind sdk"),
        )
        _, agg_mock, fake_tq = self._build_provider(mocker, enhance_enabled=True, tqsdk_enabled=True)
        kwargs = agg_mock.call_args.kwargs
        assert len(kwargs["enhancers"]) == 1
        assert kwargs["enhancers"][0] is fake_tq


class TestEnhanceFieldsValidOverwrite:
    """GAP-083 阶段 C：_enhance_fields 有效值覆盖（NaN/0 不污染主路径回填值）。"""

    @staticmethod
    def _agg(mocker, enrich_df) -> tuple:
        """构造带单个 mock enhancer 的 aggregator，返回 (aggregator, 主路径 df)。"""
        from fts.data_sources.aggregator import FuturesDataAggregator

        enhancer = mocker.MagicMock()
        enhancer.source_name = "FAKE_ENH"
        enhancer.fetch_ohlcv_or_none.return_value = enrich_df
        agg = FuturesDataAggregator(sources=[], enhancers=[enhancer])
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-08-07", "2026-08-10", "2026-08-11"]),
                "open": [10.0, 11.0, 12.0],
                "high": [11.0, 12.0, 13.0],
                "low": [9.0, 10.0, 11.0],
                "close": [10.5, 11.5, 12.5],
                "volume": [1000, 1100, 1200],
                "amount": [1e6, 1.1e6, 1.2e6],
                "hold": [100.0, 110.0, 120.0],
                "settle": [10.4, 11.4, 12.4],
                "pre_settle": [10.0, 10.5, 11.5],
                "oi_change": [0.0, 10.0, 10.0],
                "vwap": [10.45, 11.45, 12.45],
            }
        )
        return agg, df

    def test_hold_positive_only_overwrite(self, mocker):
        """enrich_df.hold 仅正数覆盖，NaN/0 保留主路径值。"""
        enrich = pd.DataFrame(
            {"date": ["2026-08-07", "2026-08-10", "2026-08-11"],
             "hold": [float("nan"), 0.0, 999.0]}
        )
        agg, df = self._agg(mocker, enrich)
        out = agg._enhance_fields(df, "RB0", "")
        assert out["hold"].tolist() == [100.0, 110.0, 999.0]

    def test_settle_positive_only_overwrite(self, mocker):
        """enrich_df.settle 仅正数覆盖，NaN 保留主路径。"""
        enrich = pd.DataFrame(
            {"date": ["2026-08-07", "2026-08-10", "2026-08-11"],
             "settle": [float("nan"), 888.0, float("nan")]}
        )
        agg, df = self._agg(mocker, enrich)
        out = agg._enhance_fields(df, "RB0", "")
        assert out["settle"].tolist() == [10.4, 888.0, 12.4]

    def test_oi_change_any_valid_overwrite(self, mocker):
        """enrich_df.oi_change 非 NaN 即覆盖（可负/零/正），NaN 保留主路径。"""
        enrich = pd.DataFrame(
            {"date": ["2026-08-07", "2026-08-10", "2026-08-11"],
             "oi_change": [float("nan"), -5.0, 0.0]}
        )
        agg, df = self._agg(mocker, enrich)
        out = agg._enhance_fields(df, "RB0", "")
        assert out["oi_change"].tolist() == [0.0, -5.0, 0.0]

    def test_missing_column_noop(self, mocker):
        """enrich_df 缺列/空 → 不抛异常，主路径原样。"""
        enrich = pd.DataFrame({"date": ["2026-08-07"]})
        agg, df = self._agg(mocker, enrich)
        out = agg._enhance_fields(df, "RB0", "")
        assert out["hold"].tolist() == [100.0, 110.0, 120.0]
        assert out["settle"].tolist() == [10.4, 11.4, 12.4]
