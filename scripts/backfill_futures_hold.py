"""scripts.backfill_futures_hold — 用 AKShare 真实持仓/结算数据回填 kline_cache。

背景（GAP-083）:
    期货持仓量 hold / 结算价 settle 在主路径 TDX_LOCAL(17709) 恒为 NA，
    kline_cache 中 TQ 15 年同步写入的 hold/settle 为 0.0 占位、AKShare 老数据为 NULL。
    AKShare futures_zh_daily_sina 返回真实 hold/settle（连续合约），本脚本按日期
    UPDATE 回填 kline_cache（双格式 symbol：RB 与 RB0），使 _from_kline_cache 的
    "真实优先"读路径真正拿到真实数据，未回填日仍走代理兜底。

用法:
    python scripts/backfill_futures_hold.py                       # 全品种 82 个
    python scripts/backfill_futures_hold.py --symbols RB0 CU0     # 指定品种
    python scripts/backfill_futures_hold.py --universe core       # 核心 25 品种
    python scripts/backfill_futures_hold.py --dry-run             # 只统计不写库
    python scripts/backfill_futures_hold.py --json                # JSON 输出

HARNESS §5.5 trace_id 全链路: 单次执行生成唯一 trace_id 贯穿始终。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd

logger = logging.getLogger("backfill_futures_hold")

DB_PATH = ROOT / "data" / "fts_history.duckdb"


def _safe_float(val: Any) -> float:
    """安全转换为 float，NaN/None/Inf → 0.0。"""
    if val is None:
        return 0.0
    try:
        f = float(val)
        if pd.isna(f) or f in (float("inf"), float("-inf")):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


def resolve_symbols(cli_symbols: Optional[list[str]], universe: str) -> list[str]:
    """解析 CLI 参数或 universe 为品种代码列表。"""
    if cli_symbols:
        flat: list[str] = []
        for s in cli_symbols:
            flat.extend(item for item in s.split(",") if item)
        return flat

    from fts.data_futures import (
        FUTURES_CORE_SUBSET,
        FUTURES_SUBSET,
    )

    if universe == "core":
        return list(FUTURES_CORE_SUBSET)
    return list(FUTURES_SUBSET)


def fetch_hold_settle_from_akshare(symbol: str) -> Optional[pd.DataFrame]:
    """拉取 AKShare futures_zh_daily_sina 连续合约，返回 date 索引 hold/settle 两列。

    接口失败/空数据返回 None（调用方跳过，不阻断主流程）。
    """
    import akshare as ak  # type: ignore[import-untyped]

    sym = symbol.strip().upper()
    if not sym.endswith("0"):
        sym = f"{sym}0"
    df = ak.futures_zh_daily_sina(symbol=sym)
    if df is None or df.empty:
        return None
    if "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"])
    out = pd.DataFrame(index=df["date"])
    for col in ("hold", "settle"):
        if col in df.columns:
            # .values 赋值避免 RangeIndex 与 datetime index 对齐错位 → NaN
            out[col] = df[col].map(_safe_float).to_numpy()
        else:
            out[col] = 0.0
    return out


def _open_db():
    """打开可写 DuckDB 连接（失败返回 None 由调用方降级）。"""
    try:
        return duckdb.connect(str(DB_PATH))
    except Exception as e:  # noqa: BLE001
        logger.error("DuckDB 打开失败 [%s]: %s", DB_PATH, e)
        return None


def write_backfill(
    symbol: str,
    df: pd.DataFrame,
    dry_run: bool = False,
    conn: Any = None,
    trace_id: str = "",
) -> int:
    """按日期 UPDATE kline_cache 的 hold/settle（双格式 RB 与 RB0，幂等）。

    仅更新 hold>0 或 settle>0 的行——无效值（0/NULL）跳过，避免覆盖真实数据。
    返回应更新行数（dry_run 不实际执行 SQL）。
    """
    raw = symbol.strip().upper()
    sym_base = raw[:-1] if raw.endswith("0") else raw
    targets = [sym_base, f"{sym_base}0"]

    n = 0
    for symbol_key in targets:
        for date, row in df.iterrows():
            hold = _safe_float(row.get("hold"))
            settle = _safe_float(row.get("settle"))
            if hold <= 0 and settle <= 0:
                continue
            n += 1
            if dry_run:
                continue
            try:
                conn.execute(
                    "UPDATE kline_cache SET hold = ?, settle = ? "
                    "WHERE symbol = ? AND period = 'daily' AND date = ?",
                    [hold, settle, symbol_key, str(date.date())],
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] UPDATE 失败 [%s/%s]: %s", trace_id, symbol_key, date, e)
    return n


def backfill_hold_settle(
    symbols: list[str],
    dry_run: bool = False,
    delay: float = 0.5,
    trace_id: str = "",
) -> dict:
    """对每个品种回填真实 hold/settle，输出统计。"""
    conn = None if dry_run else _open_db()
    results: dict = {"symbols": len(symbols), "updated": 0, "skipped": 0, "failed": []}
    for sym in symbols:
        try:
            df = fetch_hold_settle_from_akshare(sym)
            if df is None or df.empty:
                results["skipped"] += 1
                logger.info("[%s] %s: AKShare 无数据，跳过", trace_id, sym)
                continue
            n = write_backfill(sym, df, dry_run=dry_run, conn=conn, trace_id=trace_id)
            results["updated"] += n
            logger.info("[%s] %s: 回填 %d 行", trace_id, sym, n)
            time.sleep(delay)
        except Exception as e:  # noqa: BLE001
            results["failed"].append(f"{sym}: {e}")
            logger.warning("[%s] %s 回填失败: %s", trace_id, sym, e)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return results


def derive_pre_settle(
    symbols: list[str],
    dry_run: bool = False,
    conn: Any = None,
    trace_id: str = "",
) -> dict:
    """按财务定义回写 kline_cache.pre_settle = 前一交易日 settle（幂等）。

    背景（GAP-083 方案 C 落地）：settle 已由 AKShare 回填（86.6% 覆盖），
    pre_settle（昨结算）按财务定义 = 前一交易日结算价，由库内 settle 序列
    升序 shift(1) 派生，零外部依赖。仅更新 pre_settle 无效行（NULL/≤0）。
    """
    results: dict = {"symbols": len(symbols), "updated": 0, "skipped": 0, "failed": []}
    for sym in symbols:
        try:
            n = _write_pre_settle_derivation(sym, dry_run=dry_run, conn=conn, trace_id=trace_id)
            results["updated"] += n
            logger.info("[%s] %s: 派生 pre_settle %d 行", trace_id, sym, n)
        except Exception as e:  # noqa: BLE001
            results["failed"].append(f"{sym}: {e}")
            logger.warning("[%s] %s 派生失败: %s", trace_id, sym, e)
    return results


def _write_pre_settle_derivation(
    symbol: str,
    dry_run: bool,
    conn: Any,
    trace_id: str,
) -> int:
    """按日期升序派生 pre_settle[t] = 最近有效 settle[t-1]，双格式 RB/RB0。

    返回应更新行数（dry_run 不实际执行 SQL）。
    """
    raw = symbol.strip().upper()
    sym_base = raw[:-1] if raw.endswith("0") else raw
    targets = [sym_base, f"{sym_base}0"]

    n = 0
    for symbol_key in targets:
        try:
            rows = conn.execute(
                "SELECT date, settle, pre_settle FROM kline_cache "
                "WHERE symbol = ? AND period = 'daily' ORDER BY date ASC",
                [symbol_key],
            ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 查询失败 [%s]: %s", trace_id, symbol_key, e)
            continue
        prev_settle = 0.0
        for date_str, settle, pre in rows:
            # pre_settle 无效行才派生（不覆盖增强层权威值）
            if pre is None or _safe_float(pre) <= 0:
                if prev_settle > 0:
                    n += 1
                    if not dry_run:
                        try:
                            conn.execute(
                                "UPDATE kline_cache SET pre_settle = ? "
                                "WHERE symbol = ? AND period = 'daily' AND date = ?",
                                [prev_settle, symbol_key, str(date_str)],
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("[%s] UPDATE 失败 [%s/%s]: %s", trace_id, symbol_key, date_str, e)
            # 推进最近有效 settle（无效时保持）
            cur_settle = _safe_float(settle)
            if cur_settle > 0:
                prev_settle = cur_settle
    return n


def main() -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="AKShare 回填期货 hold/settle 到 kline_cache")
    parser.add_argument("--symbols", nargs="+", default=None, help="指定品种（如 RB0 CU0）")
    parser.add_argument("--universe", default="all", choices=["all", "core"], help="品种范围")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument("--derive-presettle", action="store_true", help="仅派生 pre_settle=前日 settle（不拉 AKShare，幂等）")
    parser.add_argument("--delay", type=float, default=0.5, help="AKShare 调用间隔（秒）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    trace_id = f"fts.backfill_hold.{uuid.uuid4().hex[:8]}"
    symbols = resolve_symbols(args.symbols, args.universe)

    if args.derive_presettle:
        # 派生模式：pre_settle = 前日 settle（零外部依赖，不拉 AKShare）
        logger.info("[%s] 开始派生 pre_settle（%d 品种, dry_run=%s）", trace_id, len(symbols), args.dry_run)
        # dry_run 同样打开连接：派生需读库统计行数，仅 UPDATE 受 dry_run 保护
        conn = _open_db()
        try:
            results = derive_pre_settle(symbols, dry_run=args.dry_run, conn=conn, trace_id=trace_id)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        results["trace_id"] = trace_id
        results["dry_run"] = args.dry_run
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(
                f"派生完成: {results['updated']} 行更新 / {results['skipped']} 品种跳过 / "
                f"{len(results['failed'])} 品种失败"
            )
            for f in results["failed"]:
                print(f"  ✗ {f}")
        return 0 if not results["failed"] else 1

    logger.info("[%s] 开始回填 hold/settle（%d 品种, dry_run=%s）", trace_id, len(symbols), args.dry_run)

    results = backfill_hold_settle(symbols, dry_run=args.dry_run, delay=args.delay, trace_id=trace_id)
    results["trace_id"] = trace_id
    results["dry_run"] = args.dry_run

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(
            f"回填完成: {results['updated']} 行更新 / {results['skipped']} 品种跳过 / "
            f"{len(results['failed'])} 品种失败"
        )
        for f in results["failed"]:
            print(f"  ✗ {f}")

    return 0 if not results["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
