"""
fts/factor_engine/extractors/stock_pipeline.py — 股票三源提取器管道

动态提取器:
  1. jq_factors: 聚宽因子库（从 YAML 文件读取，本批提取后暂停）
  2. broker_reports_stock: 券商研报选股因子（从研报 API 实时获取，LLM 提取）
  3. academic_papers_stock: 学术论文股票因子（从 arXiv API 实时获取，LLM 提取）

用法:
    pipeline = StockExtractorPipeline(llm_client=llm_client)
    candidates = pipeline.extract(trace_id="l1_xxx")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

from ..contracts import SeedCandidate
from .base import BaseExtractor, BaseExtractorPipeline

logger = logging.getLogger(__name__)

# ─── 种子文件路径 ──────────────────────────────────────────

SEEDS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "seeds" / "stock"

FACTOR_FILE_MAP: dict[str, str] = {
    "jq_factors": "jq_factors.yaml",
    "broker_reports_stock": "",  # 预留 YAML 路径（已改为动态提取）
    "academic_papers_stock": "",  # 预留 YAML 路径（已改为动态提取）
}


# ─── 源 1: YamlSeedExtractor（聚宽，静态） ────────────────


class YamlSeedExtractor(BaseExtractor):
    """从 YAML 种子文件读取因子并转换为候选的提取器。"""

    def __init__(
        self,
        name: str,
        yaml_file: str | Path | None = None,
        builtin_factors: list[dict[str, Any]] | None = None,
        family_name: str = "",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.yaml_file = Path(yaml_file) if yaml_file else None
        self.builtin_factors = builtin_factors or []
        self.family_name = family_name or name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            return []

        # 优先从 YAML 文件加载
        if self.yaml_file and self.yaml_file.exists():
            try:
                with open(self.yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                factors = data.get("factors", [])
                if factors:
                    candidates = [self._convert_factor(f, trace_id) for f in factors]
                    logger.info(
                        "[YamlSeedExtractor] %s 从文件提取: %d 个候选 (file=%s)",
                        self.name,
                        len(candidates),
                        self.yaml_file.name,
                    )
                    return candidates
            except Exception as e:
                logger.warning(
                    "[YamlSeedExtractor] %s 文件加载失败: %s, 尝试内置因子",
                    self.name,
                    e,
                )

        # 回退到内置因子
        if self.builtin_factors:
            candidates = [self._convert_factor(f, trace_id) for f in self.builtin_factors]
            logger.info(
                "[YamlSeedExtractor] %s 从内置因子提取: %d 个候选",
                self.name,
                len(candidates),
            )
            return candidates

        logger.info("[YamlSeedExtractor] %s 无可提取因子", self.name)
        return []

    def _convert_factor(
        self,
        factor: dict[str, Any],
        trace_id: str,
    ) -> SeedCandidate:
        return BaseExtractorPipeline._yaml_factor_to_candidate(
            factor=factor,
            source=self.name,
            market="stock",
            trace_id=trace_id,
            family_name=self.family_name,
        )


# ─── 源 2: StockResearchReportExtractor（动态，LLM 提取） ───

_EASTMONEY_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_EASTMONEY_REPORT_PARAMS = {
    "cb": "",
    "pageSize": 5,
    "industryCode": "*",
    "pageNo": 1,
    "reportType": 1,
    "columnsType": 1,
    "source": "WEB",
    "client": "WEB",
}
_EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/",
}


class StockResearchReportExtractor(BaseExtractor):
    """从券商研报实时提取股票因子候选。

    动态获取最新券商研报，使用 LLM 从研报正文中提取股票因子想法。
    """

    def __init__(
        self,
        name: str = "broker_reports_stock",
        family_name: str = "broker_stock",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[StockResearchReportExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[StockResearchReportExtractor] 开始动态提取, trace_id=%s", trace_id)

        # Step 1: 尝试从东方财富 API 获取研报
        reports_text = self._fetch_reports()

        # Step 2: 使用 LLM 从研报中提取因子
        if reports_text:
            logger.info(
                "[StockResearchReportExtractor] 成功获取研报, 长度=%d, 开始 LLM 提取",
                len(reports_text),
            )
            candidates = self._llm_extract_factors(
                reports_text,
                trace_id,
                max_factors=5,
                market="stock",
            )
            if candidates:
                for c in candidates:
                    c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
                    c["source"] = "l1_extractor_pipeline"
                    c["market"] = "stock"
                return candidates

        # Step 3: 回退
        logger.info(
            "[StockResearchReportExtractor] 研报API不可用, 使用 LLM 生成因子, trace_id=%s",
            trace_id,
        )
        fallback_text = (
            "基于最新的股票量化因子研究趋势，包括但不限于："
            "截面动量、估值因子、成长因子、质量因子、低波因子、"
            "红利因子、情绪因子、资金流因子、分析师预期修正等方向。"
            "请提取 3-5 个当前研究前沿的因子想法。"
        )
        candidates = self._llm_extract_factors(
            fallback_text,
            trace_id,
            max_factors=5,
            market="stock",
        )
        for c in candidates:
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = "stock"
        logger.info(
            "[StockResearchReportExtractor] 回退提取完成: %d 个候选, trace_id=%s",
            len(candidates),
            trace_id,
        )
        return candidates

    def _fetch_reports(self) -> str:
        """从东方财富 API 获取最新研报。"""
        try:
            text_parts = []
            for report_type in [1, 2]:
                params = dict(_EASTMONEY_REPORT_PARAMS)
                params["reportType"] = report_type
                try:
                    r = requests.get(
                        _EASTMONEY_REPORT_API,
                        params=params,
                        headers=_EASTMONEY_HEADERS,
                        timeout=15,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        reports = data.get("data", [])
                        for rep in reports[:3]:
                            title = rep.get("title", "")
                            industry = rep.get("industryName", "")
                            stock = rep.get("stockName", "")
                            summary = rep.get("summary", "")
                            if title:
                                text_parts.append(f"标题: {title}\n板块: {industry}\n标的: {stock}\n摘要: {summary}\n")
                except Exception:
                    continue

            return "\n---\n".join(text_parts) if text_parts else ""
        except Exception as e:
            logger.warning("[StockResearchReportExtractor] 研报 API 不可用: %s", e)
            return ""


# ─── 源 3: StockAcademicPaperExtractor（动态，arXiv + LLM） ─

_ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_CATEGORIES = ["q-fin.ST", "q-fin.GN", "q-fin.PM", "q-fin.RM"]
_ARXIV_HEADERS = {
    "User-Agent": "FTS/1.0 (factor extraction; mailto:fts@example.com)",
}


class StockAcademicPaperExtractor(BaseExtractor):
    """从学术论文实时提取股票因子候选。"""

    def __init__(
        self,
        name: str = "academic_papers_stock",
        family_name: str = "academic_stock",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[StockAcademicPaperExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[StockAcademicPaperExtractor] 开始动态提取, trace_id=%s", trace_id)

        papers_text = self._fetch_papers()
        if papers_text:
            logger.info(
                "[StockAcademicPaperExtractor] 成功获取论文, 长度=%d, 开始 LLM 提取",
                len(papers_text),
            )
            candidates = self._llm_extract_factors(
                papers_text,
                trace_id,
                max_factors=5,
                market="stock",
            )
            for c in candidates:
                c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
                c["source"] = "l1_extractor_pipeline"
                c["market"] = "stock"
            logger.info(
                "[StockAcademicPaperExtractor] 提取完成: %d 个候选, trace_id=%s",
                len(candidates),
                trace_id,
            )
            return candidates

        logger.info("[StockAcademicPaperExtractor] 论文获取为空, trace_id=%s", trace_id)
        return []

    def _fetch_papers(self) -> str:
        """从 arXiv API 获取最新金融量化论文。"""
        try:
            text_parts = []
            for cat in _ARXIV_CATEGORIES:
                params = {
                    "search_query": f"cat:{cat}",
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "max_results": 3,
                }
                try:
                    r = requests.get(
                        _ARXIV_API,
                        params=params,
                        headers=_ARXIV_HEADERS,
                        timeout=30,
                    )
                    if r.status_code == 200:
                        import xml.etree.ElementTree as ET

                        root = ET.fromstring(r.content)
                        ns = {"atom": "http://www.w3.org/2005/Atom"}
                        entries = root.findall("atom:entry", ns)
                        for entry in entries:
                            title_el = entry.find("atom:title", ns)
                            summary_el = entry.find("atom:summary", ns)
                            title = (title_el.text or "").strip() if title_el is not None else ""
                            summary = (summary_el.text or "").strip()[:500] if summary_el is not None else ""
                            if title:
                                text_parts.append(f"类别: {cat}\n标题: {title}\n摘要: {summary}\n")
                except Exception:
                    continue
            return "\n---\n".join(text_parts) if text_parts else ""
        except Exception as e:
            logger.warning("[StockAcademicPaperExtractor] arXiv API 不可用: %s", e)
            return ""


# ─── 管道 ──────────────────────────────────────────────────


class StockExtractorPipeline(BaseExtractorPipeline):
    """股票三源提取器管道。

    源 1 (jq_factors): 聚宽因子库，首次提取后自动暂停
    源 2 (broker_reports_stock): 券商研报选股因子（动态，LLM 提取）
    源 3 (academic_papers_stock): 学术论文股票因子（动态，arXiv + LLM 提取）
    """

    def __init__(
        self,
        state_path: str | Path = "memory/extractors/state.json",
        pause_jq_after_first: bool = True,
        llm_client: Optional[Any] = None,
        announcement_enabled: bool = True,
        macro_enabled: bool = True,
    ):
        self._pause_jq_after_first = pause_jq_after_first
        self._first_extract = True

        jq_yaml = SEEDS_DIR / FACTOR_FILE_MAP["jq_factors"]

        from .alternative_sources import AnnouncementNewsExtractor, MacroEventExtractor

        extractors = [
            YamlSeedExtractor(
                name="jq_factors",
                yaml_file=jq_yaml,
                family_name="jq_factors",
                paused=False,
            ),
            StockResearchReportExtractor(
                name="broker_reports_stock",
                family_name="broker_stock",
                paused=False,
                llm_client=llm_client,
            ),
            StockAcademicPaperExtractor(
                name="academic_papers_stock",
                family_name="academic_stock",
                paused=False,
                llm_client=llm_client,
            ),
        ]
        # GAP-I103 (v2.80.0): 另类知识源——公告/舆情 + 宏观事件（股票侧）
        if announcement_enabled:
            extractors.append(
                AnnouncementNewsExtractor(
                    name="announcement_news_stock",
                    family_name="announcement_news",
                    paused=False,
                    llm_client=llm_client,
                    market="stock",
                )
            )
        if macro_enabled:
            extractors.append(
                MacroEventExtractor(
                    name="macro_events_stock",
                    family_name="macro_events",
                    paused=False,
                    llm_client=llm_client,
                    market="stock",
                )
            )

        super().__init__(
            extractors=extractors,
            market="stock",
            state_path=state_path,
        )

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        candidates = super().extract(trace_id)

        if self._first_extract and self._pause_jq_after_first:
            jq = self.extractors.get("jq_factors")
            if jq and not jq.paused:
                self.pause_source("jq_factors")
                logger.info(
                    "[StockExtractorPipeline] 聚宽源首次提取完成，已自动暂停。"
                    " 后续可通过 resume_source('jq_factors') 恢复或指定提取。"
                )
            self._first_extract = False

        return candidates


# ─── 便捷工厂函数 ──────────────────────────────────────────


def create_stock_extractor_pipeline(
    state_path: str | Path = "memory/extractors/state.json",
    pause_jq_after_first: bool = True,
    llm_client: Optional[Any] = None,
    announcement_enabled: bool = True,
    macro_enabled: bool = True,
) -> StockExtractorPipeline:
    return StockExtractorPipeline(
        state_path=state_path,
        pause_jq_after_first=pause_jq_after_first,
        llm_client=llm_client,
        announcement_enabled=announcement_enabled,
        macro_enabled=macro_enabled,
    )
