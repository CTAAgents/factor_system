"""
fts.data_mcp_bridge — MCP 数据桥接层

桥接 FTS 因子引擎与 TRAE MCP 数据源（东方财富妙想 mx API）。
通过本地缓存机制，支持在 Agent 预填充数据后由 FTS 代码读取。

数据流:
    Agent (run_mcp) → mx_ashare_finance_data → 本地缓存 JSON
    FTS FundamentalProvider → MCPBridge → 本地缓存 JSON
                                    ↘ 合成数据降级

HARNESS §契约优先: 缓存 Schema 通过本模块 TypedDict 管理。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── 缓存路径 ───────────────────────────────────────────────

CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = CACHE_DIR / "fundamental_cache.json"


def _ensure_cache_dir() -> None:
    """确保缓存目录存在。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── 缓存记录结构 ───────────────────────────────────────────

# cache[symbol] = {
#     "pe_ttm": float,
#     "pb": float,
#     "total_market_cap": float,
#     "roe": float,
#     "eps": float,
#     "revenue_growth": float,
#     "profit_growth": float,
#     "gross_margin": float,
#     "net_margin": float,
#     "debt_to_equity": float,
#     "current_ratio": float,
#     "bps": float,
#     "turnover_rate": float,
#     "free_market_cap": float,
#     "ps_ttm": float,
#     "pcf_ttm": float,
#     "asset_growth": float,
#     "updated_at": str,   # ISO 时间戳
# }


# ─── MCP 桥接器 ─────────────────────────────────────────────

class MCPBridge:
    """MCP 数据桥接器 — 从本地缓存读取基本面数据。

    缓存由 Agent 通过 run_mcp 预填充，FTS 运行时只读。
    缓存未命中时返回空字典，由调用方自行降级。

    用法:
        bridge = MCPBridge()
        data = bridge.get_fundamental("000001")
        if data:
            pe = data["pe_ttm"]
    """

    def __init__(self, cache_file: str | Path = CACHE_FILE):
        self._cache_file = Path(cache_file)
        self._cache: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        """加载缓存文件。"""
        if self._loaded:
            return
        self._loaded = True
        if not self._cache_file.exists():
            logger.info(f"MCP 缓存不存在: {self._cache_file}")
            return
        try:
            with open(self._cache_file, encoding="utf-8") as f:
                raw = json.load(f)
            self._cache = raw.get("data", {})
            meta = raw.get("meta", {})
            count = len(self._cache)
            updated = meta.get("updated_at", "unknown")
            logger.info(f"MCP 缓存已加载: {count} 只股票, 更新于 {updated}")
        except Exception as e:
            logger.warning(f"MCP 缓存加载失败: {e}")

    def get_fundamental(self, symbol: str) -> dict[str, Any]:
        """获取单只股票的基本面数据。

        Args:
            symbol: 股票代码（如 "000001" / "sh600519"）。

        Returns:
            基本面字段字典，缓存未命中时返回空字典。
        """
        self._load()
        # 标准化代码
        code = _normalize_code(symbol)
        return self._cache.get(code, {})

    def get_batch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取多只股票的基本面数据。"""
        self._load()
        result: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            data = self.get_fundamental(sym)
            if data:
                result[sym] = data
        return result

    @property
    def cache_size(self) -> int:
        """缓存中的股票数量。"""
        self._load()
        return len(self._cache)

    @property
    def cache_stocks(self) -> list[str]:
        """缓存中的所有股票代码。"""
        self._load()
        return list(self._cache.keys())

    def get_cache_age_hours(self) -> float:
        """缓存年龄（小时）。"""
        self._load()
        if not self._cache_file.exists():
            return float("inf")
        mtime = os.path.getmtime(self._cache_file)
        age = (datetime.now().timestamp() - mtime) / 3600
        return age


# ─── 辅助函数 ───────────────────────────────────────────────

def _normalize_code(code: str) -> str:
    """标准化股票代码为缓存查询键。

    "000001" → "000001"
    "sh600519" → "600519"
    "SZ000001" → "000001"
    """
    raw = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ", "HK"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw


# ─── 缓存更新辅助（供 Agent 调用） ─────────────────────────

def _parse_mx_response(data: list[dict]) -> dict[str, dict[str, Any]]:
    """解析 mx_ashare_finance_data 的响应为结构化缓存。

    Args:
        data: mx API 返回的 data 列表，每个元素是一个 sheet。

    Returns:
        dict[symbol, dict[str, float]] — 结构化基本面数据。
    """
    result: dict[str, dict[str, Any]] = {}
    current_stock: dict[str, Any] = {}

    for sheet in data:
        sheet_name = sheet.get("sheetName", "")
        columns = sheet.get("columns", [])
        items = sheet.get("items", [])

        # 从 sheet 名称或 columns 中提取股票代码
        stock_code = _extract_code_from_sheet(sheet_name, columns)
        if not stock_code:
            continue

        if stock_code not in result:
            result[stock_code] = {}
            current_stock = result[stock_code]
        else:
            current_stock = result[stock_code]

        # 解析行数据
        for row in items:
            if len(row) < 2:
                continue
            metric_name = str(row[0]).strip()
            # 取最新一期的值（通常是第二列）
            raw_value = str(row[1]).strip() if len(row) > 1 else ""

            parsed = _parse_metric_value(metric_name, raw_value)
            if parsed is not None:
                key, value = parsed
                current_stock[key] = value

    return result


def _extract_code_from_sheet(sheet_name: str, columns: list) -> str:
    """从 sheet 名称或 columns 中提取股票代码。

    "平安银行(000001.SZ)" → "000001"
    "万科A(000002.SZ)" → "000002"
    """
    import re

    # 从 sheet 名称中提取
    m = re.search(r"\((\d{6})\.", sheet_name)
    if m:
        return m.group(1)

    # 从 columns 中提取
    for col in columns:
        col_str = str(col)
        m = re.search(r"(\d{6})\.", col_str)
        if m:
            return m.group(1)

    return ""


def _parse_metric_value(metric_name: str, raw_value: str) -> tuple | None:
    """解析指标名和原始值为 (key, value) 或 None。

    支持的指标:
        - 市盈率PE(TTM) → pe_ttm
        - 市净率PB → pb
        - 总市值 → total_market_cap
        - 净资产收益率ROE → roe
        - 每股收益EPS → eps
        - 营业收入同比增长率 → revenue_growth
        - 净利润同比增长率 → profit_growth
        - 毛利率 → gross_margin
        - 净利率 → net_margin
        - 每股净资产 → bps
    """
    mn = metric_name.replace(" ", "")

    # 估值类
    if "市盈率PE" in mn or "市盈率" in mn:
        val = _parse_number(raw_value)
        if val is not None and val > 0:
            return ("pe_ttm", val)
    if "市净率PB" in mn or "市净率" in mn:
        val = _parse_number(raw_value)
        if val is not None and val > 0:
            return ("pb", val)
    if "总市值" in mn:
        val = _parse_market_cap(raw_value)
        if val is not None:
            return ("total_market_cap", val)

    # 财务质量类
    if "净资产收益率ROE" in mn or ("ROE" in mn and "加权" not in mn):
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("roe", val)
    if "净资产收益率ROE(加权)" in mn:
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("roe", val)

    if "每股收益EPS" in mn:
        val = _parse_number(raw_value)
        if val is not None:
            return ("eps", val)
    if "每股净资产" in mn:
        val = _parse_number(raw_value)
        if val is not None:
            return ("bps", val)

    # 成长类
    if "营业收入同比增长率" in mn:
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("revenue_growth", val)
    if "净利润同比增长率" in mn:
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("profit_growth", val)

    # 毛利率
    if "毛利率" in mn:
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("gross_margin", val)

    # 净利率
    if "净利率" in mn:
        val = _parse_percentage(raw_value)
        if val is not None:
            return ("net_margin", val)

    return None


def _parse_number(s: str) -> float | None:
    """解析数字字符串，处理"元"等单位。

    "2.219元" → 2.219
    "--" → None
    """
    if not s or s in ("--", "-", ""):
        return None
    s = s.replace("元", "").replace("港元", "").replace("美元", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_percentage(s: str) -> float | None:
    """解析百分比字符串。

    "2.83%" → 0.0283
    "5.241倍" → None (倍数不解析为百分比)
    """
    if not s or s in ("--", "-", ""):
        return None
    s = s.strip()
    if "%" in s:
        try:
            return float(s.replace("%", "")) / 100.0
        except (ValueError, TypeError):
            return None
    return None


def _parse_market_cap(s: str) -> float | None:
    """解析市值字符串。

    "2257亿" → 225700000000
    "6682亿" → 668200000000
    "398.5亿" → 39850000000
    """
    if not s or s in ("--", "-", ""):
        return None
    s = s.replace(" ", "")
    try:
        if "万亿" in s:
            return float(s.replace("万亿", "")) * 1e12
        if "亿" in s:
            return float(s.replace("亿", "")) * 1e8
        if "万" in s:
            return float(s.replace("万", "")) * 1e4
        return float(s)
    except (ValueError, TypeError):
        return None


# ─── 缓存保存（供 Agent 调用） ─────────────────────────────

def save_cache(data: dict[str, dict[str, Any]], *,
               source: str = "mx_api") -> None:
    """保存缓存数据到文件。

    Args:
        data: dict[symbol, dict[str, float]]
        source: 数据来源标识
    """
    _ensure_cache_dir()
    cache = {
        "meta": {
            "source": source,
            "updated_at": datetime.now().isoformat(),
            "stock_count": len(data),
        },
        "data": data,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    logger.info(f"MCP 缓存已保存: {len(data)} 只股票 → {CACHE_FILE}")


# ─── 缺省实例 ───────────────────────────────────────────────

_default_bridge: Optional[MCPBridge] = None


def get_bridge() -> MCPBridge:
    """获取全局 MCPBridge 实例（惰性初始化）。"""
    global _default_bridge  # noqa: PLW0603
    if _default_bridge is None:
        _default_bridge = MCPBridge()
    return _default_bridge


__all__ = [
    "MCPBridge",
    "get_bridge",
    "save_cache",
    "_parse_mx_response",
    "CACHE_FILE",
]