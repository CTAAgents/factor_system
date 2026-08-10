"""fts.data_sources.base — 期货数据源抽象基类（v2.3.0）。

本模块定义适配器契约：
  - BaseFuturesSource: 3 个抽象方法（fetch_ohlcv / fetch_quote / is_available）
  - fetch_ohlcv_or_none: 默认包装，异常返回 None（聚合器友好）
  - SourceUnavailable: 数据源不可用异常（向上传播供熔断判定）
  - validate_ohlcv_row: OHLCV 行字段校验

K 线主路径: TDX_LOCAL → TQ_PYTHON → AKSHARE（不含 Wind/iFinD）
字段增强层: WIND / IFIND（独立并行）

HARNESS §5.3 契约优先: 适配器必须继承本类并实现 3 个抽象方法。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─── 异常 ──────────────────────────────────────────────────


class SourceUnavailable(Exception):
    """数据源不可用（探活失败、鉴权失败、限流等）。

    注: SourceUnavailable 必须向上传播，供聚合器判定熔断。
    其他异常被 fetch_ohlcv_or_none 吞掉返回 None。
    """

    def __init__(self, source: str, reason: str):
        self.source = source
        self.reason = reason
        super().__init__(f"[{source}] 数据源不可用: {reason}")


# ─── 抽象基类 ──────────────────────────────────────────────


class BaseFuturesSource(ABC):
    """期货数据源抽象基类（v2.3.0 多源集成）。

    子类必须实现:
        - source_name: str 标识（如 "TDX_LOCAL"）
        - is_available() → bool: 探活
        - fetch_ohlcv(symbol, days, trace_id) → Optional[DataFrame]: 拉 K 线
        - fetch_quote(symbol, trace_id) → Optional[dict]: 拉快照

    子类可继承 fetch_ohlcv_or_none 默认包装（异常返回 None）。
    """

    source_name: str = ""  # 子类必须覆盖

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        days: int,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """获取 OHLCV K 线数据。返回 None 表示失败（不抛异常）。"""
        ...

    @abstractmethod
    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """获取实时快照。返回 None 表示失败。"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """探活：当前数据源是否可用（端口存活 / 鉴权有效 / 未超限）。"""
        ...

    # ─── 默认包装方法（子类可直接继承） ──

    def fetch_ohlcv_or_none(
        self,
        symbol: str,
        days: int,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """包装 fetch_ohlcv：SourceUnavailable 向上传播，其他异常返回 None。

        聚合器通过捕获 SourceUnavailable 决定是否熔断该源；
        其他异常（如临时网络抖动）走优雅降级。
        """
        try:
            return self.fetch_ohlcv(symbol, days, trace_id)
        except SourceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] fetch_ohlcv 异常 [%s]: %s", self.source_name, symbol, e)
            return None

    # ─── 字段校验器（静态方法，子类可直接调用） ──

    @staticmethod
    def validate_ohlcv_row(row: dict) -> tuple[bool, str]:
        """校验单行 OHLCV 数据的合法性。

        校验规则:
          - 必填 7 字段: symbol / date / open / high / low / close / volume
          - 价格 open/high/low/close 必须 > 0
          - volume 必须 >= 0
          - date 必须 YYYY-MM-DD 格式

        Returns:
            (is_valid, error_message). 当 is_valid=True 时 error_message 为空。
        """
        # 必填字段检查
        required = ("symbol", "date", "open", "high", "low", "close", "volume")
        for field in required:
            if field not in row or row[field] is None:
                return False, f"缺少必填字段: {field}"

        # 价格必须 > 0
        for price_field in ("open", "high", "low", "close"):
            v = row[price_field]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                return False, f"{price_field} 必须 > 0，实际 {v}"

        # volume 必须 >= 0
        v = row["volume"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            return False, f"volume 必须 >= 0，实际 {v}"

        # 日期必须 YYYY-MM-DD
        try:
            datetime.strptime(str(row["date"]), "%Y-%m-%d")
        except (ValueError, TypeError):
            return False, f"date 必须 YYYY-MM-DD 格式，实际 {row['date']}"

        return True, ""
