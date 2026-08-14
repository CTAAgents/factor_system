"""
fts.data_futures_term_structure — 期货期限结构每日同步（Stage 3）

按字段消费字典（fts/config/futures_field_consumption.py）term_structure 组
每日同步（全 82 品种），落 Parquet 缓存:
  memory/cache/futures_term_structure/{symbol}.parquet（按 symbol 分文件，upsert）

数据流:
  sync_futures_data_job Stage 3 → sync_term_structure_fields
    → sync_contract_kline（AKShare 新浪具体合约日线刷新 contract_kline，失败降级用已有数据）
    → 最新交易日多合约截面 → term_spread / roll_yield 计算
    → Parquet upsert（date 去重）

期限结构定义（对最新交易日截面，按合约交割月份排序取最近两个合约）:
  - near_contract / far_contract : 近月 / 次近月合约代码
  - term_spread = (near_close - far_close) / near_close  （Back 结构 >0，Contango <0）
  - roll_yield  = term_spread / (月份间隔 / 12)          （年化展期收益近似）

失败完全透明: contract_kline 刷新失败不中断（降级用已有数据），
单品种无 ≥2 个活跃合约时不产出并记入摘要。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERM_STRUCTURE_CACHE_DIR = PROJECT_ROOT / "memory" / "cache" / "futures_term_structure"

TERM_STRUCTURE_COLUMNS: list[str] = [
    "term_spread",
    "roll_yield",
    "near_contract",
    "far_contract",
]

_CONTRACT_MONTH_RE = re.compile(r"(\d{2})(\d{2})$")


def _parse_contract_month(contract: str) -> Optional[tuple[int, int]]:
    """解析具体合约代码尾部 YYMM → (年, 月)。连续合约（RB0）返回 None。"""
    m = _CONTRACT_MONTH_RE.search(contract)
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if mm < 1 or mm > 12:
        return None
    return (2000 + yy, mm)


def _db_path() -> Path:
    return PROJECT_ROOT / "data" / "fts_history.duckdb"


def _compute_latest_section(symbol: str) -> Optional[pd.DataFrame]:
    """从 contract_kline 构建该品种最新多合约截面并计算期限结构。

    各合约最新日期可能不同步（逐合约拉取导致），因此按合约分别取最新 bar
    （ROW_NUMBER OVER PARTITION BY contract）构建截面，而非全局最新日期。

    Returns:
        单行 DataFrame（date, term_spread, roll_yield, near_contract, far_contract）；
        库/表/数据不足（活跃合约 <2）时返回 None。
    """
    path = _db_path()
    if not path.exists():
        return None
    try:
        import duckdb

        con = duckdb.connect(str(path), read_only=True)
        try:
            df = con.execute(
                """
                SELECT contract, date, close FROM (
                    SELECT contract, date, close,
                           ROW_NUMBER() OVER (PARTITION BY contract ORDER BY date DESC) AS rn
                    FROM contract_kline
                    WHERE symbol = ? AND close IS NOT NULL
                ) t WHERE rn = 1
                """,
                [symbol[:-1] if symbol.endswith("0") else symbol],
            ).df()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[ts_sync] %s 读取 contract_kline 失败: %s", symbol, e)
        return None

    if df is None or df.empty:
        return None

    rows: list[tuple[tuple[int, int], str, float, str]] = []
    for _, r in df.iterrows():
        ym = _parse_contract_month(str(r["contract"]))
        close = float(r["close"])
        if ym is not None and close > 0:
            rows.append((ym, str(r["contract"]), close, str(pd.Timestamp(r["date"]).date())))
    if len(rows) < 2:
        return None

    # 以该品种最新交易日为基准，优先取「未交割且交割最近」的两个合约构建期限结构；
    # 避免历史已交割旧合约（如 RB1905）进入截面。
    ref = pd.Timestamp(df["date"].max())
    ref_idx = ref.year * 12 + ref.month

    def _month_idx(ym: tuple[int, int]) -> int:
        return ym[0] * 12 + ym[1]

    future = [r for r in rows if _month_idx(r[0]) >= ref_idx]
    future.sort(key=lambda r: _month_idx(r[0]))
    if len(future) >= 2:
        selected = future[:2]
    else:
        # 回退：绝对距离最近的合约（数据不完整时的保守选择）
        rows_sorted = sorted(rows, key=lambda r: abs(_month_idx(r[0]) - ref_idx))
        selected = rows_sorted[:2]

    (near_ym, near_c, near_close, near_date), (far_ym, far_c, far_close, _) = selected
    months_gap = (far_ym[0] - near_ym[0]) * 12 + (far_ym[1] - near_ym[1])
    term_spread = (near_close - far_close) / near_close if near_close > 0 else float("nan")
    roll_yield = term_spread / (months_gap / 12.0) if months_gap > 0 else float("nan")

    return pd.DataFrame(
        [{
            "date": near_date,
            "term_spread": term_spread,
            "roll_yield": roll_yield,
            "near_contract": near_c,
            "far_contract": far_c,
        }]
    )


def _upsert_parquet(symbol: str, section: pd.DataFrame, trace_id: str) -> int:
    """按 (symbol) Parquet upsert：date 去重合并增量。"""
    TERM_STRUCTURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = TERM_STRUCTURE_CACHE_DIR / f"{symbol}.parquet"
    df = section.copy()
    df["trace_id"] = trace_id
    if path.exists():
        try:
            old = pd.read_parquet(path)
            df = pd.concat([old, df], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ts_sync] %s 旧缓存读取失败，将全量重写: %s", symbol, e)
    df = (
        df.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    df.to_parquet(path, index=False)
    return int(len(df))


def sync_term_structure_fields(
    symbols: list[str],
    days: int = 120,
    trace_id: str = "",
    refresh_contract_kline: bool = True,
) -> dict[str, Any]:
    """Stage 3 期限结构字段每日同步（字段消费字典 term_structure 组，全品种）。

    先刷新 contract_kline 具体合约截面数据（失败降级用已有数据），
    再逐品种计算期限结构落 Parquet。单品种失败不中断。

    Args:
        symbols: 连续合约代码列表。
        days: contract_kline 刷新回溯天数。
        trace_id: HARNESS trace_id。
        refresh_contract_kline: 是否先刷新 contract_kline（True=在线拉取，失败降级）。

    Returns:
        {"success", "failure", "rows", "failures", "no_section"}
    """
    if refresh_contract_kline:
        try:
            from fts.data_futures import sync_contract_kline

            result = sync_contract_kline(symbols=symbols, days=days, trace_id=trace_id)
            logger.info(
                "[ts_sync] contract_kline 刷新: written=%d failed=%d (trace_id=%s)",
                result.get("written", 0), result.get("failed", 0), trace_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[ts_sync] contract_kline 刷新失败，降级用已有数据: %s (trace_id=%s)",
                e, trace_id,
            )

    success = 0
    failure = 0
    rows = 0
    failures: list[dict] = []
    no_section: list[str] = []

    for sym in symbols:
        try:
            section = _compute_latest_section(sym)
            if section is None:
                no_section.append(sym)
                continue
            _upsert_parquet(sym, section, trace_id)
            success += 1
            rows += int(len(section))
        except Exception as e:  # noqa: BLE001
            failure += 1
            failures.append({"symbol": sym, "error": str(e)})
            logger.warning("[ts_sync] %s 期限结构同步失败: %s (trace_id=%s)", sym, e, trace_id)

    logger.info(
        "[ts_sync] 期限结构同步完成: success=%d failure=%d rows=%d no_section=%d (trace_id=%s)",
        success, failure, rows, len(no_section), trace_id,
    )
    return {
        "success": success,
        "failure": failure,
        "rows": rows,
        "failures": failures,
        "no_section": no_section,
    }


__all__ = [
    "sync_term_structure_fields",
    "TERM_STRUCTURE_CACHE_DIR",
    "TERM_STRUCTURE_COLUMNS",
    "_parse_contract_month",
    "_compute_latest_section",
]
