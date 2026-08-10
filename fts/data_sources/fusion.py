"""fts.data_sources.fusion — 多源 OHLCV 融合器（v2.3.0+ 14.3 新增）。

HARNESS §5.3 契约优先: 输出符合 `FusedOHLCV` 契约。

设计目标
--------
当多个数据源对同一 `symbol+date` 都有 K 线数据时，将多源 OHLCV
融合为一行"共识" OHLCV。支持的策略:

    - MEDIAN (默认): 每字段取中位数，抗异常值
    - MEAN: 算术平均，对偏离敏感
    - WEIGHTED: 按源权重加权平均
    - HIERARCHICAL: 优先级优先，与中位数分歧时降级到中位数
    - TRIMMED_MEAN: 去掉最高/最低后取均值（N≥3 时最稳健）

单源情况
--------
所有策略在 N=1 时退化为透传（直接返回该源的值），避免无意义计算。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from fts.core.contracts import FusedOHLCV
from fts.core.enums import FusionStrategy

logger = logging.getLogger(__name__)


# ─── 默认源权重 ────────────────────────────────────────────
# 用于 WEIGHTED 策略。可在构造 OHLCVFusion 时覆盖。
DEFAULT_SOURCE_WEIGHTS: dict[str, float] = {
    "TQ_LOCAL": 2.0,
    "TQ_PYTHON": 2.0,
    "WIND": 1.5,
    "IFIND": 1.0,
    "AKSHARE": 0.5,
    "DUCKDB_CACHE": 1.0,
    "SYNTHETIC": 0.0,  # 合成数据不参与加权
}


# 参与融合的 OHLCV 字段（HOLD/OI_CHANGE/PRE_SETTLE 不融合，是事件型字段）
FUSION_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "settle",
)


# ─── OHLCVFusion ───────────────────────────────────────────


class OHLCVFusion:
    """多源 OHLCV 融合器（v2.3.0+ 14.3 新增）。

    职责:
        - 对同一 `symbol+date` 的多源 OHLCV 行进行融合
        - 输出符合 `FusedOHLCV` 契约的结果
        - 不修改输入数据（纯函数式）

    不在范围:
        - 不做时序对齐（由调用方负责传入已对齐的 DataFrame）
        - 不做多源交叉验证告警（由 14.2 cross_check 负责）
        - 不做缓存写入（由 aggregator 负责）
    """

    def __init__(
        self,
        strategy: FusionStrategy = FusionStrategy.MEDIAN,
        source_weights: Optional[dict[str, float]] = None,
        outlier_threshold: float = 0.005,  # HIERARCHICAL 策略的异常判定阈值
    ):
        """
        Args:
            strategy: 融合策略（默认 MEDIAN）
            source_weights: 源权重（用于 WEIGHTED 策略；默认 DEFAULT_SOURCE_WEIGHTS）
            outlier_threshold: HIERARCHICAL 策略下，源值与中位数偏离超此阈值视为异常
        """
        self.strategy = strategy
        self.source_weights = source_weights or dict(DEFAULT_SOURCE_WEIGHTS)
        self.outlier_threshold = outlier_threshold

    def fuse_row(
        self,
        symbol: str,
        date: str,
        source_rows: dict[str, dict],
        trace_id: str = "",
    ) -> FusedOHLCV:
        """融合多源单行 OHLCV。

        Args:
            symbol: 品种代码
            date: ISO 日期
            source_rows: 源名 → 该行 dict（必含 FUSION_FIELDS 中的字段）
                例: {"TQ_LOCAL": {...}, "WIND": {...}}
            trace_id: 链路追踪 ID

        Returns:
            FusedOHLCV 字典

        Raises:
            ValueError: source_rows 为空
        """
        if not source_rows:
            raise ValueError("source_rows 不能为空")

        # 单源时所有策略退化为透传
        if len(source_rows) == 1:
            src_name, row = next(iter(source_rows.items()))
            return self._passthrough(symbol, date, src_name, row, trace_id)

        # 1) 收集每个字段的多源值
        field_values: dict[str, dict[str, float]] = {f: {} for f in FUSION_FIELDS}
        for src_name, row in source_rows.items():
            for field in FUSION_FIELDS:
                v = row.get(field)
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    try:
                        field_values[field][src_name] = float(v)
                    except (TypeError, ValueError):
                        continue

        # 2) 计算融合值 + 多源最大偏离
        fused: dict[str, float] = {}
        medians_per_field: dict[str, float] = {}
        for field, src_values in field_values.items():
            if not src_values:
                continue
            values = list(src_values.values())
            median = float(np.median(values))
            medians_per_field[field] = median
            fused[field] = self._fuse_field(field, src_values, median, source_rows)

        # 3) 计算最大相对偏离（用于 disagreement_pct）
        max_diff_pct = 0.0
        if medians_per_field:
            for field, median in medians_per_field.items():
                if median <= 0:
                    continue
                src_values = field_values[field]
                for v in src_values.values():
                    diff = abs(v - median) / median
                    if diff > max_diff_pct:
                        max_diff_pct = diff

        # 4) 收集非融合字段（hold/oi_change/pre_settle/vwap）—— 取首个非空源
        other_fields: dict[str, float] = {}
        for src_name, row in source_rows.items():
            for f in ("hold", "oi_change", "pre_settle", "vwap"):
                if f in other_fields:
                    continue
                v = row.get(f)
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    try:
                        other_fields[f] = float(v)
                    except (TypeError, ValueError):
                        pass

        # 5) 构造 FusedOHLCV
        sources_sorted = sorted(source_rows.keys())
        result: FusedOHLCV = {
            "symbol": symbol,
            "date": date,
            "open": fused.get("open", 0.0),
            "high": fused.get("high", 0.0),
            "low": fused.get("low", 0.0),
            "close": fused.get("close", 0.0),
            "volume": fused.get("volume", 0.0),
            "trace_id": trace_id,
            "contributing_sources": sources_sorted,
            "fusion_strategy": self.strategy.name,
            "source": sources_sorted[0],  # 主源 = 字典序第一个
        }
        if "amount" in fused:
            result["amount"] = fused["amount"]
        if "settle" in fused:
            result["settle"] = fused["settle"]
        for _f in ("hold", "oi_change", "pre_settle", "vwap"):
            if _f in other_fields:
                result[_f] = other_fields[_f]
        if max_diff_pct > 0:
            result["disagreement_pct"] = max_diff_pct
        return result

    def fuse_dataframe(
        self,
        symbol: str,
        source_dataframes: dict[str, pd.DataFrame],
        trace_id: str = "",
    ) -> pd.DataFrame:
        """对多源 DataFrame 按 date 对齐后逐行融合。

        Args:
            symbol: 品种代码
            source_dataframes: 源名 → DataFrame（含 date/open/high/low/close/volume 列）
            trace_id: 链路追踪 ID

        Returns:
            融合后的 DataFrame（按 date 升序）
        """
        if not source_dataframes:
            return pd.DataFrame()

        # 1) 收集所有 date
        all_dates: set[str] = set()
        for df in source_dataframes.values():
            if df is None or df.empty or "date" not in df.columns:
                continue
            all_dates.update(df["date"].astype(str).tolist())

        if not all_dates:
            return pd.DataFrame()

        # 2) 逐行融合
        rows: list[FusedOHLCV] = []
        for date_str in sorted(all_dates):
            source_rows: dict[str, dict] = {}
            for src_name, df in source_dataframes.items():
                if df is None or df.empty or "date" not in df.columns:
                    continue
                match = df[df["date"].astype(str) == date_str]
                if match.empty:
                    continue
                row = match.iloc[0].to_dict()
                # 移除 NaN 字段以便后续判断
                source_rows[src_name] = row
            if not source_rows:
                continue
            fused = self.fuse_row(symbol, date_str, source_rows, trace_id=trace_id)
            rows.append(fused)

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows)

    # ─── 内部辅助 ──

    def _fuse_field(
        self,
        field: str,
        src_values: dict[str, float],
        median: float,
        source_rows: dict[str, dict],
    ) -> float:
        """根据策略对单个字段进行融合。"""
        if self.strategy == FusionStrategy.MEDIAN:
            return median
        if self.strategy == FusionStrategy.MEAN:
            return float(np.mean(list(src_values.values())))
        if self.strategy == FusionStrategy.WEIGHTED:
            return self._weighted(src_values)
        if self.strategy == FusionStrategy.HIERARCHICAL:
            return self._hierarchical(src_values, median)
        if self.strategy == FusionStrategy.TRIMMED_MEAN:
            return self._trimmed_mean(list(src_values.values()))
        # 未知策略回退到 MEDIAN
        logger.warning("[fusion] 未知策略 %s，回退到 MEDIAN", self.strategy)
        return median

    def _weighted(self, src_values: dict[str, float]) -> float:
        """加权平均。源权重来自 `self.source_weights`；缺失权重视为 1.0。"""
        total_weight = 0.0
        total_value = 0.0
        for src, v in src_values.items():
            w = self.source_weights.get(src, 1.0)
            total_weight += w
            total_value += w * v
        if total_weight <= 0:
            return float(np.mean(list(src_values.values())))
        return total_value / total_weight

    @staticmethod
    def _default_weight_for(src: str) -> float:
        """查询默认源权重表（仅供未在 self.source_weights 覆盖时回退使用）。"""
        return DEFAULT_SOURCE_WEIGHTS.get(src, 1.0)

    def _hierarchical(
        self,
        src_values: dict[str, float],
        median: float,
    ) -> float:
        """优先级优先：取字典序最小源的值；若其与中位数偏离超阈值则用中位数。

        注: 当前实现以源名字典序作为"优先级"（无外部优先级表时）。
        实际生产中可由调用方在 source_rows 字典中按优先级顺序插入。
        """
        priority_src = min(src_values.keys())
        priority_value = src_values[priority_src]
        if median <= 0:
            return priority_value
        diff_pct = abs(priority_value - median) / median
        if diff_pct > self.outlier_threshold:
            logger.debug(
                "[fusion][HIERARCHICAL] 主源 %s 偏离中位数 %.4f > %.4f，降级到中位数",
                priority_src,
                diff_pct,
                self.outlier_threshold,
            )
            return median
        return priority_value

    @staticmethod
    def _trimmed_mean(values: list[float]) -> float:
        """去极值均值：去掉最高和最低后取均值。N<3 时退化为均值。"""
        if len(values) < 3:
            return float(np.mean(values))
        sorted_v = sorted(values)
        return float(np.mean(sorted_v[1:-1]))

    @staticmethod
    def _passthrough(
        symbol: str,
        date: str,
        src_name: str,
        row: dict,
        trace_id: str,
    ) -> FusedOHLCV:
        """单源透传。"""
        result: FusedOHLCV = {
            "symbol": symbol,
            "date": date,
            "open": float(row.get("open", 0.0) or 0.0),
            "high": float(row.get("high", 0.0) or 0.0),
            "low": float(row.get("low", 0.0) or 0.0),
            "close": float(row.get("close", 0.0) or 0.0),
            "volume": float(row.get("volume", 0.0) or 0.0),
            "trace_id": trace_id,
            "contributing_sources": [src_name],
            "fusion_strategy": "PASSTHROUGH",
            "source": src_name,
        }
        for f in ("amount", "hold", "settle", "pre_settle", "oi_change", "vwap"):
            v = row.get(f)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                try:
                    result[f] = float(v)
                except (TypeError, ValueError):
                    pass
        return result


__all__ = ["OHLCVFusion", "FUSION_FIELDS", "DEFAULT_SOURCE_WEIGHTS"]
