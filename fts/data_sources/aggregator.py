"""fts.data_sources.aggregator — 多源期货数据调度器（v2.3.0+ Phase 14.2）。

HARNESS §5.3 契约优先: 5 级 K 线主路径 + 字段增强层 + 熔断器 + 多源交叉验证。

K 线主路径（5 级降级）:
    1. DUCKDB_CACHE  — 命中本地缓存（最新交易日 = today/today-1）
    2. TQ_LOCAL      — 通达信本地 HTTP 127.0.0.1:7721
    3. TQ_PYTHON     — 通达信 TQ-Python SDK
    4. AKSHARE       — 兼容旧数据
    5. SYNTHETIC     — 合成降级（保证系统可运行）

字段增强层（独立并行，K 线主路径之后）:
    WIND   — 补充 settle / oi_change / 期权 IV
    IFIND  — 补充 EDB 宏观 / 产业链

熔断器:
    - 任一源连续 N 次失败 → 标记 UNAVAILABLE
    - 冷却时间过后 → 半开 → 探活 → 成功关闭 / 失败重开
    - 成功调用重置计数器

多源交叉验证（Phase 14.2）:
    - 同日期同合约多源 close 偏离中位数 > 阈值 → 告警
    - 告警写入 `data/data_source_disagreements.jsonl`（JSONL 格式）
    - 主路径尾部自动对最近 5 个交易日执行
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from fts.core.contracts import MultiSourceDisagreement
from fts.core.enums import DataSource
from fts.data_sources.base import BaseFuturesSource

logger = logging.getLogger(__name__)

# 默认告警日志路径（HARNESS §5.5 trace_id 全链路：JSONL 追加模式）
_DEFAULT_DISAGREEMENT_LOG = Path("data") / "data_source_disagreements.jsonl"


# ─── 熔断器状态 ──────────────────────────────────────────


@dataclass
class BreakerState:
    """单个数据源的熔断器状态。"""
    consecutive_failures: int = 0
    circuit_open: bool = False
    opened_at: float = 0.0
    last_error: str = ""
    total_success: int = 0
    total_failure: int = 0


# ─── Aggregator ──────────────────────────────────────────


class FuturesDataAggregator:
    """多源期货数据聚合器（v2.3.0）。

    K 线主路径: DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
    字段增强层: WIND + IFIND（K 线后并行）
    熔断器: 每源独立计数器 + 冷却时间
    """

    # 默认 K 线主路径（5 级降级）
    DEFAULT_KLINE_SOURCES: tuple[str, ...] = (
        DataSource.DUCKDB_CACHE.value,
        DataSource.TQ_LOCAL.value,
        DataSource.TQ_PYTHON.value,
        DataSource.AKSHARE.value,
        DataSource.SYNTHETIC.value,
    )

    def __init__(
        self,
        sources: list[BaseFuturesSource] | None = None,
        enhancers: list[BaseFuturesSource] | None = None,
        minute_sources: list[BaseFuturesSource] | None = None,  # v2.30.0: 分钟数据源
        tick_sources: list[BaseFuturesSource] | None = None,    # v2.31.0: tick 数据源
        db_path: Path | str | None = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 6 * 3600,
        cache_max_age_days: int = 1,
        enable_cross_check: bool = True,            # 14.2: 是否启用多源交叉验证
        cross_check_threshold: float = 0.005,       # 14.2: 价格偏离告警阈值（0.5%）
        cross_check_recent_days: int = 5,           # 14.2: 主路径触发时检查最近 N 天
        disagreement_log_path: Path | str | None = None,  # 14.2: 告警 JSONL 路径
    ):
        """
        Args:
            sources: K 线主路径数据源列表（按优先级）
            enhancers: 字段增强层数据源列表（并行）
            minute_sources: 分钟数据源列表（v2.30.0，按优先级：TDX → TQ-Local → TQSDK）
            tick_sources: tick 逐笔数据源列表（v2.31.0，按优先级）
            db_path: DuckDB 缓存路径（None 时禁用缓存）
            circuit_breaker_threshold: 连续失败次数阈值（默认 5）
            circuit_breaker_cooldown_seconds: 熔断冷却秒数（默认 6 小时）
            cache_max_age_days: 缓存最大新鲜度（默认 1 天）
            enable_cross_check: 是否启用多源交叉验证（14.2，默认 True）
            cross_check_threshold: 多源价格偏离告警阈值（14.2，默认 0.5%）
            cross_check_recent_days: 主路径自动交叉验证最近 N 天（14.2，默认 5）
            disagreement_log_path: 告警 JSONL 路径（14.2，默认 data/data_source_disagreements.jsonl）
        """
        self.sources = sources or []
        self.enhancers = enhancers or []
        self.minute_sources = minute_sources or []  # v2.30.0: 分钟数据源
        self.tick_sources = tick_sources or []      # v2.31.0: tick 数据源
        self.db_path = Path(db_path) if db_path else None
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_cooldown = circuit_breaker_cooldown_seconds
        self.cache_max_age_days = cache_max_age_days
        # 14.2 交叉验证配置
        self.enable_cross_check = enable_cross_check
        self.cross_check_threshold = cross_check_threshold
        self.cross_check_recent_days = cross_check_recent_days
        self.disagreement_log_path = (
            Path(disagreement_log_path) if disagreement_log_path
            else _DEFAULT_DISAGREEMENT_LOG
        )

        # 每源熔断器状态
        self._breakers: dict[str, BreakerState] = {}

    # ─── 主入口 ──

    def get_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取 K 线数据（5 级降级 + 字段增强）。

        Args:
            symbol: 品种代码（如 "RB0"）
            days: 回溯天数
            trace_id: 链路追踪 ID

        Returns:
            含 17 列 FTS schema 的 DataFrame（即使所有源失败也返回合成数据）
        """
        # 1) 尝试缓存
        df = self._try_cache(symbol, days)
        if df is not None and not df.empty:
            df = self._set_source(df, DataSource.DUCKDB_CACHE.value)
            df = self._enhance_fields(df, symbol, trace_id)
            # 14.2: 缓存命中后自动交叉验证最近 N 天
            self._maybe_cross_check(df, symbol, trace_id)
            return df

        # 2) 依次尝试每个 K 线源
        for source in self.sources:
            if self._is_circuit_open(source.source_name):
                logger.debug("[%s] 熔断器 OPEN，跳过", source.source_name)
                continue

            try:
                df = source.fetch_ohlcv(symbol, days, trace_id=trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] fetch_ohlcv 异常 [%s]: %s",
                               source.source_name, symbol, e)
                self._record_failure(source.source_name, str(e))
                continue

            if df is not None and not df.empty:
                self._record_success(source.source_name)
                # 统一 source 字段（避免不同源使用不一致的命名）
                df = self._set_source(df, source.source_name)
                # 截取最近的 days 行（兜底：源可能返回多于 days 的数据）
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)
                # 缓存到 DuckDB
                self._write_cache(df)
                df = self._enhance_fields(df, symbol, trace_id)
                # 14.2: K 线拉取成功 + 字段增强后自动交叉验证最近 N 天
                self._maybe_cross_check(df, symbol, trace_id)
                return df

            # 空数据不算成功也不算失败（不重置计数器也不递增）
            logger.debug("[%s] 返回空数据", source.source_name)

        # 3) 所有源都失败 → 合成数据（兜底也入缓存，避免下次重复合成）
        logger.warning("[%s] 所有 K 线源失败，使用合成数据", symbol)
        synth_df = self._synthesize(symbol, days, trace_id)
        self._write_cache(synth_df)
        return synth_df

    # ─── 分钟级 K 线（v2.30.0）───────────────────────────

    def get_minute_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        frequency: str = "5m",
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取分钟级 K 线数据（minute_cache 缓存 → 分钟级数据源降级）。

        分钟数据路径（按优先级）:
            1. minute_cache（DuckDB 缓存，命中且新鲜）
            2. TDXMinuteSource（通达信 HTTP 17709）
            3. TQLocalSource（通达信 HTTP 7721，带 period 参数）
            4. TQSDKSource（天勤 TQSDK，带 period 参数）

        Args:
            symbol: 品种代码（如 "RB0"）
            days: 回溯 K 线根数
            frequency: 分钟频率，支持 "1m" / "5m" / "15m" / "30m" / "60m"
            trace_id: 链路追踪 ID

        Returns:
            含分钟级 schema 的 DataFrame（11 列），所有源失败时返回空 DataFrame。
        """
        # 1) 尝试 minute_cache
        df = self._try_minute_cache(symbol, days, frequency)
        if df is not None and not df.empty:
            return df

        # 2) 依次尝试分钟数据源（按请求频率匹配源，不匹配时动态重建）
        for src in self.minute_sources:
            # 源支持动态周期时，按请求频率重建（TDX/TQ-Local/TQSDK 均支持）
            try:
                if getattr(src, "period", None) is not None and src.period != frequency:
                    src = type(src)(period=frequency)
            except Exception:  # noqa: BLE001
                logger.debug("[%s] 无法按频率 %s 重建源，跳过", src.source_name, frequency)
                continue

            source = src
            if self._is_circuit_open(source.source_name):
                logger.debug("[%s] 熔断器 OPEN，跳过分钟源", source.source_name)
                continue

            try:
                df = source.fetch_ohlcv(symbol, days, trace_id=trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] 分钟级 fetch_ohlcv 异常 [%s]: %s",
                               source.source_name, symbol, e)
                self._record_failure(source.source_name, str(e))
                continue

            if df is not None and not df.empty:
                self._record_success(source.source_name)
                # 截取最近 days 行
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)
                # 写入 minute_cache
                self._write_minute_cache(df, frequency)
                return df

            logger.debug("[%s] 分钟数据返回空", source.source_name)

        # 3) 所有分钟源失败 → 返回空 DataFrame（minute_cache schema）
        logger.warning("[%s] 所有分钟数据源失败，frequency=%s", symbol, frequency)
        return pd.DataFrame(
            columns=["symbol", "period", "datetime", "open", "high", "low",
                     "close", "volume", "source", "fetched_at", "trace_id"]
        )

    def _try_minute_cache(
        self,
        symbol: str,
        days: int,
        frequency: str,
    ) -> Optional[pd.DataFrame]:
        """尝试从 minute_cache 读取分钟数据。

        两步检查:
          1. 新鲜度: 缓存中最新的 datetime >= now - cache_max_age_days
          2. 大小: 返回足够的数据行（≥ days 的 80%）
        """
        if self.db_path is None or not self.db_path.exists():
            return None

        con = self._get_cache_conn()
        if con is None:
            return None

        try:
            # 品种匹配兼容
            sym_variants = [symbol, f"{symbol}.SHFE", f"{symbol}.DCE",
                            f"{symbol}.CZCE", f"{symbol}.CFFEX"]
            placeholders = ",".join(["?"] * len(sym_variants))

            # 新鲜度检查
            cutoff = (pd.Timestamp.now() - pd.Timedelta(days=self.cache_max_age_days))
            latest = con.execute(
                f"""
                SELECT MAX(datetime) FROM minute_cache
                WHERE symbol IN ({placeholders}) AND period = ?
                """,
                [*sym_variants, frequency],
            ).fetchone()
            if latest is None or latest[0] is None:
                return None
            if pd.Timestamp(latest[0]) < cutoff:
                logger.debug("[minute_cache] 过期: symbol=%s, latest=%s, cutoff=%s",
                             symbol, latest[0], cutoff)
                return None

            # 返回最近 days 行
            df = con.execute(
                f"""
                SELECT symbol, period, datetime, open, high, low, close,
                       volume, source, fetched_at, trace_id
                FROM minute_cache
                WHERE symbol IN ({placeholders}) AND period = ?
                ORDER BY datetime DESC
                LIMIT ?
                """,
                [*sym_variants, frequency, days],
            ).df()
            if df.empty:
                return None
            # 统一 symbol + 按时间升序
            df["symbol"] = symbol
            df = df.sort_values("datetime").reset_index(drop=True)
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning("[minute_cache] 读取失败: %s", e)
            return None

    def _write_minute_cache(self, df: pd.DataFrame, frequency: str) -> None:
        """将分钟数据写入 minute_cache。失败不抛异常。"""
        if self.db_path is None or df.empty:
            return
        # 确保 schema 已迁移
        try:
            from fts.data_sources.migrate import migrate_schema
            migrate_schema(self.db_path)
        except Exception as e:
            logger.warning("[minute_cache] migrate_schema 失败: %s", e)

        con = self._get_cache_conn()
        if con is None:
            return
        try:
            con.register("df_new", df)
            con.execute(
                """
                INSERT INTO minute_cache (
                    symbol, period, datetime, open, high, low, close,
                    volume, source, fetched_at, trace_id
                )
                SELECT
                    symbol, period, datetime, open, high, low, close,
                    volume, source, fetched_at, trace_id
                FROM df_new
                """
            )
            con.unregister("df_new")
        except Exception as e:  # noqa: BLE001
            logger.warning("[minute_cache] 写入失败: %s", e)

    # ─── tick 逐笔数据路径（v2.31.0）──

    def get_ticks(
        self,
        symbol: str,
        count: int = 5000,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取 tick 逐笔数据（tick_cache → tick_sources 降级）。

        Args:
            symbol: 品种代码（如 "RB0"）
            count: tick 行数
            trace_id: 链路追踪 ID

        Returns:
            含 tick schema 的 DataFrame，所有源失败时返回空 DataFrame。
        """
        # 1) 尝试 tick_cache
        df = self._try_tick_cache(symbol, count)
        if df is not None and not df.empty:
            return df

        # 2) 依次尝试 tick 数据源
        for source in self.tick_sources:
            if self._is_circuit_open(source.source_name):
                logger.debug("[%s] 熔断器 OPEN，跳过 tick 源", source.source_name)
                continue

            try:
                df = source.fetch_ticks(symbol, count=count, trace_id=trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] tick fetch 异常 [%s]: %s",
                               source.source_name, symbol, e)
                self._record_failure(source.source_name, str(e))
                continue

            if df is not None and not df.empty:
                self._record_success(source.source_name)
                if len(df) > count:
                    df = df.tail(count).reset_index(drop=True)
                self._write_tick_cache(df)
                return df

            logger.debug("[%s] tick 数据返回空", source.source_name)

        # 3) 所有 tick 源失败 → 返回空 DataFrame
        logger.warning("[%s] 所有 tick 数据源失败", symbol)
        return pd.DataFrame()

    def _try_tick_cache(
        self,
        symbol: str,
        count: int,
    ) -> Optional[pd.DataFrame]:
        """尝试从 tick_cache 读取 tick 数据（按最新日期读取最近 count 行）。"""
        if self.db_path is None or not self.db_path.exists():
            return None

        con = self._get_cache_conn()
        if con is None:
            return None

        try:
            # 表不存在（未迁移）时静默返回 None，避免警告噪音
            exists = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'tick_cache'"
            ).fetchone()[0]
            if not exists:
                return None

            df = con.execute(
                """
                SELECT symbol, datetime, last_price, average, highest, lowest,
                       volume, amount, open_interest,
                       bid_price1, bid_volume1, ask_price1, ask_volume1,
                       bid_price2, bid_volume2, ask_price2, ask_volume2,
                       bid_price3, bid_volume3, ask_price3, ask_volume3,
                       bid_price4, bid_volume4, ask_price4, ask_volume4,
                       bid_price5, bid_volume5, ask_price5, ask_volume5,
                       source, fetched_at, trace_id
                FROM tick_cache
                WHERE symbol = ?
                ORDER BY datetime DESC
                LIMIT ?
                """,
                [symbol, count],
            ).df()
            if df.empty:
                return None
            df["symbol"] = symbol
            df = df.sort_values("datetime").reset_index(drop=True)
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning("[tick_cache] 读取失败: %s", e)
            return None

    def _write_tick_cache(self, df: pd.DataFrame) -> None:
        """将 tick 数据写入 tick_cache。失败不抛异常。"""
        if self.db_path is None or df.empty:
            return
        try:
            from fts.data_sources.migrate import migrate_schema
            migrate_schema(self.db_path)
        except Exception as e:
            logger.warning("[tick_cache] migrate_schema 失败: %s", e)

        con = self._get_cache_conn()
        if con is None:
            return
        try:
            con.register("df_new", df)
            con.execute(
                """
                INSERT INTO tick_cache (
                    symbol, datetime, last_price, average, highest, lowest,
                    volume, amount, open_interest,
                    bid_price1, bid_volume1, ask_price1, ask_volume1,
                    bid_price2, bid_volume2, ask_price2, ask_volume2,
                    bid_price3, bid_volume3, ask_price3, ask_volume3,
                    bid_price4, bid_volume4, ask_price4, ask_volume4,
                    bid_price5, bid_volume5, ask_price5, ask_volume5,
                    source, fetched_at, trace_id
                )
                SELECT
                    symbol, datetime, last_price, average, highest, lowest,
                    volume, amount, open_interest,
                    bid_price1, bid_volume1, ask_price1, ask_volume1,
                    bid_price2, bid_volume2, ask_price2, ask_volume2,
                    bid_price3, bid_volume3, ask_price3, ask_volume3,
                    bid_price4, bid_volume4, ask_price4, ask_volume4,
                    bid_price5, bid_volume5, ask_price5, ask_volume5,
                    source, fetched_at, trace_id
                FROM df_new
                """
            )
            con.unregister("df_new")
        except Exception as e:  # noqa: BLE001
            logger.warning("[tick_cache] 写入失败: %s", e)

    # ─── 字段增强层 ──

    def _enhance_fields(
        self,
        df: pd.DataFrame,
        symbol: str,
        trace_id: str,
    ) -> pd.DataFrame:
        """用 Wind/iFinD 字段增强层补充 settle/oi_change 等。

        注: 字段增强层不修改 K 线主路径的行数；仅尝试补充缺失列。
        失败不抛异常（K 线主路径已成功，增强是锦上添花）。
        """
        for enhancer in self.enhancers:
            try:
                if self._is_circuit_open(enhancer.source_name):
                    continue
                # 字段增强层不直接接管 K 线，仅尝试补充字段
                # 当前实现: 调用 fetch_ohlcv 拿到 df，从中提取 settle/oi_change
                # 更精细的实现可在子类中覆盖 enhance() 方法
                enrich_df = enhancer.fetch_ohlcv_or_none(
                    symbol, days=len(df), trace_id=trace_id
                )
                if enrich_df is not None and not enrich_df.empty:
                    self._record_success(enhancer.source_name)
                    # 简单实现: 用 enrich_df 的 settle/oi_change/pre_settle 覆盖主路径的对应列
                    if "settle" in enrich_df.columns and "settle" in df.columns:
                        df["settle"] = enrich_df["settle"].values[:len(df)]
                    if "pre_settle" in enrich_df.columns and "pre_settle" in df.columns:
                        df["pre_settle"] = enrich_df["pre_settle"].values[:len(df)]
                    if "oi_change" in enrich_df.columns and "oi_change" in df.columns:
                        df["oi_change"] = enrich_df["oi_change"].values[:len(df)]
                    if "hold" in enrich_df.columns and "hold" in df.columns:
                        df["hold"] = enrich_df["hold"].values[:len(df)]
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] 字段增强异常 [%s]: %s",
                               enhancer.source_name, symbol, e)
                self._record_failure(enhancer.source_name, str(e))
        return df

    # ─── DuckDB 缓存（单连接模式，避免 read_only 冲突）──

    def _get_cache_conn(self):
        """获取或创建持久化 DuckDB 连接。"""
        if self.db_path is None:
            return None
        if not hasattr(self, "_cache_conn") or self._cache_conn is None:
            try:
                import duckdb
                self._cache_conn = duckdb.connect(str(self.db_path))
            except Exception as e:
                logger.warning("[cache] DuckDB 连接失败: %s", e)
                return None
        return self._cache_conn

    def _try_cache(
        self,
        symbol: str,
        days: int,
    ) -> Optional[pd.DataFrame]:
        """尝试从 DuckDB 缓存读取 K 线。

        两步检查:
          1. 新鲜度检查: 缓存中最新的日期 >= today - cache_max_age_days
          2. 数据返回: 跳过日期过滤，只按 LIMIT days 返回最近数据
        """
        if self.db_path is None or not self.db_path.exists():
            return None

        con = self._get_cache_conn()
        if con is None:
            return None

        try:
            # 品种匹配兼容：缓存中可能是 RB0.SHFE 也可能是 RB0
            sym_variants = [symbol, f"{symbol}.SHFE", f"{symbol}.DCE",
                            f"{symbol}.CZCE", f"{symbol}.CFFEX"]
            placeholders = ",".join(["?"] * len(sym_variants))

            # 1) 新鲜度检查：缓存中最新的日期 >= today - cache_max_age_days
            cutoff = (pd.Timestamp.now() - pd.Timedelta(days=self.cache_max_age_days)).date()
            latest = con.execute(
                f"""
                SELECT MAX(CAST(date AS DATE)) FROM kline_cache
                WHERE symbol IN ({placeholders})
                """,
                [*sym_variants],
            ).fetchone()
            if latest is None or latest[0] is None:
                return None  # 缓存中无数据，走上游源
            if latest[0] < cutoff:
                logger.debug(
                    "[cache] 缓存过期: symbol=%s, latest=%s, cutoff=%s",
                    symbol, latest[0], cutoff,
                )
                return None  # 缓存过期，走上游源

            # 2) 返回最近 days 行数据（不限制日期范围）
            df = con.execute(
                f"""
                SELECT * FROM kline_cache
                WHERE symbol IN ({placeholders})
                ORDER BY date DESC
                LIMIT ?
                """,
                [*sym_variants, days],
            ).df()
            if df.empty:
                return None
            # 统一 symbol 字段
            df["symbol"] = symbol
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning("[cache] 读取失败: %s", e)
            return None

    def _write_cache(self, df: pd.DataFrame) -> None:
        """将 K 线写入 DuckDB 缓存。失败不抛异常（缓存是次要路径）。"""
        if self.db_path is None or df.empty:
            return
        # 确保 schema 已迁移
        try:
            from fts.data_sources.migrate import migrate_schema
            migrate_schema(self.db_path)
        except Exception as e:
            logger.warning("[cache] migrate_schema 失败: %s", e)

        con = self._get_cache_conn()
        if con is None:
            return
        try:
            con.register("df_new", df)
            # 显式列 + CAST(date AS VARCHAR)：双 schema 兼容
            # - 新 schema（date DATE）：CAST 转为 'YYYY-MM-DD' 字符串
            # - 老 schema（date VARCHAR）：CAST 透传，零成本
            con.execute(
                """
                INSERT INTO kline_cache (
                    symbol, period, date, open, high, low, close,
                    volume, amount, hold, settle, pre_settle, oi_change, vwap,
                    source, fetched_at, trace_id
                )
                SELECT
                    symbol, period, CAST(date AS VARCHAR) AS date,
                    open, high, low, close,
                    volume, amount, hold, settle, pre_settle, oi_change, vwap,
                    source, fetched_at, trace_id
                FROM df_new
                """
            )
            con.unregister("df_new")
        except Exception as e:  # noqa: BLE001
            logger.warning("[cache] 写入失败: %s", e)

    # ─── 合成数据（最后兜底）──

    def _synthesize(
        self,
        symbol: str,
        days: int,
        trace_id: str,
    ) -> pd.DataFrame:
        """生成合成 K 线数据（保证系统可运行）。"""
        from datetime import date, timedelta

        np.random.seed(hash(symbol) % 2**31)
        end_date = pd.Timestamp.now().normalize()
        dates = [end_date - timedelta(days=i) for i in range(days)][::-1]

        base_price = 3500.0
        closes = base_price + np.cumsum(np.random.randn(days) * 10)
        opens = closes + np.random.randn(days) * 5
        highs = np.maximum(opens, closes) + abs(np.random.randn(days) * 8)
        lows = np.minimum(opens, closes) - abs(np.random.randn(days) * 8)

        df = pd.DataFrame({
            "symbol": symbol, "period": "daily",
            "date": [d.date() for d in dates],
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": np.random.randint(50000, 200000, days),
            "amount": np.random.randint(1e8, 5e8, days),
            "hold": np.random.randint(50000, 100000, days),
            "settle": closes, "pre_settle": np.roll(closes, 1),
            "oi_change": np.random.randint(-2000, 2000, days),
            "vwap": closes,
            "source": DataSource.SYNTHETIC.value,
            "fetched_at": pd.Timestamp.now(),
            "trace_id": trace_id,
        })
        df["pre_settle"] = df["pre_settle"].fillna(closes[0])
        return df

    # ─── 熔断器 ──

    def _is_circuit_open(self, source_name: str) -> bool:
        """检查熔断器是否开启（开启时跳过该源）。"""
        state = self._breakers.get(source_name)
        if state is None or not state.circuit_open:
            return False

        # 冷却时间到 → 半开（允许一次探活）
        if (time.time() - state.opened_at) >= self.circuit_breaker_cooldown:
            logger.info("[%s] 熔断冷却到期，尝试半开探活", source_name)
            return False
        return True

    def _record_success(self, source_name: str) -> None:
        """记录成功（重置失败计数器，关闭熔断器）。"""
        state = self._breakers.setdefault(source_name, BreakerState())
        state.consecutive_failures = 0
        state.circuit_open = False
        state.last_error = ""
        state.total_success += 1

    def _record_failure(self, source_name: str, error: str = "") -> None:
        """记录失败（递增计数器，达到阈值开启熔断）。"""
        state = self._breakers.setdefault(source_name, BreakerState())
        state.consecutive_failures += 1
        state.last_error = error
        state.total_failure += 1
        if state.consecutive_failures >= self.circuit_breaker_threshold:
            state.circuit_open = True
            state.opened_at = time.time()
            logger.warning(
                "[%s] 连续失败 %d 次，熔断器开启（冷却 %.0f 秒）",
                source_name, state.consecutive_failures, self.circuit_breaker_cooldown,
            )

    # ─── 监控 / 状态报告 ──

    def get_source_status(self) -> dict[str, dict[str, Any]]:
        """返回所有源的状态报告（用于监控 / 测试）。"""
        return {
            name: {
                "consecutive_failures": s.consecutive_failures,
                "circuit_open": s.circuit_open,
                "total_success": s.total_success,
                "total_failure": s.total_failure,
                "last_error": s.last_error,
            }
            for name, s in self._breakers.items()
        }

    # ─── 内部辅助 ──

    @staticmethod
    def _set_source(df: pd.DataFrame, source: str) -> pd.DataFrame:
        """统一设置 source 字段。"""
        if "source" in df.columns:
            df["source"] = source
        return df

    # ─── 多源交叉验证（Phase 14.2）───────────────────────

    def cross_check(
        self,
        symbol: str,
        date: str,
        sources: Optional[list[BaseFuturesSource]] = None,
        trace_id: str = "",
    ) -> list[MultiSourceDisagreement]:
        """多源交叉验证：同日期同合约多源 close 对比，差异超阈值返回告警。

        Args:
            symbol: 品种代码（如 "RB0"）
            date: ISO 日期 "2026-08-04"
            sources: 参与交叉验证的源列表（默认 = K 线源 + 字段增强层）
            trace_id: 链路追踪 ID（HARNESS §5.5）

        Returns:
            MultiSourceDisagreement 列表。outliers 非空时同时追加一行到
            `disagreement_log_path`（JSONL 格式，追加模式）。
        """
        if not self.enable_cross_check:
            return []

        # 默认源 = 字段增强层（K 线主路径只调一个源，不构成"多源"）
        # 若显式传 sources 则按传入
        if sources is not None:
            check_sources = list(sources)
        else:
            check_sources = list(self.enhancers)
            if not check_sources:
                # 字段增强层为空时回退到 K 线源（至少让用户能验证）
                check_sources = list(self.sources)
        if len(check_sources) < 2:
            # 至少需要 2 个源才有"多源"概念
            logger.debug("[cross_check] %s @ %s 源数量 < 2，跳过", symbol, date)
            return []

        # 1) 收集每个源的 close（异常被吞）
        prices: dict[str, float] = {}
        for src in check_sources:
            try:
                if self._is_circuit_open(src.source_name):
                    continue
                df = src.fetch_ohlcv_or_none(symbol, days=5, trace_id=trace_id)
                if df is None or df.empty or "close" not in df.columns:
                    continue
                # 找匹配 date 的行
                match = df[df["date"].astype(str) == date]
                if match.empty:
                    continue
                price = float(match["close"].iloc[0])
                if price > 0:
                    prices[src.source_name] = price
            except Exception as e:  # noqa: BLE001
                logger.warning("[cross_check] 源 %s 异常: %s", src.source_name, e)
                # 单源失败不影响其他源（不计入熔断，cross_check 是辅助功能）

        if len(prices) < 2:
            return []

        # 2) 计算中位数（参考价）
        values = list(prices.values())
        median = float(np.median(values))

        # 3) 找出偏离 > 阈值的源
        outliers: list[str] = []
        max_diff = 0.0
        for src_name, price in prices.items():
            if median <= 0:
                continue
            diff_pct = abs(price - median) / median
            if diff_pct > max_diff:
                max_diff = diff_pct
            if diff_pct > self.cross_check_threshold:
                outliers.append(src_name)

        if not outliers:
            return []

        # 4) 构造告警记录（符合 MultiSourceDisagreement 契约）
        record = MultiSourceDisagreement(
            symbol=symbol,
            date=date,
            prices=prices,
            median=median,
            outliers=outliers,
            max_diff_pct=max_diff,
            threshold=self.cross_check_threshold,
            trace_id=trace_id,
            detected_at=datetime.now().isoformat(timespec="seconds"),
        )

        # 5) 写入 JSONL 日志（追加模式）
        self._write_disagreement_log(record)

        logger.warning(
            "[cross_check] %s @ %s 多源分歧: %d 个 outlier, max_diff=%.4f",
            symbol, date, len(outliers), max_diff,
        )
        return [record]

    def _write_disagreement_log(self, record: MultiSourceDisagreement) -> None:
        """追加一行 JSONL 到告警日志（不抛异常）。"""
        try:
            self.disagreement_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.disagreement_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning("[disagreement_log] 写入失败: %s", e)

    def _maybe_cross_check(
        self,
        df: pd.DataFrame,
        symbol: str,
        trace_id: str,
    ) -> list[MultiSourceDisagreement]:
        """主路径尾部触发：对最近 N 个交易日自动交叉验证。

        触发条件：
        1. enable_cross_check=True
        2. enhancers 数量 ≥ 2（cross_check 需要多源对比）
        3. df 非空 + 含 date/close 列

        避免 K 线主路径源被重复调用而触发熔断计数器。

        Returns:
            本次触发产生的告警列表（供调用方计入血缘 disagreements 字段）。
        """
        if not self.enable_cross_check or df is None or df.empty:
            return []
        if len(self.enhancers) < 2:
            # 字段增强层不足 2 个源时不触发（无"多源"概念）
            return []
        if "date" not in df.columns or "close" not in df.columns:
            return []

        # 取最近 N 天（按 date 倒序）
        try:
            dates = sorted(
                df["date"].astype(str).unique().tolist(),
                reverse=True,
            )[: self.cross_check_recent_days]
        except Exception:
            return []

        all_alerts: list[MultiSourceDisagreement] = []
        for date_str in dates:
            alerts = self.cross_check(symbol, date_str, trace_id=trace_id)
            all_alerts.extend(alerts)
        return all_alerts

    # ─── 资源清理 ──

    def close(self) -> None:
        """关闭持久连接。"""
        conn = getattr(self, "_cache_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._cache_conn = None


__all__ = ["FuturesDataAggregator", "BreakerState"]
