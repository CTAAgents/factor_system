"""
tests/factor_engine/test_l1_knowledge_pipeline.py — plans/44 A1 动态检索 + P0 深读提取器测试

覆盖:
    - KnowledgeGapQueryGenerator 知识缺口 query 生成（已覆盖维度跳过）
    - WebSearchExtractor 动态检索方向刷新
    - BulkKnowledgeExtractor 采集→粗筛→深读编排（mock 全链路）

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.bulk_collector import BulkKnowledgeStore  # noqa: E402
from fts.factor_engine.extractors.bulk_knowledge import BulkKnowledgeExtractor  # noqa: E402
from fts.factor_engine.extractors.web_search import KnowledgeGapQueryGenerator, WebSearchExtractor  # noqa: E402


class TestKnowledgeGapQueryGenerator:
    """A1 知识缺口 query 生成。"""

    def test_generates_queries_for_uncovered_dims(self):
        # 已注入仅覆盖"库存"维度 → 其余维度缺口 query
        gen = KnowledgeGapQueryGenerator(injected_names=["fut_inventory_factor", "库存周期因子"])
        queries = gen.generate(max_queries=6)
        assert queries, "应生成缺口 query"
        assert len(queries) <= 6
        assert all("库存" not in q for q in queries), "已覆盖维度不生成 query"

    def test_all_covered_returns_empty(self):
        all_dims = "库存 基差 季节性 展期 波动率聚集 情绪 开工率 仓单 价差"
        gen = KnowledgeGapQueryGenerator(injected_names=[all_dims])
        assert gen.generate(max_queries=6) == []


class TestWebSearchDynamic:
    """A1 WebSearch 动态检索方向。"""

    def test_dynamic_refresh_uses_gap_queries(self):
        extractor = WebSearchExtractor(llm_client=MagicMock(), dynamic=True)
        extractor._gap_generator = KnowledgeGapQueryGenerator(injected_names=[])  # 全缺口
        extractor._refresh_queries()
        assert len(extractor.queries) > len(extractor.base_queries)
        assert extractor.queries[0] != extractor.base_queries[0]

    def test_dynamic_disabled_keeps_base(self):
        extractor = WebSearchExtractor(llm_client=MagicMock(), dynamic=False)
        extractor._refresh_queries()
        assert extractor.queries == extractor.base_queries


class TestBulkKnowledgeExtractor:
    """P0 深读提取器编排。"""

    def test_no_llm_skips(self):
        extractor = BulkKnowledgeExtractor(llm_client=None)
        assert extractor.extract("t") == []

    def test_full_flow_mocked(self, tmp_path, monkeypatch):
        """采集→粗筛→LLM 深读全链路（mock 采集与 LLM）。"""
        from fts.factor_engine.extractors import bulk_knowledge

        # mock 采集返回已知计数（不真实请求）——注意须打 bulk_knowledge.collect_all
        # （from .bulk_collector import collect_all 已在模块 import 时绑定）
        fake_results = {
            "arxiv": MagicMock(collected=200),
            "openalex": MagicMock(collected=50),
            "eastmoney": MagicMock(collected=100),
            "global": MagicMock(collected=10),
        }
        monkeypatch.setattr(bulk_knowledge, "collect_all", lambda **k: fake_results)

        # 预置缓存记录
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        store.upsert("arxiv", [{"ref_id": "k1", "date": "2026-08-16", "title": "CTA 因子 商品期货", "abstract": "能化 基差 库存", "url": "", "language": "en"}])

        llm = MagicMock()
        llm.generate_json.return_value = [
            {
                "name": "fut_bulk_factor",
                "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
                "params": {"window": 10},
                "input_fields": ["close"],
                "lookback": 10,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {"theory": 4, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "n"},
            }
        ]
        extractor = BulkKnowledgeExtractor(
            llm_client=llm,
            store=store,
            deepread_max=60,
            max_results=3,
            page_size=10,
            embedding_enabled=False,  # 关键词降级粗筛（确定性）
            embedding_threshold=0.30,
        )
        cands = extractor.extract("t")
        assert cands, "应产出候选"
        assert cands[0]["name"] == "fut_bulk_factor"
        assert cands[0]["source"] == "l1_extractor_pipeline"
        assert len(cands[0]["economic_logic"]["narrative"]) >= 20, "C3 narrative 补全生效"

    def test_empty_cache_returns_empty(self, tmp_path, monkeypatch):
        from fts.factor_engine.extractors import bulk_knowledge

        fake_results = {k: MagicMock(collected=0) for k in ("arxiv", "openalex", "eastmoney", "global")}
        monkeypatch.setattr(bulk_knowledge, "collect_all", lambda **k: fake_results)
        store = BulkKnowledgeStore(tmp_path / "knowledge.duckdb")
        extractor = BulkKnowledgeExtractor(llm_client=MagicMock(), store=store)
        assert extractor.extract("t") == []
