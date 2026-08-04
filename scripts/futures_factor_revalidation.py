"""
scripts/futures_factor_revalidation.py — 期货精英因子全量重验证

定期（如每周）在全部 76 个商品品种上重新计算精英因子的 IC，
检测因子退化，退化因子降级或标记警告。

用法:
    python scripts/futures_factor_revalidation.py [--ic-threshold 0.02] [--drop-threshold 0.3]

输出:
    - 控制台: 验证结果摘要
    - 文件:     reports/{date}/futures_factor_revalidation_{date}.md
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FUTURES_ELITE_DIR = PROJECT_ROOT / "memory/knowledge/factors/futures_elite"
DEPRECATED_DIR = FUTURES_ELITE_DIR / "_deprecated"
REPORTS_ROOT = PROJECT_ROOT / "reports"


def _load_all_elite_factors() -> list[dict[str, Any]]:
    """加载所有期货精英因子（含已降级的）。"""
    factors: list[dict[str, Any]] = []

    # 主目录
    for fp in sorted(FUTURES_ELITE_DIR.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["_filepath"] = str(fp)
            data["_deprecated"] = False
            factors.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    # 降级目录
    deprecated_dir = FUTURES_ELITE_DIR / "_deprecated"
    if deprecated_dir.exists():
        for fp in sorted(deprecated_dir.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                data["_filepath"] = str(fp)
                data["_deprecated"] = True
                factors.append(data)
            except (json.JSONDecodeError, OSError):
                continue

    return factors


def _compute_current_ic(
    factor_data: dict[str, Any],
    panel: dict[str, "pd.DataFrame"],
    common_dates: list[str],
) -> float:
    """计算因子在当前全量品种上的截面平均 IC。

    方法：对每个交易日，计算所有品种截面 Spearman IC（因子信号 vs 未来 5 日收益），
    取时间序列均值。

    Returns:
        float: 平均 IC 值（NaN 表示无法计算）
    """
    from fts.factor_engine.factor_program import FactorExecutor
    from scipy.stats import spearmanr

    n_dates = len(common_dates)
    if n_dates < 10:
        return float("nan")

    # 计算每个品种的因子信号
    sym_signals: dict[str, np.ndarray] = {}
    for sym, df in panel.items():
        if df is None or df.empty or len(df) < 20:
            continue
        try:
            executor = FactorExecutor(factor_data)
            sig = executor.execute(df, factor_data.get("params", {}))
            arr = np.array(sig, dtype=float)
            # 只保留有限数值
            arr = np.where(np.isfinite(arr), arr, np.nan)
            pair = df.reindex(common_dates)
            if len(arr) < len(pair):
                arr = np.pad(arr, (0, len(pair) - len(arr)),
                             constant_values=np.nan)[:len(pair)]
            closes = pair["close"].values
            fwd_ret = np.zeros(len(closes))
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
            sym_signals[sym] = arr
        except Exception:
            continue

    if len(sym_signals) < 5:
        return float("nan")

    # 每日截面 IC
    daily_ics: list[float] = []
    for t in range(n_dates - 5):
        signals_t: dict[str, float] = {}
        rets_t: dict[str, float] = {}
        for sym, arr in sym_signals.items():
            if t >= len(arr) or not np.isfinite(arr[t]):
                continue
            df = panel.get(sym)
            if df is None:
                continue
            closes = df.reindex(common_dates)["close"].values
            if t + 5 >= len(closes):
                continue
            p_t = closes[t]
            if not np.isfinite(p_t) or p_t <= 1e-10:
                continue
            ret = (closes[t + 5] - p_t) / p_t
            if not np.isfinite(ret):
                continue
            signals_t[sym] = float(arr[t])
            rets_t[sym] = ret

        common = set(signals_t.keys()) & set(rets_t.keys())
        if len(common) >= 5:
            s_vals = [signals_t[s] for s in common]
            r_vals = [rets_t[s] for s in common]
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning)
                r, _ = spearmanr(s_vals, r_vals)
            if not np.isnan(r):
                daily_ics.append(r)

    if not daily_ics:
        return float("nan")
    return float(np.mean(daily_ics))


def main(
    ic_threshold: float = 0.02,
    drop_threshold: float = 0.3,
    days: int = 120,
    max_symbols: int = 0,
) -> int:
    """执行精英因子全量重验证。

    Args:
        ic_threshold: IC 绝对值低于此值视为失效
        drop_threshold: IC 降幅超过此比例（如 0.3 = 30%）标记警告
        days: 回看天数
        max_symbols: 最大品种数，0=全量
    """
    t0 = time.time()
    today = date.today().isoformat()

    print("=" * 60)
    print(f"  期货精英因子全量重验证 — {today}")
    print("=" * 60)

    # ── Step 1: 加载全部精英因子 ──
    factors = _load_all_elite_factors()
    if not factors:
        print("[ERROR] 无精英因子")
        return 1
    print(f"\n[1/4] 加载精英因子: {len(factors)} 个 "
          f"({sum(1 for f in factors if not f.get('_deprecated', False))} 活跃, "
          f"{sum(1 for f in factors if f.get('_deprecated', False))} 已降级)")

    # ── Step 2: 获取全量期货数据 ──
    import pandas as pd  # noqa: F811
    from fts.data import FTSDataProvider
    from fts.data_futures import FUTURES_SUBSET

    FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
    symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL]
    if max_symbols > 0:
        symbols = symbols[:max_symbols]

    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(
        symbols=symbols, days=days,
    )
    print(f"[2/4] 获取数据: {len(panel)} 个品种, {len(common_dates)} 个交易日")

    if not panel or len(common_dates) < 10:
        print("[ERROR] 数据不足")
        return 1

    # ── Step 3: 逐因子验证 ──
    print(f"\n[3/4] 逐因子验证 ({len(factors)} 个)...")

    results: list[dict[str, Any]] = []
    n_warn = 0
    n_deprecate = 0

    for i, factor_data in enumerate(factors, 1):
        name = factor_data.get("name", "?")
        fid = factor_data.get("factor_id", "")
        is_deprecated = factor_data.get("_deprecated", False)
        filepath = factor_data.get("_filepath", "")

        # 获取历史 IC（晋级时的 IC）
        ev = factor_data.get("evaluation", {})
        bt = ev.get("level_1_backtest", {})
        hist_ic = abs(bt.get("ic", 0))

        # 计算当前 IC
        curr_ic = _compute_current_ic(factor_data, panel, common_dates)
        if np.isnan(curr_ic):
            curr_ic = 0.0
        curr_ic_abs = abs(curr_ic)

        # 判断
        ic_drop = 1.0 - (curr_ic_abs / max(hist_ic, 1e-10)) if hist_ic > 1e-10 else 0.0
        is_weak = curr_ic_abs < ic_threshold
        is_dropped = ic_drop > drop_threshold and hist_ic > ic_threshold

        warnings_count = 0
        if is_weak:
            warnings_count += 1
        if is_dropped:
            warnings_count += 1

        status = "OK"
        if is_deprecated:
            status = "DEPRECATED"
        elif is_weak and is_dropped:
            status = "CRITICAL"
            n_deprecate += 1
        elif is_weak or is_dropped:
            status = "WARN"
            n_warn += 1

        # 如果是活跃因子且 CRITICAL，自动降级
        fp = Path(filepath) if filepath else None
        if status == "CRITICAL" and not is_deprecated and fp and fp.exists():
            DEPRECATED_DIR.mkdir(parents=True, exist_ok=True)
            dest = DEPRECATED_DIR / fp.name
            import shutil
            shutil.move(str(fp), str(dest))
            print(f"      ⬇️ 自动降级: {name} ({fid}) — 当前 IC={curr_ic_abs:.4f}, "
                  f"历史 IC={hist_ic:.4f}, 降幅={ic_drop:.1%}")
            is_deprecated = True
            status = "DEPRECATED"

        results.append({
            "name": name,
            "factor_id": fid,
            "status": status,
            "hist_ic": hist_ic,
            "curr_ic": curr_ic_abs,
            "ic_drop": ic_drop,
            "is_weak": is_weak,
            "is_dropped": is_dropped,
            "is_deprecated": is_deprecated,
        })

        # 控制台输出（每 5 个一组）
        if i % 5 == 0 or i == len(factors) or status != "OK":
            status_icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🔴", "DEPRECATED": "⬇️"}.get(status, "❓")
            print(f"      [{i}/{len(factors)}] {status_icon} {name}: "
                  f"当前 IC={curr_ic_abs:.4f}, 历史 IC={hist_ic:.4f}, "
                  f"降幅={ic_drop:.1%}")

    # ── Step 4: 输出报告 ──
    elapsed = time.time() - t0
    print(f"\n[4/4] 验证完成: {len(factors)} 个因子, 耗时 {elapsed:.1f}s")
    print(f"      ✅ 正常: {sum(1 for r in results if r['status'] == 'OK')}")
    print(f"      ⚠️  警告: {n_warn}")
    print(f"      🔴 降级: {n_deprecate}")
    print(f"      ⬇️ 已降级: {sum(1 for r in results if r['is_deprecated'])}")

    # 写入报告
    report_dir = REPORTS_ROOT / today
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"futures_factor_revalidation_{today}.md"

    lines: list[str] = []
    def w(s=""):
        lines.append(s)

    w(f"# 期货精英因子重验证报告 — {today}")
    w()
    w(f"生成时间: {today} | 耗时: {elapsed:.1f}s")
    w(f"验证因子: {len(factors)} 个（活跃: {sum(1 for r in results if not r['is_deprecated'])}）")
    w(f"数据窗口: {len(common_dates)} 个交易日 × {len(panel)} 个品种")
    w(f"IC 失效阈值: {ic_threshold} | 降幅阈值: {drop_threshold:.0%}")
    w()
    w("## 验证结果汇总")
    w()
    w(f"- ✅ 正常: {sum(1 for r in results if r['status'] == 'OK')}")
    w(f"- ⚠️  警告: {n_warn}")
    w(f"- 🔴 新降级: {n_deprecate}")
    w(f"- ⬇️ 已降级: {sum(1 for r in results if r['is_deprecated'])}")
    w()

    # 异常因子详情
    abnormal = [r for r in results if r["status"] in ("WARN", "CRITICAL", "DEPRECATED")]
    if abnormal:
        w("## 异常因子详情")
        w()
        w("| 因子名称 | 状态 | 历史 IC | 当前 IC | 降幅 | 说明 |")
        w("|----------|------|---------|---------|------|------|")
        for r in abnormal:
            status_label = {"OK": "正常", "WARN": "警告", "CRITICAL": "降级", "DEPRECATED": "已降级"}.get(r["status"], "?")
            reasons = []
            if r["is_weak"]:
                reasons.append(f"IC<{ic_threshold}")
            if r["is_dropped"]:
                reasons.append(f"降幅>{drop_threshold:.0%}")
            reason_str = "+".join(reasons) if reasons else "—"
            w(f"| {r['name']} | {status_label} | {r['hist_ic']:.4f} | {r['curr_ic']:.4f} | {r['ic_drop']:.1%} | {reason_str} |")
        w()

    # 全部因子 IC 分布
    w("## 全部因子 IC 分布")
    w()
    ics = [r["curr_ic"] for r in results if not r["is_deprecated"]]
    if ics:
        w(f"- 平均 IC: {np.mean(ics):.4f}")
        w(f"- 中位数 IC: {np.median(ics):.4f}")
        w(f"- 标准差: {np.std(ics):.4f}")
        w(f"- IC>0.05: {sum(1 for ic in ics if ic > 0.05)}/{len(ics)}")
        w(f"- IC<0.02: {sum(1 for ic in ics if ic < 0.02)}/{len(ics)}")
    w()

    # 历史 IC vs 当前 IC 对比
    w("## 因子 IC 变化对比")
    w()
    w("| 因子名称 | 历史 IC | 当前 IC | 变化 | 状态 |")
    w("|----------|---------|---------|------|------|")
    for r in sorted(results, key=lambda x: -x["ic_drop"] if not x["is_deprecated"] else 0):
        if r["is_deprecated"]:
            continue
        change = r["curr_ic"] - r["hist_ic"]
        status_icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🔴"}.get(r["status"], "❓")
        w(f"| {r['name']} | {r['hist_ic']:.4f} | {r['curr_ic']:.4f} | {change:+.4f} | {status_icon} |")
    w()

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] 报告已保存: {out_path}")

    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="期货精英因子全量重验证")
    parser.add_argument("--ic-threshold", type=float, default=0.02,
                        help="IC 绝对值低于此值视为失效 (default: 0.02)")
    parser.add_argument("--drop-threshold", type=float, default=0.3,
                        help="IC 降幅超过此比例标记警告 (default: 0.3)")
    parser.add_argument("--days", type=int, default=120, help="回看天数")
    parser.add_argument("--max-symbols", type=int, default=0,
                        help="最大品种数，0=全量")
    args = parser.parse_args()
    sys.exit(main(
        ic_threshold=args.ic_threshold,
        drop_threshold=args.drop_threshold,
        days=args.days,
        max_symbols=args.max_symbols,
    ))