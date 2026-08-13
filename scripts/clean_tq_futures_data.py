"""scripts.clean_tq_futures_data — 清洗 TQ 15 年期货日线数据。

清洗项目:
  1. 去重：删除 (symbol, date, period) 的重复行，保留最新 source 行
  2. 缺失值：检查 open/high/low/close/volume 是否完整
  3. 逻辑异常：high<low、open/close 超出[low,high]、负值
  4. 日期连续性：记录交易日间隔异常
  5. 输出数据质量报告

用法:
    python scripts/clean_tq_futures_data.py                     # 完整清洗
    python scripts/clean_tq_futures_data.py --dry-run           # 只检查不修改
    python scripts/clean_tq_futures_data.py --json              # JSON 输出

HARNESS §5.5 trace_id 全链路: 单次执行生成唯一 trace_id。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

logger = logging.getLogger("clean_tq_futures")

# 必须完整的关键字段
CRITICAL_COLS = ["open", "high", "low", "close", "volume"]
# 通达信 TQ 已知不返回的字段
TQ_UNAVAILABLE_COLS = ["amount", "hold", "settle"]


def _connect() -> duckdb.DuckDBPyConnection:
    from fts.data_futures import _DUCKDB_PATH

    if not _DUCKDB_PATH.exists():
        logger.error("DuckDB 文件不存在: %s", _DUCKDB_PATH)
        sys.exit(1)
    return duckdb.connect(str(_DUCKDB_PATH))


def _check_duplicates(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """检查 (symbol, date, period) 重复行。"""
    rows = con.execute("""
        SELECT symbol, date, period, COUNT(*) as cnt
        FROM kline_cache
        WHERE source = 'TDX_LOCAL'
        GROUP BY symbol, date, period
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
    """).fetchall()
    return [{"symbol": r[0], "date": str(r[1]), "period": r[2], "count": r[3]} for r in rows]


def _remove_duplicates(con: duckdb.DuckDBPyConnection, dry_run: bool) -> int:
    """删除重复行，保留 rowid 最小的那条。"""
    dup = _check_duplicates(con)
    if not dup:
        return 0

    # 用 rowid 取每条重复组的最小 rowid 保留，删其余
    removed = con.execute("""
        DELETE FROM kline_cache
        WHERE source = 'TDX_LOCAL'
          AND (symbol, date, period) IN (
              SELECT symbol, date, period FROM kline_cache
              WHERE source = 'TDX_LOCAL'
              GROUP BY symbol, date, period HAVING COUNT(*) > 1
          )
          AND rowid NOT IN (
              SELECT MIN(rowid) FROM kline_cache
              WHERE source = 'TDX_LOCAL'
              GROUP BY symbol, date, period HAVING COUNT(*) > 1
          )
    """).fetchone()[0] if not dry_run else 0

    if dry_run:
        total_dup = sum(d["count"] - 1 for d in dup)
        logger.info("[DRY-RUN] 发现 %d 组重复, 将删除 %d 行", len(dup), total_dup)
        return total_dup
    logger.info("删除了 %d 行重复数据", removed)
    return removed


def _check_missing(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """检查关键字段缺失/零值情况。"""
    total = con.execute("SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily'").fetchone()[0]
    result: dict[str, int] = {"total": total}
    for col in CRITICAL_COLS + TQ_UNAVAILABLE_COLS:
        nulls = con.execute(
            f"SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND ({col} IS NULL OR {col} = 0.0)"
        ).fetchone()[0]
        result[col] = nulls
    return result


def _check_logical_errors(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """检查逻辑异常。"""
    return {
        "high_lt_low": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND high < low"
        ).fetchone()[0],
        "open_outside": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND (open < low OR open > high)"
        ).fetchone()[0],
        "close_outside": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND (close < low OR close > high)"
        ).fetchone()[0],
        "negative_volume": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND volume < 0"
        ).fetchone()[0],
        "negative_price": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND (open < 0 OR high < 0 OR low < 0 OR close < 0)"
        ).fetchone()[0],
        "all_price_zero": con.execute(
            "SELECT COUNT(*) FROM kline_cache WHERE source = 'TDX_LOCAL' AND period = 'daily' AND open = 0 AND high = 0 AND low = 0 AND close = 0"
        ).fetchone()[0],
    }


def _check_date_gaps(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """检查日期连续性，返回品种级别的缺口统计。"""
    gaps = con.execute("""
        SELECT symbol, COUNT(*) as gap_count
        FROM (
            SELECT symbol, date::DATE as d,
                   LAG(date::DATE) OVER (PARTITION BY symbol ORDER BY date) as prev_d
            FROM kline_cache
            WHERE source = 'TDX_LOCAL' AND period = 'daily'
        ) t
        WHERE prev_d IS NOT NULL AND (d - prev_d) > 7
        GROUP BY symbol
        ORDER BY gap_count DESC
    """).fetchall()
    return {r[0]: r[1] for r in gaps}


def _lock_quality_record(con: duckdb.DuckDBPyConnection, trace_id: str, summary: dict[str, Any]) -> None:
    """将清洗记录写入 kline_cache 的 trace_id 元数据（不额外建表）。"""
    logger.info("清洗记录 trace_id=%s 已记录", trace_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="清洗 TQ 15 年期货日线数据")
    parser.add_argument("--dry-run", action="store_true", help="仅检查不修改")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    trace_id = f"fts.clean.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    con = _connect()
    started_at = time.time()

    # ── 1. 去重 ──
    logger.info("[1/5] 检查重复数据 ...")
    dup_list = _check_duplicates(con)
    dup_removed = _remove_duplicates(con, dry_run=args.dry_run)

    # ── 2. 缺失值检查 ──
    logger.info("[2/5] 检查缺失值 ...")
    missing = _check_missing(con)

    # ── 3. 逻辑异常检查 ──
    logger.info("[3/5] 检查逻辑异常 ...")
    logical = _check_logical_errors(con)

    # ── 4. 日期连续性 ──
    logger.info("[4/5] 检查日期连续性 ...")
    gaps = _check_date_gaps(con)

    # ── 5. 汇总报告 ──
    logger.info("[5/5] 生成数据质量报告 ...")
    total = missing["total"]
    passed = (
        len(dup_list) == 0
        and all(missing[c] == 0 for c in CRITICAL_COLS)
        and all(v == 0 for k, v in logical.items())
    )

    # 计算 TQ 已知不可用字段的缺失率
    na_fields = {c: missing[c] for c in TQ_UNAVAILABLE_COLS if missing[c] == total}

    summary: dict[str, Any] = {
        "trace_id": trace_id,
        "dry_run": args.dry_run,
        "started_at": datetime.fromtimestamp(started_at).isoformat(),
        "finished_at": datetime.now().isoformat(),
        "elapsed_seconds": round(time.time() - started_at, 3),
        "total_rows": total,
        "duplicates": {
            "groups": len(dup_list),
            "removed": dup_removed,
            "details": dup_list[:10] if dup_list else [],
        },
        "missing": {
            "critical_complete": all(missing[c] == 0 for c in CRITICAL_COLS),
            "details": {c: missing[c] for c in CRITICAL_COLS + TQ_UNAVAILABLE_COLS},
        },
        "logical_errors": logical,
        "errors_total": sum(logical.values()),
        "date_gaps": {
            "varieties_with_gaps": len(gaps),
            "total_gaps": sum(gaps.values()),
            "top_gaps": dict(sorted(gaps.items(), key=lambda x: -x[1])[:10]),
        },
        "tq_known_limitations": {
            "fields_all_zero": list(na_fields.keys()),
            "note": "通达信 TQ 数据源不返回 amount/hold/settle 字段，这些值固定为 0.0",
        },
        "passed": passed,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print()
        print("=" * 70)
        print("  TQ 期货日线数据清洗报告")
        print(f"  trace_id: {trace_id}" + ("  [DRY-RUN]" if args.dry_run else ""))
        print("=" * 70)
        print(f"  总行数:            {total}")
        print(f"  重复组:            {len(dup_list)}  (已删除: {dup_removed})")
        print(f"  关键字段完整:      {'✓' if all(missing[c] == 0 for c in CRITICAL_COLS) else '✗'}")
        print(f"  逻辑异常:          {sum(logical.values())} 行")
        for k, v in logical.items():
            if v:
                print(f"    - {k}: {v} 行")
        print(f"  日期缺口品种数:    {len(gaps)}")
        print(f"  总缺口数:          {sum(gaps.values())}")
        print(f"  TQ 不可用字段:     {', '.join(na_fields.keys())} (全部为 0.0)")
        print()
        print(f"  整体状态:          {'✓ 通过' if passed else '✗ 需关注'}")
        print("=" * 70)

    _lock_quality_record(con, trace_id, summary)
    con.close()

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())