"""
scripts/futures_l3_portfolio.py — 期货 L3 投资组合构建

对 IC>0.3 的顶级因子做进一步组合优化，构建 L3 投资组合。

流程:
  1. 从 futures_elite 加载因子，按 name 去重，过滤 IC>0.3
  2. 正交化（剔除相关性 > 0.7 的因子）
  3. IC 加权合成
  4. Verifier 判定
  5. 输出组合信号 + 报告

用法:
    python scripts/futures_l3_portfolio.py [--mode ic_weight|sharpe_weight]
                                          [--ic-threshold 0.3]

输出:
    - 控制台: 组合统计 + 信号排名
    - 文件:     reports/{date}/futures_l3_portfolio_{date}.md
    - 文件:     reports/{date}/futures_l3_portfolio_{date}.json
    - 文件:     memory/portfolio/futures_combo.json
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FUTURES_ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"
PORTFOLIO_DIR = PROJECT_ROOT / "memory/portfolio"
DB_PATH = PROJECT_ROOT / "data/fts_history.duckdb"


# ─── 1. 加载因子（去重 + 过滤 IC>threshold）────────────────


def load_top_factors(ic_threshold: float = 0.3) -> list[dict[str, Any]]:
    """加载期货 Elite 因子，按 name 去重，过滤 IC>threshold。"""
    records: dict[str, dict[str, Any]] = {}
    for fp in sorted(FUTURES_ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            name = data.get("name", fp.stem)
            bt = data.get("evaluation", {}).get("level_1_backtest", {})
            ic = bt.get("ic", 0)

            if name not in records or abs(ic) > abs(records[name]["ic"]):
                records[name] = {
                    "data": data,
                    "ic": ic,
                    "sharpe": bt.get("sharpe", 0),
                    "t_stat": bt.get("t_stat", 0),
                    "max_dd": bt.get("max_drawdown", 0),
                }
        except (json.JSONDecodeError, OSError):
            continue

    factors = []
    for name, rec in records.items():
        if abs(rec["ic"]) >= ic_threshold:
            factors.append(
                {
                    "name": name,
                    "ic": rec["ic"],
                    "sharpe": rec["sharpe"],
                    "t_stat": rec["t_stat"],
                    "max_dd": rec["max_dd"],
                    "data": rec["data"],
                }
            )

    factors.sort(key=lambda f: -abs(f["ic"]))
    return factors


# ─── 2. 加载期货数据 ──────────────────────────────────────


def load_futures_data(
    min_periods: int = 252,
    end_date: str = "2026-07-31",
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """从 DuckDB 加载期货截面数据。"""
    import duckdb

    con = duckdb.connect(str(DB_PATH))
    symbols = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT symbol FROM kline_cache WHERE symbol NOT IN ('IC','IF','IH') ORDER BY symbol"
        ).fetchall()
    ]

    panel: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume, amount FROM kline_cache "
            "WHERE symbol = ? AND period = 'daily' ORDER BY date",
            [sym],
        ).fetchall()
        if len(rows) < min_periods:
            continue
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.astype(float)
        df = df[df.index <= end_date]
        panel[sym] = df

    con.close()

    all_dates = None
    for sym, df in panel.items():
        dates = set(df.index)
        all_dates = dates if all_dates is None else all_dates & dates

    common_dates = sorted(all_dates)
    common_dates_idx = pd.DatetimeIndex(common_dates)
    filtered: dict[str, pd.DataFrame] = {}
    for sym, df in panel.items():
        filtered[sym] = df.loc[common_dates]
    return filtered, common_dates_idx


# ─── 3. 信号计算 + 方向校正 ────────────────────────────────


def compute_signal_matrix(
    panel: dict[str, pd.DataFrame],
    factors: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    from fts.factor_engine.factor_program import FactorExecutor

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0
    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue
        sym_signals: dict[str, np.ndarray] = {}
        for f in factors:
            try:
                executor = FactorExecutor(f["data"])
                sig = executor.execute(df, f["data"].get("params", {}))
                arr = np.array(sig, dtype=float)
                arr = np.where(np.isfinite(arr), arr, np.nan)
                sym_signals[f["name"]] = arr
            except Exception:
                n_errors += 1
                continue
        if sym_signals:
            signal_matrix[sym] = sym_signals
    if n_errors > 0:
        print(f"      [警告] 信号计算错误: {n_errors} 次")
    return signal_matrix


def compute_direction_correction(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, pd.DataFrame],
    common_dates: list[str],
    ic_lookback: int = 60,
) -> dict[str, float]:
    """截面 IC 法方向校正 — 使用更长窗口（60天）提高稳健性。"""
    from scipy.stats import spearmanr

    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())

    n_dates = len(common_dates)
    start_idx = max(0, n_dates - ic_lookback - 5)

    factor_sign_flips: dict[str, float] = {}
    for fname in factor_names:
        daily_ics: list[float] = []
        for t in range(start_idx, n_dates - 5):
            signals_t: dict[str, float] = {}
            future_rets: dict[str, float] = {}
            for sym in signal_matrix:
                sig = signal_matrix[sym].get(fname)
                if sig is None or t >= len(sig) or not np.isfinite(sig[t]):
                    continue
                signals_t[sym] = float(sig[t])
                df = panel.get(sym)
                if df is None or df.empty:
                    continue
                closes = df["close"].values
                if t + 5 >= len(closes):
                    continue
                p_t = closes[t]
                if not np.isfinite(p_t) or p_t <= 1e-10:
                    continue
                ret = (closes[t + 5] - p_t) / p_t
                if np.isfinite(ret):
                    future_rets[sym] = ret

            common = set(signals_t.keys()) & set(future_rets.keys())
            if len(common) >= 5:
                s_vals = [signals_t[s] for s in common]
                r_vals = [future_rets[s] for s in common]
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    r, _ = spearmanr(s_vals, r_vals)
                if not np.isnan(r):
                    daily_ics.append(r)

        if daily_ics:
            avg_ic = np.mean(daily_ics)
            factor_sign_flips[fname] = -1.0 if avg_ic < 0 else 1.0
        else:
            factor_sign_flips[fname] = 1.0

    return factor_sign_flips


# ─── 4. 因子正交化 ────────────────────────────────────────


def orthogonalize_factors(
    factors: list[dict[str, Any]],
    signal_matrix: dict[str, dict[str, np.ndarray]],
    max_corr_threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """因子正交化 — 剔除高相关性因子中 IC 较低的。"""
    names = [f["name"] for f in factors]
    # 计算相关性矩阵（基于最新信号截面）
    corr_matrix = np.zeros((len(names), len(names)))
    for sym in signal_matrix:
        signals = signal_matrix[sym]
        row = []
        for name in names:
            sig = signals.get(name)
            row.append(sig[-1] if sig is not None and len(sig) > 0 and np.isfinite(sig[-1]) else np.nan)
        if all(np.isfinite(row)):
            for i, n1 in enumerate(names):
                for j, n2 in enumerate(names):
                    if i < j:
                        c = np.corrcoef(
                            [
                                signal_matrix[s].get(n1, [np.nan])[-1]
                                for s in signal_matrix
                                if signal_matrix[s].get(n1) is not None
                                and np.isfinite(signal_matrix[s].get(n1, [np.nan])[-1])
                            ],
                            [
                                signal_matrix[s].get(n2, [np.nan])[-1]
                                for s in signal_matrix
                                if signal_matrix[s].get(n2) is not None
                                and np.isfinite(signal_matrix[s].get(n2, [np.nan])[-1])
                            ],
                        )[0, 1]
                        if np.isfinite(c):
                            corr_matrix[i, j] = corr_matrix[j, i] = c

    # 按 IC 降序排列，剔除高相关性中 IC 较低的
    sorted_factors = sorted(factors, key=lambda f: -abs(f["ic"]))
    retained: list[str] = []
    dropped: list[str] = []

    for f in sorted_factors:
        fi = names.index(f["name"])
        should_keep = True
        for r in retained:
            rj = names.index(r)
            if abs(corr_matrix[fi, rj]) > max_corr_threshold:
                should_keep = False
                dropped.append(f["name"])
                break
        if should_keep:
            retained.append(f["name"])

    result = [f for f in factors if f["name"] in retained]
    print(f"      正交化: {len(factors)} → {len(result)} 个因子")
    if dropped:
        print(f"      剔除: {', '.join(dropped)}")

    return result


# ─── 5. 主函数 ────────────────────────────────────────────


def main(mode: str = "ic_weight", ic_threshold: float = 0.3) -> int:
    t0 = time.time()
    today = date.today().isoformat()

    print("=" * 60)
    print(f"  期货 L3 投资组合构建 — {today}")
    print(f"  合成模式: {mode} | IC 阈值: {ic_threshold}")
    print("=" * 60)

    # ── Step 1: 加载顶级因子 ──
    factors = load_top_factors(ic_threshold=ic_threshold)
    print(f"\n[1/6] 加载顶级因子: {len(factors)} 个（IC>{ic_threshold}）")
    for f in factors:
        print(f"      {f['name']:<30s} IC={f['ic']:.4f}  Sharpe={f['sharpe']:.2f}")

    if not factors:
        print("[ERROR] 无符合条件的因子")
        return 1

    # ── Step 2: 加载数据 ──
    print("\n[2/6] 加载期货数据...")
    panel, common_dates = load_futures_data()
    print(f"      品种: {len(panel)} 个, 交易日: {len(common_dates)} 天")

    # ── Step 3: 计算信号 ──
    print("\n[3/6] 计算信号...")
    signal_matrix = compute_signal_matrix(panel, factors)
    print(f"      有效品种: {len(signal_matrix)} 个")

    # ── Step 4: 正交化 ──
    print("\n[4/6] 因子正交化...")
    factors = orthogonalize_factors(factors, signal_matrix, max_corr_threshold=0.7)
    if len(factors) < 3:
        print("[ERROR] 正交化后因子数不足 3 个")
        return 1

    # ── Step 5: 方向校正 + IC加权合成 ──
    print("\n[5/6] 方向校正 (60天截面 IC)...")
    common_dates_str = [d.strftime("%Y-%m-%d") for d in common_dates]
    factor_sign_flips = compute_direction_correction(signal_matrix, panel, common_dates_str, ic_lookback=60)
    n_flipped = sum(1 for v in factor_sign_flips.values() if v < 0)
    print(f"      方向反转: {n_flipped}/{len(factors)} 个因子")

    # IC 加权合成
    weights: dict[str, float] = {}
    if mode == "ic_weight":
        total_ic = sum(abs(f["ic"]) + 0.001 for f in factors)
        for f in factors:
            weights[f["name"]] = (abs(f["ic"]) + 0.001) / total_ic
    else:  # sharpe_weight
        total_s = sum(max(f["sharpe"], 0.01) for f in factors)
        for f in factors:
            weights[f["name"]] = max(f["sharpe"], 0.01) / total_s

    print("\n[6/6] 合成综合得分...")
    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}
        for f in factors:
            name = f["name"]
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            val *= factor_sign_flips.get(name, 1.0)
            w = weights.get(name, 0)
            signal_sum += val * w
            weight_sum += w
            details[name] = val
        if weight_sum > 0:
            sym_scores[sym] = signal_sum / weight_sum
            sym_details[sym] = details

    elapsed = time.time() - t0
    ranked = sorted(sym_scores.items(), key=lambda x: -x[1])
    score_vals = [s for _, s in ranked]
    long_count = sum(1 for _, s in ranked if s > 0)
    short_count = sum(1 for _, s in ranked if s < 0)

    # ── 输出 ──
    print(f"\n{'=' * 60}")
    print("  L3 投资组合信号排名")
    print(f"{'=' * 60}")
    print(f"{'排名':>4s} {'品种':>8s} {'综合得分':>10s} {'方向':>6s} {'最新价':>10s} {'Top 因子':>32s}")
    print(f"{'-' * 4} {'-' * 8} {'-' * 10} {'-' * 6} {'-' * 10} {'-' * 32}")

    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        details = sym_details.get(sym, {})
        top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
        marker = " ★" if i <= 10 else ""
        print(f"{i:>4d} {sym:>8s} {score:>+10.4f} {direction:>6s} {price:>10.2f} {top_str:<32s}{marker}")

    # ── 策略统计 ──
    print(f"\n{'=' * 60}")
    print("  L3 投资组合统计")
    print(f"{'=' * 60}")
    print(f"  因子池:     {len(factors)} 个（IC>{ic_threshold} 正交化后）")
    print(f"  覆盖品种:   {len(sym_scores)} 个")
    print(f"  多头信号:   {long_count} 个")
    print(f"  空头信号:   {short_count} 个")
    print(f"  方向反转:   {n_flipped} 个因子")
    print(f"  综合得分均值: {np.mean(score_vals):+.4f}")
    print(f"  综合得分中位: {np.median(score_vals):+.4f}")
    print(f"  综合得分标准差: {np.std(score_vals):.4f}")
    print(f"  正信号占比:   {sum(1 for s in score_vals if s > 0) / len(score_vals) * 100:.1f}%")

    # ── 因子权重 ──
    print(f"\n{'─' * 60}")
    print(f"  因子权重排名 (mode={mode})")
    print(f"{'─' * 60}")
    for i, f in enumerate(factors, 1):
        w = weights.get(f["name"], 0)
        print(f"  {i:2d}. {f['name']:<30s} w={w:.4f}  IC={f['ic']:.4f}  Sharpe={f['sharpe']:.2f}")

    # ── 写入报告 ──
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)

    out_md = report_dir / f"futures_l3_portfolio_{today}.md"
    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    w(f"# 期货 L3 投资组合报告 — {today}")
    w()
    w(f"**合成模式**: {mode} | **IC 阈值**: {ic_threshold} | **耗时**: {elapsed:.1f}s")
    w(f"**因子池**: {len(factors)} 个（正交化后）| **覆盖品种**: {len(sym_scores)} 个")
    w(f"**方向反转**: {n_flipped} 个因子 | **多空比**: {long_count}/{short_count}")
    w()

    w("## 信号排名 Top 20")
    w("| 排名 | 品种 | 综合得分 | 方向 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|----------|------|--------|----------------|")
    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {score:+.4f} | {direction} | {price:.2f} | {top_str} |")
    w()

    w("## 信号分布")
    w(f"- 均值: {np.mean(score_vals):+.4f}")
    w(f"- 中位数: {np.median(score_vals):+.4f}")
    w(f"- 标准差: {np.std(score_vals):.4f}")
    w(f"- 最大值: {max(score_vals):+.4f}")
    w(f"- 最小值: {min(score_vals):+.4f}")
    w(f"- 正信号占比: {sum(1 for s in score_vals if s > 0) / len(score_vals) * 100:.1f}%")
    w()

    w("## 因子权重")
    w("| 排名 | 因子名称 | IC | Sharpe | 权重 |")
    w("|------|----------|----|--------|------|")
    for i, f in enumerate(factors, 1):
        w_val = weights.get(f["name"], 0)
        w(f"| {i} | {f['name']} | {f['ic']:.4f} | {f['sharpe']:.2f} | {w_val:.4f} |")
    w()

    w("## 全部品种信号排名")
    w("| 排名 | 品种 | 综合得分 | 方向 | 最新价 |")
    w("|------|------|----------|------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        w(f"| {i} | {sym} | {score:+.4f} | {direction} | {price:.2f} |")
    w()
    w("*报告由 FTS L3 投资组合自动生成 | FTS v1.6.0*")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_md}")

    # ── 写入 JSON ──
    out_json = report_dir / f"futures_l3_portfolio_{today}.json"
    signals_out = []
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "long" if score > 0 else "short"
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        signals_out.append(
            {
                "rank": i,
                "symbol": sym,
                "composite_score": round(score, 4),
                "direction": direction,
                "price": round(price, 2),
                "top_factors": [{"name": n, "value": round(v, 4)} for n, v in top3],
            }
        )

    json_output = {
        "date": today,
        "strategy": "futures_l3_portfolio",
        "synthesis_mode": mode,
        "n_factors": len(factors),
        "n_symbols": len(sym_scores),
        "n_long": long_count,
        "n_short": short_count,
        "n_flipped": n_flipped,
        "score_stats": {
            "mean": round(float(np.mean(score_vals)), 4),
            "median": round(float(np.median(score_vals)), 4),
            "std": round(float(np.std(score_vals)), 4),
            "positive_ratio": round(sum(1 for s in score_vals if s > 0) / len(score_vals), 4),
        },
        "factor_weights": {f["name"]: round(weights.get(f["name"], 0), 4) for f in factors},
        "signals": signals_out,
    }
    out_json.write_text(json.dumps(json_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 信号 JSON 已保存: {out_json}")

    # ── 写入组合配置（可被下游消费） ──
    combo = {
        "version": "1.6.0",
        "updated_at": today,
        "combo_id": f"fut_l3_{today.replace('-', '')}",
        "synthesis_mode": mode,
        "n_factors": len(factors),
        "n_symbols": len(sym_scores),
        "factor_weights": {f["name"]: round(weights.get(f["name"], 0), 4) for f in factors},
        "combo_sharpe": round(np.mean([f["sharpe"] for f in factors]), 2),
        "status": "active",
        "created_at": today,
    }
    combo_fp = PORTFOLIO_DIR / "futures_combo.json"
    combo_fp.write_text(json.dumps(combo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 组合配置已保存: {combo_fp}")

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="期货 L3 投资组合构建")
    parser.add_argument("--mode", default="ic_weight", choices=["ic_weight", "sharpe_weight"])
    parser.add_argument("--ic-threshold", type=float, default=0.3, help="IC 过滤阈值")
    args = parser.parse_args()
    sys.exit(main(mode=args.mode, ic_threshold=args.ic_threshold))
