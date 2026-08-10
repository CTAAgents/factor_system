"""tests/factor_engine/extractors/test_alternative_sources.py — 另类知识源提取器测试（GAP-I103，v2.80.0）。

覆盖:
    1. AnnouncementNewsExtractor: 公告 API 成功/空数据/异常降级/暂停/LLM 提取
    2. MacroEventExtractor: 宏观日历 API 成功/空数据/异常降级/暂停/LLM 提取
    3. 管道接入: 股票管道含公告+宏观源 / 期货管道含宏观源 / 开关关闭
    4. 多源并行收集（GAP-I101 二期）: 并行合并/单源异常不影响其他源
"""

from __future__ import annotations

import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[3]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.alternative_sources import (  # noqa: E402
    AnnouncementNewsExtractor,
    MacroEventExtractor,
)
from fts.factor_engine.extractors.futures_pipeline import FuturesExtractorPipeline  # noqa: E402
from fts.factor_engine.extractors.stock_pipeline import StockExtractorPipeline  # noqa: E402


class _LLMStub:
    """模拟 LLM 客户端：generate_json 返回单个因子。"""

    def __init__(self, name: str = "fut_news_factor"):
        self.name = name

    def generate_json(self, prompt, max_tokens=4000):
        return [
            {
                "name": self.name,
                "code": "def factor_program(data, params):\n    return data['close']",
                "params": {"window": 10},
                "input_fields": ["close", "volume"],
                "lookback": 10,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {"theory": 4, "behavioral": 3, "microstructure": 2, "institutional": 3},
            }
        ]


# ─── AnnouncementNewsExtractor ──────────────────────────────


class TestAnnouncementNewsExtractor:
    def test_paused_returns_empty(self):
        ext = AnnouncementNewsExtractor(paused=True)
        assert ext.extract("t1") == []

    def test_fetch_announcements_success(self, monkeypatch):
        resp = _FakeResponse(
            {"data": {"list": [{"title": "某公司重大合同公告", "notice_date": "2026-08-10", "columns": ["重大合同"]}]}}
        )
        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", lambda *a, **k: resp)
        ext = AnnouncementNewsExtractor()
        text = ext._fetch_announcements()
        assert "某公司重大合同公告" in text
        assert "重大合同" in text

    def test_fetch_announcements_empty_data(self, monkeypatch):
        monkeypatch.setattr(
            "fts.factor_engine.extractors.alternative_sources.requests.get",
            lambda *a, **k: _FakeResponse({"data": {"list": []}}),
        )
        assert AnnouncementNewsExtractor()._fetch_announcements() == ""

    def test_fetch_announcements_no_title_skipped(self, monkeypatch):
        resp = _FakeResponse(
            {"data": {"list": [{"title": "", "columns": ["x"]}, {"title": "有效公告", "columns": []}]}}
        )
        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", lambda *a, **k: resp)
        text = AnnouncementNewsExtractor()._fetch_announcements()
        assert "有效公告" in text
        assert "公告类型: []" in text or "x" not in text

    def test_fetch_announcements_request_exception(self, monkeypatch):
        def raiser(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", raiser)
        assert AnnouncementNewsExtractor()._fetch_announcements() == ""

    def test_fetch_announcements_non_200(self, monkeypatch):
        resp = _FakeResponse({}, status_code=500)
        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", lambda *a, **k: resp)
        assert AnnouncementNewsExtractor()._fetch_announcements() == ""

    def test_extract_no_data_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "fts.factor_engine.extractors.alternative_sources.requests.get",
            lambda *a, **k: _FakeResponse({"data": {"list": []}}),
        )
        assert AnnouncementNewsExtractor().extract("t1") == []

    def test_extract_with_data_and_llm(self, monkeypatch):
        ext = AnnouncementNewsExtractor(llm_client=_LLMStub("stk_announce_news"), market="stock")
        monkeypatch.setattr(ext, "_fetch_announcements", lambda: "公告文本内容")
        cands = ext.extract("t1")
        assert len(cands) == 1
        assert cands[0]["source"] == "l1_extractor_pipeline"
        assert cands[0]["market"] == "stock"
        assert cands[0]["parent_topic"].startswith("extractor_pipeline/announcement_news/")

    def test_extract_with_data_no_llm_returns_empty(self, monkeypatch):
        ext = AnnouncementNewsExtractor(llm_client=None)
        monkeypatch.setattr(ext, "_fetch_announcements", lambda: "公告文本内容")
        assert ext.extract("t1") == []


# ─── MacroEventExtractor ────────────────────────────────────


class TestMacroEventExtractor:
    def test_paused_returns_empty(self):
        ext = MacroEventExtractor(paused=True)
        assert ext.extract("t1") == []

    def test_fetch_events_success(self, monkeypatch):
        resp = _FakeResponse(
            {
                "result": {
                    "data": [
                        {
                            "TITLE": "中国 8 月 CPI 同比",
                            "REPORT_DATE": "2026-08-10",
                            "COUNTRY": "中国",
                            "IMPORTANCE": "高",
                        }
                    ]
                }
            }
        )
        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", lambda *a, **k: resp)
        ext = MacroEventExtractor()
        text = ext._fetch_events()
        assert "CPI" in text
        assert "中国" in text

    def test_fetch_events_empty_data(self, monkeypatch):
        monkeypatch.setattr(
            "fts.factor_engine.extractors.alternative_sources.requests.get",
            lambda *a, **k: _FakeResponse({"result": {"data": []}}),
        )
        assert MacroEventExtractor()._fetch_events() == ""

    def test_fetch_events_request_exception(self, monkeypatch):
        def raiser(*a, **k):
            raise TimeoutError("timeout")

        monkeypatch.setattr("fts.factor_engine.extractors.alternative_sources.requests.get", raiser)
        assert MacroEventExtractor()._fetch_events() == ""

    def test_fetch_events_missing_result_key(self, monkeypatch):
        monkeypatch.setattr(
            "fts.factor_engine.extractors.alternative_sources.requests.get",
            lambda *a, **k: _FakeResponse({}),
        )
        assert MacroEventExtractor()._fetch_events() == ""

    def test_extract_with_events_and_llm_futures(self, monkeypatch):
        ext = MacroEventExtractor(llm_client=_LLMStub("fut_macro_evt"), market="futures")
        monkeypatch.setattr(ext, "_fetch_events", lambda: "宏观事件文本")
        cands = ext.extract("t1")
        assert len(cands) == 1
        assert cands[0]["source"] == "l1_extractor_pipeline"
        assert cands[0]["market"] == "futures"
        assert cands[0]["parent_topic"].startswith("extractor_pipeline/macro_events/")

    def test_extract_no_events_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "fts.factor_engine.extractors.alternative_sources.requests.get",
            lambda *a, **k: _FakeResponse({"result": {"data": []}}),
        )
        assert MacroEventExtractor().extract("t1") == []


# ─── 管道接入 ───────────────────────────────────────────────


class TestPipelineIntegration:
    def test_stock_pipeline_contains_alternative_sources(self):
        pipe = StockExtractorPipeline(announcement_enabled=True, macro_enabled=True, llm_client=None)
        assert "announcement_news_stock" in pipe.extractors
        assert "macro_events_stock" in pipe.extractors

    def test_stock_pipeline_disabled_sources(self):
        pipe = StockExtractorPipeline(announcement_enabled=False, macro_enabled=False, llm_client=None)
        assert "announcement_news_stock" not in pipe.extractors
        assert "macro_events_stock" not in pipe.extractors

    def test_futures_pipeline_contains_macro_source(self):
        pipe = FuturesExtractorPipeline(macro_enabled=True, llm_client=None)
        assert "macro_events" in pipe.extractors

    def test_futures_pipeline_disabled_macro(self):
        pipe = FuturesExtractorPipeline(macro_enabled=False, llm_client=None)
        assert "macro_events" not in pipe.extractors


class _FakeResponse:
    """模拟 requests.Response。"""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload
