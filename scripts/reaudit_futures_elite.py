"""
FTS 存量期货精英因子新标准全量重审（手动 CLI，薄包装）。

核心逻辑见 fts/monitor/reaudit.py（月度任务 monthly_decay_eval_job 共用）。

用法:
    python scripts/reaudit_futures_elite.py [--days 750]
        [--apply] [--factor-ids fct_xxx,fct_yyy] [--json]

输出:
    memory/logs/evolution/futures/reaudit_{date}.json   (全量结果)
    控制台打印汇总。

trace_id: fts.reaudit.{ts}
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=750, help="回溯天数（与演化同口径，默认 750）")
    ap.add_argument("--apply", action="store_true", help="评估后立即按处置规则回写 DuckDB")
    ap.add_argument("--factor-ids", type=str, default="", help="只重审指定因子 ID（逗号分隔）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果文件")
    args = ap.parse_args()

    from fts.monitor.reaudit import run_reaudit

    factor_ids = [x.strip() for x in args.factor_ids.split(",") if x.strip()] or None
    report = run_reaudit(
        market="futures",
        days=args.days,
        apply=args.apply,
        factor_ids=factor_ids,
        out_json=args.json,
    )

    # 控制台打印（含 limit/offset 裁剪语义：factor_ids 指定时不受 limit/offset 影响）
    print(f"\n[reaudit] trace_id={report.trace_id} 处理 {report.total} 个")
    print(f"[reaudit] 汇总: retain={report.counts.get('retain', 0)} shadow={report.counts.get('shadow', 0)} "
          f"retire={report.counts.get('retire', 0)} error={report.counts.get('error', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
