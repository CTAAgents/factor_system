"""
fts/factor_engine/extractors/futures_pipeline.py — 期货三源提取器管道

动态提取器（方案 1）:
  1. tinysoft: 天软因子算法文档（从 YAML 文件读取，本批提取后暂停）
  2. broker_reports: 券商研报（从东方财富 API 实时获取研报，LLM 提取因子）
  3. academic_papers: 学术论文（从 arXiv API 实时获取论文，LLM 提取因子）

用法:
    pipeline = FuturesExtractorPipeline(llm_client=llm_client)
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

SEEDS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "seeds" / "futures"

FACTOR_FILE_MAP = {
    "tinysoft": "tinysoft.yaml",
    "broker_reports": "broker_reports.yaml",
    "academic_papers": "academic_papers.yaml",
}


# ─── 源 1: YamlSeedExtractor（天软，静态） ─────────────────


class YamlSeedExtractor(BaseExtractor):
    """从 YAML 种子文件读取因子并转换为候选的提取器。

    仅用于 tinysoft 源（静态因子库），broker_reports 和 academic_papers
    已替换为动态提取器。
    """

    def __init__(
        self,
        name: str,
        yaml_file: str | Path,
        family_name: str = "",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.yaml_file = Path(yaml_file)
        self.family_name = family_name or name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[YamlSeedExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        if not self.yaml_file.exists():
            logger.warning(
                "[YamlSeedExtractor] %s 文件不存在: %s",
                self.name,
                self.yaml_file,
            )
            return []

        try:
            with open(self.yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.error(
                "[YamlSeedExtractor] %s 加载失败: %s",
                self.name,
                e,
            )
            return []

        factors = data.get("factors", [])
        if not factors:
            logger.info("[YamlSeedExtractor] %s 空因子列表", self.name)
            return []

        candidates: list[SeedCandidate] = []
        for factor in factors:
            candidate = self._convert_factor(factor, trace_id)
            candidates.append(candidate)

        logger.info(
            "[YamlSeedExtractor] %s 提取完成: %d 个候选 (file=%s)",
            self.name,
            len(candidates),
            self.yaml_file.name,
        )
        return candidates

    def _convert_factor(
        self,
        factor: dict[str, Any],
        trace_id: str,
    ) -> SeedCandidate:
        return BaseExtractorPipeline._yaml_factor_to_candidate(
            factor=factor,
            source=self.name,
            market="futures",
            trace_id=trace_id,
            family_name=self.family_name,
        )


# ─── 源 2: ResearchReportExtractor（动态，LLM 提取） ────────

# 东方财富研报 API 端点（尝试多个模式）
_EASTMONEY_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_EASTMONEY_REPORT_PARAMS = {
    "cb": "",
    "pageSize": 5,
    "industryCode": "*",
    "pageNo": 1,
    "reportType": 1,  # 个股研报
    "columnsType": 1,
    "source": "WEB",
    "client": "WEB",
}
_EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/",
}


class ResearchReportExtractor(BaseExtractor):
    """从券商研报实时提取因子候选。

    动态获取最新券商研报，使用 LLM 从研报正文中提取因子想法。
    支持回退: 如果 API 不可用，使用 LLM 直接生成因子候选。
    """

    def __init__(
        self,
        name: str = "broker_reports",
        family_name: str = "broker_cta",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        """
        Args:
            name: 提取器名称
            family_name: 因子家族名称
            paused: 是否暂停
            llm_client: LLM 客户端
        """
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[ResearchReportExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[ResearchReportExtractor] 开始动态提取, trace_id=%s", trace_id)

        # Step 1: 尝试从东方财富 API 获取研报
        reports_text = self._fetch_reports()

        # Step 2: 使用 LLM 从研报中提取因子
        if reports_text:
            logger.info(
                "[ResearchReportExtractor] 成功获取研报, 长度=%d, 开始 LLM 提取",
                len(reports_text),
            )
            candidates = self._llm_extract_factors(reports_text, trace_id, max_factors=5)
            if candidates:
                # 标记来源
                for c in candidates:
                    c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
                    c["source"] = "l1_extractor_pipeline"
                return candidates

        # Step 3: 回退 — 使用 LLM 基于市场知识直接生成因子
        logger.info(
            "[ResearchReportExtractor] 研报API不可用, 使用 LLM 生成因子, trace_id=%s",
            trace_id,
        )
        fallback_text = (
            "基于最新的期货 CTA 量化因子研究趋势，包括但不限于："
            "趋势跟踪、截面动量、期限结构套利、波动率预测、量价背离、"
            "持仓量分析、季节性模式、跳跃风险、偏度交易、尾部风险对冲等方向。"
            "请提取 3-5 个当前研究前沿的因子想法。"
        )
        candidates = self._llm_extract_factors(fallback_text, trace_id, max_factors=5)
        for c in candidates:
            c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
            c["source"] = "l1_extractor_pipeline"
            c["market"] = "futures"
        logger.info(
            "[ResearchReportExtractor] 回退提取完成: %d 个候选, trace_id=%s",
            len(candidates),
            trace_id,
        )
        return candidates

    def _fetch_reports(self) -> str:
        """从东方财富 API 获取最新研报。"""
        try:
            # 尝试多个研报类型
            text_parts = []

            for report_type in [1, 2]:  # 1=个股研报, 2=行业研报
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
                except Exception as e:
                    logger.debug("[ResearchReportExtractor] 类型 %d 请求失败: %s", report_type, e)
                    continue

            if text_parts:
                return "\n---\n".join(text_parts)

            # 尝试备用 API: 东方财富研报搜索
            backup_url = "https://so.eastmoney.com/News/s"
            backup_params = {
                "keyword": "CTA 因子 量化 期货",
                "pageindex": 1,
                "pagesize": 5,
            }
            r = requests.get(backup_url, params=backup_params, headers=_EASTMONEY_HEADERS, timeout=15)
            if r.status_code == 200:
                text_parts.append(f"备用研报搜索返回: {r.text[:2000]}")

            return "\n".join(text_parts) if text_parts else ""

        except Exception as e:
            logger.warning(
                "[ResearchReportExtractor] 研报 API 全部不可用: %s",
                e,
            )
            return ""


# ─── 源 3: AcademicPaperExtractor（动态，arXiv + LLM） ─────

_ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_CATEGORIES = ["q-fin.ST", "q-fin.GN", "q-fin.PM", "q-fin.RM"]
_ARXIV_HEADERS = {
    "User-Agent": "FTS/1.0 (factor extraction; mailto:fts@example.com)",
}


class AcademicPaperExtractor(BaseExtractor):
    """从学术论文实时提取因子候选。

    从 arXiv 获取最新金融/量化论文，使用 LLM 从论文摘要中提取因子想法。
    """

    def __init__(
        self,
        name: str = "academic_papers",
        family_name: str = "academic_papers",
        paused: bool = False,
        llm_client: Optional[Any] = None,
    ):
        """
        Args:
            name: 提取器名称
            family_name: 因子家族名称
            paused: 是否暂停
            llm_client: LLM 客户端
        """
        super().__init__(name=name, paused=paused, llm_client=llm_client)
        self.family_name = family_name

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            logger.info("[AcademicPaperExtractor] %s 已暂停, 跳过提取", self.name)
            return []

        logger.info("[AcademicPaperExtractor] 开始动态提取, trace_id=%s", trace_id)

        # Step 1: 从 arXiv 获取最新论文
        papers_text = self._fetch_papers()

        # Step 2: 使用 LLM 提取因子
        if papers_text:
            logger.info(
                "[AcademicPaperExtractor] 成功获取论文, 长度=%d, 开始 LLM 提取",
                len(papers_text),
            )
            candidates = self._llm_extract_factors(papers_text, trace_id, max_factors=5)
            for c in candidates:
                c["parent_topic"] = f"extractor_pipeline/{self.name}/{c.get('name', 'unknown')}"
                c["source"] = "l1_extractor_pipeline"
                c["market"] = "futures"
            logger.info(
                "[AcademicPaperExtractor] 提取完成: %d 个候选, trace_id=%s",
                len(candidates),
                trace_id,
            )
            return candidates

        logger.info(
            "[AcademicPaperExtractor] 论文获取为空, 返回空列表, trace_id=%s",
            trace_id,
        )
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
                except Exception as e:
                    logger.debug(
                        "[AcademicPaperExtractor] 类别 %s 请求失败: %s",
                        cat,
                        e,
                    )
                    continue

            return "\n---\n".join(text_parts) if text_parts else ""

        except Exception as e:
            logger.warning(
                "[AcademicPaperExtractor] arXiv API 全部不可用: %s",
                e,
            )
            return ""


# ─── 管道 ──────────────────────────────────────────────────


class FuturesExtractorPipeline(BaseExtractorPipeline):
    """期货三源提取器管道。

    源 1 (tinysoft): 从 YAML 文件读取，首次提取后自动暂停
    源 2 (broker_reports): 从东方财富 API 实时获取研报，LLM 提取因子
    源 3 (academic_papers): 从 arXiv API 实时获取论文，LLM 提取因子
    """

    def __init__(
        self,
        state_path: str | Path = "memory/extractors/state.json",
        pause_tinysoft_after_first: bool = True,
        llm_client: Optional[Any] = None,
        macro_enabled: bool = True,
        state_store: Any | None = None,
    ):
        """
        Args:
            state_path: 状态持久化路径
            pause_tinysoft_after_first: 是否在首次提取后自动暂停天软源
            llm_client: LLM 客户端（用于动态因子提取）
            macro_enabled: 是否启用宏观事件提取器（GAP-I103）
            state_store: 可选状态存储（StateKVStore），缺省用全局 SSOT（供测试隔离）
        """
        self._pause_tinysoft_after_first = pause_tinysoft_after_first
        self._first_extract = True

        # 源 1: 天软（YAML 静态读取）
        # 源 2: 券商研报（动态，LLM 提取）
        # 源 3: 学术论文（动态，arXiv + LLM 提取）
        extractors = [
            YamlSeedExtractor(
                name="tinysoft",
                yaml_file=SEEDS_DIR / FACTOR_FILE_MAP["tinysoft"],
                family_name="tinysoft_momentum",
                paused=False,  # 首次提取启用
            ),
            ResearchReportExtractor(
                name="broker_reports",
                family_name="broker_cta",
                paused=False,  # 默认启用
                llm_client=llm_client,
            ),
            AcademicPaperExtractor(
                name="academic_papers",
                family_name="academic_papers",
                paused=False,  # 默认启用
                llm_client=llm_client,
            ),
        ]
        # GAP-I103 (v2.80.0): 另类知识源——宏观事件（期货侧，跨品种方向注入）
        if macro_enabled:
            from .alternative_sources import MacroEventExtractor

            extractors.append(
                MacroEventExtractor(
                    name="macro_events",
                    family_name="macro_events",
                    paused=False,
                    llm_client=llm_client,
                    market="futures",
                )
            )

        super().__init__(
            extractors=extractors,
            market="futures",
            state_path=state_path,
            state_store=state_store,
        )

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """执行提取，首次提取后自动暂停天软源。"""
        candidates = super().extract(trace_id)

        # 如果首次提取且配置了自动暂停，提取后暂停天软源
        if self._first_extract and self._pause_tinysoft_after_first:
            tinysoft = self.extractors.get("tinysoft")
            if tinysoft and not tinysoft.paused:
                self.pause_source("tinysoft")
                logger.info(
                    "[FuturesExtractorPipeline] 天软源首次提取完成，已自动暂停。"
                    " 后续可通过 resume_source('tinysoft') 恢复或指定提取。"
                )
            self._first_extract = False

        return candidates


# ─── 便捷工厂函数 ──────────────────────────────────────────


def create_futures_extractor_pipeline(
    state_path: str | Path = "memory/extractors/state.json",
    pause_tinysoft_after_first: bool = True,
    llm_client: Optional[Any] = None,
    macro_enabled: bool = True,
) -> FuturesExtractorPipeline:
    """创建期货提取器管道。

    Args:
        state_path: 状态持久化路径
        pause_tinysoft_after_first: 是否在首次提取后自动暂停天软源
        llm_client: LLM 客户端（用于动态因子提取）
        macro_enabled: 是否启用宏观事件提取器（GAP-I103）

    Returns:
        FuturesExtractorPipeline 实例
    """
    return FuturesExtractorPipeline(
        state_path=state_path,
        pause_tinysoft_after_first=pause_tinysoft_after_first,
        llm_client=llm_client,
        macro_enabled=macro_enabled,
    )
