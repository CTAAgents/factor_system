"""tests.data_sources.test_fusion — OHLCVFusion 单元测试。

HARNESS §5.4: 测试随重构。覆盖:
    - 5 种策略（MEDIAN / MEAN / WEIGHTED / HIERARCHICAL / TRIMMED_MEAN）
    - 边界：单源、空源、字段缺失、NaN
    - 多源 DataFrame 对齐融合
    - PASSTHROUGH 行为
    - 默认权重与自定义权重
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.core.contracts import FusedOHLCV
from fts.core.enums import FusionStrategy
from fts.data_sources.fusion import DEFAULT_SOURCE_WEIGHTS, OHLCVFusion


# ─── 辅助：构造多源单行数据 ──────────────────────────────


def _row(close: float, **kwargs) -> dict:
    """构造一行 OHLCV 数据。close 必填，其他字段默认 close ±1。"""
    return {
        "open": kwargs.get("open", close - 1.0),
        "high": kwargs.get("high", close + 1.0),
        "low": kwargs.get("low", close - 1.0),
        "close": close,
        "volume": kwargs.get("volume", 100000),
        "amount": kwargs.get("amount", 350000000.0),
        "settle": kwargs.get("settle", close),
        "hold": kwargs.get("hold", 80000),
        "oi_change": kwargs.get("oi_change", 2000),
        "pre_settle": kwargs.get("pre_settle", close - 5),
        "vwap": kwargs.get("vwap", close),
    }


# ─── 基础：单源透传 ────────────────────────────────────


class TestPassthrough:
    def test_single_source_passthrough(self):
        """单源时所有策略退化为透传。"""
        for strategy in FusionStrategy:
            fuser = OHLCVFusion(strategy=strategy)
            row = _row(close=3500.0)
            result = fuser.fuse_row("RB0", "2026-08-04", {"TQ_LOCAL": row}, trace_id="t-1")
            assert result["contributing_sources"] == ["TQ_LOCAL"]
            assert result["fusion_strategy"] == "PASSTHROUGH"
            assert result["close"] == 3500.0
            assert result["source"] == "TQ_LOCAL"
            assert result["symbol"] == "RB0"
            assert result["date"] == "2026-08-04"
            assert result["trace_id"] == "t-1"

    def test_empty_source_raises(self):
        """source_rows 为空时抛 ValueError。"""
        fuser = OHLCVFusion()
        with pytest.raises(ValueError, match="不能为空"):
            fuser.fuse_row("RB0", "2026-08-04", {})


# ─── MEDIAN 策略 ────────────────────────────────────────


class TestMedianStrategy:
    def test_median_two_sources(self):
        """2 源 MEDIAN: 取两个值的平均（中位数 = (a+b)/2）。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),
            "WIND": _row(close=3510.0),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources, trace_id="t-med-2")
        assert result["contributing_sources"] == ["TQ_LOCAL", "WIND"]
        assert result["fusion_strategy"] == "MEDIAN"
        assert result["close"] == 3505.0  # (3500+3510)/2

    def test_median_three_sources_outlier(self):
        """3 源 MEDIAN: 中位数 = 第二个值，抗异常值。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),
            "WIND": _row(close=3502.0),
            "IFIND": _row(close=4000.0),  # 异常值
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources, trace_id="t-med-3")
        assert result["close"] == 3502.0  # 中位数
        # disagreement_pct = max(|3502-3502|, |3500-3502|, |4000-3502|) / 3502
        # = max(0, 0.00057, 0.142) = 0.142
        assert result["disagreement_pct"] > 0.1
        assert result["disagreement_pct"] < 0.15


# ─── MEAN 策略 ──────────────────────────────────────────


class TestMeanStrategy:
    def test_mean_two_sources(self):
        fuser = OHLCVFusion(strategy=FusionStrategy.MEAN)
        sources = {"TQ_LOCAL": _row(close=3500.0), "WIND": _row(close=3510.0)}
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        assert result["close"] == 3505.0
        assert result["fusion_strategy"] == "MEAN"

    def test_mean_three_sources(self):
        fuser = OHLCVFusion(strategy=FusionStrategy.MEAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),
            "WIND": _row(close=3504.0),
            "IFIND": _row(close=3506.0),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        assert abs(result["close"] - 3503.333333) < 0.01


# ─── WEIGHTED 策略 ──────────────────────────────────────


class TestWeightedStrategy:
    def test_weighted_default_tq_dominates(self):
        """默认权重 TQ=2 > WIND=1.5，close 应更接近 TQ。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.WEIGHTED)
        sources = {"TQ_LOCAL": _row(close=3500.0), "WIND": _row(close=3510.0)}
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 加权 = (2*3500 + 1.5*3510) / 3.5 = (7000+5265)/3.5 = 3504.29
        assert abs(result["close"] - 3504.29) < 0.1
        assert result["fusion_strategy"] == "WEIGHTED"

    def test_weighted_custom_weights(self):
        """自定义权重可覆盖默认。"""
        fuser = OHLCVFusion(
            strategy=FusionStrategy.WEIGHTED,
            source_weights={"TQ_LOCAL": 0.0, "WIND": 1.0},
        )
        sources = {"TQ_LOCAL": _row(close=3500.0), "WIND": _row(close=3510.0)}
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # TQ 权重 0 → 实际只用 WIND
        assert result["close"] == 3510.0

    def test_default_source_weights_present(self):
        """默认权重表应包含 TQ/WIND/IFIND/AKSHARE/SYNTHETIC。"""
        assert "TQ_LOCAL" in DEFAULT_SOURCE_WEIGHTS
        assert "WIND" in DEFAULT_SOURCE_WEIGHTS
        assert "IFIND" in DEFAULT_SOURCE_WEIGHTS
        assert "AKSHARE" in DEFAULT_SOURCE_WEIGHTS
        assert "SYNTHETIC" in DEFAULT_SOURCE_WEIGHTS
        assert DEFAULT_SOURCE_WEIGHTS["SYNTHETIC"] == 0.0


# ─── HIERARCHICAL 策略 ──────────────────────────────────


class TestHierarchicalStrategy:
    def test_hierarchical_primary_kept_when_aligned(self):
        """主源（字典序最小）与中位数一致时 → 保留主源。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.HIERARCHICAL)
        sources = {
            "AKSHARE": _row(close=3500.0),  # 字典序最小 → 主源
            "WIND": _row(close=3500.5),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 主源 = 3500，中位数 ≈ 3500.25
        # |3500-3500.25|/3500.25 ≈ 0.00071 < 0.005 → 保留主源
        assert result["close"] == 3500.0
        assert result["fusion_strategy"] == "HIERARCHICAL"

    def test_hierarchical_fallback_to_median_on_outlier(self):
        """主源与中位数偏离超阈值 → 降级到中位数。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.HIERARCHICAL)
        sources = {
            "AKSHARE": _row(close=3000.0),  # 字典序最小 → 主源（但异常）
            "WIND": _row(close=3500.0),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 主源 = 3000，中位数 = 3250
        # |3000-3250|/3250 = 0.077 > 0.005 → 降级到中位数 3250
        assert result["close"] == 3250.0

    def test_hierarchical_custom_outlier_threshold(self):
        """自定义 outlier_threshold 改变降级行为。"""
        fuser = OHLCVFusion(
            strategy=FusionStrategy.HIERARCHICAL,
            outlier_threshold=0.10,  # 10% 才视为异常
        )
        sources = {
            "AKSHARE": _row(close=3480.0),
            "WIND": _row(close=3500.0),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 主源 = 3480，中位数 = 3490
        # |3480-3490|/3490 ≈ 0.00286 < 0.10 → 保留主源
        assert result["close"] == 3480.0


# ─── TRIMMED_MEAN 策略 ──────────────────────────────────


class TestTrimmedMeanStrategy:
    def test_trimmed_mean_three_sources(self):
        """3 源: 去掉最高/最低后取均值。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.TRIMMED_MEAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),
            "WIND": _row(close=3502.0),
            "IFIND": _row(close=4000.0),  # 异常
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 排序 [3500, 3502, 4000] → 去首尾 → [3502] → 均值 3502
        assert result["close"] == 3502.0

    def test_trimmed_mean_two_sources_falls_back_to_mean(self):
        """2 源 < 3: 退化为均值。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.TRIMMED_MEAN)
        sources = {"TQ_LOCAL": _row(close=3500.0), "WIND": _row(close=3510.0)}
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        assert result["close"] == 3505.0

    def test_trimmed_mean_five_sources(self):
        """5 源: 去掉首尾各一个。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.TRIMMED_MEAN)
        sources = {
            "S1": _row(close=3500.0),
            "S2": _row(close=3501.0),
            "S3": _row(close=3502.0),
            "S4": _row(close=3503.0),
            "S5": _row(close=4000.0),  # 异常
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # 排序 [3500, 3501, 3502, 3503, 4000] → 去首尾 → [3501, 3502, 3503] → 均值 3502
        assert result["close"] == 3502.0


# ─── 字段处理 ────────────────────────────────────────────


class TestFieldHandling:
    def test_missing_field_skipped(self):
        """某源缺字段时，不影响其他源。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),  # 完整
            "WIND": {"close": 3510.0, "volume": 100000},  # 缺 open/high/low
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # close 中位数 = 3505
        assert result["close"] == 3505.0
        # open/high/low 各源只有一个非 None → 中位数 = 该值
        # TQ: open=3499, high=3501, low=3499
        assert result["open"] == 3499.0
        assert result["high"] == 3501.0
        assert result["low"] == 3499.0

    def test_nan_field_treated_as_missing(self):
        """NaN 字段被跳过。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0),
            "WIND": {**_row(close=3510.0), "amount": float("nan")},
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # amount 只有 TQ 一个非 NaN → 应透传 350000000.0
        assert result.get("amount") == 350000000.0

    def test_non_fusion_fields_preserved(self):
        """非融合字段 (hold/oi_change/pre_settle/vwap) 取首个非空源。"""
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        sources = {
            "TQ_LOCAL": _row(close=3500.0, hold=80000, oi_change=2000),
            "WIND": _row(close=3510.0, hold=85000, oi_change=2500),
        }
        result = fuser.fuse_row("RB0", "2026-08-04", sources)
        # hold/oi_change 不融合 → 取字典序最小源（TQ）的值
        assert result["hold"] == 80000
        assert result["oi_change"] == 2000


# ─── DataFrame 融合 ──────────────────────────────────────


class TestDataFrameFusion:
    def test_fuse_dataframe_aligns_by_date(self):
        """多源 DataFrame 按 date 对齐融合。"""
        dates = ["2026-08-01", "2026-08-02", "2026-08-03"]
        df_tq = pd.DataFrame([
            {"date": d, "open": 3500.0, "high": 3510.0, "low": 3490.0,
             "close": 3500.0 + i, "volume": 100000}
            for i, d in enumerate(dates)
        ])
        df_wind = pd.DataFrame([
            {"date": d, "open": 3501.0, "high": 3511.0, "low": 3491.0,
             "close": 3501.0 + i, "volume": 100000}
            for i, d in enumerate(dates)
        ])
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        result = fuser.fuse_dataframe(
            "RB0", {"TQ_LOCAL": df_tq, "WIND": df_wind}, trace_id="t-df"
        )
        assert len(result) == 3
        assert result.iloc[0]["close"] == 3500.5  # (3500+3501)/2
        assert result.iloc[1]["close"] == 3501.5
        assert result.iloc[2]["close"] == 3502.5
        assert all(result["contributing_sources"].apply(lambda x: set(x) == {"TQ_LOCAL", "WIND"}))

    def test_fuse_dataframe_partial_coverage(self):
        """部分日期只有 1 个源有数据 → 透传。"""
        df_tq = pd.DataFrame([
            {"date": "2026-08-01", "open": 3500.0, "high": 3510.0, "low": 3490.0,
             "close": 3500.0, "volume": 100000},
            {"date": "2026-08-02", "open": 3510.0, "high": 3520.0, "low": 3500.0,
             "close": 3510.0, "volume": 100000},
        ])
        df_wind = pd.DataFrame([
            {"date": "2026-08-02", "open": 3511.0, "high": 3521.0, "low": 3501.0,
             "close": 3511.0, "volume": 100000},
        ])
        fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
        result = fuser.fuse_dataframe("RB0", {"TQ_LOCAL": df_tq, "WIND": df_wind})
        assert len(result) == 2
        # 08-01 只有 TQ → 透传 3500
        assert result.iloc[0]["close"] == 3500.0
        assert result.iloc[0]["fusion_strategy"] == "PASSTHROUGH"
        # 08-02 两源都有 → 中位数
        assert result.iloc[1]["close"] == 3510.5
        assert result.iloc[1]["fusion_strategy"] == "MEDIAN"

    def test_fuse_dataframe_empty(self):
        """空输入返回空 DataFrame。"""
        fuser = OHLCVFusion()
        assert fuser.fuse_dataframe("RB0", {}).empty
        assert fuser.fuse_dataframe("RB0", {"TQ_LOCAL": pd.DataFrame()}).empty


# ─── 契约合规 ────────────────────────────────────────────


class TestContractCompliance:
    def test_result_is_fused_ohlcv(self):
        """返回结果符合 FusedOHLCV 契约（必填字段齐全）。"""
        fuser = OHLCVFusion()
        sources = {"TQ_LOCAL": _row(close=3500.0), "WIND": _row(close=3510.0)}
        result = fuser.fuse_row("RB0", "2026-08-04", sources, trace_id="t-cc")
        required = {
            "symbol", "date", "open", "high", "low", "close", "volume", "trace_id",
            "contributing_sources", "fusion_strategy",
        }
        assert required.issubset(result.keys())
        assert isinstance(result["contributing_sources"], list)
        assert isinstance(result["fusion_strategy"], str)

    def test_default_strategy_is_median(self):
        """默认策略 = MEDIAN。"""
        fuser = OHLCVFusion()
        assert fuser.strategy == FusionStrategy.MEDIAN
