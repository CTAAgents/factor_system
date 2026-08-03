"""
scripts/daily_signal_pipeline.py — 每日信号生成管道

从 FTS Elite 因子库 + 组合权重，生成当前市场的逐股打分信号。

用法:
    python scripts/daily_signal_pipeline.py [--max-stocks 50] [--days 120]

输出:
    - 控制台: 信号排名表
    - 文件:     docs/daily_signals_{date}.md
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/elite"
WEIGHTS_FILE = PROJECT_ROOT / "memory/portfolio/factor_weights.json"
OUTPUT_DIR = PROJECT_ROOT / "docs"

# ── 辅助：加载因子权重 ──

def load_weights() -> dict[str, float]:
    """加载 portfolio 因子权重。"""
    if not WEIGHTS_FILE.exists():
        print(f"[WARN] 权重文件不存在: {WEIGHTS_FILE}，使用等权")
        return {}
    data = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
    return data.get("weights", {})


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


# ── 主流程 ──

def main(max_stocks: int = 50, days: int = 120) -> int:
    t0 = time.time()
    today = date.today().isoformat()
    print(f"=" * 60)
    print(f"  每日信号生成管道 — {today}")
    print(f"=" * 60)

    # ── Step 1: 加载权重 ──
    weights = load_weights()
    print(f"\n[1/4] 加载权重: {len(weights)} 个因子")

    # ── Step 2: 加载 Elite 因子 ──
    all_factors = load_elite_programs()
    print(f"[2/4] 加载 Elite 因子: {len(all_factors)} 个")

    if not all_factors:
        print("[ERROR] 无 Elite 因子，退出")
        return 1

    # ── Step 3: 获取数据 ──
    from fts.data import FTSDataProvider
    from fts.data_fundamental import get_fundamental_provider

    provider = FTSDataProvider()
    print(f"[3/4] 获取数据: CSI300, max_stocks={max_stocks}, days={days}")

    panel, common_dates = provider.get_csi300_panel(
        days=days, max_stocks=max_stocks, fundamental=True,
    )
    print(f"      面板: {len(panel)} 只股票, {len(common_dates)} 个交易日")

    if not panel:
        print("[ERROR] 无数据，退出")
        return 1

    # ── Step 4: 计算因子信号 ──
    from fts.factor_engine.factor_program import FactorExecutor

    print(f"[4/4] 计算因子信号 ({len(all_factors)} 因子 × {len(panel)} 股票)...")

    # 按权重筛选因子（只计算有权重的因子）
    weighted_factors = [f for f in all_factors if f.get("name") in weights]
    unweighted_count = len(all_factors) - len(weighted_factors)
    if unweighted_count:
        print(f"      跳过 {unweighted_count} 个无权重因子")

    # 逐股票计算信号
    stock_scores: dict[str, float] = {}
    stock_details: dict[str, dict[str, float]] = {}
    n_errors = 0

    for sym, df in panel.items():
        if df.empty or len(df) < 20:
            continue

        latest = df.iloc[-1]
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_data in weighted_factors:
            name = factor_data.get("name", "?")
            w = weights.get(name, 0.0)
            if w <= 0:
                continue

            try:
                executor = FactorExecutor(factor_data)
                signals = executor.execute(df, factor_data.get("params", {}))
                # 取最新信号值
                val = float(signals[-1]) if len(signals) > 0 else 0.0
                signal_sum += val * w
                weight_sum += w
                details[name] = val
            except Exception:
                n_errors += 1
                continue

        if weight_sum > 0:
            composite = signal_sum / weight_sum
            stock_scores[sym] = composite
            stock_details[sym] = details

    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s, 成功: {len(stock_scores)} 只, 因子错误: {n_errors}")

    # ── Step 5: 输出信号排名 ──
    if not stock_scores:
        print("[ERROR] 无有效信号")
        return 1

    ranked = sorted(stock_scores.items(), key=lambda x: -x[1])

    # 控制台输出
    print(f"\n{'=' * 60}")
    print(f"  信号排名 Top 20")
    print(f"{'=' * 60}")
    print(f"{'排名':>4s} {'代码':>8s} {'综合得分':>10s} {'最新价':>10s} {'涨跌幅':>8s} {'Top因子':>30s}")
    print(f"{'-'*4} {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*30}")

    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None and not df.empty else 0.0
        chg_pct = df.iloc[-1].get("change_pct", 0.0) if df is not None else 0.0
        # 找出贡献最大的 3 个因子
        details = stock_details.get(sym, {})
        top_factors = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = ", ".join(f"{n}({v:+.3f})" for n, v in top_factors)
        print(f"{i:>4d} {sym:>8s} {score:>+10.4f} {price:>10.2f} {chg_pct:>+7.2%} {top_str:<30s}")

    # 底部信号
    print(f"\n{'─' * 60}")
    print(f"  底部信号 Bottom 5")
    for i, (sym, score) in enumerate(ranked[-5:], len(ranked) - 4):
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
    w(f"因子池: {len(weighted_factors)} 个 | 覆盖股票: {len(stock_scores)} 只")
    w()
    w("## 信号排名 Top 20")
    w()
    w("| 排名 | 代码 | 综合得分 | 最新价 | 涨跌幅 | Top 3 因子贡献 |")
    w("|------|------|----------|--------|--------|----------------|")
    for i, (sym, score) in enumerate(ranked[:20], 1):
        df = panel.get(sym)
        price = df.iloc[-1]["close"] if df is not None else 0.0
        chg = df.iloc[-1].get("change_pct", 0.0) if df is not None else 0.0
        details = stock_details.get(sym, {})
        top3 = sorted(details.items(), key=lambda x: -abs(x[1]))[:3]
        top_str = " ".join(f"{n}({v:+.3f})" for n, v in top3)
        w(f"| {i} | {sym} | {score:+.4f} | {price:.2f} | {chg:+.2%} | {top_str} |")
    w()

    w("## 底部信号 Bottom 10")
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
    w("## 全部股票信号排名")
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

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="每日信号生成管道")
    parser.add_argument("--max-stocks", type=int, default=50, help="最大股票数")
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    args = parser.parse_args()
    sys.exit(main(max_stocks=args.max_stocks, days=args.days))