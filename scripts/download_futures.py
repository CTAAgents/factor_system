"""
scripts/download_futures.py — 期货连续合约数据下载/刷新脚本

从 AKShare futures_zh_daily_sina API 下载期货连续合约日线数据，
写入 DuckDB kline_cache 表（与 FTS 现有数据表兼容）。

支持断点续传 — 已下载的品种自动跳过。

用法:
    python scripts/download_futures.py                   # 下载全部 82 个品种
    python scripts/download_futures.py --subset          # 仅下载核心 25 个品种
    python scripts/download_futures.py --force           # 强制刷新所有品种
    python scripts/download_futures.py --subset --force  # 强制刷新核心品种

DuckDB 路径: data/fts_history.duckdb
kline_cache 表结构: symbol, period, open, high, low, close, volume, amount, date
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_DUCKDB_PATH = PROJECT_ROOT / "data" / "fts_history.duckdb"


def _get_db():
    """获取 DuckDB 连接。"""
    import duckdb
    return duckdb.connect(str(_DUCKDB_PATH))


def _ensure_table(db) -> None:
    """确保 kline_cache 表存在且有 period 列。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS kline_cache (
            symbol VARCHAR,
            period VARCHAR,
            date VARCHAR,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE
        )
    """)


def _has_data(db, symbol: str) -> bool:
    """检查某个品种是否已有数据。"""
    result = db.execute(
        "SELECT COUNT(*) FROM kline_cache WHERE symbol = ? AND period = 'daily'",
        [symbol],
    )
    return result.fetchone()[0] > 0


def _download_symbol(symbol: str, db) -> int:
    """下载单个期货品种的连续合约数据到 kline_cache。

    Args:
        symbol: 品种代码（如 "RB0"）
        db: DuckDB 连接

    Returns:
        插入的行数
    """
    import akshare as ak  # type: ignore[import-untyped]

    # 标准化: 去掉末尾的 "0" 用于 kline_cache 存储
    raw = symbol.strip().upper()
    sym = raw[:-1] if raw.endswith("0") else raw

    try:
        df = ak.futures_zh_daily_sina(symbol=symbol)
    except Exception as e:
        logger.warning("  [SKIP] %s: AKShare 获取失败 — %s", symbol, e)
        return 0

    if df is None or df.empty:
        logger.warning("  [SKIP] %s: 返回空数据", symbol)
        return 0

    # 确保必要列存在
    required = ["date", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            logger.warning("  [SKIP] %s: 缺少必要列 %s", symbol, col)
            return 0

    # 准备插入数据
    rows = []
    for _, row in df.iterrows():
        date_str = str(row["date"]) if hasattr(row["date"], "strftime") else str(row["date"])
        rows.append((
            sym,
            "daily",
            date_str,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
            float(row.get("amount", 0.0)),
        ))

    # 批量插入（先删后插 = 刷新）
    db.execute("DELETE FROM kline_cache WHERE symbol = ? AND period = 'daily'", [sym])
    db.executemany(
        "INSERT INTO kline_cache (symbol, period, date, open, high, low, close, volume, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    logger.info("  [OK] %s: %d 行", symbol, len(rows))
    return len(rows)


def _get_symbols(subset_only: bool) -> list[str]:
    """获取待下载的品种列表。"""
    from fts.data_futures import FUTURES_SUBSET, FUTURES_CORE_SUBSET
    if subset_only:
        logger.info("使用核心子集 (%d 个品种)", len(FUTURES_CORE_SUBSET))
        return FUTURES_CORE_SUBSET
    logger.info("使用完整列表 (%d 个品种)", len(FUTURES_SUBSET))
    return FUTURES_SUBSET


def main() -> int:
    """主入口。"""
    import argparse
    parser = argparse.ArgumentParser(description="下载期货连续合约数据")
    parser.add_argument("--subset", action="store_true", help="仅下载核心品种")
    parser.add_argument("--force", action="store_true", help="强制刷新（忽略已有数据）")
    args = parser.parse_args()

    symbols = _get_symbols(args.subset)
    db = _get_db()
    _ensure_table(db)

    total_inserted = 0
    skipped = 0
    failed = 0
    start = time.time()

    for i, sym in enumerate(symbols, 1):
        # 检查是否已有数据（断点续传）
        if not args.force and _has_data(db, sym):
            logger.info("[%2d/%d] %s: 已存在，跳过", i, len(symbols), sym)
            skipped += 1
            continue

        logger.info("[%2d/%d] %s: 下载中...", i, len(symbols), sym)
        inserted = _download_symbol(sym, db)
        if inserted > 0:
            total_inserted += inserted
        else:
            failed += 1

        # AKShare 调用间隔，避免被封
        if i < len(symbols):
            time.sleep(0.5)

    elapsed = time.time() - start
    logger.info("=" * 50)
    logger.info("下载完成: 总品种=%d, 新增=%d, 跳过=%d, 失败=%d, 耗时=%.1fs",
                len(symbols), total_inserted, skipped, failed, elapsed)

    db.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())