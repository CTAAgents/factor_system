#!/usr/bin/env python
"""validate_signal_delta.py — GAP-120 信号增量规则有效性验证。

背景：交易建议报告第 3/4/5 节增量规则（±0.02 加速/减速阈值、第 3 批加仓
"次日增量未反转"、止损"增量反转减半仓"、连续 3 天信号衰减平仓）为启发式
经验规则，未经样本外统计验证。本脚本对历史信号快照回放构建增量序列，
量化增量对未来收益的预测力：

    ① delta 分组（十分位）次日/未来 5 日收益单调性（spearman）
    ② 增量 IC（delta 与未来收益的逐品种 spearman，聚合均值/胜率）
    ③ 增量动量（delta 一阶自相关 / 符号持续性）
    ④ 阈值敏感性（±0.01~0.05 对"加速/减速"分类命中率影响）

样本来源：
    - 信号历史快照 reports/futures/signal_scores_history.jsonl
      {date: "...", scores: {symbol: score}}
    - 未来收益由 FTSDataProvider.get_futures_panel 加载（默认降级链）

诚实口径：样本不足（快照 <30 天）时如实标注"证据不足"，不伪造结论。

用法：
    python scripts/validate_signal_delta.py                     # 默认全量
    python scripts/validate_signal_delta.py --horizons 1 5      # 指定持有期
    python scripts/validate_signal_delta.py --out reports/futures/gap120_delta_validation.md
    python scripts/validate_signal_delta.py --json              # JSON 输出
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import pandas as pd
    from scipy.stats import spearmanr
except ImportError as e:  # pragma: no cover
    print(f"依赖缺失: {e}")
    sys.exit(1)

HISTORY_PATH = Path("reports/futures/signal_scores_history.jsonl")
DEFAULT_OUT = Path("reports/futures/gap120_delta_validation.md")

# GAP-120 阈值扫描区间（对齐 ±0.01~0.05）
THRESHOLD_SCAN = (0.01, 0.02, 0.03, 0.04, 0.05)


def load_history(path: Path) -> pd.DataFrame:
    """加载信号历史快照 → 长表 {date, symbol, score}。"""
    if not path.exists():
        raise FileNotFoundError(f"信号历史快照不存在: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        d = str(rec.get("date", ""))[:10]
        scores = rec.get("scores", {})
        if isinstance(scores, str):
            try:
                scores = json.loads(scores.replace("'", '"'))
            except Exception:  # noqa: BLE001
                scores = {}
        for sym, sc in (scores or {}).items():
            rows.append({"date": d, "symbol": sym, "score": float(sc)})
    if not rows:
        raise ValueError("信号历史快照为空")
    df = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    return df


def build_delta_series(df: pd.DataFrame) -> pd.DataFrame:
    """按品种计算日增量 delta[t] = score[t] − score[t−1]。"""
    out: list[pd.DataFrame] = []
    for _, g in df.groupby("symbol"):
        g = g.sort_values("date")
        g["delta"] = g["score"].diff()
        out.append(g)
    return pd.concat(out, ignore_index=True)


def load_forward_returns(history: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    """加载品种未来 N 日收益（复权收盘）并 merge 到快照。

    数据源：本地 DuckDB kline_cache 直读（快、离线可用）；不可用降级跳过
    未来收益校验（仅保留增量动量/统计口径）。
    """
    try:
        import duckdb

        db = Path("data/fts_history.duckdb")
        if not db.exists():
            raise FileNotFoundError("data/fts_history.duckdb 不存在")
        con = duckdb.connect(str(db), read_only=True)
        try:
            rows = con.execute(
                "SELECT symbol, datetime, close FROM kline_cache WHERE symbol IN ("
                + ",".join(f"'{s}'" for s in sorted(history['symbol'].unique()))
                + ") AND datetime >= DATE '2026-07-01' ORDER BY symbol, datetime"
            ).fetchall()
        except Exception:  # noqa: BLE001
            rows = con.execute(
                "SELECT symbol, date, close FROM kline_cache WHERE symbol IN ("
                + ",".join(f"'{s}'" for s in sorted(history['symbol'].unique()))
                + ") ORDER BY symbol, date"
            ).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] kline_cache 直读失败（{e}），跳过未来收益校验（仅增量动量/统计口径）")
        return history.assign(**{f"fwd_{h}": np.nan for h in horizons})

    closes: dict[str, pd.Series] = {}
    for sym, dt, close in rows:
        if close is None or (isinstance(close, float) and not np.isfinite(close)):
            continue
        ts = pd.Timestamp(str(dt)).strftime("%Y-%m-%d")
        closes.setdefault(sym, []).append((ts, float(close)))
    closes = {
        sym: pd.Series(dict(v)).sort_index().rename_axis("date")
        for sym, v in closes.items() if len(v) > 5
    }
    # 统一索引为 Timestamp（与快照日期对齐）
    closes = {
        sym: pd.Series(s.values, index=pd.to_datetime(s.index))
        for sym, s in closes.items()
    }

    fwd_rows: list[pd.DataFrame] = []
    for _, row in history.iterrows():
        sym, d = row["symbol"], str(row["date"])[:10]
        ts = pd.Timestamp(d)
        rec: dict[str, Any] = {"date": d, "symbol": sym}
        if sym in closes:
            s = closes[sym]
            idx = s.index
            for h in horizons:
                pos = idx.get_indexer([ts], method="ffill")[0]
                future = None
                if pos >= 0 and pos + h < len(idx):
                    p0, p1 = float(s.iloc[pos]), float(s.iloc[pos + h])
                    if p0 and np.isfinite(p0) and np.isfinite(p1):
                        future = p1 / p0 - 1.0
                rec[f"fwd_{h}"] = future
        fwd_rows.append(rec)
    fwd = pd.DataFrame(fwd_rows)
    return history.merge(fwd, on=["date", "symbol"], how="left")


def delta_ic(ddf: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """增量 IC：逐品种 spearman(delta, fwd_{h}) 聚合。"""
    per = []
    for _, g in ddf.dropna(subset=["delta", f"fwd_{horizon}"]).groupby("symbol"):
        if len(g) < 5:
            continue
        if float(np.std(g["delta"])) <= 0 or float(np.std(g[f"fwd_{horizon}"])) <= 0:
            continue
        rho, p = spearmanr(g["delta"], g[f"fwd_{horizon}"])
        if np.isfinite(rho):
            per.append({"symbol": g["symbol"].iloc[0], "ic": rho, "n": len(g), "p": p})
    if not per:
        return {"n_symbols": 0, "mean_ic": None, "median_ic": None, "positive_ratio": None}
    ics = [x["ic"] for x in per]
    return {
        "n_symbols": len(per),
        "mean_ic": float(np.mean(ics)),
        "median_ic": float(np.median(ics)),
        "positive_ratio": float(np.mean([i > 0 for i in ics])),
    }


def delta_decile_monotonicity(ddf: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """delta 十分位分组 vs 未来收益均值单调性（spearman）。"""
    sub = ddf.dropna(subset=["delta", f"fwd_{horizon}"])
    if len(sub) < 20:
        return {"n": len(sub), "spearman": None, "verdict": "样本不足"}
    try:
        sub = sub.copy()
        sub["decile"] = pd.qcut(sub["delta"].rank(method="first"), 10, labels=False)
    except Exception:  # noqa: BLE001
        return {"n": len(sub), "spearman": None, "verdict": "分组失败"}
    means = sub.groupby("decile")[f"fwd_{horizon}"].mean()
    rho, p = spearmanr(means.index, means.values)
    return {
        "n": len(sub),
        "spearman": float(rho) if np.isfinite(rho) else None,
        "p": float(p),
        "verdict": "delta 与未来收益单调" if (np.isfinite(rho) and rho > 0.2) else (
            "delta 与未来收益反单调" if (np.isfinite(rho) and rho < -0.2) else "无显著单调关系"
        ),
    }


def delta_momentum(ddf: pd.DataFrame) -> dict[str, Any]:
    """增量动量：delta 一阶自相关 + 符号持续性（P(delta_t·delta_{t-1}>0)）。"""
    out = []
    for _, g in ddf.sort_values(["symbol", "date"]).groupby("symbol"):
        d = g["delta"].dropna()
        if len(d) < 5:
            continue
        lag = d.shift(1).dropna()
        cur = d.loc[lag.index]
        same_sign = float(np.mean((lag > 0) == (cur > 0))) if len(lag) else None
        ac = float(np.corrcoef(lag, cur)[0, 1]) if len(lag) > 2 and np.std(lag) > 0 and np.std(cur) > 0 else None
        out.append({"symbol": g["symbol"].iloc[0], "same_sign_ratio": same_sign, "autocorr": ac})
    if not out:
        return {"n_symbols": 0}
    ss = [x["same_sign_ratio"] for x in out if x["same_sign_ratio"] is not None]
    acs = [x["autocorr"] for x in out if x["autocorr"] is not None]
    return {
        "n_symbols": len(out),
        "mean_same_sign_ratio": float(np.mean(ss)) if ss else None,
        "mean_autocorr": float(np.mean(acs)) if acs else None,
        "verdict": "增量动量延续（delta 正自相关）" if (acs and np.mean(acs) > 0.2) else (
            "增量动量反转（delta 负自相关）" if (acs and np.mean(acs) < -0.2) else "无显著增量动量"
        ),
    }


def threshold_sensitivity(ddf: pd.DataFrame, horizons: tuple[int, ...]) -> dict[str, Any]:
    """阈值敏感性：不同 |delta| 阈值的"加速/减速"分类对未来收益方向命中率。"""
    res: dict[str, Any] = {}
    h = horizons[0] if horizons else 5
    col = f"fwd_{h}"
    sub = ddf.dropna(subset=["delta", col])
    if len(sub) < 20:
        return {"n": len(sub), "note": "样本不足"}
    for thr in THRESHOLD_SCAN:
        accel = sub[sub["delta"] < -thr]  # 信号加速（delta 更负）
        decel = sub[sub["delta"] > thr]   # 信号减速/反转（delta 更正）
        hits_a = float(np.mean((accel[col] < 0))) if len(accel) else None   # 加速 → 后续收益为负？
        hits_d = float(np.mean((decel[col] > 0))) if len(decel) else None   # 减速 → 后续收益为正？
        res[str(thr)] = {
            "n_accel": int(len(accel)), "n_decel": int(len(decel)),
            "accel_future_negative_ratio": hits_a, "decel_future_positive_ratio": hits_d,
        }
    return res


def build_report(df: pd.DataFrame, horizons: tuple[int, ...], out_path: Path, as_json: bool) -> int:
    ddf = build_delta_series(df)
    ddf = load_forward_returns(ddf, horizons)
    n_dates = ddf["date"].nunique()
    n_symbols = ddf["symbol"].nunique()

    sections: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "sample": {
            "n_dates": int(n_dates),
            "n_symbols": int(n_symbols),
            "n_rows": int(len(ddf)),
            "note": "信号历史快照累计日数（<30 日为样本不足，结论仅供参考）",
        },
        "delta_ic": {f"fwd_{h}": delta_ic(ddf, h) for h in horizons},
        "delta_decile": {f"fwd_{h}": delta_decile_monotonicity(ddf, h) for h in horizons},
        "delta_momentum": delta_momentum(ddf),
        "threshold_sensitivity": threshold_sensitivity(ddf, horizons),
    }
    sufficient = n_dates >= 30
    sections["verdict"] = (
        "样本充足（≥30 日）——按上表显著性判定"
        if sufficient
        else "样本不足（<30 日）——增量规则有效性暂无法得出统计结论，需持续积累快照后复验"
    )

    if as_json:
        print(json.dumps(sections, ensure_ascii=False, indent=2))
        return 0

    md = [
        "# 信号增量规则有效性验证报告（GAP-120）",
        "",
        f"> 生成时间: {sections['generated_at']}",
        f"> 样本: {n_dates} 个交易日 × {n_symbols} 个品种（快照行 {len(ddf)}）",
        "",
        f"**总体判定**: {sections['verdict']}",
        "",
        "## 1. 增量 IC（delta 与未来收益 spearman）",
        "",
        "| 持有期 | 品种数 | 均值 IC | 中位 IC | IC>0 占比 |",
        "|:---|:---|:---|:---|:---|",
    ]
    for h, st in sections["delta_ic"].items():
        md.append(
            f"| {h} | {st['n_symbols']} | {st['mean_ic'] if st['mean_ic'] is not None else '—'} | "
            f"{st['median_ic'] if st['median_ic'] is not None else '—'} | "
            f"{st['positive_ratio'] if st['positive_ratio'] is not None else '—'} |"
        )
    md += ["", "## 2. delta 十分位单调性", "",
           "| 持有期 | 样本数 | spearman | 判定 |",
           "|:---|:---|:---|:---|"]
    for h, st in sections["delta_decile"].items():
        md.append(
            f"| {h} | {st['n']} | {st['spearman'] if st['spearman'] is not None else '—'} | {st['verdict']} |"
        )
    mom = sections["delta_momentum"]
    md += ["", "## 3. 增量动量（delta 自相关/符号持续）", "",
           f"- 品种数: {mom.get('n_symbols', 0)}",
           f"- 符号持续性 P(同向): {mom.get('mean_same_sign_ratio', '—')}",
           f"- delta 一阶自相关均值: {mom.get('mean_autocorr', '—')}",
           f"- 判定: {mom.get('verdict', '—')}",
           "",
           "## 4. 阈值敏感性（±0.01~0.05 加速/减速分类）", "",
           "| 阈值 | n_accel | n_decel | 加速→后续收益<0 | 减速→后续收益>0 |",
           "|:---|:---|:---|:---|:---|"]
    for thr, st in sections["threshold_sensitivity"].items():
        if not isinstance(st, dict) or "n_accel" not in st:
            continue
        md.append(
            f"| {thr} | {st['n_accel']} | {st['n_decel']} | "
            f"{st['accel_future_negative_ratio'] if st['accel_future_negative_ratio'] is not None else '—'} | "
            f"{st['decel_future_positive_ratio'] if st['decel_future_positive_ratio'] is not None else '—'} |"
        )
    md += ["", "## 5. 结论与建议", "",
           "- 若增量 IC/单调性/动量均不显著：增量规则疑似噪声驱动（day-over-day 差分放大模型输出噪声），"
           "建议第 3 批加仓/止损减半/衰减平仓规则降级为观察项。",
           "- 若增量 IC 显著且动量延续：增量含有效预测力，可保留并按阈值敏感性结果校准 ±0.02 阈值。",
           "- 快照历史 <30 日时上述判定无效，需积累样本后复验（建议在信号管道写入侧持续追加快照）。",
           ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"验证报告已写入: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GAP-120 信号增量规则有效性验证")
    parser.add_argument("--history", type=Path, default=HISTORY_PATH, help="信号历史快照 jsonl")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 5], help="未来收益持有期（日）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Markdown 报告输出路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON（不写报告）")
    args = parser.parse_args()

    trace_id = f"gap120_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        hist = load_history(args.history)
        horizons = tuple(args.horizons)
        return build_report(hist, horizons, args.out, args.json)
    except Exception as e:  # noqa: BLE001
        print(f"[{trace_id}] 验证失败: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
