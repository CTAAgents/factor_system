# -*- coding: utf-8 -*-
"""能化链盲测失效品种（BZ0/PL0）归因分析（一次性分析脚本，非生产代码）。

复用 scripts/futures_signal_pipeline.py 的同一计算路径（数据面板、因子库、
信号矩阵、IC 口径），对盲测 8 品种逐一拆解：
  1. 数据覆盖度（行数、起始日、是否新上市品种）
  2. 因子信号质量（常数占比 / NaN 占比 / 信号标准差）
  3. 逐因子时序 IC（原始口径，无方向校正）
  4. 因子符号一致性（IC 同向占比 → 等权合成是否互相抵消）
  5. 合成信号 IC 三种口径对比：
       raw  = 等权未翻转（= 报告盲测 IC 口径）
       flip = 等权 + 品种级符号翻转
       wflip = abs(IC) 加权 + 符号翻转（= 实盘交易信号口径）
  6. 当前因子读数分解（驱动今日得分的方向来源）
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 复用信号管线的同构计算（保证口径一致）
spec = importlib.util.spec_from_file_location(
    "futures_signal_pipeline",
    PROJECT_ROOT / "scripts" / "futures_signal_pipeline.py",
)
pipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipe)

from fts.data import FTSDataProvider  # noqa: E402
from fts.data_futures import ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_SYMBOLS  # noqa: E402

WEIGHTS_PATH = PROJECT_ROOT / "memory" / "portfolio" / "energy" / "factor_weights.json"
DAYS = 300
FWD = 5  # 前向收益周期（与管线一致）

IGNORE_WARNINGS = True
if IGNORE_WARNINGS:
    import warnings

    warnings.filterwarnings("ignore")


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import spearmanr

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        try:
            from scipy.stats import ConstantInputWarning

            warnings.filterwarnings("ignore", category=ConstantInputWarning)
        except ImportError:
            pass
        ic, _ = spearmanr(a, b)
    return float(ic) if np.isfinite(ic) else np.nan


def _fwd_ret(closes: np.ndarray, fwd: int = FWD) -> np.ndarray:
    out = np.full(len(closes), np.nan)
    out[:-fwd] = (closes[fwd:] - closes[:-fwd]) / np.maximum(closes[:-fwd], 1e-10)
    return out


def main() -> None:
    print("=" * 78)
    print("能化链盲测失效品种（BZ0/PL0）归因分析")
    print("=" * 78)

    # ── 1. 与管线完全一致的加载路径 ──
    weights = pipe._load_l3_combo_weights(weights_path=WEIGHTS_PATH)
    factors = pipe._load_l3_combo_factors(weights, market="energy")
    print(f"[加载] L3 组合因子 {len(factors)} 个, 权重文件 {WEIGHTS_PATH.name}")

    symbols = list(ENERGY_CHAIN_SYMBOLS) + list(ENERGY_CHAIN_HOLDOUT)
    provider = FTSDataProvider()
    print(f"[加载] 面板 {len(symbols)} 品种, days={DAYS} ...")
    panel, common_dates = provider.get_futures_panel(symbols=symbols, days=DAYS)
    # 过滤陈旧品种（与管线一致）
    if len(common_dates) > 0:
        last_common = common_dates[-1]
        stale = [s for s, df in panel.items() if df.index[-1] < last_common]
        for s in stale:
            panel.pop(s)
    print(f"[加载] 面板 {len(panel)} 品种 × {len(common_dates)} 交易日")

    # ── 2. 信号矩阵 ──
    signal_matrix = pipe._compute_signal_matrix(panel, factors, use_optimizer=False)
    print(f"[计算] 信号矩阵完成: {sum(len(v) for v in signal_matrix.values())} 项")

    # ── 2b. 精确复算报告口径盲测 IC（管线同函数），并与手写复算对比 ──
    holdout_set = set(ENERGY_CHAIN_HOLDOUT) & set(panel.keys())
    holdout_result = pipe._compute_holdout_validation(
        signal_matrix, panel, list(common_dates), {}, holdout_set
    )
    print("\n[复核] 管线 _compute_holdout_validation 逐品种 IC（报告口径）:")
    for sym, ic in sorted(holdout_result.get("details", {}).items()):
        print(f"   {sym}: {ic:+.4f}")
    # 逐品种诊断：对齐细节（新上市品种早期 NaN 如何处理）
    print("\n[复核] 对齐诊断（新上市品种）:")
    for sym in ("BZ0", "PL0", "PR0", "FG0"):
        df = panel.get(sym)
        if df is None:
            continue
        aligned = df.reindex(list(common_dates))
        closes = aligned["close"].values
        n_nan_head = int(np.isnan(closes).sum())
        n_valid = int(np.isfinite(closes).sum())
        sig_len = len(signal_matrix.get(sym, {}).get(list(weights)[0], []))
        print(
            f"   {sym}: df_rows={len(df)}, closes_len={len(closes)}, "
            f"head_NaN={n_nan_head}, valid={n_valid}, sig_len={sig_len}"
        )


    # ── 3. 逐因子 IC 矩阵（原始口径，无翻转）──
    per_variety_ic = pipe._compute_per_variety_ic_matrix(
        signal_matrix, panel, list(common_dates), {}
    )

    # ── 4. L3 基础权重 → Regime 调整（需市场制度；此处直接用基础权重，Regime 缩放为类别级同乘，
    #      对品种间对比无方向影响）──
    total_base = sum(weights.values()) or 1.0
    factor_weights = {k: v / total_base for k, v in weights.items()}

    per_variety_weights = pipe._compute_per_variety_weights(factor_weights, per_variety_ic)

    # 品种级符号翻转（与管线 3e2 一致）
    per_variety_sign_flips: dict[str, dict[str, float]] = {}
    for fname, vics in per_variety_ic.items():
        for var, ic in vics.items():
            per_variety_sign_flips.setdefault(var, {})[fname] = 1.0 if ic >= 0 else -1.0

    # ── 5. 逐品种归因 ──
    holdout_syms = [s for s in ENERGY_CHAIN_HOLDOUT if s in panel]
    rows: list[dict[str, object]] = []
    print()
    print("=" * 78)
    print("盲测 8 品种逐品种归因")
    print("=" * 78)

    for sym in holdout_syms:
        df = panel[sym]
        sym_signals = signal_matrix.get(sym, {})
        closes = df["close"].values
        fwd = _fwd_ret(closes)

        # 数据覆盖度
        n_rows = len(df)
        start = str(df.index[0])[:10]
        end = str(df.index[-1])[:10]

        # 逐因子诊断
        factor_ics: dict[str, float] = {}
        factor_quality: dict[str, dict[str, float]] = {}
        for fname in factor_weights.keys():
            arr = sym_signals.get(fname)
            ic = per_variety_ic.get(fname, {}).get(sym, np.nan)
            factor_ics[fname] = ic
            if arr is None:
                factor_quality[fname] = {"nan_ratio": 1.0, "const_ratio": 1.0, "std": 0.0}
                continue
            finite = np.isfinite(arr)
            n_finite = int(finite.sum())
            q = {"nan_ratio": 1.0 - n_finite / max(len(arr), 1), "const_ratio": 0.0, "std": 0.0}
            if n_finite > 1:
                fin = arr[finite]
                q["const_ratio"] = float((np.abs(np.diff(fin)) < 1e-12).mean())
                q["std"] = float(np.nanstd(fin))
            factor_quality[fname] = q

        # 合成信号三种口径 IC
        def _composite_ic(weights_for_var: dict[str, float] | None, flips: dict[str, float] | None) -> float:
            comp = np.zeros(len(closes))
            n_active = 0
            for fname in factor_weights.keys():
                arr = sym_signals.get(fname)
                if arr is None:
                    continue
                sig = np.array(arr, dtype=float)
                if len(sig) < len(closes):
                    sig = np.pad(sig, (0, len(closes) - len(sig)), constant_values=np.nan)[: len(closes)]
                sig = np.where(np.isfinite(sig), sig, 0.0)
                if flips and fname in flips:
                    sig = sig * flips[fname]
                if weights_for_var and fname in weights_for_var:
                    sig = sig * weights_for_var[fname]
                comp += sig
                n_active += 1
            if n_active > 0:
                comp /= n_active
            valid = np.isfinite(comp) & np.isfinite(fwd)
            if valid.sum() < 10:
                return np.nan
            return _spearman(comp[valid], fwd[valid])

        raw_ic = _composite_ic(None, None)                      # 报告盲测 IC 口径
        flip_ic = _composite_ic(None, per_variety_sign_flips.get(sym))  # 等权+翻转
        wflip_ic = _composite_ic(per_variety_weights.get(sym), per_variety_sign_flips.get(sym))  # 加权+翻转

        # 因子符号一致性：|IC| > 0.05 的因子中，主导符号占比
        sig_factors = {f: ic for f, ic in factor_ics.items() if abs(ic) > 0.05}
        n_pos = sum(1 for ic in sig_factors.values() if ic > 0)
        agreement = (max(n_pos, len(sig_factors) - n_pos) / len(sig_factors)) if sig_factors else np.nan

        rows.append(
            {
                "sym": sym,
                "n_rows": n_rows,
                "start": start,
                "raw_ic": raw_ic,
                "flip_ic": flip_ic,
                "wflip_ic": wflip_ic,
                "agreement": agreement,
                "n_sig_factors": len(sig_factors),
                "factor_ics": factor_ics,
            }
        )
        print(
            f"\n▶ {sym}  (数据 {n_rows} 行, {start} → {end})"
        )
        print(
            f"   合成IC: 原始(报告口径)={raw_ic:+.4f} | 等权+翻转={flip_ic:+.4f} | "
            f"加权+翻转(交易口径)={wflip_ic:+.4f}"
        )
        print(f"   因子符号一致性: {agreement:.0%} ({n_pos} 正 / {len(sig_factors)-n_pos} 负, |IC|>0.05)")
        for fname, ic in factor_ics.items():
            q = factor_quality[fname]
            flip = per_variety_sign_flips.get(sym, {}).get(fname, 1.0)
            mark = ""
            if np.isfinite(ic) and abs(ic) > 0.05:
                mark = " ◀主导"
            print(
                f"     {fname[:46]:<46} IC={ic:+.4f} flip={'+' if flip>0 else '-'} "
                f"std={q['std']:.3f} const={q['const_ratio']:.0%}"
            )

    # ── 6. 对比表 ──
    print()
    print("=" * 78)
    print("盲测 8 品种归因对比表")
    print("=" * 78)
    hdr = f"{'品种':<6}{'行数':>5}  {'起始日':<11}{'原始IC':>9}{'翻转IC':>9}{'加权翻转IC':>11}{'符号一致':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['sym']:<6}{r['n_rows']:>5}  {str(r['start']):<11}"
            f"{r['raw_ic']:>+9.4f}{r['flip_ic']:>+9.4f}{r['wflip_ic']:>+11.4f}"
            f"{r['agreement']:>8.0%}"
        )

    # ── 7b. 错位量化：报告口径（信号未 reindex 错位）vs 修正口径（正确对齐）──
    report_ics: dict[str, float] = holdout_result.get("details", {})
    print()
    print("=" * 78)
    print("错位量化: 报告IC(错位口径) vs 修正IC(正确对齐) — 头部缺口天数")
    print("=" * 78)
    print(f"{'品种':<6}{'报告IC':>10}{'修正IC':>10}{'头部NaN':>8}{'缺口日':>7}{'行数':>6}")
    print("-" * 52)
    for r in rows:
        sym = r["sym"]
        df = panel[sym]
        aligned = df.reindex(list(common_dates))
        head_nan = int(np.isnan(aligned["close"].values).sum())
        # r['raw_ic'] 为正确对齐口径（df 自身索引）
        print(
            f"{sym:<6}{report_ics.get(sym, float('nan')):>+10.4f}{r['raw_ic']:>+10.4f}"
            f"{head_nan:>8}{head_nan:>7}{r['n_rows']:>6}"
        )


    print()
    print("=" * 78)
    print("训练池 12 品种原始 IC 参照（同口径）")
    print("=" * 78)
    train_rows: list[dict[str, object]] = []
    for sym in [s for s in ENERGY_CHAIN_SYMBOLS if s in panel]:
        df = panel[sym]
        sym_signals = signal_matrix.get(sym, {})
        closes = df["close"].values
        fwd = _fwd_ret(closes)
        comp = np.zeros(len(closes))
        n_active = 0
        for fname in factor_weights.keys():
            arr = sym_signals.get(fname)
            if arr is None:
                continue
            sig = np.array(arr, dtype=float)
            if len(sig) < len(closes):
                sig = np.pad(sig, (0, len(closes) - len(sig)), constant_values=np.nan)[: len(closes)]
            comp += np.where(np.isfinite(sig), sig, 0.0)
            n_active += 1
        comp /= max(n_active, 1)
        valid = np.isfinite(comp) & np.isfinite(fwd)
        ic = _spearman(comp[valid], fwd[valid]) if valid.sum() >= 10 else np.nan
        train_rows.append({"sym": sym, "n_rows": len(df), "start": str(df.index[0])[:10], "ic": ic})
        print(f"  {sym:<6} 行数={len(df):>4} 起始={str(df.index[0])[:10]}  原始IC={ic:+.4f}")

    # ── 7c. BZ0/PL0 逐因子: 错位IC(管线) vs 修正IC(df自身索引) ──
    print()
    print("=" * 78)
    print("BZ0/PL0 逐因子: 错位IC(管线口径) vs 修正IC(正确对齐)")
    print("=" * 78)
    for sym in ("BZ0", "PL0"):
        df = panel[sym]
        closes = df["close"].values
        fwd = _fwd_ret(closes)
        print(f"\n▶ {sym} (df 自身索引 {len(df)} 行, 正确对齐):")
        for fname in weights.keys():
            arr = signal_matrix.get(sym, {}).get(fname)
            if arr is None:
                continue
            sig = np.where(np.isfinite(arr), arr, 0.0)
            valid = np.isfinite(sig) & np.isfinite(fwd)
            corr_ic = _spearman(sig[valid], fwd[valid]) if valid.sum() >= 10 else np.nan
            pipe_ic = per_variety_ic.get(fname, {}).get(sym, np.nan)
            print(
                f"     {fname[:44]:<44} 管线IC={pipe_ic:+.4f}  修正IC={corr_ic:+.4f}"
                f"  Δ={corr_ic - pipe_ic:+.4f}"
            )

    # ── 8. 关键结论量 ──
    print()
    print("=" * 78)
    print("关键结论量")
    print("=" * 78)
    fail = [r for r in rows if r["sym"] in {"BZ0", "PL0"}]
    ok = [r for r in rows if r["sym"] not in {"BZ0", "PL0"}]
    print(f"失效品种平均行数: {np.mean([r['n_rows'] for r in fail]):.0f} vs 有效品种: {np.mean([r['n_rows'] for r in ok]):.0f}")
    print(f"失效品种平均起始日: {[str(r['start']) for r in fail]}")
    print(f"失效品种 原始IC→翻转IC: " + ", ".join(f"{r['sym']}: {r['raw_ic']:+.4f}→{r['flip_ic']:+.4f}" for r in fail))
    print(f"失效品种 加权翻转IC(交易口径): " + ", ".join(f"{r['sym']}: {r['wflip_ic']:+.4f}" for r in fail))
    print(f"失效品种 符号一致性: " + ", ".join(f"{r['sym']}: {r['agreement']:.0%}" for r in fail))
    ok_agreements = [r["agreement"] for r in ok if np.isfinite(r["agreement"])]
    print(
        f"有效品种 符号一致性均值: "
        + (f"{np.mean(ok_agreements):.0%}" if ok_agreements else "N/A")
    )

    # 保存中间结果供报告引用
    out_path = PROJECT_ROOT / "reports" / "energy_chain" / "2026-08-16" / "attribution_holdout_failures_data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "holdout": [
                    {
                        "sym": r["sym"],
                        "n_rows": r["n_rows"],
                        "start": r["start"],
                        "raw_ic": r["raw_ic"],
                        "flip_ic": r["flip_ic"],
                        "wflip_ic": r["wflip_ic"],
                        "agreement": r["agreement"],
                        "factor_ics": r["factor_ics"],
                    }
                    for r in rows
                ],
                "train": train_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[OK] 中间数据已保存: {out_path}")


if __name__ == "__main__":
    main()
