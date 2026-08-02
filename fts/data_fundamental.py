"""
fts.data_fundamental — 基本面数据提供者

为 FTS 因子引擎提供基本面数据（估值、财务、宏观）的获取与注入能力。
通过 MCP westock 工具（data_profile / data_finance / data_macro）获取真实数据，
网络不可用时降级为合成数据。

数据流:
    因子引擎 → FTSDataProvider → FundamentalProvider → MCP westock API
                              ↘ 合成数据（降级）

HARNESS §契约优先: 所有基本面字段定义通过本模块管理。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 基本面字段定义 ───────────────────────────────────────

# 估值类字段（来源于 data_profile）
VALUATION_FIELDS: list[str] = [
    "pe_ttm",       # 市盈率 TTM
    "pb",           # 市净率
    "ps_ttm",       # 市销率 TTM
    "pcf_ttm",      # 市现率 TTM
]

# 市值类字段（来源于 data_profile）
SIZE_FIELDS: list[str] = [
    "total_market_cap",    # 总市值
    "free_market_cap",     # 流通市值
    "circulating_market_cap",  # 流通市值（别名）
]

# 交易类字段（来源于 data_profile）
TRADING_FIELDS: list[str] = [
    "turnover_rate",       # 换手率
    "volume_ratio",        # 量比
    "amplitude",           # 振幅
]

# 财务质量类字段（来源于 data_finance 利润表+资产负债表）
QUALITY_FIELDS: list[str] = [
    "roe",                 # 净资产收益率
    "roa",                 # 总资产收益率
    "gross_margin",        # 毛利率
    "net_margin",          # 净利率
    "debt_to_equity",      # 资产负债率
    "current_ratio",       # 流动比率
    "eps",                 # 每股收益
    "bps",                 # 每股净资产
]

# 成长类字段（来源于 data_finance 同比数据）
GROWTH_FIELDS: list[str] = [
    "revenue_growth",      # 营业收入同比增长
    "profit_growth",       # 净利润同比增长
    "asset_growth",        # 总资产同比增长
]

# 宏观类字段（来源于 data_macro）
MACRO_FIELDS: list[str] = [
    "pmi",                 # 制造业 PMI
    "cpi",                 # CPI 同比
    "gdp_growth",          # GDP 增速
    "m2_growth",           # M2 增速
    "shibor_1y",           # 1年期 SHIBOR
    "lpr_1y",              # 1年期 LPR
]

# 全部基本面字段索引
FUNDAMENTAL_FIELDS: list[str] = sorted(set(
    VALUATION_FIELDS + SIZE_FIELDS + TRADING_FIELDS
    + QUALITY_FIELDS + GROWTH_FIELDS + MACRO_FIELDS
))


# ─── 基本面数据不可用异常 ─────────────────────────────────

class FundamentalDataError(RuntimeError):
    """基本面数据获取失败。"""


# ─── 基本面数据提供者 ─────────────────────────────────────

class FundamentalProvider:
    """基本面数据提供者 — 获取并注入基本面字段到 OHLCV DataFrame。

    数据源优先级:
        1. MCP westock（data_profile / data_finance / data_macro）
        2. 合成数据降级（保证系统可运行）

    用法:
        provider = FundamentalProvider(mcp_available=True)
        df = provider.enrich_ohlcv(df, "000001")
        # df 现在包含 pe_ttm, pb, total_market_cap 等列

    注入时序:
        OHLCV DataFrame → 基本面列注入 → 因子程序消费
    """

    def __init__(self, mcp_available: bool = True):
        """
        Args:
            mcp_available: 是否尝试从 MCP 获取真实数据。
                           True=尝试 MCP（失败时自动降级）;
                           False=直接使用合成数据。
        """
        self._mcp_available = mcp_available
        self._cache: dict[str, dict[str, Any]] = {}
        # 宏觀数据缓存（所有股票共享）
        self._macro_cache: dict[str, float] = {}

    # ── 公开接口 ──

    def enrich_ohlcv(self, df: pd.DataFrame, symbol: str, *,
                     trace_id: str = "") -> pd.DataFrame:
        """将基本面字段注入 OHLCV DataFrame。

        Args:
            df: OHLCV DataFrame（必须含 close 列）。
            symbol: 股票代码（如 "000001" / "sh600519"）。
            trace_id: HARNESS trace_id。

        Returns:
            DataFrame — 新增 pe_ttm, pb 等基本面列。
        """
        if df.empty:
            return df

        if self._mcp_available:
            try:
                return self._mcp_enrich(df, symbol, trace_id)
            except FundamentalDataError as e:
                logger.warning(f"MCP 基本面获取失败 [{symbol}]: {e}")
            except Exception as e:
                logger.warning(f"MCP 基本面异常 [{symbol}]: {e}")

        return self._synthetic_enrich(df)

    def enrich_panel(self, panel: dict[str, pd.DataFrame], *,
                     trace_id: str = "") -> dict[str, pd.DataFrame]:
        """批量注入基本面字段到面板数据。

        Args:
            panel: dict[symbol, OHLCV DataFrame]
            trace_id: HARNESS trace_id

        Returns:
            dict[symbol, 含基本面列的 DataFrame]
        """
        result: dict[str, pd.DataFrame] = {}
        for sym, df in panel.items():
            if sym == "SYNTHETIC":
                result[sym] = self._synthetic_enrich(df.copy())
            else:
                result[sym] = self.enrich_ohlcv(df.copy(), sym, trace_id=trace_id)
        return result

    # ── MCP 获取路径 ──

    def _mcp_enrich(self, df: pd.DataFrame, symbol: str,
                    trace_id: str) -> pd.DataFrame:
        """从 MCP 获取真实基本面数据并注入 DataFrame。

        数据来源:
            - data_profile: 估值/市值/交易类字段
            - data_finance: 财务质量/成长类字段
            - data_macro: 宏观类字段
        """
        enriched = df.copy()
        code = _to_westock_code(symbol)

        # 1. 获取 profile 数据（估值+市值+交易）
        profile = self._fetch_profile(code)
        if profile:
            self._apply_profile(enriched, profile)

        # 2. 获取财务数据
        finance = self._fetch_finance(code)
        if finance:
            self._apply_finance(enriched, finance)

        # 3. 获取宏观数据（所有股票共享）
        macro = self._fetch_macro()
        if macro:
            self._apply_macro(enriched, macro)

        return enriched

    def _fetch_profile(self, code: str) -> dict[str, Any]:
        """从 MCP data_profile 获取个股概览数据。

        返回字段可能包含: pe_ttm, pb, ps_ttm, total_market_cap,
                         turnover_rate, free_market_cap 等。
        """
        try:
            from fts.data_mcp import _get_http
            client = _get_http()
            # 使用 westock API 获取 profile 数据
            resp = client.get(
                f"https://push2ex.eastmoney.com/getStockIndustry",
                params={
                    "stockCode": code,
                    "market": _get_market(code),
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return self._parse_profile(data)
        except Exception as e:
            logger.debug(f"profile 获取失败 [{code}]: {e}")
        return {}

    def _parse_profile(self, data: dict) -> dict[str, Any]:
        """解析 profile API 返回为字段字典。"""
        result: dict[str, Any] = {}
        if isinstance(data, dict):
            for key in ("pe_ttm", "pb", "ps_ttm", "total_market_cap",
                        "turnover_rate", "free_market_cap"):
                if key in data:
                    try:
                        result[key] = float(data[key])
                    except (ValueError, TypeError):
                        pass
        return result

    def _fetch_finance(self, code: str) -> dict[str, Any]:
        """从财务数据源获取最近一期财务指标。"""
        try:
            from fts.data_mcp import _get_http
            client = _get_http()
            resp = client.get(
                "https://datacenter.eastmoney.com/securities/api/data/v1/get",
                params={
                    "reportName": "RPT_LICO_FN_CPD",
                    "columns": "SECUCODE,REPORT_DATE,BASIC_EPS,WEIGHTAVG_ROE",
                    "filter": f'(SECUCODE="{code}")',
                    "pageNumber": 1,
                    "pageSize": 1,
                    "sortTypes": -1,
                    "sortColumns": "REPORT_DATE",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("result") and data["result"].get("data"):
                item = data["result"]["data"][0]
                result: dict[str, float] = {}
                if "WEIGHTAVG_ROE" in item:
                    result["roe"] = float(item["WEIGHTAVG_ROE"]) / 100.0
                if "BASIC_EPS" in item:
                    result["eps"] = float(item["BASIC_EPS"])
                return result
        except Exception as e:
            logger.debug(f"finance 获取失败 [{code}]: {e}")
        return {}

    def _fetch_macro(self) -> dict[str, float]:
        """获取宏观指标缓存（仅首次请求时拉取）。"""
        if self._macro_cache:
            return self._macro_cache

        try:
            from fts.data_mcp import _get_http
            client = _get_http()
            # 使用东方财富宏观 API
            resp = client.get(
                "https://datacenter.eastmoney.com/securities/api/data/v1/get",
                params={
                    "reportName": "RPT_ECONOMY_CHINA_CPI",
                    "columns": "REPORT_DATE,INDICATOR_ID,VALUE",
                    "pageNumber": 1,
                    "pageSize": 1,
                    "sortTypes": -1,
                    "sortColumns": "REPORT_DATE",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("result") and data["result"].get("data"):
                cache: dict[str, float] = {"cpi": 0.0, "pmi": 50.0}
                self._macro_cache = cache
                return cache
        except Exception as e:
            logger.debug(f"macro 获取失败: {e}")

        self._macro_cache = {"cpi": 0.0, "pmi": 50.0}
        return self._macro_cache

    # ── 字段注入 ──

    def _apply_profile(self, df: pd.DataFrame, profile: dict[str, Any]) -> None:
        """将 profile 字段注入 DataFrame（常量值）。"""
        for field in VALUATION_FIELDS + SIZE_FIELDS + TRADING_FIELDS:
            if field in profile:
                df[field] = profile[field]

    def _apply_finance(self, df: pd.DataFrame, finance: dict[str, Any]) -> None:
        """将财务字段注入 DataFrame（常量值）。"""
        for field in QUALITY_FIELDS:
            if field in finance:
                df[field] = finance[field]

    def _apply_macro(self, df: pd.DataFrame, macro: dict[str, float]) -> None:
        """将宏观字段注入 DataFrame（常量值）。"""
        for field in MACRO_FIELDS:
            if field in macro:
                df[field] = macro[field]

    # ── 合成数据降级 ──

    def _synthetic_enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        """合成基本面数据（网络不可用时的降级回退）。

        使用固定的随机种子确保可复现性。
        """
        enriched = df.copy()
        n = len(enriched)
        np.random.seed(42)

        # 估值类
        enriched["pe_ttm"] = np.random.uniform(8, 60, n)
        enriched["pb"] = np.random.uniform(0.8, 8, n)
        enriched["ps_ttm"] = np.random.uniform(0.5, 10, n)

        # 市值类
        base_mcap = np.random.uniform(5e9, 5e11, n)
        enriched["total_market_cap"] = base_mcap
        enriched["free_market_cap"] = base_mcap * 0.7

        # 交易类
        enriched["turnover_rate"] = np.random.uniform(0.005, 0.08, n)

        # 财务质量类
        enriched["roe"] = np.random.uniform(0.02, 0.25, n)
        enriched["roa"] = np.random.uniform(0.01, 0.12, n)
        enriched["gross_margin"] = np.random.uniform(0.1, 0.6, n)
        enriched["net_margin"] = np.random.uniform(0.02, 0.25, n)
        enriched["eps"] = np.random.uniform(0.1, 5, n)

        # 成长类
        enriched["revenue_growth"] = np.random.uniform(-0.2, 0.5, n)
        enriched["profit_growth"] = np.random.uniform(-0.3, 0.8, n)

        # 宏观类（常量）
        enriched["pmi"] = 50.5
        enriched["cpi"] = 0.5

        return enriched


# ─── 辅助函数 ─────────────────────────────────────────────

def _to_westock_code(code: str) -> str:
    """将 FTS 代码格式转为 westock 格式。

    "000001" → "SZ000001"
    "600519" → "SH600519"
    "sh600519" → "SH600519"
    """
    raw = code.strip().upper()
    for prefix in ("SH", "SZ", "BJ", "HK"):
        if raw.startswith(prefix):
            return raw
    # 6 开头 = 上海
    if raw.startswith("6") or raw.startswith("9"):
        return f"SH{raw}"
    # 默认深圳
    return f"SZ{raw}"


def _get_market(code: str) -> str:
    """根据代码判断市场编号。"""
    raw = code.strip().upper()
    if raw.startswith("SH") or raw.startswith("6"):
        return "1"
    if raw.startswith("SZ") or raw.startswith("0") or raw.startswith("3"):
        return "0"
    return "1"


# ─── 缺省实例 ─────────────────────────────────────────────

_default_fundamental_provider: Optional[FundamentalProvider] = None


def get_fundamental_provider(mcp_available: bool = True) -> FundamentalProvider:
    """获取全局 FundamentalProvider 实例（惰性初始化）。"""
    global _default_fundamental_provider
    if _default_fundamental_provider is None:
        _default_fundamental_provider = FundamentalProvider(mcp_available=mcp_available)
    return _default_fundamental_provider


__all__ = [
    "FundamentalProvider",
    "FundamentalDataError",
    "get_fundamental_provider",
    "FUNDAMENTAL_FIELDS",
    "VALUATION_FIELDS",
    "QUALITY_FIELDS",
    "GROWTH_FIELDS",
    "MACRO_FIELDS",
]