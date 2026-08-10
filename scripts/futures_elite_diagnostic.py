"""
scripts/futures_elite_diagnostic.py — 期货精英因子全面诊断

批量诊断所有期货精英因子，生成详细报告。

诊断维度：
1. 信号分布：是否退化为 ±1 开关信号
2. 换手率：信号变化频率
3. VWAP 近似有效性（针对使用 VWAP 的因子）
4. 分品种 IC（时序 IC）
5. 分年度 IC 稳定性（截面 IC）
6. 信号自相关

用法:
    python scripts/futures_elite_diagnostic.py [--factor-id fct_xxx] [--days 500]

输出:
    reports/{date}/futures_elite_diagnostic_{date}.md
    reports/{date}/factor_details/{factor_id}.md (每个因子详细报告)
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FUTURES_ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
REPORTS_ROOT = PROJECT_ROOT / "reports"


def load_all_elite_factors() -> list[dict[str, Any]]:
    """加载所有期货精英因子"""
    factors = []
    for fp in sorted(FUTURES_ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["_filepath"] = str(fp)
            factors.append(data)
        except Exception as e:
            print(f"  警告: 无法加载 {fp.name}: {e}")
    return factors


def load_panel(days=500, max_symbols=0):
    """加载期货数据面板"""
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
    """执行因子计算"""
    from fts.factor_engine.factor_program import FactorExecutor

    executor = FactorExecutor(factor_data)
    sig = executor.execute(df, factor_data.get("params", {}))
    return np.array(sig, dtype=float)


def diagnose_factor(
    factor_data: dict[str, Any],
    panel: dict[str, pd.DataFrame],
    common_dates: list,
) -> dict[str, Any]:
    """诊断单个因子"""
    from scipy.stats import spearmanr

    factor_id = factor_data.get("factor_id", "unknown")
    factor_name = factor_data.get("name", "unknown")

    result = {
        "factor_id": factor_id,
        "factor_name": factor_name,
        "n_symbols": 0,
        "n_dates": len(common_dates),
        "date_range": f"{common_dates[0]} ~ {common_dates[-1]}",
        "signal_stats": {},
        "vwap_stats": {},
        "tanh_stats": {},
        "per_symbol_ic": [],
        "cross_section_ic": {},
        "autocorrelation": {},
        "issues": [],
        "recommendation": "",
    }

    # 逐品种执行因子
    all_signals = {}
    all_vwap_raw = {}
    sym_stats = []

    for sym, df in panel.items():
        if df is None or len(df) < 30:
            continue
        try:
            sig = execute_factor(factor_data, df)
            sig = np.where(np.isfinite(sig), sig, np.nan)
            all_signals[sym] = sig

            # 提取中间变量（用于 VWAP 分析）
            close = df["close"].values
            high = df["high"].values
            low = df["low"].values
            volume = df["volume"].values

            # 检查是否使用 VWAP
            code = factor_data.get("code", "")
            if "vwap" in code.lower():
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

            # 换手率
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

    result["n_symbols"] = len(sym_stats)

    if not sym_stats:
        result["issues"].append("🔴 严重: 无有效信号")
        result["recommendation"] = "降级 — 因子无法产生有效信号"
        return result

    # 1. 信号分布分析
    boundary_ratios = [s["at_boundary"] for s in sym_stats]
    stds = [s["std"] for s in sym_stats]
    turnovers = [s["turnover"] for s in sym_stats]
    n_uniques = [s["n_unique"] for s in sym_stats]

    result["signal_stats"] = {
        "avg_boundary": np.mean(boundary_ratios),
        "median_boundary": np.median(boundary_ratios),
        "max_boundary": np.max(boundary_ratios),
        "n_100pct_boundary": sum(1 for b in boundary_ratios if b > 0.99),
        "avg_std": np.mean(stds),
        "median_std": np.median(stds),
        "n_std_zero": sum(1 for s in stds if s < 0.01),
        "avg_unique": np.mean(n_uniques),
        "n_low_unique": sum(1 for n in n_uniques if n <= 5),
        "avg_turnover": np.mean(turnovers),
        "median_turnover": np.median(turnovers),
        "n_zero_turnover": sum(1 for t in turnovers if t < 0.001),
    }

    # 2. VWAP 分析（仅针对使用 VWAP 的因子）
    if all_vwap_raw:
        vwap_deviation_stats = []
        volume_zero_stats = []

        for sym, data in all_vwap_raw.items():
            close = data["close"]
            vwap = data["vwap"]
            volume = data["volume"]

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

            vol_zero = np.sum(volume < 1) / len(volume)
            volume_zero_stats.append({"sym": sym, "vol_zero_pct": vol_zero})

        if vwap_deviation_stats:
            mean_devs = [s["mean_dev"] for s in vwap_deviation_stats]
            std_devs = [s["std_dev"] for s in vwap_deviation_stats]
            vol_zeros = [s["vol_zero_pct"] for s in volume_zero_stats]

            result["vwap_stats"] = {
                "avg_deviation": np.mean(mean_devs),
                "std_deviation": np.mean(std_devs),
                "max_deviation": np.max([s["max_dev"] for s in vwap_deviation_stats]),
                "avg_vol_zero": np.mean(vol_zeros),
                "n_vol_zero": sum(1 for v in vol_zeros if v > 0.01),
            }

    # 3. tanh 饱和分析
    if all_vwap_raw:
        saturation_stats = []
        for sym, data in all_vwap_raw.items():
            raw = data["raw"]
            valid_raw = raw[~np.isnan(raw)]
            if len(valid_raw) == 0:
                continue
            std_raw = np.std(valid_raw)
            if std_raw < 1e-10:
                saturation_stats.append({"sym": sym, "pct_saturated": 1.0, "std_raw": std_raw})
                continue
            normalized = np.abs(valid_raw) / std_raw * 3
            saturated = np.sum(normalized > 2.5) / len(normalized)
            saturation_stats.append(
                {
                    "sym": sym,
                    "pct_saturated": saturated,
                    "std_raw": std_raw,
                }
            )

        if saturation_stats:
            sat_pcts = [s["pct_saturated"] for s in saturation_stats]
            result["tanh_stats"] = {
                "avg_saturated": np.mean(sat_pcts),
                "median_saturated": np.median(sat_pcts),
                "n_90pct": sum(1 for s in sat_pcts if s > 0.9),
                "n_100pct": sum(1 for s in sat_pcts if s > 0.99),
            }

    # 4. 分品种 IC（时序 IC）
    per_sym_ic = []
    for sym, sig in all_signals.items():
        df = panel[sym]
        close = df["close"].values
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

    result["per_symbol_ic"] = per_sym_ic

    # 5. 截面 IC 时序稳定性
    sym_list = list(all_signals.keys())
    n_dates = len(common_dates)
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

    daily_ics = []
    daily_dates = []
    for t in range(n_dates):
        valid = ~np.isnan(sig_matrix[t]) & ~np.isnan(ret_matrix[t])
        if np.sum(valid) >= 5:
            ic, _ = spearmanr(sig_matrix[t, valid], ret_matrix[t, valid])
            if not np.isnan(ic):
                daily_ics.append(ic)
                daily_dates.append(str(common_dates[t]))

    if daily_ics:
        ics_arr = np.array(daily_ics)

        # 分年度
        years = {}
        for d, ic in zip(daily_dates, daily_ics):
            y = d[:4]
            years.setdefault(y, []).append(ic)

        yearly_ic = {}
        for y in sorted(years.keys()):
            y_ics = years[y]
            yearly_ic[y] = {
                "ic": np.mean(y_ics),
                "std": np.std(y_ics),
                "icir": np.mean(y_ics) / max(np.std(y_ics), 1e-10),
                "n": len(y_ics),
            }

        # 滚动 60 日
        recent_60 = ics_arr[-60:] if len(ics_arr) >= 60 else ics_arr
        old_60 = ics_arr[:60] if len(ics_arr) >= 60 else ics_arr

        result["cross_section_ic"] = {
            "n_valid": len(daily_ics),
            "avg_ic": np.mean(ics_arr),
            "std_ic": np.std(ics_arr),
            "icir": np.mean(ics_arr) / max(np.std(ics_arr), 1e-10),
            "pct_positive": np.mean(ics_arr > 0),
            "n_significant": sum(1 for i in ics_arr if abs(i) > 0.05),
            "yearly": yearly_ic,
            "recent_60_ic": np.mean(recent_60),
            "recent_60_std": np.std(recent_60),
            "old_60_ic": np.mean(old_60),
            "old_60_std": np.std(old_60),
        }

    # 6. 信号自相关
    autocorrs = []
    for sym, sig in all_signals.items():
        valid = sig[~np.isnan(sig)]
        if len(valid) < 10:
            continue
        ac = np.corrcoef(valid[:-1], valid[1:])[0, 1]
        if not np.isnan(ac):
            autocorrs.append(ac)

    if autocorrs:
        result["autocorrelation"] = {
            "avg_lag1": np.mean(autocorrs),
            "median_lag1": np.median(autocorrs),
            "n_99pct": sum(1 for a in autocorrs if a > 0.99),
            "n_95pct": sum(1 for a in autocorrs if a > 0.95),
        }

    # 7. 综合诊断结论
    issues = []

    # 检查1: 信号退化
    avg_boundary = result["signal_stats"].get("avg_boundary", 0)
    if avg_boundary > 0.90:
        issues.append(f"🔴 严重: {avg_boundary:.1%} 信号处于 ±1 边界 (tanh 饱和)")
    elif avg_boundary > 0.70:
        issues.append(f"⚠️  警告: {avg_boundary:.1%} 信号处于 ±1 边界")

    # 检查2: 零换手
    avg_turnover = result["signal_stats"].get("avg_turnover", 0)
    if avg_turnover < 0.001:
        issues.append(f"🔴 严重: 平均日换手率 {avg_turnover:.6f} (几乎不变)")
    elif avg_turnover < 0.01:
        issues.append(f"⚠️  警告: 平均日换手率 {avg_turnover:.4f} (极低)")

    # 检查3: 信号标准差
    avg_std = result["signal_stats"].get("avg_std", 0)
    if avg_std < 0.05:
        issues.append(f"🔴 严重: 平均信号标准差 {avg_std:.4f} (信号几乎恒定)")
    elif avg_std < 0.15:
        issues.append(f"⚠️  警告: 平均信号标准差 {avg_std:.4f} (信号变化很小)")

    # 检查4: 自相关
    if result["autocorrelation"]:
        avg_ac = result["autocorrelation"].get("avg_lag1", 0)
        if avg_ac > 0.99:
            issues.append(f"🔴 严重: Lag-1 自相关 {avg_ac:.4f} (信号近乎常数)")
        elif avg_ac > 0.95:
            issues.append(f"⚠️  警告: Lag-1 自相关 {avg_ac:.4f} (信号变化极慢)")

    # 检查5: IC 稳定性
    if result["cross_section_ic"]:
        avg_ic = result["cross_section_ic"].get("avg_ic", 0)
        if abs(avg_ic) < 0.01:
            issues.append(f"🔴 严重: 截面 IC 仅 {avg_ic:.4f} (无预测能力)")
        elif abs(avg_ic) < 0.03:
            issues.append(f"⚠️  警告: 截面 IC 仅 {avg_ic:.4f} (预测能力弱)")

    # 检查6: VWAP 偏离
    if result["vwap_stats"]:
        avg_vwap_dev = result["vwap_stats"].get("avg_deviation", 0)
        if abs(avg_vwap_dev) < 0.001:
            issues.append(f"⚠️  警告: VWAP 平均偏离仅 {avg_vwap_dev:.4%} (5日均量近似 VWAP 太粗糙)")

    result["issues"] = issues

    # 最终建议
    red_count = sum(1 for i in issues if "🔴" in i)
    yellow_count = sum(1 for i in issues if "⚠️" in i)

    if red_count >= 2:
        result["recommendation"] = "降级 — 多个严重问题，因子在日频数据上已退化"
    elif red_count >= 1:
        result["recommendation"] = "标记观察 — 存在严重问题，需跟踪后续表现"
    elif yellow_count >= 2:
        result["recommendation"] = "标记警告 — 存在多个潜在问题，建议优化"
    else:
        result["recommendation"] = "保留 — 因子表现可接受"

    return result


def generate_summary_report(results: list[dict], today: str) -> str:
    """生成汇总报告"""
    lines = []
    lines.append(f"# 期货精英因子全面诊断报告 — {today}")
    lines.append("")
    lines.append(f"生成时间: {today}")
    lines.append(f"诊断因子: {len(results)} 个")
    lines.append("")

    # 统计
    n_retain = sum(1 for r in results if "保留" in r.get("recommendation", ""))
    n_warn = sum(1 for r in results if "警告" in r.get("recommendation", ""))
    n_observe = sum(1 for r in results if "观察" in r.get("recommendation", ""))
    n_deprecate = sum(1 for r in results if "降级" in r.get("recommendation", ""))

    lines.append("## 诊断结果汇总")
    lines.append("")
    lines.append(f"- ✅ 保留: {n_retain}")
    lines.append(f"- ⚠️  警告: {n_warn}")
    lines.append(f"- 👀 观察: {n_observe}")
    lines.append(f"- 🔴 降级: {n_deprecate}")
    lines.append("")

    # 按建议分组
    lines.append("## 建议降级因子")
    lines.append("")
    deprecated = [r for r in results if "降级" in r.get("recommendation", "")]
    if deprecated:
        lines.append("| 因子名称 | 因子 ID | 问题 |")
        lines.append("|----------|---------|------|")
        for r in deprecated:
            issues_str = "; ".join(r.get("issues", [])[:2])
            lines.append(f"| {r['factor_name']} | {r['factor_id']} | {issues_str} |")
    else:
        lines.append("无")
    lines.append("")

    lines.append("## 建议保留因子")
    lines.append("")
    retained = [r for r in results if "保留" in r.get("recommendation", "")]
    if retained:
        lines.append("| 因子名称 | 因子 ID | 截面 IC | ICIR | 建议 |")
        lines.append("|----------|---------|---------|------|------|")
        for r in retained:
            cs_ic = r.get("cross_section_ic", {})
            ic = cs_ic.get("avg_ic", 0)
            icir = cs_ic.get("icir", 0)
            lines.append(f"| {r['factor_name']} | {r['factor_id']} | {ic:.4f} | {icir:.4f} | {r['recommendation']} |")
    lines.append("")

    lines.append("## 全部因子详细指标")
    lines.append("")
    lines.append("| 因子名称 | 品种数 | 信号标准差 | 换手率 | 截面 IC | ICIR | 自相关 | 建议 |")
    lines.append("|----------|--------|------------|--------|---------|------|--------|------|")
    for r in results:
        ss = r.get("signal_stats", {})
        cs = r.get("cross_section_ic", {})
        ac = r.get("autocorrelation", {})
        lines.append(
            f"| {r['factor_name']} "
            f"| {r['n_symbols']} "
            f"| {ss.get('avg_std', 0):.4f} "
            f"| {ss.get('avg_turnover', 0):.4f} "
            f"| {cs.get('avg_ic', 0):.4f} "
            f"| {cs.get('icir', 0):.4f} "
            f"| {ac.get('avg_lag1', 0):.4f} "
            f"| {r['recommendation']} |"
        )
    lines.append("")

    lines.append("---")
    lines.append("详细报告见: factor_details/ 目录")

    return "\n".join(lines)


def generate_factor_detail_report(result: dict, today: str) -> str:
    """生成单个因子的详细报告"""
    lines = []
    lines.append(f"# {result['factor_name']} ({result['factor_id']}) 诊断报告")
    lines.append("")
    lines.append(f"生成时间: {today}")
    lines.append(f"数据范围: {result['date_range']}")
    lines.append(f"品种数: {result['n_symbols']}")
    lines.append(f"交易日数: {result['n_dates']}")
    lines.append("")

    # 信号分布
    lines.append("## 1. 信号分布分析")
    lines.append("")
    ss = result.get("signal_stats", {})
    if ss:
        lines.append(
            f"- 边界值占比 (|sig| >= 0.99): 平均 {ss.get('avg_boundary', 0):.1%}, 中位数 {ss.get('median_boundary', 0):.1%}"
        )
        lines.append(f"- 100% 边界品种数: {ss.get('n_100pct_boundary', 0)}/{result['n_symbols']}")
        lines.append(f"- 信号标准差: 平均 {ss.get('avg_std', 0):.4f}, 中位数 {ss.get('median_std', 0):.4f}")
        lines.append(f"- 标准差=0 品种数: {ss.get('n_std_zero', 0)}/{result['n_symbols']}")
        lines.append(f"- 信号唯一值: 平均 {ss.get('avg_unique', 0):.1f}")
        lines.append(f"- 日换手率: 平均 {ss.get('avg_turnover', 0):.4f}, 中位数 {ss.get('median_turnover', 0):.4f}")
        lines.append(f"- 零换手品种数: {ss.get('n_zero_turnover', 0)}/{result['n_symbols']}")
    else:
        lines.append("无有效信号")
    lines.append("")

    # VWAP 分析
    vs = result.get("vwap_stats", {})
    if vs:
        lines.append("## 2. VWAP 近似有效性")
        lines.append("")
        lines.append(f"- (close - vwap) / close 偏离度: 平均 {vs.get('avg_deviation', 0):.4%}")
        lines.append(f"- 偏离标准差: {vs.get('std_deviation', 0):.4%}")
        lines.append(f"- 最大偏离: {vs.get('max_deviation', 0):.4%}")
        lines.append(f"- 成交量=0 比例: {vs.get('avg_vol_zero', 0):.2%}")
        lines.append("")

    # tanh 饱和
    ts = result.get("tanh_stats", {})
    if ts:
        lines.append("## 3. tanh 饱和分析")
        lines.append("")
        lines.append(f"- 饱和区占比: 平均 {ts.get('avg_saturated', 0):.1%}, 中位数 {ts.get('median_saturated', 0):.1%}")
        lines.append(f"- >90% 饱和品种数: {ts.get('n_90pct', 0)}/{result['n_symbols']}")
        lines.append(f"- 100% 饱和品种数: {ts.get('n_100pct', 0)}/{result['n_symbols']}")
        lines.append("")

    # 分品种 IC
    per_ic = result.get("per_symbol_ic", [])
    if per_ic:
        lines.append("## 4. 分品种 IC (时序 IC)")
        lines.append("")
        ics = [s["ic"] for s in per_ic]
        lines.append(f"- 有效品种: {len(per_ic)}")
        lines.append(f"- 平均 IC: {np.mean(ics):.4f}")
        lines.append(f"- 中位数 IC: {np.median(ics):.4f}")
        lines.append(f"- IC > 0.05: {sum(1 for i in ics if i > 0.05)}/{len(ics)}")
        lines.append(f"- IC < -0.05: {sum(1 for i in ics if i < -0.05)}/{len(ics)}")
        lines.append(f"- |IC| < 0.01: {sum(1 for i in ics if abs(i) < 0.01)}/{len(ics)}")
        lines.append("")

        sorted_ic = sorted(per_ic, key=lambda x: x["ic"], reverse=True)
        lines.append("**Top 5 IC:**")
        lines.append("")
        for s in sorted_ic[:5]:
            lines.append(f"- {s['sym']}: {s['ic']:+.4f}")
        lines.append("")
        lines.append("**Bottom 5 IC:**")
        lines.append("")
        for s in sorted_ic[-5:]:
            lines.append(f"- {s['sym']}: {s['ic']:+.4f}")
        lines.append("")

    # 截面 IC
    cs = result.get("cross_section_ic", {})
    if cs:
        lines.append("## 5. 截面 IC 时序稳定性")
        lines.append("")
        lines.append(f"- 有效交易日: {cs.get('n_valid', 0)}/{result['n_dates']}")
        lines.append(f"- 平均 IC: {cs.get('avg_ic', 0):.4f}")
        lines.append(f"- IC 标准差: {cs.get('std_ic', 0):.4f}")
        lines.append(f"- ICIR: {cs.get('icir', 0):.4f}")
        lines.append(f"- IC > 0 比例: {cs.get('pct_positive', 0):.1%}")
        lines.append(f"- |IC| > 0.05: {cs.get('n_significant', 0)}/{cs.get('n_valid', 0)}")
        lines.append("")

        yearly = cs.get("yearly", {})
        if yearly:
            lines.append("**分年度 IC:**")
            lines.append("")
            lines.append("| 年份 | IC | 标准差 | ICIR | 样本数 |")
            lines.append("|------|-----|--------|------|--------|")
            for y, data in yearly.items():
                lines.append(f"| {y} | {data['ic']:+.4f} | {data['std']:.4f} | {data['icir']:.4f} | {data['n']} |")
            lines.append("")

        lines.append("**60日滚动 IC:**")
        lines.append(f"- 最近60日: IC={cs.get('recent_60_ic', 0):+.4f}, std={cs.get('recent_60_std', 0):.4f}")
        lines.append(f"- 最早60日: IC={cs.get('old_60_ic', 0):+.4f}, std={cs.get('old_60_std', 0):.4f}")
        lines.append("")

    # 自相关
    ac = result.get("autocorrelation", {})
    if ac:
        lines.append("## 6. 信号自相关")
        lines.append("")
        lines.append(f"- Lag-1 自相关: 平均 {ac.get('avg_lag1', 0):.4f}, 中位数 {ac.get('median_lag1', 0):.4f}")
        lines.append(f"- >0.99 品种数: {ac.get('n_99pct', 0)}/{result['n_symbols']}")
        lines.append(f"- >0.95 品种数: {ac.get('n_95pct', 0)}/{result['n_symbols']}")
        lines.append("")

    # 综合结论
    lines.append("## 7. 综合诊断结论")
    lines.append("")
    issues = result.get("issues", [])
    if issues:
        lines.append(f"发现 {len(issues)} 个问题:")
        lines.append("")
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.append("✅ 所有检查通过，因子表现正常")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"**建议: {result['recommendation']}**")

    return "\n".join(lines)


def main(factor_id=None, days=500):
    today = date.today().isoformat()
    print(f"期货精英因子全面诊断 — {today}")
    print(f"数据窗口: {days} 个交易日")
    print()

    # 加载因子
    if factor_id:
        # 单因子模式
        fp = FUTURES_ELITE_DIR / f"{factor_id}.json"
        if not fp.exists():
            print(f"ERROR: 因子 {factor_id} 不存在")
            return 1
        factors = [json.loads(fp.read_text(encoding="utf-8"))]
        print(f"单因子模式: {factors[0].get('name', factor_id)}")
    else:
        # 批量模式
        factors = load_all_elite_factors()
        print(f"批量模式: {len(factors)} 个因子")

    if not factors:
        print("ERROR: 无精英因子")
        return 1

    # 加载数据
    print("\n加载数据...")
    panel, common_dates, symbols = load_panel(days=days)
    print(f"数据: {len(panel)} 品种 × {len(common_dates)} 交易日")
    print(f"日期范围: {common_dates[0]} ~ {common_dates[-1]}")

    # 逐因子诊断
    print("\n开始诊断...")
    results = []
    for i, factor_data in enumerate(factors, 1):
        name = factor_data.get("name", "?")
        fid = factor_data.get("factor_id", "")
        print(f"[{i}/{len(factors)}] {name} ({fid})...")

        try:
            result = diagnose_factor(factor_data, panel, common_dates)
            results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(
                {
                    "factor_id": fid,
                    "factor_name": name,
                    "n_symbols": 0,
                    "n_dates": len(common_dates),
                    "date_range": f"{common_dates[0]} ~ {common_dates[-1]}",
                    "signal_stats": {},
                    "vwap_stats": {},
                    "tanh_stats": {},
                    "per_symbol_ic": [],
                    "cross_section_ic": {},
                    "autocorrelation": {},
                    "issues": [f"🔴 严重: 诊断失败 - {e}"],
                    "recommendation": "降级 — 诊断失败",
                }
            )

    # 生成报告
    print("\n生成报告...")
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)

    # 汇总报告
    summary = generate_summary_report(results, today)
    summary_path = report_dir / f"futures_elite_diagnostic_{today}.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(f"汇总报告: {summary_path}")

    # 详细报告
    details_dir = report_dir / "factor_details"
    details_dir.mkdir(exist_ok=True)

    for result in results:
        detail = generate_factor_detail_report(result, today)
        detail_path = details_dir / f"{result['factor_id']}.md"
        detail_path.write_text(detail, encoding="utf-8")

    print(f"详细报告: {details_dir}/ ({len(results)} 个文件)")

    # 打印汇总
    print()
    print("=" * 60)
    print(summary)

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="期货精英因子全面诊断")
    parser.add_argument("--factor-id", type=str, default=None, help="单因子模式: 指定因子 ID (如 fct_d9a1d0fd)")
    parser.add_argument("--days", type=int, default=500, help="数据窗口天数 (default: 500)")
    args = parser.parse_args()

    sys.exit(main(factor_id=args.factor_id, days=args.days))
