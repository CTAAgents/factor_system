"""fts.data_sources.ifind_sdk_source — 同花顺 iFinD 官方 SDK 字段增强源（GAP-083 阶段 C 方案 A）。

背景:
    天勤 TQSDK 不提供结算价 → pre_settle（昨结算）与 settle 的实时权威来源缺失。
    本源通过 **iFinD 官方 Python SDK（iFinDPy）** 进程内直连，补充:
      - settle   ← iFinD settle（结算价）
      - pre_settle ← iFinD preSettle（昨结算）【方案 A 核心目标】
      - hold     ← iFinD openInterest（持仓量）
      - oi_change ← iFinD openInterestChg（持仓变动）
    与 TQSDKEnhanceSource（补 hold/oi_change）并存，多源增强：
    `_enhance_fields` 依 enhancers 顺序逐源有效值覆盖（后者优先）。

认证（.env，二选一）:
    - IFIND_TOKEN=<51ifind API Token>（推荐，ths.token 模式）
    - IFIND_USERNAME=<同花顺账号> + IFIND_PASSWORD=<密码>（ths.login 模式）

SDK:
    iFinDPy 为 iFinD 官方 SDK，不在公开 PyPI，需从 quantapi.51ifind.com
    获取安装包本地安装。本模块懒加载（import 放方法内），未装 SDK 自动降级。

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法（fetch_ohlcv / fetch_quote / is_available）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource

logger = logging.getLogger(__name__)

# iFinD 期货日线字段（接口字段标识 → FTS 字段）
_FIELD_MAP: dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "settle": "settle",
    "preSettle": "pre_settle",
    "openInterest": "hold",
    "openInterestChg": "oi_change",
}


class IFindSDKSource(BaseFuturesSource):
    """同花顺 iFinD 官方 SDK 字段增强源。

    不参与 K 线主路径；仅作为 aggregator 的 enhancer，
    经 `_enhance_fields` 有效值覆盖主路径的 settle/pre_settle/hold/oi_change。
    """

    source_name: str = "IFIND_SDK"

    def is_available(self) -> bool:
        """探活：iFinDPy 已安装且 .env 配置了凭据（token 或账号密码）。"""
        try:
            import iFinDPy  # noqa: F401
        except ImportError:
            return False
        return bool(
            os.environ.get("IFIND_TOKEN")
            or (
                os.environ.get("IFIND_USERNAME") and os.environ.get("IFIND_PASSWORD")
            )
        )

    def _resolve_symbol(self, symbol: str) -> Optional[str]:
        """FTS 品种代码 → iFinD 期货代码（RB0/RB → RB.SHF，RB2609 → RB2609.SHF）。

        复用 ifind_source._infer_exchange 交易所后缀推断；主连（带 0 后缀）剥离 0。
        未知品种返回 None（_infer_exchange 抛 ValueError）。
        """
        from fts.data_sources.ifind_source import _infer_exchange

        sym = symbol.strip().upper()
        base = sym[:-1] if sym.endswith("0") else sym
        try:
            return f"{base}.{_infer_exchange(base)}"
        except ValueError:
            return None

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """拉取 iFinD 期货日线，返回含 settle/pre_settle/hold/oi_change 的 DataFrame。

        Args:
            symbol: FTS 品种代码（如 "RB0"）
            days: 回溯交易日数量
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None（未装 SDK/无凭据/登录失败/接口异常均降级）
        """
        try:
            import iFinDPy as ths  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("[%s] iFinDPy 未安装，跳过字段增强", self.source_name)
            return None

        code = self._resolve_symbol(symbol)
        if code is None:
            logger.debug("[%s] %s 无 iFinD 交易所映射，跳过", self.source_name, symbol)
            return None

        token = os.environ.get("IFIND_TOKEN")
        user = os.environ.get("IFIND_USERNAME")
        pwd = os.environ.get("IFIND_PASSWORD")
        if not token and not (user and pwd):
            logger.debug("[%s] 未配置 IFIND_TOKEN 或 IFIND_USERNAME/PASSWORD，跳过", self.source_name)
            return None

        logged_in = False
        try:
            if token:
                err = ths.token(token)
            else:
                err = ths.login(user, pwd)  # type: ignore[arg-type]
            logged_in = True
            if err not in (0, None):  # 返回 0 或错误码/None 表示成功
                logger.warning("[%s] iFinD 登录失败 [%s] err=%s", self.source_name, symbol, err)
                return None

            end = datetime.now().date()
            start = end - timedelta(days=int(days * 1.6) + 10)
            fields = ",".join(_FIELD_MAP.keys())
            raw = ths.thsi.futures_get(
                code=code,
                indi_para=fields,
                start_date=str(start),
                end_date=str(end),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] iFinD 取数异常 [%s]: %s", self.source_name, symbol, e)
            return None
        finally:
            if logged_in:
                try:
                    ths.logout()
                except Exception:  # noqa: BLE001
                    pass

        df = self._parse_futures_df(raw, symbol, trace_id=trace_id)
        if df is None or df.empty:
            return None
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        return df

    def _parse_futures_df(
        self,
        raw: Any,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """将 iFinD futures_get 原始返回解析为增强层 DataFrame（可独立测试）。

        期望输入:
            DataFrame（含 date 或 datetime 索引 + 字段列）或
            {"data": [{"date":..., "open":..., ..., "preSettle":...}, ...]}
        """
        if raw is None:
            return None
        if isinstance(raw, pd.DataFrame):
            df = raw.copy()
            if df.empty:
                return None
        elif isinstance(raw, dict):
            rows = raw.get("data")
            if not isinstance(rows, list) or not rows:
                return None
            df = pd.DataFrame(rows)
        else:
            return None

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "date"})
        else:
            return None

        out = pd.DataFrame(index=df.index)
        out["date"] = df["date"].dt.date
        for ifield, ffield in _FIELD_MAP.items():
            if ifield in df.columns:
                out[ffield] = pd.to_numeric(df[ifield], errors="coerce")
        # 无效值清理：价格/持仓类 ≤0 或 NaN → NaN（_enhance_fields 仅覆盖有效值）
        for col in ("open", "high", "low", "close", "volume", "settle", "pre_settle", "hold"):
            if col in out.columns:
                out.loc[out[col] <= 0, col] = float("nan") if col != "volume" else 0.0

        out["symbol"] = symbol
        out["period"] = "daily"
        out["source"] = self.source_name
        out["fetched_at"] = pd.Timestamp.now()
        out["trace_id"] = trace_id
        out.reset_index(drop=True, inplace=True)
        return out

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """实时快照：本源不支持，返回 None（增强层只补日频字段）。"""
        return None


__all__ = ["IFindSDKSource"]
