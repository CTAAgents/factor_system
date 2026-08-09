"""
scripts/throughput_gp_channel.py — GP/operator 通道吞吐实测（修复验证）

验证对象（v2.66.0，GAP-X01/X02/X03）:
    X03: _execute_factor_code globals 合并 — operator 因子不再 NameError 降级全零
    X02: operator 生成常数信号校验前移 — 生成阶段即拦截非常数表达式
    X01: 横截面预筛改真实截面收益 — 预筛 IC 反映截面区分能力

测量指标:
    A. batch 漏斗（生产路径）: 生成速率 (candidates/s)、粗筛速率、通过率、各阶段延迟
    B. GP 演化单次耗时（_run_gp_evolution）
    C. operator 因子全链路校验通过率（修复前为 0，全被常数信号拦截）

Usage:
    python scripts/throughput_gp_channel.py [--symbols N] [--rows M] [--generations G]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.factor_engine.batch_mining import BatchMiner, BatchMiningConfig
from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.state import generate_trace_id


def load_futures_panel(
    db_path: str = "data/fts_history.duckdb",
    max_symbols: int = 30,
    max_rows: int = 600,
    end_date: str = "2026-07-31",
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, np.ndarray]:
    """从 DuckDB 加载期货截面数据（有界子集，控制实测时长）。

    Returns:
        panel: dict[symbol -> OHLCV DataFrame]
        common_dates: 共有交易日索引
        forward_returns: 截面平均未来 5 日收益率
    """
    con = duckdb.connect(db_path)
    symbols = [
        r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM kline_cache WHERE period='daily' ORDER BY symbol"
        ).fetchall()
    ][:max_symbols]
    panel: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM kline_cache "
            "WHERE symbol = ? AND period = 'daily' ORDER BY date DESC LIMIT ?",
            [sym, max_rows],
        ).fetchall()
        if not rows:
            continue
        df = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume"],
        )
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.astype(float).sort_index()
        df = df[df.index <= end_date]
        panel[sym] = df
    con.close()

    common_dates = sorted(
        set.intersection(*(set(df.index) for df in panel.values()))
    )
    if len(common_dates) < 60:
        print("[data] 共有交易日过少，改用合成面板")
        return _synthetic_panel(40, 400)

    common_dates_idx = pd.DatetimeIndex(common_dates)
    close_panel = pd.DataFrame({sym: df["close"] for sym, df in panel.items()})
    ret_5d = close_panel.pct_change(5).shift(-5)
    forward_returns = np.nan_to_num(ret_5d.mean(axis=1).values, nan=0.0)
    print(
        f"[data] {len(panel)} 品种 × {len(common_dates)} 天, "
        f"forward_returns=[{np.nanmin(forward_returns):.4f}, {np.nanmax(forward_returns):.4f}]"
    )
    return panel, common_dates_idx, forward_returns


def _synthetic_panel(n_symbols: int, n_rows: int):
    """合成面板（DuckDB 数据不可用时兜底）。"""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2023-01-01", periods=n_rows, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for j in range(n_symbols):
        drift = 0.0005 + (j % 5) * 0.0003
        rets = rng.normal(drift, 0.012, n_rows)
        close = 100 * np.exp(np.cumsum(rets))
        panel[f"SYM{j}"] = pd.DataFrame(
            {
                "open": close * (1 + rng.normal(0, 0.002, n_rows)),
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.integers(1e5, 1e6, n_rows).astype(float),
            },
            index=dates,
        )
    common_dates = dates
    close_panel = pd.DataFrame({s: df["close"] for s, df in panel.items()})
    ret_5d = close_panel.pct_change(5).shift(-5)
    fwd = np.nan_to_num(ret_5d.mean(axis=1).values, nan=0.0)
    return panel, common_dates, fwd


def _seed_parent(primary_data: pd.DataFrame) -> FactorProgram:
    """构造种子父因子（趋势族，真实可用）。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature

    code = (
        "import numpy as np\n"
        "def factor_program(data, params):\n"
        "    close = data['close']\n"
        "    if isinstance(close, np.ndarray):\n"
        "        import pandas as pd\n"
        "        close = pd.Series(close)\n"
        "    m20 = close.rolling(20).mean()\n"
        "    m60 = close.rolling(60).mean()\n"
        "    return (m20 - m60) / close.rolling(60).std().replace(0, np.nan)\n"
    )
    return {
        "factor_id": "fct_seed_trend",
        "name": "seed_trend_20_60",
        "code": code,
        "params": {},
        "family": "trend",
        "signature": FactorSignature(
            input_fields=["close"], output_type="signal",
            frequency="daily", lookback=60,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="均线差趋势因子（吞吐实测父因子）",
        ),
    }


def stage_batch_funnel(
    loop: EvolutionLoop, parent: FactorProgram, generations: int,
) -> dict[str, float]:
    """Stage A: batch 漏斗吞吐实测（生成 + 并行粗筛）。"""
    miner = BatchMiner(
        config=BatchMiningConfig(
            batch_size=loop.batch_size,
            max_candidates=loop.batch_max_candidates,
            max_workers=loop.batch_max_workers,
            random_seed=loop.batch_random_seed,
        ),
        generate_cb=loop._batch_generate_one,
        runtime_check_cb=loop._check_factor_runtime,
        prefilter_cb=loop._batch_prefilter,
    )
    trace_id = generate_trace_id("tput")
    stats = {
        "total_generated": 0, "total_passed": 0, "total_rejected": 0,
        "gen_s": 0.0, "filter_s": 0.0, "cand_s": 0.0,
        "runtime_reject": 0, "prefilter_reject": 0, "gp_count": 0,
    }
    t0 = time.perf_counter()
    for g in range(generations):
        loop._batch_idx = 0
        result = miner.run_iteration(parent, generation=g, trace_id=trace_id)
        stats["total_generated"] += result.total_generated
        stats["total_passed"] += result.total_passed
        stats["total_rejected"] += result.total_rejected
        for p in result.rejected:
            reason = p.get("prefilter_reason", "")
            if reason.startswith("运行时校验"):
                stats["runtime_reject"] += 1
            else:
                stats["prefilter_reject"] += 1
        stats["gp_count"] += sum(
            1 for p in result.passed + result.rejected if p.get("method") == "gp_evolution"
        )
    wall = time.perf_counter() - t0
    stats["cand_s"] = stats["total_generated"] / max(wall, 1e-9)
    return stats


def stage_gp_evolution(loop: EvolutionLoop, parent: FactorProgram) -> float:
    """Stage B: GP 演化单次耗时。"""
    from fts.factor_engine.gp_evolver import tree_to_factor_program

    target_col = "forward_return"
    gp_data = loop.data.copy()
    if loop.forward_returns is not None and len(loop.forward_returns) == len(gp_data):
        gp_data[target_col] = loop.forward_returns
    else:
        gp_data[target_col] = 0.0
    train_mask = pd.Series(
        [True] * int(len(gp_data) * 0.6) + [False] * (len(gp_data) - int(len(gp_data) * 0.6)),
        index=gp_data.index,
    )
    t0 = time.perf_counter()
    gp_result = loop.feature_ops_engine.run_gp_search(
        data=gp_data,
        target=target_col,
        config={
            "population_size": 100,
            "max_generations": 20,
            "tournament_size": 3,
            "crossover_rate": 0.7,
            "mutation_rate": 0.1,
            "max_tree_depth": 4,
        },
        train_mask=train_mask,
    )
    elapsed = time.perf_counter() - t0
    factor_program = tree_to_factor_program(gp_result.best_tree)
    # 验证 GP 产物可通过运行时校验（恢复通道的端到端确认）
    ok, reason = loop._check_factor_runtime(factor_program)
    print(
        f"    GP 产物: fitness={gp_result.best_fitness:.4f} "
        f"expr={gp_result.best_expression[:60]}"
    )
    print(f"    GP 产物运行时校验: {'通过' if ok else f'失败: {reason}'}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="GP/operator 通道吞吐实测")
    parser.add_argument("--symbols", type=int, default=30)
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--generations", type=int, default=5)
    args = parser.parse_args()

    print("=" * 72)
    print("GP/operator 通道吞吐实测（修复验证: GAP-X01/X02/X03）")
    print("=" * 72)

    panel, common_dates, forward_returns = load_futures_panel(
        max_symbols=args.symbols, max_rows=args.rows,
    )
    primary = list(panel.values())[0]
    parent = _seed_parent(primary)

    loop = EvolutionLoop(
        data=primary,
        forward_returns=forward_returns,
        market="futures",
        cross_section_data=panel,
        cross_section_dates=common_dates,
        n_trials_micro=2,
    )

    # ── Stage C: operator 因子全链路校验通过率 ──────────
    print("\n--- Stage C: operator 因子全链路校验（修复前通过率为 0） ---")
    n_try = 20
    passed = 0
    gen_cost = 0.0
    runtime_cost = 0.0
    prefilter_cost = 0.0
    for i in range(n_try):
        t0 = time.perf_counter()
        factor, _summary = loop._generate_operator_factor(
            parent, generation=i, trace_id="tput-op",
        )
        gen_cost += time.perf_counter() - t0
        t0 = time.perf_counter()
        ok, reason = loop._check_factor_runtime(factor)
        runtime_cost += time.perf_counter() - t0
        if not ok:
            continue
        t0 = time.perf_counter()
        ok2, reason2, ic = loop._quick_prefilter(factor, "tput-op")
        prefilter_cost += time.perf_counter() - t0
        if ok2:
            passed += 1
    print(
        f"    生成 {n_try} 个 → 通过运行时+预筛 {passed} 个 "
        f"({passed / n_try:.0%})"
    )
    print(
        f"    平均延迟: 生成 {gen_cost / n_try * 1000:.1f}ms, "
        f"运行时校验 {runtime_cost / max(n_try, 1) * 1000:.1f}ms, "
        f"预筛 {prefilter_cost / max(n_try, 1) * 1000:.1f}ms"
    )

    # ── Stage A: batch 漏斗吞吐 ────────────────────────
    print(f"\n--- Stage A: batch 漏斗吞吐（{args.generations} 代） ---")
    stats = stage_batch_funnel(loop, parent, args.generations)
    print(
        f"    生成 {stats['total_generated']} 候选, "
        f"通过粗筛 {stats['total_passed']}, 拦截 {stats['total_rejected']}"
    )
    print(
        f"    拦截分布: 运行时校验 {stats['runtime_reject']}, "
        f"预筛 {stats['prefilter_reject']}"
    )
    print(f"    吞吐: {stats['cand_s']:.1f} 候选/s")

    # ── Stage B: GP 演化耗时 ───────────────────────────
    print("\n--- Stage B: GP 演化单次耗时（_run_gp_evolution） ---")
    n_gp = 3
    gp_times = [stage_gp_evolution(loop, parent) for _ in range(n_gp)]
    print(f"    平均耗时: {np.mean(gp_times):.2f}s ({n_gp} 次)")

    print("\n=== 实测完成 ===")


if __name__ == "__main__":
    main()
