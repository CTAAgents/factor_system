"""
tests/factor_engine/test_knowledge_filter.py — plans/44 P0 粗筛层测试

覆盖:
    - TextEmbedder 关键词降级态（模型不可用）
    - KnowledgeRelevanceFilter 关键词降级粗筛 / embedding 粗筛（mock embedder）
    - dedup_semantic 语义去重（embedding 不可用回退名称精确匹配）
    - C3 _ensure_narrative narrative 补全

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.base import _ensure_narrative  # noqa: E402
from fts.factor_engine.extractors.knowledge_filter import (  # noqa: E402
    KnowledgeRelevanceFilter,
    TextEmbedder,
    dedup_semantic,
)


class TestTextEmbedder:
    """embedding 降级态。"""

    def test_model_unavailable_degraded(self):
        """模型加载失败 → degraded=True 且 embed 返回 None。"""
        embedder = TextEmbedder(enabled=True)
        embedder._load_error = "模型不可用"  # 模拟加载失败
        assert embedder.degraded is True
        assert embedder.embed(["x"]) is None


class TestKnowledgeRelevanceFilter:
    """相关性粗筛。"""

    def test_keyword_fallback_hits(self):
        """关键词降级：命中量化/期货关键词 → 命中子集带 filter_mode=keyword。"""
        flt = KnowledgeRelevanceFilter(threshold=0.30, embedder=TextEmbedder(enabled=False))
        records = [
            {"title": "CTA 因子 商品期货 基差 库存", "abstract": "能化 原油 基差"},
            {"title": "某无关论文", "abstract": "生物医药进展"},
        ]
        hits = flt.filter(records)
        assert len(hits) == 1
        assert hits[0]["filter_mode"] == "keyword"
        assert hits[0]["relevance"] > 0

    def test_embedding_path_hits(self, monkeypatch):
        """embedding 路径：mock embedder 返回可控向量。"""
        embedder = MagicMock()
        embedder.embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])  # 记录0与query相似、记录1正交
        flt = KnowledgeRelevanceFilter(threshold=0.0, embedder=embedder)
        # query 向量 = [1, 0]
        flt._query_vec = np.array([1.0, 0.0])
        records = [{"title": "a", "abstract": "1"}, {"title": "b", "abstract": "2"}]
        hits = flt.filter(records)
        # 相似度: rec0 = 1.0, rec1 = 0.0 → 阈值 0.0 时两者都命中
        assert len(hits) == 2
        assert hits[0]["filter_mode"] == "embedding"

    def test_empty_records(self):
        flt = KnowledgeRelevanceFilter(threshold=0.30, embedder=TextEmbedder(enabled=False))
        assert flt.filter([]) == []


class TestDedupSemantic:
    """语义去重。"""

    def test_embedding_unavailable_fallback_exact(self):
        """embedding 不可用 → 回退名称精确匹配。"""
        allow = dedup_semantic(["FUT_A", "FUT_B"], ["FUT_A"], embedder=TextEmbedder(enabled=False))
        assert allow == [False, True]  # FUT_A 重复，FUT_B 放行

    def test_empty_existing_allows(self):
        assert dedup_semantic(["A", "B"], [], embedder=TextEmbedder(enabled=False)) == [True, True]

    def test_empty_candidates(self):
        assert dedup_semantic([], ["A"], embedder=TextEmbedder(enabled=False)) == []


class TestEnsureNarrative:
    """C3 narrative 补全。"""

    def test_short_narrative_completed(self):
        econ = {"theory": 4, "behavioral": 3, "microstructure": 2, "institutional": 1, "narrative": "短"}
        out = _ensure_narrative("fut_gap_factor", econ)
        assert len(out["narrative"]) >= 20, "补全后应达最小长度"
        assert "fut_gap_factor" in out["narrative"]

    def test_long_narrative_unchanged(self):
        econ = {"narrative": "这是一个足够长的 narrative 论证内容，超过二十个字。"}
        out = _ensure_narrative("x", econ)
        assert out["narrative"] == econ["narrative"]
