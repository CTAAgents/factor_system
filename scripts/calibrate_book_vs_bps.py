#!/usr/bin/env python3
"""
FTS book vs bps 撮合差异实证标定脚本（D.2 §4.9）

对比同一信号序列在「tick 盘口逐档撮合」与「固定 bps 滑点」两条路径下的
成交价差异，产出统计报告支撑参数取舍：

    - avg_price_diff_bps: 平均成交价差（book − bps，基点）
    - slippage_distribution: book 实际滑点分布（bps，分位数）
    - partial_fill_rate: book 路径部分成交频率（深度不足占比）
    - spread_sensitivity: 价差宽度对差异的影响（窄/中/宽三档）

合成盘口场景（无真实 tick 依赖，可离线/CI 运行）；--ticks 提供真实 tick
行时可走 build_book_from_ticks 构造盘口。

用法:
    python scripts/calibrate_book_vs_bps.py
    python scripts/calibrate_book_vs_bps.py --out reports/futures/{date}/
    python scripts/calibrate_book_vs_bps.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fts.live_trade.matching import OrderBookMatchingEngine  # noqa: E402

_REF_BPS = 0.5  # 对比用默认 bps 滑点（futures 默认）


def _bps_from_price(price: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return (price - base) / base * 10000.0


def _bps_price(base: float, side: str, slippage_bps: float = _REF_BPS) -> float:
    """bps 路径折算价：买入上浮、卖下沉。"""
    slip = slippage_bps / 10000.0
    return base * (1 + slip) if side == "buy" else base * (1 - slip)


def simulate_book_vs_bps(
    n_scenarios: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """合成盘口场景标定 book vs bps 差异。

    Args:
        n_scenarios: 场景数
        seed: 随机种子（可复现）

    Returns:
        {avg_price_diff_bps, slippage_distribution, partial_fill_rate,
         spread_sensitivity, n}
    """
    rng = np.random.default_rng(seed)
    engine = OrderBookMatchingEngine(depth=5)

    diffs: list[float] = []
    book_slips: list[float] = []
    partial_count = 0
    spread_buckets: dict[str, list[float]] = {"narrow": [], "medium": [], "wide": []}

    for _ in range(n_scenarios):
        base = float(rng.uniform(50, 5000))
        spread_bps = float(rng.choice([2.0, 10.0, 30.0]))  # 窄/中/宽价差
        half = base * (spread_bps / 10000.0) / 2.0
        # 构造 3 档盘口：深度随机，可能不足
        ask_q = [float(rng.integers(1, 20)) for _ in range(3)]
        bid_q = [float(rng.integers(1, 20)) for _ in range(3)]
        asks = [{"price": base + half + i * base * 0.001, "quantity": q} for i, q in enumerate(ask_q)]
        bids = [{"price": base - half - i * base * 0.001, "quantity": q} for i, q in enumerate(bid_q)]
        book = {"ask_levels": asks, "bid_levels": bids, "last_price": base}

        qty = float(rng.integers(1, 10))
        side = "buy" if rng.random() < 0.5 else "sell"

        res = engine.match_market(book, side, qty, base_price=base)
        if not res.get("book_used"):
            continue
        book_price = res["avg_price"]
        if res.get("unfilled_qty", 0.0) > 1e-9:
            partial_count += 1
        bps_price = _bps_price(base, side, _REF_BPS)

        diffs.append(_bps_from_price(book_price, bps_price))
        book_slips.append(res.get("slippage_bps", 0.0))
        key = "narrow" if spread_bps <= 2.0 else ("medium" if spread_bps <= 10.0 else "wide")
        spread_buckets[key].append(_bps_from_price(book_price, bps_price))

    if not diffs:
        return {"n": 0, "avg_price_diff_bps": 0.0, "slippage_distribution": {},
                "partial_fill_rate": 0.0, "spread_sensitivity": {}}

    return {
        "n": len(diffs),
        "avg_price_diff_bps": round(float(np.mean(np.abs(diffs))), 4),
        "mean_signed_diff_bps": round(float(np.mean(diffs)), 4),
        "slippage_distribution": {
            "p10": round(float(np.percentile(book_slips, 10)), 4),
            "p50": round(float(np.percentile(book_slips, 50)), 4),
            "p90": round(float(np.percentile(book_slips, 90)), 4),
        },
        "partial_fill_rate": round(partial_count / max(len(diffs), 1), 4),
        "spread_sensitivity": {
            k: (round(float(np.mean(np.abs(v))), 4) if v else None) for k, v in spread_buckets.items()
        },
    }


def render_markdown(result: dict[str, Any], today: str) -> str:
    """渲染 Markdown 标定报告。"""
    lines = [f"# book vs bps 撮合差异标定 — {today}", ""]
    lines.append(f"- 场景数: {result['n']} | 基准 bps: {_REF_BPS}")
    lines.append(f"- 平均绝对价差: **{result['avg_price_diff_bps']} bps** (book − bps)")
    lines.append(f"- 带符号均值: {result['mean_signed_diff_bps']} bps")
    lines.append(f"- 部分成交率: {result['partial_fill_rate']:.2%}")
    lines.append("")
    lines.append("## book 实际滑点分布（bps）")
    lines.append("")
    lines.append("| 分位 | 值 |")
    lines.append("|------|----|")
    for k, v in result.get("slippage_distribution", {}).items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## 价差宽度敏感性（平均绝对价差 bps）")
    lines.append("")
    lines.append("| 价差档 | 平均绝对价差 |")
    lines.append("|--------|--------------|")
    for k, v in result.get("spread_sensitivity", {}).items():
        lines.append(f"| {k} | {v if v is not None else 'N/A'} |")
    lines.append("")
    lines.append("> 结论：book 路径滑点由盘口缺口自然产生；bps 路径为固定参考值。")
    lines.append("> 差异大说明固定 bps 未能刻画市场流动性变化，需按价差档位分层定价。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="book vs bps 撮合差异标定（D.2 §4.9）")
    parser.add_argument("--scenarios", type=int, default=200, help="合成场景数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--out", type=str, default="", help="报告输出目录（默认 reports/calibration/）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = simulate_book_vs_bps(n_scenarios=args.scenarios, seed=args.seed)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    today = date.today().isoformat()
    report = render_markdown(result, today)
    out_dir = Path(args.out) if args.out else _PROJECT_ROOT / "reports" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"book_vs_bps_{today}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[OK] 报告已保存: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
