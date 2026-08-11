"""P3-B 行情库冷热归档：kline 类表按年份导出冷层 Parquet + 热库瘦身（plans/29 Phase 3）

背景:
    `data/fts_history.duckdb` 行情库（kline_cache 367k 行 / contract_kline 911k 行）为
    单写者长期持有（演化进程持有写锁），早期年份（2013 前）数据量小、访问频次低，
    属冷数据。P3-B 将其按年份导出至 `data/archive/history_{table}_{year}.parquet`
    （Parquet 冷层）并从热库 DELETE，实现热库瘦身、热查询提速。

模式:
    --dry-run       只读统计各年份行数，预估归档规模，不写库（安全）
    --verify-only   校验冷层 Parquet 行数与应归档行数一致（只读）
    --archive       执行归档：导出 + DELETE（需无写锁，破坏性，需显式 --until-year）
    --table         目标表（默认 kline_cache；contract_kline 亦支持）
    --until-year    归档截止年份（含），默认 2013
    --db-path       行情库路径（默认 data/fts_history.duckdb）
    --archive-root  冷层根目录（默认 data/archive）
    --json          JSON 输出

关键约束:
    - 库被写锁占用时（BADOPENFILE）连接失败 → dry-run/verify 降级提示、archive 拒绝执行
    - kline_cache.date 实为 VARCHAR，按 year(date::DATE) 判定年份
    - trace_id 全链路（fts.history_archive.{ts}）

HARNESS: 幂等可重入；失败透明；实际归档执行需在无写锁窗口（演化任务结束后）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本独立运行时的导入引导（项目惯用法，ruff E402 豁免）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("archive_history_cold")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "fts_history.duckdb"
DEFAULT_ARCHIVE_ROOT = PROJECT_ROOT / "data" / "archive"
DEFAULT_TABLE = "kline_cache"
DEFAULT_UNTIL_YEAR = 2013


def _connect(db_path: Path) -> Any | None:
    """打开文档库连接；被写锁占用时返回 None（不抛错）。"""
    import duckdb

    try:
        return duckdb.connect(str(db_path))
    except Exception:
        return None


def _year_counts(con: Any, table: str) -> dict[int, int]:
    """按年份统计行数（date 实为 VARCHAR，cast 后 group by）。"""
    q = (
        f"SELECT year(date::DATE) AS y, count(*) AS c "
        f"FROM {table} GROUP BY 1 ORDER BY 1"
    )
    return {int(r[0]): int(r[1]) for r in con.execute(q).fetchall()}


def _archive_file(archive_root: Path, table: str, year: int) -> Path:
    return archive_root / f"history_{table}_{year}.parquet"


def dry_run(db_path: Path, table: str, until_year: int) -> dict[str, Any]:
    con = _connect(db_path)
    if con is None:
        raise RuntimeError(f"行情库被其他进程写锁占用（{db_path.name}），dry-run 无法连接")
    try:
        counts = _year_counts(con, table)
        archivable = {y: c for y, c in counts.items() if y <= until_year}
        return {
            "mode": "dry-run",
            "table": table,
            "total": sum(counts.values()),
            "until_year": until_year,
            "archivable_rows": sum(archivable.values()),
            "by_year": {str(y): c for y, c in sorted(counts.items())},
            "archivable_years": {str(y): c for y, c in sorted(archivable.items())},
        }
    finally:
        con.close()


def verify(db_path: Path, table: str, until_year: int, archive_root: Path) -> dict[str, Any]:
    """校验归档一致性：冷层 Parquet 文件行数 > 0 且热库已无 ≤ until_year 残留。"""
    con = _connect(db_path)
    if con is None:
        raise RuntimeError(f"行情库被其他进程写锁占用（{db_path.name}），verify 无法连接")
    try:
        cold_rows = 0
        present_years: list[int] = []
        for fp in sorted(archive_root.glob(f"history_{table}_*.parquet")):
            year = int(fp.stem.rsplit("_", 1)[-1])
            present_years.append(year)
            n = int(
                con.execute(f"SELECT count(*) FROM read_parquet('{str(fp)}')").fetchone()[0]
            )
            cold_rows += n
        hot_remaining = int(
            con.execute(
                f"SELECT count(*) FROM {table} WHERE year(date::DATE) <= {int(until_year)}"
            ).fetchone()[0]
        )
        consistent = cold_rows > 0 and hot_remaining == 0
        return {
            "mode": "verify",
            "table": table,
            "until_year": until_year,
            "cold_rows": cold_rows,
            "hot_remaining": hot_remaining,
            "consistent": consistent,
            "present_years": present_years,
        }
    finally:
        con.close()


def _min_year(con: Any, table: str) -> int:
    q = f"SELECT min(year(date::DATE)) FROM {table}"
    row = con.execute(q).fetchone()
    return int(row[0]) if row and row[0] is not None else 1970


def archive(
    db_path: Path, table: str, until_year: int, archive_root: Path
) -> dict[str, Any]:
    """执行归档：逐年份导出 Parquet + 从热库 DELETE（需无写锁，破坏性）。"""
    import duckdb

    con = duckdb.connect(str(db_path))
    archive_root.mkdir(parents=True, exist_ok=True)
    exported: dict[str, int] = {}
    try:
        for year in range(_min_year(con, table), until_year + 1):
            fp = _archive_file(archive_root, table, year)
            # 导出该年份到 Parquet（幂等：已存在则跳过）
            if fp.exists():
                continue
            q_export = (
                f"COPY (SELECT * FROM {table} WHERE year(date::DATE) = {year}) "
                f"TO '{str(fp)}' (FORMAT PARQUET)"
            )
            con.execute(q_export)
            n = int(
                con.execute(f"SELECT count(*) FROM read_parquet('{str(fp)}')").fetchone()[0]
            )
            exported[str(year)] = n
        # 从热库删除 ≤ until_year 的行（被删行数 = 本次导出行数）
        con.execute(f"DELETE FROM {table} WHERE year(date::DATE) <= {int(until_year)}")
        deleted = sum(exported.values())
        return {
            "mode": "archive",
            "table": table,
            "until_year": until_year,
            "exported_files": {str(y): c for y, c in exported.items()},
            "deleted_rows": int(deleted),
        }
    finally:
        con.close()


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="行情库冷热归档（P3-B）")
    parser.add_argument("--dry-run", action="store_true", help="只读统计，不写库")
    parser.add_argument("--verify-only", action="store_true", help="只读校验冷层一致性")
    parser.add_argument("--archive", action="store_true", help="执行归档（需无写锁）")
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"目标表（默认 {DEFAULT_TABLE}）")
    parser.add_argument("--until-year", type=int, default=DEFAULT_UNTIL_YEAR, help=f"归档截止年份（默认 {DEFAULT_UNTIL_YEAR}）")
    parser.add_argument("--db-path", default=str(DEFAULT_DB))
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    trace_id = f"fts.history_archive.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[%s] 模式: %s", trace_id, "archive" if args.archive else ("verify" if args.verify_only else "dry-run"))
    db_path = Path(args.db_path)
    archive_root = Path(args.archive_root)

    try:
        if args.archive:
            result = archive(db_path, args.table, args.until_year, archive_root)
        elif args.verify_only:
            result = verify(db_path, args.table, args.until_year, archive_root)
        else:
            result = dry_run(db_path, args.table, args.until_year)
            logger.info(
                "[%s] 归档预估: total=%d archivable=%d（≤%d）",
                trace_id, result["total"], result["archivable_rows"], args.until_year,
            )
    except RuntimeError as exc:
        logger.error("[%s] %s", trace_id, exc)
        if args.json:
            print(json.dumps({"trace_id": trace_id, "error": str(exc)}, ensure_ascii=False))
        return 1

    if args.json:
        print(json.dumps({"trace_id": trace_id, **result}, ensure_ascii=False, default=str))
    else:
        logger.info("[%s] 结果: %s", trace_id, json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(_run_cli())