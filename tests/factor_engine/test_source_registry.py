"""
tests/factor_engine/test_source_registry.py — plans/46 注册表+探活+发现层测试

覆盖:
    - SourceRegistry CRUD / 状态流转（pending→active→cooldown→retired）/ 健康度双维度
    - canary 试采晋升与失败冷却
    - 因子产出淘汰与复权（业务维度）
    - SourceProber 探活类型识别（JSON/RSS/HTML/PDF）+ 评分 + 坏源拒绝
    - SourceDiscoverer LLM 提取（mock）/ 规则降级 / URL 去重 / 探活不达标不注册
"""

from __future__ import annotations

import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.source_discovery import (  # noqa: E402
    DiscoveryStore,
)
from fts.factor_engine.extractors.source_registry import (  # noqa: E402
    STATUS_ACTIVE,
    STATUS_COOLDOWN,
    STATUS_PENDING,
    STATUS_RETIRED,
    SourceInfo,
    SourceProber,
    SourceRegistry,
    is_probe_acceptable,
)


class TestSourceRegistry:
    def test_upsert_and_get(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        info = SourceInfo(source_id="s1", name="Test Source", url="https://example.com", type="rss")
        reg.upsert(info)
        got = reg.get("s1")
        assert got is not None
        assert got.name == "Test Source"
        assert got.status == STATUS_PENDING
        assert got.probe_score == 0.0

    def test_upsert_idempotent(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com"))
        reg.upsert(SourceInfo(source_id="s1", name="A2", url="https://a.com"))
        assert len(reg.list_all()) == 1, "source_id 主键幂等"
        assert reg.get("s1").name == "A2"

    def test_get_by_url_and_list_active(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com", status=STATUS_ACTIVE))
        assert reg.get_by_url("https://a.com") is not None
        active = reg.list_active()
        assert len(active) == 1
        assert active[0].source_id == "s1"

    def test_mark_collect_failure_cooldown_then_retired(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com", status=STATUS_ACTIVE))
        # 3 次连续失败 → cooldown
        for _ in range(3):
            reg.mark_collect_failure("s1")
        assert reg.get("s1").status == STATUS_COOLDOWN
        # 累计 10 次 → retired
        for _ in range(7):
            reg.mark_collect_failure("s1")
        assert reg.get("s1").status == STATUS_RETIRED

    def test_mark_collect_success_resets_failures(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com", status=STATUS_ACTIVE))
        reg.mark_collect_failure("s1")
        reg.mark_collect_success("s1")
        assert reg.get("s1").consecutive_failures == 0

    def test_zero_output_demotes_and_has_output_recovers(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com", status=STATUS_ACTIVE))
        # 5 轮零产出 → cooldown（业务维度）
        for _ in range(5):
            reg.mark_zero_output("s1")
        assert reg.get("s1").status == STATUS_COOLDOWN
        assert reg.get("s1").zero_output_rounds == 5
        # 恢复产出 → 复权（仅清零计数，状态由调度侧恢复 active）
        reg.mark_has_output("s1")
        assert reg.get("s1").zero_output_rounds == 0

    def test_canary_promotes_after_n_success(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com"))
        for _ in range(2):
            assert not reg.canary_tick("s1", ok=True, canary_rounds=3)
        assert reg.canary_tick("s1", ok=True, canary_rounds=3), "第 3 次成功应晋升 active"
        assert reg.get("s1").status == STATUS_ACTIVE

    def test_canary_failure_cooldown(self, tmp_path):
        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="s1", name="A", url="https://a.com"))
        for _ in range(3):
            reg.canary_tick("s1", ok=False, canary_rounds=3)
        assert reg.get("s1").status == STATUS_COOLDOWN, "canary 连续失败进入冷却"


class TestSourceProber:
    def _fake(self, status_code=200, text="", ctype="text/html"):
        class FakeResp:
            def __init__(self):
                self.status_code = status_code
                self.text = text
                self.content = text.encode("utf-8")
                self.headers = {"Content-Type": ctype}
                self.encoding = "utf-8"

        return FakeResp()

    def test_probe_json(self, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr

        monkeypatch.setattr(
            sr.requests,
            "get",
            lambda *a, **k: self._fake(ctype="application/json", text='{"items":[{"title":"t1"}]}'),
        )
        r = SourceProber().probe("https://example.com/api")
        assert r.type == sr.TYPE_JSON
        assert r.http_status == 200
        assert r.score >= 0.5
        assert is_probe_acceptable(r)

    def test_probe_rss(self, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr

        xml = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>Paper</title></item></channel></rss>'
        monkeypatch.setattr(
            sr.requests,
            "get",
            lambda *a, **k: self._fake(ctype="application/rss+xml", text=xml),
        )
        r = SourceProber().probe("https://example.com/feed")
        assert r.type == sr.TYPE_RSS
        assert is_probe_acceptable(r)

    def test_probe_html_with_links(self, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr

        html = '<html><head><title>Reports</title></head><body><a href="https://a.com/1">Report One</a><a href="https://a.com/2">Report Two</a></body></html>'
        monkeypatch.setattr(
            sr.requests,
            "get",
            lambda *a, **k: self._fake(text=html),
        )
        r = SourceProber().probe("https://example.com/reports")
        assert r.type == sr.TYPE_HTML
        assert r.sample_count >= 2
        assert is_probe_acceptable(r)

    def test_probe_http_failure_rejected(self, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr

        monkeypatch.setattr(sr.requests, "get", lambda *a, **k: self._fake(status_code=403))
        r = SourceProber().probe("https://example.com/blocked")
        assert r.status == "fail"
        assert not is_probe_acceptable(r), "403 不应通过探活"

    def test_probe_unknown_type_low_score_rejected(self, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr

        monkeypatch.setattr(
            sr.requests,
            "get",
            lambda *a, **k: self._fake(text="binary garbage no structure no links at all"),
        )
        r = SourceProber().probe("https://example.com/x")
        assert not is_probe_acceptable(r), "无结构低分源不应注册"


class TestSourceDiscoverer:
    def test_discover_registers_probe_ok(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr
        from fts.factor_engine.extractors.source_discovery import SourceDiscoverer

        class FakeLLM:
            def generate_json(self, prompt):
                return [{"name": "CFTC", "url": "https://www.cftc.gov", "type": "html", "region": "en"}]

        html = '<html><head><title>CFTC Reports</title></head><body><a href="https://www.cftc.gov/r1">Commitments of Traders</a></body></html>'

        def fake_get(*a, **k):
            return type("R", (), {"raise_for_status": lambda self: None, "text": html, "status_code": 200, "headers": {"Content-Type": "text/html"}, "content": html.encode("utf-8"), "encoding": "utf-8"})()
        fake_req = type("RR", (), {"get": staticmethod(fake_get)})
        monkeypatch.setattr(sr, "requests", fake_req)
        monkeypatch.setattr("fts.factor_engine.extractors.source_discovery.requests", fake_req)

        reg = SourceRegistry(tmp_path / "sources.duckdb")
        d = SourceDiscoverer(
            llm_client=FakeLLM(),
            registry=reg,
            store=DiscoveryStore(tmp_path / "disc.duckdb"),
            max_candidates=5,
        )
        registered = d.discover(trace_id="t1")
        assert len(registered) == 1, "探活达标的候选应注册"
        assert reg.get(registered[0].source_id) is not None
        assert reg.get(registered[0].source_id).status == STATUS_PENDING

    def test_discover_skips_existing_url(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr
        from fts.factor_engine.extractors.source_discovery import SourceDiscoverer

        class FakeLLM:
            def generate_json(self, prompt):
                return [{"name": "Dup", "url": "https://duplicate.com", "type": "html"}]

        html = '<html><head><title>Dup</title></head><body><a href="https://duplicate.com/r">A Report</a></body></html>'

        def fake_get(*a, **k):
            return type("R", (), {"raise_for_status": lambda s: None, "text": html, "status_code": 200, "headers": {"Content-Type": "text/html"}, "content": html.encode("utf-8"), "encoding": "utf-8"})()
        fake_req = type("RR", (), {"get": staticmethod(fake_get)})
        monkeypatch.setattr(sr, "requests", fake_req)
        monkeypatch.setattr("fts.factor_engine.extractors.source_discovery.requests", fake_req)

        reg = SourceRegistry(tmp_path / "sources.duckdb")
        reg.upsert(SourceInfo(source_id="dup", name="Dup", url="https://duplicate.com", status=STATUS_ACTIVE))
        d = SourceDiscoverer(llm_client=FakeLLM(), registry=reg, store=DiscoveryStore(tmp_path / "disc.duckdb"))
        registered = d.discover(trace_id="t2")
        assert registered == [], "已注册 URL 不应重复注册"

    def test_discover_rejects_probe_fail(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr
        from fts.factor_engine.extractors.source_discovery import SourceDiscoverer

        class FakeLLM:
            def generate_json(self, prompt):
                return [{"name": "Bad", "url": "https://bad.example.com", "type": "html"}]

        def fake_get(*a, **k):
            return type("R", (), {"status_code": 404, "raise_for_status": lambda s: None, "text": ""})()
        fake_req = type("RR", (), {"get": staticmethod(fake_get)})
        monkeypatch.setattr(sr, "requests", fake_req)
        monkeypatch.setattr("fts.factor_engine.extractors.source_discovery.requests", fake_req)

        reg = SourceRegistry(tmp_path / "sources.duckdb")
        d = SourceDiscoverer(llm_client=FakeLLM(), registry=reg, store=DiscoveryStore(tmp_path / "disc.duckdb"))
        registered = d.discover(trace_id="t3")
        assert registered == [], "探活失败的候选不应注册"
        assert len(reg.list_all()) == 0, "注册表应为空"

    def test_rule_fallback_without_llm(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import source_registry as sr
        from fts.factor_engine.extractors.source_discovery import SourceDiscoverer

        html = '<html><body><a href="https://www.iea.org/oil-market-report">IEA Oil Market Report</a></body></html>'

        def fake_get(*a, **k):
            return type("R", (), {"raise_for_status": lambda s: None, "text": html, "status_code": 200, "headers": {"Content-Type": "text/html"}, "content": html.encode("utf-8"), "encoding": "utf-8"})()
        fake_req = type("RR", (), {"get": staticmethod(fake_get)})
        monkeypatch.setattr(sr, "requests", fake_req)
        monkeypatch.setattr("fts.factor_engine.extractors.source_discovery.requests", fake_req)

        d = SourceDiscoverer(
            llm_client=None,
            registry=SourceRegistry(tmp_path / "sources.duckdb"),
            store=DiscoveryStore(tmp_path / "disc.duckdb"),
            queries=("test query",),
        )
        # 无 LLM → 规则降级提取候选；链接页探活达标则注册
        registered = d.discover(trace_id="t4")
        assert isinstance(registered, list)
