#!/usr/bin/env python3
"""scripts/verify_fusion.py — Phase 14.3 多源融合端到端验证。

HARNESS §5.4: 测试随重构。覆盖 5 个端到端场景:
    1. 单源透传（PASSTHROUGH）
    2. MEDIAN 抗异常值（3 源中 1 个偏离 1.7%）
    3. WEIGHTED 默认权重（TQ 主导）
    4. HIERARCHICAL 优先级优先 + 异常降级
    5. TRIMMED_MEAN 去极值均值

通过标准: 5/5 场景 ✅
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# ── 让脚本可以独立运行 ─────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.core.enums import FusionStrategy  # noqa: E402
from fts.data_sources.fusion import OHLCVFusion  # noqa: E402


# ─── 辅助：构造源 DataFrame ──────────────────────────────


def _make_kline_df(
    closes: list[float],
    base_date: date,
    source: str,
    open_off: float = 0.0,
    high_off: float = 1.0,
    low_off: float = -1.0,
    volume: int = 100000,
) -> pd.DataFrame:
    """构造一个简单的 K 线 DataFrame。"""
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "symbol": "RB0",
                "date": base_date + timedelta(days=i),
                "open": c + open_off,
                "high": c + high_off,
                "low": c + low_off,
                "close": c,
                "volume": volume,
                "amount": c * volume * 1.0,
                "settle": c,
                "source": source,
            }
        )
    return pd.DataFrame(rows)


# ─── 场景 ─────────────────────────────────────────────────


def scenario_1_passthrough() -> bool:
    """场景 1: 单源 → 透传。"""
    print("\n[1/5] 场景 1: 单源透传 (PASSTHROUGH)")
    base = date(2026, 8, 4)
    df = _make_kline_df([3500.0, 3510.0], base, "TQ_LOCAL")
    fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
    result = fuser.fuse_dataframe("RB0", {"TQ_LOCAL": df}, trace_id="v-1")
    if len(result) != 2:
        print(f"       ❌ 期望 2 行，实际 {len(result)}")
        return False
    if result.iloc[0]["fusion_strategy"] != "PASSTHROUGH":
        print(f"       ❌ 期望 PASSTHROUGH，实际 {result.iloc[0]['fusion_strategy']}")
        return False
    if result.iloc[0]["close"] != 3500.0:
        print(f"       ❌ 期望 close=3500.0，实际 {result.iloc[0]['close']}")
        return False
    if result.iloc[0]["contributing_sources"] != ["TQ_LOCAL"]:
        print(f"       ❌ 期望 sources=[TQ_LOCAL]，实际 {result.iloc[0]['contributing_sources']}")
        return False
    print("       ✅ 单源透传 2 行，strategy=PASSTHROUGH，close=3500.0")
    return True


def scenario_2_median_robust() -> bool:
    """场景 2: 3 源 MEDIAN 抗异常值。"""
    print("\n[2/5] 场景 2: MEDIAN 抗异常值（3 源中 1 个偏离 1.7%）")
    base = date(2026, 8, 4)
    df_tq = _make_kline_df([3500.0], base, "TQ_LOCAL")
    df_wind = _make_kline_df([3500.5], base, "WIND")
    # IFIND 偏离 1.7%（60 / 3500 ≈ 1.71%）
    df_ifind = _make_kline_df([3560.0], base, "IFIND")
    fuser = OHLCVFusion(strategy=FusionStrategy.MEDIAN)
    result = fuser.fuse_dataframe(
        "RB0",
        {"TQ_LOCAL": df_tq, "WIND": df_wind, "IFIND": df_ifind},
        trace_id="v-2",
    )
    if len(result) != 1:
        print(f"       ❌ 期望 1 行，实际 {len(result)}")
        return False
    fused_close = result.iloc[0]["close"]
    # 中位数 = 3500.5
    if abs(fused_close - 3500.5) > 0.01:
        print(f"       ❌ 期望 close=3500.5（中位数），实际 {fused_close}")
        return False
    # disagreement_pct 应反映 IFIND 的偏离 ≈ 0.017
    if result.iloc[0]["disagreement_pct"] < 0.01:
        print(f"       ❌ 期望 disagreement_pct > 0.01，实际 {result.iloc[0]['disagreement_pct']:.4f}")
        return False
    print(f"       ✅ 中位数 close={fused_close}，disagreement={result.iloc[0]['disagreement_pct']:.4f}")
    return True


def scenario_3_weighted_default() -> bool:
    """场景 3: WEIGHTED 默认权重（TQ=2 > WIND=1.5）。"""
    print("\n[3/5] 场景 3: WEIGHTED 默认权重（TQ 主导）")
    base = date(2026, 8, 4)
    df_tq = _make_kline_df([3500.0], base, "TQ_LOCAL")
    df_wind = _make_kline_df([3510.0], base, "WIND")
    fuser = OHLCVFusion(strategy=FusionStrategy.WEIGHTED)
    result = fuser.fuse_dataframe("RB0", {"TQ_LOCAL": df_tq, "WIND": df_wind}, trace_id="v-3")
    if len(result) != 1:
        print(f"       ❌ 期望 1 行，实际 {len(result)}")
        return False
    fused_close = result.iloc[0]["close"]
    # 加权 = (2*3500 + 1.5*3510) / 3.5 = (7000+5265)/3.5 = 3504.29
    if abs(fused_close - 3504.29) > 0.1:
        print(f"       ❌ 期望 close≈3504.29，实际 {fused_close}")
        return False
    if result.iloc[0]["fusion_strategy"] != "WEIGHTED":
        print(f"       ❌ 期望 strategy=WEIGHTED，实际 {result.iloc[0]['fusion_strategy']}")
        return False
    print(f"       ✅ 加权 close={fused_close:.2f}（TQ 主导）")
    return True


def scenario_4_hierarchical() -> bool:
    """场景 4: HIERARCHICAL 优先级优先 + 异常降级。"""
    print("\n[4/5] 场景 4: HIERARCHICAL 优先级优先 + 异常降级")
    base = date(2026, 8, 4)
    # 主源（字典序最小）= AKSHARE，close=3500
    # WIND close=3500.5（中位数 ≈ 3500.25，主源偏离 0.07% < 0.5% → 保留主源）
    df_akshare = _make_kline_df([3500.0], base, "AKSHARE")
    df_wind = _make_kline_df([3500.5], base, "WIND")
    fuser = OHLCVFusion(strategy=FusionStrategy.HIERARCHICAL)
    result_aligned = fuser.fuse_dataframe("RB0", {"AKSHARE": df_akshare, "WIND": df_wind}, trace_id="v-4a")
    if abs(result_aligned.iloc[0]["close"] - 3500.0) > 0.01:
        print(f"       ❌ 对齐场景: 期望 close=3500.0，实际 {result_aligned.iloc[0]['close']}")
        return False
    print("       ✅ 对齐场景: 保留主源 close=3500.0（偏离 < 阈值）")

    # 主源异常场景: AKSHARE close=3000，WIND close=3500
    df_akshare_bad = _make_kline_df([3000.0], base, "AKSHARE")
    df_wind_good = _make_kline_df([3500.0], base, "WIND")
    result_outlier = fuser.fuse_dataframe("RB0", {"AKSHARE": df_akshare_bad, "WIND": df_wind_good}, trace_id="v-4b")
    fused_close = result_outlier.iloc[0]["close"]
    # 中位数 = 3250，主源 3000 偏离 7.7% > 0.5% → 降级到中位数
    if abs(fused_close - 3250.0) > 0.01:
        print(f"       ❌ 异常场景: 期望 close=3250.0（中位数），实际 {fused_close}")
        return False
    print("       ✅ 异常场景: 降级到中位数 close=3250.0（主源偏离 > 阈值）")
    return True


def scenario_5_trimmed_mean() -> bool:
    """场景 5: TRIMMED_MEAN 去极值均值（N≥3）。"""
    print("\n[5/5] 场景 5: TRIMMED_MEAN 去极值均值")
    base = date(2026, 8, 4)
    # 4 源: 3500, 3501, 3503, 4000（异常）→ 去首尾 → [3501, 3503] → 均值 3502
    df_tq = _make_kline_df([3500.0], base, "TQ_LOCAL")
    df_wind = _make_kline_df([3501.0], base, "WIND")
    df_ifind = _make_kline_df([3503.0], base, "IFIND")
    df_akshare = _make_kline_df([4000.0], base, "AKSHARE")
    fuser = OHLCVFusion(strategy=FusionStrategy.TRIMMED_MEAN)
    result = fuser.fuse_dataframe(
        "RB0",
        {
            "TQ_LOCAL": df_tq,
            "WIND": df_wind,
            "IFIND": df_ifind,
            "AKSHARE": df_akshare,
        },
        trace_id="v-5",
    )
    if len(result) != 1:
        print(f"       ❌ 期望 1 行，实际 {len(result)}")
        return False
    fused_close = result.iloc[0]["close"]
    if abs(fused_close - 3502.0) > 0.01:
        print(f"       ❌ 期望 close=3502.0（去极值均值），实际 {fused_close}")
        return False
    print("       ✅ 去极值均值 close=3502.0（去掉 3500 与 4000）")
    return True


# ─── 主入口 ──────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("  Phase 14.3 端到端验证: 多源数据融合 (OHLCVFusion)")
    print("=" * 60)

    scenarios = [
        scenario_1_passthrough,
        scenario_2_median_robust,
        scenario_3_weighted_default,
        scenario_4_hierarchical,
        scenario_5_trimmed_mean,
    ]
    passed = sum(1 for s in scenarios if s())
    print("\n" + "=" * 60)
    print(f"  端到端验证结果: {passed}/{len(scenarios)} 场景通过")
    print("=" * 60)
    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(main())
