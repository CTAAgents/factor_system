"""tests/data_sources/test_macro_eastmoney_source.py — 东财宏观数据源测试。

覆盖: 东财 CPI/进出口归一化、中债登 1 年期、美债 10 年期、edb_cache 读写、
未映射指标、拉取失败降级。全部 monkeypatch 隔离网络。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fts.data_sources.macro_eastmoney_source import (  # noqa: E402
    EastmoneyMacroSource,
    _CN_BOND_CURVE,
)


def _em_rows(rows: list[dict[str, object]]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"success": True, "code": 200, "result": {"data": rows}}
    return resp


class TestFetchEm:
    def test_cpi_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """东财 CPI 报表 → DatetimeIndex Series（同比列）。"""
        import requests as real_requests

        rows = [
            {"REPORT_DATE": "2026-06-01 00:00", "NATIONAL_SAME": "0.2"},
            {"REPORT_DATE": "2026-07-01 00:00", "NATIONAL_SAME": "0.3"},
        ]

        def fake_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            assert params["reportName"] == "RPT_ECONOMY_CPI"
            return _em_rows(rows)

        monkeypatch.setattr(real_requests, "get", fake_get)
        s = EastmoneyMacroSource._fetch_em("RPT_ECONOMY_CPI", "NATIONAL_SAME", "REPORT_DATE")
        assert s is not None and len(s) == 2
        assert s.index[0] == pd.Timestamp("2026-06-01")
        assert s.iloc[-1] == 0.3

    def test_customs_export_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """东财海关进出口 → EXIT_BASE/IMPORT_BASE（万元人民币）。"""
        import requests as real_requests

        rows = [{"REPORT_DATE": "2026-07-01 00:00", "EXIT_BASE": "186931000", "IMPORT_BASE": "168569000"}]

        def fake_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            return _em_rows(rows)

        monkeypatch.setattr(real_requests, "get", fake_get)
        s = EastmoneyMacroSource._fetch_em("RPT_ECONOMY_CUSTOMS", "EXIT_BASE", "REPORT_DATE")
        assert s is not None and s.iloc[0] == 186931000

    def test_em_no_rows_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests as real_requests

        def fake_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            return _em_rows([])

        monkeypatch.setattr(real_requests, "get", fake_get)
        assert EastmoneyMacroSource._fetch_em("RPT_ECONOMY_CPI", "NATIONAL_SAME", "REPORT_DATE") is None


class TestFetchBonds:
    def test_cn_1y_multi_year_concat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """中债登 1 年期分年拼接，去重取最新。"""
        import akshare as ak

        def fake_bond_china(start_date: str, end_date: str) -> pd.DataFrame:
            # 每段返回同一日期 1 个点（模拟分年拉取重叠去重）
            day = end_date[:4] + "-07-01"
            return pd.DataFrame(
                {
                    "曲线名称": [_CN_BOND_CURVE, "中债中短期票据收益率曲线(AAA)"],
                    "日期": [day, day],
                    "1年": [1.2, 2.5],
                }
            )

        monkeypatch.setattr(ak, "bond_china_yield", fake_bond_china)
        s = EastmoneyMacroSource._fetch_cn_1y()
        assert s is not None and not s.empty
        # 只保留国债曲线（1.2），且去重
        assert (s == 1.2).all()

    def test_us_10y(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import akshare as ak

        def fake_us(start_date: str) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "日期": ["2026-08-08", "2026-08-11"],
                    "美国国债收益率10年": ["4.65", "4.72"],
                    "美国国债收益率2年": ["4.19", "4.25"],
                }
            )

        monkeypatch.setattr(ak, "bond_zh_us_rate", fake_us)
        s = EastmoneyMacroSource._fetch_us_10y()
        assert s is not None and len(s) == 2
        assert s.iloc[-1] == 4.72


class TestGetMacroSeries:
    def test_unknown_indicator_none(self) -> None:
        assert EastmoneyMacroSource().get_macro_series("不存在指标") is None

    def test_fetch_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """拉取异常 → None（降级不抛错）。"""
        import requests as real_requests

        def boom(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            raise ConnectionError("net down")

        monkeypatch.setattr(real_requests, "get", boom)
        assert EastmoneyMacroSource().get_macro_series("中国CPI当月同比") is None

    def test_cache_write_and_hit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """首次拉取写 edb_cache，二次直接命中（不请求网络）。"""
        import requests as real_requests

        db = tmp_path / "t.duckdb"
        src = EastmoneyMacroSource(cache_db_path=db)
        rows = [{"REPORT_DATE": "2026-07-01 00:00", "NATIONAL_SAME": "0.3"}]
        calls: list[int] = []

        def fake_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            calls.append(1)
            return _em_rows(rows)

        monkeypatch.setattr(real_requests, "get", fake_get)
        s1 = src.get_macro_series("中国CPI当月同比", db_path=db)
        assert s1 is not None and len(s1) == 1
        assert len(calls) == 1
        # 二次命中缓存，不再请求网络
        s2 = src.get_macro_series("中国CPI当月同比", db_path=db)
        assert s2 is not None and s2.iloc[0] == 0.3
        assert len(calls) == 1


class TestAlignerIntegration:
    def test_aligner_default_source_injects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """MacroFieldAligner 默认源（EastmoneyMacroSource）注入 CPI 列端到端。"""
        import requests as real_requests

        from fts.data_sources.macro_aligner import MacroFieldAligner

        db = tmp_path / "t.duckdb"
        rows = [{"REPORT_DATE": "2026-06-30 00:00", "NATIONAL_SAME": "0.2"}]

        def fake_get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int | None = None, **kwargs: object) -> MagicMock:
            return _em_rows(rows)

        monkeypatch.setattr(real_requests, "get", fake_get)
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=pd.bdate_range("2026-07-01", periods=3))
        aligner = MacroFieldAligner(lag_days=0, db_path=db)
        out = aligner.inject(df, fields=["cpi"])
        assert "cpi" in out.columns  # 7 月 K 线注入 6 月 CPI
        assert out["cpi"].iloc[0] == 0.2
