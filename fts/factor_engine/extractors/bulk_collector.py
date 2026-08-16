"""
fts/factor_engine/extractors/bulk_collector.py — plans/44 P0 批量采集层（全球多源）

三层管线之「采集层」：批量拉取全球论文/研报元数据（标题+摘要），零 LLM token，
增量去重后落 DuckDB `l1_knowledge_cache` 缓存。输出计数契约供每日 ≥300 篇审计。

来源（全球范围，如实标注可用性）:
    - arxiv:      arXiv q-fin 全球量化/金融论文（官方 API，可靠）
    - openalex:   OpenAlex 开放学术（覆盖 SSRN 预印本 + 全球期刊论文，官方 API，可靠）
    - eastmoney:  东财全行业研报（中文，国内券商，官方 API）
    - global:     CFTC COT 周报 / IEA / OPEC / EIA 原油与商品市场公开报告（best effort）

存储: data/l1_knowledge_cache.duckdb（独立库，避免与行情库锁竞争；E.4 短连接 + filelock）
契约: collect(source) -> BulkCollectResult{collected, new, deduped, errors}
"""

from __future__ import annotations

import hashlib
import logging
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
        """读取近期采集记录（供粗筛层消费）。"""
        try:
            with self._connect() as conn:
                self._ensure_schema(conn)
                since = (datetime.now() - timedelta(days=since_days)).date().isoformat()
                sql = "SELECT source, ref_id, date, title, abstract, url, language FROM l1_knowledge_cache"
                params: list[Any] = []
                conds: list[str] = []
                if source:
                    conds.append("source = ?")
                    params.append(source)
                conds.append("COALESCE(date, '') >= ?")
                params.append(since)
                if conds:
                    sql += " WHERE " + " AND ".join(conds)
                sql += " ORDER BY date DESC LIMIT ?"
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
    """东财研报（中文，国内券商）——pageSize 扩容 + 全行业覆盖 + 关键词过滤。"""

    _KEYWORDS = ("量化", "CTA", "期货", "化工", "能化", "原油", "商品", "宏观", "策略", "聚酯", "甲醇")

    def __init__(self, page_size: int = 100, pages: int = 2):
        self.page_size = page_size
        self.pages = pages

    def fetch(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
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
                    if not title or not any(k in title for k in self._KEYWORDS):
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
    """从 HTML 提取候选链接标题（href + 标题文本），best effort。"""
    import re

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.IGNORECASE | re.DOTALL):
        href, raw_title = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>", " ", raw_title)
        title = re.sub(r"\s+", " ", title).strip()
        if not (10 <= len(title) <= 200):
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


# ─── 采集引擎（契约入口） ─────────────────────────────────


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
        source: arxiv / openalex / eastmoney / global / nonen
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
    else:
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
) -> dict[str, BulkCollectResult]:
    """全源采集（全球 300 篇口径审计入口，含非中英语种源 nonen）。"""
    results: dict[str, BulkCollectResult] = {}
    for src in ("arxiv", "openalex", "eastmoney", "global", "nonen"):
        results[src] = collect_bulk(
            src,
            store=store,
            max_results=max_results,
            page_size=page_size,
            openalex_languages=openalex_languages,
            non_en_reports_enabled=non_en_reports_enabled,
        )
    total = sum(r.collected for r in results.values())
    new_total = sum(r.new for r in results.values())
    logger.info("[bulk] 全源采集完成: total_collected=%d total_new=%d", total, new_total)
    return results
