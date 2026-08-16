"""
fts/factor_engine/extractors/bulk_collector.py — plans/44 P0 批量采集层（全球多源）

三层管线之「采集层」：批量拉取全球论文/研报元数据（标题+摘要），零 LLM token，
增量去重后落 DuckDB `l1_knowledge_cache` 缓存。输出计数契约供每日 ≥300 篇审计。

来源（全球范围，如实标注可用性）:
    - arxiv:      arXiv q-fin 全球量化/金融论文（官方 API，可靠）
    - openalex:   OpenAlex 开放学术（覆盖 SSRN 预印本 + 全球期刊论文，官方 API，可靠）
    - eastmoney:  东财全行业研报（中文，国内券商，官方 API，2026-08-17 修复 beginTime/endTime/qType）
    - global:     CFTC COT 周报 / IEA / OPEC / EIA 原油与商品市场公开报告（best effort）
    - nonen:      日韩法能源研究机构研报（IEEJ/KEEI/IFPEN，网页链接标题，best effort）
    - crossref:   Crossref 全球期刊/预印本论文（官方 API，覆盖 DOI 文献，可靠）
    - nber:       NBER 经济学工作论文（官方 API，宏观/金融/商品主题，可靠）
    - cninfo:     巨潮资讯上市公司公告/研报（中文，官方查询接口）
    - sina:       新浪财经研报中心（中文研报列表，HTML 解析，best effort）
    - semanticscholar: Semantic Scholar 学术论文（官方 API，429 限速重试 + 降级）

存储: data/l1_knowledge_cache.duckdb（独立库，避免与行情库锁竞争；E.4 短连接 + filelock）
契约: collect(source) -> BulkCollectResult{collected, new, deduped, errors}
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_KNOWLEDGE_DB = _PROJECT_ROOT / "data" / "l1_knowledge_cache.duckdb"

_ARXIV_API = "http://export.arxiv.org/api/query"
_ARXIV_HEADERS = {"User-Agent": "Mozilla/5.0 (FTS L1 knowledge collector)"}
_ARXIV_DEFAULT_CATEGORIES = ["q-fin.ST", "q-fin.GN", "q-fin.PM", "q-fin.RM"]
_OPENALEX_API = "https://api.openalex.org/works"
_EASTMONEY_REPORT_API = "https://reportapi.eastmoney.com/report/list"
_EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/",
}
# 全球商品/原油公开报告（best effort 源，各 URL 独立失败不阻断）
_GLOBAL_REPORT_SOURCES = [
    {"name": "CFTC_COT", "url": "https://www.cftc.gov/files/dea/history/fut_fin_txt_2024.zip", "kind": "zip"},
    {"name": "EIA_WPSR", "url": "https://www.eia.gov/petroleum/supply/weekly/", "kind": "html"},
    {"name": "IEA_OIL", "url": "https://www.iea.org/topics/oil-market-report", "kind": "html"},
    {"name": "OPEC_MOMR", "url": "https://www.opec.org/opec_web/en/publications/338.htm", "kind": "html"},
]
# plans/44 Phase 2 补丁（2026-08-16 用户确认"全球范围内不限中英文"）：
# OpenAlex 多语种分路本地化检索关键词（commodity/energy factor 主题，ISO 639-1 语种码）
_OPENALEX_LANG_QUERIES: dict[str, str] = {
    "en": "commodity futures factor OR quantitative trading OR CTA OR term structure",
    "zh": "商品期货 因子 量化交易 OR 期限结构",
    "ja": "商品先物 ファクター OR エネルギー 価格 OR 先物市場",
    "de": "Rohstoff-Futures Faktor OR Energiepreis OR Terminmarkt",
    "fr": "futures matières premières facteur OR prix de l'énergie",
    "ko": "상품 선물 팩터 OR 에너지 가격 OR 선물시장",
    "es": "futuros materias primas factor OR precio energía",
    "ru": "товарные фьючерсы фактор OR цена энергия",
}
# 非中英语种能源/商品公开研究机构（无官方 API，网页检索链接标题，best effort）
_NON_EN_REPORT_SOURCES = [
    {"name": "IEEJ_JP", "url": "https://eneken.ieej.or.jp/", "language": "ja"},  # 日本エネルギー経済研究所
    {"name": "KEEI_KR", "url": "https://www.keei.re.kr/keei.nsf/main/main", "language": "ko"},  # 에너지경제연구원
    {"name": "IFPEN_FR", "url": "https://www.ifpenergiesnouvelles.com/", "language": "fr"},  # IFP Énergies nouvelles
]
# 导航垃圾链接标题黑名单（2026-08-17 修复：nonen 源误采 "Skip to main content" 等导航项）
_NAV_TITLE_BLOCKLIST = (
    "skip to",
    "main content",
    "main menu",
    "main navigation",
    "search",
    "login",
    "sign in",
    "log in",
    "register",
    "contact",
    "privacy",
    "terms of use",
    "cookie",
    "sitemap",
    "language",
    "accessibility",
    "subscribe",
    "newsletter",
    "careers",
    "jobs",
    "about us",
    "presentation",
    "governance",
    "organization",
    "regional sites",
    "areas of expertise",
    "public policy",
    "research and innovation",
    "follow us",
    "legal notice",
    "press",
    "media",
    "twitter",
    "linkedin",
    "youtube",
    "facebook",
    "rss",
)
# 全球期刊/预印本论文（Crossref，官方 API）
_CROSSREF_API = "https://api.crossref.org/works"
_CROSSREF_HEADERS = {"User-Agent": "FTS/1.0 (L1 knowledge collector; mailto:fts@example.com)"}
# NBER 工作论文（官方 API）
_NBER_API = "https://www.nber.org/api/v1/working_page_listing/contentType/working_paper/_/_/search"
# 巨潮资讯（中文公告/研报查询）
_CNINFO_API = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.cninfo.com.cn/",
    "Content-Type": "application/x-www-form-urlencoded",
}
# 新浪财经研报中心
_SINA_REPORT_API = "https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml"
# Semantic Scholar（免费无 key，严格限速 1rps，429 重试）
_SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_SEMANTIC_SCHOLAR_HEADERS = {"User-Agent": "FTS/1.0 (L1 knowledge collector; mailto:fts@example.com)"}


@dataclass
class BulkCollectResult:
    """单源采集结果计数契约。"""

    source: str
    collected: int = 0  # 本次拉取总条数
    new: int = 0  # 新增（去重后）条数
    deduped: int = 0  # 重复（历史已入库）条数
    errors: list[str] = field(default_factory=list)


class BulkKnowledgeStore:
    """l1_knowledge_cache 增量存储（DuckDB，E.4 短连接 + filelock 写）。

    表: l1_knowledge_cache(source TEXT, ref_id TEXT, date TEXT, title TEXT,
                           abstract TEXT, url TEXT, language TEXT,
                           PRIMARY KEY (source, ref_id))
    """

    def __init__(self, db_path: str | Path = _KNOWLEDGE_DB):
        self.db_path = Path(db_path)

    def _connect(self):
        import duckdb

        return duckdb.connect(str(self.db_path))

    def _ensure_schema(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS l1_knowledge_cache (
                source TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                date TEXT,
                title TEXT,
                abstract TEXT,
                url TEXT,
                language TEXT,
                collected_at TEXT,
                PRIMARY KEY (source, ref_id)
            )
            """
        )

    def upsert(self, source: str, records: list[dict[str, Any]]) -> tuple[int, int]:
        """幂等写入（(source, ref_id) 主键），返回 (new, deduped)。

        写路径: filelock 跨进程互斥 + 短连接（对齐 E.4 S1）。
        """
        if not records:
            return 0, 0
        from fts.store.duckdb_lock import duckdb_write_lock

        new = 0
        deduped = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb_write_lock(self.db_path, timeout=30.0):
            with self._connect() as conn:
                self._ensure_schema(conn)
                existing = {
                    r[0]
                    for r in conn.execute(
                        "SELECT ref_id FROM l1_knowledge_cache WHERE source = ?", [source]
                    ).fetchall()
                }
                now = datetime.now().isoformat(timespec="seconds")
                for rec in records:
                    ref_id = rec.get("ref_id") or _default_ref_id(source, rec)
                    if ref_id in existing:
                        deduped += 1
                        continue
                    conn.execute(
                        """
                        INSERT INTO l1_knowledge_cache
                            (source, ref_id, date, title, abstract, url, language, collected_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            source,
                            ref_id,
                            rec.get("date", ""),
                            rec.get("title", ""),
                            rec.get("abstract", ""),
                            rec.get("url", ""),
                            rec.get("language", "unknown"),
                            now,
                        ],
                    )
                    existing.add(ref_id)
                    new += 1
        return new, deduped

    def recent(self, source: Optional[str] = None, since_days: int = 3, limit: int = 500) -> list[dict[str, Any]]:
        """读取近期采集记录（供粗筛层消费）。

        2026-08-17 修复：按 `collected_at`（采集时间）而非 `date`（论文发布日）过滤——
        arXiv 当日采集的论文发布日可能早于窗口，按 date 过滤会全部漏掉（GAP-13x）。
        """
        try:
            with self._connect() as conn:
                self._ensure_schema(conn)
                since = (datetime.now() - timedelta(days=since_days)).isoformat(timespec="seconds")
                sql = "SELECT source, ref_id, date, title, abstract, url, language, collected_at FROM l1_knowledge_cache"
                params: list[Any] = []
                conds: list[str] = []
                if source:
                    conds.append("source = ?")
                    params.append(source)
                conds.append("COALESCE(collected_at, '') >= ?")
                params.append(since)
                if conds:
                    sql += " WHERE " + " AND ".join(conds)
                sql += " ORDER BY collected_at DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
                return [
                    {
                        "source": r[0],
                        "ref_id": r[1],
                        "date": r[2],
                        "title": r[3],
                        "abstract": r[4],
                        "url": r[5],
                        "language": r[6],
                        "collected_at": r[7],
                    }
                    for r in rows
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.store] 读取失败: %s", e)
            return []


def _default_ref_id(source: str, rec: dict[str, Any]) -> str:
    raw = f"{source}|{rec.get('title', '')}|{rec.get('date', '')}".strip()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _http_get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 30) -> Optional[requests.Response]:
    """统一 HTTP GET（单源失败返回 None，不抛）。"""
    try:
        return requests.get(url, params=params, headers=headers, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.warning("[bulk.http] 请求失败 url=%s, error=%s", url, e)
        return None


class ArxivBulkCollector:
    """arXiv 全球量化/金融论文（q-fin 分类，submittedDate 倒序）。"""

    def __init__(self, max_results: int = 50, categories: Optional[list[str]] = None):
        self.max_results = max_results
        self.categories = categories or _ARXIV_DEFAULT_CATEGORIES

    def fetch(self) -> list[dict[str, Any]]:
        import xml.etree.ElementTree as ET

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        out: list[dict[str, Any]] = []
        for cat in self.categories:
            resp = _http_get(
                _ARXIV_API,
                params={
                    "search_query": f"cat:{cat}",
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "max_results": self.max_results,
                },
                headers=_ARXIV_HEADERS,
            )
            if resp is None or resp.status_code != 200:
                logger.warning("[bulk.arxiv] 类别 %s 请求失败 status=%s", cat, getattr(resp, "status_code", None))
                continue
            try:
                root = ET.fromstring(resp.content)
                for entry in root.findall("atom:entry", ns):
                    title_el = entry.find("atom:title", ns)
                    summary_el = entry.find("atom:summary", ns)
                    id_el = entry.find("atom:id", ns)
                    published_el = entry.find("atom:published", ns)
                    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
                    summary = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
                    ref_id = (id_el.text or "").strip() if id_el is not None else ""
                    if not title or not ref_id:
                        continue
                    out.append(
                        {
                            "ref_id": ref_id.rsplit("/abs/", 1)[-1] if ref_id else "",
                            "date": (published_el.text or "")[:10] if published_el is not None else "",
                            "title": title,
                            "abstract": summary,
                            "url": ref_id,
                            "language": "en",
                        }
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[bulk.arxiv] 解析失败 cat=%s, error=%s", cat, e)
        return out


class OpenAlexBulkCollector:
    """OpenAlex 开放学术多语种分路（覆盖全球期刊 + SSRN 预印本 + 各语种论文）。

    plans/44 Phase 2 补丁（2026-08-16 用户确认"全球范围内不限中英文"）：
    由单英文 query 扩展为多语种分路——逐语种本地化关键词检索
    （l1_openalex_languages 配置，默认 en/zh/ja/de/fr/ko/es/ru），
    language 字段如实标注各语种；单语种失败仅记录不阻断。
    """

    def __init__(
        self,
        max_results: int = 50,
        days: int = 3,
        languages: Optional[list[str]] = None,
    ):
        self.max_results = max_results
        self.days = days
        self.languages = languages or list(_OPENALEX_LANG_QUERIES)

    def fetch(self) -> list[dict[str, Any]]:
        from_date = (datetime.now() - timedelta(days=self.days)).date().isoformat()
        out: list[dict[str, Any]] = []
        per_lang = max(5, self.max_results // max(1, len(self.languages)))
        for lang in self.languages:
            query = _OPENALEX_LANG_QUERIES.get(lang, _OPENALEX_LANG_QUERIES["en"])
            resp = _http_get(
                _OPENALEX_API,
                params={
                    "search": query,
                    "filter": f"from_publication_date:{from_date},type:article,language:{lang}",
                    "per-page": per_lang,
                    "sort": "publication_date:desc",
                },
                timeout=30,
            )
            if resp is None or resp.status_code != 200:
                logger.warning("[bulk.openalex] 语种 %s 请求失败 status=%s", lang, getattr(resp, "status_code", None))
                continue
            try:
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                logger.warning("[bulk.openalex] JSON 解析失败 lang=%s: %s", lang, e)
                continue
            for w in data.get("results", []) or []:
                title = (w.get("title") or "").strip()
                ref_id = w.get("id", "")
                if not title or not ref_id:
                    continue
                abstract_inv = w.get("abstract_inverted_index") or {}
                # inverted index → 明文摘要（OpenAlex 返回倒排索引）
                words: list[tuple[int, str]] = []
                for word, positions in abstract_inv.items():
                    for pos in positions:
                        words.append((pos, word))
                abstract = " ".join(w for _, w in sorted(words))[:1000]
                out.append(
                    {
                        "ref_id": ref_id.rsplit("/", 1)[-1],
                        "date": (w.get("publication_date") or "")[:10],
                        "title": title,
                        "abstract": abstract,
                        "url": w.get("doi") or w.get("id") or "",
                        "language": lang,
                    }
                )
        logger.info("[bulk.openalex] 获取完成: %d 篇 languages=%s", len(out), self.languages)
        return out


class EastmoneyReportBulkCollector:
    """东财研报（中文，国内券商）——pageSize 扩容 + 全行业覆盖 + 关键词过滤。

    2026-08-17 修复 400：report/list 接口必填 beginTime/endTime（缺参报
    "Required String parameter 'beginTime' is not present"），且 qType 不可缺省
    （缺参报 "cannot be translated into a null value"）。补参数后 reportType 1/2/3
    实测 200。窗口 window_days 默认 3 天，与 since_days 采集窗口对齐。
    """

    _KEYWORDS = ("量化", "CTA", "期货", "化工", "能化", "原油", "商品", "宏观", "策略", "聚酯", "甲醇")

    def __init__(self, page_size: int = 100, pages: int = 2, window_days: int = 3):
        self.page_size = page_size
        self.pages = pages
        self.window_days = window_days

    def fetch(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        end = datetime.now().date()
        begin = end - timedelta(days=self.window_days)
        for report_type in (1, 2, 3):  # 个股/行业/策略研报
            for page in range(1, self.pages + 1):
                resp = _http_get(
                    _EASTMONEY_REPORT_API,
                    params={
                        "cb": "",
                        "pageSize": self.page_size,
                        "industryCode": "*",
                        "pageNo": page,
                        "reportType": report_type,
                        "columnsType": 1,
                        "source": "WEB",
                        "client": "WEB",
                        "beginTime": begin.isoformat(),
                        "endTime": end.isoformat(),
                        "qType": 0,
                    },
                    headers=_EASTMONEY_HEADERS,
                    timeout=20,
                )
                if resp is None or resp.status_code != 200:
                    logger.warning(
                        "[bulk.eastmoney] 请求失败 reportType=%s page=%s status=%s",
                        report_type,
                        page,
                        getattr(resp, "status_code", None),
                    )
                    continue
                try:
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[bulk.eastmoney] JSON 解析失败 reportType=%s: %s", report_type, e)
                    continue
                rows = data.get("data") or []
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    title = (r.get("title") or "").strip()
                    if not title:
                        continue
                    out.append(
                        {
                            "ref_id": str(r.get("infoCode") or r.get("code") or _default_ref_id("eastmoney", r)),
                            "date": (r.get("publishDate") or "")[:10],
                            "title": title,
                            "abstract": (r.get("summary") or "")[:800],
                            "url": r.get("infoUrl") or r.get("url") or "",
                            "language": "zh",
                        }
                    )
        # 去重（东财多接口可能返回同一条）
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for rec in out:
            key = rec["ref_id"]
            if key in seen:
                continue
            seen.add(key)
            unique.append(rec)
        return unique


class GlobalReportBulkCollector:
    """全球商品/原油公开报告（CFTC COT / EIA / IEA / OPEC）——best effort。

    各源独立失败仅记录 errors，不阻断整体（防反爬/改版单点失效）。
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch(self) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        errors: list[str] = []
        for src in _GLOBAL_REPORT_SOURCES:
            resp = _http_get(src["url"], timeout=self.timeout)
            if resp is None or resp.status_code != 200:
                errors.append(f"{src['name']}: status={getattr(resp, 'status_code', 'unreachable')}")
                continue
            text = resp.text[:4000]
            # 标题抽取（HTML 粗解析：<title>）
            title = src["name"]
            import re

            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
            if m:
                title = (m.group(1).strip()[:200] or src["name"])
            out.append(
                {
                    "ref_id": f"{src['name']}-{datetime.now().date().isoformat()}",
                    "date": datetime.now().date().isoformat(),
                    "title": title,
                    "abstract": re.sub(r"<[^>]+>", " ", text)[:800],
                    "url": src["url"],
                    "language": "en",
                }
            )
        return out, errors


def _extract_link_records(text: str, base_url: str, max_items: int = 12) -> list[tuple[str, str]]:
    """从 HTML 提取候选链接标题（href + 标题文本），best effort。

    2026-08-17 修复：过滤导航垃圾链接（"Skip to main content"、"Governance"、
    "Presentation" 等黑名单命中项 + href 锚点/javascript），避免非研报页面导航入库。
    """
    import re

    def _is_nav(title: str, href: str) -> bool:
        low = title.lower()
        if any(k in low for k in _NAV_TITLE_BLOCKLIST):
            return True
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            return True
        # 仅含 1-2 个英文单词的短标题（如 "Governance"、"News"）视为导航
        words = low.split()
        if len(words) <= 2 and all(w.isascii() and w.isalpha() for w in words):
            return True
        return False

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.IGNORECASE | re.DOTALL):
        href, raw_title = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>", " ", raw_title)
        title = re.sub(r"\s+", " ", title).strip()
        if not (10 <= len(title) <= 200):
            continue
        if _is_nav(title, href):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        url = href if href.startswith("http") else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
        out.append((title, url))
        if len(out) >= max_items:
            break
    return out


class NonEnReportBulkCollector:
    """非中英语种能源/商品公开研究报告（日本 IEEJ / 韩国 KEEI / 法国 IFPEN）——best effort。

    plans/44 Phase 2 补丁（2026-08-16 用户确认"全球范围内不限中英文"）：
    无官方 API 的日/韩/法能源研究机构，网页检索链接标题构建记录
    （title-only，ref_id 按标题哈希保持跨日去重稳定），language 如实标注
    ja/ko/fr；各源独立失败仅 errors，不阻断整体。
    """

    def __init__(self, timeout: int = 15, max_items: int = 12):
        self.timeout = timeout
        self.max_items = max_items

    def fetch(self) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        errors: list[str] = []
        for src in _NON_EN_REPORT_SOURCES:
            resp = _http_get(src["url"], timeout=self.timeout)
            if resp is None or resp.status_code != 200:
                errors.append(f"{src['name']}: status={getattr(resp, 'status_code', 'unreachable')}")
                continue
            links = _extract_link_records(resp.text, src["url"], max_items=self.max_items)
            if not links:
                errors.append(f"{src['name']}: no extractable links")
                continue
            for title, url in links:
                out.append(
                    {
                        "ref_id": f"{src['name']}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:12]}",
                        "date": datetime.now().date().isoformat(),
                        "title": title,
                        "abstract": "",
                        "url": url,
                        "language": src["language"],
                    }
                )
        return out, errors


# ─── 新增源（2026-08-17 plans/44 扩容：全部加上，各源独立失败不阻断） ──


class CrossrefBulkCollector:
    """Crossref 全球期刊/预印本论文（DOI 文献，官方 API）。

    覆盖期刊文章/预印本/会议论文，query 检索 + from-pub-date 窗口过滤；
    issued 年缺失时用空日期兜底；abstract 可能为 JATS XML 需去标签。
    """

    _QUERY = "commodity futures factor OR CTA OR term structure OR energy price"

    def __init__(self, max_results: int = 100, days: int = 3, query: Optional[str] = None):
        self.max_results = max_results
        self.days = days
        self.query = query or self._QUERY

    def fetch(self) -> list[dict[str, Any]]:
        from_date = (datetime.now() - timedelta(days=self.days)).date().isoformat()
        resp = _http_get(
            _CROSSREF_API,
            params={
                "query": self.query,
                "rows": self.max_results,
                "select": "DOI,title,abstract,issued,container-title",
                "filter": f"from-pub-date:{from_date}",
                "mailto": "fts@example.com",
            },
            headers=_CROSSREF_HEADERS,
            timeout=30,
        )
        if resp is None or resp.status_code != 200:
            return []
        try:
            items = (resp.json().get("message") or {}).get("items") or []
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.crossref] JSON 解析失败: %s", e)
            return []
        out: list[dict[str, Any]] = []
        for it in items:
            title = (it.get("title") or [""])[0].strip()
            if not title:
                continue
            doi = (it.get("DOI") or "").strip()
            abstract = it.get("abstract") or ""
            abstract = re.sub(r"<[^>]+>", " ", abstract).strip()[:1000]
            date_parts = (it.get("issued") or {}).get("date-parts") or [[]]
            year = (date_parts[0][0] if date_parts and date_parts[0] else "")
            out.append(
                {
                    "ref_id": doi or _default_ref_id("crossref", {"title": title}),
                    "date": str(year) if year else "",
                    "title": title,
                    "abstract": abstract,
                    "url": f"https://doi.org/{doi}" if doi else "",
                    "language": "en",
                }
            )
        return out


class NberBulkCollector:
    """NBER 经济学工作论文（官方 API，宏观/金融/商品主题）。"""

    _QUERY = "commodity futures energy financial markets"

    def __init__(self, max_results: int = 50, query: Optional[str] = None):
        self.max_results = max_results
        self.query = query or self._QUERY

    def fetch(self) -> list[dict[str, Any]]:
        resp = _http_get(
            _NBER_API,
            params={"keyword": self.query},
            timeout=30,
        )
        if resp is None or resp.status_code != 200:
            return []
        try:
            results = (resp.json().get("results") or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.nber] JSON 解析失败: %s", e)
            return []
        out: list[dict[str, Any]] = []
        for w in results[: self.max_results]:
            title = (w.get("title") or "").strip()
            if not title:
                continue
            nid = str(w.get("id") or "")
            abstract = (w.get("abstract") or "").strip()[:1000]
            # displaydate 形如 "August 2026" → 转 YYYY-MM 无精确日，用发布日期字段兜底
            display = (w.get("displaydate") or "").strip()
            out.append(
                {
                    "ref_id": nid or _default_ref_id("nber", {"title": title}),
                    "date": display,
                    "title": title,
                    "abstract": abstract,
                    "url": w.get("url") or f"https://www.nber.org/papers/{nid}" if nid else "",
                    "language": "en",
                }
            )
        return out


class CninfoBulkCollector:
    """巨潮资讯中文公告/研报（官方查询接口，POST）。"""

    _KEYWORDS = ("期货", "化工", "能化", "原油", "聚酯", "甲醇", "量化")
    _COLUMNS = ("szse", "sse")  # 深市 + 沪市

    def __init__(self, max_items: int = 100, days: int = 3, keywords: Optional[list[str]] = None):
        self.max_items = max_items
        self.days = days
        self.keywords = keywords or self._KEYWORDS

    def fetch(self) -> list[dict[str, Any]]:
        end = datetime.now().date()
        begin = end - timedelta(days=self.days)
        out: list[dict[str, Any]] = []
        for kw in self.keywords:
            for col in self._COLUMNS:
                try:
                    # 巨潮接口为 POST 表单（2026-08-17 修复：原 _http_get 仅 GET 致采集 0）
                    resp = requests.post(
                        _CNINFO_API,
                        data={
                            "pageNum": 1,
                            "pageSize": min(self.max_items, 30),
                            "column": col,
                            "tabName": "fulltext",
                            "plate": "",
                            "stock": "",
                            "searchkey": kw,
                            "secid": "",
                            "category": "",
                            "trade": "",
                            "seDate": f"{begin.isoformat()}~{end.isoformat()}",
                            "sortName": "",
                            "sortType": "",
                            "isHLtitle": "true",
                        },
                        headers=_CNINFO_HEADERS,
                        timeout=20,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("[bulk.cninfo] 请求失败 kw=%s col=%s: %s", kw, col, e)
                    continue
                if resp is None or resp.status_code != 200:
                    logger.warning(
                        "[bulk.cninfo] 请求失败 kw=%s col=%s status=%s",
                        kw,
                        col,
                        getattr(resp, "status_code", None),
                    )
                    continue
                try:
                    anns = (resp.json().get("announcements") or [])
                except Exception as e:  # noqa: BLE001
                    logger.warning("[bulk.cninfo] JSON 解析失败 kw=%s col=%s: %s", kw, col, e)
                    continue
                for a in anns:
                    title = (a.get("announcementTitle") or "").strip()
                    # 公告标题带 <em> 高亮标签
                    title = re.sub(r"<[^>]+>", "", title).strip()
                    if not title:
                        continue
                    ts = a.get("announcementTime")
                    date = ""
                    if isinstance(ts, (int, float)) and ts > 0:
                        date = datetime.fromtimestamp(ts / 1000).date().isoformat()
                    adj = a.get("adjunctUrl") or ""
                    out.append(
                        {
                            "ref_id": str(a.get("announcementId") or _default_ref_id("cninfo", {"title": title, "date": date})),
                            "date": date,
                            "title": title,
                            "abstract": (a.get("announcementContent") or "")[:800],
                            "url": f"https://static.cninfo.com.cn/{adj}" if adj else "",
                            "language": "zh",
                        }
                    )
        # 关键词去重（同一公告命中多关键词只留一条）
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for rec in out:
            key = rec["ref_id"]
            if key in seen:
                continue
            seen.add(key)
            unique.append(rec)
        return unique[: self.max_items]


class SinaReportBulkCollector:
    """新浪财经研报中心（中文研报列表，HTML 解析，best effort）。"""

    _KEYWORDS = ("期货", "化工", "原油", "能化", "量化")

    def __init__(self, max_items: int = 50, keywords: Optional[list[str]] = None):
        self.max_items = max_items
        self.keywords = keywords or self._KEYWORDS

    def fetch(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kw in self.keywords:
            resp = _http_get(
                _SINA_REPORT_API,
                params={"symbol": "", "t": 1, "q": kw},
                timeout=20,
            )
            if resp is None or resp.status_code != 200:
                continue
            links = _extract_link_records(resp.text, _SINA_REPORT_API, max_items=self.max_items)
            for title, url in links:
                out.append(
                    {
                        "ref_id": f"sina-{hashlib.sha1(f'{kw}|{title}'.encode('utf-8')).hexdigest()[:12]}",
                        "date": "",
                        "title": title,
                        "abstract": "",
                        "url": url,
                        "language": "zh",
                    }
                )
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for rec in out:
            key = rec["ref_id"]
            if key in seen:
                continue
            seen.add(key)
            unique.append(rec)
        return unique[: self.max_items]


class SemanticScholarBulkCollector:
    """Semantic Scholar 学术论文（官方 API，免费无 key）。

    严格限速：429 时按 Retry-After/2s 退避重试最多 3 次，仍失败返回空（不阻断）。
    """

    _QUERY = "commodity futures factor quantitative trading"

    def __init__(self, max_results: int = 100, query: Optional[str] = None, retries: int = 3):
        self.max_results = max_results
        self.query = query or self._QUERY
        self.retries = retries

    def fetch(self) -> list[dict[str, Any]]:
        for attempt in range(self.retries):
            resp = _http_get(
                _SEMANTIC_SCHOLAR_API,
                params={
                    "query": self.query,
                    "limit": min(self.max_results, 100),
                    "fields": "title,abstract,url,year,externalIds",
                },
                headers=_SEMANTIC_SCHOLAR_HEADERS,
                timeout=30,
            )
            if resp is not None and resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = min(float(retry_after) if retry_after else 2.0, 10.0)
                logger.warning("[bulk.semanticscholar] 429 限速, 退避 %.1fs (attempt %d)", delay, attempt + 1)
                time.sleep(delay)
                continue
            if resp is None or resp.status_code != 200:
                return []
            break
        else:
            logger.warning("[bulk.semanticscholar] 429 重试耗尽, 返回空")
            return []
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.semanticscholar] JSON 解析失败: %s", e)
            return []
        out: list[dict[str, Any]] = []
        for p in (data.get("data") or [])[: self.max_results]:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            paper_id = p.get("paperId") or ""
            out.append(
                {
                    "ref_id": paper_id or _default_ref_id("semanticscholar", {"title": title}),
                    "date": str(p.get("year") or ""),
                    "title": title,
                    "abstract": (p.get("abstract") or "")[:1000],
                    "url": p.get("url") or "",
                    "language": "en",
                }
            )
        return out


# ─── 采集引擎（契约入口） ─────────────────────────────────


class DynamicSourceCollector:
    """动态源采集器（plans/46 M3+M4）：按注册表 type 驱动解析。

    - json: 请求 JSON，尝试通用字段提取（title/url/date/abstract）
    - rss:  内置 xml.etree 解析 RSS/Atom 条目
    - html: 链接提取（复用 _extract_link_records）+ LLM 兜底（M4，可选）

    解析失败返回空列表（best-effort，不阻断整体）；单源独立失败记录 errors。
    """

    def __init__(
        self,
        source_id: str,
        url: str,
        type_: str = "html",
        language: str = "en",
        max_items: int = 50,
        llm_client: Optional[Any] = None,
        timeout: int = 30,
    ):
        self.source_id = source_id
        self.url = url
        self.type = type_
        self.language = language or "en"
        self.max_items = max_items
        self.llm_client = llm_client
        self.timeout = timeout

    def fetch(self) -> list[dict[str, Any]]:
        if self.type == "json":
            return self._fetch_json()
        if self.type == "rss":
            return self._fetch_rss()
        return self._fetch_html()

    def _fetch_json(self) -> list[dict[str, Any]]:
        resp = _http_get(self.url, timeout=self.timeout)
        if resp is None or resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.dynamic.json] %s JSON 解析失败: %s", self.source_id, e)
            return []
        items = data if isinstance(data, list) else data.get("items") or data.get("results") or data.get("data") or []
        if not isinstance(items, list):
            return []
        out: list[dict[str, Any]] = []
        for it in items[: self.max_items]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or it.get("name") or it.get("headline") or "").strip()
            if not title:
                continue
            link = str(it.get("url") or it.get("link") or it.get("href") or "")
            abstract = str(it.get("abstract") or it.get("summary") or it.get("description") or it.get("excerpt") or "")[:800]
            date = str(it.get("date") or it.get("published") or it.get("pubDate") or it.get("created") or "")[:10]
            out.append(
                {
                    "ref_id": _default_ref_id(self.source_id, {"title": title, "date": date}),
                    "date": date,
                    "title": title,
                    "abstract": abstract,
                    "url": link,
                    "language": self.language,
                }
            )
        return out

    def _fetch_rss(self) -> list[dict[str, Any]]:
        import xml.etree.ElementTree as ET

        resp = _http_get(self.url, timeout=self.timeout)
        if resp is None or resp.status_code != 200:
            return []
        out: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.dynamic.rss] %s XML 解析失败: %s", self.source_id, e)
            return []
        # 兼容 RSS(<item>) 与 Atom(<entry>)
        entries = root.iter("{http://www.w3.org/2005/Atom}entry") if any(
            e.tag.endswith("}entry") for e in root.iter()
        ) else list(root.iter("item"))
        atom = bool(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        for item in entries[: self.max_items]:
            if atom:
                title_el = item.find("{http://www.w3.org/2005/Atom}title")
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                date_el = item.find("{http://www.w3.org/2005/Atom}updated")
                abstract_el = item.find("{http://www.w3.org/2005/Atom}summary")
                title = (title_el.text or "").strip() if title_el is not None else ""
                link = (link_el.get("href") or "") if link_el is not None else ""
                date = (date_el.text or "")[:10] if date_el is not None else ""
                abstract = (abstract_el.text or "")[:800] if abstract_el is not None else ""
            else:
                title_el = item.find("title")
                link_el = item.find("link")
                date_el = item.find("pubDate")
                abstract_el = item.find("description")
                title = (title_el.text or "").strip() if title_el is not None else ""
                link = (link_el.text or "") if link_el is not None else ""
                date = (date_el.text or "")[:10] if date_el is not None else ""
                abstract = (abstract_el.text or "")[:800] if abstract_el is not None else ""
            if not title:
                continue
            out.append(
                {
                    "ref_id": _default_ref_id(self.source_id, {"title": title, "date": date}),
                    "date": date,
                    "title": title,
                    "abstract": abstract,
                    "url": link,
                    "language": self.language,
                }
            )
        return out

    def _fetch_html(self) -> list[dict[str, Any]]:
        resp = _http_get(self.url, timeout=self.timeout)
        if resp is None or resp.status_code != 200:
            return []
        links = _extract_link_records(resp.text, self.url, max_items=self.max_items)
        import html as _html

        records = [
            {
                "ref_id": f"{self.source_id}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:12]}",
                "date": "",
                "title": _html.unescape(title),
                "abstract": "",
                "url": url,
                "language": self.language,
            }
            for title, url in links
        ]
        # M4: 规则提取为空且可用 LLM → LLM 兜底提取标题/摘要
        if not records and self.llm_client is not None:
            return self._llm_fallback_html(resp.text)
        return records

    def _llm_fallback_html(self, text: str) -> list[dict[str, Any]]:
        prompt = (
            "从以下 HTML 去标签文本中提取最多 {} 条研报/论文条目，每条输出 "
            '{{"title": "...", "url": "...", "abstract": "..."}}，只输出 JSON 数组：\n'
            "{}"
        ).format(self.max_items, text[:8000])
        try:
            data = self.llm_client.generate_json(prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[bulk.dynamic.html] %s LLM 兜底失败: %s", self.source_id, e)
            return []
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for it in data:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            out.append(
                {
                    "ref_id": f"{self.source_id}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:12]}",
                    "date": "",
                    "title": title,
                    "abstract": str(it.get("abstract") or "")[:800],
                    "url": str(it.get("url") or ""),
                    "language": self.language,
                }
            )
        return out[: self.max_items]


def _collect_dynamic_source(source_id: str, store: Optional[BulkKnowledgeStore]) -> list[dict[str, Any]]:
    """动态源采集入口：按注册表信息构造采集器（未注册/未知源返回空）。"""
    try:
        from .source_registry import SourceRegistry

        info = SourceRegistry().get(source_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[bulk.dynamic] 读取注册表失败 source=%s: %s", source_id, e)
        return []
    if info is None:
        return []
    collector = DynamicSourceCollector(
        source_id=info.source_id,
        url=info.url,
        type_=info.type,
        language=info.language,
    )
    return collector.fetch()


def _is_registered_dynamic_source(source_id: str) -> bool:
    """注册表中是否存在该 source_id（用于未知源错误语义区分）。"""
    try:
        from .source_registry import SourceRegistry

        return SourceRegistry().get(source_id) is not None
    except Exception:  # noqa: BLE001
        return False


def collect_bulk(
    source: str,
    store: Optional[BulkKnowledgeStore] = None,
    max_results: int = 50,
    page_size: int = 100,
    openalex_languages: Optional[list[str]] = None,
    non_en_reports_enabled: bool = True,
) -> BulkCollectResult:
    """单源采集 → 入库，返回计数契约。

    Args:
        source: arxiv / openalex / eastmoney / global / nonen / crossref / nber /
                cninfo / sina / semanticscholar
        store: 存储（None 用默认 data/l1_knowledge_cache.duckdb）
        max_results: 论文类源每类/每语种拉取数（arxiv 每类别 / openalex 每语种）
        page_size: 东财研报分页大小
        openalex_languages: OpenAlex 多语种分路语种清单（None 用默认 8 语种）
        non_en_reports_enabled: 非中英语种研报源开关（false 跳过 nonen 采集）
    """
    store = store or BulkKnowledgeStore()
    result = BulkCollectResult(source=source)
    t0 = time.time()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if source == "arxiv":
        records = ArxivBulkCollector(max_results=max_results).fetch()
    elif source == "openalex":
        records = OpenAlexBulkCollector(max_results=max_results, languages=openalex_languages).fetch()
    elif source == "eastmoney":
        records = EastmoneyReportBulkCollector(page_size=page_size).fetch()
    elif source == "global":
        records, errors = GlobalReportBulkCollector().fetch()
    elif source == "nonen":
        if non_en_reports_enabled:
            records, errors = NonEnReportBulkCollector().fetch()
        else:
            errors.append("nonen disabled by l1_non_en_reports_enabled")
    elif source == "crossref":
        records = CrossrefBulkCollector(max_results=max_results).fetch()
    elif source == "nber":
        records = NberBulkCollector(max_results=max_results).fetch()
    elif source == "cninfo":
        records = CninfoBulkCollector(max_items=page_size).fetch()
    elif source == "sina":
        records = SinaReportBulkCollector(max_items=page_size).fetch()
    elif source == "semanticscholar":
        records = SemanticScholarBulkCollector(max_results=max_results).fetch()
    else:
        # 动态源（plans/46）：注册表 active 源按 type 驱动解析
        records = _collect_dynamic_source(source, store)
        if not records and not _is_registered_dynamic_source(source):
            errors.append(f"未知源: {source}")
    result.collected = len(records)
    result.errors = errors
    result.new, result.deduped = store.upsert(source, records)
    logger.info(
        "[bulk] 采集完成 source=%s collected=%d new=%d deduped=%d errors=%d elapsed_ms=%.1f",
        source,
        result.collected,
        result.new,
        result.deduped,
        len(errors),
        (time.time() - t0) * 1000,
    )
    return result


def collect_all(
    store: Optional[BulkKnowledgeStore] = None,
    max_results: int = 50,
    page_size: int = 100,
    openalex_languages: Optional[list[str]] = None,
    non_en_reports_enabled: bool = True,
    include_dynamic: bool = True,
    registry: Optional[Any] = None,
) -> dict[str, BulkCollectResult]:
    """全源采集（全球 300 篇口径审计入口，含新增 crossref/nber/cninfo/sina/semanticscholar）。

    plans/46: include_dynamic=True 时叠加注册表动态源（active 直接采集，pending 走 canary）。
    """
    results: dict[str, BulkCollectResult] = {}
    for src in (
        "arxiv",
        "openalex",
        "eastmoney",
        "global",
        "nonen",
        "crossref",
        "nber",
        "cninfo",
        "sina",
        "semanticscholar",
    ):
        results[src] = collect_bulk(
            src,
            store=store,
            max_results=max_results,
            page_size=page_size,
            openalex_languages=openalex_languages,
            non_en_reports_enabled=non_en_reports_enabled,
        )
    if include_dynamic:
        results.update(_collect_dynamic_sources(store, registry=registry))
    total = sum(r.collected for r in results.values())
    new_total = sum(r.new for r in results.values())
    logger.info("[bulk] 全源采集完成: total_collected=%d total_new=%d", total, new_total)
    return results


def _collect_dynamic_sources(
    store: Optional[BulkKnowledgeStore],
    registry: Optional[Any] = None,
) -> dict[str, BulkCollectResult]:
    """注册表动态源采集：active 源直接采集，pending 源 canary 试采，健康度回写。

    Returns:
        {source_id: BulkCollectResult}
    """
    try:
        from .source_registry import SourceRegistry

        reg = registry or SourceRegistry()
        active = reg.list_active()
        pending = reg.list_status("pending")
    except Exception as e:  # noqa: BLE001
        logger.warning("[bulk.dynamic] 注册表读取失败: %s", e)
        return {}
    results: dict[str, BulkCollectResult] = {}
    for info in active:
        res = collect_bulk(info.source_id, store=store)
        results[info.source_id] = res
        if res.collected > 0:
            reg.mark_collect_success(info.source_id)
        else:
            reg.mark_collect_failure(info.source_id)
    for info in pending:
        # canary 试采（小批量 page_size 传 20），成功计入 canary 进度
        res = collect_bulk(info.source_id, store=store, page_size=20)
        results[info.source_id] = res
        reg.canary_tick(info.source_id, ok=res.collected > 0)
    return results
