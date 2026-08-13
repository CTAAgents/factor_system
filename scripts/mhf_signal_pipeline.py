"""
scripts/mhf_signal_pipeline.py — 阶段4：中高频策略分钟级信号生成管道。

流程：拉取合格品种池最新 30m 数据（TDX 17709，绕过 DuckDB）→ MHF 因子 →
混合信号（截面多空）→ 组装 FactorSignal 契约 → SignalBridge 发布（默认 JSON）→ 报告。

输出:
    reports/mhf/signals/mhf_signals_{date}.md    （信号排名报告）
    signals/latest_signal.json                   （SignalBridge JSON 协议，供 FDT 消费）

用法:
    python scripts/mhf_signal_pipeline.py [--bars 160] [--max-symbols 22]
                                          [--protocol json] [--output-dir signals]

设计约束:
    - 零未来：仅用当前及历史 bar，输出最新已收盘 bar 的信号
    - 内存计算，不写 DuckDB（DuckDB 锁兼容）
    - 可直接被 scheduler 定时任务调用（generate_mhf_signals 入口）
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.bridge.signal_bridge import SignalBridge  # noqa: E402
from fts.data_sources.tdx_local_source import TdxLocalSource  # noqa: E402
from fts.factor_engine.mhf_factors import MhfFactorConfig, compute_mhf_factors  # noqa: E402
from fts.factor_engine.mhf_signals import (  # noqa: E402
    MhfSignalConfig,
    build_hybrid_signals,
)
from scripts.backtest_mhf_strategy import _load_pool  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf" / "signals"
FREQ: str = "30m"
TRACE_PREFIX: str = f"mhf_sig_{date.today().isoformat()}"


def generate_mhf_signals(
    max_symbols: int = 22,
    bars: int = 160,
    max_positions: int = 8,
    trace_id: str = "",
) -> dict[str, Any]:
    """生成最新信号（供脚本与 scheduler 共用）。

    Returns:
        FactorSignal 契约 dict（signal_id/timestamp/signals/…）。
    """
    trace = trace_id or f"{TRACE_PREFIX}_{int(time.time())}"
    pool = _load_pool()[:max_symbols]
    src = TdxLocalSource(period=FREQ)
    cfg_factor = MhfFactorConfig()
    panel: dict[str, pd.DataFrame] = {}
    factor_panel: dict[str, dict[str, pd.Series]] = {}
    for sym in pool:
        try:
            df = src.fetch_ohlcv(sym, bars, trace_id=trace)
            if df is None or df.empty:
                continue
            df = df.copy()
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
            df = df[["open", "high", "low", "close", "volume"]]
            factors = compute_mhf_factors(df, cfg_factor)
            panel[sym] = df
            factor_panel[sym] = factors
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] {sym}: {type(e).__name__} {e}", flush=True)
            continue

    signals = build_hybrid_signals(
        factor_panel, MhfSignalConfig(max_positions=max_positions)
    )
    if not signals:
        return {"signal_id": trace, "timestamp": _now_iso(),
                "signals": {}, "symbols": 0, "ok": False}

    # 取最新共同时间点
    common: Optional[pd.DatetimeIndex] = None
    for s in signals.values():
        common = s.index if common is None else common.intersection(s.index)
    if common is None or len(common) == 0:
        return {"signal_id": trace, "timestamp": _now_iso(),
                "signals": {}, "symbols": 0, "ok": False}
    latest = common[-1]

    signal_map: dict[str, dict[str, Any]] = {}
    for sym, s in signals.items():
        if latest not in s.index:
            continue
        direction = int(np.sign(s.loc[latest]))
        signal_map[sym] = {
            "direction": direction,          # +1 多 / -1 空 / 0 观望
            "score": round(float(s.loc[latest]), 4),
            "bar_time": str(latest),
            "last_close": float(panel[sym]["close"].loc[latest])
            if latest in panel[sym].index else None,
        }

    payload: dict[str, Any] = {
        "signal_id": trace,
        "timestamp": _now_iso(),
        "signal_date": str(latest.normalize().date()),
        "frequency": FREQ,
        "bar_time": str(latest),
        "signals": signal_map,
        "symbols": int(len(signal_map)),
        "ok": True,
    }
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段4 分钟级信号生成")
    parser.add_argument("--bars", type=int, default=160)
    parser.add_argument("--max-symbols", type=int, default=22)
    parser.add_argument("--max-positions", type=int, default=8)
    parser.add_argument("--protocol", type=str, default="json",
                        choices=["json", "redis", "rest"])
    parser.add_argument("--output-dir", type=str, default="signals")
    parser.add_argument("--trace-id", type=str, default="")
    args = parser.parse_args()

    print(f"生成 MHF 信号（频率={FREQ} 品种上限={args.max_symbols}）", flush=True)
    payload = generate_mhf_signals(
        max_symbols=args.max_symbols, bars=args.bars,
        max_positions=args.max_positions, trace_id=args.trace_id,
    )
    if not payload.get("ok"):
        print("信号生成失败或无有效信号", file=sys.stderr, flush=True)
        sys.exit(1)

    # SignalBridge 发布
    bridge = SignalBridge(protocol=args.protocol,
                          output_dir=Path(args.output_dir))
    try:
        bridge.publish(payload)
        print(f"信号已发布（{args.protocol}）: {payload['signal_id']}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"信号发布失败: {e}", file=sys.stderr, flush=True)

    # 可读报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"mhf_signals_{date.today().isoformat()}.md"
    lines: list[str] = [
        "# MHF 中高频信号（30m）",
        "",
        f"- 信号ID: `{payload['signal_id']}`  时间: {payload['timestamp']}",
        f"- 最新 bar: {payload.get('bar_time')}  频率: {FREQ}",
        "",
        "## 多头信号",
        "",
        "| 品种 | 方向 | 得分 | 最新收盘 |",
        "|:--|:--|--:|--:|",
    ]
    sigs = payload["signals"]
    for sym in sorted(sigs, key=lambda s: -sigs[s]["score"]):
        d = sigs[sym]
        if d["direction"] > 0:
            lines.append(f"| {sym} | 多 | {d['score']} | {d['last_close']} |")
    lines += ["", "## 空头信号", "", "| 品种 | 方向 | 得分 | 最新收盘 |", "|:--|:--|--:|--:|"]
    for sym in sorted(sigs, key=lambda s: sigs[s]["score"]):
        d = sigs[sym]
        if d["direction"] < 0:
            lines.append(f"| {sym} | 空 | {d['score']} | {d['last_close']} |")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"信号报告已输出: {out}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"信号生成失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
