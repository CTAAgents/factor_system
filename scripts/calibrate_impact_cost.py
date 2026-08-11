#!/usr/bin/env python3
"""
FTS 冲击成本实证标定脚本（C7，v2.100.1）

用真实主力合约历史（TQ-Local 17709 / DuckDB / AKShare 4 级降级，动态池同源）
统计「成交量相对活跃度 vs 日内波幅（实现滑点代理）」样本，做 log-log 幂回归
    impact_bps = a × (volume_pct)^b
输出标定参数建议（1% 成交量占比对应的冲击 bps），供 FTS_COST_IMPACT_BPS_PER_PCT
注入或 load_market_cost_config(overrides=...) 使用。

用法:
    python scripts/calibrate_impact_cost.py                 # 默认动态池 25 品种
    python scripts/calibrate_impact_cost.py --symbols RB0,CU0 --days 120
    python scripts/calibrate_impact_cost.py --json          # 机器可读输出

数据不足/API 失败优雅降级（不阻断），无真实数据时输出空报告。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 动态池缺省 25 核心品种（与 memory/portfolio/futures_dynamic_pool.json 同源；缓存缺失回退静态池）
_DEFAULT_SYMBOLS = ["RB0", "CU0", "AU0", "AG0", "AL0", "ZN0", "NI0", "SN0", "I0", "J0",
                    "JM0", "FG0", "SA0", "MA0", "TA0", "PP0", "EG0", "RU0", "BU0", "SC0",
                    "FU0", "M0", "Y0", "P0", "C0"]


def fit_impact_curve(samples: list[tuple[float, float]]) -> Optional[dict[str, Any]]:
    """log-log 幂回归：impact_bps = a × volume_pct^b。

    Args:
        samples: (volume_pct, realized_slippage_bps) 样本对列表

    Returns:
        {a, b, r2, n, impact_at_1pct}；样本不足（<5 或退化为无效）返回 None
    """
    if len(samples) < 5:
        return None
    xs = np.array([max(float(p), 1e-6) for p, _ in samples], dtype=float)
    ys = np.array([max(float(c), 1e-6) for _, c in samples], dtype=float)
    if np.std(xs) < 1e-12 or np.std(ys) < 1e-12:
        return None
    log_x = np.log(xs)
    log_y = np.log(ys)
    # OLS: log(y) = log(a) + b·log(x)
    x_mean, y_mean = float(np.mean(log_x)), float(np.mean(log_y))
    b = float(np.sum((log_x - x_mean) * (log_y - y_mean)) / np.sum((log_x - x_mean) ** 2))
    log_a = y_mean - b * x_mean
    a = float(np.exp(log_a))
    # R²
    resid = log_y - (log_a + b * log_x)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((log_y - y_mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "a": round(a, 4),
        "b": round(b, 4),
        "r2": round(r2, 4),
        "n": len(samples),
        "impact_at_1pct": round(a * (0.01 ** b), 4),  # 1% 成交量占比对应冲击 bps
    }


def collect_slippage_samples(df: Any, min_rows: int = 20) -> list[tuple[float, float]]:
    """从单品种 OHLCV 构造（成交量相对活跃度, 日内波幅 bps）样本。

    代理说明（实证标定的合理近似）:
        - volume_pct = 当日成交量 / 滚动 20 日均量（相对交易活跃度）
        - slippage_bps = (high - low) / close × 10000（日内波幅，作为实现滑点上界代理）

    Args:
        df: OHLCV DataFrame（需 close/high/low/volume 列）
        min_rows: 最小行数（不足返回空）

    Returns:
        样本列表 [(volume_pct, slippage_bps), ...]
    """
    if df is None or len(df) < min_rows:
        return []
    required = {"close", "high", "low", "volume"}
    if not required.issubset(set(df.columns)):
        logger.warning("[Calib] 缺列 %s，跳过样本收集", required - set(df.columns))
        return []
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    rolling = pd.Series(volume).rolling(20, min_periods=5).mean().to_numpy()
    samples: list[tuple[float, float]] = []
    for t in range(len(df)):
        if rolling[t] is None or np.isnan(rolling[t]) or rolling[t] <= 0:
            continue
        if close[t] <= 0:
            continue
        volume_pct = float(volume[t] / rolling[t])
        bps = float((high[t] - low[t]) / close[t]) * 10000.0
        if volume_pct > 0 and bps > 0 and np.isfinite(volume_pct) and np.isfinite(bps):
            samples.append((volume_pct, bps))
    return samples


def run_calibration(
    symbols: list[str],
    days: int = 120,
    use_dynamic_pool: bool = True,
) -> dict[str, Any]:
    """主流程：多品种收集样本 → 幂回归标定 → 报告。

    Args:
        symbols: 品种主连代码列表
        days: 回溯交易日数
        use_dynamic_pool: 品种列表缺失时尝试加载动态池

    Returns:
        标定报告 {symbols, n_samples, curve, recommendation, note}
    """
    from fts.data_futures import FuturesDataProvider

    if not symbols and use_dynamic_pool:
        try:
            from fts.data_futures import get_dynamic_core_subset

            dyn = get_dynamic_core_subset() or []
            if dyn:
                symbols = [s if str(s).endswith("0") else f"{s}0" for s in dyn]
        except Exception as e:  # noqa: BLE001
            logger.warning("[Calib] 动态池加载失败（%s），回退静态池", e)
    if not symbols:
        symbols = list(_DEFAULT_SYMBOLS)

    provider = FuturesDataProvider()
    all_samples: list[tuple[float, float]] = []
    per_symbol: dict[str, int] = {}
    for sym in symbols:
        try:
            df = provider.get_ohlcv(sym, days=days)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Calib] 品种 %s 数据获取失败: %s", sym, e)
            continue
        samples = collect_slippage_samples(df)
        if samples:
            all_samples.extend(samples)
            per_symbol[sym] = len(samples)

    curve = fit_impact_curve(all_samples) if all_samples else None
    recommendation = None
    if curve:
        recommendation = {
            "impact_bps_per_pct": curve["impact_at_1pct"],
            "apply_hint": "python -c \"from fts.factor_engine.cost_model import load_market_cost_config; "
                          "print(load_market_cost_config('futures', {'impact_bps_per_pct': %s}))\""
                          % curve["impact_at_1pct"],
        }
    return {
        "symbols": symbols,
        "per_symbol_samples": per_symbol,
        "n_samples": len(all_samples),
        "curve": curve,
        "recommendation": recommendation,
        "note": "slippage_proxy=日内波幅(high-low)/close；volume_pct=当日量/20日均量；数据不足时降级空报告",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FTS 冲击成本实证标定（C7）")
    parser.add_argument("--symbols", default=None, help="逗号分隔品种主连（默认动态池/静态 25 品种）")
    parser.add_argument("--days", type=int, default=120, help="回溯交易日数（默认 120）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    report = run_calibration(symbols, days=args.days)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=== 冲击成本实证标定报告（C7） ===")
        print(f"  品种: {len(report['symbols'])}  样本: {report['n_samples']}")
        if report["curve"]:
            c = report["curve"]
            print(f"  幂回归: impact_bps = {c['a']} × volume_pct^{c['b']}  (R²={c['r2']}, n={c['n']})")
            print(f"  建议 impact_bps_per_pct（1% 占比）: {c['impact_at_1pct']} bps")
            if report["recommendation"]:
                print(f"  注入: {report['recommendation']['apply_hint']}")
        else:
            print("  样本不足，未产出标定曲线（请检查数据源或增大 --days）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
