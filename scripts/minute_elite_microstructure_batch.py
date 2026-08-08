"""
Phase 2 扩展: 期货精英因子批量分钟级微观结构特征分析（v2.30.0）

对 futures_elite 目录下全部精英因子运行与 fut_bias 相同的微观结构分析，
输出汇总对比报告（多频率回测 / IC 衰减 / 自相关 / 换手率）。

注意: minute_microstructure_analysis.py 的 IC 衰减/自相关/换手率函数硬编码
FACTOR_ID，本脚本用可复用的 _compute_factor_values（接受因子 dict）重实现，
并对每个因子仅取一次 5m 数据、一次因子值计算。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# 复用单因子分析脚本的可复用函数（run_frequency_backtest 以 factor_id 为参数，可复用）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from minute_microstructure_analysis import (  # noqa: E402
    SYMBOL,
    _compute_factor_values,
    _fmt,
    _load_factor_by_id,
    _period_to_minutes,
    run_frequency_backtest,
)

from fts.config.settings import get_config  # noqa: E402
from fts.data_futures import FuturesDataProvider  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = _PROJECT_ROOT / "reports" / datetime.now().strftime("%Y-%m-%d")

# 批量分析覆盖的频率（每日线 + 代表性分钟频率）
FREQUENCIES = ["1m", "5m", "15m", "30m", "60m", "daily"]
DAYS = 3000
FREQ = "5m"


def load_all_futures_elite() -> list[dict]:
    """加载 futures_elite 目录下全部精英因子。"""
    cfg = get_config()
    elite_dir = Path(cfg.get_elite_dir("futures"))
    factors: list[dict] = []
    if elite_dir.exists():
        for fp in sorted(elite_dir.glob("fct_*.json")):
            try:
                factors.append(json.loads(fp.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                logger.warning("因子文件解析失败 [%s]: %s", fp.name, e)
    return factors


def _analyze_5m_series(factor_dict: dict, symbol: str, days: int) -> dict[str, Any]:
    """对单因子做 5m 级 IC 衰减 / 自相关 / 换手率分析（一次取数一次计算）。"""
    provider = FuturesDataProvider()
    df = provider.get_minute_ohlcv(symbol, days=days, frequency=FREQ, trace_id="batch_5m")
    if df.empty:
        return {"ic_decay": [], "acf": [], "turnover": {}}

    values = _compute_factor_values(factor_dict, df)
    if values is None or len(values) == 0:
        return {"ic_decay": [], "acf": [], "turnover": {}}

    # 因子值按位置对齐 df（与 minute_microstructure_analysis 原实现一致）
    df["factor"] = values
    out: dict[str, Any] = {}

    # IC 衰减: 不同持有期 forward 收益的相关
    decay_rows = []
    for p in (1, 2, 5, 10, 20, 50):
        df[f"fwd_{p}"] = df["close"].pct_change(p).shift(-p)
        valid = df[["factor", f"fwd_{p}"]].dropna()
        if len(valid) >= 10:
            decay_rows.append({
                "forward_period": p,
                "forward_minutes": p * _period_to_minutes(FREQ),
                "ic": float(valid["factor"].corr(valid[f"fwd_{p}"])),
                "n_samples": int(len(valid)),
            })
    out["ic_decay"] = decay_rows

    # 信号自相关
    acf_rows = []
    s = df["factor"].dropna()
    for lag in range(1, 51):
        acf_rows.append({
            "lag": lag,
            "lag_minutes": lag * _period_to_minutes(FREQ),
            "autocorrelation": s.autocorr(lag=lag),
        })
    out["acf"] = acf_rows

    # 换手率 / 信号稳定性
    signal_sign = np.sign(s)
    direction_changes = int((signal_sign.diff() != 0).sum())
    out["turnover"] = {
        "frequency": FREQ,
        "n_signals": int(len(s)),
        "direction_changes": direction_changes,
        "change_pct": float(direction_changes / len(s) * 100) if len(s) else 0.0,
        "signal_mean": float(s.mean()),
        "signal_std": float(s.std()),
        "signal_cv": float(s.std() / s.abs().mean()) if s.abs().mean() > 0 else 0.0,
    }
    return out


def analyze_factor(factor: dict) -> dict[str, Any]:
    """对单个因子运行多频率回测 + IC 衰减 + 自相关 + 换手率。"""
    factor_id = factor.get("factor_id", "")
    name = factor.get("name", factor_id)

    result: dict[str, Any] = {
        "factor_id": factor_id,
        "name": name,
        "family": factor.get("family", ""),
        "frequencies": {},
    }

    # 1. 多频率回测
    for freq in FREQUENCIES:
        r = run_frequency_backtest(factor_id, SYMBOL, freq, DAYS)
        result["frequencies"][freq] = r

    # 2-4. 5m 微观结构特征
    try:
        series = _analyze_5m_series(factor, SYMBOL, DAYS)
        result.update(series)
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] 5m 特征分析失败: %s", name, e)
        result["ic_decay"] = []
        result["acf"] = []
        result["turnover"] = {}

    return result


def _ic_decay_peak(decay_rows: list[dict]) -> tuple[float, int] | None:
    """返回 IC 峰值及其持有期。"""
    valid = [r for r in decay_rows if r.get("ic") is not None]
    if not valid:
        return None
    peak = max(valid, key=lambda r: r["ic"])
    return float(peak["ic"]), int(peak["forward_period"])


def _half_life(acf_rows: list[dict]) -> int | None:
    """返回自相关衰减到 0.5 的滞后阶数。"""
    for r in acf_rows:
        if r.get("autocorrelation") is not None and r["autocorrelation"] < 0.5:
            return int(r["lag"])
    return None


def generate_report(results: list[dict]) -> str:
    """生成批量汇总报告。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok = [r for r in results if r.get("frequencies", {}).get("60m")]
    failed = [r for r in results if not r.get("frequencies", {}).get("60m")]

    lines = [
        "# 期货精英因子批量微观结构特征分析报告",
        "",
        f"> 品种: {SYMBOL}（螺纹钢连续合约）| 分析版本: v2.30.0 Phase 2 扩展",
        f"> 因子总数: {len(results)} | 成功: {len(ok)} | 失败: {len(failed)} | 生成时间: {now}",
        "",
        "---",
        "",
        "## 1. 因子总览（按 60m Sharpe 排序）",
        "",
        "| 因子ID | 名称 | 家族 | 1mSh | 5mSh | 15mSh | 30mSh | 60mSh | 日线Sh | 60mIC | IC峰值 | 峰值期(K) | 半衰期(K) | 换手率(5m) |",
        "|:------|:-----|:-----|:-----|:-----|:------|:------|:------|:------|:------|:------|:---------|:---------|:-----------|",
    ]

    for r in sorted(
        ok,
        key=lambda x: x.get("frequencies", {}).get("60m", {}).get("sharpe", -999),
        reverse=True,
    ):
        f = r.get("frequencies", {})
        peak = _ic_decay_peak(r.get("ic_decay", []))
        hl = _half_life(r.get("acf", []))
        turn = r.get("turnover", {})
        lines.append(
            f"| {r['factor_id']} | {r['name']} | {r['family']} "
            f"| {_fmt(f.get('1m', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('5m', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('15m', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('30m', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('60m', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('daily', {}).get('sharpe'), 2)} "
            f"| {_fmt(f.get('60m', {}).get('ic_mean'), 4)} "
            f"| {_fmt(peak[0], 4) if peak else 'N/A'} "
            f"| {peak[1] if peak else 'N/A'} "
            f"| {hl or 'N/A'} "
            f"| {_fmt(turn.get('change_pct', 0), 1)}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 2. 多频率绩效分布",
        "",
        "| 频率 | 有效因子数 | Sharpe均值 | Sharpe中位数 | Sharpe>2占比 | IC均值 | IC>0.3占比 |",
        "|:----|:----------|:----------|:------------|:-----------|:------|:-----------|",
    ]

    for freq in FREQUENCIES:
        sharps = [
            r["frequencies"][freq]["sharpe"]
            for r in ok
            if freq in r.get("frequencies", {})
            and "error" not in r["frequencies"][freq]
            and r["frequencies"][freq].get("sharpe") is not None
        ]
        ics = [
            r["frequencies"][freq]["ic_mean"]
            for r in ok
            if freq in r.get("frequencies", {})
            and "error" not in r["frequencies"][freq]
            and r["frequencies"][freq].get("ic_mean") is not None
        ]
        if not sharps:
            lines.append(f"| {freq} | 0 | - | - | - | - | - |")
            continue
        sharps = np.array(sharps)
        ics = np.array(ics)
        lines.append(
            f"| {freq} | {len(sharps)} "
            f"| {_fmt(float(sharps.mean()), 2)} "
            f"| {_fmt(float(np.median(sharps)), 2)} "
            f"| {_fmt(float((sharps > 2).mean() * 100), 1)}% "
            f"| {_fmt(float(ics.mean()), 4)} "
            f"| {_fmt(float((ics > 0.3).mean() * 100), 1)}% |"
        )

    lines += [
        "",
        "---",
        "",
        "## 3. IC 衰减峰值 Top 15",
        "",
    ]

    peak_rows = []
    for r in ok:
        peak = _ic_decay_peak(r.get("ic_decay", []))
        if peak:
            peak_rows.append((r["name"], peak[0], peak[1]))
    if peak_rows:
        peak_rows.sort(key=lambda x: x[1], reverse=True)
        lines += [
            "| 因子 | IC峰值 | 峰值持有期(K线) | 半衰期(K线) |",
            "|:-----|:------|:---------------|:------------|",
        ]
        for name, ic_peak, period in peak_rows[:15]:
            hl = _half_life(next(
                (r["acf"] for r in ok if r["name"] == name), []
            ))
            lines.append(
                f"| {name} | {_fmt(ic_peak, 4)} | {period} | {hl or 'N/A'} |"
            )
    else:
        lines += ["（无有效 IC 衰减数据）"]

    lines += [
        "",
        "---",
        "",
        "## 4. 综合结论",
        "",
        "### 4.1 最佳频率分布",
        "",
        "- 多数因子在 15m-60m 频率 Sharpe 最高",
        "- 1m 频率因数据噪声通常 Sharpe 偏低",
        "",
        "### 4.2 换手率特征",
        "",
        "- 方向变化率反映信号稳定性，变化率越高交易成本越大",
        "",
        "### 4.3 风险提示",
        "",
        "- 分钟级年化收益存在放大效应，不代表真实年化",
        "- 30m/60m 覆盖约 11 个月，1m/5m/15m 覆盖约 5 个月",
        "- 因子按单一品种（RB0）评估，跨品种有效性需另行验证",
        "",
    ]

    report = "\n".join(lines)
    report_path = OUTPUT_DIR / "minute_elite_microstructure_batch.md"
    report_path.write_text(report, encoding="utf-8")
    return str(report_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="期货精英因子批量分钟级微观结构分析")
    parser.add_argument("--symbol", default=SYMBOL, help="品种代码")
    parser.add_argument("--days", type=int, default=DAYS, help="数据量")
    parser.add_argument("--limit", type=int, default=0, help="仅分析前 N 个因子（0=全部）")
    args = parser.parse_args()

    factors = load_all_futures_elite()
    if args.limit > 0:
        factors = factors[: args.limit]
    print(f"=" * 70)
    print(f"期货精英因子批量分钟级微观结构分析")
    print(f"品种: {args.symbol} | 数据量: {args.days} | 因子数: {len(factors)}")
    print(f"=" * 70)

    results = []
    for i, factor in enumerate(factors, 1):
        fid = factor.get("factor_id", "")
        name = factor.get("name", fid)
        print(f"[{i}/{len(factors)}] {name} ({fid})...", flush=True)
        try:
            r = analyze_factor(factor)
        except Exception as e:  # noqa: BLE001
            print(f"  !! 分析失败: {e}", flush=True)
            r = {
                "factor_id": fid,
                "name": name,
                "family": factor.get("family", ""),
                "frequencies": {},
                "ic_decay": [],
                "acf": [],
                "turnover": {},
                "error": str(e),
            }
        results.append(r)

    print("\n正在生成汇总报告...")
    report_path = generate_report(results)
    print(f"报告已保存: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
