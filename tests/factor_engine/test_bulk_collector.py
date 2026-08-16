"""
tests/factor_engine/test_bulk_collector.py — plans/44 P0 批量采集层测试

覆盖:
    - BulkKnowledgeStore 增量去重（(source, ref_id) 主键）/ 读回
    - ArxivBulkCollector 拉取解析（mock http）
    - OpenAlexBulkCollector 多语种分路 + 摘要倒排索引还原
    - EastmoneyReportBulkCollector 关键词过滤
    - NonEnReportBulkCollector 非中英语种研报（IEEJ/KEEI/IFPEN）链接提取 + 独立失败
    - collect_bulk / collect_all 计数契约 + 失败降级 + nonen 开关

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from pathlib import Path


_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.bulk_collector import (  # noqa: E402
    ArxivBulkCollector,
    BulkKnowledgeStore,
    EastmoneyReportBulkCollector,
    NonEnReportBulkCollector,
    OpenAlexBulkCollector,
    collect_bulk,
)


class TestBulkKnowledgeStore:
    """增量存储去重与读回。"""

    def test_upsert_dedup_by_ref_id(self, tmp_path):
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        recs = [
            {"ref_id": "a1", "date": "2026-08-16", "title": "Alpha 1", "abstract": "x", "url": "", "language": "en"},
            {"ref_id": "a2", "date": "2026-08-16", "title": "Alpha 2", "abstract": "y", "url": "", "language": "en"},
        ]
        new, deduped = store.upsert("arxiv", recs)
        assert (new, deduped) == (2, 0)
        # 再次写入同一批 → 全部重复
        new2, deduped2 = store.upsert("arxiv", recs)
        assert (new2, deduped2) == (0, 2)
        # 部分新 + 部分重复
        recs3 = [{"ref_id": "a1", "date": "2026-08-16", "title": "dup", "abstract": "x", "url": "", "language": "en"},
                 {"ref_id": "a3", "date": "2026-08-16", "title": "Alpha 3", "abstract": "z", "url": "", "language": "en"}]
        new3, deduped3 = store.upsert("arxiv", recs3)
        assert (new3, deduped3) == (1, 1)

    def test_recent_readback(self, tmp_path):
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        store.upsert("arxiv", [{"ref_id": "a1", "date": "2026-08-16", "title": "T", "abstract": "A", "url": "", "language": "en"}])
        rows = store.recent(source="arxiv", since_days=3)
        assert len(rows) == 1
        assert rows[0]["title"] == "T"
        assert rows[0]["source"] == "arxiv"

    def test_unknown_source_collect_returns_error(self, tmp_path):
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        res = collect_bulk("nope", store=store)
        assert res.collected == 0
        assert res.errors, "未知源应记录错误"
        assert res.new == 0


class TestArxivBulkCollector:
    """arXiv 采集（mock http）。"""

    def test_fetch_parses_atom(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><id>http://arxiv.org/abs/2608.00001v1</id>
        <published>2026-08-16T00:00:00Z</published>
        <title>   Test Factor Paper  </title>
        <summary>  A summary line.  </summary></entry></feed>"""

        class FakeResp:
            status_code = 200
            content = atom.encode("utf-8")

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        collector = ArxivBulkCollector(max_results=3, categories=["q-fin.ST"])
        recs = collector.fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "Test Factor Paper"
        assert recs[0]["date"] == "2026-08-16"
        assert recs[0]["language"] == "en"

    def test_fetch_failure_returns_empty(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        assert ArxivBulkCollector(max_results=3).fetch() == []


class TestOpenAlexBulkCollector:
    """OpenAlex 采集（mock http，多语种分路 + 摘要倒排还原）。"""

    def test_fetch_reconstructs_abstract(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Global Commodity Factors",
                    "publication_date": "2026-08-15",
                    "doi": "https://doi.org/10.1/test",
                    "abstract_inverted_index": {"factor": [0], "test": [1]},
                }
            ]
        }

        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs = OpenAlexBulkCollector(max_results=5, languages=["en"]).fetch()
        assert len(recs) == 1
        assert recs[0]["abstract"] == "factor test"
        assert recs[0]["language"] == "en"

    def test_fetch_multi_language_routes(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        captured: list[str] = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {
                    "results": [
                        {
                            "id": "https://openalex.org/W9",
                            "title": "Factor",
                            "publication_date": "2026-08-15",
                            "doi": "d",
                            "abstract_inverted_index": {"x": [0]},
                        }
                    ]
                }

        def fake_get(url, params=None, headers=None, timeout=30):
            captured.append((params or {}).get("filter", ""))
            return FakeResp()

        monkeypatch.setattr(bulk_collector, "_http_get", fake_get)
        recs = OpenAlexBulkCollector(max_results=50, languages=["ja", "de"]).fetch()
        assert len(recs) == 2
        assert {r["language"] for r in recs} == {"ja", "de"}
        # 每语种分路 filter 带 language:xx，per-page 在语种间分摊
        assert any("language:ja" in f for f in captured)
        assert any("language:de" in f for f in captured)

    def test_fetch_failure_returns_empty(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        assert OpenAlexBulkCollector(max_results=5).fetch() == []


class TestNonEnReportBulkCollector:
    """非中英语种研报（IEEJ/KEEI/IFPEN）采集（mock html）。"""

    def test_fetch_extracts_links_with_language(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        pages = {
            "https://eneken.ieej.or.jp/": '<a href="/r">エネルギー価格レポート2026年第3四半期</a>',
            "https://www.keei.re.kr/keei.nsf/main/main": '<a href="/r">에너지가격 전망 2026 보고서</a>',
            "https://www.ifpenergiesnouvelles.com/": '<a href="/r">Étude marché pétrole 2026 perspectives</a>',
        }

        class FakeResp:
            status_code = 200
            text = ""

        def fake_get(url, params=None, headers=None, timeout=30):
            resp = FakeResp()
            resp.text = pages.get(url, "")
            return resp

        monkeypatch.setattr(bulk_collector, "_http_get", fake_get)
        recs, errors = NonEnReportBulkCollector(max_items=12).fetch()
        assert len(recs) == 3, "三个源各提取 1 个有效链接"
        assert {r["language"] for r in recs} == {"ja", "ko", "fr"}
        assert all(r["title"] for r in recs)
        assert not errors

    def test_fetch_partial_failure_continues(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        counter = {"n": 0}

        class FakeResp:
            status_code = 200
            text = '<a href="/a">エネルギー価格動向レポート2026年第3四半期</a>'

        def fake_get(*a, **k):
            counter["n"] += 1
            return None if counter["n"] == 2 else FakeResp()  # 第 2 个源失败

        monkeypatch.setattr(bulk_collector, "_http_get", fake_get)
        recs, errors = NonEnReportBulkCollector(max_items=12).fetch()
        assert len(errors) == 1, "单源失败仅记录，不阻断"
        assert len(recs) >= 1

    def test_no_links_reports_error(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        class FakeResp:
            status_code = 200
            text = "<html><body>no links</body></html>"

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs, errors = NonEnReportBulkCollector().fetch()
        assert recs == []
        assert errors


class TestEastmoneyBulkCollector:
    """东财研报采集（mock http，2026-08-17 起采集层保量不再关键词过滤）。"""

    def test_collects_all_rows(self, monkeypatch):
        """采集层保量：移除关键词硬过滤（相关性由下游粗筛层负责）。"""
        from fts.factor_engine.extractors import bulk_collector

        payload = {"data": [
            {"infoCode": "r1", "title": "能化产业链周报：原油与聚酯价差", "publishDate": "2026-08-16", "summary": "库存去化", "url": "u1"},
            {"infoCode": "r2", "title": "白酒行业跟踪", "publishDate": "2026-08-16", "summary": "消费复苏", "url": "u2"},
        ]}

        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs = EastmoneyReportBulkCollector(page_size=100, pages=1).fetch()
        assert len(recs) == 2, "采集层全部入库，不过滤关键词"
        assert "能化" in recs[0]["title"]
        assert recs[0]["language"] == "zh"
        assert recs[1]["title"] == "白酒行业跟踪"

    def test_request_params_include_begin_end_qtype(self, monkeypatch):
        """2026-08-17 修复 400：必填 beginTime/endTime + qType（缺参实测 400/500）。"""
        from fts.factor_engine.extractors import bulk_collector

        captured: list[dict] = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": []}

        def fake_get(url, params=None, headers=None, timeout=30):
            captured.append(params or {})
            return FakeResp()

        monkeypatch.setattr(bulk_collector, "_http_get", fake_get)
        EastmoneyReportBulkCollector(page_size=10, pages=1).fetch()
        assert captured, "应发起东财请求"
        for p in captured:
            assert "beginTime" in p, f"缺 beginTime: {p}"
            assert "endTime" in p, f"缺 endTime: {p}"
            assert "qType" in p, f"缺 qType: {p}"
            assert p["qType"] == 0


class TestEastmoneyCollectorNewFix:
    """2026-08-17 东财 400 修复回归：缺参接口拒绝 → 补 beginTime/endTime/qType 后 200。"""

    def test_no_begin_time_is_rejected(self, monkeypatch):
        """未传 beginTime 时接口返回 400（复现原始故障形态，应如实记录失败不崩溃）。"""
        from fts.factor_engine.extractors import bulk_collector

        class FakeResp400:
            status_code = 400

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp400())
        recs = EastmoneyReportBulkCollector(page_size=10, pages=1).fetch()
        assert recs == []


class TestRecentByCollectedAt:
    """2026-08-17 修复：recent() 按采集时间(collected_at)而非论文发布日(date)过滤。"""

    def test_old_publish_date_new_collected_at_included(self, tmp_path):
        """论文发布日很旧但当天采集 → 应出现在近 3 日窗口（修复前会被 date 过滤漏掉）。"""
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        # 发布日 2020-01-01，但采集时间=now（upsert 内部写 collected_at=now）
        store.upsert("arxiv", [{"ref_id": "a_old", "date": "2020-01-01", "title": "Old paper, fresh collect", "abstract": "x", "url": "", "language": "en"}])
        rows = store.recent(source="arxiv", since_days=3)
        assert len(rows) == 1, "采集日近 3 日应包含发布日很旧的论文"
        assert rows[0]["title"] == "Old paper, fresh collect"
        assert "collected_at" in rows[0]

    def test_old_collected_at_excluded(self, tmp_path):
        """采集时间超出窗口（模拟历史记录）→ 应被排除。"""
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        store.upsert("arxiv", [{"ref_id": "b1", "date": "2026-08-10", "title": "T", "abstract": "A", "url": "", "language": "en"}])
        # 直接改写 collected_at 为 30 天前模拟历史采集
        import duckdb

        with duckdb.connect(str(tmp_path / "knowledge.duckdb")) as con:
            con.execute("UPDATE l1_knowledge_cache SET collected_at = ?", ["2026-07-01T00:00:00"])
        rows = store.recent(source="arxiv", since_days=3)
        assert len(rows) == 0, "采集时间超窗口应被排除"


class TestNonEnNavFilter:
    """2026-08-17 修复：nonen 源过滤导航垃圾链接（IFPEN 导航项不再入库）。"""

    def test_navigation_links_filtered(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        html = """
        <a href="https://www.ifpenergiesnouvelles.com/#main-content">Skip to main content</a>
        <a href="https://www.ifpenergiesnouvelles.com/ifpen/presentation">Presentation</a>
        <a href="https://www.ifpenergiesnouvelles.com/ifpen/organization/governance">Governance</a>
        <a href="/research">Étude marché pétrole 2026 perspectives</a>
        """

        class FakeResp:
            status_code = 200
            text = html

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs, errors = NonEnReportBulkCollector().fetch()
        titles = [r["title"] for r in recs]
        assert "Étude marché pétrole 2026 perspectives" in titles, "有效研报应保留"
        assert not any("Skip" in t or "Presentation" in t or "Governance" in t for t in titles), f"导航项应过滤: {titles}"
        assert all(r["url"] for r in recs)


class TestNewSources:
    """2026-08-17 新增源：Crossref / NBER / 巨潮 / 新浪 / Semantic Scholar。"""

    def test_crossref_parses(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        payload = {"message": {"items": [
            {"DOI": "10.1000/test1", "title": ["Commodity Futures Basis Factor"], "abstract": "<jats:p>Abstract text</jats:p>", "issued": {"date-parts": [[2026, 8]]}, "container-title": ["Journal of Commodity Markets"]},
        ]}}

        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs = bulk_collector.CrossrefBulkCollector(max_results=5).fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "Commodity Futures Basis Factor"
        assert recs[0]["url"] == "https://doi.org/10.1000/test1"
        assert "Abstract text" in recs[0]["abstract"], "JATS XML 标签应被剥离"

    def test_nber_parses(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        payload = {"results": [
            {"id": "w31234", "title": "Commodity Price Dynamics", "abstract": "We study commodity futures.", "displaydate": "August 2026", "url": "https://www.nber.org/papers/w31234"},
        ]}

        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs = bulk_collector.NberBulkCollector(max_results=5).fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "Commodity Price Dynamics"
        assert recs[0]["ref_id"] == "w31234"
        assert recs[0]["language"] == "en"

    def test_cninfo_parses(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        payload = {"announcements": [
            {"announcementId": "1225473897", "announcementTitle": "滨化股份关于开展<em>期货</em>及衍生品套期保值业务计划的公告", "announcementTime": 1786723200000, "adjunctUrl": "finalpage/2026-08-15/1225473897.PDF"},
        ]}

        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(bulk_collector.requests, "post", lambda *a, **k: FakeResp())
        recs = bulk_collector.CninfoBulkCollector(max_items=30, keywords=["期货"]).fetch()
        assert len(recs) == 1
        assert "套期保值" in recs[0]["title"], "em 高亮标签应剥离"
        assert recs[0]["date"] == "2026-08-15"
        assert "static.cninfo.com.cn" in recs[0]["url"]

    def test_sina_parses(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        html = '<a href="https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/1234/index.phtml">能化产业链周报：原油去库与聚酯价差</a>'

        class FakeResp:
            status_code = 200
            text = html

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp())
        recs = bulk_collector.SinaReportBulkCollector(max_items=10, keywords=["能化"]).fetch()
        assert len(recs) >= 1
        assert "能化产业链周报" in recs[0]["title"]
        assert recs[0]["language"] == "zh"

    def test_semantic_scholar_retries_on_429_then_succeeds(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        calls = {"n": 0}

        class FakeResp429:
            status_code = 429
            headers = {}

        class FakeResp200:
            status_code = 200

            def json(self):
                return {"data": [{"paperId": "p1", "title": "CTA Factor Investing", "abstract": "A", "url": "https://api.semanticscholar.org/CorpusID:1", "year": 2026}]}

        def fake_get(*a, **k):
            calls["n"] += 1
            return FakeResp429() if calls["n"] == 1 else FakeResp200()

        monkeypatch.setattr(bulk_collector, "_http_get", fake_get)
        monkeypatch.setattr(bulk_collector.time, "sleep", lambda s: None)
        recs = bulk_collector.SemanticScholarBulkCollector(max_results=5, retries=3).fetch()
        assert len(recs) == 1, "429 重试后应成功"
        assert recs[0]["title"] == "CTA Factor Investing"

    def test_semantic_scholar_all_429_returns_empty(self, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        class FakeResp429:
            status_code = 429
            headers = {}

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: FakeResp429())
        monkeypatch.setattr(bulk_collector.time, "sleep", lambda s: None)
        recs = bulk_collector.SemanticScholarBulkCollector(max_results=5, retries=2).fetch()
        assert recs == [], "429 重试耗尽应返回空不崩溃"


class TestCollectAll:
    """全源采集契约（mock 全源返回空 → 计数归零不崩溃；含 nonen 开关）。"""

    def test_collect_all_counts(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        # cninfo 走 requests.post（2026-08-17 修复），需一并屏蔽
        monkeypatch.setattr(bulk_collector.requests, "post", lambda *a, **k: None)
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        # include_dynamic=False：固定源契约校验（动态源为空注册表场景另测）
        results = bulk_collector.collect_all(store=store, max_results=3, page_size=10, include_dynamic=False)
        assert set(results.keys()) == {
            "arxiv", "openalex", "eastmoney", "global", "nonen",
            "crossref", "nber", "cninfo", "sina", "semanticscholar",
        }
        total = sum(r.collected for r in results.values())
        assert total == 0  # 全源失败 → 如实计数 0（不虚报）
        assert results["nonen"].errors, "nonen 源失败应如实记录"

    def test_collect_all_nonen_disabled(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        monkeypatch.setattr(bulk_collector.requests, "post", lambda *a, **k: None)
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        results = bulk_collector.collect_all(store=store, max_results=3, page_size=10, non_en_reports_enabled=False)
        assert results["nonen"].collected == 0
        assert any("disabled" in e for e in results["nonen"].errors)


class TestDynamicSourceCollector:
    """plans/46 动态源采集器（按注册表 type 驱动解析）。"""

    def test_json_source(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        payload = {"items": [{"title": "Global Oil Report", "url": "https://a.com/1", "abstract": "x", "date": "2026-08-16"}]}
        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: type("R", (), {"status_code": 200, "json": lambda self: payload, "text": ""})())
        c = bulk_collector.DynamicSourceCollector("d1", "https://a.com/api", type_="json", max_items=5)
        recs = c.fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "Global Oil Report"
        assert recs[0]["language"] == "en"

    def test_rss_source(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        rss = b'<?xml version="1.0"?><rss><channel><item><title>Paper A</title><link>https://a.com/1</link><pubDate>2026-08-16</pubDate><description>d</description></item></channel></rss>'
        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: type("R", (), {"status_code": 200, "content": rss, "text": ""})())
        c = bulk_collector.DynamicSourceCollector("d1", "https://a.com/feed", type_="rss", max_items=5)
        recs = c.fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "Paper A"

    def test_html_source_links(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        html = '<html><a href="https://a.com/1">Energy &amp; Commodity Market Weekly Report</a></html>'
        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: type("R", (), {"status_code": 200, "text": html, "content": html.encode()})())
        c = bulk_collector.DynamicSourceCollector("d1", "https://a.com/reports", type_="html", max_items=5)
        recs = c.fetch()
        assert len(recs) == 1
        assert "&amp;" not in recs[0]["title"], "HTML 实体应解码"
        assert "&" in recs[0]["title"]

    def test_html_source_llm_fallback(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        # 规则提取为空（无链接）→ LLM 兜底
        html = "<html><body>No links here, only text about energy research reports</body></html>"
        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: type("R", (), {"status_code": 200, "text": html, "content": html.encode()})())

        class FakeLLM:
            def generate_json(self, prompt):
                return [{"title": "LLM Extracted Report", "url": "https://a.com/x", "abstract": "a"}]

        c = bulk_collector.DynamicSourceCollector("d1", "https://a.com/r", type_="html", max_items=5, llm_client=FakeLLM())
        recs = c.fetch()
        assert len(recs) == 1
        assert recs[0]["title"] == "LLM Extracted Report"

    def test_collect_all_includes_dynamic_sources(self, tmp_path, monkeypatch):
        """plans/46：collect_all 叠加注册表 active 动态源 + pending canary。"""
        from fts.factor_engine.extractors import bulk_collector
        from fts.factor_engine.extractors.source_registry import SourceInfo, SourceRegistry

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        monkeypatch.setattr(bulk_collector.requests, "post", lambda *a, **k: None)
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="dyn1", name="Dyn", url="https://dyn.com", type="rss", status="active"))
        reg.upsert(SourceInfo(source_id="dyn2", name="Pending", url="https://pend.com", type="html", status="pending"))
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        results = bulk_collector.collect_all(store=store, max_results=2, page_size=5, registry=reg)
        assert "dyn1" in results and "dyn2" in results, "动态源应进入采集结果"
        # 全源 mock 失败 → dyn1 collected=0 → mark_collect_failure
        assert reg.get("dyn1").consecutive_failures >= 1
        # pending 源 canary_tick 失败 → 不晋升
        assert reg.get("dyn2").status == "pending"
