"""
fts/factor_engine/extractors/web_search.py — WebSearch 动态因子源（plans/41 B 层）

通过必应 HTML 搜索检索量化平台/研报/社区因子源文本，交给 LLM 提取因子候选。
搜索结果随市场/时间变化，实现"每轮动态换一批新知识"的动态因子源。

用法:
    extractor = WebSearchExtractor(llm_client=llm_client, queries=[...])
    candidates = extractor.extract(trace_id="l1_xxx")
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..contracts import SeedCandidate
from .base import BaseExtractor

logger = logging.getLogger(__name__)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
_WS_RE = re.compile(r"\s+")

# 知识缺口维度桶（plans/44 A1）：已注入因子未覆盖的维度 → 生成检索 query
_GAP_DIMENSIONS = (
    ("库存", "商品期货 库存周期 因子"),
    ("基差", "商品期货 基差 因子"),
    ("季节性", "商品期货 季节性 因子"),
    ("展期", "商品期货 展期收益 期限结构 因子"),
    ("波动率聚集", "商品期货 波动率聚集 因子"),
    ("情绪", "期货 市场情绪 持仓 因子"),
    ("开工率", "化工 开工率 产能利用率 因子"),
    ("仓单", "期货 仓单 注册仓单 因子"),
    ("价差", "能化 跨品种价差 裂解价差 因子"),
)


class KnowledgeGapQueryGenerator:
    """plans/44 A1: 知识缺口驱动检索方向生成器。

    统计已注入因子（factor_pool 名 + parent_topic）的维度覆盖，
    对未覆盖维度生成检索 query，实现"每轮动态换一批新知识"。
    """

    def __init__(self, injected_names: Optional[list[str]] = None):
        # 注入的来源可替换（如 factor_pool 读取器）；None 时惰性读文件
        self._injected_names = injected_names

    def _load_injected(self) -> list[str]:
        if self._injected_names is not None:
            return self._injected_names
        try:
            from pathlib import Path

            pool_path = Path("memory/knowledge/factors/factor_pool_energy.json")
            if not pool_path.exists():
                pool_path = Path("memory/knowledge/factors/factor_pool.json")
            if not pool_path.exists():
                return []
            import json

            data = json.loads(pool_path.read_text(encoding="utf-8"))
            return [str(e.get("name", "")) + " " + str(e.get("parent_topic", "")) for e in data.get("factors", [])]
        except Exception as e:  # noqa: BLE001
            logger.warning("[gap_query] 读取已注入因子失败: %s", e)
            return []

    def generate(self, max_queries: int = 6) -> list[str]:
        """返回未覆盖维度的检索 query（按缺口优先级）。"""
        injected = " ".join(self._load_injected()).lower()
        gap_queries: list[str] = []
        for keyword, query in _GAP_DIMENSIONS:
            if len(gap_queries) >= max_queries:
                break
            if keyword not in injected:
                gap_queries.append(query)
        logger.info("[gap_query] 知识缺口 query: %d 个 (覆盖维度=%d/总注入=%d)", len(gap_queries), len(_GAP_DIMENSIONS), len(injected.split()))
        return gap_queries


class WebSearchExtractor(BaseExtractor):
    """Web 搜索动态因子源：搜索 → 去标签 → LLM 提取因子。

    Args:
        name: 提取器名称（默认 "web_search"）
        queries: 检索关键词列表（每轮逐条搜索，文本合并后交 LLM）
        timeout: HTTP 超时（秒）
        paused: 是否暂停
        llm_client: LLM 客户端（用于 LLM 提取）
        market: 市场类型（futures/energy，用于 prompt 语境）
        max_results_chars: 单条搜索结果去标签文本最大保留字符数
    """

    def __init__(
        self,
        name: str = "web_search",
        queries: Optional[list[str]] = None,
        timeout: int = 10,
        paused: bool = False,
        llm_client: Optional[Any] = None,
        market: str = "futures",
        max_results_chars: int = 3000,
        max_factors: int = 20,
        dynamic: bool = True,
    ) -> None:
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.base_queries = queries or [
            "量化因子 商品期货 趋势 期限结构 动量",
            "CTA 因子 化工期货 库存周期 基差",
            "商品期货 因子挖掘 波动率 季节性",
        ]
        self.queries = list(self.base_queries)
        self.timeout = timeout
        self.market = market
        self.max_results_chars = max_results_chars
        # plans/41 A3: max_factors 配置化（管道构造时注入，默认 20）
        self.max_factors = max_factors
        # plans/44 A1: 动态检索（知识缺口驱动，每轮换新 query）
        self.dynamic = dynamic
        self._gap_generator: Optional[KnowledgeGapQueryGenerator] = None

    def _refresh_queries(self) -> None:
        """plans/44 A1: 动态检索方向——知识缺口 query 置前 + 基础 query 兜底。"""
        if not self.dynamic:
            return
        if self._gap_generator is None:
            self._gap_generator = KnowledgeGapQueryGenerator()
        gap = self._gap_generator.generate(max_queries=6)
        if gap:
            self.queries = gap + self.base_queries
            logger.info(
                "[%s] 动态检索方向: gap_queries=%d total=%d",
                self.name,
                len(gap),
                len(self.queries),
            )

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """搜索 → 去标签 → LLM 提取候选因子。"""
        if self.paused:
            logger.info("[WebSearchExtractor] %s 已暂停, 跳过提取", self.name)
            return []
        if self.llm_client is None:
            logger.info("[WebSearchExtractor] 未配置 llm_client, 跳过提取")
            return []

        # plans/44 A1: 每轮动态换新检索方向（知识缺口驱动）
        self._refresh_queries()

        logger.info("[WebSearchExtractor] 开始搜索, trace_id=%s, queries=%d", trace_id, len(self.queries))
        merged_text = self._search_all()
        if not merged_text:
            logger.warning("[WebSearchExtractor] 搜索无结果, 返回空, trace_id=%s", trace_id)
            return []

        candidates = self._llm_extract_factors(
            merged_text,
            trace_id,
            max_factors=self.max_factors,  # plans/41 A3: 配置化配额
            market=self.market,
        )
        for c in candidates:
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = self.market
        logger.info(
            "[WebSearchExtractor] 搜索提取完成: %d 个候选, trace_id=%s",
            len(candidates),
            trace_id,
        )
        return candidates

    def _search_all(self) -> str:
        """逐条搜索并合并去标签文本（单源失败不阻断）。"""
        import requests

        texts: list[str] = []
        for q in self.queries:
            try:
                resp = requests.get(
                    "https://www.bing.com/search",
                    params={"q": q, "mkt": "zh-CN"},
                    headers=_UA,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                text = _SCRIPT_STYLE_RE.sub("", resp.text)
                text = _HTML_TAG_RE.sub(" ", text)
                text = _WS_RE.sub(" ", text)
                if text:
                    texts.append(text[: self.max_results_chars])
            except Exception as e:  # noqa: BLE001
                logger.warning("[WebSearchExtractor] 搜索失败 query=%s: %s", q, e)
        merged = "\n".join(texts)
        logger.info("[WebSearchExtractor] 合并文本长度=%d", len(merged))
        return merged
