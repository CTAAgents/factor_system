"""
scripts/backtest_mhf_strategy.py — 阶段2：中高频混合策略真实数据回测。

流程：读合格品种池 → 拉 15m 分钟数据（TDX 17709，绕过 DuckDB）→ 计算 MHF 因子 →
混合信号合成（截面+时序）→ 事件驱动回测（含成本敏感性多档）→ 输出报告与净值。

输出:
    reports/mhf/phase2_backtest_{date}.md   （绩效报告）
    reports/mhf/phase2_equity_{date}.csv    （各成本档净值曲线）

用法:
    python scripts/backtest_mhf_strategy.py [--max-symbols 22] [--bars 6000]
                                            [--cost-bps "2 5 10 20"]

设计约束:
    - 纯读取 + 内存计算，不写 DuckDB（DuckDB 锁兼容）
    - 零未来：因子零未来 + 回测信号 shift(1) 开盘成交
    - 反转信号对成本敏感，成本档位敏感性是核心验收
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

from fts.data_sources.tdx_local_source import TdxLocalSource  # noqa: E402
from fts.factor_engine.mhf_backtest import (  # noqa: E402
    MhfBacktestConfig,
    run_mhf_backtest,
)
from fts.factor_engine.mhf_factors import MhfFactorConfig, compute_mhf_factors  # noqa: E402
from fts.factor_engine.mhf_signals import (  # noqa: E402
    MhfSignalConfig,
    build_hybrid_signals,
)

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"
POOL_CACHE = PROJECT_ROOT / "memory" / "portfolio" / "futures" / "mhf_pool.json"
COST_CACHE = PROJECT_ROOT / "memory" / "portfolio" / "futures" / "mhf_cost.json"
TRACE_ID: str = f"mhf_bt_{date.today().isoformat()}"
FREQ: str = "15m"

# 频率 → 年化 bar 数（250 交易日 × 日均 bar 数）
ANNUAL_BARS: dict[str, float] = {
    "5m": 20000.0, "15m": 6800.0, "30m": 3400.0, "60m": 1700.0,
}


def _load_pool() -> list[str]:
    """读取合格品种池（缺失回退动态池）。"""
    try:
        payload = json.loads(POOL_CACHE.read_text(encoding="utf-8"))
        pool = payload.get("pool") or []
        if pool:
            return list(pool)
    except Exception:  # noqa: BLE001
        pass
    from fts.data_futures import get_dynamic_core_subset

    return list(get_dynamic_core_subset())


def _fetch(sym: str, src: TdxLocalSource, bars: int) -> pd.DataFrame:
    """拉取分钟数据，异常返回空 DataFrame。"""
    try:
        df = src.fetch_ohlcv(sym, bars, trace_id=TRACE_ID)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] {sym} 拉取失败: {type(e).__name__} {e}", flush=True)
        return pd.DataFrame()


def _load_cost_map() -> dict[str, float]:
    """读取盘口校准单边成本（one_way_cost_bps）；缺失返回空 dict。"""
    try:
        payload = json.loads(COST_CACHE.read_text(encoding="utf-8"))
        by_sym = payload.get("by_symbol") or {}
        return {sym: float(r["one_way_cost_bps"]) for sym, r in by_sym.items()}
    except Exception:  # noqa: BLE001
        return {}


def daily_direction_filter(
    panel: dict[str, pd.DataFrame],
    signals: dict[str, pd.Series],
    lookback_days: int = 5,
) -> dict[str, pd.Series]:
    """日频方向过滤：日线动量符号（前填充到分钟）与分钟信号同向才保留。

    信号 × 日频方向；日频方向为 0（无趋势）时不过滤（乘 1）。
    """
    out: dict[str, pd.Series] = {}
    for sym, sig in signals.items():
        df = panel.get(sym)
        if df is None or df.empty:
            continue
        daily_close = df["close"].groupby(df.index.normalize()).last()
        mom = daily_close / daily_close.shift(lookback_days) - 1.0
        dir_ = np.sign(mom).replace(0.0, 1.0).ffill().fillna(1.0)
        day_idx = df.index.normalize()
        d = pd.Series(dir_.reindex(day_idx).fillna(1.0).to_numpy(), index=df.index)
        out[sym] = sig * d
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段2 中高频混合策略回测")
    parser.add_argument("--max-symbols", type=int, default=22)
    parser.add_argument("--bars", type=int, default=6000, help="分钟 bar 数（15m≈10个月）")
    parser.add_argument("--freq", type=str, default="15m", choices=["5m", "15m", "30m", "60m"],
                        help="回测频率（降频摊薄成本）")
    parser.add_argument("--cost-bps", type=str, default="2 5 10 20",
                        help="成本档位（空格分隔，基点；与 --use-real-cost 互斥）")
    parser.add_argument("--max-positions", type=int, default=8)
    parser.add_argument("--price-mode", type=str, default="close",
                        help="因子/信号用价：close（收盘）或 typical（(H+L+C)/3，去bounce验证）")
    parser.add_argument("--direction-filter", action="store_true",
                        help="启用日频方向过滤（日线动量与分钟反转同向才交易）")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="强化阈值：信号得分绝对值低于该值不交易")
    parser.add_argument("--use-real-cost", action="store_true",
                        help="用盘口校准差异化成本（mhf_cost.json），忽略 --cost-bps 档位")
    args = parser.parse_args()
    cost_tiers = [float(x) for x in args.cost_bps.split()]
    freq = args.freq
    annual_bars = ANNUAL_BARS[freq]
    cost_map = _load_cost_map() if args.use_real_cost else {}
    if args.use_real_cost:
        print(f"启用盘口校准差异化成本: {len(cost_map)} 品种", flush=True)

    pool = _load_pool()[: args.max_symbols]
    print(f"品种池: {len(pool)}  频率: {freq}  成本档: {cost_tiers}  trace_id={TRACE_ID}",
          flush=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    src = TdxLocalSource(period=freq)
    cfg_factor = MhfFactorConfig()
    panel: dict[str, pd.DataFrame] = {}
    factor_panel: dict[str, dict[str, pd.Series]] = {}
    for i, sym in enumerate(pool, 1):
        t0 = time.time()
        df = _fetch(sym, src, args.bars)
        if df.empty:
            print(f"  [{i}/{len(pool)}] {sym}: 无数据", flush=True)
            continue
        # 因子/信号用价：typical 模式用 (H+L+C)/3 平滑，减轻 bid-ask bounce（验证用）
        factor_df = df
        if args.price_mode == "typical":
            factor_df = df.copy()
            factor_df["close"] = (df["high"] + df["low"] + df["close"]) / 3.0
        factors = compute_mhf_factors(factor_df, cfg_factor)
        panel[sym] = df            # 回测仍用真实 OHLC
        factor_panel[sym] = factors
        print(f"  [{i}/{len(pool)}] {sym}: {len(df)} 行 {time.time()-t0:.1f}s", flush=True)

    if not panel:
        print("无有效品种数据，终止", file=sys.stderr)
        sys.exit(1)

    # 混合信号（截面+时序），可强化阈值
    signals = build_hybrid_signals(
        factor_panel,
        MhfSignalConfig(max_positions=args.max_positions, min_score=args.min_score),
    )
    # 日频方向过滤（可选）
    if args.direction_filter and signals:
        signals = daily_direction_filter(panel, signals)
        print("已启用日频方向过滤", flush=True)
    print(f"有效信号品种: {len(signals)}", flush=True)

    # 回测（多成本档 / 差异化真实成本）
    rows: list[dict[str, float]] = []
    equity_curves: dict[str, pd.Series] = {}
    split_rows: list[dict[str, float]] = []
    base_cfg = MhfBacktestConfig(target_pct=0.5 / args.max_positions)
    bt_costs: list[float] = [min(cost_map.values())] if args.use_real_cost else cost_tiers
    for cost in bt_costs:
        cfg = MhfBacktestConfig(
            cost_bps=cost,
            cost_bps_map=cost_map if args.use_real_cost else {},
            target_pct=base_cfg.target_pct,
            max_positions=args.max_positions,
            annual_bars=annual_bars,
        )
        res = run_mhf_backtest(panel, signals, cfg)
        m = res.metrics
        rows.append({"cost_bps": cost, **m})
        if not res.equity.empty:
            equity_curves[f"cost_{cost:g}"] = res.equity
            # 分段稳健性（walk-forward 近似：前后半段独立绩效）
            eq = res.equity
            half = len(eq) // 2
            for label, seg in (("first", eq.iloc[:half]), ("second", eq.iloc[half:])):
                seg_ret = seg.pct_change().dropna()
                sd = float(seg_ret.std())
                sharpe = float(seg_ret.mean() / sd * np.sqrt(cfg.annual_bars)) if sd > 0 else 0.0
                seg_total = float(seg.iloc[-1] / seg.iloc[0] - 1.0) if len(seg) > 1 else 0.0
                split_rows.append({
                    "cost_bps": cost, "split": label,
                    "total_return": round(seg_total, 4),
                    "sharpe": round(sharpe, 3),
                    "n_bars": int(len(seg)),
                })
        print(f"  成本 {cost:g}bps: 年化={m.get('annualized_return')} "
              f"夏普={m.get('sharpe')} 回撤={m.get('max_drawdown')} "
              f"换手={m.get('turnover_daily')}", flush=True)

    # ── 报告 ──
    out_path = REPORTS_DIR / f"phase2_backtest_{date.today().isoformat()}.md"
    lines: list[str] = [
        "# 阶段2 中高频混合策略回测报告",
        "",
        f"- 日期: {date.today().isoformat()}  trace_id: `{TRACE_ID}`",
        f"- 品种: {len(panel)}（合格池）  频率: {freq}  bar 数: {args.bars}",
        f"- 信号用价: {args.price_mode}（typical 为去 bounce 验证）",
        f"- 信号: 截面选品种(max {args.max_positions} 仓) + 时序反转进出场"
        f"{' + 日频方向过滤' if args.direction_filter else ''}"
        f"{f' + 阈值{args.min_score}' if args.min_score > 0 else ''}",
        f"- 成本: {'盘口校准差异化' if args.use_real_cost else f'档位 {cost_tiers}bps'}",
        f"- 目标仓位/品种: {base_cfg.target_pct}  年化 bar 数: {annual_bars:g}",
        "",
        "## 成本敏感性",
        "",
        "| 成本(bps) | 总收益 | 年化 | 夏普 | 最大回撤 | 日换手 | 成本占比 | 交易数 |",
        "|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['cost_bps']:g} | {r.get('total_return', '-')} | "
            f"{r.get('annualized_return', '-')} | {r.get('sharpe', '-')} | "
            f"{r.get('max_drawdown', '-')} | {r.get('turnover_daily', '-')} | "
            f"{r.get('cost_ratio', '-')} | {r.get('n_trades', '-')} |"
        )
    lines += ["", "## 分段稳健性（前后半段，walk-forward 近似）", "",
              "| 成本(bps) | 分段 | 总收益 | 夏普 | bar数 |",
              "|--:|:--|--:|--:|--:|"]
    for r in split_rows:
        lines.append(
            f"| {r['cost_bps']:g} | {r['split']} | {r['total_return']} | "
            f"{r['sharpe']} | {r['n_bars']} |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"回测报告已输出: {out_path}", flush=True)

    # 净值 CSV
    if equity_curves:
        eq_df = pd.DataFrame(equity_curves)
        csv_path = REPORTS_DIR / f"phase2_equity_{date.today().isoformat()}.csv"
        eq_df.to_csv(csv_path, encoding="utf-8-sig")
        print(f"净值曲线已输出: {csv_path}（{len(eq_df)} 行）", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"回测失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
