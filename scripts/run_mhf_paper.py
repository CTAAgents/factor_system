"""
scripts/run_mhf_paper.py — 阶段3：中高频策略模拟盘回放（内存模式）。

流程：读合格品种池 → 拉 30m 数据（TDX 17709，绕过 DuckDB）→ MHF 因子 → 混合信号 →
分钟级模拟盘（MhfPaperTrader，含盘中风控：单品种止损/日内止损/持仓时限/品种限额）
→ 与回测引擎结果对比（一致性验证）→ 输出报告。

输出:
    reports/mhf/phase3_paper_{date}.md     （模拟盘绩效 + 风控事件）
    reports/mhf/phase3_paper_{date}.csv    （每日净值）

用法:
    python scripts/run_mhf_paper.py [--max-symbols 22] [--bars 4000]
                                    [--stop-loss 0.012] [--holding-bars 16]

设计约束:
    - 内存模式：不写 DuckDB（DuckDB 锁兼容），结果落文件
    - 撮合口径与回测引擎一致（t-1 信号 → t 开盘成交，差异化成本）
    - 风控为逐 bar 状态机，全部事件留痕
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.mhf_backtest import (  # noqa: E402
    MhfBacktestConfig,
    run_mhf_backtest,
)
from fts.factor_engine.mhf_factors import MhfFactorConfig, compute_mhf_factors  # noqa: E402
from fts.factor_engine.mhf_signals import (  # noqa: E402
    MhfSignalConfig,
    build_hybrid_signals,
)
from fts.live_trade.paper_trader_mhf import MhfPaperTrader, MhfRiskConfig  # noqa: E402
from scripts.backtest_mhf_strategy import (  # noqa: E402
    ANNUAL_BARS,
    _fetch,
    _load_cost_map,
    _load_pool,
)

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"
TRACE_ID: str = f"mhf_paper_{date.today().isoformat()}"
FREQ: str = "30m"


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段3 中高频模拟盘回放")
    parser.add_argument("--max-symbols", type=int, default=22)
    parser.add_argument("--bars", type=int, default=4000)
    parser.add_argument("--stop-loss", type=float, default=0.012,
                        help="单品种止损（比例）")
    parser.add_argument("--daily-loss", type=float, default=0.015,
                        help="日内组合止损（比例）")
    parser.add_argument("--holding-bars", type=int, default=16,
                        help="持仓时限（bar）")
    parser.add_argument("--max-positions", type=int, default=8)
    args = parser.parse_args()

    pool = _load_pool()[: args.max_symbols]
    cost_map = _load_cost_map()
    print(f"品种池: {len(pool)}  频率: {FREQ}  trace_id={TRACE_ID}", flush=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 数据 + 因子 + 信号（复用 backtest 链路）──
    from fts.data_sources.tdx_local_source import TdxLocalSource

    src = TdxLocalSource(period=FREQ)
    cfg_factor = MhfFactorConfig()
    panel: dict[str, pd.DataFrame] = {}
    factor_panel: dict[str, dict[str, pd.Series]] = {}
    for i, sym in enumerate(pool, 1):
        df = _fetch(sym, src, args.bars)
        if df.empty:
            continue
        factors = compute_mhf_factors(df, cfg_factor)
        panel[sym] = df
        factor_panel[sym] = factors
        print(f"  [{i}/{len(pool)}] {sym}: {len(df)} 行", flush=True)

    signals = build_hybrid_signals(
        factor_panel, MhfSignalConfig(max_positions=args.max_positions)
    )
    print(f"有效信号品种: {len(signals)}", flush=True)

    # ── 模拟盘（含风控）──
    risk = MhfRiskConfig(
        stop_loss_pct=args.stop_loss,
        daily_loss_pct=args.daily_loss,
        holding_bars=args.holding_bars,
        max_positions=args.max_positions,
        target_pct=0.5 / args.max_positions,
        cost_bps_map=cost_map,
    )
    trader = MhfPaperTrader(panel, signals, risk)
    t0 = time.time()
    res = trader.run()
    print(f"模拟盘回放完成: {res.metrics}  耗时 {time.time()-t0:.1f}s", flush=True)

    # ── 与回测引擎一致性对比（无风控口径）──
    bt = run_mhf_backtest(
        panel, signals,
        MhfBacktestConfig(
            cost_bps=min(cost_map.values()) if cost_map else 3.0,
            cost_bps_map=cost_map,
            target_pct=risk.target_pct,
            max_positions=args.max_positions,
            annual_bars=ANNUAL_BARS[FREQ],
        ),
    )
    bt_final = bt.metrics.get("total_return", 0.0)
    paper_final = res.metrics.get("final_equity", 1.0) - 1.0

    # ── 报告 ──
    out = REPORTS_DIR / f"phase3_paper_{date.today().isoformat()}.md"
    lines: list[str] = [
        "# 阶段3 中高频策略模拟盘回放报告（内存模式）",
        "",
        f"- 日期: {date.today().isoformat()}  trace_id: `{TRACE_ID}`",
        f"- 品种: {len(panel)}  频率: {FREQ}  bar 数: {args.bars}",
        f"- 风控: 止损 {args.stop_loss:.1%} / 日内止损 {args.daily_loss:.1%} / "
        f"持仓时限 {args.holding_bars} bar / 上限 {args.max_positions} 仓",
        f"- 成本: 盘口校准差异化（{len(cost_map)} 品种）",
        "",
        "## 模拟盘绩效",
        "",
        f"- 期末净值: {res.metrics.get('final_equity')}",
        f"- 总收益: {paper_final:.2%}",
        f"- 成交数: {res.metrics.get('n_fills')}  风控事件: {res.metrics.get('n_events')}",
        f"- 回放天数: {res.metrics.get('n_days')}",
        "",
        "## 与回测引擎一致性",
        "",
        f"- 回测引擎总收益（同信号/成本，无风控）: {bt_final:.2%}",
        f"- 模拟盘总收益（含风控）: {paper_final:.2%}",
        f"- 差异（风控影响）: {paper_final - bt_final:.2%}",
        "",
        "## 风控事件明细",
        "",
        "| 时间 | 品种 | 类型 | 说明 |",
        "|:--|:--|:--|:--|",
    ]
    for e in res.events:
        lines.append(f"| {e.time} | {e.symbol} | {e.kind} | {e.message} |")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"模拟盘报告已输出: {out}", flush=True)

    # 每日净值 CSV
    if not res.daily_equity.empty:
        csv = REPORTS_DIR / f"phase3_paper_{date.today().isoformat()}.csv"
        res.daily_equity.to_csv(csv, encoding="utf-8-sig")
        print(f"每日净值已输出: {csv}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"模拟盘失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
