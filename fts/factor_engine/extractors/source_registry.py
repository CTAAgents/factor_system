"""
fts/factor_engine/extractors/source_registry.py — 知识源注册表 + 探活层（plans/46）

四层管道之「注册层 + 探活层」：动态源以 DuckDB `l1_knowledge_sources` 为 SSOT，
探活层纯规则（零 LLM）对候选源做 HTTP 嗅探 + 类型识别 + 可用性评分。

状态流转:
    pending → (canary 试采 N 次成功) → active
    pending → (探活/试采失败) → cooldown → (累计失败) → retired
    active → (连续采集失败) → cooldown → retired
    active → (连续零因子产出 zero_output_rounds≥M) → cooldown（业务维度，可复权）

对齐 plans/46 定位（2026-08-17）：因子产出导向，全流程无人——
坏源由"零因子产出"自动淘汰，探活只做技术校验（HTTP 200 + 结构可解析）。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _PROJECT_ROOT / "data" / "l1_knowledge_sources.duckdb"

# 状态常量
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_COOLDOWN = "cooldown"
STATUS_RETIRED = "retired"

# 类型常量
TYPE_JSON = "json"
TYPE_RSS = "rss"
TYPE_HTML = "html"
TYPE_PDF = "pdf"
TYPE_UNKNOWN = "unknown"

# 健康度阈值（默认值，可经构造参数覆盖）
_DEFAULT_CANARY_ROUNDS = 3
_DEFAULT_FAIL_THRESHOLD = 3
_DEFAULT_RETIRE_FAILURES = 10
_DEFAULT_ZERO_OUTPUT_ROUNDS = 5


def _cfg_int(name: str, default: int) -> int:
    try:
        from fts.config.settings import get_config

        return int(getattr(get_config(), name, default))
    except Exception:  # noqa: BLE001
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        from fts.config.settings import get_config

        return float(getattr(get_config(), name, default))
    except Exception:  # noqa: BLE001
        return default

_HTTP_TIMEOUT = 15
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# 探活识别用的内容特征
_RSS_MARKERS = ("<rss", "<rdf:rdf", "<feed", "application/rss+xml", "application/atom+xml")
_JSON_MARKERS = ("{", "[")
_PDF_MARKER = "%PDF"


@dataclass
class SourceProbeResult:
    """探活结果契约。"""

    url: str
    status: str = "unknown"  # ok / fail
    type: str = TYPE_UNKNOWN
    http_status: Optional[int] = None
    score: float = 0.0  # 0.0~1.0
    title: str = ""
    sample_count: int = 0  # 可解析出的条目数（标题/链接）
    has_date: bool = False
    has_link: bool = False
    error: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "type": self.type,
            "http_status": self.http_status,
            "score": self.score,
            "title": self.title,
            "sample_count": self.sample_count,
            "has_date": self.has_date,
            "has_link": self.has_link,
            "error": self.error,
            "trace_id": self.trace_id,
        }


@dataclass
class SourceInfo:
    """注册表记录（与 l1_knowledge_sources 表字段对齐）。"""

    source_id: str
    name: str
    url: str
    type: str = TYPE_UNKNOWN
    region: str = ""
    language: str = ""
    discoverer_trace_id: str = ""
    probe_score: float = 0.0
    status: str = STATUS_PENDING
    first_seen: str = ""
    last_probe: str = ""
    consecutive_failures: int = 0
    zero_output_rounds: int = 0
    canary_result: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _source_id_from_url(url: str) -> str:
    """url 归一化哈希 → source_id（稳定跨日去重）。"""
    return hashlib.sha1(url.strip().rstrip("/").encode("utf-8")).hexdigest()[:16]


class SourceRegistry:
    """l1_knowledge_sources 注册表（DuckDB，E.4 短连接 + filelock 写）。"""

    def __init__(self, db_path: str | Path = _DEFAULT_DB):
        self.db_path = Path(db_path)

    def _connect(self):
        import duckdb

        return duckdb.connect(str(self.db_path))

    def _ensure_schema(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS l1_knowledge_sources (
                source_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                type TEXT,
                region TEXT,
                language TEXT,
                discoverer_trace_id TEXT,
                probe_score REAL DEFAULT 0.0,
                status TEXT DEFAULT 'pending',
                first_seen TEXT,
                last_probe TEXT,
                consecutive_failures INT DEFAULT 0,
                zero_output_rounds INT DEFAULT 0,
                canary_result TEXT,
                updated_at TEXT
            )
            """
        )

    def _row_to_info(self, row) -> SourceInfo:
        return SourceInfo(
            source_id=row[0],
            name=row[1] or "",
            url=row[2] or "",
            type=row[3] or TYPE_UNKNOWN,
            region=row[4] or "",
            language=row[5] or "",
            discoverer_trace_id=row[6] or "",
            probe_score=float(row[7] or 0.0),
            status=row[8] or STATUS_PENDING,
            first_seen=row[9] or "",
            last_probe=row[10] or "",
            consecutive_failures=int(row[11] or 0),
            zero_output_rounds=int(row[12] or 0),
            canary_result=row[13] or "",
            updated_at=row[14] or "",
        )

    def upsert(self, source: SourceInfo) -> None:
        """写入/更新注册表（source_id 主键）。"""
        from fts.store.duckdb_lock import duckdb_write_lock

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat(timespec="seconds")
        with duckdb_write_lock(self.db_path, timeout=30.0):
            with self._connect() as conn:
                self._ensure_schema(conn)
                conn.execute(
                    """
                    INSERT INTO l1_knowledge_sources (
                        source_id, name, url, type, region, language,
                        discoverer_trace_id, probe_score, status, first_seen,
                        last_probe, consecutive_failures, zero_output_rounds,
                        canary_result, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (source_id) DO UPDATE SET
                        name = excluded.name,
                        url = excluded.url,
                        type = excluded.type,
                        region = excluded.region,
                        language = excluded.language,
                        probe_score = excluded.probe_score,
                        status = excluded.status,
                        last_probe = excluded.last_probe,
                        consecutive_failures = excluded.consecutive_failures,
                        zero_output_rounds = excluded.zero_output_rounds,
                        canary_result = excluded.canary_result,
                        updated_at = excluded.updated_at
                    """,
                    [
                        source.source_id,
                        source.name,
                        source.url,
                        source.type,
                        source.region,
                        source.language,
                        source.discoverer_trace_id,
                        source.probe_score,
                        source.status,
                        source.first_seen or now,
                        source.last_probe or now,
                        source.consecutive_failures,
                        source.zero_output_rounds,
                        source.canary_result,
                        source.updated_at or now,
                    ],
                )

    def get(self, source_id: str) -> Optional[SourceInfo]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM l1_knowledge_sources WHERE source_id = ?", [source_id]
            ).fetchall()
        return self._row_to_info(rows[0]) if rows else None

    def get_by_url(self, url: str) -> Optional[SourceInfo]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM l1_knowledge_sources WHERE url = ?", [url]
            ).fetchall()
        return self._row_to_info(rows[0]) if rows else None

    def list_active(self) -> list[SourceInfo]:
        return self._query_by_status(STATUS_ACTIVE)

    def list_status(self, status: str) -> list[SourceInfo]:
        return self._query_by_status(status)

    def _query_by_status(self, status: str) -> list[SourceInfo]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM l1_knowledge_sources WHERE status = ?", [status]
            ).fetchall()
        return [self._row_to_info(r) for r in rows]

    def list_all(self) -> list[SourceInfo]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM l1_knowledge_sources ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_info(r) for r in rows]

    def set_status(self, source_id: str, status: str) -> None:
        info = self.get(source_id)
        if info is None:
            return
        info.status = status
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)

    def mark_collect_failure(self, source_id: str) -> None:
        """采集失败 → consecutive_failures+1；达阈值冷却/退役。"""
        info = self.get(source_id)
        if info is None:
            return
        retire_threshold = _cfg_int("l1_source_retire_failures", _DEFAULT_RETIRE_FAILURES)
        fail_threshold = _cfg_int("l1_source_fail_threshold", _DEFAULT_FAIL_THRESHOLD)
        info.consecutive_failures += 1
        if info.consecutive_failures >= retire_threshold:
            info.status = STATUS_RETIRED
        elif info.consecutive_failures >= fail_threshold:
            info.status = STATUS_COOLDOWN
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)

    def mark_collect_success(self, source_id: str) -> None:
        """采集成功 → 技术失败清零。"""
        info = self.get(source_id)
        if info is None:
            return
        info.consecutive_failures = 0
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)

    def mark_zero_output(self, source_id: str) -> bool:
        """业务维度：连续零因子产出 +1；达阈值 → cooldown，返回是否被停用。"""
        info = self.get(source_id)
        if info is None:
            return False
        zero_threshold = _cfg_int("l1_source_zero_output_rounds", _DEFAULT_ZERO_OUTPUT_ROUNDS)
        info.zero_output_rounds += 1
        demoted = info.zero_output_rounds >= zero_threshold
        if demoted:
            info.status = STATUS_COOLDOWN
            logger.warning(
                "[SourceRegistry] 源 %s 连续 %d 轮零因子产出，自动停用(cooldown)",
                source_id,
                info.zero_output_rounds,
            )
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)
        return demoted

    def mark_has_output(self, source_id: str) -> None:
        """有因子产出 → 零产出计数清零复权。"""
        info = self.get(source_id)
        if info is None:
            return
        if info.zero_output_rounds > 0:
            info.zero_output_rounds = 0
            logger.info("[SourceRegistry] 源 %s 恢复因子产出，复权", source_id)
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)

    def canary_tick(self, source_id: str, ok: bool, canary_rounds: Optional[int] = None) -> bool:
        """canary 试采：pending 源连续 N 次成功 → active。返回是否晋升。"""
        info = self.get(source_id)
        if info is None:
            return False
        if canary_rounds is None:
            canary_rounds = _cfg_int("l1_source_canary_rounds", _DEFAULT_CANARY_ROUNDS)
        if ok:
            info.canary_result = (info.canary_result + "1") if info.canary_result.startswith("1") else "1"
            info.consecutive_failures = 0
        else:
            info.canary_result = "0"
            info.consecutive_failures += 1
            fail_threshold = _cfg_int("l1_source_fail_threshold", _DEFAULT_FAIL_THRESHOLD)
            info.status = STATUS_COOLDOWN if info.consecutive_failures >= fail_threshold else STATUS_PENDING
        promoted = ok and info.canary_result.count("1") >= canary_rounds
        if promoted:
            info.status = STATUS_ACTIVE
            logger.info("[SourceRegistry] 源 %s canary %d 次成功，晋升 active", source_id, canary_rounds)
        info.updated_at = datetime.now().isoformat(timespec="seconds")
        self.upsert(info)
        return promoted


class SourceProber:
    """探活层（纯规则、零 LLM）：HTTP 嗅探 + 类型识别 + 可用性评分。"""

    def __init__(self, timeout: int = _HTTP_TIMEOUT):
        self.timeout = timeout

    def probe(self, url: str, trace_id: str = "") -> SourceProbeResult:
        try:
            resp = requests.get(url, headers=_UA, timeout=self.timeout, allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[SourceProber] 请求失败 url=%s error=%s", url, e)
            return SourceProbeResult(url=url, status="fail", error=str(e), trace_id=trace_id)
        if resp.status_code != 200:
            return SourceProbeResult(
                url=url, status="fail", http_status=resp.status_code,
                error=f"HTTP {resp.status_code}", trace_id=trace_id,
            )
        return self._analyze(url, resp, trace_id)

    def _analyze(self, url: str, resp: requests.Response, trace_id: str) -> SourceProbeResult:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        text = resp.text[:20000] if resp.encoding else resp.content[:20000].decode("utf-8", "ignore")
        head = text[:2000]
        result = SourceProbeResult(url=url, http_status=200, trace_id=trace_id)

        # 类型识别
        if _PDF_MARKER in head:
            result.type = TYPE_PDF
            result.sample_count = 0
        elif _RSS_MARKERS and any(m in text.lower() for m in _RSS_MARKERS):
            result.type = TYPE_RSS
        elif "json" in ctype or head.lstrip().startswith(_JSON_MARKERS):
            result.type = TYPE_JSON
        elif "<html" in text.lower() or "<!doctype" in text.lower():
            result.type = TYPE_HTML
        else:
            result.type = TYPE_UNKNOWN

        # 标题
        import re

        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if m:
            result.title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]

        # 可用性评分（规则启发式）
        score = 0.0
        if result.type in (TYPE_JSON, TYPE_RSS):
            score += 0.5  # 结构化源基础分
        elif result.type == TYPE_HTML:
            score += 0.3
            # HTML：统计可提取链接数
            import re as _re

            links = _re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', text)
            result.sample_count = len(set(links))
            result.has_link = result.sample_count > 0
            if result.has_link:
                score += 0.3
        elif result.type == TYPE_PDF:
            score += 0.2
        if result.title:
            score += 0.2
        if result.sample_count > 0 or result.type in (TYPE_JSON, TYPE_RSS):
            result.has_date = "date" in text.lower() or result.type == TYPE_RSS
        result.score = round(min(score, 1.0), 2)
        result.status = "ok" if result.score >= 0.5 else "low"
        return result


def is_probe_acceptable(result: SourceProbeResult, min_score: float = 0.5) -> bool:
    """探活达标判定（技术维度）：HTTP 200 + 类型可识别 + 评分达标。"""
    return (
        result.status in ("ok", "low")
        and result.http_status == 200
        and result.type not in (TYPE_UNKNOWN, TYPE_PDF)
        and result.score >= min_score
    )
