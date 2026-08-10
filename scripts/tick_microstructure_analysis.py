"""
tick 盘口微观结构特征分析（v2.31.0 Phase 5）

分析维度:
  1. 买卖价差（Spread） — 绝对/相对价差分布与时序
  2. 盘口深度（Depth）  — 五档总深度、深度不平衡 OBI
  3. 冲击成本（Impact） — Amihud 非流动性 / 有效价差 / Kyle's Lambda
  4. 价差-深度联动      — 价差与深度的相关性

数据源: TQSDK tick（FuturesDataProvider.get_tick_data，含 5 档盘口）

HARNESS §5.3 契约优先: 使用已实现的 tick 数据源接口。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from fts.data_futures import FuturesDataProvider

logger = logging.getLogger(__name__)

SYMBOL = "RB0"
COUNT = 5000  # TQSDK 免费账号 tick 上限
OUTPUT_DIR = _PROJECT_ROOT / "reports" / datetime.now().strftime("%Y-%m-%d")


# ─── 分析函数 ─────────────────────────────────────────────


def _mid_price(df: pd.DataFrame) -> pd.Series:
    """中点价 mid = (bid1 + ask1) / 2。"""
    return (df["bid_price1"] + df["ask_price1"]) / 2


def analyze_spread(df: pd.DataFrame) -> dict[str, Any]:
    """买卖价差分析。

    - 绝对价差 = ask1 - bid1（最小变动价位）
    - 相对价差 = 绝对价差 / mid
    """
    if df.empty or "bid_price1" not in df.columns or "ask_price1" not in df.columns:
        return {}
    mid = _mid_price(df)
    abs_spread = df["ask_price1"] - df["bid_price1"]
    rel_spread = abs_spread / mid.replace(0, np.nan)

    return {
        "n_ticks": int(len(df)),
        "abs_spread_mean": float(abs_spread.mean()),
        "abs_spread_median": float(abs_spread.median()),
        "abs_spread_std": float(abs_spread.std()),
        "abs_spread_min": float(abs_spread.min()),
        "abs_spread_max": float(abs_spread.max()),
        "rel_spread_mean_bps": float(rel_spread.mean() * 1e4),
        "rel_spread_median_bps": float(rel_spread.median() * 1e4),
        "abs_spread_pct_1tick": float((abs_spread == 1).mean() * 100),  # 1 个最小变动价位占比
        "abs_spread_pct_2tick": float((abs_spread == 2).mean() * 100),
    }


def analyze_depth(df: pd.DataFrame) -> dict[str, Any]:
    """盘口深度分析（五档）。

    - 买深 = sum(bid_vol1..5)，卖深 = sum(ask_vol1..5)
    - 深度不平衡 OBI = (买深 - 卖深) / (买深 + 卖深)
    """
    if df.empty:
        return {}

    bid_vols = [f"bid_volume{i}" for i in range(1, 6)]
    ask_vols = [f"ask_volume{i}" for i in range(1, 6)]
    if bid_vols[0] not in df.columns:
        return {}

    bid_depth = df[bid_vols].sum(axis=1)
    ask_depth = df[ask_vols].sum(axis=1)
    total = bid_depth + ask_depth
    obi = (bid_depth - ask_depth) / total.replace(0, np.nan)

    return {
        "bid_depth_mean": float(bid_depth.mean()),
        "ask_depth_mean": float(ask_depth.mean()),
        "total_depth_mean": float(total.mean()),
        "obi_mean": float(obi.mean()),
        "obi_std": float(obi.std()),
        "obi_pct_positive": float((obi > 0).mean() * 100),  # 买方占优占比
        "bid_vol1_ratio": float((df["bid_volume1"] / total.replace(0, np.nan)).mean() * 100),
        "ask_vol1_ratio": float((df["ask_volume1"] / total.replace(0, np.nan)).mean() * 100),
    }


def analyze_impact(df: pd.DataFrame) -> dict[str, Any]:
    """冲击成本分析。

    - Amihud 非流动性 = |return| / amount（每元成交的价格冲击）
    - 有效价差 = 2 * |last_price - mid|
    - Kyle's Lambda = Δlast_price / signed_volume（回归斜率）
    """
    if df.empty or "last_price" not in df.columns:
        return {}

    mid = _mid_price(df)

    # 收益率与 Amihud
    ret = df["last_price"].pct_change().abs()
    amihud = ret / df["amount"].replace(0, np.nan)
    amihud = amihud.replace([np.inf, -np.inf], np.nan)

    # 有效价差
    eff_spread = (2 * (df["last_price"] - mid).abs()).replace([np.inf, -np.inf], np.nan)

    # Kyle's Lambda: 回归 Δlast_price ~ signed_volume
    dp = df["last_price"].diff()
    signed_vol = df["volume"].diff() * np.sign(dp.fillna(0))
    valid = pd.DataFrame({"dp": dp, "sv": signed_vol}).replace([np.inf, -np.inf], np.nan).dropna()
    kyle_lambda = 0.0
    if len(valid) > 10 and valid["sv"].std() > 0:
        kyle_lambda = float(np.cov(valid["dp"], valid["sv"])[0, 1] / valid["sv"].var())

    return {
        "amihud_mean": float(amihud.mean()) if amihud.notna().any() else 0.0,
        "amihud_median": float(amihud.median()) if amihud.notna().any() else 0.0,
        "eff_spread_mean": float(eff_spread.mean()) if eff_spread.notna().any() else 0.0,
        "eff_spread_bps": float(eff_spread.mean() / mid.mean() * 1e4) if mid.mean() else 0.0,
        "kyle_lambda": kyle_lambda,
        "avg_volume_per_tick": float(df["volume"].diff().abs().mean()) if len(df) > 1 else 0.0,
        "avg_amount_per_tick": float(df["amount"].diff().abs().mean()) if len(df) > 1 else 0.0,
    }


def analyze_spread_depth_relation(df: pd.DataFrame) -> dict[str, Any]:
    """价差-深度联动：价差与盘口深度的相关性。"""
    if df.empty or "bid_price1" not in df.columns:
        return {}

    abs_spread = df["ask_price1"] - df["bid_price1"]
    total_depth = df[[f"bid_volume{i}" for i in range(1, 6)]].sum(axis=1) + df[
        [f"ask_volume{i}" for i in range(1, 6)]
    ].sum(axis=1)

    valid = pd.DataFrame({"spread": abs_spread, "depth": total_depth}).dropna()
    if len(valid) < 10:
        return {}

    corr = valid["spread"].corr(valid["depth"])
    # 深度分位数下的平均价差（深度越高价差应越窄）
    depth_q = pd.qcut(valid["depth"], 4, labels=["Q1_浅", "Q2", "Q3", "Q4_深"], duplicates="drop")
    by_depth = valid.groupby(depth_q, observed=True)["spread"].mean()

    return {
        "spread_depth_corr": float(corr),
        "spread_by_depth_q1": float(by_depth.iloc[0]) if len(by_depth) >= 1 else np.nan,
        "spread_by_depth_q4": float(by_depth.iloc[-1]) if len(by_depth) >= 2 else np.nan,
    }


def generate_report(symbol: str, spread: dict, depth: dict, impact: dict, relation: dict) -> str:
    """生成 Markdown 分析报告。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def f(v: Any, d: int = 4) -> str:
        return "N/A" if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) else f"{v:.{d}f}"

    lines = [
        "# tick 盘口微观结构特征分析报告",
        "",
        f"> 品种: {symbol} (螺纹钢连续合约)",
        "> 数据源: TQSDK tick（含 5 档盘口）",
        f"> 生成时间: {now}",
        "> 分析版本: v2.31.0 Phase 5",
        "",
        "---",
        "",
        "## 1. 买卖价差（Spread）",
        "",
    ]

    if spread:
        lines += [
            "| 指标 | 值 |",
            "|:----|:---|",
            f"| tick 数 | {spread.get('n_ticks', 'N/A')} |",
            f"| 绝对价差均值 | {f(spread.get('abs_spread_mean'))} 元 |",
            f"| 绝对价差中位数 | {f(spread.get('abs_spread_median'))} 元 |",
            f"| 绝对价差标准差 | {f(spread.get('abs_spread_std'))} |",
            f"| 绝对价差区间 | [{f(spread.get('abs_spread_min'))}, {f(spread.get('abs_spread_max'))}] |",
            f"| 相对价差均值 | {f(spread.get('rel_spread_mean_bps'))} bps |",
            f"| 相对价差中位数 | {f(spread.get('rel_spread_median_bps'))} bps |",
            f"| 1 最小变动价位占比 | {f(spread.get('abs_spread_pct_1tick'), 1)}% |",
            f"| 2 最小变动价位占比 | {f(spread.get('abs_spread_pct_2tick'), 1)}% |",
        ]
    else:
        lines += ["（盘口数据不足）"]

    lines += [
        "",
        "### 1.1 解读",
        "",
        "- **最小变动价位**: 螺纹钢为 1 元/吨，价差为 1 表示报价连续",
        "- **相对价差**: 反映流动性成本，越低越利于高频策略",
        "",
        "---",
        "",
        "## 2. 盘口深度（Depth）",
        "",
    ]

    if depth:
        lines += [
            "| 指标 | 值 |",
            "|:----|:---|",
            f"| 五档买深均值 | {f(depth.get('bid_depth_mean'), 0)} 手 |",
            f"| 五档卖深均值 | {f(depth.get('ask_depth_mean'), 0)} 手 |",
            f"| 五档总深度均值 | {f(depth.get('total_depth_mean'), 0)} 手 |",
            f"| 深度不平衡 OBI 均值 | {f(depth.get('obi_mean'))} |",
            f"| OBI 标准差 | {f(depth.get('obi_std'))} |",
            f"| 买方占优占比 | {f(depth.get('obi_pct_positive'), 1)}% |",
            f"| 买一量占比 | {f(depth.get('bid_vol1_ratio'), 1)}% |",
            f"| 卖一量占比 | {f(depth.get('ask_vol1_ratio'), 1)}% |",
        ]
    else:
        lines += ["（盘口深度数据不足）"]

    lines += [
        "",
        "### 2.1 解读",
        "",
        "- **OBI > 0**: 买方承接力强，短期上行动力",
        "- **OBI < 0**: 卖方供给强，短期下行压力",
        "- **一档占比**: 反映深度集中在最优报价的程度",
        "",
        "---",
        "",
        "## 3. 冲击成本（Impact）",
        "",
    ]

    if impact:
        lines += [
            "| 指标 | 值 |",
            "|:----|:---|",
            f"| Amihud 非流动性均值 | {f(impact.get('amihud_mean'), 8)} |",
            f"| Amihud 中位数 | {f(impact.get('amihud_median'), 8)} |",
            f"| 有效价差均值 | {f(impact.get('eff_spread_mean'))} 元 |",
            f"| 有效价差 | {f(impact.get('eff_spread_bps'))} bps |",
            f"| Kyle's Lambda | {f(impact.get('kyle_lambda'), 8)} |",
            f"| 每 tick 平均成交量 | {f(impact.get('avg_volume_per_tick'), 0)} 手 |",
            f"| 每 tick 平均成交额 | {f(impact.get('avg_amount_per_tick'), 0)} 元 |",
        ]
    else:
        lines += ["（冲击成本数据不足）"]

    lines += [
        "",
        "### 3.1 解读",
        "",
        "- **Amihud**: 每元成交的价格冲击，越高流动性越差",
        "- **有效价差**: 实际成交相对中点的偏离，衡量成交成本",
        "- **Kyle's Lambda**: 单位成交量导致的价格变化斜率",
        "",
        "---",
        "",
        "## 4. 价差-深度联动",
        "",
    ]

    if relation:
        lines += [
            "| 指标 | 值 |",
            "|:----|:---|",
            f"| 价差-深度相关系数 | {f(relation.get('spread_depth_corr'))} |",
            f"| 最浅深度档平均价差 | {f(relation.get('spread_by_depth_q1'))} 元 |",
            f"| 最深深度档平均价差 | {f(relation.get('spread_by_depth_q4'))} 元 |",
        ]
        lines += [
            "",
            "### 4.1 解读",
            "",
            "- 相关系数为负: 深度越厚价差越窄（流动性越好）",
            "- 若为 0/正: 深度与价差无联动，可能存在流动性分层",
        ]
    else:
        lines += ["（联动数据不足）"]

    lines += [
        "",
        "---",
        "",
        "## 5. 综合结论",
        "",
        "### 5.1 流动性水平",
        "",
        "- 价差水平（绝对/相对）: 见 §1",
        "- 深度水平（五档总量）: 见 §2",
        "- 冲击成本（Amihud/Kyle）: 见 §3",
        "",
        "### 5.2 交易建议",
        "",
        "- 最优下单时机: 价差最窄时段",
        "- 订单拆分建议: 根据五档深度评估单笔冲击",
        "- 滑点预算: 基于有效价差均值设定",
        "",
        "### 5.3 风险提示",
        "",
        "- tick 数据仅覆盖盘中 42 分钟（免费账号限制），统计显著性有限",
        "- 5 档盘口为瞬时快照，未捕捉撤单行为",
        "- 实盘滑点可能高于盘口深度估计（大单冲击）",
        "",
    ]

    report = "\n".join(lines)
    report_path = OUTPUT_DIR / "tick_microstructure_analysis.md"
    report_path.write_text(report, encoding="utf-8")
    return str(report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="tick 盘口微观结构特征分析")
    parser.add_argument("--symbol", default=SYMBOL, help="品种代码")
    parser.add_argument("--count", type=int, default=COUNT, help="tick 行数（免费账号上限 5000）")
    args = parser.parse_args()

    print("=" * 60)
    print("Phase 5: tick 盘口微观结构特征分析")
    print(f"品种: {args.symbol} | tick 数: {args.count}")
    print("=" * 60)

    provider = FuturesDataProvider()
    df = provider.get_tick_data(args.symbol, count=args.count, trace_id="tick_microstructure")
    if df.empty:
        print("tick 数据获取失败，无法分析")
        return

    print(f"获取 {len(df)} 行 tick 数据")

    print("\n[1/4] 买卖价差分析...")
    spread = analyze_spread(df)
    print(f"  → 绝对价差均值={spread.get('abs_spread_mean')}, 相对价差={spread.get('rel_spread_mean_bps')}bps")

    print("\n[2/4] 盘口深度分析...")
    depth = analyze_depth(df)
    print(f"  → OBI均值={depth.get('obi_mean')}, 五档总深度={depth.get('total_depth_mean')}")

    print("\n[3/4] 冲击成本分析...")
    impact = analyze_impact(df)
    print(f"  → Amihud={impact.get('amihud_mean')}, Kyle's Lambda={impact.get('kyle_lambda')}")

    print("\n[4/4] 价差-深度联动分析...")
    relation = analyze_spread_depth_relation(df)
    print(f"  → 相关系数={relation.get('spread_depth_corr')}")

    print("\n正在生成报告...")
    report_path = generate_report(args.symbol, spread, depth, impact, relation)
    print(f"\n报告已保存: {report_path}")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    main()
