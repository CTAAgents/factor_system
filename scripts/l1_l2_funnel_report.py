"""
scripts/l1_l2_funnel_report.py — plans/44 Phase 3 D2 L1→L2 闭环每日报告

读取 state.db 中 `meta_loop/{market}/l1_l2_funnel` 漏斗统计，
输出 injected → consumed → promoted 转化率与积压 warning（防 L1 无限注入）。

用法:
    python scripts/l1_l2_funnel_report.py [--market futures] [--market energy] [--backlog-days 7]

输出:
    - 控制台: 分市场转化率表 + 积压 warning
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# scripts/ 不在 sys.path 中，需要手动添加（基于项目根目录动态解析）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.l1_l2_funnel import funnel_report  # noqa: E402
from fts.config.settings import get_config  # noqa: E402


def _print_report(rows: list[dict]) -> None:
    """控制台输出转化率表。"""
    if not rows:
        print("[l1_l2_funnel] 无漏斗记录（L1 尚未运行或 state.db 为空）")
        return
    header = (
        f"{'market':<10} {'injected':>9} {'consumed':>9} {'promoted':>9} "
        f"{'consume%':>9} {'promote%':>9} {'backlog':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['market']:<10} {r['injected']:>9} {r['l2_consumed']:>9} {r['l2_promoted']:>9} "
            f"{r['consume_rate'] * 100:>8.1f}% {r['promote_rate'] * 100:>8.1f}% {r['backlog']:>8}"
        )
        if r["warning"]:
            print(f"  ⚠️  {r['warning']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="L1→L2 闭环转化率每日报告")
    parser.add_argument("--market", action="append", default=[], help="市场（可重复，默认全部）")
    parser.add_argument("--backlog-days", type=int, default=0, help="积压 warning 天数（默认读配置 l1_l2_backlog_days）")
    args = parser.parse_args()

    markets = tuple(args.market) or ("futures", "energy")
    backlog_days = args.backlog_days or int(getattr(get_config(), "l1_l2_backlog_days", 7))
    rows = funnel_report(markets=markets, backlog_days=backlog_days)
    _print_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
