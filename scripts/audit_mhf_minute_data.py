"""
scripts/audit_mhf_minute_data.py — 阶段0：期货分钟数据深度审计 + 流动性筛选。

从 TDX 17709 实时拉取动态池 25 品种的 5m 分钟与日线数据（绕过 DuckDB，
兼容 DuckDB 被锁定场景），统计数据深度与流动性，输出审计报告与合格品种池。

输出:
    - 报告:  reports/mhf/phase0_data_audit_{date}.md（每品种深度/流动性表）
    - 池:    memory/portfolio/futures/mhf_pool.json（合格品种池，Phase 1 读取）

用法:
    python scripts/audit_mhf_minute_data.py [--min-days 120] [--min-daily-amount 5e8]

设计约束:
    - 纯读取，不写 DuckDB（DuckDB 锁兼容）
    - 全部向量化，缺数据品种不抛错、标记降级
    - trace_id 全链路
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import get_dynamic_core_subset  # noqa: E402
from fts.data_sources.tdx_local_source import TdxLocalSource  # noqa: E402
from fts.live_trade.contracts import contract_multiplier  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports" / "mhf"
POOL_CACHE = (
    PROJECT_ROOT / "memory" / "portfolio" / "futures" / "mhf_pool.json"
)

MINUTE_BARS: int = 20000  # 5m 单次可拉上限（实测 14578 根 ≈ 10.8 个月）
DAY_BARS: int = 600
TRACE_ID: str = f"mhf_audit_{date.today().isoformat()}"


def _fetch_minute(sym: str, src: TdxLocalSource) -> pd.DataFrame:
    """拉取 5m 分钟数据，异常返回空 DataFrame（不抛错）。"""
    try:
        df = src.fetch_ohlcv(sym, MINUTE_BARS, trace_id=TRACE_ID)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        return df.dropna(subset=["datetime"])
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] {sym} 5m 拉取失败: {type(e).__name__} {e}")
        return pd.DataFrame()


def _fetch_daily(sym: str, src: TdxLocalSource) -> pd.DataFrame:
    """拉取日线数据，异常返回空 DataFrame（不抛错）。

    日线 schema 用 date 列（%Y%m%d 字符串），分钟用 datetime 列，分别归一化。
    """
    try:
        df = src.fetch_ohlcv(sym, DAY_BARS, trace_id=TRACE_ID)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        if "date" in df.columns:
            df["datetime"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        else:
            df["datetime"] = pd.to_datetime(df.get("datetime"), errors="coerce")
        return df.dropna(subset=["datetime"])
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] {sym} 日线拉取失败: {type(e).__name__} {e}")
        return pd.DataFrame()


def _audit_symbol(
    sym: str, minute_src: TdxLocalSource, day_src: TdxLocalSource
) -> dict[str, Any]:
    """审计单品种：返回深度与流动性统计字典。"""
    m5 = _fetch_minute(sym, minute_src)
    day = _fetch_daily(sym, day_src)
    rec: dict[str, Any] = {"symbol": sym}

    if m5.empty:
        rec.update(
            {"ok": False, "reason": "5m 无数据", "rows": 0, "days": 0,
             "start": None, "end": None, "avg_daily_amount": 0.0}
        )
        return rec

    vol = pd.to_numeric(m5["volume"], errors="coerce").fillna(0.0)
    close = pd.to_numeric(m5["close"], errors="coerce").fillna(0.0)
    amount_per_bar = vol * close  # 近似成交额
    dates = m5["datetime"].dt.normalize()
    n_days = dates.nunique()
    avg_daily_amount = float(amount_per_bar.groupby(dates).sum().mean())

    rec.update(
        {
            "ok": True,
            "rows": int(len(m5)),
            "days": int(n_days),
            "start": m5["datetime"].min().isoformat(),
            "end": m5["datetime"].max().isoformat(),
            "avg_daily_amount": avg_daily_amount,
            "daily_rows": float(amount_per_bar.groupby(dates).count().mean()),
        }
    )
    # 日线口径：可比成交额 = close × volume × 合约乘数，60 日均值（TDX 无 amount 字段，
    # 乘数修正后跨品种可比，为主流动性指标）
    if not day.empty:
        mult = contract_multiplier(sym)
        d_close = pd.to_numeric(day["close"], errors="coerce").fillna(0.0)
        d_vol = pd.to_numeric(day["volume"], errors="coerce").fillna(0.0)
        rec["day_avg_amount"] = float((d_close * d_vol * mult).tail(60).mean())
        rec["day_bars"] = int(len(day))
        rec["day_start"] = day["datetime"].min().isoformat()
        rec["day_end"] = day["datetime"].max().isoformat()
    return rec


def _fmt_amount(v: float) -> str:
    """成交额格式化（亿元）。"""
    return f"{v / 1e8:.2f} 亿"


def _is_qualified(rec: dict[str, Any], min_days: int, min_amount: float) -> bool:
    """合格判断：有数据 + 分钟交易日达标 + 可比日均成交额达标（纯函数，可测）。"""
    if not rec.get("ok"):
        return False
    if int(rec.get("days") or 0) < min_days:
        return False
    amount = rec.get("day_avg_amount") or rec.get("avg_daily_amount") or 0.0
    return float(amount) >= float(min_amount)


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段0 分钟数据审计")
    parser.add_argument("--min-days", type=int, default=120,
                        help="合格品种最低分钟交易日数（默认 120）")
    parser.add_argument("--min-daily-amount", type=float, default=5e8,
                        help="合格品种最低日均成交额（默认 5 亿元）")
    parser.add_argument("--out", type=str, default="",
                        help="报告输出路径（默认 reports/mhf/phase0_data_audit_{date}.md）")
    args = parser.parse_args()

    pool = get_dynamic_core_subset()
    print(f"动态池品种数: {len(pool)}  trace_id={TRACE_ID}")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    minute_src = TdxLocalSource(period="5m")
    day_src = TdxLocalSource(period="day")

    rows: list[dict[str, Any]] = []
    for i, sym in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {sym} ...")
        t0 = time.time()
        rec = _audit_symbol(sym, minute_src, day_src)
        rec["elapsed_s"] = round(time.time() - t0, 1)
        rows.append(rec)

    df = pd.DataFrame(rows)
    # 主流动性指标：日线真实成交额 60 日均值；日线缺失回退分钟近似
    df["effective_amount"] = df.apply(
        lambda r: r.get("day_avg_amount") or r.get("avg_daily_amount") or 0.0,
        axis=1,
    )
    df = df.sort_values("effective_amount", ascending=False)
    mask = df.apply(
        lambda r: _is_qualified(r.to_dict(), args.min_days, args.min_daily_amount),
        axis=1,
    )
    qualified = df[mask]
    excluded = df[~mask]

    # ── 报告 ──
    out_path = Path(args.out) if args.out else (
        REPORTS_DIR / f"phase0_data_audit_{date.today().isoformat()}.md"
    )
    lines: list[str] = [
        "# 阶段0 数据深度审计报告（中高频策略）",
        "",
        f"- 日期: {date.today().isoformat()}  trace_id: `{TRACE_ID}`",
        "- 数据源: TDX 17709（5m 分钟 + 日线，绕过 DuckDB）",
        f"- 审计品种: {len(pool)}（动态池）",
        f"- 合格品种: {len(qualified)}  剔除: {len(excluded)}",
        f"- 筛选条件: 分钟交易日 ≥ {args.min_days} 且 日均成交额 ≥ {args.min_daily_amount / 1e8:.0f} 亿",
        "",
        "## 品种明细（按日均成交额降序）",
        "",
        "| 品种 | 分钟行数 | 交易日数 | 时间跨度 | 日均成交额(日线口径) | 日均行数/日 | 日线对齐 | 状态 |",
        "|:--|--:|--:|:--|--:|--:|:--|:--|",
    ]
    for _, r in df.iterrows():
        span = f"{r['start']} ~ {r['end']}" if r.get("start") else "-"
        day_ok = "✓" if r.get("day_bars", 0) > 0 else "✗"
        status = "合格" if r["symbol"] in set(qualified["symbol"]) else (
            "数据缺失" if not r["ok"] else "流动性不足"
        )
        lines.append(
            f"| {r['symbol']} | {int(r['rows'])} | {int(r['days'])} | {span} | "
            f"{_fmt_amount(r['effective_amount'])} | {r.get('daily_rows', 0):.0f} | {day_ok} | {status} |"
        )
    lines += [
        "",
        "## 剔除明细",
        "",
        "| 品种 | 原因 |",
        "|:--|:--|",
    ]
    for _, r in excluded.iterrows():
        reason = r.get("reason", "流动性不足" if r.get("ok") else "数据缺失")
        lines.append(f"| {r['symbol']} | {reason} |")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已输出: {out_path}")

    # ── 品种池落盘 ──
    q_list = qualified["symbol"].tolist() if not qualified.empty else []
    payload = {
        "pool": q_list,
        "updated": date.today().isoformat(),
        "trace_id": TRACE_ID,
        "filter": {"min_days": args.min_days,
                   "min_daily_amount": args.min_daily_amount},
    }
    POOL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    POOL_CACHE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"合格品种池已落盘: {POOL_CACHE} ({len(q_list)} 品种)")
    print(f"合格池: {q_list}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"审计失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
