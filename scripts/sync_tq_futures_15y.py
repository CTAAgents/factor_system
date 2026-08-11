"""scripts.sync_tq_futures_15y — 通过通达信 TQ 数据源同步 15 年期货日线数据到 DuckDB。

背景:
    现有 sync_futures_data_job（Phase 14.5）仅同步 120 天数据，用于日常增量更新。
    本脚本通过 TdxLocalSource（通达信 HTTP 17709）直接拉取 15 年日线，
    全量写入 kline_cache 表（每品种 DELETE + INSERT，幂等），
    满足 15 年历史回测和因子分析对长周期数据的需求。

用法:
    python scripts/sync_tq_futures_15y.py                          # 全品种 82 个
    python scripts/sync_tq_futures_15y.py --symbol RB0 CU0         # 指定品种
    python scripts/sync_tq_futures_15y.py --universe core          # 核心 25 品种
    python scripts/sync_tq_futures_15y.py --universe stratified    # 分层训练集
    python scripts/sync_tq_futures_15y.py --days 3650              # 自定义回溯（天）
    python scripts/sync_tq_futures_15y.py --json                   # JSON 输出
    python scripts/sync_tq_futures_15y.py -v                       # 详细日志

HARNESS §5.5 trace_id 全链路: 单次执行生成唯一 trace_id 贯穿始终。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

logger = logging.getLogger("sync_tq_15y")

# 15 年 ≈ 5500 个交易日（每年约 242-252 个交易日）
DEFAULT_DAYS = 5500

# kline_cache 写入列（与 migrate.py KLINE_CACHE_CREATE_DDL 对齐，不含 adj_factor）
KLINE_INSERT_COLUMNS = [
    "symbol", "period", "date", "open", "high", "low", "close",
    "volume", "amount", "hold", "settle", "pre_settle", "oi_change", "vwap",
    "source", "fetched_at", "trace_id",
]

KLINE_INSERT_SQL = f"""INSERT INTO kline_cache (
    {", ".join(KLINE_INSERT_COLUMNS)}
) VALUES ({", ".join("?" * len(KLINE_INSERT_COLUMNS))})"""


def _safe_float(val: Any) -> float:
    """安全转换为 float，NaN/None/NA → 0.0。"""
    if val is None:
        return 0.0
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


def _resolve_symbols(cli_symbols: Optional[list[str]], universe: str) -> list[str]:
    """解析 CLI 参数或 universe 为品种代码列表。"""
    if cli_symbols:
        flat: list[str] = []
        for s in cli_symbols:
            flat.extend(item for item in s.split(",") if item)
        return flat

    from fts.data_futures import (
        FUTURES_CORE_SUBSET,
        FUTURES_HOLDOUT,
        FUTURES_STRATIFIED_SUBSET,
        FUTURES_SUBSET,
    )

    pool: dict[str, list[str]] = {
        "core": FUTURES_CORE_SUBSET,
        "stratified": FUTURES_STRATIFIED_SUBSET,
        "holdout": list(FUTURES_HOLDOUT),
        "all": FUTURES_SUBSET,
    }
    return list(pool.get(universe, FUTURES_SUBSET))


def _df_to_kline_rows(df: pd.DataFrame, symbol: str, trace_id: str) -> list[tuple]:
    """将 TdxLocalSource.fetch_ohlcv 返回的 DataFrame 转为 kline_cache 行元组列表。

    TdxLocalSource._process_daily 返回 17 列（不含 adj_factor）:
        symbol, period, date, open, high, low, close, volume, amount,
        hold, settle, pre_settle, oi_change, vwap, source, fetched_at, trace_id
    """
    now_iso = datetime.now().isoformat()
    rows: list[tuple] = []

    for _, r in df.iterrows():
        date_val = r.get("date")
        if date_val is None:
            continue
        # date 可能为 datetime.date 或 Timestamp
        if hasattr(date_val, "strftime"):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val)

        rows.append((
            symbol,
            "daily",
            date_str,
            _safe_float(r.get("open")),
            _safe_float(r.get("high")),
            _safe_float(r.get("low")),
            _safe_float(r.get("close")),
            _safe_float(r.get("volume")),
            _safe_float(r.get("amount")),
            _safe_float(r.get("hold")),
            _safe_float(r.get("settle")),
            _safe_float(r.get("pre_settle")),
            _safe_float(r.get("oi_change")),
            _safe_float(r.get("vwap")),
            "TDX_LOCAL",
            now_iso,
            trace_id,
        ))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过通达信 TQ 数据源同步 15 年期货日线数据到 DuckDB",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="指定品种（可多次或逗号分隔），默认全品种",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"回溯天数（默认 {DEFAULT_DAYS} ≈ 15 年）",
    )
    parser.add_argument(
        "--universe",
        choices=["core", "stratified", "holdout", "all"],
        default="all",
        help="品种池（默认 all 全品种 82 个）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── 1. 解析品种列表 ──
    symbols = _resolve_symbols(args.symbol, args.universe)
    if not symbols:
        logger.error("无品种可同步，退出")
        return 1
    logger.info("品种列表: %d 个", len(symbols))

    # ── 2. 检查通达信 TQ 服务 ──
    logger.info("检查通达信 TQ 服务 (127.0.0.1:17709) ...")
    from fts.data_sources.tdx_local_source import TdxLocalSource

    source = TdxLocalSource(period="day")
    if not source.is_available():
        logger.error("通达信 TQ 服务不可达，请确认已启动通达信量化模拟客户端")
        return 1
    logger.info("TQ 服务可达 ✓")

    # ── 3. 初始化 DuckDB 写入器 ──
    from fts.data_futures import _get_writer, _DUCKDB_PATH

    logger.info("DuckDB 路径: %s", _DUCKDB_PATH)
    if not _DUCKDB_PATH.exists():
        logger.error("DuckDB 文件不存在: %s", _DUCKDB_PATH)
        return 1

    # 尝试 schema 迁移（文件可能被其他进程锁定，跳过不阻塞）
    try:
        from fts.data_sources.migrate import migrate_schema
        migrate_schema(str(_DUCKDB_PATH))
    except Exception as e:
        logger.info("migrate_schema 跳过（表结构应已存在）: %s", e)

    # 获取写入器（如 FTS 进程已打开连接，复用单写者）
    writer = _get_writer()
    logger.info("DuckDB 写入器就绪 ✓")

    # ── 4. 逐品种同步 ──
    trace_id = f"fts.tq15y.sync_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("=" * 70)
    logger.info("  TQ 15 年期货日线数据同步")
    logger.info("  symbols=%d  days=%d  trace_id=%s", len(symbols), args.days, trace_id)
    logger.info("=" * 70)

    started_at = time.time()
    results: dict[str, Any] = {"success": [], "failure": [], "total_rows": 0}

    for idx, sym in enumerate(symbols, 1):
        try:
            logger.info("[%d/%d] 正在同步 %s ...", idx, len(symbols), sym)

            # 4a. 从 TQ 拉取数据
            df = source.fetch_ohlcv(sym, days=args.days, trace_id=trace_id)
            if df is None or df.empty:
                logger.warning("[%s] TQ 返回空数据，跳过", sym)
                results["failure"].append({"symbol": sym, "error": "empty data"})
                continue

            # 4b. 转为 kline_cache 行格式
            rows = _df_to_kline_rows(df, sym, trace_id)
            if not rows:
                logger.warning("[%s] 转换后无有效行，跳过", sym)
                results["failure"].append({"symbol": sym, "error": "no valid rows"})
                continue

            # 4c. 全量重写（幂等）：DELETE 旧数据 + INSERT 新数据
            writer.execute("DELETE FROM kline_cache WHERE symbol = ?", [sym])
            writer.executemany(KLINE_INSERT_SQL, rows)

            results["success"].append({"symbol": sym, "rows": len(rows)})
            results["total_rows"] += len(rows)
            logger.info(
                "[%d/%d] %s 完成: %d 行写入 (%.1f 年)",
                idx, len(symbols), sym, len(rows),
                len(rows) / 252,
            )

        except Exception as e:
            logger.error("[%s] 同步失败: %s", sym, e, exc_info=True)
            results["failure"].append({"symbol": sym, "error": str(e)})

    # ── 5. 输出摘要 ──
    elapsed = time.time() - started_at
    summary = {
        "trace_id": trace_id,
        "started_at": datetime.fromtimestamp(started_at).isoformat(),
        "finished_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "symbols_total": len(symbols),
        "success": len(results["success"]),
        "failure": len(results["failure"]),
        "total_rows": results["total_rows"],
        "source": "TDX_LOCAL",
        "days": args.days,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print()
        print("─" * 70)
        print("  同步结果摘要")
        print("─" * 70)
        print(f"  trace_id         : {summary['trace_id']}")
        print(f"  symbols_total    : {summary['symbols_total']}")
        print(f"  success          : {summary['success']}")
        print(f"  failure          : {summary['failure']}")
        print(f"  total_rows       : {summary['total_rows']}")
        print(f"  elapsed_seconds  : {summary['elapsed_seconds']}")
        print(f"  days             : {summary['days']}")

    if results["failure"]:
        print()
        print("  失败品种:")
        for f in results["failure"]:
            print(f"    - {f['symbol']}: {f['error']}")

    return 0 if summary["failure"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())