"""tests/test_data_mcp_bridge.py — MCP 数据桥接层测试。

覆盖:
    1. MCPBridge 缓存加载/查询/批量/统计/缓存年龄
    2. 代码标准化 _normalize_code
    3. mx 响应解析 _parse_mx_response / 指标解析全路径
    4. save_cache 持久化 / get_bridge 单例

隔离性: 使用 monkeypatch 覆盖模块级 CACHE_FILE，避免写入真实 data/ 目录。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[1]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import fts.data_mcp_bridge as bridge_mod  # noqa: E402
from fts.data_mcp_bridge import (  # noqa: E402
    MCPBridge,
    _extract_code_from_sheet,
    _normalize_code,
    _parse_market_cap,
    _parse_metric_value,
    _parse_mx_response,
    _parse_number,
    _parse_percentage,
    get_bridge,
    save_cache,
)


# ─── 工具函数 ──────────────────────────────────────────────


def _write_cache(file_path: Path, data: dict, meta: dict | None = None) -> None:
    cache = {"meta": meta or {"source": "test", "updated_at": "2026-08-08T00:00:00"}, "data": data}
    file_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# ─── MCPBridge ─────────────────────────────────────────────


class TestMCPBridge:
    def test_load_missing_file(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        bridge = MCPBridge(tmp_path / "none.json")
        assert bridge.cache_size == 0
        assert "缓存不存在" in caplog.text

    def test_load_corrupted_file(self, tmp_path, caplog):
        fp = tmp_path / "cache.json"
        fp.write_text("{broken", encoding="utf-8")
        bridge = MCPBridge(fp)
        assert bridge.cache_size == 0
        assert "加载失败" in caplog.text

    def test_load_valid_cache(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"000001": {"pe_ttm": 5.5}})
        bridge = MCPBridge(fp)
        assert bridge.cache_size == 1
        assert bridge.cache_stocks == ["000001"]

    def test_get_fundamental_hit_and_miss(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"000001": {"pe_ttm": 5.5}})
        bridge = MCPBridge(fp)
        assert bridge.get_fundamental("000001")["pe_ttm"] == 5.5
        assert bridge.get_fundamental("999999") == {}

    def test_get_fundamental_code_normalization(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"600519": {"pb": 8.0}})
        bridge = MCPBridge(fp)
        assert bridge.get_fundamental("sh600519")["pb"] == 8.0
        assert bridge.get_fundamental("SH600519")["pb"] == 8.0

    def test_get_batch_filters_empty(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"000001": {"pe_ttm": 5.5}})
        bridge = MCPBridge(fp)
        result = bridge.get_batch(["000001", "no_such"])
        assert set(result) == {"000001"}

    def test_load_only_once(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"000001": {}})
        bridge = MCPBridge(fp)
        bridge.cache_size
        # 修改文件后再访问，_loaded=True 不重新加载
        _write_cache(fp, {"000002": {}})
        assert bridge.cache_size == 1

    def test_cache_age_hours_inf_when_missing(self, tmp_path):
        bridge = MCPBridge(tmp_path / "none.json")
        assert bridge.get_cache_age_hours() == float("inf")

    def test_cache_age_hours_computed(self, tmp_path):
        fp = tmp_path / "cache.json"
        _write_cache(fp, {"000001": {}})
        bridge = MCPBridge(fp)
        age = bridge.get_cache_age_hours()
        # 文件 mtime 与 datetime.now() 存在时钟舍入，允许极小负值（刚写入）
        assert age > -0.001
        assert age < 1.0  # 刚写入，小于 1 小时


# ─── 辅助函数 ──────────────────────────────────────────────


class TestNormalizeCode:
    def test_bare_code(self):
        assert _normalize_code("000001") == "000001"

    def test_prefixes(self):
        assert _normalize_code("sh600519") == "600519"
        assert _normalize_code("SZ000001") == "000001"
        assert _normalize_code("bj830799") == "830799"
        assert _normalize_code("hk00700") == "00700"

    def test_whitespace_and_case(self):
        assert _normalize_code(" 000001 ") == "000001"
        assert _normalize_code("SH600519") == "600519"


class TestParseHelpers:
    def test_parse_number(self):
        assert _parse_number("2.219元") == 2.219
        assert _parse_number("--") is None
        assert _parse_number("") is None
        assert _parse_number("abc") is None

    def test_parse_percentage(self):
        assert _parse_percentage("2.83%") == pytest.approx(0.0283)
        assert _parse_percentage("5.241倍") is None
        assert _parse_percentage("--") is None

    def test_parse_market_cap(self):
        assert _parse_market_cap("2257亿") == pytest.approx(2257e8)
        assert _parse_market_cap("1.5万亿") == pytest.approx(1.5e12)
        assert _parse_market_cap("800万") == pytest.approx(800e4)
        assert _parse_market_cap("100") == 100.0
        assert _parse_market_cap("--") is None

    def test_extract_code_from_sheet(self):
        assert _extract_code_from_sheet("平安银行(000001.SZ)", []) == "000001"
        assert _extract_code_from_sheet("", ["000002.SZ"]) == "000002"
        assert _extract_code_from_sheet("no code here", []) == ""


class TestParseMetricValue:
    def test_valuation_metrics(self):
        assert _parse_metric_value("市盈率PE(TTM)", "12.5") == ("pe_ttm", 12.5)
        assert _parse_metric_value("市盈率", "12.5") == ("pe_ttm", 12.5)
        assert _parse_metric_value("市盈率PE(TTM)", "-5") is None  # 非正不解析
        assert _parse_metric_value("市净率PB", "2.5") == ("pb", 2.5)
        assert _parse_metric_value("总市值", "2257亿")[0] == "total_market_cap"

    def test_quality_metrics(self):
        assert _parse_metric_value("净资产收益率ROE", "8.5%") == ("roe", 0.085)
        assert _parse_metric_value("净资产收益率ROE(加权)", "9.0%") == ("roe", 0.09)
        assert _parse_metric_value("每股收益EPS", "1.25") == ("eps", 1.25)
        assert _parse_metric_value("每股净资产", "6.0") == ("bps", 6.0)

    def test_growth_metrics(self):
        assert _parse_metric_value("营业收入同比增长率", "15.0%") == ("revenue_growth", 0.15)
        assert _parse_metric_value("净利润同比增长率", "-3.2%") == ("profit_growth", -0.032)

    def test_margin_metrics(self):
        assert _parse_metric_value("毛利率", "40.0%") == ("gross_margin", 0.4)
        assert _parse_metric_value("净利率", "12.5%") == ("net_margin", 0.125)

    def test_unrecognized_returns_none(self):
        assert _parse_metric_value("随机指标", "1.0") is None


class TestParseMxResponse:
    def test_parses_multiple_sheets(self):
        data = [
            {
                "sheetName": "平安银行(000001.SZ)",
                "columns": ["指标", "最新"],
                "items": [
                    ["市盈率PE(TTM)", "5.5"],
                    ["市净率PB", "0.9"],
                    ["总市值", "2257亿"],
                ],
            },
            {
                "sheetName": "万科A(000002.SZ)",
                "columns": ["指标", "最新"],
                "items": [
                    ["每股收益EPS", "2.0"],
                    ["毛利率", "30.0%"],
                ],
            },
        ]
        result = _parse_mx_response(data)
        assert set(result) == {"000001", "000002"}
        assert result["000001"]["pe_ttm"] == 5.5
        assert result["000001"]["total_market_cap"] == pytest.approx(2257e8)
        assert result["000002"]["eps"] == 2.0
        assert result["000002"]["gross_margin"] == pytest.approx(0.3)

    def test_skips_sheets_without_code(self):
        data = [{"sheetName": "无代码", "columns": [], "items": [["x", "1"]]}]
        assert _parse_mx_response(data) == {}

    def test_skips_short_rows_and_invalid_values(self):
        data = [
            {
                "sheetName": "测试(000001.SZ)",
                "columns": [],
                "items": [
                    ["市盈率PE(TTM)", "--"],
                    ["单列行"],  # len < 2 → 跳过
                    ["市净率PB", "1.2"],
                ],
            }
        ]
        result = _parse_mx_response(data)
        assert result["000001"] == {"pb": 1.2}


# ─── save_cache 与单例 ─────────────────────────────────────


class TestSaveCacheAndSingleton:
    def test_save_cache_writes_file(self, tmp_path, monkeypatch):
        target = tmp_path / "cache.json"
        monkeypatch.setattr(bridge_mod, "CACHE_FILE", target)
        save_cache({"000001": {"pe_ttm": 5.5}}, source="mx_test")
        raw = json.loads(target.read_text(encoding="utf-8"))
        assert raw["meta"]["source"] == "mx_test"
        assert raw["meta"]["stock_count"] == 1
        assert raw["data"]["000001"]["pe_ttm"] == 5.5

    def test_save_cache_roundtrip_with_bridge(self, tmp_path, monkeypatch):
        target = tmp_path / "cache.json"
        monkeypatch.setattr(bridge_mod, "CACHE_FILE", target)
        save_cache({"000001": {"pe_ttm": 5.5}})
        bridge = MCPBridge(target)
        assert bridge.get_fundamental("000001")["pe_ttm"] == 5.5

    def test_get_bridge_singleton(self, monkeypatch):
        monkeypatch.setattr(bridge_mod, "_default_bridge", None)
        assert get_bridge() is get_bridge()

    def test_get_bridge_uses_default_cache_file(self, monkeypatch):
        monkeypatch.setattr(bridge_mod, "_default_bridge", None)
        bridge = get_bridge()
        assert bridge._cache_file == bridge_mod.CACHE_FILE


# ─── 补充分支覆盖 ──────────────────────────────────────────


class TestParseMxResponseMultiSheetSameCode:
    def test_same_code_multiple_sheets_merged(self):
        """同一股票代码多个 sheet → 指标合并到同一记录（走已有记录 else 分支）。"""
        data = [
            {
                "sheetName": "平安银行(000001.SZ)",
                "columns": [],
                "items": [["市盈率PE(TTM)", "5.5"]],
            },
            {
                "sheetName": "平安银行A(000001.SZ)",
                "columns": [],
                "items": [["市净率PB", "0.9"]],
            },
        ]
        result = _parse_mx_response(data)
        assert result["000001"] == {"pe_ttm": 5.5, "pb": 0.9}


class TestParseEdgeCases:
    def test_percentage_bad_float_returns_none(self):
        """含 % 但数字部分非法 → None（except 分支）。"""
        assert _parse_percentage("abc%") is None

    def test_market_cap_bad_float_returns_none(self):
        """含单位但数字部分非法 → None（except 分支）。"""
        assert _parse_market_cap("abc亿") is None
        assert _parse_market_cap("xyz万") is None

    def test_number_units_stripped(self):
        """数字解析剥离单位（先长单位后"元"的顺序已修复）。"""
        assert _parse_number("2.219元") == 2.219
        # 修复前：先执行 replace("元","") 破坏"港元/美元"后缀 → float 失败返回 None
        assert _parse_number("3.5港元") == 3.5
        assert _parse_number("2.0美元") == 2.0

    def test_metric_value_invalid_percentage_number(self):
        """指标值无法解析时该指标被跳过（不返回键）。"""
        # "加权" 分支之前已被非加权分支拦截的说明见报告：ROE(加权) 命中首分支
        assert _parse_metric_value("净资产收益率ROE", "abc") is None
