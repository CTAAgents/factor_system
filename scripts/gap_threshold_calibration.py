"""gap_threshold_calibration.py — G4/G11 硬阈值校准脚本（35-gap-closure-plan.md §9）。

对现有期货因子库（data/factor_catalog_futures.duckdb）重算 IC / ICIR / 日换手 / 衰减分布，
输出分位数表（P25/P50/P75/P90/P95）与候选阈值下的因子通过率，供定值依据：
  - G4  `icir_min`（ICIR 硬门槛）
  - G11 `turnover_daily_max`（日换手硬剔除线）

数据来源（read_only，不写库）：
  - factor_catalog:        当前活跃版本（ic/icir/turnover_monthly/decay_6m/sharpe/status/is_elite/family）
  - factor_evaluations:    评估历史（level_1_ic/level_1_icir/level_1_turnover）

说明：
  - 日换手反推口径：turnover_monthly / 42——库内月度换手 = 日换手 × 42
    （G11 信号翻转率口径：turnover_daily = mean(|Δsign|)/2、turnover_monthly = mean(|Δsign|)×21，
    见 evaluation_chain 时序/横截面两条路径与 scripts/backfill_turnover.py）
  - 前后半段符号反转无直接存储列，无法从库中校准，需在 evaluation_chain 落地时以样本分布记录（见计划 §4.1）

用法:
    python scripts/gap_threshold_calibration.py [--db-path data/factor_catalog_futures.duckdb]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

logger = logging.getLogger("gap_threshold_calibration")

# 项目根目录（脚本位于 scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "factor_catalog_futures.duckdb"

# 候选阈值（供通过率对比）
CANDIDATE_ICIR: tuple[float, ...] = (0.10, 0.20, 0.30, 0.40, 0.50)
CANDIDATE_DAILY_TURNOVER: tuple[float, ...] = (0.10, 0.15, 0.20, 0.25, 0.30)
# 库内 turnover_monthly = turnover_daily × 42（G11 口径：turnover_daily = mean(|Δsign|)/2，
# turnover_monthly = mean(|Δsign|)×21，故换算系数 = 21/(1/2) = 42；见 evaluation_chain 与 backfill_turnover.py）。
# 注意：21 为月均交易日数，但 G11 日换手取信号翻转率 mean(|Δsign|)/2，反推日换手须除以 42。
TURNOVER_DAILY_TO_MONTHLY: int = 42


def _percentiles(values: np.ndarray, ps: tuple[float, ...] = (25.0, 50.0, 75.0, 90.0, 95.0)) -> dict[str, float]:
    """计算分位数（NaN 已剔除）。"""
    if values.size == 0:
        return {f"p{int(p)}": float("nan") for p in ps}
    return {f"p{int(p)}": float(np.percentile(values, p)) for p in ps}


def _pass_rate(values: np.ndarray, threshold: float, direction: str = "ge") -> tuple[int, int, float]:
    """方向 ge（≥threshold）/ le（≤threshold）通过率。返回 (通过数, 总数, 比率)。"""
    if values.size == 0:
        return 0, 0, 0.0
    if direction == "ge":
        mask = values >= threshold
    else:
        mask = values <= threshold
    passed = int(mask.sum())
    return passed, int(values.size), passed / values.size


def _clean(values: list[Any]) -> np.ndarray:
    """清洗为 float ndarray，剔除 None/NaN/Inf。"""
    arr = np.asarray([float(v) for v in values if v is not None], dtype=float)
    arr = arr[np.isfinite(arr)]
    return arr


def _calibrate_catalog(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """factor_catalog 主表校准。"""
    rows = conn.execute(
        """
        SELECT ic, icir, turnover_monthly, decay_6m, sharpe, status, is_elite, family
        FROM factor_catalog
        WHERE market = 'futures'
        """
    ).fetchall()

    ic = _clean([r[0] for r in rows])
    icir = _clean([r[1] for r in rows])
    turnover_monthly = _clean([r[2] for r in rows])
    daily_turnover = turnover_monthly / TURNOVER_DAILY_TO_MONTHLY if turnover_monthly.size else turnover_monthly
    decay = _clean([r[3] for r in rows])
    sharpe = _clean([r[4] for r in rows])

    elite = [r for r in rows if r[6] is True]
    icir_elite = _clean([r[1] for r in elite])
    daily_turnover_elite = _clean([r[2] for r in elite]) / TURNOVER_DAILY_TO_MONTHLY if elite else np.array([])

    icir_abs = np.abs(icir)
    icir_abs_elite = np.abs(icir_elite)

    out: dict[str, Any] = {
        "n_total": len(rows),
        "n_elite": len(elite),
        "ic": {"n": int(ic.size), "percentiles": _percentiles(ic)},
        "icir_abs": {"n": int(icir_abs.size), "percentiles": _percentiles(icir_abs)},
        "daily_turnover": {"n": int(daily_turnover.size), "percentiles": _percentiles(daily_turnover)},
        "decay_6m": {"n": int(decay.size), "percentiles": _percentiles(decay)},
        "sharpe": {"n": int(sharpe.size), "percentiles": _percentiles(sharpe)},
        "icir_abs_elite": {"n": int(icir_abs_elite.size), "percentiles": _percentiles(icir_abs_elite)},
        "daily_turnover_elite": {
            "n": int(daily_turnover_elite.size),
            "percentiles": _percentiles(daily_turnover_elite),
        },
        "icir_candidates": {
            str(t): {"passed": _pass_rate(icir_abs, t)[0], "rate": _pass_rate(icir_abs, t)[2]}
            for t in CANDIDATE_ICIR
        },
        "turnover_candidates": {
            str(t): {"passed": _pass_rate(daily_turnover, t, "le")[0], "rate": _pass_rate(daily_turnover, t, "le")[2]}
            for t in CANDIDATE_DAILY_TURNOVER
        },
    }
    return out


def _calibrate_evaluations(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """factor_evaluations 评估历史校准（Level-1 评估链口径，最贴近 evaluation_chain 输出）。"""
    rows = conn.execute(
        """
        SELECT level_1_ic, level_1_icir, level_1_turnover, overall_passed
        FROM factor_evaluations
        WHERE level_1_ic IS NOT NULL
        """
    ).fetchall()

    ic = _clean([r[0] for r in rows])
    icir = _clean([r[1] for r in rows])
    turnover = _clean([r[2] for r in rows])
    daily_turnover = turnover / TURNOVER_DAILY_TO_MONTHLY if turnover.size else turnover

    icir_abs = np.abs(icir)
    out: dict[str, Any] = {
        "n_total": len(rows),
        "ic": {"n": int(ic.size), "percentiles": _percentiles(ic)},
        "icir_abs": {"n": int(icir_abs.size), "percentiles": _percentiles(icir_abs)},
        "daily_turnover": {"n": int(daily_turnover.size), "percentiles": _percentiles(daily_turnover)},
        "icir_candidates": {
            str(t): {"passed": _pass_rate(icir_abs, t)[0], "rate": _pass_rate(icir_abs, t)[2]}
            for t in CANDIDATE_ICIR
        },
        "turnover_candidates": {
            str(t): {"passed": _pass_rate(daily_turnover, t, "le")[0], "rate": _pass_rate(daily_turnover, t, "le")[2]}
            for t in CANDIDATE_DAILY_TURNOVER
        },
    }
    return out


def _render_markdown(catalog: dict[str, Any], evals: dict[str, Any], db_path: Path) -> str:
    """渲染校准报告 Markdown。"""
    def pct_block(title: str, d: dict[str, Any]) -> str:
        rows = "".join(f"| {k} | {v:.4f} |\n" for k, v in d["percentiles"].items())
        return f"### {title}（n={d['n']}）\n\n| 分位 | 值 |\n|---|---|\n{rows}"

    def cand_block(title: str, cand: dict[str, Any], n_total: int) -> str:
        rows = "".join(
            f"| ≥{t} | {v['passed']} | {v['rate']:.1%} |\n" for t, v in cand.items()
        )
        return f"### {title}\n\n| 阈值 | 通过数 | 通过率（共 {n_total}） |\n|---|---|---|\n{rows}"

    lines = [
        "# 阈值校准报告（35-gap-closure-plan §9）",
        "",
        f"- 数据库: `{db_path}`",
        "- 生成时间: 由脚本运行产生",
        "",
        "## 一、factor_catalog（当前活跃版本）",
        f"- 总因子数: {catalog['n_total']}，精英数: {catalog['n_elite']}",
        "",
        pct_block("IC 分布", catalog["ic"]),
        "",
        pct_block("|ICIR| 分布（全量）", catalog["icir_abs"]),
        "",
        pct_block("|ICIR| 分布（精英）", catalog["icir_abs_elite"]),
        "",
        pct_block("日换手分布（全量，turnover_monthly/21）", catalog["daily_turnover"]),
        "",
        pct_block("日换手分布（精英）", catalog["daily_turnover_elite"]),
        "",
        pct_block("decay_6m 分布", catalog["decay_6m"]),
        "",
        pct_block("Sharpe 分布", catalog["sharpe"]),
        "",
        "## 二、候选阈值通过率（全量）",
        "",
        cand_block("G4 ICIR 硬门槛候选（|ICIR| ≥ 阈值）", catalog["icir_candidates"], catalog["icir_abs"]["n"]),
        "",
        cand_block("G11 日换手硬剔除候选（日换手 ≤ 阈值通过）", catalog["turnover_candidates"], catalog["daily_turnover"]["n"]),
        "",
        "## 三、factor_evaluations（评估历史，Level-1 口径）",
        f"- 评估记录数: {evals['n_total']}",
        "",
        pct_block("|ICIR| 分布", evals["icir_abs"]),
        "",
        pct_block("日换手分布", evals["daily_turnover"]),
        "",
        cand_block("ICIR 硬门槛候选", evals["icir_candidates"], evals["icir_abs"]["n"]),
        "",
        cand_block("日换手硬剔除候选", evals["turnover_candidates"], evals["daily_turnover"]["n"]),
        "",
        "## 四、结论与建议",
        "",
        "> 阈值定值由人工依据上述分布决定，禁止直接套用外部硬值（35-gap-closure-plan.md §9）。",
        "> 定值后回填 35-gap-closure-plan.md §4.1（icir_min）与 §5.4（turnover_daily_max）并登记 08-gap-analysis.md。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G4/G11 阈值校准：跑因子库 IC/ICIR/日换手分布")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB), help="因子库 DuckDB 路径")
    parser.add_argument("--out", type=str, default="", help="报告输出路径（缺省 reports/gap/threshold_calibration_<date>.md）")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error("数据库不存在: %s", db_path)
        return 1

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        catalog = _calibrate_catalog(conn)
        evals = _calibrate_evaluations(conn)
    finally:
        conn.close()

    # 日志摘要
    logger.info("=== factor_catalog ===")
    logger.info("ICIR| 分位: %s", catalog["icir_abs"]["percentiles"])
    logger.info("日换手 分位: %s", catalog["daily_turnover"]["percentiles"])
    logger.info("ICIR≥0.30 通过率: %.1f%%", catalog["icir_candidates"]["0.3"]["rate"] * 100)
    logger.info("日换手≤0.20 通过率: %.1f%%", catalog["turnover_candidates"]["0.2"]["rate"] * 100)
    logger.info("=== factor_evaluations ===")
    logger.info("ICIR| 分位: %s", evals["icir_abs"]["percentiles"])
    logger.info("日换手 分位: %s", evals["daily_turnover"]["percentiles"])

    # 落盘报告
    out_path = Path(args.out) if args.out else PROJECT_ROOT / "reports" / "gap" / f"threshold_calibration_{__import__('datetime').date.today():%Y%m%d}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_markdown(catalog, evals, db_path), encoding="utf-8")
    logger.info("校准报告已写入: %s", out_path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    raise SystemExit(main())
