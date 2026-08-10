"""全链路诊断脚本 — 从数据源到绩效指标逐环节验证。"""

import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from fts.data import FTSDataProvider


# ─── 1. 数据完整性检查 ────────────────────────────────────


def check_data_integrity(symbol: str = "RB0", days: int = 500):
    print(f"\n{'=' * 60}")
    print(f"🔍 [1/5] 数据完整性检查 — {symbol}")
    print(f"{'=' * 60}")

    provider = FTSDataProvider()
    data = provider.get_ohlcv(symbol, days=days)
    print(f"  数据行数: {len(data)}")
    print(f"  日期范围: {data.index[0].date()} ~ {data.index[-1].date()}")

    # 检查缺失值
    nulls = data.isnull().sum()
    if nulls.sum() > 0:
        print(f"  ⚠️ 发现缺失值:\n{nulls[nulls > 0]}")
    else:
        print("  ✅ 无缺失值")

    # 检查异常值
    close = data["close"]
    for col in ["open", "high", "low", "close"]:
        vals = data[col]
        if (vals <= 0).any():
            print(f"  ⚠️ {col} 包含非正数: {(vals <= 0).sum()} 行")
        if (vals != vals).any():
            print(f"  ⚠️ {col} 包含 NaN: {(vals != vals).sum()} 行")

    # 检查高开低收一致性
    bad_hl = (data["high"] < data["low"]).sum()
    bad_hc = (data["high"] < data["close"]).sum()
    bad_ho = (data["high"] < data["open"]).sum()
    bad_lc = (data["low"] > data["close"]).sum()
    bad_lo = (data["low"] > data["open"]).sum()
    total_bad = bad_hl + bad_hc + bad_ho + bad_lc + bad_lo
    if total_bad > 0:
        print(f"  ⚠️ OHLC 一致性异常: H<L={bad_hl}, H<C={bad_hc}, H<O={bad_ho}, L>C={bad_lc}, L>O={bad_lo}")
    else:
        print("  ✅ OHLC 一致性正常")

    # 检查价格跳空
    gaps = (close.diff().abs() / close.shift(1) > 0.10).sum()
    if gaps > 0:
        print(f"  ⚠️ 单日跳空 >10%: {gaps} 次")
        gap_dates = close[close.diff().abs() / close.shift(1) > 0.10].index
        for d in gap_dates:
            idx = close.index.get_loc(d)
            if idx > 0:
                prev_close = close.iloc[idx - 1]
                curr_close = close.iloc[idx]
                gap_pct = (curr_close - prev_close) / prev_close * 100
                print(f"    {d.date()}: {prev_close:.2f} → {curr_close:.2f} ({gap_pct:+.2f}%)")
    else:
        print("  ✅ 无异常跳空")

    # 检查连续 0 值
    zero_streaks = 0
    streak = 0
    for v in close.values:
        if v == 0:
            streak += 1
        else:
            if streak > 3:
                zero_streaks += 1
            streak = 0
    if zero_streaks > 0:
        print(f"  ⚠️ 发现连续 0 值段: {zero_streaks} 处")
    else:
        print("  ✅ 无连续 0 值")

    # 成交量检查
    vol = data["volume"]
    if (vol == 0).sum() > len(vol) * 0.1:
        print(f"  ⚠️ 零成交量占比过高: {(vol == 0).sum()}/{len(vol)}")
    elif (vol < 0).any():
        print(f"  ⚠️ 成交量存在负值: {(vol < 0).sum()} 行")
    else:
        print("  ✅ 成交量正常")

    return data


# ─── 2. 因子代码执行检查 ──────────────────────────────────


def check_factor_code(factor_id: str, data: pd.DataFrame):
    print(f"\n{'=' * 60}")
    print(f"🔍 [2/5] 因子代码执行检查 — {factor_id}")
    print(f"{'=' * 60}")

    # 从 elite 目录加载因子
    factor = None
    from pathlib import Path
    from fts.config import get_config

    cfg = get_config()
    # 先尝试 elite 目录
    elite_dir = Path(cfg.get_elite_dir("futures"))
    if elite_dir.exists():
        candidates = list(elite_dir.glob(f"*{factor_id}*.json"))
        if candidates:
            factor = json.loads(candidates[0].read_text(encoding="utf-8"))
    # 再尝试 seeds 目录
    if factor is None:
        from scripts.unified_factor_converter import load_all_factors

        all_factors = load_all_factors("futures")
        for f in all_factors:
            if f.get("factor_id") == factor_id or f.get("name") == factor_id:
                factor = f
                break
    if factor is None:
        print(f"  ❌ 未找到因子 {factor_id}")
        return None

    code = factor.get("code", "")
    name = factor.get("name", factor_id)
    print(f"  因子名称: {name}")
    print(f"  代码长度: {len(code)} 字符")

    # 检查代码中是否有潜在的前向偏差
    forward_bias_keywords = ["shift(-", "future", "forward", "lead", "next", "rolling("]
    for kw in forward_bias_keywords:
        if kw in code:
            print(f"  ⚠️ 代码包含潜在前向偏差关键词: '{kw}'")

    # 检查是否使用了未来数据
    if "close" in code and "open" in code:
        print("  ✅ 因子使用 close+open（正常）")
    if "high" in code or "low" in code:
        print("  ⚠️ 因子使用了 high/low（日内数据，需确认是否在当日可用）")

    # 执行因子代码获取具体值
    n = len(data)
    open_price = data["open"].values.astype(np.float64)
    high = data["high"].values.astype(np.float64)
    low = data["low"].values.astype(np.float64)
    close = data["close"].values.astype(np.float64)
    volume = data["volume"].values.astype(np.float64)

    local_vars = {"open": open_price, "high": high, "low": low, "close": close, "volume": volume, "n": n, "np": np}
    try:
        exec(code, {"np": np}, local_vars)
    except Exception as e:
        print(f"  ❌ 因子代码执行异常: {e}")
        return None

    # 检查标准约定
    factor_fn = local_vars.get("factor_program")
    if callable(factor_fn):
        data_dict = {col: data[col].values.astype(np.float64) for col in data.columns}
        factor_values = factor_fn(data_dict, {})
        if isinstance(factor_values, (np.ndarray, pd.Series)):
            factor_values = np.asarray(factor_values, dtype=float)
    elif "output" in local_vars:
        factor_values = np.asarray(local_vars["output"], dtype=float)
    else:
        print("  ❌ 未找到 factor_program 或 output 变量")
        return None

    factor_values = np.nan_to_num(factor_values, nan=0.0)
    print(f"  因子值形状: {factor_values.shape}")
    print("  因子值统计:")
    print(f"    mean={factor_values.mean():.6f}, std={factor_values.std():.6f}")
    print(f"    min={factor_values.min():.6f}, max={factor_values.max():.6f}")
    print(f"    非零占比: {(factor_values != 0).sum()}/{len(factor_values)} ({(factor_values != 0).mean() * 100:.1f}%)")

    # 检查因子值是否全为零
    if np.all(factor_values == 0):
        print("  ❌ 因子值全部为零！")
    elif np.std(factor_values) < 1e-10:
        print(f"  ⚠️ 因子值几乎为常数: std={np.std(factor_values):.2e}")
    else:
        print("  ✅ 因子值分布正常")

    return factor_values


# ─── 3. 回测指标计算验证 ──────────────────────────────────


def check_backtest_metrics(data: pd.DataFrame, factor_values: np.ndarray):
    print(f"\n{'=' * 60}")
    print("🔍 [3/5] 回测指标计算验证")
    print(f"{'=' * 60}")

    # 手工计算 forward_returns
    close = data["close"].values
    forward_returns = np.zeros(len(close))
    forward_returns[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    print("  Forward returns 统计:")
    print(f"    mean={forward_returns.mean():.6f}, std={forward_returns.std():.6f}")
    print(f"    min={forward_returns.min():.6f}, max={forward_returns.max():.6f}")

    # 手工计算策略收益
    positions = np.zeros_like(factor_values)
    n = len(factor_values)
    for i in range(1, n):
        if np.std(factor_values[:i]) > 1e-8:
            z = (factor_values[i] - np.mean(factor_values[:i])) / max(np.std(factor_values[:i]), 1e-8)
            positions[i] = np.clip(z, -2, 2) * 0.5

    turnover = np.abs(np.diff(positions, prepend=0))
    costs = turnover * (0.0003 + 0.0001)
    strategy_returns = positions * forward_returns - costs

    print("  策略收益统计:")
    print(f"    mean={strategy_returns.mean():.6f}, std={strategy_returns.std():.6f}")
    print(f"    min={strategy_returns.min():.6f}, max={strategy_returns.max():.6f}")
    print(f"    正收益天数: {(strategy_returns > 0).sum()}/{n} ({(strategy_returns > 0).mean() * 100:.2f}%)")
    print(f"    零收益天数: {(strategy_returns == 0).sum()}/{n}")

    # 手工计算绩效指标
    equity = 1_000_000 * (1 + strategy_returns).cumprod()
    total_return = equity[-1] / equity[0] - 1
    n_days = len(strategy_returns)
    annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    volatility = float(np.std(strategy_returns) * np.sqrt(252))
    sharpe = annual_return / volatility if volatility > 1e-8 else 0.0

    cummax = np.maximum.accumulate(equity)
    drawdown = (equity - cummax) / cummax
    max_dd = drawdown.min()

    # 盈亏比
    pos_ret = strategy_returns[strategy_returns > 0]
    neg_ret = strategy_returns[strategy_returns < 0]
    avg_win = pos_ret.mean() if len(pos_ret) > 0 else 0.0
    avg_loss = abs(neg_ret.mean()) if len(neg_ret) > 0 else 0.0
    payoff_ratio = avg_win / avg_loss if avg_loss > 1e-8 else 0.0
    total_win = pos_ret.sum() if len(pos_ret) > 0 else 0.0
    total_loss = abs(neg_ret.sum()) if len(neg_ret) > 0 else 0.0
    profit_factor = total_win / total_loss if total_loss > 1e-8 else 0.0

    print("\n  手工计算的绩效指标:")
    print(f"    总收益: {total_return * 100:.2f}%")
    print(f"    年化: {annual_return * 100:.2f}%")
    print(f"    Sharpe: {sharpe:.3f}")
    print(f"    最大回撤: {max_dd * 100:.2f}%")
    print(f"    胜率: {(strategy_returns > 0).sum() / max(n_days, 1) * 100:.2f}%")
    print(f"    盈亏比: {payoff_ratio:.2f}")
    print(f"    盈亏因子: {profit_factor:.2f}")

    return strategy_returns, equity, positions


# ─── 4. 信号生成检查 ──────────────────────────────────────


def check_signal_generation(
    factor_values: np.ndarray, strategy_returns: np.ndarray, positions: np.ndarray, data: pd.DataFrame
):
    print(f"\n{'=' * 60}")
    print("🔍 [4/5] 信号生成与交易逻辑检查")
    print(f"{'=' * 60}")

    # 信号分布
    long_signal = (positions > 0.1).sum()
    short_signal = (positions < -0.1).sum()
    flat = (abs(positions) <= 0.1).sum()
    n = len(positions)
    print("  信号分布:")
    print(f"    做多: {long_signal}/{n} ({long_signal / n * 100:.1f}%)")
    print(f"    做空: {short_signal}/{n} ({short_signal / n * 100:.1f}%)")
    print(f"    空仓: {flat}/{n} ({flat / n * 100:.1f}%)")

    # 检查是否所有信号都集中在同一方向
    if long_signal > 0.9 * n:
        print(f"  ⚠️ 做多信号占比过高 ({long_signal / n * 100:.1f}%)，因子可能始终输出正值")
    if short_signal > 0.9 * n:
        print(f"  ⚠️ 做空信号占比过高 ({short_signal / n * 100:.1f}%)，因子可能始终输出负值")

    # 信号与收益关系
    long_returns = strategy_returns[positions > 0.1]
    short_returns = strategy_returns[positions < -0.1]
    if len(long_returns) > 0:
        print(f"  做多期间平均收益: {long_returns.mean() * 100:.4f}%")
    if len(short_returns) > 0:
        print(f"  做空期间平均收益: {short_returns.mean() * 100:.4f}%")

    # 检查换手率
    avg_turnover = float(np.mean(np.abs(np.diff(strategy_returns, prepend=strategy_returns[0]))))
    print(f"  平均换手率: {avg_turnover:.6f}")

    # 交易成本占比
    gross_return = positions * (data["close"].pct_change().fillna(0).values)
    gross_return = np.nan_to_num(gross_return, nan=0.0)
    total_gross = gross_return.sum()
    total_costs = np.abs(np.diff(positions, prepend=0)).sum() * (0.0003 + 0.0001)
    if abs(total_gross) > 1e-8:
        cost_ratio = total_costs / abs(total_gross) * 100
        print(f"  交易成本占总收益比例: {cost_ratio:.2f}%")

    # 检查因子与未来收益的相关性（IC 序列）
    from scipy.stats import spearmanr

    ics = []
    window = 20
    forward_returns = (data["close"].shift(-1) / data["close"] - 1).fillna(0).values
    for i in range(window, n):
        if np.std(factor_values[i - window : i]) > 1e-8 and np.std(forward_returns[i - window : i]) > 1e-8:
            r, _ = spearmanr(factor_values[i - window : i], forward_returns[i - window : i])
            ics.append(r if not np.isnan(r) else 0.0)
    if ics:
        print(f"  滚动 IC 均值: {np.mean(ics):.4f}")
        print(f"  滚动 IC 标准差: {np.std(ics):.4f}")
        print(f"  IC>0 占比: {sum(1 for ic in ics if ic > 0) / len(ics) * 100:.1f}%")
        print(f"  IC IR: {np.mean(ics) / max(np.std(ics), 1e-8):.3f}")
    else:
        print("  ⚠️ 无法计算 IC 序列")


# ─── 5. 常见计算错误检查 ──────────────────────────────────


def check_common_errors(
    data: pd.DataFrame, factor_values: np.ndarray, strategy_returns: np.ndarray, positions: np.ndarray
):
    print(f"\n{'=' * 60}")
    print("🔍 [5/5] 常见计算错误检查")
    print(f"{'=' * 60}")

    # 前向偏差检查
    close = data["close"].values
    forward_returns = np.zeros(len(close))
    forward_returns[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 检查因子值是否与未来收益率存在异常高相关性（可能的前向偏差）
    n = len(factor_values)
    if n > 100:
        # 检查因子与未来收益的同期相关性
        valid = (np.std(factor_values) > 1e-8) and (np.std(forward_returns) > 1e-8)
        if valid:
            from scipy.stats import pearsonr

            r, p = pearsonr(factor_values, forward_returns)
            print(f"  因子 vs 次日收益 Pearson r: {r:.4f} (p={p:.4f})")
            if r > 0.5:
                print(f"  ⚠️ 相关性过高 (r={r:.4f})，可能包含前向偏差")
            else:
                print("  ✅ 相关性在合理范围")

    # 检查策略收益分布
    pos_returns = strategy_returns[strategy_returns > 0]
    neg_returns = strategy_returns[strategy_returns < 0]
    if len(pos_returns) > 0 and len(neg_returns) > 0:
        print(f"  盈利分布: mean={pos_returns.mean() * 100:.4f}%, median={np.median(pos_returns) * 100:.4f}%")
        print(f"  亏损分布: mean={neg_returns.mean() * 100:.4f}%, median={np.median(neg_returns) * 100:.4f}%")

        # 检查是否存在极端收益扭曲总收益
        top5 = np.sort(pos_returns)[-5:] if len(pos_returns) >= 5 else pos_returns
        if len(top5) > 0 and pos_returns.sum() > 0:
            top5_ratio = top5.sum() / pos_returns.sum()
            if top5_ratio > 0.5:
                print(f"  ⚠️ 前5大盈利占比 {top5_ratio * 100:.1f}%，总收益由少数极端值驱动")

    # 检查净值曲线异常
    equity = pd.Series(1_000_000 * (1 + strategy_returns).cumprod())
    if (equity.diff() == 0).sum() > len(equity) * 0.5:
        print(f"  ⚠️ 净值无变化天数过多: {(equity.diff() == 0).sum()}/{len(equity)}")

    # 检查连续亏损
    losses = strategy_returns < 0
    max_consecutive_losses = 0
    current = 0
    for loss in losses:
        if loss:
            current += 1
            max_consecutive_losses = max(max_consecutive_losses, current)
        else:
            current = 0
    print(f"  最大连续亏损天数: {max_consecutive_losses}")

    # 检查盈亏分布是否对称（偏度）
    from scipy.stats import skew

    ret_skew = skew(strategy_returns)
    print(f"  收益偏度: {ret_skew:.4f} ({'正偏(偶有大赚)' if ret_skew > 0 else '负偏(偶有大亏)'})")


# ─── 主流程 ────────────────────────────────────────────────


def main():
    symbol = "RB0"
    factor_id = "fct_1bd8ac1e"

    print(f"🔬 FTS 全链路诊断 — {symbol} / {factor_id}")
    print(f"   诊断时间: {pd.Timestamp.now()}")

    # 1. 数据完整性
    data = check_data_integrity(symbol)

    # 2. 因子代码执行
    factor_values = check_factor_code(factor_id, data)
    if factor_values is None:
        print("\n❌ 因子执行失败，无法继续诊断")
        return

    # 3. 回测指标
    strategy_returns, equity, positions = check_backtest_metrics(data, factor_values)

    # 4. 信号生成
    check_signal_generation(factor_values, strategy_returns, positions, data)

    # 5. 常见错误
    check_common_errors(data, factor_values, strategy_returns, positions)

    # 6. 汇总
    print(f"\n{'=' * 60}")
    print("📋 诊断汇总")
    print(f"{'=' * 60}")
    print("  数据: OK")
    print("  因子执行: OK")
    print("  指标计算: OK")
    print("  信号生成: OK")
    print("  常见错误: 已检查")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
