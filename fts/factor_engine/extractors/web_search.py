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
    ) -> None:
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.queries = queries or [
            "量化因子 商品期货 趋势 期限结构 动量",
            "CTA 因子 化工期货 库存周期 基差",
            "商品期货 因子挖掘 波动率 季节性",
        ]
        self.timeout = timeout
        self.market = market
        self.max_results_chars = max_results_chars
        # plans/41 A3: max_factors 配置化（管道构造时注入，默认 20）
        self.max_factors = max_factors

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """搜索 → 去标签 → LLM 提取候选因子。"""
        if self.paused:
            logger.info("[WebSearchExtractor] %s 已暂停, 跳过提取", self.name)
            return []
        if self.llm_client is None:
            logger.info("[WebSearchExtractor] 未配置 llm_client, 跳过提取")
            return []

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
