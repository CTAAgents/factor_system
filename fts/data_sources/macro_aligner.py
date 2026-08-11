"""fts.data_sources.macro_aligner — 宏观字段增强层（v2.32.0）。

将 EDB 宏观/行业指标（月度频率）对齐到 K 线交易日并注入为 DataFrame 列，
供宏观因子（fut_macro_export / fut_macro_us_bond 等）通过
`data.get('export')` 读取真实宏观数据。

关键设计:
  - 发布滞后防未来函数: lag_days 将报告期数据点后移，避免用未来数据回测。
  - 缺数据降级: 某指标拉取失败 → 不注入列，因子走 close 代理（不阻断主路径）。
  - 默认数据源: EastmoneyMacroSource（东财 CPI/进出口 + 中债登/美债收益率，
    2026-08-11 起替代 iFinD EDB——iFinD MCP 需 API Key 实测不可用；
    显式传 source 可切回 IFindSource）。

HARNESS §5.3 契约优先: 依赖 source.get_macro_series() 与 pd.DataFrame。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# 因子字段名 → EDB 指标查询词（源 get_macro_series 自然语言）
MACRO_FIELD_QUERIES: dict[str, str] = {
    "export": "中国出口金额当月值",
    "import_data": "中国进口金额当月值",
    "cpi": "中国CPI当月同比",
    "rate": "中国1年期国债收益率",
    "us_bond": "美国10年期国债收益率",
}


class MacroFieldAligner:
    """宏观字段增强层：批量拉取 + 时序对齐 + 注入。

    Args:
        source: 宏观数据源（提供 get_macro_series()）。None 时默认
            EastmoneyMacroSource（东财+中债登，无需 API Key）。
        lag_days: 发布滞后天数（防未来函数，默认 0）。
        db_path: edb_cache DuckDB 路径（None = 默认 data/fts_history.duckdb）。
    """

    def __init__(
        self,
        source: Any | None = None,
        lag_days: int = 0,
        db_path: Any | None = None,
    ) -> None:
        self._source = source
        self.lag_days = lag_days
        self.db_path = db_path

    # ─── 单序列对齐 ──

    @staticmethod
    def align(
        df: pd.DataFrame,
        macro: Optional[pd.Series],
        field: str,
        lag_days: int = 0,
    ) -> pd.DataFrame:
        """将宏观月度序列对齐到 K 线 index 并注入为列（返回副本）。

        Args:
            df: OHLCV DataFrame（DatetimeIndex）
            macro: DatetimeIndex Series（date → value），None/空 → 不注入
            field: 注入列名（如 "export"）
            lag_days: 发布滞后天数（数据点 t 在 t+lag_days 才可用）

        Returns:
            注入 macro 列后的 df 副本（macro 缺失时返回原 df 副本）。
        """
        df = df.copy()
        if macro is None or macro.empty:
            logger.debug("[macro] 无数据，跳过注入 [%s]", field)
            return df

        macro = macro.sort_index()
        # 发布滞后: 报告期 t 的数据在 t+lag_days 才公开 → 可用的最早交易日
        usable = macro.index + pd.Timedelta(days=lag_days)
        macro_lagged = pd.Series(macro.values, index=usable).sort_index()

        # 按 K 线交易日前向填充（取最近已发布值），早于首个数据点为 NaN
        aligned = macro_lagged.reindex(df.index, method="ffill")
        df[field] = aligned.values
        return df

    # ─── 批量注入 ──

    def inject(
        self,
        df: pd.DataFrame,
        fields: list[str] | None = None,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """批量注入宏观字段。某字段失败 → 保留缺失列，不阻断主路径。

        Args:
            df: OHLCV DataFrame（DatetimeIndex）
            fields: 要注入的因子字段名列表（默认 MACRO_FIELD_QUERIES 全部）
            trace_id: 链路追踪 ID

        Returns:
            注入宏观列后的 df 副本。
        """
        df = df.copy()
        if self._source is None:
            from fts.data_sources.macro_eastmoney_source import EastmoneyMacroSource

            self._source = EastmoneyMacroSource()

        for field in fields or list(MACRO_FIELD_QUERIES):
            indicator = MACRO_FIELD_QUERIES.get(field, field)
            try:
                macro = self._source.get_macro_series(
                    indicator,
                    db_path=self.db_path,
                    trace_id=trace_id,
                )
                df = self.align(df, macro, field, lag_days=self.lag_days)
            except Exception as e:  # noqa: BLE001
                logger.warning("[macro] 注入失败 [%s]: %s，因子将走代理", field, e)
        return df


def inject_macro_fields(
    df: pd.DataFrame,
    aligner: MacroFieldAligner,
    fields: list[str] | None = None,
    trace_id: str = "",
) -> pd.DataFrame:
    """便捷入口：批量注入宏观字段（等价 aligner.inject）。"""
    return aligner.inject(df, fields=fields, trace_id=trace_id)


__all__ = [
    "MACRO_FIELD_QUERIES",
    "MacroFieldAligner",
    "inject_macro_fields",
]
