"""
tests.test_data_futures_fundamental — 期货基本面 provider（库存/基差）单元测试。

覆盖: 品种解析、AKShare 归一化、双源降级、缓存、FTSDataProvider 挂接。
全部通过 monkeypatch 隔离网络，不依赖真实 AKShare 接口。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import akshare as ak
import pandas as pd
import pytest

from fts.data_futures_fundamental import (
    BASIS_COLUMNS,
    INVENTORY_COLUMNS,
    VARIETY_EXCHANGE,
    VARIETY_MAP,
    WAREHOUSE_COLUMNS,
    AkshareFuturesFundamentalProvider,
)


# ─── 品种解析 ─────────────────────────────────────────────


class TestParseVariety:
    def test_with_continuous_suffix(self) -> None:
        p = AkshareFuturesFundamentalProvider()
        assert p._parse_variety("RB0") == "RB"
        assert p._parse_variety("CU0") == "CU"
        assert p._parse_variety("IF0") == "IF"

    def test_without_suffix(self) -> None:
        p = AkshareFuturesFundamentalProvider()
        assert p._parse_variety("RB") == "RB"

    def test_single_char_variety(self) -> None:
        p = AkshareFuturesFundamentalProvider()
        assert p._parse_variety("M0") == "M"
        assert p._parse_variety("I0") == "I"

    def test_unknown_returns_none(self) -> None:
        p = AkshareFuturesFundamentalProvider()
        assert p._parse_variety("XX0") is None

    def test_core_subset_all_covered(self) -> None:
        """核心品种子集全部在映射表内（保证接入覆盖）。"""
        from fts.data_futures import FUTURES_CORE_SUBSET

        missing = [s for s in FUTURES_CORE_SUBSET if s.upper().rstrip("0") not in VARIETY_MAP]
        assert missing == [], f"未覆盖品种: {missing}"


# ─── 库存归一化 ───────────────────────────────────────────


class TestNormalizeInventory:
    def test_em_normalize(self) -> None:
        raw = pd.DataFrame({"日期": ["2026-05-06", "2026-05-07"], "库存": [90221, 93856], "增减": [None, 3635.0]})
        df = AkshareFuturesFundamentalProvider._normalize_inventory_em(raw)
        assert list(df.columns) == INVENTORY_COLUMNS
        assert len(df) == 2
        assert df["inventory"].iloc[-1] == 93856.0
        assert df["change"].iloc[-1] == 3635.0
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index[0] < df.index[1]

    def test_em_missing_change_column_keeps_inventory(self) -> None:
        """缺增减列 → inventory 保留，change 全 NaN。"""
        raw = pd.DataFrame({"日期": ["2026-05-06"], "库存": [90221]})
        df = AkshareFuturesFundamentalProvider._normalize_inventory_em(raw)
        assert list(df.columns) == INVENTORY_COLUMNS
        assert len(df) == 1
        assert df["inventory"].iloc[0] == 90221.0
        assert df["change"].isna().all()

    def test_99_normalize_derives_change(self) -> None:
        raw = pd.DataFrame({"日期": ["2026-05-06", "2026-05-13"], "收盘价": [3800.0, 3810.0], "库存": [90221, 93856]})
        df = AkshareFuturesFundamentalProvider._normalize_inventory_99(raw)
        assert list(df.columns) == INVENTORY_COLUMNS
        assert df["inventory"].iloc[-1] == 93856.0
        assert df["change"].iloc[-1] == 3635.0

    def test_empty_raw_returns_empty(self) -> None:
        df = AkshareFuturesFundamentalProvider._normalize_inventory_em(pd.DataFrame())
        assert df.empty and list(df.columns) == INVENTORY_COLUMNS


# ─── 基差归一化 ───────────────────────────────────────────


class TestNormalizeBasis:
    def test_filter_variety_and_contract(self) -> None:
        raw = pd.DataFrame(
            {
                "date": ["20260803", "20260804", "20260803"],
                "symbol": ["RB", "RB", "CU"],
                "spot_price": [3002.34, 3022.0, 70000.0],
                "near_basis": [-112.34, -117.0, 50.0],
                "dom_basis": [-26.34, -39.0, 20.0],
                "near_basis_rate": [-0.037, -0.038, 0.001],
                "dom_basis_rate": [-0.008, -0.012, 0.0005],
            }
        )
        df = AkshareFuturesFundamentalProvider._normalize_basis(raw, "RB")
        assert list(df.columns) == BASIS_COLUMNS
        assert len(df) == 2  # CU 行被过滤
        assert df.index[0] < df.index[1]
        assert df["spot_price"].iloc[0] == 3002.34

    def test_missing_basis_cols_reindex_nan(self) -> None:
        """部分基差列缺失 → 保留已有列，缺失列补 NaN（契约列完整）。"""
        raw = pd.DataFrame({"date": ["20260803"], "symbol": ["RB"], "spot_price": [3000.0]})
        df = AkshareFuturesFundamentalProvider._normalize_basis(raw, "RB")
        assert list(df.columns) == BASIS_COLUMNS
        assert len(df) == 1
        assert df["spot_price"].iloc[0] == 3000.0
        assert df["near_basis"].isna().all()


# ─── 库存获取（降级链）────────────────────────────────────


class TestGetInventory:
    def test_em_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()

        def fake_em(symbol: str) -> pd.DataFrame:
            return pd.DataFrame({"日期": ["2026-05-06", "2026-05-07"], "库存": [90221, 93856], "增减": [None, 3635.0]})

        monkeypatch.setattr(ak, "futures_inventory_em", fake_em)
        df = provider.get_inventory("RB0")
        assert len(df) == 2 and df["inventory"].iloc[-1] == 93856.0

    def test_em_failure_fallback_99(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()

        def fake_em(symbol: str) -> pd.DataFrame:
            raise ConnectionError("boom")

        def fake_99(symbol: str) -> pd.DataFrame:
            return pd.DataFrame(
                {"日期": ["2026-05-06", "2026-05-13"], "收盘价": [3800.0, 3810.0], "库存": [90221, 93856]}
            )

        monkeypatch.setattr(ak, "futures_inventory_em", fake_em)
        monkeypatch.setattr(ak, "futures_inventory_99", fake_99)
        df = provider.get_inventory("RB0")
        assert df["inventory"].iloc[-1] == 93856.0
        assert df["change"].iloc[-1] == 3635.0

    def test_all_sources_fail_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()

        def boom(symbol: str) -> pd.DataFrame:
            raise ConnectionError("boom")

        monkeypatch.setattr(ak, "futures_inventory_em", boom)
        monkeypatch.setattr(ak, "futures_inventory_99", boom)
        df = provider.get_inventory("RB0")
        assert df.empty and list(df.columns) == INVENTORY_COLUMNS

    def test_index_futures_no_inventory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """股指品种无现货库存 → 空 df（不请求网络）。"""
        provider = AkshareFuturesFundamentalProvider()
        monkeypatch.setattr(ak, "futures_inventory_em", lambda s: (_ for _ in ()).throw(AssertionError("不应调用")))
        df = provider.get_inventory("IF0")
        assert df.empty

    def test_unknown_symbol_empty(self) -> None:
        provider = AkshareFuturesFundamentalProvider()
        assert provider.get_inventory("XX0").empty

    def test_cached_no_second_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake_em(symbol: str) -> pd.DataFrame:
            calls.append(symbol)
            return pd.DataFrame({"日期": ["2026-05-06"], "库存": [90221], "增减": [None]})

        monkeypatch.setattr(ak, "futures_inventory_em", fake_em)
        provider.get_inventory("RB0")
        provider.get_inventory("RB0")
        assert len(calls) == 1


# ─── 基差获取 ─────────────────────────────────────────────


class TestGetBasis:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake_spot(date: str, vars_list: list) -> pd.DataFrame:
            calls.append(date)
            return pd.DataFrame(
                {
                    "date": [date],
                    "symbol": [vars_list[0]],
                    "spot_price": [3000.0],
                    "near_basis": [-100.0],
                    "dom_basis": [-30.0],
                    "near_basis_rate": [-0.03],
                    "dom_basis_rate": [-0.01],
                }
            )

        monkeypatch.setattr(ak, "futures_spot_price", fake_spot)
        df = provider.get_basis("RB0", days=60)
        assert list(df.columns) == BASIS_COLUMNS
        assert len(df) >= 1
        assert len(calls) > 0  # 逐日并行请求
        assert df.index[0] < df.index[-1]  # 升序

    def test_failure_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()

        def boom(date: str, vars_list: list) -> pd.DataFrame:
            raise ConnectionError("boom")

        monkeypatch.setattr(ak, "futures_spot_price", boom)
        df = provider.get_basis("RB0", days=60)
        assert df.empty and list(df.columns) == BASIS_COLUMNS

    def test_unknown_symbol_empty(self) -> None:
        provider = AkshareFuturesFundamentalProvider()
        assert provider.get_basis("XX0").empty

    def test_cached_no_second_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake(date: str, vars_list: list) -> pd.DataFrame:
            calls.append(date)
            return pd.DataFrame(
                {
                    "date": [date],
                    "symbol": ["RB"],
                    "spot_price": [3000.0],
                    "near_basis": [-100.0],
                    "dom_basis": [-30.0],
                    "near_basis_rate": [-0.03],
                    "dom_basis_rate": [-0.01],
                }
            )

        monkeypatch.setattr(ak, "futures_spot_price", fake)
        provider.get_basis("RB0", days=60)
        first = len(calls)
        assert first > 0  # 首调逐日请求
        provider.get_basis("RB0", days=60)
        assert len(calls) == first  # 缓存命中，不二次请求


# ─── FTSDataProvider 挂接 ─────────────────────────────────


class TestFTSProviderWiring:
    def test_default_provider_wired(self) -> None:
        """FTSDataProvider 默认挂接 AKShare 期货基本面 provider。"""
        from fts.data import FTSDataProvider

        p = FTSDataProvider(mcp_provider=MagicMock())
        assert isinstance(p._futures_fundamental, AkshareFuturesFundamentalProvider)

    def test_injectable_provider(self) -> None:
        """显式注入的 provider 优先于默认。"""
        from fts.data import FTSDataProvider

        mock = MagicMock(spec=AkshareFuturesFundamentalProvider)
        p = FTSDataProvider(mcp_provider=MagicMock(), futures_fundamental_provider=mock)
        assert p._futures_fundamental is mock


# ─── 仓单归一化 ───────────────────────────────────────────


class TestNormalizeWarehouse:
    def test_czce_dict_aggregates(self) -> None:
        """CZCE dict 按品种表聚合仓单总量与增减。"""
        raw = {
            "SR": pd.DataFrame(
                {
                    "仓库编号": ["0103", "0104"],
                    "仓单数量": [246, 213],
                    "当日增减": [-18, 0],
                }
            ),
            "CF": pd.DataFrame({"仓单数量": [10], "当日增减": [1]}),
        }
        df = AkshareFuturesFundamentalProvider._normalize_warehouse_czce_gfex(raw, "SR", "20260807")
        assert list(df.columns) == WAREHOUSE_COLUMNS
        assert df["warehouse_receipt"].iloc[0] == 459  # 246+213
        assert df["change"].iloc[0] == -18  # -18+0
        assert df.index[0] == pd.Timestamp("20260807")

    def test_missing_variety_returns_empty(self) -> None:
        raw = {"CF": pd.DataFrame({"仓单数量": [10]})}
        df = AkshareFuturesFundamentalProvider._normalize_warehouse_czce_gfex(raw, "SR", "20260807")
        assert df.empty and list(df.columns) == WAREHOUSE_COLUMNS

    def test_empty_table_returns_empty(self) -> None:
        df = AkshareFuturesFundamentalProvider._normalize_warehouse_czce_gfex({"SR": pd.DataFrame()}, "SR", "20260807")
        assert df.empty

    def test_non_dict_returns_empty(self) -> None:
        df = AkshareFuturesFundamentalProvider._normalize_warehouse_czce_gfex(None, "SR", "20260807")
        assert df.empty and list(df.columns) == WAREHOUSE_COLUMNS


# ─── 仓单获取（路由/并行/降级）────────────────────────────


class TestGetWarehouseReceipt:
    def test_czce_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake_czce(date: str) -> dict:
            calls.append(date)
            return {"SR": pd.DataFrame({"仓单数量": [459], "当日增减": [-18]})}

        monkeypatch.setattr(ak, "futures_warehouse_receipt_czce", fake_czce)
        df = provider.get_warehouse_receipt("SR0", days=60)
        assert list(df.columns) == WAREHOUSE_COLUMNS
        assert len(df) >= 1
        assert df["warehouse_receipt"].iloc[0] == 459
        assert len(calls) > 0

    def test_gfex_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()

        def fake_gfex(date: str) -> dict:
            return {"SI": pd.DataFrame({"仓单数量": [120], "当日增减": [5]})}

        monkeypatch.setattr(ak, "futures_gfex_warehouse_receipt", fake_gfex)
        df = provider.get_warehouse_receipt("SI0", days=60)
        assert df["warehouse_receipt"].iloc[0] == 120

    def test_shfe_dce_via_eastmoney(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """阶段 2：SHFE/DCE 走东财 RPT_FUTU_STOCKDATA（不再是空降级）。"""
        import requests as real_requests

        provider = AkshareFuturesFundamentalProvider()
        fake_rows = [
            {"SECURITY_CODE": "RB", "TRADE_DATE": "2026-08-10 00:00:00", "ON_WARRANT_NUM": 35015, "ADDCHANGE": 4812},
            {"SECURITY_CODE": "RB", "TRADE_DATE": "2026-08-11 00:00:00", "ON_WARRANT_NUM": 36512, "ADDCHANGE": 1497},
        ]

        def fake_get(url: str, params: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            assert params.get("reportName") == "RPT_FUTU_STOCKDATA"
            assert '"RB"' in params.get("filter", "")
            resp = MagicMock()
            resp.json.return_value = {"success": True, "code": 200, "result": {"data": fake_rows}}
            return resp

        monkeypatch.setattr(real_requests, "get", fake_get)
        df = provider.get_warehouse_receipt("RB0", days=60)
        assert not df.empty
        assert list(df.columns) == WAREHOUSE_COLUMNS
        assert df["warehouse_receipt"].iloc[-1] == 36512
        assert df["change"].iloc[-1] == 1497

    def test_ine_via_eastmoney(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """阶段 2：INE 品种走东财（小写 SECURITY_CODE）。"""
        import requests as real_requests

        provider = AkshareFuturesFundamentalProvider()
        fake_rows = [
            {"SECURITY_CODE": "nr", "TRADE_DATE": "2026-08-11 00:00:00", "ON_WARRANT_NUM": 5000, "ADDCHANGE": -100},
        ]

        def fake_get(url: str, params: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            assert '"nr"' in params.get("filter", "")
            resp = MagicMock()
            resp.json.return_value = {"result": {"data": fake_rows}}
            return resp

        monkeypatch.setattr(real_requests, "get", fake_get)
        df = provider.get_warehouse_receipt("NR0", days=60)
        assert df["warehouse_receipt"].iloc[0] == 5000

    def test_em_warehouse_no_mapping_empty(self) -> None:
        """无东财映射的品种（如欧线 EC）→ 空且不请求。"""
        provider = AkshareFuturesFundamentalProvider()
        assert provider.get_warehouse_receipt("EC0").empty

    def test_index_futures_empty(self) -> None:
        """中金所股指无商品仓单 → 空。"""
        provider = AkshareFuturesFundamentalProvider()
        assert provider.get_warehouse_receipt("IF0").empty

    def test_unknown_symbol_empty(self) -> None:
        provider = AkshareFuturesFundamentalProvider()
        assert provider.get_warehouse_receipt("XX0").empty

    def test_cached_no_second_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake_czce(date: str) -> dict:
            calls.append(date)
            return {"SR": pd.DataFrame({"仓单数量": [459], "当日增减": [-18]})}

        monkeypatch.setattr(ak, "futures_warehouse_receipt_czce", fake_czce)
        provider.get_warehouse_receipt("SR0", days=60)
        first = len(calls)
        assert first > 0
        provider.get_warehouse_receipt("SR0", days=60)
        assert len(calls) == first  # 缓存命中

    def test_partial_days_failure_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """部分日请求失败 → 跳过，其余成功日保留。"""
        provider = AkshareFuturesFundamentalProvider()
        calls: list[str] = []

        def fake_czce(date: str) -> dict:
            calls.append(date)
            if len(calls) == 1:
                raise ConnectionError("boom")
            return {"SR": pd.DataFrame({"仓单数量": [459], "当日增减": [-18]})}

        monkeypatch.setattr(ak, "futures_warehouse_receipt_czce", fake_czce)
        df = provider.get_warehouse_receipt("SR0", days=60)
        assert not df.empty  # 失败日被跳过，成功日保留

    def test_core_subset_all_routed(self) -> None:
        """核心品种子集全部可路由到交易所（仓单分发无遗漏）。"""
        from fts.data_futures import FUTURES_CORE_SUBSET

        missing = [s for s in FUTURES_CORE_SUBSET if s.upper().rstrip("0") not in VARIETY_EXCHANGE]
        assert missing == [], f"无交易所路由品种: {missing}"

    def test_stage1_exchanges_covered(self) -> None:
        """阶段 1 可用交易所品种（CZCE/GFEX）均已在映射表。"""
        for v, ex in VARIETY_EXCHANGE.items():
            if ex in ("czce", "gfex"):
                assert v in VARIETY_MAP, f"{v} 缺库存映射"

    def test_stage2_em_mapping_covers_shfe_dce_ine(self) -> None:
        """阶段 2：SHFE/DCE/INE 品种（EC 无实物仓单、FB/BB 东财无数据）均有东财仓单映射。"""
        from fts.data_futures_fundamental import EM_WAREHOUSE_MAP

        for v, ex in VARIETY_EXCHANGE.items():
            if ex in ("shfe", "dce", "ine") and v not in ("EC", "FB", "BB"):
                assert v in EM_WAREHOUSE_MAP, f"{v} 缺东财仓单映射"

    def test_em_mapping_no_orphan(self) -> None:
        """东财仓单映射无孤儿（品种均在交易所路由表）。"""
        from fts.data_futures_fundamental import EM_WAREHOUSE_MAP

        for v in EM_WAREHOUSE_MAP:
            assert v in VARIETY_EXCHANGE, f"{v} 为孤儿映射"
