"""
fts/factor_engine/extractors/alternative_sources.py — 另类知识源提取器（GAP-I103，v2.80.0）

L1 知识补给多路扩展：在既有三源（研报/论文/天软）之外，新增两路另类知识源：

    - AnnouncementNewsExtractor: 上市公司公告/舆情（东方财富公告中心 API + LLM 提取因子想法）
    - MacroEventExtractor: 宏观日历事件（东方财富数据中心宏观日历 + LLM 提取跨品种方向）

设计对齐既有三源模式（BaseExtractor）：
    - 继承 BaseExtractor，复用 `_llm_extract_factors`（market 参数驱动 stk_/fut_ 前缀与字段集）
    - 公开 HTTP API 抓取（requests + timeout=15），失败/空数据优雅降级返回空列表（不阻断 L1）
    - 提取结果统一标记 `source="l1_extractor_pipeline"`，parent_topic 携带源名
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from ..contracts import SeedCandidate
from .base import BaseExtractor

logger = logging.getLogger(__name__)

# 东方财富公告中心 API（公开，无需鉴权；与研报 API 同域）
_EASTMONEY_ANNOUNCE_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_ANNOUNCE_PARAMS = {
    "sr": "-1",
    "page_size": "30",
    "page_index": "1",
    "ann_type": "A",
    "client_source": "web",
    "f_node": "0",
    "s_node": "0",
}

# 东方财富数据中心宏观日历 API（RPT_ECONOMIC_CALENDAR）
_EASTMONEY_CALENDAR_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_CALENDAR_PARAMS = {
    "reportName": "RPT_ECONOMIC_CALENDAR",
    "columns": "ALL",
    "sortColumns": "REPORT_DATE",
    "sortTypes": "-1",
    "pageSize": "30",
    "pageNumber": "1",
}

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# 每源最多抓取的原始条目数（送入 LLM 的文本长度护栏）
_ANNOUNCE_ITEM_LIMIT = 10
_CALENDAR_ITEM_LIMIT = 15


class AnnouncementNewsExtractor(BaseExtractor):
    """公告/舆情提取器（GAP-I103）。

    从东方财富公告中心获取最新 A 股上市公司公告（含标题/日期/公告类型），
    使用 LLM 提炼因子想法（事件驱动、舆情、资金行为等维度）。
    数据获取失败/为空时优雅降级返回空列表，不阻断 L1 知识补给。
    """

    def __init__(
        self,
        name: str = "announcement_news",
        family_name: str = "announcement_news",
        paused: bool = False,
        llm_client: Optional[Any] = None,
        market: str = "stock",
        max_factors: int = 20,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name
        self.market = market
        # plans/41 A3: max_factors 配置化（管道构造时注入，默认 20）
        self.max_factors = max_factors

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[AnnouncementNewsExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[AnnouncementNewsExtractor] 开始动态提取, trace_id=%s", trace_id)
        text = self._fetch_announcements()
        if not text:
            logger.info("[AnnouncementNewsExtractor] 公告 API 无数据, 返回空 (trace_id=%s)", trace_id)
            return []

        candidates = self._llm_extract_factors(text, trace_id, max_factors=self.max_factors, market=self.market)  # plans/41 A3: 配置化配额
        for c in candidates:
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = self.market
        logger.info(
            "[AnnouncementNewsExtractor] 提取完成: %d 个候选 (market=%s, trace_id=%s)",
            len(candidates),
            self.market,
            trace_id,
        )
        return candidates

    def _fetch_announcements(self) -> str:
        """从东方财富公告中心获取最新公告文本。失败返回空字符串。"""
        try:
            r = requests.get(
                _EASTMONEY_ANNOUNCE_API,
                params=_ANNOUNCE_PARAMS,
                headers=_HTTP_HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                logger.warning("[AnnouncementNewsExtractor] 公告 API 非 200: %s", r.status_code)
                return ""
            data = r.json()
            items = (data.get("data") or {}).get("list") or []
            parts: list[str] = []
            for it in items[:_ANNOUNCE_ITEM_LIMIT]:
                title = it.get("title", "")
                if not title:
                    continue
                date = it.get("notice_date", "") or it.get("noticeDate", "")
                cols = it.get("columns") or []
                parts.append(f"标题: {title}\n日期: {date}\n公告类型: {cols}\n")
            return "\n---\n".join(parts) if parts else ""
        except Exception as e:  # noqa: BLE001 - 外部 API 异常全面降级
            logger.warning("[AnnouncementNewsExtractor] 公告 API 不可用: %s", e)
            return ""


class MacroEventExtractor(BaseExtractor):
    """宏观事件提取器（GAP-I103）。

    从东方财富数据中心获取宏观日历事件（CPI/PMI/利率决议/非农等），
    使用 LLM 提炼跨品种/跨板块的宏观方向因子想法。
    数据获取失败/为空时优雅降级返回空列表。
    """

    def __init__(
        self,
        name: str = "macro_events",
        family_name: str = "macro_events",
        paused: bool = False,
        llm_client: Optional[Any] = None,
        market: str = "futures",
        max_factors: int = 20,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name
        self.market = market
        # plans/41 A3: max_factors 配置化（管道构造时注入，默认 20）
        self.max_factors = max_factors

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[MacroEventExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[MacroEventExtractor] 开始动态提取, trace_id=%s", trace_id)
        text = self._fetch_events()
        if not text:
            logger.info("[MacroEventExtractor] 宏观日历无数据, 返回空 (trace_id=%s)", trace_id)
            return []

        candidates = self._llm_extract_factors(text, trace_id, max_factors=20, market=self.market)  # plans/41 A3: max_factors 5→20
        for c in candidates:
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = self.market
        logger.info(
            "[MacroEventExtractor] 提取完成: %d 个候选 (market=%s, trace_id=%s)",
            len(candidates),
            self.market,
            trace_id,
        )
        return candidates

    def _fetch_events(self) -> str:
        """从东方财富数据中心获取宏观日历事件文本。失败返回空字符串。"""
        try:
            r = requests.get(
                _EASTMONEY_CALENDAR_API,
                params=_CALENDAR_PARAMS,
                headers=_HTTP_HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                logger.warning("[MacroEventExtractor] 宏观日历 API 非 200: %s", r.status_code)
                return ""
            data = r.json()
            items = (data.get("result") or {}).get("data") or []
            parts: list[str] = []
            for it in items[:_CALENDAR_ITEM_LIMIT]:
                title = it.get("TITLE") or it.get("title", "")
                if not title:
                    continue
                date = it.get("REPORT_DATE") or it.get("report_date", "")
                country = it.get("COUNTRY") or it.get("country", "")
                importance = it.get("IMPORTANCE") or it.get("importance", "")
                parts.append(
                    f"事件: {title}\n日期: {date}\n地区: {country}\n重要性: {importance}\n"
                )
            return "\n---\n".join(parts) if parts else ""
        except Exception as e:  # noqa: BLE001 - 外部 API 异常全面降级
            logger.warning("[MacroEventExtractor] 宏观日历 API 不可用: %s", e)
            return ""


__all__ = [
    "AnnouncementNewsExtractor",
    "MacroEventExtractor",
]
