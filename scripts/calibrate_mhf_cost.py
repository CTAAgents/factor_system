"""
scripts/calibrate_mhf_cost.py — 阶段2：期货真实单边成本盘口校准。

用 TQSDK tick 近实时盘口（含 5 档）实测各品种买卖价差，估算真实单边成本：

    单边成本(bps) = 手续费(commission) + 滑点(slippage) + 半价差(spread/2)

- 手续费：复用 FTS 期货默认 0.2bps（按成交额）
- 滑点：市价单冲击，保守取 max(1.5, 半价差) bps
- 半价差：实测 (ask1-bid1)/mid 中位数 / 2

输出:
    memory/portfolio/futures/mhf_cost.json   （{symbol: {spread_bps, one_way_cost_bps, ...}}）
    reports/mhf/phase0_cost_calibration_{date}.md

用法:
    python scripts/calibrate_mhf_cost.py [--max-symbols 22] [--ticks 3000]

设计约束:
    - 纯读取（tick 实时拉取，不写 DuckDB；DuckDB 锁兼容）
    - 拉取失败品种降级：回退默认成本（spread=2bps 估计）
    - 价差结构相对稳定，当前时点快照可作为回测成本基准
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import FuturesDataProvider  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"
COST_CACHE = PROJECT_ROOT / "memory" / "portfolio" / "futures" / "mhf_cost.json"
TRACE_ID: str = f"mhf_cost_{date.today().isoformat()}"
DEFAULT_COMMISSION_BPS: float = 0.2   # FTS 期货默认手续费（按成交额）
DEFAULT_SLIPPAGE_BPS: float = 1.5     # 保守滑点下限
FALLBACK_SPREAD_BPS: float = 2.0      # 拉取失败品种的回退价差


def _load_pool() -> list[str]:
    """读取合格品种池（缺失回退动态池）。"""
    try:
        payload = json.loads(
            (PROJECT_ROOT / "memory/portfolio/futures/mhf_pool.json")
            .read_text(encoding="utf-8")
        )
        pool = payload.get("pool") or []
        if pool:
            return list(pool)
    except Exception:  # noqa: BLE001
        pass
    from fts.data_futures import get_dynamic_core_subset

    return list(get_dynamic_core_subset())


def measure_spread(df: pd.DataFrame) -> float:
    """实测相对价差中位数（bps）。缺少盘口列返回 fallback。"""
    if df is None or df.empty:
        return FALLBACK_SPREAD_BPS
    need = {"bid_price1", "ask_price1"}
    if not need.issubset(df.columns):
        return FALLBACK_SPREAD_BPS
    bid = pd.to_numeric(df["bid_price1"], errors="coerce")
    ask = pd.to_numeric(df["ask_price1"], errors="coerce")
    mid = (bid + ask) / 2
    spread = ((ask - bid) / mid.replace(0.0, np.nan)).dropna()
    if spread.empty:
        return FALLBACK_SPREAD_BPS
    return float(np.median(spread) * 1e4)


def main() -> None:
    parser = argparse.ArgumentParser(description="期货单边成本盘口校准")
    parser.add_argument("--max-symbols", type=int, default=22)
    parser.add_argument("--ticks", type=int, default=3000)
    parser.add_argument("--commission-bps", type=float, default=DEFAULT_COMMISSION_BPS)
    parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS)
    args = parser.parse_args()

    pool = _load_pool()[: args.max_symbols]
    print(f"品种池: {len(pool)}  trace_id={TRACE_ID}", flush=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    prov = FuturesDataProvider()
    rows: dict[str, dict[str, float]] = {}
    for i, sym in enumerate(pool, 1):
        t0 = time.time()
        spread = FALLBACK_SPREAD_BPS
        try:
            df = prov.get_tick_data(sym, args.ticks, TRACE_ID)
            spread = measure_spread(df)
            src = "tick"
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] {sym} tick 失败: {type(e).__name__} {e}", flush=True)
            src = "fallback"
        half_spread = spread / 2.0
        slippage = max(args.slippage_bps, half_spread)
        one_way = args.commission_bps + slippage + half_spread
        rows[sym] = {
            "spread_bps": round(spread, 2),
            "half_spread_bps": round(half_spread, 2),
            "commission_bps": args.commission_bps,
            "slippage_bps": round(slippage, 2),
            "one_way_cost_bps": round(one_way, 2),
            "source": src,
        }
        print(f"  [{i}/{len(pool)}] {sym}: 价差={spread:.2f}bps "
              f"单边成本={one_way:.2f}bps [{src}] {time.time()-t0:.1f}s", flush=True)

    # 落盘成本参数
    payload = {
        "updated": date.today().isoformat(),
        "trace_id": TRACE_ID,
        "by_symbol": rows,
    }
    COST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    COST_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"成本参数已落盘: {COST_CACHE}", flush=True)

    # 报告
    out = REPORTS_DIR / f"phase0_cost_calibration_{date.today().isoformat()}.md"
    lines = [
        "# 期货单边成本盘口校准报告",
        "",
        f"- 日期: {date.today().isoformat()}  trace_id: `{TRACE_ID}`",
        f"- 口径: 单边成本 = 手续费({args.commission_bps}bps) + 滑点 + 半价差",
        "- 数据: TQSDK tick 近实时盘口（当前时点快照）",
        "",
        "| 品种 | 价差(bps) | 半价差 | 滑点 | 单边成本(bps) | 来源 |",
        "|:--|--:|--:|--:|--:|:--|",
    ]
    for sym in pool:
        r = rows[sym]
        lines.append(
            f"| {sym} | {r['spread_bps']} | {r['half_spread_bps']} | "
            f"{r['slippage_bps']} | {r['one_way_cost_bps']} | {r['source']} |"
        )
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"校准报告已输出: {out}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"校准失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
