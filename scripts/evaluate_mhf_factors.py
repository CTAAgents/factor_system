"""
scripts/evaluate_mhf_factors.py — 阶段1：分钟因子真实数据评估。

从合格品种池（mhf_pool.json）拉取 5m/15m 分钟数据（TDX 17709，绕过 DuckDB），
计算 MHF 因子族并评估各因子 × 周期的 IC/IR/胜率/显著性，输出评估报告。

输出:
    reports/mhf/phase1_factor_evaluation_{date}.md

用法:
    python scripts/evaluate_mhf_factors.py [--horizon-5m 5] [--horizon-15m 4] [--max-symbols 22]

设计约束:
    - 纯读取 + 内存计算，不写 DuckDB
    - 因子零未来（mhf_factors 保证），评估前视收益仅用于 IC 对齐
    - 多品种 IC 聚合：品种内时序 IC → 跨品种均值/分散度
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_sources.tdx_local_source import TdxLocalSource  # noqa: E402
from fts.factor_engine.mhf_evaluation import (  # noqa: E402
    evaluate_factor,
)
from fts.factor_engine.mhf_factors import MhfFactorConfig, compute_mhf_factors  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"
POOL_CACHE = PROJECT_ROOT / "memory" / "portfolio" / "futures" / "mhf_pool.json"
TRACE_ID: str = f"mhf_eval_{date.today().isoformat()}"
MINUTE_BARS: int = 6000  # 5m ≈ 4 个月，足够因子窗口与 IC 统计，控制拉取耗时


def _load_pool() -> list[str]:
    """读取合格品种池（缺失回退动态池读取）。"""
    try:
        payload = json.loads(POOL_CACHE.read_text(encoding="utf-8"))
        pool = payload.get("pool") or []
        if pool:
            return list(pool)
    except Exception:  # noqa: BLE001
        pass
    from fts.data_futures import get_dynamic_core_subset

    return list(get_dynamic_core_subset())


def _fetch(sym: str, src: TdxLocalSource) -> pd.DataFrame:
    """拉取分钟数据，异常/超时返回空 DataFrame。"""
    try:
        df = src.fetch_ohlcv(sym, MINUTE_BARS, trace_id=TRACE_ID)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] {sym} 拉取失败: {type(e).__name__} {e}", flush=True)
        return pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段1 分钟因子评估")
    parser.add_argument("--horizon-5m", type=int, default=5, help="5m 前视周期（bar，默认5≈25分钟）")
    parser.add_argument("--horizon-15m", type=int, default=4, help="15m 前视周期（bar，默认4≈1小时）")
    parser.add_argument("--max-symbols", type=int, default=22, help="最多评估品种数")
    args = parser.parse_args()

    pool = _load_pool()[: args.max_symbols]
    print(f"品种池: {len(pool)}  trace_id={TRACE_ID}", flush=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    src5 = TdxLocalSource(period="5m")
    src15 = TdxLocalSource(period="15m")
    cfg = MhfFactorConfig()

    # 收集：{(factor, freq): {symbol: IcSummary}}
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for i, sym in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {sym} ...")
        t0 = time.time()
        for freq, src, hz in (("5m", src5, args.horizon_5m), ("15m", src15, args.horizon_15m)):
            df = _fetch(sym, src)
            if df.empty:
                continue
            factors = compute_mhf_factors(df, cfg)
            for name, f in factors.items():
                s = evaluate_factor(f, df["close"], horizon=hz)
                results.setdefault((name, freq), {})[sym] = s
        print(f"  {time.time()-t0:.1f}s")

    # ── 报告 ──
    out_path = REPORTS_DIR / f"phase1_factor_evaluation_{date.today().isoformat()}.md"
    lines: list[str] = [
        "# 阶段1 分钟因子评估报告（中高频策略）",
        "",
        f"- 日期: {date.today().isoformat()}  trace_id: `{TRACE_ID}`",
        f"- 品种: {len(pool)}（合格池）  数据源: TDX 17709",
        f"- 前视周期: 5m={args.horizon_5m}bar, 15m={args.horizon_15m}bar",
        f"- 因子数: {len(set(k[0] for k in results))} × 周期组合: {len(results)}",
        "",
        "## 因子 × 周期 IC 汇总（跨品种）",
        "",
        "| 因子 | 周期 | 品种数 | 平均IC | IC分散度 | IR均值 | 正IC胜率 | 显著品种(|t|>2) |",
        "|:--|:--|--:|--:|--:|--:|--:|--:|",
    ]
    ranked: list[tuple[tuple[str, str], Any]] = []
    for key, per_sym in results.items():
        name, freq = key
        if not per_sym:
            continue
        ic_means = np.array([s.ic_mean for s in per_sym.values()])
        irs = np.array([s.ir for s in per_sym.values()])
        sig = sum(1 for s in per_sym.values() if abs(s.t_stat) > 2)
        avg_ic = float(ic_means.mean())
        ranked.append((key, dict(
            n=len(ic_means), avg_ic=avg_ic, ic_std=float(ic_means.std(ddof=1)),
            ir_mean=float(irs.mean()), win=float((ic_means > 0).mean()), sig=sig,
        )))
    ranked.sort(key=lambda kv: -abs(kv[1]["avg_ic"]))
    for (name, freq), r in ranked:
        lines.append(
            f"| {name} | {freq} | {r['n']} | {r['avg_ic']:.4f} | {r['ic_std']:.4f} | "
            f"{r['ir_mean']:.3f} | {r['win']:.2%} | {r['sig']}/{r['n']} |"
        )
    lines += ["", "## 品种内最强因子（单品种最高 |IC|，前 20）", "",
              "| 品种 | 因子 | 周期 | IC均值 | IR | t值 |", "|:--|:--|:--|--:|--:|--:|"]
    per_sym_best: list[tuple[float, dict[str, Any]]] = []
    for (name, freq), per_sym in results.items():
        for sym, s in per_sym.items():
            per_sym_best.append((abs(s.ic_mean), {
                "sym": sym, "factor": name, "freq": freq,
                "ic": s.ic_mean, "ir": s.ir, "t": s.t_stat,
            }))
    per_sym_best.sort(key=lambda kv: -kv[0])
    for _, r in per_sym_best[:20]:
        lines.append(
            f"| {r['sym']} | {r['factor']} | {r['freq']} | {r['ic']:.4f} | "
            f"{r['ir']:.3f} | {r['t']:.2f} |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"评估报告已输出: {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"评估失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
