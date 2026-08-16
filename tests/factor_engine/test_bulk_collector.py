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
    """东财研报采集（mock http，关键词过滤）。"""

    def test_filter_by_keywords(self, monkeypatch):
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
        assert len(recs) == 1, "仅命中关键词研报"
        assert "能化" in recs[0]["title"]
        assert recs[0]["language"] == "zh"


class TestCollectAll:
    """全源采集契约（mock 全源返回空 → 计数归零不崩溃；含 nonen 开关）。"""

    def test_collect_all_counts(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        results = bulk_collector.collect_all(store=store, max_results=3, page_size=10)
        assert set(results.keys()) == {"arxiv", "openalex", "eastmoney", "global", "nonen"}
        total = sum(r.collected for r in results.values())
        assert total == 0  # 全源失败 → 如实计数 0（不虚报）
        assert results["nonen"].errors, "nonen 源失败应如实记录"

    def test_collect_all_nonen_disabled(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_collector

        monkeypatch.setattr(bulk_collector, "_http_get", lambda *a, **k: None)
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        results = bulk_collector.collect_all(store=store, max_results=3, page_size=10, non_en_reports_enabled=False)
        assert results["nonen"].collected == 0
        assert any("disabled" in e for e in results["nonen"].errors)
