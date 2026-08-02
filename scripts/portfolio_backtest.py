"""
scripts/portfolio_backtest.py — L3 组合策略历史回测

基于 memory/portfolio/current_combo.json 中的组合配置，
在沪深 300 横截面上回测等权多因子策略表现。

流程:
    1. 加载组合配置（因子列表 + 权重）
    2. 加载精英因子程序
    3. 获取沪深 300 面板数据
    4. 计算每个因子在每只股票上的信号
    5. 按权重合成复合评分
    6. 纯多头组合（做多 top 20%）
    7. 计算绩效指标
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ─── 项目路径 ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data import FTSDataProvider  # noqa: E402
from fts.factor_engine.factor_program import FactorExecutor, FactorCompileError  # noqa: E402


# ─── 常量 ──────────────────────────────────────────────────

ELITE_DIR = PROJECT_ROOT / "memory" / "knowledge" / "factors" / "elite"
COMBO_FILE = PROJECT_ROOT / "memory" / "portfolio" / "current_combo.json"
N_BUCKETS = 5          # 五分位
TOP_PCT = 0.2          # 多头比例
OOS_RATIO = 0.3        # 样本外比例
PERIODS_PER_YEAR = 252


# ─── 指标计算 ─────────────────────────────────────────────

def compute_sharpe(returns: np.ndarray, periods: int = PERIODS_PER_YEAR) -> float:
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    return float(np.mean(returns) / std * np.sqrt(periods)) if std > 1e-10 else 0.0


def compute_max_drawdown(cumulative: np.ndarray) -> float:
    if len(cumulative) < 2:
        return 0.0
    nav = 1.0 + cumulative
    peak = np.maximum.accumulate(nav)
    drawdown = (peak - nav) / np.maximum(peak, 1e-10)
    return float(np.max(drawdown))


def compute_calmar_ratio(returns: np.ndarray, periods: int = PERIODS_PER_YEAR) -> float:
    sharpe = compute_sharpe(returns, periods)
    mdd = compute_max_drawdown(np.cumsum(returns))
    return sharpe / mdd if mdd > 1e-10 else 0.0


def compute_win_rate(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    return float(np.mean(returns > 0))


def compute_profit_factor(returns: np.ndarray) -> float:
    gross_profit = np.sum(returns[returns > 0])
    gross_loss = abs(np.sum(returns[returns < 0]))
    return float(gross_profit / max(gross_loss, 1e-10))


# ─── 加载组合配置 ─────────────────────────────────────────

def load_portfolio() -> dict:
    if not COMBO_FILE.exists():
        print(f"[ERROR] 组合文件不存在: {COMBO_FILE}")
        sys.exit(1)
    return json.loads(COMBO_FILE.read_text(encoding="utf-8"))


def load_elite_factors() -> dict[str, dict]:
    """加载所有精英因子程序，返回 {factor_id: data}。"""
    factors: dict[str, dict] = {}
    if not ELITE_DIR.exists():
        print(f"[ERROR] Elite 目录不存在: {ELITE_DIR}")
        sys.exit(1)
    for fp in sorted(ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            fid = data.get("factor_id", fp.stem)
            factors[fid] = data
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] 读取失败 {fp.name}: {e}")
    return factors


# ─── 回测主逻辑 ──────────────────────────────────────────

def run_backtest(
    combo: dict,
    elite_factors: dict[str, dict],
    max_stocks: int = 50,
    days: int = 500,
) -> dict:
    """执行组合回测。

    Args:
        combo: 组合配置
        elite_factors: {factor_id: data} 映射
        max_stocks: 最大标的数
        days: 回溯天数

    Returns:
        回测结果 dict
    """
    signals_def = combo.get("signals", [])
    print(f"\n{'='*60}")
    print(f"组合回测启动")
    print(f"  组合 ID: {combo.get('combo_id', '?')}")
    print(f"  合成模式: {combo.get('synthesis_mode', '?')}")
    print(f"  信号数: {len(signals_def)}")
    print(f"  回溯天数: {days}")
    print(f"  最大标的: {max_stocks}")
    print(f"{'='*60}\n")

    # ── Step 1: 获取数据 ──
    print("[1/5] 获取沪深 300 面板数据...")
    provider = FTSDataProvider()
    panel, common_dates = provider.get_csi300_panel(days=days, max_stocks=max_stocks)
    symbols = sorted(panel.keys())
    n_dates = len(common_dates)
    n_stocks = len(symbols)
    print(f"  标的数: {n_stocks}, 共同日期: {n_dates}")
    print(f"  日期范围: {common_dates[0].date()} ~ {common_dates[-1].date()}")

    if n_stocks < 5 or n_dates < 20:
        print("[ERROR] 数据不足，无法回测")
        return {"status": "failed", "reason": "数据不足"}

    # 检查是否为合成数据
    is_synthetic = "SYNTHETIC" in panel
    if is_synthetic:
        print("  [WARN] 使用合成数据，回测结果仅供参考")

    # ── Step 2: 编译因子执行器 ──
    print(f"\n[2/5] 编译 {len(signals_def)} 个因子执行器...")
    executors: dict[str, FactorExecutor] = {}
    compile_errors = 0
    for sig in signals_def:
        fid = sig["factor_id"]
        fp_data = elite_factors.get(fid)
        if fp_data is None:
            compile_errors += 1
            continue
        try:
            executor = FactorExecutor(fp_data)
            executor.compile()
            executors[fid] = executor
        except FactorCompileError as e:
            compile_errors += 1
            print(f"  [WARN] 编译失败 [{fp_data.get('name', fid)}]: {e}")
    print(f"  成功编译: {len(executors)} / {len(signals_def)}, 失败: {compile_errors}")

    if len(executors) < 3:
        return {"status": "failed", "reason": f"可用因子不足 ({len(executors)})"}

    # ── Step 3: 计算因子信号 ──
    print(f"\n[3/5] 计算因子信号 ({len(executors)} 因子 × {n_stocks} 标的)...")
    # signal_matrix: dict[factor_id, (n_dates, n_stocks)]
    signal_matrices: dict[str, np.ndarray] = {}
    factor_errors = 0
    t0 = time.time()

    for fid, executor in executors.items():
        stock_signals: list[np.ndarray] = []
        ok = True
        for sym in symbols:
            df = panel[sym]
            try:
                arr = executor.execute(df, {})
                if not isinstance(arr, np.ndarray) or len(arr) != len(df):
                    raise ValueError(f"信号长度 {len(arr) if isinstance(arr, np.ndarray) else 'N/A'} != {len(df)}")
                stock_signals.append(arr)
            except Exception:
                stock_signals.append(np.full(len(df), np.nan))
                ok = False

        if ok:
            # 对齐到共同日期
            matrix = np.zeros((n_dates, n_stocks))
            for j, sym in enumerate(symbols):
                full_sig = stock_signals[j]
                # 映射到共同日期索引
                date_idx = panel[sym].index
                date_map = {d: i for i, d in enumerate(date_idx)}
                for t, d in enumerate(common_dates):
                    if d in date_map:
                        matrix[t, j] = full_sig[date_map[d]]
                    else:
                        matrix[t, j] = np.nan
            signal_matrices[fid] = matrix
        else:
            factor_errors += 1

    # 构建 signals_by_id 查找
    signals_by_id = {s["factor_id"]: s for s in signals_def}

    elapsed = time.time() - t0
    print(f"  完成: {len(signal_matrices)} 因子, 耗时: {elapsed:.1f}s, 错误: {factor_errors}")

    if len(signal_matrices) < 3:
        return {"status": "failed", "reason": f"有效因子不足 ({len(signal_matrices)})"}

    # ── Step 4: 合成复合评分 ──
    print(f"\n[4/5] 合成复合评分 ({combo.get('synthesis_mode', 'equal_weight')})...")
    # 权重归一化
    weight_map: dict[str, float] = {}
    total_w = 0.0
    for s in signals_def:
        if s.get("retained", True) and s["factor_id"] in signal_matrices:
            weight_map[s["factor_id"]] = s.get("weight", 0)
            total_w += s.get("weight", 0)
    if total_w > 0:
        for k in weight_map:
            weight_map[k] /= total_w

    # 合成复合评分: composite[t, j] = sum(w_i * signal_i[t, j])
    composite = np.zeros((n_dates, n_stocks))
    for fid, w in weight_map.items():
        mat = signal_matrices[fid]
        # 横截面标准化（每期 z-score）
        for t in range(n_dates):
            row = mat[t, :]
            mu = np.nanmean(row)
            sigma = np.nanstd(row)
            if sigma > 1e-10:
                mat[t, :] = (row - mu) / sigma
            else:
                mat[t, :] = 0.0
        composite += w * mat

    # ── Step 5: 构建纯多头组合 ──
    print(f"[5/5] 构建纯多头组合 (top {TOP_PCT*100:.0f}% long)...")

    # 计算每只股票的 forward returns（1日收益，避免重叠窗口造成的伪高夏普）
    fwd_ret_matrix = np.zeros((n_dates, n_stocks))
    for j, sym in enumerate(symbols):
        df = panel[sym]
        closes = df["close"].values
        fwd = np.zeros(len(closes))
        if len(closes) > 1:
            fwd[:-1] = (closes[1:] - closes[:-1]) / np.maximum(closes[:-1], 1e-10)
        date_idx = df.index
        date_map = {d: i for i, d in enumerate(date_idx)}
        for t, d in enumerate(common_dates):
            if d in date_map:
                fwd_ret_matrix[t, j] = fwd[date_map[d]]

    # OOS 切片
    oos_n = max(int(n_dates * OOS_RATIO), 20)
    oos_composite = composite[-oos_n:, :]
    oos_fwd_ret = fwd_ret_matrix[-oos_n:, :]
    oos_dates = common_dates[-oos_n:]

    # 每期纯多头收益
    daily_returns = np.zeros(oos_n)
    long_returns = np.zeros(oos_n)
    daily_ic = np.zeros(oos_n)

    # 基准：等权持有所有有效标的
    benchmark_returns = np.zeros(oos_n)

    for t in range(oos_n):
        scores = oos_composite[t, :]
        rets = oos_fwd_ret[t, :]
        valid = ~(np.isnan(scores) | np.isnan(rets))
        valid_count = np.sum(valid)
        if valid_count < 5:
            continue

        scores_v = scores[valid]
        rets_v = rets[valid]
        sorted_idx = np.argsort(scores_v)
        top_n = max(1, int(len(sorted_idx) * TOP_PCT))

        long_ret = np.mean(rets_v[sorted_idx[-top_n:]])
        bench_ret = np.mean(rets_v)
        long_returns[t] = long_ret
        benchmark_returns[t] = bench_ret
        daily_returns[t] = long_ret  # 纯多头收益

        # 截面 IC
        from scipy import stats as sp_stats
        ic_val, _ = sp_stats.spearmanr(scores_v, rets_v)
        daily_ic[t] = ic_val if not np.isnan(ic_val) else 0.0

    # ── 绩效指标 ──
    cumulative = np.cumsum(daily_returns)
    sharpe = compute_sharpe(daily_returns)
    max_dd = compute_max_drawdown(cumulative)
    calmar = compute_calmar_ratio(daily_returns)
    win_rate = compute_win_rate(daily_returns)
    profit_factor = compute_profit_factor(daily_returns)
    ic_mean = float(np.mean(daily_ic))
    ic_std = float(np.std(daily_ic, ddof=1)) if len(daily_ic) > 1 else 0.0
    icir = ic_mean / max(ic_std, 1e-10)

    # 按月统计
    df_daily = pd.DataFrame({
        "date": oos_dates,
        "daily_return": daily_returns,
        "long_return": long_returns,
        "benchmark_return": benchmark_returns,
        "cumulative": cumulative,
        "ic": daily_ic,
    })
    df_daily["month"] = df_daily["date"].dt.to_period("M")
    monthly = df_daily.groupby("month")["daily_return"].sum()

    # 正月份比例
    positive_months = float(np.mean(monthly > 0))

    return {
        "status": "completed",
        "combo_id": combo.get("combo_id", "?"),
        "synthesis_mode": combo.get("synthesis_mode", "?"),
        "n_factors_input": len(signals_def),
        "n_factors_effective": len(signal_matrices),
        "n_stocks": n_stocks,
        "n_dates": n_dates,
        "oos_dates": oos_n,
        "date_range": f"{oos_dates[0].date()} ~ {oos_dates[-1].date()}",
        "is_synthetic": is_synthetic,

        # 绩效指标
        "total_return": float(cumulative[-1]),
        "annualized_return": float((1 + cumulative[-1]) ** (PERIODS_PER_YEAR / oos_n) - 1) if oos_n > 0 else 0.0,
        "annualized_vol": float(np.std(daily_returns, ddof=1) * np.sqrt(PERIODS_PER_YEAR)),
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "positive_month_ratio": positive_months,
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "icir": icir,
        "long_avg_return": float(np.mean(long_returns)),
        "benchmark_avg_return": float(np.mean(benchmark_returns)),
        "excess_avg_return": float(np.mean(long_returns - benchmark_returns)),

        # 原始数据
        "daily_returns": daily_returns.tolist(),
        "cumulative_returns": cumulative.tolist(),
        "dates": [str(d.date()) for d in oos_dates],
        "monthly_returns": {str(k): float(v) for k, v in monthly.items()},
    }


# ─── 报告输出 ──────────────────────────────────────────────

def print_report(result: dict) -> None:
    """打印格式化回测报告。"""
    if result["status"] != "completed":
        print(f"\n[回测失败] {result.get('reason', '未知错误')}")
        return

    print(f"\n{'='*60}")
    print(f"  组合回测报告")
    print(f"{'='*60}")
    print(f"  组合 ID:     {result['combo_id']}")
    print(f"  合成模式:    {result['synthesis_mode']}")
    print(f"  有效因子数:  {result['n_factors_effective']} / {result['n_factors_input']}")
    print(f"  回测标的:    {result['n_stocks']} 只 (沪深300)")
    print(f"  回测区间:    {result['date_range']}")
    print(f"  样本外天数:  {result['oos_dates']}")
    if result["is_synthetic"]:
        print(f"  [WARN] 使用合成数据")
    print(f"{'─'*60}")

    print(f"  ┌─ 绩效指标{'─'*40}┐")
    print(f"  │ 累计收益率:    {result['total_return']*100:+7.2f}%")
    print(f"  │ 年化收益率:    {result['annualized_return']*100:+7.2f}%")
    print(f"  │ 年化波动率:    {result['annualized_vol']*100:7.2f}%")
    print(f"  │ 夏普比率:      {result['sharpe']:7.2f}")
    print(f"  │ 最大回撤:      {result['max_drawdown']*100:7.2f}%")
    print(f"  │ 卡玛比率:      {result['calmar_ratio']:7.2f}")
    print(f"  │ 胜率:          {result['win_rate']*100:7.2f}%")
    print(f"  │ 盈亏比:        {result['profit_factor']:7.2f}")
    print(f"  │ 正月份比例:    {result['positive_month_ratio']*100:7.2f}%")
    print(f"  └{'─'*55}┘")

    print(f"\n  ┌─ IC 指标{'─'*44}┐")
    print(f"  │ 截面 IC 均值:  {result['ic_mean']:7.4f}")
    print(f"  │ 截面 IC 标准差: {result['ic_std']:7.4f}")
    print(f"  │ ICIR:          {result['icir']:7.2f}")
    print(f"  └{'─'*55}┘")

    print(f"\n  ┌─ 收益分析{'─'*42}┐")
    print(f"  │ 多头平均日收益:  {result['long_avg_return']*10000:+7.2f} bp")
    print(f"  │ 基准平均日收益:  {result['benchmark_avg_return']*10000:+7.2f} bp")
    print(f"  │ 超额平均日收益:  {result['excess_avg_return']*10000:+7.2f} bp")
    print(f"  └{'─'*55}┘")

    # 月度收益
    monthly = result.get("monthly_returns", {})
    if monthly:
        print(f"\n  ┌─ 月度收益{'─'*42}┐")
        for m, r in sorted(monthly.items()):
            marker = "■" if r > 0 else "□"
            bar = "█" * max(1, int(abs(r) * 500))
            print(f"  │ {m} {marker} {r*100:+6.2f}% {bar}")
        print(f"  └{'─'*55}┘")

    print(f"\n{'='*60}")
    print(f"  回测完成")
    print(f"{'='*60}\n")


# ─── 主入口 ────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="L3 组合策略历史回测")
    parser.add_argument("--max-stocks", type=int, default=50, help="最大标的数")
    parser.add_argument("--days", type=int, default=500, help="回溯天数")
    parser.add_argument("--save", action="store_true", help="保存回测结果到 JSON")
    args = parser.parse_args()

    combo = load_portfolio()
    elite_factors = load_elite_factors()
    print(f"加载精英因子: {len(elite_factors)} 个")

    result = run_backtest(combo, elite_factors, max_stocks=args.max_stocks, days=args.days)
    print_report(result)

    if args.save and result["status"] == "completed":
        out_path = PROJECT_ROOT / "memory" / "portfolio" / "backtest_result.json"
        # 移除原始数据（太大）
        report = {k: v for k, v in result.items()
                  if k not in ("daily_returns", "cumulative_returns", "dates", "monthly_returns")}
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"回测结果已保存到: {out_path}")

    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())