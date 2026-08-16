"""
fts/factor_engine/extractors/bulk_knowledge.py — plans/44 P0 深读提取层 + 采集/粗筛编排

三层管线之「深读层」：把批量采集（零 token）→ embedding 粗筛（零 token）→
命中子集分块交 LLM 提取因子（受 token 预算约束，深读子集上限 l1_knowledge_deepread_max）。

BulkKnowledgeExtractor 注册进 FuturesExtractorPipeline（l1_bulk_enabled 开关），
与既有研报/论文/宏观/WebSearch 源并行，实现"每天全球 ≥300 篇 → 有效子集深读"。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import BaseExtractor
from .bulk_collector import BulkKnowledgeStore, collect_all
from .knowledge_filter import KnowledgeRelevanceFilter, TextEmbedder

logger = logging.getLogger(__name__)


class BulkKnowledgeExtractor(BaseExtractor):
    """全球多源批量知识深读提取器（plans/44 P0）。

    流程: collect_all（arXiv/OpenAlex/东财/全球报告，计数审计）→
          recent 读取近 3 日缓存 → embedding 粗筛 → 命中子集分块 LLM 深读提取。
    各环节降级不阻断：单源采集失败仅记录；embedding 缺失走关键词粗筛。
    """

    def __init__(
        self,
        name: str = "bulk_knowledge",
        paused: bool = False,
        llm_client: Optional[Any] = None,
        market: str = "futures",
        max_factors: int = 20,
        store: Optional[BulkKnowledgeStore] = None,
        deepread_max: int = 60,
        max_results: int = 50,
        page_size: int = 100,
        embedding_enabled: bool = True,
        embedding_threshold: float = 0.30,
        openalex_languages: Optional[list[str]] = None,
        non_en_reports_enabled: bool = True,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.market = market
        self.max_factors = max_factors
        self._store = store or BulkKnowledgeStore()
        self.deepread_max = deepread_max
        self.max_results = max_results
        self.page_size = page_size
        self.embedding_enabled = embedding_enabled
        self.embedding_threshold = embedding_threshold
        self.openalex_languages = openalex_languages
        self.non_en_reports_enabled = non_en_reports_enabled

    # 每块深读文本预算（_llm_extract_factors 输入截断 8000，留余量）
    _CHUNK_TEXT_BUDGET = 7000

    def extract(self, trace_id: str) -> list[dict[str, Any]]:
        if self.paused:
            logger.info("[%s] 已暂停, 跳过", self.name)
            return []
        if self.llm_client is None:
            logger.info("[%s] 未配置 llm_client, 跳过", self.name)
            return []

        # 1. 批量采集（计数契约审计：≥300 篇/天，全球多语种）
        results = collect_all(
            store=self._store,
            max_results=self.max_results,
            page_size=self.page_size,
            openalex_languages=self.openalex_languages,
            non_en_reports_enabled=self.non_en_reports_enabled,
        )
        total = sum(r.collected for r in results.values())
        counts = {src: r.collected for src, r in results.items()}
        logger.info(
            "[%s] 批量采集完成: total_collected=%d, by_source=%s, trace_id=%s",
            self.name,
            total,
            counts,
            trace_id,
        )

        # 2. 读取近 3 日缓存 → 粗筛
        records = self._store.recent(since_days=3, limit=2000)
        if not records:
            logger.info("[%s] 缓存无记录, 跳过深读, trace_id=%s", self.name, trace_id)
            return []
        flt = KnowledgeRelevanceFilter(
            threshold=self.embedding_threshold,
            embedder=TextEmbedder(enabled=self.embedding_enabled),
        )
        hits = flt.filter(records)[: self.deepread_max]
        logger.info(
            "[%s] 粗筛命中 %d/%d 篇 (deepread_max=%d), trace_id=%s",
            self.name,
            len(hits),
            len(records),
            self.deepread_max,
            trace_id,
        )
        if not hits:
            return []

        # 3. 分块深读提取（每块文本 ≤ 预算，调用 _llm_extract_factors）
        candidates: list[dict[str, Any]] = []
        chunk: list[str] = []
        chunk_len = 0
        for rec in hits:
            text = f"[{rec.get('source', '?')}] {rec.get('title', '')}\n{(rec.get('abstract') or '')[:600]}"
            if chunk and chunk_len + len(text) > self._CHUNK_TEXT_BUDGET:
                candidates.extend(self._extract_chunk(chunk, trace_id))
                chunk = []
                chunk_len = 0
            chunk.append(text)
            chunk_len += len(text)
        if chunk:
            candidates.extend(self._extract_chunk(chunk, trace_id))

        # 去重（LLM 多块可能产出同名）
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for c in candidates:
            name = c.get("name", "")
            if name in seen:
                continue
            seen.add(name)
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{name}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = self.market
            unique.append(c)
        logger.info(
            "[%s] 深读提取完成: hits=%d candidates=%d, trace_id=%s",
            self.name,
            len(hits),
            len(unique),
            trace_id,
        )
        return unique

    def _extract_chunk(self, chunk_texts: list[str], trace_id: str) -> list[dict[str, Any]]:
        merged = "\n---\n".join(chunk_texts)
        try:
            return self._llm_extract_factors(merged, trace_id, max_factors=self.max_factors, market=self.market)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 深读块提取异常: %s", self.name, e)
            return []
