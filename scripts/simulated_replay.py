"""
scripts/simulated_replay.py — 模拟仓历史回放落地脚本（D.1，v2.101.0）。

用模拟仓在历史行情上回放信号，生成权益曲线 + 因子归因反馈记录，
验证"信号 → 模拟撮合 → 逐日盯市 → 因子归因 → 反馈闭环"链路。

- 市场: 期货（多空双向，保证金）+ 股票/ETF（仅做多，全额现金）
- 撮合纪律: t 日信号 → t+1 开盘价成交 → t+1 收盘盯市（无未来函数）
- 信号源: 内置简易均线穿越规则（ma_cross），演示用；可替换为真实因子信号
- 输出: 控制台摘要（--json 输出机器可读）+ 可落盘反馈记录（--out）

用法:
    python scripts/simulated_replay.py [--symbols RB0 CU0 600519] [--days 250] \\
        [--initial-cash 1000000] [--rule ma_cross] [--json] [--out report.json]

反馈闭环: --out 落盘反馈记录文件，可由 LiveFeedbackImporter 导入
（DuckDB feedback_live 表）驱动 LiveVsBacktestICReport 衰减判定。

角色边界: FTS 只做模拟核算，真实撮合由下游（FDT）负责。
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.state import generate_trace_id  # noqa: E402
from fts.live_trade import SimulatedPortfolio, SimulatedReplayEngine  # noqa: E402

# 默认标的：期货主连（多空）+ 股票（仅做多）
DEFAULT_FUTURES = ["RB0", "CU0", "AU0"]
DEFAULT_STOCKS = ["600519", "000858"]


def generate_signals(panel: dict[str, pd.DataFrame], lookback: int = 20) -> list[dict[str, Any]]:
    """按均线穿越规则逐日生成 FactorSignal（含 contributing_factors 布归因字段）。

    规则: close > MA(lookback) → 做多 +1；close < MA → 做空 -1（股票/ETF 转为 flat）。
    每交易日产出一条覆盖全部标的多空仓位的信号。

    Args:
        panel: {symbol: DataFrame(index=date, columns=[open, close])}
        lookback: 均线窗口

    Returns:
        按时间升序的 FactorSignal 列表。
    """
    dates = sorted({d for df in panel.values() for d in df.index})
    signals: list[dict[str, Any]] = []
    for dt in dates:
        legs: list[dict[str, Any]] = []
        for sym, df in panel.items():
            if dt not in df.index:
                continue
            close = float(df.loc[dt, "close"])
            hist = df.loc[:dt, "close"] if dt in df.index else df["close"]
            ma = float(hist.tail(lookback).mean()) if len(hist) >= lookback else float("nan")
            if not np.isfinite(ma) or ma <= 0:
                continue
            is_stock = sym.isdigit() and len(sym) == 6
            if close > ma:
                direction, position = "long", 1.0
            elif is_stock:
                direction, position = "flat", 0.0  # 股票仅做多
            else:
                direction, position = "short", 1.0
            legs.append(
                {
                    "symbol": sym,
                    "direction": direction,
                    "position": position,
                    "confidence": 0.8,
                    "price": close,
                    "contributing_factors": [
                        {"factor_id": "ma_cross", "weight": 1.0, "signal": 1.0 if direction == "long" else -1.0}
                    ],
                }
            )
        if not legs:
            continue
        signals.append(
            {
                "signal_id": f"ma_cross_{pd.Timestamp(dt).strftime('%Y%m%d')}",
                "timestamp": pd.Timestamp(dt).strftime("%Y-%m-%dT15:00:00"),
                "universe": [leg["symbol"] for leg in legs],
                "signals": legs,
                "meta": {"trace_id": generate_trace_id("sim_replay"), "rule": "ma_cross"},
            }
        )
    return signals


def load_panel(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """加载行情面板（期货走 FTSDataProvider futures，股票走 stock；失败降级合成数据）。"""
    from fts.data import get_data_provider

    provider = get_data_provider()
    panel: dict[str, pd.DataFrame] = {}
    futures = [s for s in symbols if not (s.isdigit() and len(s) == 6)]
    stocks = [s for s in symbols if s.isdigit() and len(s) == 6]

    for sym in futures:
        try:
            df = provider.get_futures_ohlcv(sym, days=days, trace_id="sim_replay")
            df = df[["open", "close"]].dropna()
            if not df.empty:
                panel[sym] = df
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 期货 {sym} 加载失败，跳过: {e}")
    if stocks:
        try:
            sp, _ = provider.get_stock_panel(stocks, days=days, trace_id="sim_replay")
            for sym, df in sp.items():
                if "open" in df.columns and "close" in df.columns:
                    panel[sym] = df[["open", "close"]].dropna()
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 股票面板加载失败: {e}")

    # 无数据降级合成（保证脚本可运行）
    if not panel:
        print("  [WARN] 无真实数据，降级合成示例数据")
        from fts.data import FTSDataProvider

        for i, sym in enumerate(symbols):
            df = FTSDataProvider.synthesize_ohlcv(n_days=days, base_price=15.0 + i * 5)
            panel[sym] = df[["open", "close"]]
    return panel


def main(
    symbols: list[str],
    days: int = 250,
    initial_cash: float = 1_000_000.0,
    rule: str = "ma_cross",
    out: str | None = None,
    json_out: bool = False,
) -> int:
    """执行历史回放主流程。

    Args:
        symbols: 标的列表（期货主连 + 股票）
        days: 回溯天数
        initial_cash: 初始资金
        rule: 信号规则（当前仅支持 ma_cross）
        out: 反馈记录落盘路径（None 不落盘）
        json_out: 是否输出机器可读 JSON

    Returns:
        进程退出码。
    """
    if rule != "ma_cross":
        print(f"[ERROR] 不支持的规则: {rule!r}（当前支持 ma_cross）")
        return 1

    trace_id = generate_trace_id("sim_replay")
    print(f"[simulated_replay] trace_id={trace_id} | symbols={symbols} | days={days}")

    # 1. 加载行情
    panel = load_panel(symbols, days)
    print(f"[1/4] 行情面板: {len(panel)} 标的")

    # 2. 生成信号
    signals = generate_signals(panel)
    print(f"[2/4] 生成信号: {len(signals)} 条（{rule}）")

    # 3. 回放
    pf = SimulatedPortfolio(config={"initial_cash": initial_cash})
    engine = SimulatedReplayEngine(pf)
    result = engine.replay(signals, panel)
    summary = result["summary"]
    print(f"[3/4] 回放完成: {summary}")

    # 4. 反馈闭环落盘
    records = result["feedback_records"]
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[4/4] 反馈记录落盘: {out_path}（{len(records)} 条）")
    else:
        print(f"[4/4] 反馈记录 {len(records)} 条（未落盘，--out 可指定路径）")

    if json_out:
        payload = {
            "trace_id": trace_id,
            "summary": summary,
            "n_feedback": len(records),
            "feedback_sample": records[:5],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="模拟仓历史回放（D.1）")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_FUTURES + DEFAULT_STOCKS, help="标的（期货主连+股票）")
    parser.add_argument("--days", type=int, default=250, help="回溯天数")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0, help="初始资金")
    parser.add_argument("--rule", choices=["ma_cross"], default="ma_cross", help="信号规则")
    parser.add_argument("--out", type=str, default=None, help="反馈记录落盘路径（JSON）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()
    sys.exit(
        main(
            symbols=args.symbols,
            days=args.days,
            initial_cash=args.initial_cash,
            rule=args.rule,
            out=args.out,
            json_out=args.json,
        )
    )