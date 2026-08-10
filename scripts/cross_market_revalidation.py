"""
scripts/cross_market_revalidation.py — 跨市场泛化验证脚本

验证期货因子在股票/ETF 市场的有效性，或股票因子在期货市场的有效性。
识别通用因子、市场特异因子和失效因子。

用法:
    # 期货→股票（默认）
    python scripts/cross_market_revalidation.py --direction futures-to-stock --days 120

    # 期货→ETF
    python scripts/cross_market_revalidation.py --direction futures-to-etf --days 120

    # 股票→期货
    python scripts/cross_market_revalidation.py --direction stock-to-futures --days 120

    # 限制因子/品种数（快速调试）
    python scripts/cross_market_revalidation.py --max-factors 5 --max-stocks 10

输出:
    - 控制台: 验证结果摘要
    - 文件:     reports/{date}/cross_market_revalidation_{date}.md
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cross_market")


def main(
    direction: str = "futures-to-stock",
    days: int = 120,
    max_factors: int = 0,
    max_stocks: int = 0,
    output_dir: str | None = None,
) -> int:
    """执行跨市场泛化验证。

    Args:
        direction: 验证方向 (futures-to-stock / futures-to-etf / stock-to-futures)
        days: 回溯天数
        max_factors: 最大因子数（0=全量）
        max_stocks: 最大成分股数（0=全量，仅 futures-to-stock 有效）
        output_dir: 报告输出目录

    Returns:
        int: 0=成功, 1=失败
    """
    from fts.cross_market import CrossMarketDataAdapter, CrossMarketEngine

    t0 = time.time()
    today = date.today().isoformat()

    print("=" * 60)
    print(f"  跨市场泛化验证 — {today}")
    print(f"  方向: {direction}")
    print("=" * 60)

    # 初始化引擎
    adapter = CrossMarketDataAdapter()
    engine = CrossMarketEngine(adapter)

    # 根据方向选择验证方法
    direction_map = {
        "futures-to-stock": ("futures", "stock", engine.run_futures_to_stock),
        "futures-to-etf": ("futures", "etf", engine.run_futures_to_etf),
        "stock-to-futures": ("stock", "futures", engine.run_stock_to_futures),
    }

    if direction not in direction_map:
        print(f"[ERROR] 不支持的方向: {direction}")
        print(f"        可选: {', '.join(direction_map.keys())}")
        return 1

    src_market, tgt_market, run_fn = direction_map[direction]

    # 执行验证
    kwargs = {"days": days, "max_factors": max_factors}
    if direction == "futures-to-stock":
        kwargs["max_stocks"] = max_stocks

    report = run_fn(**kwargs)

    # 生成报告
    output_path = None
    if output_dir:
        output_path = Path(output_dir) / f"cross_market_revalidation_{today}.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)

    report_path = engine.generate_report(report, output_path=output_path)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  验证完成: {report.total_factors} 个因子, 耗时 {elapsed:.1f}s")
    print(f"  🌍 通用: {report.n_universal}")
    print(f"  🔄 市场特异: {report.n_market_specific}")
    print(f"  ❌ 失效: {report.n_failed}")
    print(f"  ⬇️ 已降级: {report.n_deprecated}")
    print(f"  📄 报告: {report_path}")
    print(f"{'=' * 60}")

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="跨市场泛化验证")
    parser.add_argument(
        "--direction",
        type=str,
        default="futures-to-stock",
        choices=["futures-to-stock", "futures-to-etf", "stock-to-futures"],
        help="验证方向 (default: futures-to-stock)",
    )
    parser.add_argument("--days", type=int, default=120, help="回溯天数")
    parser.add_argument("--max-factors", type=int, default=0, help="最大因子数，0=全量")
    parser.add_argument("--max-stocks", type=int, default=0, help="最大成分股数，0=全量 (仅 futures-to-stock)")
    parser.add_argument("--output-dir", type=str, default=None, help="报告输出目录 (默认 reports/{date}/)")
    args = parser.parse_args()

    sys.exit(
        main(
            direction=args.direction,
            days=args.days,
            max_factors=args.max_factors,
            max_stocks=args.max_stocks,
            output_dir=args.output_dir,
        )
    )
