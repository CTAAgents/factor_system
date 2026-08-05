"""
generate_quality_report.py — 因子质量排名报告生成脚本

加载全量种子因子，使用合成面板数据运行批量质检（含 WalkForward），
输出因子质量排名报告到 reports/ 目录。

用法:
    python scripts/generate_quality_report.py
    python scripts/generate_quality_report.py --symbols RB0,M0,CU0,AU0
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.seed_loader import load_all_yaml_seeds
from fts.pipeline.batch_quality_inspector import (
    BatchQualityInspector,
    QualityRankReport,
    run_batch_quality_inspection,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def generate_synthetic_panel(symbols: list[str], n_days: int = 500) -> dict[str, pd.DataFrame]:
    """生成多品种合成面板数据。"""
    dates = pd.date_range(end=datetime.now(), periods=n_days, freq="B")
    rng = np.random.default_rng(42)

    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        base_price = 2000 + i * 5000
        drift = 0.0001 + i * 0.0001
        vol = 0.010 + i * 0.002
        returns = rng.normal(drift, vol, len(dates))
        prices = base_price * np.cumprod(1 + returns)
        panel[sym] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, len(dates))),
            "high": prices * (1 + np.abs(rng.normal(0, 0.005, len(dates)))),
            "low": prices * (1 - np.abs(rng.normal(0, 0.005, len(dates)))),
            "close": prices,
            "volume": rng.integers(10000, 50000, len(dates)),
            "open_interest": rng.integers(50000, 100000, len(dates)),
        }, index=dates)

    return panel


def main() -> None:
    parser = argparse.ArgumentParser(description="生成因子质量排名报告")
    parser.add_argument("--symbols", default="RB0,M0,CU0,AU0,AG0",
                        help="品种列表，逗号分隔")
    parser.add_argument("--output", default="reports",
                        help="报告输出目录")
    parser.add_argument("--no-walk-forward", action="store_true",
                        help="禁用 WalkForward 验证")
    parser.add_argument("--max-factors", type=int, default=0,
                        help="最大质检因子数（0=全量）")
    parser.add_argument("--min-grade", default="B",
                        help="最低准入等级 (A/B/C)")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info("=" * 60)
    logger.info("FTS 因子质量排名报告生成器")
    logger.info("=" * 60)

    # 1. 加载种子因子
    logger.info("加载全量种子因子...")
    seeds = load_all_yaml_seeds(include_external=True)
    logger.info("加载完成: %d 个因子", len(seeds))

    # 2. 生成合成面板
    logger.info("生成合成面板数据: %d 品种 x 500 交易日", len(symbols))
    panel = generate_synthetic_panel(symbols)
    logger.info("面板生成完成")

    # 3. 准备因子列表
    factors = seeds
    if args.max_factors > 0:
        factors = seeds[:args.max_factors]
        logger.info("截取前 %d 个因子进行质检", len(factors))

    # 4. 运行批量质检
    enable_wf = not args.no_walk_forward
    logger.info("运行批量质检 (WalkForward=%s, 准入=%s)...", enable_wf, args.min_grade)

    report = run_batch_quality_inspection(
        factors=factors,
        panel_data=panel,
        output_dir=args.output,
        enable_walk_forward=enable_wf,
        min_grade=args.min_grade,
    )

    # 5. 输出摘要
    logger.info("=" * 60)
    logger.info("质检完成:")
    logger.info("  总因子数: %d", report.total_factors)
    logger.info("  通过: %d, 淘汰: %d", report.passed_factors, report.failed_factors)
    logger.info("  通过率: %.1f%%", report.pass_rate)
    logger.info("  平均得分: %.1f/50", report.average_score)
    logger.info("  等级分布: A=%d, B=%d, C=%d",
                report.grade_distribution.get("A", 0),
                report.grade_distribution.get("B", 0),
                report.grade_distribution.get("C", 0))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
