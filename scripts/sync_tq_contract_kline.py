"""scripts.sync_tq_contract_kline — 通过通达信 TQ 主数据源同步所有品种全合约日线数据到 contract_kline。

数据流:
    TQ (主源) → 当前活跃合约 → contract_kline
    AKShare (降级) → 历史合约 → contract_kline

背景:
    - 现有 contract_kline 表: 59 品种, 4753 合约, 866,716 行（全部来自 AKShare）
    - 缺失: hold, settle, source, fetched_at, trace_id 列（旧表结构）
    - 本脚本: 迁移 schema + TQ 主源同步 + AKShare 降级 + 全品种覆盖

用法:
    python scripts/sync_tq_contract_kline.py                          # 全品种 82 个
    python scripts/sync_tq_contract_kline.py --symbol RB0 CU0         # 指定品种
    python scripts/sync_tq_contract_kline.py --universe core          # 核心 25 品种
    python scripts/sync_tq_contract_kline.py --years 15               # 15 年
    python scripts/sync_tq_contract_kline.py --dry-run                # 试运行
    python scripts/sync_tq_contract_kline.py --json                   # JSON 输出
    python scripts/sync_tq_contract_kline.py -v                       # 详细日志
    python scripts/sync_tq_contract_kline.py --incremental            # 增量模式（跳过已有合约）

HARNESS §5.5 trace_id 全链路: 单次执行生成唯一 trace_id。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

logger = logging.getLogger("sync_contract_kline")

# ─── 常量 ────────────────────────────────────────────────────────────────

# contract_kline 写入列（与 migrate.py CONTRACT_KLINE_CREATE_DDL 对齐）
CONTRACT_KLINE_COLUMNS = [
    "symbol", "contract", "period", "date", "open", "high", "low", "close",
    "volume", "amount", "hold", "settle", "source", "fetched_at", "trace_id",
]

# contract_kline 缺失列（旧表需补充）
CONTRACT_KLINE_MISSING_COLUMNS: list[tuple[str, str]] = [
    ("hold", "DOUBLE"),
    ("settle", "DOUBLE"),
    ("source", "VARCHAR"),
    ("fetched_at", "TIMESTAMP"),
    ("trace_id", "VARCHAR"),
]

# 交易所后缀映射（用于 TQ 查询）
# FTS 品种基名 → 交易所后缀
EXCHANGE_MAP: dict[str, str] = {
    # 大商所 (dce) — 22 个
    "V": "DCE", "P": "DCE", "B": "DCE", "M": "DCE", "I": "DCE",
    "JD": "DCE", "L": "DCE", "PP": "DCE", "FB": "DCE", "Y": "DCE",
    "C": "DCE", "A": "DCE", "J": "DCE", "JM": "DCE", "CS": "DCE",
    "EG": "DCE", "RR": "DCE", "EB": "DCE", "PG": "DCE", "LH": "DCE",
    "LG": "DCE", "BZ": "DCE",
    # 郑商所 (czce) — 25 个
    "TA": "CZC", "OI": "CZC", "RS": "CZC", "RM": "CZC", "WH": "CZC",
    "JR": "CZC", "SR": "CZC", "CF": "CZC", "RI": "CZC", "MA": "CZC",
    "FG": "CZC", "LR": "CZC", "SF": "CZC", "SM": "CZC", "CY": "CZC",
    "AP": "CZC", "CJ": "CZC", "UR": "CZC", "SA": "CZC", "PF": "CZC",
    "PK": "CZC", "SH": "CZC", "PX": "CZC", "PR": "CZC", "PL": "CZC",
    # 上期所 (shfe) — 19 个
    "FU": "SHF", "AL": "SHF", "RU": "SHF", "ZN": "SHF", "CU": "SHF",
    "AU": "SHF", "RB": "SHF", "PB": "SHF", "AG": "SHF", "BU": "SHF",
    "HC": "SHF", "SN": "SHF", "NI": "SHF", "SP": "SHF", "SS": "SHF",
    "AO": "SHF", "BR": "SHF", "AD": "SHF", "OP": "SHF",
    # 能源中心 (ine) — 5 个
    "SC": "INE", "NR": "INE", "LU": "INE", "BC": "INE", "EC": "INE",
    # 中金所 (cffex) — 6 个
    "IF": "CFF", "TF": "CFF", "IH": "CFF", "IC": "CFF", "TS": "CFF",
    "IM": "CFF",
    # 广期所 (gfex) — 5 个
    "SI": "GFE", "LC": "GFE", "PS": "GFE", "PT": "GFE", "PD": "GFE",
}

# 中金所品种（主力连续用 L0，而非商品期货的 L8）
_CFFEX_PRODUCTS = ("IF", "IH", "IC", "IM", "TF", "TS", "TL", "T")

# 通达信 TQ-Local HTTP 服务地址
TDX_RPC_URL = "http://127.0.0.1:17709/"

# 默认回溯年数
DEFAULT_YEARS = 15

# TQ 批量查询大小
TQ_BATCH_SIZE = 20

# TQ 超时（秒）
TQ_TIMEOUT = 15.0


# ─── 工具函数 ────────────────────────────────────────────────────────────

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


def _get_base_symbol(symbol: str) -> str:
    """去掉 '0' 后缀得到品种基名。"""
    return symbol[:-1] if symbol.endswith("0") else symbol


def _get_exchange_suffix(base: str) -> str:
    """获取品种的交易所后缀。"""
    return EXCHANGE_MAP.get(base, "SHF")


def _contract_to_tdx(contract: str, exchange: str) -> str:
    """AKShare 合约代码 → 通达信代码（如 'RB2610' → 'RB2610.SHF'）。"""
    return f"{contract}.{exchange}"


def _generate_contract_codes(
    base: str,
    years: int = 15,
    current_year: int | None = None,
) -> list[str]:
    """生成品种的所有可能合约代码（AKShare 格式，无交易所后缀）。

    根据品种的已知交割月份模式生成合约代码。
    对于未知模式的品种，生成所有 12 个月份。
    """
    if current_year is None:
        current_year = datetime.now().year

    # 已知交割月份模式
    # 格式: {品种基名: [月份列表]}
    # None = 所有 12 个月
    delivery_months: dict[str, list[int] | None] = {
        # 大商所
        "A": [1, 3, 5, 7, 9, 11],       # 豆一
        "B": [1, 3, 5, 7, 9, 11],       # 豆二
        "C": [1, 3, 5, 7, 9, 11],       # 玉米
        "CS": [1, 3, 5, 7, 9, 11],      # 玉米淀粉
        "M": [1, 3, 5, 7, 8, 9, 11, 12],  # 豆粕
        "Y": [1, 3, 5, 7, 8, 9, 11, 12],  # 豆油
        "P": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],  # 棕榈油
        "I": None,                        # 铁矿石
        "J": None,                        # 焦炭
        "JM": None,                       # 焦煤
        "L": None,                        # 聚乙烯
        "V": None,                        # 聚氯乙烯
        "PP": None,                       # 聚丙烯
        "JD": None,                       # 鸡蛋
        "EG": None,                       # 乙二醇
        "EB": None,                       # 苯乙烯
        "PG": None,                       # 液化气
        "LH": None,                       # 生猪
        "RR": None,                       # 粳米
        "FB": None,                       # 纤维板
        "LG": None,                       # 原木
        "BZ": None,                       # 苯
        # 郑商所
        "TA": None,                       # PTA
        "MA": None,                       # 甲醇
        "SR": [1, 3, 5, 7, 9, 11],       # 白糖
        "CF": [1, 3, 5, 7, 9, 11],       # 棉花
        "OI": [1, 3, 5, 7, 9, 11],       # 菜油
        "RM": [1, 3, 5, 7, 8, 9, 11, 12],  # 菜粕
        "RS": None,                       # 菜籽
        "FG": None,                       # 玻璃
        "SA": None,                       # 纯碱
        "UR": None,                       # 尿素
        "SF": None,                       # 硅铁
        "SM": None,                       # 锰硅
        "AP": [1, 3, 5, 7, 9, 11, 12],   # 苹果
        "CJ": [1, 3, 5, 7, 9, 11, 12],   # 红枣
        "PF": None,                       # 短纤
        "PK": None,                       # 花生
        "PX": None,                       # 对二甲苯
        "SH": None,                       # 烧碱
        "PR": None,                       # 瓶片
        "PL": None,                       # 丙烯
        "CY": None,                       # 棉纱
        "RI": None,                       # 早籼稻
        "JR": None,                       # 粳稻
        "LR": None,                       # 晚籼稻
        "WH": None,                       # 强麦
        # 上期所
        "RB": [1, 5, 10],                 # 螺纹钢
        "HC": [1, 5, 10],                 # 热卷
        "CU": None,                       # 铜
        "AL": None,                       # 铝
        "ZN": None,                       # 锌
        "PB": None,                       # 铅
        "NI": None,                       # 镍
        "SN": None,                       # 锡
        "AU": None,                       # 黄金
        "AG": None,                       # 白银
        "RU": None,                       # 橡胶
        "FU": None,                       # 燃料油
        "BU": None,                       # 沥青
        "SP": None,                       # 纸浆
        "SS": None,                       # 不锈钢
        "AO": None,                       # 氧化铝
        "BR": None,                       # 丁二烯橡胶
        "AD": None,                       # 铝衍生
        "OP": None,                       # 双胶纸
        # 能源中心
        "SC": None,                       # 原油
        "NR": None,                       # 20号胶
        "LU": None,                       # 低硫燃料油
        "BC": None,                       # 铜(BC)
        "EC": None,                       # 集运欧线
        # 中金所
        # IF/IC/IH/IM: 当月, 次月, 下季, 隔季（特殊处理）
        # T/TF/TS: 3, 6, 9, 12
        "IF": None,  # 沪深300 - 特殊处理
        "IC": None,  # 中证500 - 特殊处理
        "IH": None,  # 上证50 - 特殊处理
        "IM": None,  # 中证1000 - 特殊处理
        "TF": None,  # 5年国债 - 特殊处理
        "T": None,   # 10年国债 - 特殊处理
        "TS": [3, 6, 9, 12],  # 2年国债
        # 广期所
        "SI": None,                       # 工业硅
        "LC": None,                       # 碳酸锂
        "PS": None,                       # 铂
        "PT": None,                       # 钯
        "PD": None,                       # 钯
    }

    months = delivery_months.get(base, None)
    start_year = current_year - years

    codes: list[str] = []
    for year in range(start_year, current_year + 1):
        year_short = year % 100
        if months is None:
            for m in range(1, 13):
                codes.append(f"{base}{year_short:02d}{m:02d}")
        else:
            for m in months:
                codes.append(f"{base}{year_short:02d}{m:02d}")

    return codes


# ─── Schema 迁移 ────────────────────────────────────────────────────────

def _migrate_contract_kline_schema(con: Any) -> int:
    """向 contract_kline 表添加缺失列，返回新增列数。"""
    import duckdb

    # 检查表是否存在
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='contract_kline'"
    ).fetchone()
    if not exists or exists[0] == 0:
        # 表不存在，创建
        from fts.data_sources.migrate import CONTRACT_KLINE_CREATE_DDL
        con.execute(CONTRACT_KLINE_CREATE_DDL)
        logger.info("[migrate] contract_kline 表已创建")
        return 0

    # 检查现有列
    rows = con.execute("PRAGMA table_info('contract_kline')").fetchall()
    existing = {r[1] for r in rows}
    logger.info("[migrate] contract_kline 现有列: %s", sorted(existing))

    added = 0
    for col_name, col_type in CONTRACT_KLINE_MISSING_COLUMNS:
        if col_name in existing:
            continue
        con.execute(f'ALTER TABLE "contract_kline" ADD COLUMN "{col_name}" {col_type}')
        added += 1
        logger.info("[migrate] 新增列: %s %s", col_name, col_type)

    if added > 0:
        # 为旧数据补 source 默认值
        con.execute("UPDATE contract_kline SET source = 'AKSHARE' WHERE source IS NULL")
        logger.info("[migrate] 旧数据 source 已补默认值 'AKSHARE'")

    logger.info("[migrate] contract_kline schema 迁移完成: %d 列新增", added)
    return added


# ─── TQ 数据获取 ─────────────────────────────────────────────────────────

def _tq_batch_query(
    contracts: list[str],
    count: int = 500,
) -> dict[str, dict[str, list]]:
    """批量查询 TQ 合约数据。

    Args:
        contracts: 通达信合约代码列表（如 ['RB2610.SHF', 'RB2605.SHF']）
        count: 每个合约返回的 K 线数量

    Returns:
        {合约代码: {字段名: [值列表]}} 或空字典（失败）
    """
    if not contracts:
        return {}

    import json as _json
    import urllib.error
    import urllib.request

    payload = {
        "id": int(time.time() * 1000),
        "method": "get_market_data",
        "params": {
            "stock_list": contracts,
            "count": count,
            "period": "1d",
        },
    }
    try:
        req = urllib.request.Request(
            TDX_RPC_URL,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TQ_TIMEOUT) as resp:
            raw = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("[TQ] 批量查询失败: %s", e)
        return {}

    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, dict):
        return {}

    values = result.get("Value", {})
    if not isinstance(values, dict):
        return {}

    # 过滤有数据的合约
    output: dict[str, dict[str, list]] = {}
    for code, block in values.items():
        if not isinstance(block, dict):
            continue
        # 检查是否有数据
        if "Date" not in block or not block.get("Date"):
            continue
        output[code] = block

    return output


def _tq_block_to_rows(
    block: dict[str, list],
    contract: str,
    base: str,
    trace_id: str,
) -> list[tuple]:
    """将 TQ 返回的合约数据块转为 contract_kline 行。

    TQ 返回字段: Date, Open, High, Low, Close, Volume, Amount, VolInStock
    """
    dates = block.get("Date", [])
    if not dates:
        return []

    opens = block.get("Open", [])
    highs = block.get("High", [])
    lows = block.get("Low", [])
    closes = block.get("Close", [])
    volumes = block.get("Volume", [])
    amounts = block.get("Amount", [])
    holds = block.get("VolInStock", block.get("Hold", []))

    now_iso = datetime.now().isoformat()
    rows: list[tuple] = []

    for i in range(len(dates)):
        date_str = str(dates[i]) if dates[i] else ""
        if not date_str or len(date_str) != 8:
            continue
        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            continue

        row = (
            base,                    # symbol
            contract,                # contract
            "daily",                 # period
            date_obj,                # date
            _safe_float(opens[i] if i < len(opens) else None),
            _safe_float(highs[i] if i < len(highs) else None),
            _safe_float(lows[i] if i < len(lows) else None),
            _safe_float(closes[i] if i < len(closes) else None),
            _safe_float(volumes[i] if i < len(volumes) else None),
            _safe_float(amounts[i] if i < len(amounts) else None),
            _safe_float(holds[i] if i < len(holds) else None),
            0.0,                     # settle - TQ 不返回结算价
            "TDX_LOCAL",             # source
            now_iso,                 # fetched_at
            trace_id,                # trace_id
        )
        rows.append(row)

    return rows


# ─── AKShare 数据获取 ────────────────────────────────────────────────────

def _aks_fetch_contract(
    contract: str,
    base: str,
    days: int,
    trace_id: str,
) -> list[tuple]:
    """从 AKShare 获取单个合约日线数据。

    AKShare futures_zh_daily_sina 返回: date, open, high, low, close, volume, hold, settle
    """
    import akshare as ak  # type: ignore[import-untyped]

    try:
        df = ak.futures_zh_daily_sina(symbol=contract)
    except Exception as e:
        logger.debug("[AKS] %s 获取失败: %s", contract, e)
        return []

    if df is None or df.empty:
        return []

    df = df.tail(days)
    now_iso = datetime.now().isoformat()
    rows: list[tuple] = []

    for _, r in df.iterrows():
        date_val = r.get("date")
        if date_val is None:
            continue
        if hasattr(date_val, "strftime"):
            date_obj = date_val
        else:
            try:
                date_obj = pd.Timestamp(date_val).date()
            except Exception:
                continue

        row = (
            base,
            contract,
            "daily",
            date_obj,
            _safe_float(r.get("open")),
            _safe_float(r.get("high")),
            _safe_float(r.get("low")),
            _safe_float(r.get("close")),
            _safe_float(r.get("volume")),
            _safe_float(r.get("amount")),
            _safe_float(r.get("hold")),
            _safe_float(r.get("settle")),
            "AKSHARE",
            now_iso,
            trace_id,
        )
        rows.append(row)

    return rows


# ─── 写入 contract_kline ─────────────────────────────────────────────────

def _write_contract_kline(
    writer: Any,
    base: str,
    rows: list[tuple],
    incremental: bool = False,
) -> int:
    """将合约数据写入 contract_kline。

    Args:
        writer: DuckDB 写入器
        base: 品种基名（如 "RB"）
        rows: 数据行
        incremental: 增量模式（不删除已有品种数据）

    Returns:
        写入行数
    """
    if not rows:
        return 0

    if not incremental:
        # 全量模式：删除已有品种数据后写入
        writer.execute("DELETE FROM contract_kline WHERE symbol = ?", [base])

    # 增量模式：跳过已存在的合约
    if incremental:
        existing = set()
        try:
            existing_rows = writer.execute(
                "SELECT DISTINCT contract FROM contract_kline WHERE symbol = ?",
                [base],
            ).fetchall()
            existing = {r[0] for r in existing_rows}
        except Exception:
            pass

        rows = [r for r in rows if r[1] not in existing]

    if not rows:
        return 0

    insert_sql = f"""INSERT INTO contract_kline (
        {", ".join(CONTRACT_KLINE_COLUMNS)}
    ) VALUES ({", ".join("?" * len(CONTRACT_KLINE_COLUMNS))})"""

    writer.executemany(insert_sql, rows)
    return len(rows)


# ─── 主流程 ──────────────────────────────────────────────────────────────

def _resolve_symbols(
    cli_symbols: Optional[list[str]],
    universe: str,
) -> list[str]:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="通过通达信 TQ 同步全合约日线数据到 contract_kline",
    )
    parser.add_argument(
        "--symbol", action="append", default=None,
        help="指定品种（可多次或逗号分隔），默认全品种",
    )
    parser.add_argument(
        "--universe", choices=["core", "stratified", "holdout", "all"],
        default="all", help="品种池（默认 all 全品种 82 个）",
    )
    parser.add_argument(
        "--years", type=int, default=DEFAULT_YEARS,
        help=f"回溯年数（默认 {DEFAULT_YEARS}）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="试运行（不写入数据库）",
    )
    parser.add_argument(
        "--incremental", action="store_true", help="增量模式（跳过已有合约）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    trace_id = f"fts.contract.sync_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    started_at = time.time()

    # ── 1. 解析品种列表 ──
    symbols = _resolve_symbols(args.symbol, args.universe)
    if not symbols:
        logger.error("无品种可同步，退出")
        return 1
    logger.info("品种列表: %d 个", len(symbols))

    # ── 2. Schema 迁移 ──
    logger.info("=" * 60)
    logger.info("  Phase 1: Schema 迁移")
    logger.info("=" * 60)

    from fts.data_futures import _get_writer, _DUCKDB_PATH

    logger.info("DuckDB 路径: %s", _DUCKDB_PATH)
    if not _DUCKDB_PATH.exists():
        logger.error("DuckDB 文件不存在: %s", _DUCKDB_PATH)
        return 1

    writer = _get_writer()
    cols_added = _migrate_contract_kline_schema(writer)
    logger.info("Schema 迁移: %d 列新增", cols_added)

    # ── 3. 生成合约代码 ──
    logger.info("=" * 60)
    logger.info("  Phase 2: 合约代码生成")
    logger.info("=" * 60)

    all_contracts: dict[str, list[str]] = {}
    for sym in symbols:
        base = _get_base_symbol(sym)
        codes = _generate_contract_codes(base, years=args.years)
        all_contracts[base] = codes
        logger.debug("[%s] 生成 %d 个合约代码", base, len(codes))

    total_contracts = sum(len(c) for c in all_contracts.values())
    logger.info("合约代码生成完成: %d 品种, %d 个合约", len(all_contracts), total_contracts)

    if args.dry_run:
        logger.info("DRY-RUN 模式: 跳过数据获取和写入")
        elapsed = time.time() - started_at
        summary = {
            "trace_id": trace_id,
            "mode": "dry-run",
            "symbols": len(symbols),
            "contracts_generated": total_contracts,
            "elapsed_seconds": round(elapsed, 3),
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    # ── 4. TQ 主源同步 ──
    logger.info("=" * 60)
    logger.info("  Phase 3: TQ 主源同步")
    logger.info("=" * 60)

    tq_results: dict[str, dict[str, int]] = {}  # base -> {contract: rows}
    tq_failed: list[str] = []

    for base, contracts in all_contracts.items():
        exchange = _get_exchange_suffix(base)
        tdx_contracts = [_contract_to_tdx(c, exchange) for c in contracts]

        # 分批查询 TQ
        for batch_start in range(0, len(tdx_contracts), TQ_BATCH_SIZE):
            batch = tdx_contracts[batch_start:batch_start + TQ_BATCH_SIZE]
            tq_data = _tq_batch_query(batch, count=5000)

            if not tq_data:
                continue

            for tdx_code, block in tq_data.items():
                contract_name = tdx_code.split(".")[0]  # 去掉后缀
                rows = _tq_block_to_rows(block, contract_name, base, trace_id)
                if rows:
                    written = _write_contract_kline(writer, base, rows, incremental=args.incremental)
                    if base not in tq_results:
                        tq_results[base] = {}
                    tq_results[base][contract_name] = written
                    logger.info(
                        "[TQ] %s/%s: %d 行写入 (%s)",
                        base, contract_name, written,
                        "增量" if args.incremental else "全量",
                    )

        # 标记 TQ 同步状态
        tq_contracts = tq_results.get(base, {})
        logger.info(
            "[TQ] %s 完成: %d/%d 合约有数据",
            base, len(tq_contracts), len(contracts),
        )

    tq_total_rows = sum(
        v for d in tq_results.values() for v in d.values()
    )
    logger.info("TQ 同步完成: %d 品种, %d 行写入", len(tq_results), tq_total_rows)

    # ── 5. AKShare 降级同步 ──
    logger.info("=" * 60)
    logger.info("  Phase 4: AKShare 降级同步")
    logger.info("=" * 60)

    # 检查哪些合约已有 TQ 数据
    existing_tq_contracts: set[tuple[str, str]] = set()
    for base, contracts in tq_results.items():
        for c in contracts:
            existing_tq_contracts.add((base, c))

    aks_results: dict[str, int] = {}  # base -> rows
    aks_failed: list[str] = []

    for base, contracts in all_contracts.items():
        for contract in contracts:
            # 跳过已有 TQ 数据的合约
            if (base, contract) in existing_tq_contracts:
                continue

            # 检查是否已有数据（增量模式）
            if args.incremental:
                try:
                    exists = writer.execute(
                        "SELECT COUNT(*) FROM contract_kline WHERE symbol = ? AND contract = ?",
                        [base, contract],
                    ).fetchone()[0]
                    if exists > 0:
                        continue
                except Exception:
                    pass

            rows = _aks_fetch_contract(contract, base, days=5000, trace_id=trace_id)
            if rows:
                written = _write_contract_kline(writer, base, rows, incremental=True)
                if base not in aks_results:
                    aks_results[base] = 0
                aks_results[base] += written
                logger.info("[AKS] %s/%s: %d 行写入", base, contract, written)
            else:
                aks_failed.append(f"{base}/{contract}")

    aks_total_rows = sum(aks_results.values())
    logger.info(
        "AKShare 同步完成: %d 品种, %d 行写入, %d 合约无数据",
        len(aks_results), aks_total_rows, len(aks_failed),
    )

    # ── 6. 输出摘要 ──
    elapsed = time.time() - started_at
    summary = {
        "trace_id": trace_id,
        "started_at": datetime.fromtimestamp(started_at).isoformat(),
        "finished_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "mode": "incremental" if args.incremental else "full",
        "symbols_total": len(symbols),
        "tq_varieties": len(tq_results),
        "tq_rows": tq_total_rows,
        "aks_varieties": len(aks_results),
        "aks_rows": aks_total_rows,
        "aks_missing_contracts": len(aks_failed),
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print()
        print("─" * 60)
        print("  同步结果摘要")
        print("─" * 60)
        print(f"  trace_id          : {summary['trace_id']}")
        print(f"  symbols_total     : {summary['symbols_total']}")
        print(f"  TQ varieties      : {summary['tq_varieties']}")
        print(f"  TQ rows           : {summary['tq_rows']}")
        print(f"  AKShare varieties : {summary['aks_varieties']}")
        print(f"  AKShare rows      : {summary['aks_rows']}")
        print(f"  缺失合约数        : {summary['aks_missing_contracts']}")
        print(f"  elapsed_seconds   : {summary['elapsed_seconds']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())