"""
scripts/fut_gp_alpha1_diagnostic.py — fut_gp_alpha1 全面验证

验证维度：
1. 信号分布：是否退化为 ±1 开关信号
2. 换手率：信号变化频率
3. VWAP 近似有效性
4. 分品种 IC
5. 分年度 IC 稳定性
6. 信号自相关
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from datetime import date

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FACTOR_PATH = PROJECT_ROOT / "memory/knowledge/factors/futures_elite/fct_d9a1d0fd.json"


def load_factor():
    return json.loads(FACTOR_PATH.read_text(encoding="utf-8"))


def load_panel(days=500, max_symbols=0):
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_SUBSET

    FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
    symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL]
    if max_symbols > 0:
        symbols = symbols[:max_symbols]
    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(symbols=symbols, days=days)
    return panel, common_dates, symbols


def execute_factor(factor_data, df):
    from fts.factor_engine.factor_program import FactorExecutor

    executor = FactorExecutor(factor_data)
    sig = executor.execute(df, factor_data.get("params", {}))
    return np.array(sig, dtype=float)


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def main():
    today = date.today().isoformat()
    print(f"fut_gp_alpha1 全面验证 — {today}")

    # ── 加载 ──
    factor_data = load_factor()
    print(f"因子: {factor_data['name']} ({factor_data['factor_id']})")
    print(f"逻辑: {factor_data['economic_logic']['narrative']}")

    panel, common_dates, symbols = load_panel(days=500)
    n_sym = len(panel)
    n_dates = len(common_dates)
    print(f"数据: {n_sym} 品种 × {n_dates} 交易日")
    print(f"日期范围: {common_dates[0]} ~ {common_dates[-1]}")

    # ── 逐品种执行因子 ──
    section("1. 信号分布分析")

    all_signals = {}
    all_vwap_raw = {}  # 保存中间变量用于分析
    sym_stats = []

    for sym, df in panel.items():
        if df is None or len(df) < 30:
            continue
        try:
            sig = execute_factor(factor_data, df)
            sig = np.where(np.isfinite(sig), sig, np.nan)
            all_signals[sym] = sig

            # 提取中间变量用于 VWAP 分析
            close = df["close"].values
            high = df["high"].values
            low = df["low"].values
            volume = df["volume"].values
            n = len(close)

            vwap = np.convolve(close * volume, np.ones(5) / 5, mode="same") / np.maximum(
                np.convolve(volume, np.ones(5) / 5, mode="same"), 1e-10
            )
            hl = np.maximum(high - low, 1e-10)
            raw = (close - vwap) * volume / hl

            all_vwap_raw[sym] = {
                "close": close,
                "vwap": vwap,
                "raw": raw,
                "volume": volume,
                "high": high,
                "low": low,
                "signal": sig,
            }

            valid = sig[~np.isnan(sig)]
            if len(valid) == 0:
                continue

            # 信号统计
            unique_vals = np.unique(valid)
            at_plus1 = np.sum(valid >= 0.99) / len(valid)
            at_minus1 = np.sum(valid <= -0.99) / len(valid)
            at_boundary = at_plus1 + at_minus1
            std = np.std(valid)
            mean = np.mean(valid)

            # 换手率：信号变化次数 / 总天数
            changes = np.sum(np.abs(np.diff(valid[~np.isnan(valid)])) > 0.01)
            valid_len = np.sum(~np.isnan(sig[:-1]) & ~np.isnan(sig[1:]))
            turnover = changes / max(valid_len, 1)

            sym_stats.append(
                {
                    "sym": sym,
                    "n_unique": len(unique_vals),
                    "at_boundary": at_boundary,
                    "at_plus1": at_plus1,
                    "at_minus1": at_minus1,
                    "std": std,
                    "mean": mean,
                    "turnover": turnover,
                    "n_valid": len(valid),
                }
            )
        except Exception:
            continue

    # 汇总信号分布
    print(f"\n有效品种: {len(sym_stats)}/{n_sym}")
    if not sym_stats:
        print("ERROR: 无有效信号")
        return

    boundary_ratios = [s["at_boundary"] for s in sym_stats]
    stds = [s["std"] for s in sym_stats]
    turnovers = [s["turnover"] for s in sym_stats]
    n_uniques = [s["n_unique"] for s in sym_stats]

    print("\n信号边界值占比 (|sig| >= 0.99):")
    print(f"  平均: {np.mean(boundary_ratios):.1%}")
    print(f"  中位数: {np.median(boundary_ratios):.1%}")
    print(f"  最小: {np.min(boundary_ratios):.1%}")
    print(f"  最大: {np.max(boundary_ratios):.1%}")
    print(f"  100%边界品种数: {sum(1 for b in boundary_ratios if b > 0.99)}/{len(sym_stats)}")

    print("\n信号标准差:")
    print(f"  平均: {np.mean(stds):.4f}")
    print(f"  中位数: {np.median(stds):.4f}")
    print(f"  std=0 品种数: {sum(1 for s in stds if s < 0.01)}/{len(sym_stats)}")

    print("\n信号唯一值数量:")
    print(f"  平均: {np.mean(n_uniques):.1f}")
    print(f"  <=5 个品种数: {sum(1 for n in n_uniques if n <= 5)}/{len(sym_stats)}")

    print("\n日换手率 (信号变化频率):")
    print(f"  平均: {np.mean(turnovers):.4f}")
    print(f"  中位数: {np.median(turnovers):.4f}")
    print(f"  零换手品种数: {sum(1 for t in turnovers if t < 0.001)}/{len(sym_stats)}")

    # ── VWAP 近似有效性 ──
    section("2. VWAP 近似有效性")

    vwap_deviation_stats = []
    volume_zero_stats = []

    for sym, data in all_vwap_raw.items():
        close = data["close"]
        vwap = data["vwap"]
        volume = data["volume"]
        raw = data["raw"]

        # VWAP 偏离度
        deviation = (close - vwap) / np.maximum(close, 1e-10)
        valid_dev = deviation[~np.isnan(deviation)]
        if len(valid_dev) > 0:
            vwap_deviation_stats.append(
                {
                    "sym": sym,
                    "mean_dev": np.mean(valid_dev),
                    "std_dev": np.std(valid_dev),
                    "max_dev": np.max(np.abs(valid_dev)),
                }
            )

        # volume=0 比例
        vol_zero = np.sum(volume < 1) / len(volume)
        volume_zero_stats.append({"sym": sym, "vol_zero_pct": vol_zero})

    mean_devs = [s["mean_dev"] for s in vwap_deviation_stats]
    std_devs = [s["std_dev"] for s in vwap_deviation_stats]
    vol_zeros = [s["vol_zero_pct"] for s in volume_zero_stats]

    print("\n(close - vwap) / close 偏离度:")
    print(f"  平均偏离: {np.mean(mean_devs):.4%}")
    print(f"  偏离标准差: {np.mean(std_devs):.4%}")
    print(f"  最大偏离: {np.max([s['max_dev'] for s in vwap_deviation_stats]):.4%}")

    print("\n成交量=0 比例:")
    print(f"  平均: {np.mean(vol_zeros):.2%}")
    print(f"  有零成交量品种: {sum(1 for v in vol_zeros if v > 0.01)}/{len(volume_zero_stats)}")

    # ── tanh 饱和分析 ──
    section("3. tanh 饱和分析")

    saturation_stats = []
    for sym, data in all_vwap_raw.items():
        raw = data["raw"]
        valid_raw = raw[~np.isnan(raw)]
        if len(valid_raw) == 0:
            continue
        # tanh(x*3) 在 |x| > 1 时接近 ±1
        # 即 raw/std * 3 > 3 => raw > std
        std_raw = np.std(valid_raw)
        if std_raw < 1e-10:
            saturation_stats.append({"sym": sym, "pct_saturated": 1.0, "std_raw": std_raw})
            continue
        normalized = np.abs(valid_raw) / std_raw * 3
        saturated = np.sum(normalized > 2.5) / len(normalized)  # tanh(2.5) = 0.987
        saturation_stats.append(
            {
                "sym": sym,
                "pct_saturated": saturated,
                "std_raw": std_raw,
            }
        )

    sat_pcts = [s["pct_saturated"] for s in saturation_stats]
    print("\ntanh 输入值 |x|*3/std > 2.5 的比例 (饱和区):")
    print(f"  平均: {np.mean(sat_pcts):.1%}")
    print(f"  中位数: {np.median(sat_pcts):.1%}")
    print(f"  >90% 饱和品种数: {sum(1 for s in sat_pcts if s > 0.9)}/{len(sat_pcts)}")
    print(f"  100% 饱和品种数: {sum(1 for s in sat_pcts if s > 0.99)}/{len(sat_pcts)}")

    # ── 分品种 IC ──
    section("4. 分品种 IC (时序 IC: 信号 vs 未来5日收益)")

    from scipy.stats import spearmanr

    per_sym_ic = []
    for sym, data in all_vwap_raw.items():
        sig = data["signal"]
        close = data["close"]
        n = len(close)
        if n < 10:
            continue

        fwd_ret = np.full(n, np.nan)
        for t in range(n - 5):
            if close[t] > 1e-10:
                fwd_ret[t] = (close[t + 5] - close[t]) / close[t]

        valid = ~np.isnan(sig) & ~np.isnan(fwd_ret)
        if np.sum(valid) < 10:
            continue

        ic, _ = spearmanr(sig[valid], fwd_ret[valid])
        if np.isnan(ic):
            continue
        per_sym_ic.append({"sym": sym, "ic": ic})

    if per_sym_ic:
        ics = [s["ic"] for s in per_sym_ic]
        print(f"\n有效品种: {len(per_sym_ic)}")
        print(f"  平均 IC: {np.mean(ics):.4f}")
        print(f"  中位数 IC: {np.median(ics):.4f}")
        print(f"  IC > 0.05: {sum(1 for i in ics if i > 0.05)}/{len(ics)}")
        print(f"  IC < -0.05: {sum(1 for i in ics if i < -0.05)}/{len(ics)}")
        print(f"  |IC| < 0.01: {sum(1 for i in ics if abs(i) < 0.01)}/{len(ics)}")

        # Top/Bottom 5
        sorted_ic = sorted(per_sym_ic, key=lambda x: x["ic"], reverse=True)
        print("\n  Top 5 IC:")
        for s in sorted_ic[:5]:
            print(f"    {s['sym']}: {s['ic']:+.4f}")
        print("  Bottom 5 IC:")
        for s in sorted_ic[-5:]:
            print(f"    {s['sym']}: {s['ic']:+.4f}")
    else:
        print("  无有效 IC 结果")

    # ── 截面 IC 时序 ──
    section("5. 截面 IC 时序稳定性 (每日截面 Spearman)")

    # 构建 sym × date 矩阵
    sym_list = list(all_signals.keys())
    sig_matrix = np.full((n_dates, len(sym_list)), np.nan)
    ret_matrix = np.full((n_dates, len(sym_list)), np.nan)

    for j, sym in enumerate(sym_list):
        sig = all_signals[sym]
        df = panel[sym]
        close = df["close"].values
        arr_len = min(len(sig), len(close))

        sig_matrix[:arr_len, j] = sig[:arr_len]

        fwd_ret = np.full(len(close), np.nan)
        for t in range(len(close) - 5):
            if close[t] > 1e-10:
                fwd_ret[t] = (close[t + 5] - close[t]) / close[t]
        ret_matrix[:arr_len, j] = fwd_ret[:arr_len]

    # 每日截面 IC
    daily_ics = []
    daily_dates = []
    for t in range(n_dates):
        valid = ~np.isnan(sig_matrix[t]) & ~np.isnan(ret_matrix[t])
        if np.sum(valid) >= 5:
            ic, _ = spearmanr(sig_matrix[t, valid], ret_matrix[t, valid])
            if not np.isnan(ic):
                daily_ics.append(ic)
                daily_dates.append(common_dates[t])

    if daily_ics:
        ics_arr = np.array(daily_ics)
        print(f"\n有效交易日: {len(daily_ics)}/{n_dates}")
        print(f"  平均 IC: {np.mean(ics_arr):.4f}")
        print(f"  IC 标准差: {np.std(ics_arr):.4f}")
        print(f"  ICIR: {np.mean(ics_arr) / max(np.std(ics_arr), 1e-10):.4f}")
        print(f"  IC > 0 比例: {np.mean(ics_arr > 0):.1%}")
        print(f"  |IC| > 0.05: {sum(1 for i in ics_arr if abs(i) > 0.05)}/{len(ics_arr)}")

        # 分年度
        years = {}
        for d, ic in zip(daily_dates, daily_ics):
            y = str(d)[:4]
            years.setdefault(y, []).append(ic)
        print("\n  分年度 IC:")
        for y in sorted(years.keys()):
            y_ics = years[y]
            print(
                f"    {y}: IC={np.mean(y_ics):+.4f}, std={np.std(y_ics):.4f}, "
                f"ICIR={np.mean(y_ics) / max(np.std(y_ics), 1e-10):.4f}, n={len(y_ics)}"
            )

        # 滚动 60 日 IC
        if len(ics_arr) >= 60:
            print("\n  60日滚动 IC (最近):")
            recent_60 = ics_arr[-60:]
            print(f"    最近60日 IC={np.mean(recent_60):+.4f}, std={np.std(recent_60):.4f}")
            old_60 = ics_arr[:60]
            print(f"    最早60日 IC={np.mean(old_60):+.4f}, std={np.std(old_60):.4f}")
    else:
        print("  无有效截面 IC")

    # ── 信号自相关 ──
    section("6. 信号自相关 (品种平均)")

    autocorrs = []
    for sym, data in all_vwap_raw.items():
        sig = data["signal"]
        valid = sig[~np.isnan(sig)]
        if len(valid) < 10:
            continue
        # lag-1 自相关
        ac = np.corrcoef(valid[:-1], valid[1:])[0, 1]
        if not np.isnan(ac):
            autocorrs.append(ac)

    if autocorrs:
        print("\nLag-1 自相关:")
        print(f"  平均: {np.mean(autocorrs):.4f}")
        print(f"  中位数: {np.median(autocorrs):.4f}")
        print(f"  >0.99 品种数: {sum(1 for a in autocorrs if a > 0.99)}/{len(autocorrs)}")
        print(f"  >0.95 品种数: {sum(1 for a in autocorrs if a > 0.95)}/{len(autocorrs)}")
    else:
        print("  无有效自相关数据")

    # ── 综合诊断结论 ──
    section("7. 综合诊断结论")

    issues = []

    # 检查1: 信号退化
    avg_boundary = np.mean(boundary_ratios)
    if avg_boundary > 0.90:
        issues.append(f"🔴 严重: {avg_boundary:.1%} 信号处于 ±1 边界 (tanh 饱和)")
    elif avg_boundary > 0.70:
        issues.append(f"⚠️  警告: {avg_boundary:.1%} 信号处于 ±1 边界")

    # 检查2: 零换手
    avg_turnover = np.mean(turnovers)
    if avg_turnover < 0.001:
        issues.append(f"🔴 严重: 平均日换手率 {avg_turnover:.6f} (几乎不变)")
    elif avg_turnover < 0.01:
        issues.append(f"⚠️  警告: 平均日换手率 {avg_turnover:.4f} (极低)")

    # 检查3: 信号标准差
    avg_std = np.mean(stds)
    if avg_std < 0.05:
        issues.append(f"🔴 严重: 平均信号标准差 {avg_std:.4f} (信号几乎恒定)")
    elif avg_std < 0.15:
        issues.append(f"⚠️  警告: 平均信号标准差 {avg_std:.4f} (信号变化很小)")

    # 检查4: 自相关
    if autocorrs:
        avg_ac = np.mean(autocorrs)
        if avg_ac > 0.99:
            issues.append(f"🔴 严重: Lag-1 自相关 {avg_ac:.4f} (信号近乎常数)")
        elif avg_ac > 0.95:
            issues.append(f"⚠️  警告: Lag-1 自相关 {avg_ac:.4f} (信号变化极慢)")

    # 检查5: IC 稳定性
    if daily_ics:
        avg_ic = np.mean(ics_arr)
        if abs(avg_ic) < 0.01:
            issues.append(f"🔴 严重: 截面 IC 仅 {avg_ic:.4f} (无预测能力)")
        elif abs(avg_ic) < 0.03:
            issues.append(f"⚠️  警告: 截面 IC 仅 {avg_ic:.4f} (预测能力弱)")

    # 检查6: VWAP 偏离
    avg_vwap_dev = np.mean(mean_devs)
    if avg_vwap_dev < 0.001:
        issues.append(f"⚠️  警告: VWAP 平均偏离仅 {avg_vwap_dev:.4%} (5日均量近似 VWAP 太粗糙)")

    if not issues:
        print("\n✅ 所有检查通过，因子表现正常")
    else:
        print(f"\n发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"  {issue}")

    # 最终建议
    print(f"\n{'─' * 60}")
    red_count = sum(1 for i in issues if "🔴" in i)
    yellow_count = sum(1 for i in issues if "⚠️" in i)
    if red_count >= 2:
        print("📌 建议: 降级该因子 — 多个严重问题，因子在日频数据上已退化")
    elif red_count >= 1:
        print("📌 建议: 标记观察 — 存在严重问题，需跟踪后续表现")
    elif yellow_count >= 2:
        print("📌 建议: 标记警告 — 存在多个潜在问题，建议优化 VWAP 近似方式")
    else:
        print("📌 建议: 保留 — 因子表现可接受")

    print(f"\n报告完成: {today}")


if __name__ == "__main__":
    main()
