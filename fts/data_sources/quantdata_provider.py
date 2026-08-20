"""
fts.data_sources.quantdata_provider — QuantData 权威数据源（v2.105.0+32 规划，GAP-156）。

权威原则
--------
FTS 因子生命周期管理的 **K 线唯一数据源 = QuantData**（v3.0.0+1 起）：
FTS 对 QuantData 的采集方式无感，仅只读消费其 DuckDB 库；不再直连天勤/通达信实时/
AKShare 等外部源（其他来源权威性受质疑，已从默认聚合器移除）。

数据现实（2026-08-19 实测）
---------------------------
`market_data/kline_history.duckdb`（3.3GB）：
  - kline_daily：127.6 万行 / 5339 合约 / 2016-01~2026-08，列 = OHLCV + open_interest（**无 amount/settle**）
  - continuous_daily：23.9 万行 / 88 品种，series_type=main/sub，自带 adj_factor 后复权（重叠窗口平滑换月）
  - continuous_map：12.1 万行 / 88 品种，逐日 main_contract/sub_contract（期限结构构建源）
  - kline_minute / kline_tick / instrument_info

实现约束
--------
- DuckDB **只读短连接**直读（E.4 S1），不依赖 `D:\\QuantData\\client_v2.py`（避免跨项目
  `sys.path.insert` 绝对路径注入，CLAUDE.md §5.9）；路径经 `FTS_QUANTDATA_HOME` 配置解析。
- 复权：continuous_daily 自带后复权 adj_factor，主链路直接消费其复权序列；
  与 FTS `RollCalendar` 二选一（Provider 已复权，上层不得再复权，避免双重复权）。
- settle/amount/pre_settle 为 QuantData 无权威源字段（GAP-158）→ 置 NaN，
  由 aggregator 增强层补充或典型价/均量代理，全程标注非权威。
- oi_change 由 hold 一阶差分自算（v3.0.0+1 去天勤 TQSDKEnhanceSource 后的权威派生）。

字段权威矩阵（SSOT，导入管道/演化约束/信号链路共用）
------------------------------------------------------
L0 权威（QuantData 可得）   : open / high / low / close / volume / hold(=open_interest)
L0 权威（接线后）           : term_spread / roll_yield（continuous_map 近远月构建）
L1 降级·非权威（标注来源）  : vwap / amount / settle / pre_settle
L2 缺失·禁依赖（GAP-157）   : fut_inventory / fut_warehouse_receipt / fut_spot_price /
                              fut_near_basis / fut_dom_basis 等 fundamental 9 字段

版本: v1.0.0
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import numpy as np

from .base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)

# ─── 默认路径与字段权威矩阵 ─────────────────────────────────

QUANTDATA_DEFAULT_HOME = r"D:\QuantData"
QUANTDATA_DB_RELATIVE = "market_data/kline_history.duckdb"

# 字段权威矩阵（SSOT）：导入管道/演化约束/信号链路统一引用
L0_AUTHORITATIVE_FIELDS: frozenset[str] = frozenset(
    {"open", "high", "low", "close", "volume", "hold"}
)
# 接线后升入 L0 的期货结构字段（QuantData continuous_map 近远月构建）
L0_STRUCTURE_FIELDS: frozenset[str] = frozenset({"term_spread", "roll_yield"})
# L1 降级·非权威：FTS 现有缓存/增强源可得，标注来源不硬拒（GAP-158）
L1_FALLBACK_FIELDS: frozenset[str] = frozenset(
    {"vwap", "amount", "settle", "pre_settle", "oi_change"}
)
# L2 缺失·禁依赖：QuantData 无、AKShare 非权威（GAP-157），新因子禁止依赖
L2_MISSING_FIELDS: frozenset[str] = frozenset(
    {
        "fut_inventory", "fut_inventory_chg",
        "fut_warehouse_receipt", "fut_warehouse_receipt_chg",
        "fut_spot_price", "fut_near_basis", "fut_dom_basis",
        "fut_near_basis_rate", "fut_dom_basis_rate",
    }
)


def resolve_quantdata_home() -> str:
    """解析 QuantData 根目录：FTS_QUANTDATA_HOME env 优先，默认 D:\\QuantData。"""
    return os.environ.get("FTS_QUANTDATA_HOME", QUANTDATA_DEFAULT_HOME).strip()


def validate_field_availability(input_fields: list[str] | tuple[str, ...] | frozenset[str]) -> dict:
    """字段权威层校验（导入管道防线，防空谈因子）。

    按字段权威矩阵判定每个字段的层级：
        - L0 权威 / L0 接线后  → 可得
        - L1 降级·非权威       → 可得但标注来源非权威
        - L2 缺失·禁依赖       → 不可得（拒绝/降级）
        - 未登记字段           → 未知（保守按不可得处理）

    Returns:
        {"authoritative": [...], "fallback": [...], "missing": [...], "unknown": [...]}
    """
    authoritative: list[str] = []
    fallback: list[str] = []
    missing: list[str] = []
    unknown: list[str] = []
    for f in input_fields:
        if f in L0_AUTHORITATIVE_FIELDS or f in L0_STRUCTURE_FIELDS:
            authoritative.append(f)
        elif f in L1_FALLBACK_FIELDS:
            fallback.append(f)
        elif f in L2_MISSING_FIELDS:
            missing.append(f)
        else:
            unknown.append(f)
    return {
        "authoritative": authoritative,
        "fallback": fallback,
        "missing": missing,
        "unknown": unknown,
    }


def symbol_to_quantdata(symbol: str) -> str:
    """FTS 连续合约代码 → QuantData 品种代码（去 0 后缀）。

    FTS 用 `RB0`（品种大写+0 连续标记），QuantData continuous_daily 用 `RB`。
    仅对"字母+末尾 0"形态转换（如 RB0→RB、SC0→SC）；具体合约/已无后缀输入原样返回。
    """
    s = symbol.strip()
    if len(s) > 1 and s.endswith("0") and s[:-1].isalpha():
        return s[:-1]
    return s


class QuantDataProvider(BaseFuturesSource):
    """QuantData 权威数据源（只读直读 kline_history.duckdb）。

    与 tdx_local_source 对齐的 17 列 kline_cache schema 输出（date 列），
    settle/amount/pre_settle/oi_change 置 NaN（GAP-158 非权威字段由增强层补充）。
    熔断：连续失败 `circuit_breaker_threshold` 次 + `circuit_breaker_cooldown_seconds` 冷却。
    """

    source_name: str = "QUANTDATA"

    def __init__(
        self,
        home: str | None = None,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_seconds: float = 300.0,
    ) -> None:
        self.home = Path(home or resolve_quantdata_home())
        self._db_path: Path = self.home / QUANTDATA_DB_RELATIVE
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown_seconds = circuit_breaker_cooldown_seconds
        self._consecutive_failures = 0
        self._breaker_open_until: float = 0.0

    # ─── 连接与探活 ──

    def _connect(self):
        """DuckDB 只读短连接（E.4 S1：读连接一律 read_only 短生命周期）。"""
        import duckdb  # 惰性导入，避免模块级硬依赖

        return duckdb.connect(str(self._db_path), read_only=True)

    def _is_circuit_open(self) -> bool:
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            if time.time() >= self._breaker_open_until:
                # 冷却结束，半开重试
                self._consecutive_failures = 0
                return False
            return True
        return False

    def _record_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._breaker_open_until = time.time() + self.circuit_breaker_cooldown_seconds
            logger.warning(
                "[QUANTDATA] 连续 %d 次失败触发熔断（冷却 %.0fs）: %s",
                self._consecutive_failures,
                self.circuit_breaker_cooldown_seconds,
                reason,
            )

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def is_available(self) -> bool:
        """探活：文件存在 + 只读连接可打开（熔断期返回 False）。"""
        if self._is_circuit_open():
            return False
        if not self._db_path.exists():
            logger.warning("[QUANTDATA] 数据库不存在: %s", self._db_path)
            return False
        try:
            con = self._connect()
            try:
                con.execute("SELECT 1").fetchone()
            finally:
                con.close()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[QUANTDATA] 探活失败: %s", e)
            self._record_failure(str(e))
            return False

    # ─── 品种映射 ──

    @staticmethod
    def _map_symbol(symbol: str) -> str:
        return symbol_to_quantdata(symbol)

    # ─── BaseFuturesSource 接口 ──

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """拉取 QuantData 连续合约日线（17 列 kline_cache schema，date 列）。

        主链路读取 continuous_daily 的 series_type='main'（后复权连续序列，自带 adj_factor），
        open_interest → hold（L0 权威）；oi_change 由 hold 一阶差分自算（v3.0.0+1 去天勤
        TQSDKEnhanceSource 后由本 Provider 直接提供，无天勤依赖）。非权威字段
        （amount/settle/pre_settle）置 NaN（GAP-158）；vwap 用典型价 (H+L+C)/3。
        """
        if self._is_circuit_open():
            raise SourceUnavailable(self.source_name, "熔断器 OPEN（连续失败超阈值）")
        qs = self._map_symbol(symbol)
        try:
            con = self._connect()
            try:
                df = con.execute(
                    "SELECT trade_date AS date, open, high, low, close, volume, "
                    "open_interest AS hold, adj_factor "
                    "FROM continuous_daily "
                    "WHERE symbol = ? AND series_type = 'main' "
                    "ORDER BY trade_date DESC LIMIT ?",
                    [qs, days],
                ).fetchdf()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[QUANTDATA] fetch_ohlcv 异常 [%s]: %s", symbol, e)
            self._record_failure(str(e))
            return None

        if df is None or df.empty:
            logger.debug("[QUANTDATA] 品种无数据 [%s] → %s", symbol, qs)
            return None

        self._record_success()
        df = df.sort_values("date").reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        for col in ("open", "high", "low", "close", "volume", "hold"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 非权威字段（GAP-158）：QuantData 无 amount/settle/pre_settle，置 NaN（float）
        # 保持与 aggregator._derive_pre_settle 兼容；oi_change 由 hold 一阶差分自算
        # （v3.0.0+1 去天勤 TQSDKEnhanceSource 后的权威派生，与旧增强层同口径：
        # diff().fillna(0.0)）
        df["oi_change"] = df["hold"].diff().fillna(0.0)
        for col in ("amount", "settle", "pre_settle"):
            df[col] = np.nan
        # vwap 典型价（amount 不可得时）
        df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0
        # 元数据
        df["symbol"] = symbol
        df["period"] = "daily"
        df["source"] = self.source_name
        df["fetched_at"] = datetime.now()
        df["trace_id"] = trace_id
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        return df

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """实时快照：QuantData 历史库不提供实时行情，返回最新日线 bar（读路径降级）。"""
        df = self.fetch_ohlcv(symbol, days=1, trace_id=trace_id)
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        return {
            "symbol": symbol,
            "last": float(row["close"]) if pd.notna(row["close"]) else None,
            "open_interest": float(row["hold"]) if pd.notna(row["hold"]) else None,
            "source": self.source_name,
        }

    # ─── 权威期限结构构建（R5，continuous_map + kline_daily）──

    def get_term_structure(self, symbol: str, days: int = 500) -> pd.DataFrame:
        """QuantData 权威期限结构：continuous_map 近远月映射 + kline_daily 对齐 close。

        Returns:
            DataFrame(date, term_spread, roll_yield, near_contract, far_contract)
            term_spread = (far_close − near_close) / near_close（主力为近月）
            roll_yield = term_spread（近似：月差年化留待合约代码解析，当前为近似值，文档标注）
            数据不足返回空 DataFrame（不伪造）。
        """
        qs = self._map_symbol(symbol)
        try:
            con = self._connect()
            try:
                map_df = con.execute(
                    "SELECT trade_date AS date, main_contract AS near_contract, "
                    "sub_contract AS far_contract "
                    "FROM continuous_map WHERE symbol = ? ORDER BY trade_date DESC LIMIT ?",
                    [qs, days],
                ).fetchdf()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[QUANTDATA] get_term_structure 映射读取异常 [%s]: %s", symbol, e)
            self._record_failure(str(e))
            return pd.DataFrame()

        if map_df is None or map_df.empty:
            return pd.DataFrame()

        contracts = sorted(
            set(map_df["near_contract"].tolist()) | set(map_df["far_contract"].tolist())
        )
        try:
            con = self._connect()
            try:
                # 合约 close 面板：date 列 × 合约 → close
                close_panel = con.execute(
                    "SELECT trade_date AS date, symbol, close FROM kline_daily "
                    "WHERE symbol IN (%s)" % ",".join("?" * len(contracts)),
                    contracts,
                ).fetchdf()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[QUANTDATA] get_term_structure 合约 close 读取异常 [%s]: %s", symbol, e)
            self._record_failure(str(e))
            return pd.DataFrame()

        if close_panel is None or close_panel.empty:
            return pd.DataFrame()

        close_wide = close_panel.pivot_table(
            index="date", columns="symbol", values="close", aggfunc="first"
        )
        result_rows: list[dict[str, Any]] = []
        for _, row in map_df.iterrows():
            d = row["date"]
            near, far = row["near_contract"], row["far_contract"]
            if near not in close_wide.columns or far not in close_wide.columns:
                continue
            nc, fc = close_wide.at[d, near], close_wide.at[d, far]
            if pd.isna(nc) or pd.isna(fc) or nc == 0:
                continue
            spread = (fc - nc) / nc
            result_rows.append(
                {
                    "date": pd.Timestamp(d).date(),
                    "term_spread": float(spread),
                    # 近似：未做月差年化（合约代码月份解析存在歧义），标注近似值
                    "roll_yield": float(spread),
                    "near_contract": near,
                    "far_contract": far,
                }
            )
        if not result_rows:
            return pd.DataFrame()
        out = pd.DataFrame(result_rows).sort_values("date").reset_index(drop=True)
        if len(out) > days:
            out = out.tail(days).reset_index(drop=True)
        return out

    # ─── 便捷别名（对齐 01-architecture 一致性元数据断言）──

    def get_ohlcv(self, symbol: str, days: int = 500) -> pd.DataFrame:
        """别名：fetch_ohlcv（返回 17 列 kline_cache schema，空数据返回空 DataFrame）。"""
        df = self.fetch_ohlcv(symbol, days=days)
        return df if df is not None else pd.DataFrame()


__all__ = [
    "QuantDataProvider",
    "QUANTDATA_DEFAULT_HOME",
    "L0_AUTHORITATIVE_FIELDS",
    "L0_STRUCTURE_FIELDS",
    "L1_FALLBACK_FIELDS",
    "L2_MISSING_FIELDS",
    "resolve_quantdata_home",
    "validate_field_availability",
    "symbol_to_quantdata",
]
