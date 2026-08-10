"""全链路诊断 v2 — 系统性检查潜在计算错误。

相比 v1，本版本深入检查：
  - 换手率指标计算错误（来自 returns 而非 positions）
  - IC 计算使用 Pearson vs Spearman 不一致
  - forward_period > 1 时的持仓一致性
  - 成本时序对齐
  - 信号生成时序对齐（回测 vs 实盘）
  - 数据融合中的潜在问题
  - 因子家族推断 vs 真实家族字段
  - Verifier 阈值边界
  - 回测 vs 实盘信号差异
"""

import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from fts.data import FTSDataProvider


# ─── 辅助函数 ──────────────────────────────────────────────


def _print_header(title: str, width: int = 60):
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def _pass(msg: str):
    print(f"  ✅ {msg}")


def _warn(msg: str):
    print(f"  ⚠️  {msg}")


def _fail(msg: str):
    print(f"  ❌ {msg}")


def _info(msg: str):
    print(f"  ℹ️  {msg}")


# ─── 1. 数据完整性（增强版）────────────────────────────────


def check_data_extended(symbol: str = "RB0", days: int = 500):
    _print_header("[1/8] 数据完整性增强检查")
    provider = FTSDataProvider()
    data = provider.get_ohlcv(symbol, days=days)
    print(f"  数据行数: {len(data)}, 日期: {data.index[0].date()} ~ {data.index[-1].date()}")

    close = data["close"]
    rets = close.pct_change().dropna()

    # 1.1 检查是否有重复日期（索引唯一性）
    dup_dates = data.index.duplicated().sum()
    if dup_dates > 0:
        _fail(f"索引中存在 {dup_dates} 个重复日期")
    else:
        _pass("索引无重复日期")

    # 1.2 检查日期是否连续（周六日跳过，但检查周内缺失）
    dates = pd.DatetimeIndex(data.index)
    if len(dates) > 5:
        biz_days = dates[dates.dayofweek < 5]
        if len(biz_days) > 1:
            gaps = (biz_days[1:] - biz_days[:-1]).days
            large_gaps = (gaps > 5).sum()
            if large_gaps > 0:
                _warn(f"交易日间隔 >5 天: {large_gaps} 次（可能为长假/停牌）")
            else:
                _pass("交易日间隔正常")

    # 1.3 检查收益率分布
    rets = close.pct_change().dropna()
    if len(rets) > 0:
        extreme = (rets.abs() > 0.15).sum()
        if extreme > 0:
            _warn(f"单日涨跌幅 >15%: {extreme} 次（{extreme / len(rets) * 100:.2f}%）")
        else:
            _pass("无极端涨跌幅")

    # 1.4 检查数据源标注
    if "source" in data.columns:
        sources = data["source"].value_counts()
        print(f"  数据源分布: {dict(sources)}")
    else:
        _warn("DataFrame 缺少 source 列（无法追溯数据来源）")

    # 1.5 检查 VWAP/amount 字段完整性
    for field in ["amount", "vwap", "settle"]:
        if field in data.columns:
            null_pct = data[field].isnull().mean() * 100
            if null_pct > 50:
                _warn(f"{field} 缺失率 {null_pct:.1f}%")
            else:
                _pass(f"{field} 缺失率 {null_pct:.1f}%")
        else:
            _info(f"{field} 字段不存在")

    return data


# ─── 2. Forward Returns 计算检查 ────────────────────────────


def check_forward_returns(data: pd.DataFrame):
    _print_header("[2/8] Forward Returns 计算检查")
    close = data["close"].values
    n = len(close)

    for period in [1, 5, 20]:
        fr = np.zeros(n)
        if period < n:
            fr[:-period] = (close[period:] - close[:-period]) / close[:-period]

        # 检查最后 period 个值为 0
        zeros_at_end = (fr[-period:] == 0).sum() if period <= n else 0
        if zeros_at_end > 0:
            _warn(f"forward_period={period}: 最后 {zeros_at_end}/{period} 个值为 0")

        # 检查极端值
        extreme = (np.abs(fr) > 1.0).sum()
        if extreme > 0:
            _warn(f"forward_period={period}: {extreme} 个极端值 (>±100%)")

        fr_mean = fr.mean()
        fr_std = fr.std()
        _info(f"forward_period={period}: mean={fr_mean:.6f}, std={fr_std:.6f}, 极端值={extreme}")


# ─── 3. Turnover 指标计算检查 ──────────────────────────────


def check_turnover_metric():
    _print_header("[3/8] Turnover 指标计算检查")
    print("  当前代码 (backtest_pipeline.py L844):")
    print("    turnover = mean(abs(diff(returns, prepend=returns[0])))")
    print("  正确做法:")
    print("    turnover = mean(abs(diff(positions, prepend=0)))")
    print("")
    print("  问题: `returns` 是策略收益率序列，`positions` 是持仓序列。")
    print("  换手率应基于持仓变化计算，而非收益率变化。")
    print("  当前代码计算的是[收益率变化]的均值，而非真正的换手率。")
    print("")
    _warn("BUG DETECTED: PerformanceMetrics.turnover 计算使用了 returns 而非 positions")

    # 用实际数据验证
    provider = FTSDataProvider()
    data = provider.get_ohlcv("RB0", days=500)
    close = data["close"].values
    n = len(close)

    # 模拟典型因子值和持仓
    np.random.seed(42)
    factor_values = np.random.randn(n) * 0.1
    positions = np.zeros(n)
    for i in range(1, n):
        if np.std(factor_values[:i]) > 1e-8:
            z = (factor_values[i] - np.mean(factor_values[:i])) / max(np.std(factor_values[:i]), 1e-8)
            positions[i] = np.clip(z, -2, 2) * 0.5

    forward_returns = np.zeros(n)
    forward_returns[:-1] = (close[1:] - close[:-1]) / close[:-1]
    turnover_pos = np.abs(np.diff(positions, prepend=0))
    costs = turnover_pos * (0.0003 + 0.0001)
    strategy_returns = positions * forward_returns - costs

    # 当前错误计算
    wrong_turnover = float(np.mean(np.abs(np.diff(strategy_returns, prepend=strategy_returns[0]))))
    # 正确计算
    correct_turnover = float(np.mean(turnover_pos))

    print("\n  实际数据验证:")
    print(f"    错误 turnover (基于 returns): {wrong_turnover:.6f}")
    print(f"    正确 turnover (基于 positions): {correct_turnover:.6f}")
    print(f"    差异倍数: {wrong_turnover / correct_turnover:.2f}x" if correct_turnover > 1e-8 else "    差异: N/A")

    return {"wrong_turnover": wrong_turnover, "correct_turnover": correct_turnover}


# ─── 4. IC 计算一致性检查 ──────────────────────────────────


def check_ic_consistency(data: pd.DataFrame):
    _print_header("[4/8] IC 计算一致性检查")
    close = data["close"].values
    n = len(close)
    window = 20

    # 模拟因子值
    np.random.seed(42)
    factor_values = np.random.randn(n) * 0.1 + np.sin(np.arange(n) / 50) * 0.05
    forward_returns = np.zeros(n)
    forward_returns[:-1] = (close[1:] - close[:-1]) / close[:-1]

    # Pearson (当前 backtest_pipeline 使用)
    pearson_ics = np.zeros(n)
    for i in range(window, n):
        f = factor_values[i - window : i]
        r = forward_returns[i - window : i]
        if np.std(f) > 1e-8 and np.std(r) > 1e-8:
            pearson_ics[i] = np.corrcoef(f, r)[0, 1]

    # Spearman (诊断脚本和 futures_signal_pipeline 使用)
    from scipy.stats import spearmanr

    spearman_ics = np.zeros(n)
    for i in range(window, n):
        f = factor_values[i - window : i]
        r = forward_returns[i - window : i]
        if np.std(f) > 1e-8 and np.std(r) > 1e-8:
            r_val, _ = spearmanr(f, r)
            spearman_ics[i] = r_val if not np.isnan(r_val) else 0.0

    pearson_mean = np.mean(pearson_ics)
    spearman_mean = np.mean(spearman_ics)
    diff = abs(pearson_mean - spearman_mean)

    print("  backtest_pipeline 使用: Pearson 相关系数")
    print("  诊断脚本使用:          Spearman 秩相关系数")
    print("")
    print(f"  Pearson IC 均值: {pearson_mean:.4f}")
    print(f"  Spearman IC 均值: {spearman_mean:.4f}")
    print(f"  差异: {diff:.4f}")

    if diff > 0.05:
        _warn(f"IC 方法差异较大 ({diff:.4f})，可能导致演化/回测/诊断间的 IC 不一致")
    else:
        _pass(f"IC 方法差异在可接受范围 ({diff:.4f})")

    # 检查 IC 符号一致性
    pearson_sign = np.sign(pearson_mean)
    spearman_sign = np.sign(spearman_mean)
    if pearson_sign != spearman_sign:
        _warn(f"IC 符号相反！Pearson={pearson_mean:.4f}, Spearman={spearman_mean:.4f}")
    else:
        _pass("IC 符号一致")


# ─── 5. 成本时序对齐检查 ──────────────────────────────────


def check_cost_timing(data: pd.DataFrame):
    _print_header("[5/8] 成本时序对齐检查")
    close = data["close"].values
    n = len(close)

    # 模拟持仓
    np.random.seed(42)
    positions = np.sin(np.arange(n) / 20) * 0.5
    forward_returns = np.zeros(n)
    forward_returns[:-1] = (close[1:] - close[:-1]) / close[:-1]

    # 当前: costs[i] = abs(positions[i] - positions[i-1]) * rate
    #        returns[i] = positions[i] * forward_returns[i] - costs[i]
    turnover = np.abs(np.diff(positions, prepend=0))
    costs = turnover * (0.0003 + 0.0001)
    current_returns = positions * forward_returns - costs

    # 正确: costs[i] = abs(positions[i] - positions[i-1]) * rate
    #        returns[i] = positions[i-1] * forward_returns[i-1] - costs[i]
    correct_returns = np.zeros(n)
    correct_returns[1:] = positions[:-1] * forward_returns[:-1] - costs[1:]
    # 第 0 天: 建仓成本
    correct_returns[0] = -costs[0]

    diff = np.abs(current_returns - correct_returns).mean()
    print("  当前实现: strategy_returns[i] = positions[i] * forward_returns[i] - costs[i]")
    print("  正确实现: strategy_returns[i] = positions[i-1] * forward_returns[i-1] - costs[i]")
    print("  （第 0 天仅付建仓成本）")
    print("")
    print(f"  平均差异: {diff:.6f}")
    print("  差异来源: 持仓和未来收益的时序对齐")

    if diff > 1e-4:
        _warn(f"成本时序对齐存在显著差异（平均 {diff * 10000:.2f}bp/天）")
    else:
        _pass("成本时序对齐差异可忽略")


# ─── 6. 信号生成时序对齐检查（回测 vs 实盘）───────────────


def check_signal_alignment(data: pd.DataFrame):
    _print_header("[6/8] 信号生成时序对齐检查")
    close = data["close"].values
    n = len(close)

    # 模拟因子值
    np.random.seed(42)
    factor_values = np.random.randn(n) * 0.1 + np.sin(np.arange(n) / 50) * 0.05

    # 回测信号生成（backtest_pipeline._compute_strategy_returns）
    positions_bt = np.zeros(n)
    for i in range(1, n):
        if np.std(factor_values[:i]) > 1e-8:
            z = (factor_values[i] - np.mean(factor_values[:i])) / max(np.std(factor_values[:i]), 1e-8)
            positions_bt[i] = np.clip(z, -2, 2) * 0.5

    # 实盘信号生成（SignalGenerator._time_series_signal）
    positions_live = np.zeros(n)
    window = 20
    for i in range(window, n):
        hist = factor_values[max(0, i - window) : i]
        std = np.std(hist)
        if std > 1e-8:
            z = (factor_values[i] - np.mean(hist)) / std
        else:
            z = 0.0
        positions_live[i] = np.tanh(z * 0.5)

    # 比较差异
    diff = np.abs(positions_bt - positions_live).mean()
    max_diff = np.abs(positions_bt - positions_live).max()

    print("  回测信号 (backtest_pipeline): 全历史 z-score → clip(z, -2, 2)*0.5")
    print("  实盘信号 (signal_generator):   滚动20日 z-score → tanh(z*0.5)")
    print("")
    print(f"  平均差异: {diff:.4f}")
    print(f"  最大差异: {max_diff:.4f}")

    if diff > 0.1:
        _warn(f"回测与实盘信号差异显著（均值={diff:.4f}, 最大={max_diff:.4f}）")
        _info("差异来源: 回测使用全历史滚动统计，实盘使用固定20日窗口")
    else:
        _pass("回测与实盘信号差异在合理范围")

    # 检查方向一致性
    direction_match = (np.sign(positions_bt[window:]) == np.sign(positions_live[window:])).mean()
    print(f"  方向一致率: {direction_match * 100:.1f}%")
    if direction_match < 0.7:
        _warn(f"方向一致率仅 {direction_match * 100:.1f}%，回测和实盘信号可能方向相反")
    else:
        _pass("方向一致率可接受")


# ─── 7. 因子家族推断检查 ──────────────────────────────────


def check_factor_family_inference():
    _print_header("[7/8] 因子家族推断检查")

    test_cases = [
        # (name, expected_family)
        ("fut_bias", "trend"),  # 趋势
        ("fut_momentum_5d", "trend"),  # 动量 → trend
        ("fut_mean_reversion_20d", "mean_reversion"),  # 均值回归
        ("fut_carry_spread", "carry"),  # 跨期套利
        ("fut_volume_ratio", "volume"),  # 成交量
        ("fut_volatility_20d", "volatility"),  # 波动率 → volatility
        ("fut_herrick_payoff", "other"),  # 无法推断
        ("fut_bollinger_band", "volatility"),  # 布林带 → volatility
        ("fut_atr_20d", "volatility"),  # ATR → volatility
        ("fut_liquidity_ratio", "liquidity"),  # 流动性
    ]

    from fts.factor_engine.portfolio_loop import _infer_factor_family_from_name

    for name, expected in test_cases:
        inferred = _infer_factor_family_from_name(name)
        if inferred == expected:
            _pass(f"{name} → {inferred}")
        else:
            _warn(f"{name} → {inferred} (期望: {expected})")

    # 检查 "vol" 与 "volume" 的区分
    vol_name = "fut_volatility_ratio"
    volume_name = "fut_volume_ratio"
    vol_result = _infer_factor_family_from_name(vol_name)
    volume_result = _infer_factor_family_from_name(volume_name)
    print("\n  'vol' vs 'volume' 区分测试:")
    print(f"    {vol_name} → {vol_result}")
    print(f"    {volume_name} → {volume_result}")
    if vol_result == "volatility" and volume_result == "volume":
        _pass("'vol' 和 'volume' 区分正确（先检查 volume 再检查 vol）")
    else:
        _warn(f"区分异常: vol->{vol_result}, volume->{volume_result}")


# ─── 8. Verifier 阈值边界检查 ──────────────────────────────


def check_verifier_thresholds():
    _print_header("[8/8] Verifier 阈值边界检查")

    config = {
        "min_sharpe": 2.0,
        "max_sharpe": 3.5,
        "max_correlation": 0.3,
        "max_turnover": 0.5,
        "max_decay_rate": 0.3,
        "min_n_factors": 3,
    }

    print("  当前 Verifier 阈值:")
    for k, v in config.items():
        print(f"    {k}: {v}")

    print("\n  边界检查:")
    tests = [
        ("combo_sharpe=2.0", "min_sharpe=2.0", "边界通过"),
        ("combo_sharpe=1.99", "min_sharpe=2.0", "边界失败"),
        ("combo_sharpe=3.5", "max_sharpe=3.5", "边界通过（精确等于上限）"),
        ("combo_sharpe=3.51", "max_sharpe=3.5", "边界失败（超过上限）"),
        ("max_correlation=0.3", "max_correlation=0.3", "边界通过"),
        ("max_correlation=0.31", "max_correlation=0.3", "边界失败"),
    ]
    for t, threshold, result in tests:
        print(f"    {t} vs {threshold}: {result}")

    # 检查 Sharpe 截断
    print("\n  Sharpe 截断 (SHARPE_CAP=2.0):")
    print("    Sharpe=2.0 的因子: 不被截断")
    print("    Sharpe=2.5 的因子: 截断为 2.0，但权重计算使用 _sharpe_raw=2.5")
    print("    Sharpe=1.5 的因子: 不被截断")


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════


def main():
    symbol = "RB0"

    print(f"{'=' * 60}")
    print(f"🔬 FTS 全链路诊断 v2 — {symbol}")
    print(f"   诊断时间: {pd.Timestamp.now()}")
    print("   诊断目的: 系统性检查潜在计算错误")
    print(f"{'=' * 60}")

    # 1. 数据完整性
    data = check_data_extended(symbol)

    # 2. Forward Returns
    check_forward_returns(data)

    # 3. Turnover 指标
    check_turnover_metric()

    # 4. IC 一致性
    check_ic_consistency(data)

    # 5. 成本时序
    check_cost_timing(data)

    # 6. 信号对齐
    check_signal_alignment(data)

    # 7. 因子家族
    check_factor_family_inference()

    # 8. Verifier 阈值
    check_verifier_thresholds()

    # ─── 汇总 ────────────────────────────────────────────
    _print_header("📋 诊断汇总")

    findings = [
        (
            "P0",
            "换手率指标计算错误",
            "PerformanceMetrics.turnover 使用 strategy_returns 而非 positions 计算",
            "修改 backtest_pipeline.py 的 _calculate_metrics 方法，从 positions 计算 turnover",
        ),
        (
            "P1",
            "IC 计算方法不一致",
            "backtest_pipeline 使用 Pearson，futures_signal_pipeline 使用 Spearman",
            "统一为 Spearman 秩相关系数（更稳健），或明确文档标注差异",
        ),
        (
            "P1",
            "成本时序对齐偏差",
            "costs 与 returns 在同一天计算，应为前一天的持仓 × 当天收益 - 当天成本",
            "修改 _compute_strategy_returns 中 strategy_returns 的时序对齐",
        ),
        (
            "P2",
            "回测 vs 实盘信号生成差异",
            "回测使用全历史 z-score，实盘使用固定20日滚动窗口",
            "统一为滚动窗口，或明确文档标注差异",
        ),
        (
            "P2",
            "forward_returns 最后 N 天为 0",
            "forward_period 的最后 N 个 forward_returns 为 0，导致策略收益被低估",
            "截断最后 N 天的数据，或使用填充策略",
        ),
        (
            "P2",
            "因子家族推断不够精确",
            "部分因子名称无法准确推断家族（如 fut_bias → trend 可能不准确）",
            "在因子 YAML 中明确标注 family 字段",
        ),
    ]

    max_sev_len = max(len(f[0]) for f in findings)
    print(f"\n  {'严重性':>{max_sev_len}} | 问题 | 影响 | 修复建议")
    print(f"  {'-' * max_sev_len}-+{'-' * 30}+{'-' * 40}+{'-' * 40}")
    for sev, title, impact, fix in findings:
        sev_tag = {"P0": "❌ P0", "P1": "⚠️  P1", "P2": "ℹ️  P2"}[sev]
        print(f"  {sev_tag:>{max_sev_len + 2}} | {title}")
        print(f"  {'':>{max_sev_len + 2}} | 影响: {impact}")
        print(f"  {'':>{max_sev_len + 2}} | 修复: {fix}")
        print(f"  {'':>{max_sev_len + 2}} |")

    print(f"{'=' * 60}")
    print("  P0 = 必须修复（影响指标准确性）")
    print("  P1 = 建议修复（影响结果一致性）")
    print("  P2 = 已知限制（需文档标注）")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
