"""scripts.sync_futures_data — Phase 14.5 调度任务的手动触发包装。

将 `fts.scheduler.jobs.sync_futures_data_job` 暴露为独立 CLI 入口，
便于运维手动触发或调试。

用法:
    python scripts/sync_futures_data.py                    # 同步 25 核心品种
    python scripts/sync_futures_data.py --symbol RB0 CU0    # 指定品种
    python scripts/sync_futures_data.py --days 30           # 短回溯
    python scripts/sync_futures_data.py --json              # JSON 输出

HARNESS §5.5 trace_id 全链路: 单次执行生成唯一 trace_id 贯穿整个同步流程。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("sync_futures_data")


def _parse_symbols(arg: Optional[list[str]]) -> Optional[list[str]]:
    """解析 CLI 参数为品种列表。"""
    if not arg:
        return None
    flat: list[str] = []
    for item in arg:
        flat.extend(s for s in item.split(",") if s)
    return flat or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 14.5 多源数据同步（手动触发）")
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="指定品种（可多次或逗号分隔），默认 FUTURES_CORE_SUBSET 25 个",
    )
    parser.add_argument("--days", type=int, default=500, help="回溯天数（默认 500）")
    parser.add_argument(
        "--universe",
        choices=["core", "stratified", "holdout", "all"],
        default="core",
        help="品种池（默认 core，详见 fts.data_futures）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    symbols: Optional[list[str]] = _parse_symbols(args.symbol)
    if symbols is None:
        from fts.data_futures import (
            FUTURES_CORE_SUBSET,
            FUTURES_HOLDOUT,
            FUTURES_STRATIFIED_SUBSET,
            FUTURES_SUBSET,
        )

        if args.universe == "core":
            symbols = list(FUTURES_CORE_SUBSET)
        elif args.universe == "stratified":
            symbols = list(FUTURES_STRATIFIED_SUBSET)
        elif args.universe == "holdout":
            symbols = list(FUTURES_HOLDOUT)
        elif args.universe == "all":
            symbols = list(FUTURES_SUBSET)
        else:
            symbols = list(FUTURES_CORE_SUBSET)

    from fts.scheduler.jobs import sync_futures_data_job

    print("=" * 70)
    print("  Phase 14.5 多源数据同步（手动触发）")
    print(f"  universe={args.universe} symbols={len(symbols)} days={args.days}")
    print("=" * 70)

    # 直接调 job（内部已含异常处理 + 落盘摘要）
    sync_futures_data_job(symbols=symbols, days=args.days)

    # 读取最新摘要
    lineage_dir = ROOT / "data" / "_lineage"
    summaries = sorted(lineage_dir.glob("sync_summary_*.json"), reverse=True)
    if not summaries:
        print("[ERROR] 同步失败：无摘要落盘", file=sys.stderr)
        return 1

    latest = summaries[0]
    summary = json.loads(latest.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print()
        print("─" * 70)
        print(f"trace_id         : {summary.get('trace_id')}")
        print(f"started_at       : {summary.get('started_at')}")
        print(f"finished_at      : {summary.get('finished_at')}")
        print(f"elapsed_seconds  : {summary.get('elapsed_seconds')}")
        print(f"symbols_total    : {summary.get('symbols_total')}")
        print(f"success          : {summary.get('success')}")
        print(f"failure          : {summary.get('failure')}")
        print(f"total_rows       : {summary.get('total_rows')}")
        print(f"summary          : {latest}")

    return 0 if summary.get("failure", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
