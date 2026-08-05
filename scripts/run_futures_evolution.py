"""
scripts/run_futures_evolution.py — 期货因子演化脚本

从 DuckDB 加载期货数据，构建截面数据集，运行 L2 演化循环。

Usage:
    python scripts/run_futures_evolution.py [--generations N] [--n-trials N] [--budget-tokens N]

数据说明:
    - kline_cache 表: 59 个期货品种日线数据（2005-01-04 ~ 2026-07-31）
    - 字段: symbol, date, open, high, low, close, volume, amount
    - 注意: 无 hold(持仓量) 字段，依赖 hold 的因子会自动降级
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# 项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.seed_pool import SeedPool
from fts.factor_engine.contracts import BudgetConfig
from fts.factor_engine.state import generate_trace_id


def load_futures_panel(
    db_path: str = "data/fts_history.duckdb",
    min_symbols: int = 10,
    min_periods: int = 252,
    end_date: str = "2026-07-31",
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, np.ndarray]:
    """从 DuckDB 加载期货截面数据。

    Returns:
        cross_section_data: dict[symbol -> OHLCV DataFrame]
        common_dates: 所有品种共有的交易日索引
        forward_returns: 未来 5 日收益率（截面平均）
    """
    con = duckdb.connect(db_path)

    # 获取所有期货品种（数据库中存储的是不带0后缀的品种代码）
    symbols = [
        r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM kline_cache WHERE period='daily' ORDER BY symbol"
        ).fetchall()
    ]
    print(f"[data] 共 {len(symbols)} 个可用品种")

    # 加载每个品种的日线数据
    panel: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume, amount FROM kline_cache "
            "WHERE symbol = ? AND period = 'daily' ORDER BY date",
            [sym],
        ).fetchall()

        if len(rows) < min_periods:
            continue

        df = pd.DataFrame(
            rows,
            columns=["date", "open", "high", "low", "close", "volume", "amount"],
        )
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.astype(float)
        # 过滤到 end_date
        df = df[df.index <= end_date]
        panel[sym] = df

    con.close()
    print(f"[data] 加载 {len(panel)} 个品种（至少 {min_periods} 根K线）")

    # 找到至少有 min_symbols 个品种共有的日期（而非要求全部品种）
    from collections import Counter
    date_counts = Counter()
    for sym, df in panel.items():
        for d in df.index:
            date_counts[d] += 1

    # 取至少有 min_symbols 个品种有数据的日期
    common_dates = sorted([d for d, cnt in date_counts.items() if cnt >= min_symbols])
    if not common_dates:
        print(f"[data] 错误: 没有找到至少有 {min_symbols} 个品种共有的日期")
        return {}, pd.DatetimeIndex([]), np.array([])

    print(f"[data] 共有交易日: {len(common_dates)} 天 ({common_dates[0]} ~ {common_dates[-1]})")
    print(f"[data] 日期覆盖: 至少 {min_symbols} 个品种有数据")

    # 过滤 panel 只保留共有日期（允许某些品种缺少部分日期）
    common_set = set(common_dates)
    filtered: dict[str, pd.DataFrame] = {}
    for sym, df in panel.items():
        valid_dates = [d for d in df.index if d in common_set]
        if len(valid_dates) >= min_periods:
            filtered[sym] = df.loc[valid_dates]
    panel = filtered
    print(f"[data] 过滤后: {len(panel)} 个品种保留（至少 {min_periods} 个共有日期）")

    # 计算截面平均未来 5 日收益率作为 forward_returns
    close_panel = pd.DataFrame({sym: df["close"] for sym, df in panel.items()})
    ret_5d = close_panel.pct_change(5).shift(-5)
    forward_returns = np.nan_to_num(ret_5d.mean(axis=1).values, nan=0.0)  # 截面平均

    common_dates_idx = pd.DatetimeIndex(common_dates)
    print(f"[data] 最终: {len(panel)} 个品种 × {len(common_dates)} 天")
    print(f"[data] forward_returns 范围: {np.nanmin(forward_returns):.4f} ~ {np.nanmax(forward_returns):.4f}")

    return panel, common_dates_idx, forward_returns


def main():
    parser = argparse.ArgumentParser(description="期货因子演化")
    parser.add_argument("--generations", type=int, default=3, help="演化代数")
    parser.add_argument("--n-trials", type=int, default=20, help="每代微观演化 trial 数")
    parser.add_argument("--budget-tokens", type=int, default=500_000, help="单夜 token 预算")
    parser.add_argument("--min-symbols", type=int, default=10, help="最少品种数")
    parser.add_argument("--min-periods", type=int, default=252, help="最少K线数")
    parser.add_argument("--end-date", type=str, default="2026-07-31", help="截止日期")
    parser.add_argument("--elite-dir", type=str, default="memory/knowledge/factors/futures_elite", help="精英池目录")
    parser.add_argument("--memory-dir", type=str, default="memory/evolution/futures", help="演化状态目录")
    args = parser.parse_args()

    trace_id = generate_trace_id("fut_evol")
    print(f"=== 期货因子演化开始 === trace_id={trace_id}")
    print(f"    代数={args.generations}, 每代 trials={args.n_trials}, token预算={args.budget_tokens}")

    # ─── 1. 加载数据 ────────────────────────────────────
    print("\n--- 步骤 1: 加载期货数据 ---")
    cross_section_data, common_dates, forward_returns = load_futures_panel(
        min_symbols=args.min_symbols,
        min_periods=args.min_periods,
        end_date=args.end_date,
    )

    # ─── 2. 创建种子池（期货模式）───────────────────────
    print("\n--- 步骤 2: 创建期货种子池 ---")
    seed_pool = SeedPool(trace_id=trace_id, market="futures")
    seeds = seed_pool.load_all_seeds()
    print(f"    加载 {len(seeds)} 个期货种子因子")
    for seed in seeds:
        print(f"      - {seed['name']}")

    # ─── 3. 配置预算 ────────────────────────────────────
    budget = BudgetConfig(
        nightly_token_limit=args.budget_tokens,
        monthly_token_limit=args.budget_tokens * 30,
        max_generation=args.generations,
        max_tokens_per_factor=50_000,
        circuit_breaker_token_ratio=2.0,
        circuit_breaker_consecutive_low_ic=100,
        circuit_breaker_low_ic_threshold=0.01,
        circuit_breaker_failure_rate=0.99,
    )

    # ─── 4. 创建演化循环 ────────────────────────────────
    print("\n--- 步骤 3: 创建演化循环 ---")
    # 使用第一个品种的 OHLCV 作为主 data（用于评估链的默认回测）
    primary_symbol = list(cross_section_data.keys())[0]
    primary_data = cross_section_data[primary_symbol]

    loop = EvolutionLoop(
        data=primary_data,
        forward_returns=forward_returns,
        elite_dir=args.elite_dir,
        memory_dir=args.memory_dir,
        budget=budget,
        seed_pool=seed_pool,
        n_trials_micro=args.n_trials,
        cross_section_data=cross_section_data,
        cross_section_dates=common_dates,
        market="futures",
    )

    # ─── 5. 运行演化 ────────────────────────────────────
    print("\n--- 步骤 4: 运行演化 ---")
    result = loop.run(max_generation=args.generations)

    # ─── 6. 输出结果 ────────────────────────────────────
    print("\n" + "=" * 60)
    print("演化结果")
    print("=" * 60)
    print(f"  run_id:           {result.run_id}")
    print(f"  状态:              {result.status}")
    print(f"  完成代数:          {result.generations_completed}")
    print(f"  评估因子数:        {result.total_factors_evaluated}")
    print(f"  晋级精英池:        {result.total_factors_promoted}")
    print(f"  消耗 token:        {result.tokens_consumed}")
    if result.circuit_breaker_reason:
        print(f"  熔断原因:          {result.circuit_breaker_reason}")
    if result.elite_factor_ids:
        print(f"  精英因子 ID:       {result.elite_factor_ids[:10]}...")
        print(f"  精英因子总数:      {len(result.elite_factor_ids)}")

    # 列出精英池文件
    elite_dir = Path(args.elite_dir)
    if elite_dir.exists():
        elite_files = list(elite_dir.glob("*.json"))
        print(f"\n  精英池文件数: {len(elite_files)}")
        for f in elite_files:
            print(f"    - {f.name}")

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()