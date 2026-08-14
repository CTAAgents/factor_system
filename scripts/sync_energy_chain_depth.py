# -*- coding: utf-8 -*-
"""能源产业链品种历史深度补全（GAP-Ixxx，A 数据补全）。

背景：LU0/PR0/PL0/BZ0 等新品种在 TqSdk 免费源下仅 120 行（约 4 个月），
训练链共同窗口被拉短至 ~120 行，因子统计意义极弱。实测 AKShare
`futures_zh_daily_sina` 可补全全历史：
    LU0 → 2020-06（1492 行）；PR0 → 2024-08（473 行）；
    PL0 → 2025-07（260 行）；BZ0 → 2025-07（270 行）。

本脚本将深度不足的能源链品种（训练链 + 盲测池）全历史写入 kline_cache
（复用 FuturesDataAggregator._write_cache 标准写路径，filelock + 短连接），
先删除该品种旧缓存行再插入，保证无残留浅数据污染。

用法（示例）:
    python scripts/sync_energy_chain_depth.py                       # 默认补全全部能源链品种
    python scripts/sync_energy_chain_depth.py --symbols LU0,PR0     # 仅补全指定品种
    python scripts/sync_energy_chain_depth.py --min-rows 300        # 深度阈值（默认 300）
    python scripts/sync_energy_chain_depth.py --dry-run             # 只报告不写库

失败透明：单品种失败记录并继续，不中断整体。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _symbol_variants(symbol: str) -> list[str]:
    """kline_cache 双格式变体（如 LU0 / LU）。"""
    base = symbol[:-1] if symbol.endswith("0") else symbol
    return [base, f"{base}0"]


def _current_rows(agg: Any, symbol: str) -> tuple[int, int, str | None]:
    """读取品种当前缓存行数与最新日期（尽力而为）。

    Returns:
        (真实行数, SYNTHETIC 行数, 最新日期) —— 真实行 = 非 SYNTHETIC 源行，
        SYNTHETIC 为合成降级脏数据，需清理重建。
    """
    try:
        import duckdb

        if agg.db_path is None or not agg.db_path.exists():
            return 0, 0, None
        con = duckdb.connect(str(agg.db_path), read_only=True)
        try:
            variants = _symbol_variants(symbol)
            placeholders = ",".join(["?"] * len(variants))
            n_real = con.execute(
                f"SELECT COUNT(*) FROM kline_cache WHERE symbol IN ({placeholders}) AND period='daily' "
                f"AND (source IS NULL OR source != 'SYNTHETIC')",
                variants,
            ).fetchone()[0]
            n_synth = con.execute(
                f"SELECT COUNT(*) FROM kline_cache WHERE symbol IN ({placeholders}) AND period='daily' "
                f"AND source = 'SYNTHETIC'",
                variants,
            ).fetchone()[0]
            latest = con.execute(
                f"SELECT MAX(date) FROM kline_cache WHERE symbol IN ({placeholders}) AND period='daily' "
                f"AND (source IS NULL OR source != 'SYNTHETIC')",
                variants,
            ).fetchone()[0]
            return int(n_real), int(n_synth), str(latest) if latest else None
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0, 0, None


def _fetch_akshare_full(base: str, trace_id: str) -> Any | None:
    """经 AKShare 拉取品种全历史日线（DataFrame，未限制深度）。"""
    try:
        import akshare as ak

        df = ak.futures_zh_daily_sina(symbol=f"{base}0")
        if df is None or df.empty:
            return None
        df = df.reset_index(drop=True)
        return df
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("[补全] AKShare 拉取失败 %s: %s", base, e)
        return None


def _build_cache_df(df: Any, symbol: str, trace_id: str) -> Any:
    """将 AKShare 日线转 kline_cache 17 列 schema。"""
    import pandas as pd

    base = symbol[:-1] if symbol.endswith("0") else symbol
    now = datetime.now()

    def _col(name: str, default: float = 0.0) -> Any:
        """取列（缺列时用默认值广播为同长 Series）。"""
        if name in df.columns:
            return df[name].astype(float)
        return pd.Series(default, index=df.index)

    out = pd.DataFrame(
        {
            "symbol": f"{base}0",
            "period": "daily",
            "date": pd.to_datetime(df["date"]),
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["volume"].astype(float),
            "amount": _col("amount"),
            "hold": _col("hold"),
            "settle": _col("settle"),
            "pre_settle": 0.0,
            "oi_change": 0.0,
            "vwap": 0.0,
            "source": "AKSHARE",
            "fetched_at": now,
            "trace_id": trace_id,
        }
    )
    return out


def sync_energy_chain_depth(
    symbols: list[str] | None = None,
    min_rows: int = 300,
    dry_run: bool = False,
    trace_id: str = "energy_depth",
) -> dict[str, Any]:
    """补全能源链品种历史深度。

    Args:
        symbols: 品种列表；None 时使用训练链 + 盲测池全部品种。
        min_rows: 深度阈值（缓存行数低于该值触发补全）。
        dry_run: 仅报告不写库。
        trace_id: HARNESS trace_id。

    Returns:
        {"checked": n, "filled": n, "skipped": n, "failed": n, "detail": [...]}
    """
    from fts.data_futures import ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_SYMBOLS
    from fts.data_sources.aggregator import FuturesDataAggregator

    if symbols is None:
        symbols = sorted(set(ENERGY_CHAIN_SYMBOLS) | set(ENERGY_CHAIN_HOLDOUT))
    elif isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]

    agg = FuturesDataAggregator(
        sources=[],
        enhancers=[],
        db_path=PROJECT_ROOT / "data" / "fts_history.duckdb",
        cache_max_age_days=30,
    )
    if agg.db_path is None or not agg.db_path.exists():
        return {"checked": 0, "filled": 0, "skipped": 0, "failed": 0,
                "detail": [], "error": f"缓存库不存在: {agg.db_path}"}

    detail: list[dict[str, Any]] = []
    filled = skipped = failed = 0
    for sym in symbols:
        rows_real, rows_synth, latest = _current_rows(agg, sym)
        # 跳过条件：真实行数达标 且 无 SYNTHETIC 合成脏数据
        if rows_real >= min_rows and rows_synth == 0:
            skipped += 1
            detail.append({"symbol": sym, "rows": rows_real, "latest": latest, "action": "skip"})
            continue
        df = _fetch_akshare_full(sym[:-1] if sym.endswith("0") else sym, trace_id)
        if df is None or df.empty:
            failed += 1
            detail.append({"symbol": sym, "rows": rows_real, "synth": rows_synth, "latest": latest, "action": "failed", "error": "AKShare 空数据"})
            continue
        if dry_run:
            filled += 1
            detail.append({"symbol": sym, "rows": rows_real, "synth": rows_synth, "latest": latest, "action": "dry-run", "akshare_rows": len(df)})
            continue
        cache_df = _build_cache_df(df, sym, trace_id)
        # 先删除该品种全部旧缓存行（含 SYNTHETIC 脏数据），再插入 AKShare 全历史
        with agg._write_scope() as con:  # noqa: SLF001 — 复用标准写路径
            if con is not None:
                variants = _symbol_variants(sym)
                placeholders = ",".join(["?"] * len(variants))
                con.execute(
                    f"DELETE FROM kline_cache WHERE symbol IN ({placeholders}) AND period='daily'",
                    variants,
                )
        agg._write_cache(cache_df)  # noqa: SLF001
        filled += 1
        detail.append({"symbol": sym, "rows": rows_real, "synth": rows_synth, "latest": latest,
                       "action": "filled", "akshare_rows": len(cache_df)})

    return {"checked": len(symbols), "filled": filled, "skipped": skipped,
            "failed": failed, "detail": detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="能源产业链品种历史深度补全")
    parser.add_argument("--symbols", default=None, help="品种清单（逗号分隔；默认训练链+盲测池全部）")
    parser.add_argument("--min-rows", type=int, default=300, help="深度阈值（默认 300 行）")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写库")
    args = parser.parse_args(argv)

    result = sync_energy_chain_depth(
        symbols=args.symbols,
        min_rows=args.min_rows,
        dry_run=args.dry_run,
        trace_id="energy_depth.cli",
    )
    print(f"checked={result['checked']} filled={result['filled']} skipped={result['skipped']} failed={result['failed']}")
    for d in result["detail"]:
        print(f"  {d['symbol']}: rows={d['rows']} latest={d['latest']} action={d['action']}"
              + (f" akshare_rows={d.get('akshare_rows')}" if "akshare_rows" in d else "")
              + (f" error={d.get('error')}" if "error" in d else ""))
    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return 1
    return 0 if result["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
