"""
scripts/futures_signal_pipeline.py — 期货每日信号生成管道

从 FTS 期货 Elite 因子库，生成期货横截面交易信号。

用法:
    python scripts/futures_signal_pipeline.py [--max-symbols 25] [--days 120]

输出:
    - 控制台: 信号排名表
    - 文件:     reports/{date}/futures_signals_{date}.md

方向校正方法（v2）:
    期货是多空双向，因子在期货上的 IC 方向可能为负。
    校正方法：计算每个因子最近 N 天的**每日截面 IC**（因子信号与
    未来 5 日收益的 Spearman 秩相关性），如果平均 IC < 0 则反转信号。
    这比 v1 的时序相关性方法更符合横截面因子投资逻辑。
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

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"

# 期货 Elite 因子 trace_id（来自期货演化运行）
FUTURES_TRACE_ID = "l2_d3719690_20260803T101300"


def load_futures_elite_factors(ic_threshold: float = 0.3) -> list[dict[str, Any]]:
    """加载期货顶级 Elite 因子（按 trace_id 过滤，IC>{threshold}）。"""
    factors: list[dict[str, Any]] = []
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            ev = data.get("evaluation", {})
            tid = ev.get("trace_id", "")
            if tid != FUTURES_TRACE_ID:
                continue
            bt = ev.get("level_1_backtest", {})
            ic = bt.get("ic", 0)
            if abs(ic) < ic_threshold:
                continue
            factors.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return factors


def _compute_signal_matrix(
    panel: dict[str, "pd.DataFrame"],
    factors: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    """一次性计算所有因子 × 所有品种的信号矩阵。

    Returns:
        signal_matrix[symbol][factor_name] = np.ndarray (信号值时间序列)
    """
    from fts.factor_engine.factor_program import FactorExecutor

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0

    # 抑制因子编译/执行时的运行时警告（除零等，已通过 NaN 处理防御）
    warnings.filterwarnings("ignore", category=RuntimeWarning)

    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue
        sym_signals: dict[str, np.ndarray] = {}
        for factor_data in factors:
            name = factor_data.get("name", "?")
            try:
                executor = FactorExecutor(factor_data)
                sig = executor.execute(df, factor_data.get("params", {}))
                arr = np.array(sig, dtype=float)
                # 只保留有限数值
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


def _compute_factor_sign_flips(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
    ic_lookback: int = 20,
) -> dict[str, float]:
    """用截面 IC 法计算每个因子是否需要反转信号。

    方法：
        对每个因子，遍历最近 ic_lookback 个交易日，收集该日所有品种的
        因子信号值与未来 5 日收益率，计算 Spearman 秩相关性（截面 IC），
        取平均。如果平均 IC < 0，反转因子信号（flip = -1.0）。

    Args:
        signal_matrix: 信号矩阵 (symbol → factor_name → array)
        panel: 品种行情面板 (symbol → DataFrame)
        common_dates: 共同交易日列表（字符串格式）
        ic_lookback: 使用最近多少天的数据计算截面 IC

    Returns:
        dict[factor_name, sign_flip]  # +1=正常, -1=需反转
    """
    from scipy.stats import spearmanr

    # 获取所有因子名称
    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())

    n_dates = len(common_dates)
    # 多留 5 天给未来收益计算
    start_idx = max(0, n_dates - ic_lookback - 5)

    factor_sign_flips: dict[str, float] = {}
    for fname in factor_names:
        daily_ics: list[float] = []
        for t in range(start_idx, n_dates - 5):
            # 收集该日所有品种的信号值和未来 5 日收益
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

            # 计算截面 Spearman 相关性（抑制常量输入警告）
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


def _compute_composite_scores(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    factor_sign_flips: dict[str, float],
    factors: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """等权合成因子信号（带方向校正）。

    Returns:
        sym_scores: 品种 → 综合得分
        sym_details: 品种 → {因子名 → 信号值}
    """
    n_factors = len(factors)
    weight = 1.0 / n_factors

    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_data in factors:
            name = factor_data.get("name", "?")
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            # 方向校正
            flip = factor_sign_flips.get(name, 1.0)
            val *= flip
            signal_sum += val * weight
            weight_sum += weight
            details[name] = val

        if weight_sum > 0:
            composite = signal_sum / weight_sum
            sym_scores[sym] = composite
            sym_details[sym] = details

    return sym_scores, sym_details


def main(max_symbols: int = 25, days: int = 120) -> int:
    t0 = time.time()
    today = date.today().isoformat()
    print("=" * 60)
    print(f"  期货信号生成管道 v2 — {today}")
    print("=" * 60)

    # ── Step 1: 加载期货 Elite 因子 ──
    factors = load_futures_elite_factors()
    print(f"\n[1/4] 加载期货 Elite 因子: {len(factors)} 个")

    if not factors:
        print("[ERROR] 无期货 Elite 因子，退出")
        return 1

    # ── Step 2: 获取期货数据 ──
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_CORE_SUBSET

    provider = FTSDataProvider()

    symbols = FUTURES_CORE_SUBSET[:max_symbols]
    print(f"[2/4] 获取期货数据: {len(symbols)} 个品种, days={days}")

    panel, common_dates = provider.get_futures_panel(
        symbols=symbols, days=days,
    )
    print(f"      面板: {len(panel)} 个品种, {len(common_dates)} 个交易日")

    if not panel:
        print("[ERROR] 无数据，退出")
        return 1

    # ── Step 3: 计算信号 ──
    n_factors = len(factors)
    print(f"[3/4] 计算信号 ({n_factors} 因子 × {len(panel)} 品种)...")

    # 3a: 一次性计算所有因子×品种的信号矩阵
    signal_matrix = _compute_signal_matrix(panel, factors)
    print(f"      信号矩阵: {sum(len(v) for v in signal_matrix.values())} 项")

    # 3b: 方向校正（截面 IC 法）
    print("      方向校正: 截面 IC 法（因子信号 vs 未来 5 日收益的 Spearman 秩相关）...")
    factor_sign_flips = _compute_factor_sign_flips(signal_matrix, panel, common_dates)

    n_flipped = sum(1 for v in factor_sign_flips.values() if v < 0)
    if n_flipped > 0:
        print(f"      方向反转: {n_flipped}/{n_factors} 个因子 (截面 IC<0)")

    # 3c: 等权合成（带方向校正）
    sym_scores, sym_details = _compute_composite_scores(
        signal_matrix, factor_sign_flips, factors,
    )

    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s, 成功: {len(sym_scores)} 个品种")

    # ── Step 4: 输出信号排名 ──
    if not sym_scores:
        print("[ERROR] 无有效信号")
        return 1

    ranked = sorted(sym_scores.items(), key=lambda x: -x[1])

    # 控制台输出
    print(f"\n{'=' * 60}")
    print(f"  期货信号排名 Top 20")
    print(f"{'=' * 60}")
    print(f"{'排名':>4s} {'品种':>8s} {'综合得分':>10s} {'最新价':>10s} {'Top因子':>30s}")
    print(f"{'-'*4} {'-'*8} {'-'*10} {'-'*10} {'-'*30}")

    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        details = sym_details.get(sym, {})
        top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
        print(f"{i:>4d} {sym:>8s} {score:>+10.4f} {price:>10.2f} {top_str:<30s}")

    # 底部信号
    print(f"\n{'─' * 60}")
    print(f"  底部信号 Bottom 5")
    for i, (sym, score) in enumerate(ranked[-5:], len(ranked) - 4):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        print(f"  {i:>4d} {sym:>8s} {score:>+10.4f} {price:>10.2f}")

    # ── Step 5: 写入 Markdown 报告 ──
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"futures_signals_{today}.md"
    lines: list[str] = []
    def w(s=""):
        lines.append(s)

    w(f"# 期货信号报告 — {today}")
    w()
    w(f"生成时间: {today} | 耗时: {elapsed:.1f}s")
    w(f"因子池: {len(factors)} 个 | 覆盖品种: {len(sym_scores)} 个")
    flips_info = f" | 方向反转: {n_flipped} 个因子 (截面 IC<0)"
    w(f"方向校正: 截面 IC 法（因子信号 vs 未来 5 日收益的 Spearman 秩相关）{flips_info}")
    w()
    w("## 信号排名 Top 20")
    w()
    w("| 排名 | 品种 | 综合得分 | 最新价 | Top 3 因子贡献 |")
    w("|------|------|----------|--------|----------------|")
    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        details = sym_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} | {top_str} |")
    w()

    w("## 底部信号 Bottom 10")
    w()
    w("| 排名 | 品种 | 综合得分 | 最新价 |")
    w("|------|------|----------|--------|")
    for i, (sym, score) in enumerate(ranked[-10:], len(ranked) - 9):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} |")
    w()

    # 信号分布
    scores = [s for _, s in ranked]
    w("## 信号分布")
    w()
    w(f"- 均值: {np.mean(scores):+.4f}")
    w(f"- 中位数: {np.median(scores):+.4f}")
    w(f"- 标准差: {np.std(scores):.4f}")
    w(f"- 最大值: {max(scores):+.4f}")
    w(f"- 最小值: {min(scores):+.4f}")
    w(f"- 正信号占比: {sum(1 for s in scores if s > 0) / len(scores) * 100:.1f}%")
    w()

    # 因子贡献排名
    w("## 因子贡献排名（当前市场最有效的因子）")
    w()
    w("> 注：方向校正基于截面 IC。因子信号值已根据截面 IC 方向校正，")
    w("> IC<0 的因子信号已反转，使信号方向与未来收益方向一致。")
    w()
    factor_contribs: dict[str, list[float]] = {}
    for sym, details in sym_details.items():
        for name, val in details.items():
            if name not in factor_contribs:
                factor_contribs[name] = []
            factor_contribs[name].append(val)
    factor_avg = {n: np.mean(v) for n, v in factor_contribs.items()}
    factor_ranked = sorted(factor_avg.items(), key=lambda x: -abs(x[1]))[:20]
    w("| 排名 | 因子名称 | 平均信号值 | 标准差 |")
    w("|------|----------|------------|--------|")
    for i, (name, avg) in enumerate(factor_ranked, 1):
        std = np.std(factor_contribs[name])
        w(f"| {i} | {name} | {avg:+.4f} | {std:.4f} |")
    w()

    # 全部品种信号排名
    w("## 全部品种信号排名")
    w()
    w("| 排名 | 品种 | 综合得分 | 最新价 |")
    w("|------|------|----------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} |")
    w()

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_path}")

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="期货信号生成管道")
    parser.add_argument("--max-symbols", type=int, default=25, help="最大品种数")
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    args = parser.parse_args()
    sys.exit(main(max_symbols=args.max_symbols, days=args.days))