"""
fts/factor_engine/extractors/source_discovery.py — 知识源自动发现（plans/46 发现层）

四层管道之「发现层」：WebSearch 检索 → LLM 提取候选源（name/url/type/region/language）
→ 写 l1_source_discovery 暂存表（与注册表 URL 去重）→ 探活达标后注册进
l1_knowledge_sources。全流程无人（plans/46 定位：因子产出导向，坏源由产出淘汰）。

用法:
    discoverer = SourceDiscoverer(llm_client=llm_client, registry=registry)
    candidates = discoverer.discover(trace_id="l1_xxx")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from .source_registry import (
    SourceInfo,
    SourceProber,
    SourceRegistry,
    is_probe_acceptable,
    _source_id_from_url,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DISCOVERY_DB = _PROJECT_ROOT / "data" / "l1_source_discovery.duckdb"

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S)
_WS_RE = re.compile(r"\s+")

# 检索主题模板（量化/能化/论文/研报源发现方向）
_DISCOVERY_QUERIES = (
    "commodity futures research paper source arxiv SSRN API",
    "energy market report source CFTC EIA IEA publication",
    "商品期货 量化研报 数据源 网站",
    "commodity futures factor research RSS feed",
    "能化 产业链 研报 发布 平台",
)

# LLM 提取 prompt：从搜索结果文本提取候选源（name/url/type/region/language）
_EXTRACT_PROMPT = """你是知识源发现助手。从下面的网页搜索结果文本中，找出**可能提供论文/研报列表的网站或 API 源**（能源、商品、量化金融领域优先）。

对每个候选源输出 JSON 数组项，字段：
- name: 源名称（简短）
- url: 主页或列表页 URL（必须 http(s)）
- type: json | rss | html（猜测，可空）
- region: 地区（en/zh/ja/kr/fr 等，可空）
- language: 主语言（可空）
- reason: 一句话说明为什么可能是研报/论文源

只输出 JSON 数组，不要输出其他内容。搜索结果文本：
---BEGIN---
{search_text}
---END---"""


@dataclass
class DiscoveryRecord:
    """发现暂存记录（l1_source_discovery 表字段对齐）。"""

    url: str
    name: str = ""
    type: str = ""
    region: str = ""
    language: str = ""
    discoverer_trace_id: str = ""
    probe_score: float = 0.0
    probe_status: str = ""  # pending / ok / fail
    discovered_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return _source_id_from_url(self.url)


class DiscoveryStore:
    """l1_source_discovery 暂存表（DuckDB，E.4 短连接 + filelock 写）。"""

    def __init__(self, db_path: str | Path = _DEFAULT_DISCOVERY_DB):
        self.db_path = Path(db_path)

    def _connect(self):
        import duckdb

        return duckdb.connect(str(self.db_path))

    def _ensure_schema(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS l1_source_discovery (
                source_id TEXT PRIMARY KEY,
                url TEXT,
                name TEXT,
                type TEXT,
                region TEXT,
                language TEXT,
                discoverer_trace_id TEXT,
                probe_score REAL DEFAULT 0.0,
                probe_status TEXT DEFAULT 'pending',
                discovered_at TEXT
            )
            """
        )

    def upsert(self, rec: DiscoveryRecord) -> bool:
        """写入（source_id 主键）。返回是否新增。"""
        from fts.store.duckdb_lock import duckdb_write_lock

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat(timespec="seconds")
        with duckdb_write_lock(self.db_path, timeout=30.0):
            with self._connect() as conn:
                self._ensure_schema(conn)
                existing = conn.execute(
                    "SELECT 1 FROM l1_source_discovery WHERE source_id = ?", [rec.source_id]
                ).fetchall()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO l1_source_discovery (
                        source_id, url, name, type, region, language,
                        discoverer_trace_id, probe_score, probe_status, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        rec.source_id, rec.url, rec.name, rec.type, rec.region, rec.language,
                        rec.discoverer_trace_id, rec.probe_score, rec.probe_status,
                        rec.discovered_at or now,
                    ],
                )
                return not existing


class SourceDiscoverer:
    """发现层：WebSearch → LLM 提取 → 暂存 → 探活 → 注册。"""

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        registry: Optional[Any] = None,
        store: Optional[DiscoveryStore] = None,
        prober: Optional[SourceProber] = None,
        queries: Optional[tuple[str, ...]] = None,
        max_candidates: int = 10,
        min_probe_score: float = 0.5,
    ):
        self.llm_client = llm_client
        self.registry = registry or SourceRegistry()
        self.store = store or DiscoveryStore()
        self.prober = prober or SourceProber()
        self.queries = queries or _DISCOVERY_QUERIES
        self.max_candidates = max_candidates
        self.min_probe_score = min_probe_score

    def discover(self, trace_id: str = "") -> list[DiscoveryRecord]:
        """执行一轮发现：检索 → 提取 → 暂存 → 探活 → 注册。

        Returns:
            本轮探活达标并注册的候选列表。
        """
        search_text = self._search_all()
        if not search_text:
            logger.warning("[SourceDiscoverer] 检索无文本, 本轮无发现 trace_id=%s", trace_id)
            return []
        candidates = self._extract_candidates(search_text, trace_id)
        logger.info("[SourceDiscoverer] LLM 提取候选 %d 个 trace_id=%s", len(candidates), trace_id)
        registered: list[DiscoveryRecord] = []
        for cand in candidates[: self.max_candidates]:
            rec = DiscoveryRecord(
                url=cand.get("url", ""),
                name=cand.get("name", ""),
                type=cand.get("type", ""),
                region=cand.get("region", ""),
                language=cand.get("language", ""),
                discoverer_trace_id=trace_id,
            )
            if not rec.url or not rec.url.startswith(("http://", "https://")):
                continue
            # 注册表 URL 去重
            if self.registry.get_by_url(rec.url) is not None:
                logger.info("[SourceDiscoverer] 已注册源, 跳过 url=%s", rec.url)
                continue
            is_new = self.store.upsert(rec)
            if not is_new:
                logger.info("[SourceDiscoverer] 候选已存在, 跳过 url=%s", rec.url)
                continue
            # 探活（真实 HTTP 校验，零 LLM）
            probe = self.prober.probe(rec.url, trace_id=trace_id)
            rec.probe_score = probe.score
            rec.probe_status = probe.status
            self.store.upsert(rec)
            if not is_probe_acceptable(probe, min_score=self.min_probe_score):
                logger.info(
                    "[SourceDiscoverer] 探活不达标, 不注册 url=%s type=%s score=%s",
                    rec.url, probe.type, probe.score,
                )
                continue
            # 注册进 SSOT（pending 状态，后续 canary 晋升）
            source = SourceInfo(
                source_id=rec.source_id,
                name=rec.name or probe.title or rec.url,
                url=rec.url,
                type=probe.type,
                region=rec.region,
                language=rec.language,
                discoverer_trace_id=trace_id,
                probe_score=probe.score,
                status="pending",
            )
            self.registry.upsert(source)
            registered.append(rec)
            logger.info(
                "[SourceDiscoverer] 注册动态源 source_id=%s url=%s type=%s score=%s",
                rec.source_id, rec.url, probe.type, probe.score,
            )
        return registered

    def _search_all(self) -> str:
        """逐条必应检索并合并去标签文本（单条失败不阻断）。"""
        texts: list[str] = []
        for q in self.queries:
            try:
                resp = requests.get(
                    "https://www.bing.com/search",
                    params={"q": q, "mkt": "zh-CN"},
                    headers=_UA,
                    timeout=15,
                )
                resp.raise_for_status()
                text = _SCRIPT_STYLE_RE.sub("", resp.text)
                text = _HTML_TAG_RE.sub(" ", text)
                text = _WS_RE.sub(" ", text)
                if text:
                    texts.append(text[:8000])
            except Exception as e:  # noqa: BLE001
                logger.warning("[SourceDiscoverer] 搜索失败 query=%s: %s", q, e)
        merged = "\n".join(texts)
        logger.info("[SourceDiscoverer] 合并检索文本长度=%d", len(merged))
        return merged

    def _extract_candidates(self, search_text: str, trace_id: str) -> list[dict[str, Any]]:
        """LLM 从检索文本提取候选源（无 llm_client 时降级规则粗提取）。"""
        if self.llm_client is None:
            return self._rule_fallback(search_text)
        try:
            prompt = _EXTRACT_PROMPT.format(search_text=search_text[:8000])
            data = self.llm_client.generate_json(prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[SourceDiscoverer] LLM 提取失败, 降级规则: %s", e)
            return self._rule_fallback(search_text)
        if not isinstance(data, list):
            data = data.get("sources", []) if isinstance(data, dict) else []
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and item.get("url"):
                out.append(item)
        return out

    def _rule_fallback(self, text: str) -> list[dict[str, Any]]:
        """无 LLM 时的规则降级：提取 http(s) 链接 + 邻接标题。"""
        links = re.findall(r"(https?://[^\s\"'<>]+)", text)
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for url in links:
            clean = url.rstrip(").,;")
            if clean in seen or not clean.startswith(("http://", "https://")):
                continue
            seen.add(clean)
            out.append({"name": clean.split("/")[2] if "/" in clean[8:] else clean, "url": clean})
        return out
