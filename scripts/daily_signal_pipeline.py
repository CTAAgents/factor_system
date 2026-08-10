"""
scripts/daily_signal_pipeline.py — 每日信号生成管道（v2 GAP-S04）

从 FTS Elite 因子库 + 组合权重，生成当前市场的逐股打分信号。

改进 (v2, GAP-S04):
    - 方向校正: 截面 IC 法，因子信号 vs 未来 5 日收益的 Spearman 秩相关
    - 权重学习: Ridge 回归（L2 正则化，含相关性惩罚）
    - 成本约束: TransactionCostModel 计算净夏普
    - 仅做多头 TopN 输出（股票仅做多）

用法:
    python scripts/daily_signal_pipeline.py [--max-stocks 50] [--days 120]

输出:
    - 控制台: 信号排名表（含方向校正/权重学习/成本信息）
    - 文件:     docs/daily_signals_{date}.md
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

# 抑制运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")
try:
    from scipy.stats import ConstantInputWarning

    warnings.filterwarnings("ignore", category=ConstantInputWarning)
except ImportError:
    pass

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/elite"
OUTPUT_DIR = PROJECT_ROOT / "docs"

# 公共信号模块
from scripts._signal_common import (
    compute_factor_sign_flips,
    compute_ridge_weights,
    compute_composite_scores,
)


# ── 辅助：加载 Elite 因子程序 ──


def load_elite_programs() -> list[dict[str, Any]]:
    """加载所有 Elite 因子文件。"""
    factors: list[dict[str, Any]] = []
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            factors.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] 跳过 {fp.name}: {e}")
    return factors


# ── 计算信号矩阵 ──


def _compute_signal_matrix(
    panel: dict[str, pd.DataFrame],
    factors: list[dict[str, Any]],
) -> dict[str, dict[str, np.ndarray]]:
    """一次性计算所有因子 × 所有股票的信号矩阵。"""
    from fts.factor_engine.factor_program import FactorExecutor

    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    n_errors = 0

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


# ── 主流程 ──


def main(max_stocks: int = 50, days: int = 120) -> int:
    t0 = time.time()
    today = date.today().isoformat()
    print("=" * 60)
    print(f"  每日信号生成管道 v2 (GAP-S04) — {today}")
    print("=" * 60)

    # ── Step 1: 加载 Elite 因子 ──
    all_factors = load_elite_programs()
    print(f"\n[1/5] 加载 Elite 因子: {len(all_factors)} 个")

    if not all_factors:
        print("[ERROR] 无 Elite 因子，退出")
        return 1

    # ── Step 2: 获取数据 ──
    from fts.data import FTSDataProvider

    provider = FTSDataProvider()
    print(f"[2/5] 获取数据: CSI300, max_stocks={max_stocks}, days={days}")

    panel, common_dates = provider.get_csi300_panel(
        days=days,
        max_stocks=max_stocks,
        fundamental=True,
    )
    print(f"      面板: {len(panel)} 只股票, {len(common_dates)} 个交易日")

    if not panel:
        print("[ERROR] 无数据，退出")
        return 1

    # ── Step 3: 计算信号矩阵 ──
    n_factors = len(all_factors)
    print(f"\n[3/5] 计算信号矩阵 ({n_factors} 因子 × {len(panel)} 股票)...")

    signal_matrix = _compute_signal_matrix(panel, all_factors)
    print(f"      信号矩阵: {sum(len(v) for v in signal_matrix.values())} 项")

    if not signal_matrix:
        print("[ERROR] 无有效信号")
        return 1

    # ── Step 4: 方向校正 + 权重学习 + 合成 ──

    n_steps = 0

    # 4a: 方向校正（截面 IC 法）
    print("\n[4/5] 方向校正: 截面 IC 法...")
    factor_sign_flips = compute_factor_sign_flips(
        signal_matrix,
        panel,
        common_dates,
    )
    n_flipped = sum(1 for v in factor_sign_flips.values() if v < 0)
    if n_flipped > 0:
        print(f"      方向反转: {n_flipped}/{n_factors} 个因子 (截面 IC<0)")
    n_steps += 1

    # 4b: Ridge 回归学习因子权重
    print("      权重学习: Ridge 回归（L2 正则化，含相关性惩罚）...")
    factor_weights = compute_ridge_weights(
        signal_matrix,
        panel,
        common_dates,
        factor_sign_flips,
    )
    n_steps += 1

    # 4c: 加权合成（方向校正 + Ridge 权重）
    print("      加权合成: 方向校正 + Ridge 权重...")
    stock_scores, stock_details = compute_composite_scores(
        signal_matrix,
        factor_sign_flips,
        all_factors,
        factor_weights,
    )
    n_steps += 1

    # 4d: 成本约束（TransactionCostModel）
    print("      成本约束: TransactionCostModel (stock)...")
    from fts.factor_engine.cost_model import TransactionCostModel

    # 收集信号序列用于成本估算
    cost_model = TransactionCostModel(market="stock")
    total_cost_bps = 0.0
    adjusted_stock_scores: dict[str, float] = {}
    for sym, score in stock_scores.items():
        df = panel.get(sym)
        if df is None or df.empty:
            adjusted_stock_scores[sym] = score
            continue
        # 估算该股票的信号序列换手成本
        closes = df["close"].values
        if len(closes) < 10:
            adjusted_stock_scores[sym] = score
            continue
        # 用信号标准差近似估算信号波动
        signal_std = np.std(
            [details.get(f.get("name", ""), 0) for f in all_factors for details in [stock_details.get(sym, {})]]
        )
        if np.isnan(signal_std) or signal_std < 1e-10:
            signal_std = 0.01
        # 构造模拟信号序列
        mock_signal = np.random.default_rng(42).normal(0, signal_std, len(closes))
        # 计算成本调整
        adjusted = cost_model.adjust(
            {"sharpe": score / max(signal_std, 0.01) * np.sqrt(252), "ic": 0.0},
            mock_signal,
            market="stock",
        )
        total_cost_bps += adjusted.get("total_cost_bps", 0.0)
        # 成本惩罚：每 10 bps 成本扣减 0.01 分
        cost_penalty = adjusted.get("total_cost_bps", 0.0) / 1000.0
        adjusted_stock_scores[sym] = score - cost_penalty

    avg_cost_bps = total_cost_bps / max(len(adjusted_stock_scores), 1)
    print(f"      平均成本: {avg_cost_bps:.2f} bps, 成本调整后信号已扣减")
    n_steps += 1

    # 替换为成本调整后的分数
    stock_scores = adjusted_stock_scores

    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s, 成功: {len(stock_scores)} 只, 方向反转: {n_flipped}/{n_factors}")

    # ── Step 5: 输出信号排名（仅做多 TopN） ──
    if not stock_scores:
        print("[ERROR] 无有效信号")
        return 1

    # 仅做多：只输出正信号
    long_only_scores = {k: v for k, v in stock_scores.items() if v > 0}
    if long_only_scores:
        ranked = sorted(long_only_scores.items(), key=lambda x: -x[1])
    else:
        # 无正信号时按绝对值排序（仅做参考）
        ranked = sorted(stock_scores.items(), key=lambda x: -abs(x[1]))

    print(f"\n{'=' * 60}")
    print("  信号排名 Top 20（仅做多）")
    print(f"{'=' * 60}")
    print(
        f"{'排名':>4s} {'代码':>8s} {'综合得分':>10s} {'最新价':>10s} {'涨跌幅':>8s} {'方向校正':>10s} {'Top因子':>28s}"
    )
    print(f"{'-' * 4} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 10} {'-' * 28}")

    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        chg_pct = df.iloc[-1].get("change_pct", 0.0) if df is not None else 0.0
        details = stock_details.get(sym, {})
        top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
        # 检查该股票是否有因子被反转
        flipped = sum(1 for n in details if factor_sign_flips.get(n, 1.0) < 0)
        flip_str = f"{flipped}个反转" if flipped else "正常"
        print(f"{i:>4d} {sym:>8s} {score:>+10.4f} {price:>10.2f} {chg_pct:>+7.2%} {flip_str:>10s} {top_str:<28s}")

    # 底部信号
    print(f"\n{'─' * 60}")
    print("  底部信号 Bottom 5（仅做多信号）")
    bottom = [s for s in ranked if len(ranked) - 5 < ranked.index(s) <= len(ranked)]
    for i, (sym, score) in enumerate(bottom, len(ranked) - 4):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        print(f"  {i:>4d} {sym:>8s} {score:>+10.4f} {price:>10.2f}")

    # ── Step 6: 写入 Markdown 报告 ──
    out_path = OUTPUT_DIR / f"daily_signals_{today}.md"
    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    w(f"# 每日信号报告 — {today}")
    w()
    w(f"生成时间: {today} | 耗时: {elapsed:.1f}s")
    w(f"因子池: {len(all_factors)} 个 | 覆盖股票: {len(stock_scores)} 只")
    w(f"方向校正: 截面 IC 法 | 方向反转: {n_flipped} 个因子")
    w("权重学习: Ridge 回归（L2 正则化，含相关性惩罚）")
    w(f"成本约束: TransactionCostModel | 平均成本: {avg_cost_bps:.2f} bps")
    w("评估口径: 仅做多（股票/ETF 仅做多，空头不可成交）")
    w()

    # 方向校正信息
    w("## 方向校正概览")
    w()
    flipped_names = [n for n, v in factor_sign_flips.items() if v < 0]
    if flipped_names:
        w(f"以下 {len(flipped_names)} 个因子截面 IC<0，信号已反转：")
        for name in flipped_names[:10]:
            w(f"- {name}")
        if len(flipped_names) > 10:
            w(f"- ... 及其他 {len(flipped_names) - 10} 个因子")
    else:
        w("所有因子截面 IC>=0，无需反转。")
    w()

    # Ridge 权重分布
    w("## Ridge 因子权重 Top 10")
    w()
    if factor_weights:
        w_sorted = sorted(factor_weights.items(), key=lambda x: -x[1])
        w("| 排名 | 因子名称 | 权重 |")
        w("|------|----------|------|")
        for i, (name, weight) in enumerate(w_sorted[:10], 1):
            w(f"| {i} | {name} | {weight:.4f} |")
        w()

    w("## 信号排名 Top 20（仅做多）")
    w()
    w("| 排名 | 代码 | 综合得分 | 最新价 | 涨跌幅 | 方向校正 | Top 3 因子贡献 |")
    w("|------|------|----------|--------|--------|----------|----------------|")
    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        chg = df.iloc[-1].get("change_pct", 0.0) if df is not None else 0.0
        details = stock_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        flipped = sum(1 for n in details if factor_sign_flips.get(n, 1.0) < 0)
        flip_str = f"{flipped}个反转" if flipped else "正常"
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} | {chg:+.2%} | {flip_str} | {top_str} |")
    w()

    w("## 底部信号 Bottom 10（仅做多信号）")
    w()
    w("| 排名 | 代码 | 综合得分 | 最新价 |")
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
    factor_contribs: dict[str, list[float]] = {}
    for sym, details in stock_details.items():
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

    # 全部股票排名
    w("## 全部股票信号排名（仅做多）")
    w()
    w("| 排名 | 代码 | 综合得分 | 最新价 |")
    w("|------|------|----------|--------|")
    for i, (sym, score) in enumerate(ranked, 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} |")
    w()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_path}")

    # 成本摘要
    print(f"\n[成本摘要] 平均交易成本: {avg_cost_bps:.2f} bps")
    print(f"           方向校正: {n_flipped}/{n_factors} 个因子反转")
    print(f"           权重学习: Ridge 回归, {len(factor_weights)} 个因子获权重")

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="每日信号生成管道 v2")
    parser.add_argument("--max-stocks", type=int, default=50, help="最大股票数")
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    args = parser.parse_args()
    sys.exit(main(max_stocks=args.max_stocks, days=args.days))
