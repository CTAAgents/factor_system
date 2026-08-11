"""
scripts/sync_liquidity_pool.py — 数据驱动动态池生成（GAP-054）

基于 TqSdk 流动性快照，按**渐进式替换 + 产业覆盖约束**生成动态核心池，
落盘 memory/portfolio/futures/futures_dynamic_pool.json 供运行期
`fts.data_futures.get_dynamic_core_subset()` 读取。

渐进式替换规则（机构实践，避免因子横截面断裂）:
    1. 现有池（FUTURES_CORE_SUBSET）中"够格"的品种全部保留（渐进，不换血）
    2. 现有池中"不够格"的品种按排名从后往前进入替换队列
    3. 候补 = 池外且"够格"的品种，按成交额排名降序；受产业约束限制
       （FUTURES_SECTOR_MAP 每板块最多 max-per-sector 个，默认 6）
    4. 替换数量 = 池内不够格数（池大小固定 25）；池内全达标则不替换

用法:
    python scripts/sync_liquidity_pool.py [--days 60] [--top-n 60]
        [--pool-size 25] [--max-per-sector 6] [--json out.json]

输出:
    - memory/portfolio/futures/futures_dynamic_pool.json（运行期动态池缓存）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import (
    FUTURES_CORE_SUBSET,
    FUTURES_SECTOR_MAP,
)
from fts.data_futures import DYNAMIC_POOL_CACHE
from scripts.liquidity_snapshot import build_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sync_liquidity_pool")

DEFAULT_POOL_CACHE = Path(DYNAMIC_POOL_CACHE)


def _sector_of(sym: str) -> str:
    """品种 → 产业链板块（FUTURES_SECTOR_MAP 反查）；未知返回 "其他"。"""
    for sector, members in FUTURES_SECTOR_MAP.items():
        if sym.upper() in {m.upper() for m in members}:
            return sector
    return "其他"


def build_pool(
    snap,  # DataFrame: symbol/avg_turnover_yi/rank/qualified
    pool_size: int = 25,
    max_per_sector: int = 6,
) -> dict:
    """渐进式替换生成动态池。"""
    current = [s.upper() for s in FUTURES_CORE_SUBSET]
    rows = snap.sort_values("avg_turnover_yi", ascending=False)
    by_rank = {r["symbol"]: r for _, r in rows.iterrows()}

    # 1. 现有池够格 → 保留（渐进）
    kept: list[str] = []
    removed: list[str] = []
    for s in current:
        r = by_rank.get(s)
        if r is not None and bool(r["qualified"]):
            kept.append(s)
        else:
            removed.append(s)

    # 2. 池内板块计数（保留 + 已替换）
    sector_count: dict[str, int] = {}
    for s in kept:
        sec = _sector_of(s)
        sector_count[sec] = sector_count.get(sec, 0) + 1

    # 3. 候补：池外 + 够格，按排名降序，受产业约束
    candidates = [(s, r) for s, r in by_rank.items() if s not in kept and s not in removed and bool(r["qualified"])]
    candidates.sort(key=lambda kv: kv[1]["avg_turnover_yi"], reverse=True)

    slots = pool_size - len(kept)
    added: list[str] = []
    skipped_sector: list[str] = []
    for s, r in candidates:
        if slots <= 0:
            break
        sec = _sector_of(s)
        if sector_count.get(sec, 0) >= max_per_sector:
            skipped_sector.append(s)
            continue
        added.append(s)
        sector_count[sec] = sector_count.get(sec, 0) + 1
        slots -= 1

    # 超限裁剪：池内达标品种超过 pool_size 时，按成交额排名保留前 pool_size
    pool = kept + added
    if len(pool) > pool_size:
        ranked = sorted(pool, key=lambda s: by_rank.get(s, {}).get("avg_turnover_yi", 0.0), reverse=True)
        pool = ranked[:pool_size]

    return {
        "version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_asof": snap.attrs.get("asof", ""),
        "pool_size": pool_size,
        "max_per_sector": max_per_sector,
        "pool": pool,
        "kept": kept,
        "added": added,
        "removed": removed,
        "skipped_sector": skipped_sector,
        "rules": "渐进式替换: 池内够格全保留, 不够格按排名由池外够格候补替换, 每板块最多 max_per_sector 个",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="数据驱动动态池生成（渐进式替换）")
    ap.add_argument("--days", type=int, default=60, help="回溯交易日数（默认 60）")
    ap.add_argument("--top-n", type=int, default=60, help="成交额排名门槛（默认 60）")
    ap.add_argument("--pool-size", type=int, default=25, help="动态池大小（默认 25）")
    ap.add_argument("--max-per-sector", type=int, default=6, help="每产业链最多品种数（默认 6）")
    ap.add_argument("--json", type=str, default="", help="可选：动态池 JSON 落盘路径（默认 DYNAMIC_POOL_CACHE）")
    args = ap.parse_args()

    from scripts.liquidity_snapshot import _probe_tq_local

    # TQ-Local 为主力期窗口（最近5日），完整度门槛 3 日；TqSdk 主连为完整窗口，用 50
    eff_min_days = 3 if _probe_tq_local() else 50
    snap, asof = build_snapshot(args.days, eff_min_days, args.top_n)
    snap.attrs["asof"] = asof

    result = build_pool(snap, pool_size=args.pool_size, max_per_sector=args.max_per_sector)

    out = Path(args.json) if args.json else DEFAULT_POOL_CACHE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"== 动态池已生成（数据截至 {asof or 'N/A'}）==")
    print(
        f"池大小: {len(result['pool'])} | 保留: {len(result['kept'])} | 新增: {len(result['added'])} | 移除: {len(result['removed'])}"
    )
    if result["added"]:
        print(f"新增: {', '.join(result['added'])}")
    if result["removed"]:
        print(f"移除: {', '.join(result['removed'])}")
    if result["skipped_sector"]:
        print(f"产业约束跳过: {', '.join(result['skipped_sector'])}")
    print(f"落盘: {out}")


if __name__ == "__main__":
    main()
