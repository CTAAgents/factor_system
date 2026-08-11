"""
scripts/futures_seed_diagnostic.py — 期货种子因子批量诊断

对所有 81 个期货种子因子执行以下诊断：
1. 因子代码安全沙箱编译
2. 在 76 个商品品种上执行因子计算
3. 信号分布分析（退化检测）
4. 换手率与 IC 计算
5. 因子间相关性检查（发现重复因子）
6. 字段依赖一致性检查

输出:
  - 控制台: 诊断结果摘要
  - reports/futures/{date}/futures_seed_diagnostic_{date}.md
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

warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORTS_ROOT = PROJECT_ROOT / "reports"


def _load_all_seeds() -> list[dict[str, Any]]:
    """加载所有期货种子因子。"""
    from fts.factor_engine.seed_data_futures_full import load_futures_seeds_full

    return load_futures_seeds_full()


def _load_panel(days: int = 250, max_symbols: int = 0):
    """加载全品种期货面板数据。"""
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_SUBSET

    FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
    symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL]
    if max_symbols > 0:
        symbols = symbols[:max_symbols]

    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(symbols=symbols, days=days)
    return panel, common_dates, symbols


def _compile_factor(code: str) -> tuple[bool, str]:
    """安全沙箱编译因子代码。"""
    try:
        compile(code, "<factor>", "exec")
        return True, "OK"
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _execute_factor(factor_data: dict, df) -> np.ndarray | None:
    """在指定数据上执行因子。"""
    try:
        from fts.factor_engine.factor_program import FactorExecutor

        executor = FactorExecutor(factor_data)
        sig = executor.execute(df, factor_data.get("params", {}))
        arr = np.array(sig, dtype=float)
        return np.where(np.isfinite(arr), arr, np.nan)
    except Exception:
        return None


def _diagnose_signal_distribution(signals: dict[str, np.ndarray]) -> dict:
    """信号分布分析。"""
    if not signals:
        return {
            "n_valid_symbols": 0,
            "avg_std": 0.0,
            "avg_at_boundary": 0.0,
            "avg_unique": 0.0,
            "pct_degenerate": 0.0,
        }

    sym_stats = []
    for sym, sig in signals.items():
        if sig is None or len(sig) == 0:
            continue
        valid = sig[~np.isnan(sig)]
        if len(valid) < 5:
            continue
        std = float(np.std(valid))
        unique = len(np.unique(valid))
        at_boundary = float((np.sum(valid >= 0.99) + np.sum(valid <= -0.99)) / len(valid))
        sym_stats.append({"sym": sym, "std": std, "unique": unique, "at_boundary": at_boundary})

    if not sym_stats:
        return {
            "n_valid_symbols": 0,
            "avg_std": 0.0,
            "avg_at_boundary": 0.0,
            "avg_unique": 0.0,
            "pct_degenerate": 0.0,
        }

    stds = [s["std"] for s in sym_stats]
    boundaries = [s["at_boundary"] for s in sym_stats]
    uniques = [s["unique"] for s in sym_stats]
    degenerate = sum(1 for s in sym_stats if s["std"] < 1e-6)

    return {
        "n_valid_symbols": len(sym_stats),
        "avg_std": float(np.mean(stds)),
        "avg_at_boundary": float(np.mean(boundaries)),
        "avg_unique": float(np.mean(uniques)),
        "pct_degenerate": float(degenerate / len(sym_stats)),
    }


def _compute_turnover(sig: np.ndarray) -> float:
    """月度换手率（基于信号幅度变化）。"""
    if sig is None or len(sig) < 2:
        return 0.0
    valid = sig[~np.isnan(sig)]
    if len(valid) < 2:
        return 0.0
    sig_std = np.std(valid)
    if sig_std < 1e-10:
        return 0.0
    daily_changes = np.abs(np.diff(valid))
    return float(np.mean(daily_changes) / sig_std * np.sqrt(21))


def _compute_cross_sectional_ic(panel: dict, signals: dict, common_dates: list, horizon: int = 5) -> float:
    """截面平均 IC。"""
    from scipy.stats import spearmanr

    n_dates = len(common_dates)
    sym_list = list(signals.keys())
    sig_matrix = np.full((n_dates, len(sym_list)), np.nan)
    ret_matrix = np.full((n_dates, len(sym_list)), np.nan)

    for j, sym in enumerate(sym_list):
        sig = signals[sym]
        df = panel.get(sym)
        if df is None or sig is None:
            continue
        close = df["close"].values
        arr_len = min(len(sig), len(close))
        sig_matrix[:arr_len, j] = sig[:arr_len]
        fwd_ret = np.full(len(close), np.nan)
        for t in range(len(close) - horizon):
            if close[t] > 1e-10:
                fwd_ret[t] = (close[t + horizon] - close[t]) / close[t]
        ret_matrix[:arr_len, j] = fwd_ret[:arr_len]

    daily_ics = []
    for t in range(n_dates - horizon):
        valid = ~np.isnan(sig_matrix[t]) & ~np.isnan(ret_matrix[t])
        if np.sum(valid) >= 5:
            ic, _ = spearmanr(sig_matrix[t, valid], ret_matrix[t, valid])
            if not np.isnan(ic):
                daily_ics.append(ic)
    return float(np.mean(daily_ics)) if daily_ics else 0.0


def _compute_autocorrelation(sig: np.ndarray) -> float:
    """lag-1 自相关。"""
    if sig is None or len(sig) < 10:
        return 0.0
    valid = sig[~np.isnan(sig)]
    if len(valid) < 10:
        return 0.0
    ac = np.corrcoef(valid[:-1], valid[1:])[0, 1]
    return float(ac) if not np.isnan(ac) else 0.0


def _check_field_dependencies(factor_data: dict, available_fields: set[str]) -> list[str]:
    """检查因子声明的输入字段是否在数据中可用。"""
    issues = []
    declared_inputs = factor_data.get("signature", {}).get("input_fields", [])
    if not declared_inputs:
        issues.append("未声明 input_fields")
        return issues
    missing = [f for f in declared_inputs if f not in available_fields]
    if missing:
        issues.append(f"声明字段缺失: {missing}")
    return issues


def diagnose_single_factor(
    factor_data: dict,
    panel: dict,
    common_dates: list,
) -> dict[str, Any]:
    """诊断单个种子因子。"""
    name = factor_data.get("name", "?")
    fid = factor_data.get("factor_id", "?")
    code = factor_data.get("code", "")
    declared_inputs = factor_data.get("signature", {}).get("input_fields", [])

    issues = []
    warnings_list = []
    metrics: dict[str, Any] = {}

    # ── Step 1: 编译检查 ──
    compiled_ok, compile_msg = _compile_factor(code)
    metrics["compile_status"] = "OK" if compiled_ok else "FAIL"
    if not compiled_ok:
        issues.append(f"代码编译失败: {compile_msg}")
        return {
            "name": name,
            "factor_id": fid,
            "status": "BROKEN",
            "issues": issues,
            "warnings": warnings_list,
            "metrics": metrics,
        }

    # ── Step 2: 字段依赖检查 ──
    if panel:
        sample_df = next(iter(panel.values()))
        available_fields = set(sample_df.columns.tolist())
        field_issues = _check_field_dependencies(factor_data, available_fields)
        if field_issues:
            issues.extend(field_issues)

    # ── Step 3: 在所有品种上执行 ──
    signals: dict[str, np.ndarray] = {}
    exec_errors: list[str] = []
    for sym, df in panel.items():
        if df is None or len(df) < 30:
            continue
        try:
            sig = _execute_factor(factor_data, df)
            if sig is not None:
                signals[sym] = sig
        except Exception as e:
            exec_errors.append(f"{sym}: {type(e).__name__}")

    if exec_errors and len(exec_errors) > len(signals):
        issues.append(f"执行失败品种数过多: {len(exec_errors)}/{len(exec_errors) + len(signals)}")
        metrics["exec_errors_sample"] = exec_errors[:3]

    # ── Step 4: 信号分布分析 ──
    dist = _diagnose_signal_distribution(signals)
    metrics.update(dist)

    if dist["n_valid_symbols"] < 5:
        issues.append(f"有效品种数 {dist['n_valid_symbols']} < 5")
    if dist["avg_std"] < 1e-4:
        issues.append(f"信号标准差极小: {dist['avg_std']:.6f} (信号近乎常数)")
    if dist["avg_at_boundary"] > 0.95:
        issues.append(f"信号极度饱和: {dist['avg_at_boundary']:.1%} 处于 ±1 (tanh 退化为开关)")
    elif dist["avg_at_boundary"] > 0.85:
        warnings_list.append(f"信号饱和: {dist['avg_at_boundary']:.1%} 处于 ±1")

    # ── Step 5: 换手率 + 自相关 + IC ──
    turnovers = []
    autocorrs = []
    for sig in signals.values():
        turnovers.append(_compute_turnover(sig))
        autocorrs.append(_compute_autocorrelation(sig))

    if turnovers:
        metrics["avg_turnover"] = float(np.mean(turnovers))
    if autocorrs:
        metrics["avg_autocorr"] = float(np.mean(autocorrs))

    if turnovers and np.mean(turnovers) < 0.001:
        warnings_list.append(f"换手率极低: {metrics.get('avg_turnover', 0):.6f}")
    if autocorrs and np.mean(autocorrs) > 0.99:
        warnings_list.append(f"自相关极高: {metrics.get('avg_autocorr', 0):.4f}")

    if signals and len(common_dates) > 30:
        ic = _compute_cross_sectional_ic(panel, signals, common_dates)
        metrics["cross_sectional_ic"] = ic
        if abs(ic) < 0.01 and dist["n_valid_symbols"] >= 10:
            warnings_list.append(f"IC 接近 0: {ic:+.4f}")

    # ── 判定状态 ──
    if issues:
        status = "BROKEN"
    elif warnings_list:
        status = "WARN"
    else:
        status = "OK"

    return {
        "name": name,
        "factor_id": fid,
        "status": status,
        "issues": issues,
        "warnings": warnings_list,
        "metrics": metrics,
        "declared_inputs": declared_inputs,
    }


def _build_correlation_report(results: list[dict], panel: dict, common_dates: list) -> list[dict]:
    """因子间相关性分析：发现逻辑重复的种子因子。"""
    from scipy.stats import spearmanr
    from fts.factor_engine.factor_program import FactorExecutor

    ok_results = [r for r in results if r["status"] in ("OK", "WARN") and r["metrics"].get("n_valid_symbols", 0) >= 10]
    if len(ok_results) < 2:
        return []

    # 计算每个因子的截面平均信号
    factor_signals: dict[str, np.ndarray] = {}
    for r in ok_results:
        # 重新执行获取信号
        try:
            from fts.factor_engine.seed_data_futures_full import load_futures_seeds_full

            seeds = load_futures_seeds_full()
            factor_data = next((s for s in seeds if s.get("factor_id") == r["factor_id"]), None)
            if factor_data is None:
                continue
            executor = FactorExecutor(factor_data)
            sym_sigs = []
            for sym, df in panel.items():
                if df is None or len(df) < 30:
                    continue
                try:
                    sig = executor.execute(df, factor_data.get("params", {}))
                    arr = np.array(sig, dtype=float)
                    arr = np.where(np.isfinite(arr), arr, np.nan)
                    arr = arr[-min(len(arr), 60) :]  # 取最近60日
                    if len(arr) >= 30:
                        sym_sigs.append(np.nanmean(arr))
                except Exception:
                    continue
            if len(sym_sigs) >= 10:
                factor_signals[r["name"]] = np.array(sym_sigs)
        except Exception:
            continue

    if len(factor_signals) < 2:
        return []

    # 计算两两 Spearman 相关
    names = list(factor_signals.keys())
    corr_pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = factor_signals[names[i]], factor_signals[names[j]]
            n = min(len(a), len(b))
            if n < 10:
                continue
            try:
                rho, _ = spearmanr(a[:n], b[:n])
                if not np.isnan(rho) and abs(rho) > 0.85:
                    corr_pairs.append(
                        {
                            "f1": names[i],
                            "f2": names[j],
                            "correlation": float(rho),
                        }
                    )
            except Exception:
                continue

    return corr_pairs


def _generate_report(results: list[dict], corr_pairs: list[dict], today: str) -> str:
    """生成诊断报告。"""
    n_total = len(results)
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_broken = sum(1 for r in results if r["status"] == "BROKEN")

    lines = [
        f"# 期货种子因子批量诊断报告 — {today}",
        "",
        f"生成时间: {today}",
        f"诊断种子因子总数: {n_total}",
        "",
        "## 诊断结果汇总",
        "",
        f"- ✅ 正常: {n_ok}",
        f"- ⚠️  警告: {n_warn}",
        f"- 🔴 异常: {n_broken}",
        "",
    ]

    # 异常因子
    broken = [r for r in results if r["status"] == "BROKEN"]
    if broken:
        lines.append("## 🔴 异常因子 (需修复)")
        lines.append("")
        lines.append("| 因子名称 | 因子 ID | 问题 |")
        lines.append("|----------|---------|------|")
        for r in broken:
            issues_str = "; ".join(r["issues"])
            lines.append(f"| {r['name']} | {r['factor_id']} | {issues_str} |")
        lines.append("")

    # 警告因子
    warns = [r for r in results if r["status"] == "WARN"]
    if warns:
        lines.append("## ⚠️ 警告因子 (需关注)")
        lines.append("")
        lines.append("| 因子名称 | 因子 ID | 警告 |")
        lines.append("|----------|---------|------|")
        for r in warns:
            w_str = "; ".join(r["warnings"])
            lines.append(f"| {r['name']} | {r['factor_id']} | {w_str} |")
        lines.append("")

    # 重复因子对
    if corr_pairs:
        lines.append("## 🔁 高度相关因子对 (|ρ| > 0.85，可能存在逻辑重复)")
        lines.append("")
        lines.append("| 因子 A | 因子 B | Spearman ρ |")
        lines.append("|--------|--------|------------|")
        for p in sorted(corr_pairs, key=lambda x: -abs(x["correlation"])):
            lines.append(f"| {p['f1']} | {p['f2']} | {p['correlation']:+.4f} |")
        lines.append("")

    # 全部因子详细指标
    lines.append("## 全部因子详细指标")
    lines.append("")
    lines.append("| 因子名称 | 状态 | 编译 | 品种数 | σ(sig) | 边界占比 | 换手 | IC | 自相关 |")
    lines.append("|----------|------|------|--------|--------|----------|------|-----|--------|")
    for r in results:
        m = r["metrics"]
        compile_s = "✅" if m.get("compile_status") == "OK" else "❌"
        nsym = m.get("n_valid_symbols", 0)
        std = m.get("avg_std", 0)
        boundary = m.get("avg_at_boundary", 0)
        turnover = m.get("avg_turnover", 0)
        ic = m.get("cross_sectional_ic", 0)
        ac = m.get("avg_autocorr", 0)
        status_icon = {"OK": "✅", "WARN": "⚠️", "BROKEN": "🔴"}.get(r["status"], "❓")
        lines.append(
            f"| {r['name']} | {status_icon} {r['status']} | {compile_s} | "
            f"{nsym} | {std:.4f} | {boundary:.1%} | {turnover:.3f} | "
            f"{ic:+.4f} | {ac:+.3f} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("详细诊断数据保存在 `factor_details/` 目录")

    return "\n".join(lines)


def main(
    days: int = 250,
    max_symbols: int = 0,
    skip_correlation: bool = False,
) -> int:
    """执行期货种子因子批量诊断。"""
    t0 = time.time()
    today = date.today().isoformat()

    print("=" * 60)
    print(f"  期货种子因子批量诊断 — {today}")
    print("=" * 60)

    # ── Step 1: 加载种子因子 ──
    seeds = _load_all_seeds()
    print(f"\n[1/4] 加载种子因子: {len(seeds)} 个")

    # ── Step 2: 加载数据 ──
    print("[2/4] 加载期货数据...")
    panel, common_dates, symbols = _load_panel(days=days, max_symbols=max_symbols)
    print(f"       品种数: {len(panel)}, 交易日: {len(common_dates)}")

    if not panel or len(common_dates) < 30:
        print("[ERROR] 数据不足")
        return 1

    # ── Step 3: 逐因子诊断 ──
    print(f"\n[3/4] 诊断 {len(seeds)} 个种子因子...")
    results = []
    for i, factor_data in enumerate(seeds, 1):
        if i % 10 == 0 or i == len(seeds):
            print(f"      进度: {i}/{len(seeds)}")
        result = diagnose_single_factor(factor_data, panel, common_dates)
        results.append(result)

    # ── Step 4: 相关性分析 ──
    corr_pairs = []
    if not skip_correlation:
        print("\n[4/4] 因子相关性分析...")
        corr_pairs = _build_correlation_report(results, panel, common_dates)

    # ── 汇总 ──
    n_broken = sum(1 for r in results if r["status"] == "BROKEN")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_ok = sum(1 for r in results if r["status"] == "OK")

    print(f"\n{'=' * 60}")
    print(f"  诊断结果: ✅ {n_ok} | ⚠️ {n_warn} | 🔴 {n_broken}")
    print(f"  高度相关因子对: {len(corr_pairs)}")
    print(f"  耗时: {time.time() - t0:.1f}s")
    print(f"{'=' * 60}")

    # ── 保存报告 ──
    report_dir = REPORTS_ROOT / "futures" / today
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"futures_seed_diagnostic_{today}.md"
    report_path.write_text(_generate_report(results, corr_pairs, today), encoding="utf-8")
    print(f"\n报告已保存: {report_path}")

    # 保存详细 JSON 数据
    details_dir = report_dir / "seed_factor_details"
    details_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        detail_path = details_dir / f"{r['name']}_{r['factor_id']}.json"
        detail_path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
