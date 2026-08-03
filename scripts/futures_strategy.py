"""
scripts/futures_strategy.py — 期货因子组合策略信号生成

将顶级 Elite 因子（IC>0.3，IC加权合成）组合成统一的交易信号，
输出多空排名和信号报告。

流程:
  1. 加载期货顶级 Elite 因子（IC>0.3，从 futures_elite 目录，按 name 去重，保留最高 IC）
  2. 从 DuckDB 加载期货品种日线数据
  3. 计算每个因子 × 每个品种的信号
  4. 截面 IC 方向校正
  5. IC 加权合成综合得分
  6. 输出排名 + 信号报告

用法:
    python scripts/futures_strategy.py [--mode ic_weight|sharpe_weight|equal_weight]
                                       [--top-n 10]

输出:
    - 控制台: 信号排名表 + 策略统计
    - 文件:     reports/{date}/futures_strategy_{date}.md
    - 文件:     reports/{date}/futures_strategy_{date}.json
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
DB_PATH = PROJECT_ROOT / "data/fts_history.duckdb"


# ─── 1. 加载 Elite 因子（去重）────────────────────────────

def load_elite_factors(ic_threshold: float = 0.3) -> list[dict[str, Any]]:
    """加载期货顶级 Elite 因子，按 name 去重，保留 IC>threshold 的顶级因子。"""
    import duckdb  # noqa: F401 — 确保 duckdb 可用

    records: dict[str, dict[str, Any]] = {}
    for fp in sorted(FUTURES_ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            name = data.get("name", fp.stem)
            bt = data.get("evaluation", {}).get("level_1_backtest", {})
            ic = bt.get("ic", 0)

            # 去重：保留 IC 最高的
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
            factors.append({
                "name": name,
                "ic": rec["ic"],
                "sharpe": rec["sharpe"],
                "t_stat": rec["t_stat"],
                "max_dd": rec["max_dd"],
                "data": rec["data"],
            })

    # 按 IC 降序排列
    factors.sort(key=lambda f: -abs(f["ic"]))
    return factors


# ─── 2. 加载期货数据 ──────────────────────────────────────

def load_futures_data(
    min_periods: int = 252,
    end_date: str = "2026-07-31",
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """从 DuckDB 加载期货截面数据。

    Returns:
        panel: symbol → OHLCV DataFrame
        common_dates: 共有交易日索引
    """
    import duckdb

    con = duckdb.connect(str(DB_PATH))

    symbols = [
        r[0] for r in con.execute(
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

    # 共有日期
    all_dates = None
    for sym, df in panel.items():
        dates = set(df.index)
        all_dates = dates if all_dates is None else all_dates & dates

    common_dates = sorted(all_dates)
    common_dates_idx = pd.DatetimeIndex(common_dates)

    # 过滤到共有日期
    filtered: dict[str, pd.DataFrame] = {}
    for sym, df in panel.items():
        filtered[sym] = df.loc[common_dates]
    panel = filtered

    return panel, common_dates_idx


# ─── 3. 信号计算 ──────────────────────────────────────────

def compute_signal_matrix(
    panel: dict[str, pd.DataFrame],
    factors: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    """计算所有因子 × 所有品种的信号矩阵。"""
    from fts.factor_engine.factor_program import FactorExecutor

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0

    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue
        sym_signals: dict[str, np.ndarray] = {}
        for factor_info in factors:
            name = factor_info["name"]
            try:
                executor = FactorExecutor(factor_info["data"])
                sig = executor.execute(df, factor_info["data"].get("params", {}))
                arr = np.array(sig, dtype=float)
                arr = np.where(np.isfinite(arr), arr, np.nan)
                sym_signals[name] = arr
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
    ic_lookback: int = 20,
) -> dict[str, float]:
    """截面 IC 法方向校正 — 计算每个因子是否需要反转信号。"""
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


# ─── 4. 策略合成 ──────────────────────────────────────────

def compute_composite_scores(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    factor_sign_flips: dict[str, float],
    factors: list[dict[str, Any]],
    mode: str = "ic_weight",
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """合成综合得分。

    Modes:
        - ic_weight:   按 |IC| 加权（IC 越高权重越大）
        - sharpe_weight: 按 Sharpe 加权
        - equal_weight: 等权
    """
    # 计算权重
    weights: dict[str, float] = {}
    if mode == "ic_weight":
        total_ic = sum(abs(f["ic"]) + 0.001 for f in factors)  # 避免除零
        for f in factors:
            weights[f["name"]] = (abs(f["ic"]) + 0.001) / total_ic
    elif mode == "sharpe_weight":
        total_s = sum(max(f["sharpe"], 0.01) for f in factors)
        for f in factors:
            weights[f["name"]] = max(f["sharpe"], 0.01) / total_s
    else:  # equal_weight
        n = len(factors)
        for f in factors:
            weights[f["name"]] = 1.0 / n

    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_info in factors:
            name = factor_info["name"]
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            flip = factor_sign_flips.get(name, 1.0)
            val *= flip
            w = weights.get(name, 0)
            signal_sum += val * w
            weight_sum += w
            details[name] = val

        if weight_sum > 0:
            sym_scores[sym] = signal_sum / weight_sum
            sym_details[sym] = details

    return sym_scores, sym_details


# ─── 5. 主函数 ────────────────────────────────────────────

def main(mode: str = "ic_weight", top_n: int = 10) -> int:
    t0 = time.time()
    today = date.today().isoformat()

    print("=" * 60)
    print(f"  期货因子组合策略信号 — {today}")
    print(f"  合成模式: {mode}")
    print("=" * 60)

    # ── Step 1: 加载顶级因子（IC>0.3，去重） ──
    factors = load_elite_factors(ic_threshold=0.3)
    print(f"\n[1/5] 加载 Elite 因子: {len(factors)} 个（去重后）")
    for i, f in enumerate(factors, 1):
        print(f"      {i:2d}. {f['name']:<30s} IC={f['ic']:.4f}  Sharpe={f['sharpe']:.2f}")

    if not factors:
        print("[ERROR] 无因子，退出")
        return 1

    # ── Step 2: 加载期货数据 ──
    print(f"\n[2/5] 加载期货数据...")
    panel, common_dates = load_futures_data()
    print(f"      品种: {len(panel)} 个, 交易日: {len(common_dates)} 天")
    print(f"      日期范围: {common_dates[0]} ~ {common_dates[-1]}")

    # ── Step 3: 计算信号 ──
    print(f"\n[3/5] 计算信号 ({len(factors)} 因子 × {len(panel)} 品种)...")
    signal_matrix = compute_signal_matrix(panel, factors)
    n_valid_symbols = len(signal_matrix)
    print(f"      有效品种: {n_valid_symbols} 个")

    # ── Step 4: 方向校正 + 合成 ──
    print(f"\n[4/5] 方向校正 (截面 IC 法)...")
    common_dates_str = [d.strftime("%Y-%m-%d") for d in common_dates]
    factor_sign_flips = compute_direction_correction(signal_matrix, panel, common_dates_str)
    n_flipped = sum(1 for v in factor_sign_flips.values() if v < 0)
    print(f"      方向反转: {n_flipped}/{len(factors)} 个因子")

    print(f"\n[5/5] 合成综合得分 (mode={mode})...")
    sym_scores, sym_details = compute_composite_scores(
        signal_matrix, factor_sign_flips, factors, mode=mode,
    )

    elapsed = time.time() - t0
    print(f"      耗时: {elapsed:.1f}s, 有效品种: {len(sym_scores)} 个")

    # ── 输出信号排名 ──
    ranked = sorted(sym_scores.items(), key=lambda x: -x[1])

    # === 策略信号 ===
    print(f"\n{'=' * 60}")
    print(f"  📊 期货多空信号排名")
    print(f"{'=' * 60}")
    print(f"{'排名':>4s} {'品种':>8s} {'综合得分':>10s} {'方向':>6s} {'最新价':>10s} {'Top 因子':>32s}")
    print(f"{'-'*4} {'-'*8} {'-'*10} {'-'*6} {'-'*10} {'-'*32}")

    long_count = 0
    short_count = 0
    score_vals = [s for _, s in ranked]

    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        if score > 0:
            long_count += 1
        else:
            short_count += 1

        details = sym_details.get(sym, {})
        top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)

        # Top N 高亮
        marker = " ★" if i <= top_n else (" ☆" if i <= top_n * 2 else "")
        print(f"{i:>4d} {sym:>8s} {score:>+10.4f} {direction:>6s} {price:>10.2f} {top_str:<32s}{marker}")

    # 底部信号
    print(f"\n{'─' * 60}")
    print(f"  底部信号 Bottom 5（最强空头信号）")
    print(f"{'─' * 60}")
    print(f"{'排名':>4s} {'品种':>8s} {'综合得分':>10s} {'方向':>6s} {'最新价':>10s}")
    for i, (sym, score) in enumerate(ranked[-5:], len(ranked) - 4):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        # 底部也是看空
        print(f"  {i:>4d} {sym:>8s} {score:>+10.4f} {direction:>6s} {price:>10.2f}")

    # ── 策略统计 ──
    print(f"\n{'=' * 60}")
    print(f"  策略统计")
    print(f"{'=' * 60}")
    print(f"  因子池:     {len(factors)} 个")
    print(f"  覆盖品种:   {len(sym_scores)} 个")
    print(f"  多头信号:   {long_count} 个")
    print(f"  空头信号:   {short_count} 个")
    print(f"  方向反转:   {n_flipped} 个因子")
    print(f"  综合得分均值: {np.mean(score_vals):+.4f}")
    print(f"  综合得分中位: {np.median(score_vals):+.4f}")
    print(f"  综合得分标准差: {np.std(score_vals):.4f}")
    print(f"  正信号占比: {sum(1 for s in score_vals if s > 0) / len(score_vals) * 100:.1f}%")

    # ── 因子权重排名 ──
    print(f"\n{'─' * 60}")
    print(f"  因子权重排名 (mode={mode})")
    print(f"{'─' * 60}")
    total_w = sum(abs(f["ic"]) + 0.001 for f in factors) if mode == "ic_weight" else 1.0
    for i, f in enumerate(factors, 1):
        if mode == "ic_weight":
            w = (abs(f["ic"]) + 0.001) / total_w
        elif mode == "sharpe_weight":
            total_s = sum(max(f["sharpe"], 0.01) for f in factors)
            w = max(f["sharpe"], 0.01) / total_s
        else:
            w = 1.0 / len(factors)
        print(f"  {i:2d}. {f['name']:<30s} w={w:.4f}  IC={f['ic']:.4f}  Sharpe={f['sharpe']:.2f}")

    # ── 写入 Markdown 报告 ──
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)
    out_md = report_dir / f"futures_strategy_{today}.md"

    lines: list[str] = []
    def w(s=""):
        lines.append(s)

    w(f"# 期货因子组合策略信号报告 — {today}")
    w()
    w(f"**合成模式**: {mode} | **生成时间**: {today} | **耗时**: {elapsed:.1f}s")
    w(f"**因子池**: {len(factors)} 个 | **覆盖品种**: {len(sym_scores)} 个")
    w(f"**方向反转**: {n_flipped} 个因子 | **多空比**: {long_count}/{short_count}")
    w()

    w("## 信号排名 Top 20")
    w()
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

    w("## 底部信号 Bottom 10")
    w()
    w("| 排名 | 品种 | 综合得分 | 方向 | 最新价 |")
    w("|------|------|----------|------|--------|")
    for i, (sym, score) in enumerate(ranked[-10:], len(ranked) - 9):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        w(f"| {i} | {sym} | {score:+.4f} | {direction} | {price:.2f} |")
    w()

    w("## 信号分布")
    w()
    w(f"- 均值: {np.mean(score_vals):+.4f}")
    w(f"- 中位数: {np.median(score_vals):+.4f}")
    w(f"- 标准差: {np.std(score_vals):.4f}")
    w(f"- 最大值: {max(score_vals):+.4f}")
    w(f"- 最小值: {min(score_vals):+.4f}")
    w(f"- 正信号占比: {sum(1 for s in score_vals if s > 0) / len(score_vals) * 100:.1f}%")
    w()

    w("## 因子权重 (IC加权)")
    w()
    w("| 排名 | 因子名称 | IC | Sharpe | 权重 |")
    w("|------|----------|----|--------|------|")
    for i, f in enumerate(factors, 1):
        if mode == "ic_weight":
            w_val = (abs(f["ic"]) + 0.001) / total_w
        elif mode == "sharpe_weight":
            total_s = sum(max(f["sharpe"], 0.01) for f in factors)
            w_val = max(f["sharpe"], 0.01) / total_s
        else:
            w_val = 1.0 / len(factors)
        w(f"| {i} | {f['name']} | {f['ic']:.4f} | {f['sharpe']:.2f} | {w_val:.4f} |")
    w()

    w("## 因子贡献排名")
    w()
    factor_contribs: dict[str, list[float]] = {}
    for sym, details in sym_details.items():
        for name, val in details.items():
            factor_contribs.setdefault(name, []).append(val)
    factor_avg = {n: np.mean(v) for n, v in factor_contribs.items()}
    factor_ranked = sorted(factor_avg.items(), key=lambda x: -abs(x[1]))
    w("| 排名 | 因子名称 | 平均信号值 | 标准差 |")
    w("|------|----------|------------|--------|")
    for i, (name, avg) in enumerate(factor_ranked, 1):
        std = np.std(factor_contribs[name])
        w(f"| {i} | {name} | {avg:+.4f} | {std:.4f} |")
    w()

    w("## 全部品种信号排名")
    w()
    w("| 排名 | 品种 | 综合得分 | 方向 | 最新价 |")
    w("|------|------|----------|------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "LONG" if score > 0 else "SHORT"
        w(f"| {i} | {sym} | {score:+.4f} | {direction} | {price:.2f} |")
    w()

    w("---")
    w(f"*报告由 FTS 期货因子组合策略自动生成 | FTS v1.6.0*")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_md}")

    # ── 写入 JSON 信号（可直接被下游系统消费） ──
    out_json = report_dir / f"futures_strategy_{today}.json"
    signals_out = []
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        direction = "long" if score > 0 else "short"
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        signals_out.append({
            "rank": i,
            "symbol": sym,
            "composite_score": round(score, 4),
            "direction": direction,
            "price": round(price, 2),
            "top_factors": [{"name": n, "value": round(v, 4)} for n, v in top3],
        })

    json_output = {
        "date": today,
        "strategy": "futures_multi_factor",
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
        "signals": signals_out,
    }
    out_json.write_text(json.dumps(json_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 信号 JSON 已保存: {out_json}")

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="期货因子组合策略信号生成")
    parser.add_argument("--mode", default="ic_weight",
                        choices=["ic_weight", "sharpe_weight", "equal_weight"],
                        help="合成模式")
    parser.add_argument("--top-n", type=int, default=10, help="高亮 Top N 品种")
    args = parser.parse_args()
    sys.exit(main(mode=args.mode, top_n=args.top_n))