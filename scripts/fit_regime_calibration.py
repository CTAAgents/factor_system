"""Regime 置信度统计校准器离线拟合 CLI（GAP-094，28 计划远期）。

用法:
    python scripts/fit_regime_calibration.py [--data <csv|duckdb>] [--table <表名>] \
        [--window N] [--step N] [--horizon N] [--method isotonic|platt|binning] \
        [--min-samples N] [--out <json>] [--dry-run] [--trace-id <id>]

流程:
    1. 加载 OHLCV（--data 缺省时取真实期货数据（RB0），失败降级合成数据演示）；
    2. 用 RegimeAwareSelector（规则快路径）对历史窗口滚动检测，
       生成逐日 (regime, confidence) 序列（与 validate_regime.py T9 同口径）；
    3. 按制度方向预期构造命中标签（前向 horizon 收益/波动对照）：
         bull→正收益 / bear→负收益 / oscillate→|收益|≤中位数 /
         high_vol→波动≥中位数 / low_vol→波动<中位数
    4. 拟合 StatisticalRegimeCalibrator（isotonic 优先）并输出校准报告
       （样本分布、Brier 校准前/后、置信度阶梯命中率表）；
    5. 原子写校准 JSON（默认 data/regime_calibration.json），
       生产侧 _compute_exposure_scale 经 adaptive_config calibration_path 消费。

版本: v0.1.0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime import RegimeAwareSelector
from fts.factor_engine.regime_calibration import StatisticalRegimeCalibrator


def _build_synthetic_ohlcv(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """构造双制度合成 OHLCV：前段上涨（bull）→ 后段下跌（bear）→ 高波段。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    third = n // 3
    drift = np.concatenate(
        [
            np.full(third, 0.002),
            np.full(third, -0.002),
            np.full(n - 2 * third, 0.0),
        ]
    )
    noise = np.concatenate([rng.normal(0.0, 0.008, 2 * third), rng.normal(0.0, 0.03, n - 2 * third)])
    close = 100.0 * np.cumprod(1.0 + drift + noise)
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.002, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.002, n)))
    volume = rng.integers(1000, 10000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _load_ohlcv(data: str | None, table: str | None, trace_id: str) -> pd.DataFrame:
    """加载 OHLCV：csv / duckdb 表 / 真实期货数据（RB0）/ 合成数据兜底。"""
    if data is None:
        try:
            from fts.data_futures import FuturesDataProvider

            df = FuturesDataProvider().get_ohlcv("RB0", days=700, trace_id=trace_id)
            if df is not None and not df.empty:
                return df
        except Exception as e:  # 真实数据失败 → 合成兜底（保证可运行）
            print(f"[fit-regime] 真实期货数据获取失败，降级合成数据: {e}")
        return _build_synthetic_ohlcv()
    path = Path(data)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        for col in ("date", "datetime"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
                df = df.set_index(col)
                break
    else:
        import duckdb

        if table is None:
            raise SystemExit(f"--data 为 duckdb 文件时需提供 --table（文件: {path}）")
        con = duckdb.connect(str(path))
        try:
            df = con.execute(f'SELECT * FROM "{table}"').df()
        finally:
            con.close()
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"数据缺少列: {sorted(missing)}（现有: {list(df.columns)}）")
    return df[["open", "high", "low", "close", "volume"]]


def _build_regime_records(ohlcv: pd.DataFrame, window: int, step: int) -> list[dict[str, Any]]:
    """滚动窗口检测，返回逐检测步 {date, regime, confidence} 记录。"""
    selector = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False, use_msm=False)
    records: list[dict[str, Any]] = []
    n = len(ohlcv)
    for i in range(window, n, step):
        det = selector.detect(ohlcv.iloc[i - window : i])
        records.append(
            {
                "date": ohlcv.index[i - 1],
                "regime": det.get("regime", "oscillate"),
                "confidence": float(det.get("confidence", 0.5)),
            }
        )
    return records


def _hit_label(regime: str, fwd_ret: float, fwd_vol: float, med_abs: float, med_vol: float) -> int | None:
    """按制度方向预期判定命中：bull→正收益 / bear→负收益 / oscillate→低振幅 /
    high_vol→高波动 / low_vol→低波动；未知制度返回 None（跳过）。"""
    if regime == "bull":
        return int(fwd_ret > 0.0)
    if regime == "bear":
        return int(fwd_ret < 0.0)
    if regime == "oscillate":
        return int(abs(fwd_ret) <= med_abs)
    if regime == "high_vol":
        return int(fwd_vol >= med_vol)
    if regime == "low_vol":
        return int(fwd_vol < med_vol)
    return None


def _conf_bands_summary(conf: np.ndarray, hits: np.ndarray) -> list[dict[str, Any]]:
    """置信度阶梯命中率诊断表（每 0.2 一档）。"""
    rows = []
    for lo in np.arange(0.0, 0.8, 0.2):
        hi = lo + 0.2
        mask = (conf >= lo) & (conf < hi)
        if mask.sum() == 0:
            continue
        rows.append(
            {
                "band": f"[{lo:.1f},{hi:.1f})",
                "n": int(mask.sum()),
                "mean_conf": round(float(conf[mask].mean()), 3),
                "hit_rate": round(float(hits[mask].mean()), 3),
            }
        )
    mask = conf >= 0.8
    if mask.sum() > 0:
        rows.append(
            {
                "band": "[0.8,1.0]",
                "n": int(mask.sum()),
                "mean_conf": round(float(conf[mask].mean()), 3),
                "hit_rate": round(float(hits[mask].mean()), 3),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime 置信度统计校准器离线拟合（GAP-094）")
    parser.add_argument("--data", default=None, help="OHLCV CSV 或 duckdb 文件（缺省真实 RB0，失败合成）")
    parser.add_argument("--table", default=None, help="duckdb 表名")
    parser.add_argument("--window", type=int, default=60, help="滚动检测窗口（默认 60）")
    parser.add_argument("--step", type=int, default=1, help="滚动检测步长（默认 1）")
    parser.add_argument("--horizon", type=int, default=5, help="前向收益/波动周期（默认 5）")
    parser.add_argument("--method", default="isotonic", choices=["isotonic", "platt", "binning"])
    parser.add_argument("--min-samples", type=int, default=30, help="拟合最少样本数（默认 30）")
    parser.add_argument("--out", default=None, help="校准 JSON 输出路径（默认 data/regime_calibration.json）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告不保存")
    parser.add_argument("--trace-id", default="", help="HARNESS trace_id")
    args = parser.parse_args()

    trace_id = args.trace_id or f"fit-regime-{pd.Timestamp.now():%Y%m%d%H%M%S}"
    print(f"[fit-regime] trace_id={trace_id}")

    if args.window < 20 or args.step < 1 or args.horizon < 1:
        raise SystemExit("参数非法: --window>=20, --step>=1, --horizon>=1")

    ohlcv = _load_ohlcv(args.data, args.table, trace_id)
    print(f"[fit-regime] OHLCV 加载完成: {len(ohlcv)} 行 {ohlcv.index[0].date()}~{ohlcv.index[-1].date()}")

    records = _build_regime_records(ohlcv, args.window, args.step)
    if len(records) < args.min_samples:
        raise SystemExit(f"有效检测样本 {len(records)} < --min-samples {args.min_samples}，无法拟合")

    # 前向收益/波动（t → t+horizon）
    close = ohlcv["close"].astype(float)
    rets = close.pct_change()
    fwd_ret = (close.shift(-args.horizon) / close - 1.0).reindex(close.index)
    fwd_vol = rets.rolling(args.horizon).std().shift(-args.horizon).reindex(close.index)

    rec_dates = pd.DatetimeIndex([r["date"] for r in records])
    fr = fwd_ret.reindex(rec_dates).astype(float).to_numpy()
    fv = fwd_vol.reindex(rec_dates).astype(float).to_numpy()
    med_abs = float(np.nanmedian(np.abs(fr)))
    med_vol = float(np.nanmedian(fv))

    confs: list[float] = []
    hits: list[int] = []
    by_regime: dict[str, int] = {}
    for i, rec in enumerate(records):
        if math.isnan(fr[i]) or math.isnan(fv[i]):
            continue
        label = _hit_label(rec["regime"], fr[i], fv[i], med_abs, med_vol)
        if label is None:
            continue
        confs.append(rec["confidence"])
        hits.append(label)
        by_regime[rec["regime"]] = by_regime.get(rec["regime"], 0) + 1

    if len(confs) < args.min_samples:
        raise SystemExit(f"有效样本（含命中判定）{len(confs)} < --min-samples {args.min_samples}")

    cal = StatisticalRegimeCalibrator(method=args.method, min_samples=args.min_samples).fit(confs, hits)

    raw_brier = float(np.mean((np.asarray(confs) - np.asarray(hits)) ** 2))
    cal_brier = cal.brier_score(confs, hits)

    print("\n=== Regime 置信度统计校准报告 ===")
    print(f"方法: {cal.method} | 有效样本: {len(confs)} | 已拟合: {cal.calibrated}")
    print(f"制度分布: {by_regime}")
    print(f"Brier 校准前: {raw_brier:.4f} → 校准后: {cal_brier:.4f}")

    if cal.calibrated:
        print("\n置信度-命中率阶梯表（校准有效性诊断）:")
        for row in _conf_bands_summary(np.asarray(confs), np.asarray(hits)):
            print(f"  {row['band']}: n={row['n']:>4} 平均置信={row['mean_conf']:.3f} 实际命中率={row['hit_rate']:.3f}")

    out_path = Path(args.out or (_FTS_ROOT / "data" / "regime_calibration.json"))
    if args.dry_run:
        print(f"\n[dry-run] 不保存校准文件（--out 目标: {out_path}）")
    elif not cal.calibrated:
        print("\n警告: 校准器未拟合（样本不足/标签非法），不保存文件")
    else:
        cal.save(out_path)
        print(f"\n已保存校准文件: {out_path}")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "method": cal.method,
                    "n_samples": len(confs),
                    "fitted": cal.calibrated,
                    "brier_before": round(raw_brier, 4),
                    "brier_after": round(cal_brier, 4) if not math.isnan(cal_brier) else None,
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
