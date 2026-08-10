#!/usr/bin/env python3
"""精英因子全员质量巡检 — 对存量精英因子执行 HighICScreener 质检，不合格因子出库处理。

用法:
    python scripts/elite_quality_inspection.py [--dry-run] [--market stock|futures]

选项:
    --dry-run       仅展示结果，不执行出库操作
    --market stock  仅对股票精英因子执行质检
    --market futures 仅对期货精英因子执行质检
    --min-score N   最低总分门槛（默认 60，即 B 级下限）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# 确保项目根目录在 sys.path 中
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fts.factor_engine.high_ic_screener import HighICScreener, HighICScreenReport


# ─── 路径 ──────────────────────────────────────────────────────

ELITE_DIRS: dict[str, Path] = {
    "stock": _PROJECT_ROOT / "memory" / "knowledge" / "factors" / "elite",
    "futures": _PROJECT_ROOT / "memory" / "knowledge" / "factors" / "futures_elite",
}


# ─── 核心逻辑 ──────────────────────────────────────────────────


def _load_elite_factors(elite_dir: Path) -> list[dict]:
    """从 elite 目录加载所有精英因子 JSON。"""
    factors: list[dict] = []
    for fp in sorted(elite_dir.glob("fct_*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            factors.append(data)
        except Exception as e:
            print(f"  ⚠️  加载失败 {fp.name}: {e}")
    return factors


def _build_screener_kwargs(factor: dict) -> dict:
    """从 elite 因子 JSON 构建 HighICScreener.screen 所需参数。

    缺失字段自动 skipped，不误杀。
    """
    # factor 本身
    factor_kw = {
        "factor_id": factor.get("factor_id", ""),
        "name": factor.get("name", ""),
        "market": factor.get("market", "unknown"),
        "family": factor.get("family", "unknown"),
    }

    # evaluation（精英 JSON 中已有）
    evaluation = factor.get("evaluation") or {}
    if isinstance(evaluation, dict):
        eval_kw = dict(evaluation)  # 浅拷贝，避免修改原始数据
    else:
        eval_kw = {}

    # 修复: 种子因子的 evaluation.level_2_economic 可能是全 0 占位值，
    # 此时从 factor.economic_logic 取真实值作为 fallback
    l2 = eval_kw.get("level_2_economic")
    eco = factor.get("economic_logic", {})
    if isinstance(l2, dict) and isinstance(eco, dict):
        dims = ["theory", "behavioral", "microstructure", "institutional"]
        l2_zeros = all(l2.get(d) in (0, 0.0, None) for d in dims)
        eco_has = any(eco.get(d) not in (None, 0, 0.0) for d in dims)
        if l2_zeros and eco_has:
            for d in dims:
                if eco.get(d) is not None:
                    l2[d] = eco[d]

    # correlation_metadata — 从 elite 快照中提取
    # 部分 elite 快照存有 max_corr_detected 字段
    corr_meta = {}
    if "max_corr_detected" in factor:
        corr_meta["max_corr_detected"] = factor["max_corr_detected"]

    # backtest_pipeline — 从 elite 快照中提取 net_excess_return
    bt_pipeline = {}
    if "net_excess_return" in factor:
        bt_pipeline["net_excess_return"] = factor["net_excess_return"]

    return {
        "factor": factor_kw,
        "evaluation": eval_kw,
        "correlation_metadata": corr_meta,
        "backtest_pipeline": bt_pipeline,
    }


def _retire_factor(factor_id: str, elite_dir: Path, reason: str = "质量巡检不合格") -> bool:
    """将精英因子出库：移动 JSON 文件到 _retired/ 子目录。

    不依赖 FactorRepo（避免 DuckDB 连接问题），直接操作文件系统。
    """
    retired_dir = elite_dir / "_retired"
    retired_dir.mkdir(parents=True, exist_ok=True)

    src = elite_dir / f"{factor_id}.json"
    if not src.exists():
        return False

    dst = retired_dir / f"{factor_id}.json"
    shutil.move(str(src), str(dst))
    return True


def _summarize_report(report: HighICScreenReport) -> dict:
    """浓缩筛查报告为摘要字典。"""
    return {
        "factor_id": report.factor_id,
        "factor_name": report.factor_name,
        "market": report.market,
        "total_score": report.total_score,
        "grade": report.grade,
        "disposition": report.disposition,
        "veto_triggered": report.veto_triggered,
        "veto_reasons": report.veto_reasons,
        "n_items_skipped": sum(1 for it in report.items if it.passed is None),
        "n_items_failed": sum(1 for it in report.items if it.passed is False),
    }


def run_inspection(
    market: str,
    elite_dir: Path,
    dry_run: bool = False,
    min_score: float = 60.0,
) -> dict:
    """对指定市场的精英因子执行全员质量巡检。

    Returns:
        {
            "market": str,
            "total": int,
            "passed": int,
            "failed": int,
            "skipped_not_retired": int,
            "details": [summary_dict, ...],
        }
    """
    print(f"\n{'=' * 60}")
    print(f"  [{market.upper()}] 精英因子质量巡检")
    print(f"  Elite 目录: {elite_dir}")
    print(f"  Dry-run: {dry_run}")
    print(f"  最低总分门槛: {min_score}")
    print(f"{'=' * 60}")

    factors = _load_elite_factors(elite_dir)
    print(f"\n  加载精英因子: {len(factors)} 个")

    if not factors:
        return {"market": market, "total": 0, "passed": 0, "failed": 0, "skipped_not_retired": 0, "details": []}

    screener = HighICScreener()

    passed: list[dict] = []
    failed: list[dict] = []
    retired_count = 0

    for i, factor in enumerate(factors, 1):
        fid = factor.get("factor_id", "?")
        name = factor.get("name", fid)
        kwargs = _build_screener_kwargs(factor)

        try:
            report = screener.screen(
                factor=kwargs["factor"],
                evaluation=kwargs["evaluation"],
                correlation_metadata=kwargs["correlation_metadata"],
                backtest_pipeline=kwargs["backtest_pipeline"],
                trace_id=f"quality_inspect_{fid}",
            )
        except Exception as e:
            print(f"  [{i}/{len(factors)}] ❌ {name} ({fid}) 质检异常: {e}")
            failed.append(
                {
                    "factor_id": fid,
                    "factor_name": name,
                    "market": market,
                    "total_score": 0.0,
                    "grade": "ERROR",
                    "disposition": "异常",
                    "veto_triggered": False,
                    "veto_reasons": [str(e)],
                    "n_items_skipped": 0,
                    "n_items_failed": 0,
                }
            )
            continue

        summary = _summarize_report(report)

        # 判定是否合格：总分 >= min_score 且无 veto
        is_qualified = report.total_score >= min_score and not report.veto_triggered and report.grade != "C"

        if is_qualified:
            passed.append(summary)
            print(f"  [{i}/{len(factors)}] ✅ {name} ({fid}) 总分={report.total_score:.1f} 评级={report.grade}")
        else:
            failed.append(summary)
            reason = (
                "; ".join(report.veto_reasons)
                if report.veto_reasons
                else f"总分={report.total_score:.1f} < {min_score}"
            )
            print(
                f"  [{i}/{len(factors)}] ❌ {name} ({fid}) 总分={report.total_score:.1f} 评级={report.grade} 原因: {reason}"
            )

            if not dry_run:
                retire_reason = f"质量巡检不合格: 总分={report.total_score:.1f} 评级={report.grade}"
                if report.veto_reasons:
                    retire_reason += f"; 否决={'; '.join(report.veto_reasons)}"
                if _retire_factor(fid, elite_dir, retire_reason):
                    retired_count += 1
                    print(f"     → 已出库: {fid}.json → _retired/")

    # 结果汇总
    result = {
        "market": market,
        "total": len(factors),
        "passed": len(passed),
        "failed": len(failed),
        "retired": retired_count,
        "details": {"passed": passed, "failed": failed},
    }

    print("\n  ─── 质检结果 ───")
    print(f"  总计: {result['total']}")
    print(f"  合格: {result['passed']}")
    print(f"  不合格: {result['failed']}")
    print(f"  出库: {result['retired']}")

    return result


def save_report(all_results: list[dict], output_dir: Path) -> Path:
    """保存综合质检报告。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"elite_quality_inspection_{timestamp}.md"

    lines = [
        "# 精英因子全员质量巡检报告",
        "",
        f"> 生成时间: {datetime.now(timezone.utc).isoformat()}",
        "> 质检工具: HighICScreener (v2.53.0)",
        "> 最低总分门槛: 60.0 (B 级下限)",
        "",
        "---",
        "",
        "## 综合统计",
        "",
        "| 市场 | 总计 | 合格 | 不合格 | 出库 |",
        "|:-----|:-----|:-----|:-------|:-----|",
    ]

    total_all = total_pass = total_fail = total_retire = 0
    for r in all_results:
        lines.append(f"| {r['market'].upper()} | {r['total']} | {r['passed']} | {r['failed']} | {r['retired']} |")
        total_all += r["total"]
        total_pass += r["passed"]
        total_fail += r["failed"]
        total_retire += r["retired"]

    lines.append(f"| **合计** | **{total_all}** | **{total_pass}** | **{total_fail}** | **{total_retire}** |")
    lines.append("")

    for r in all_results:
        if not r["details"]["failed"]:
            continue
        lines.append(f"## {r['market'].upper()} 不合格因子明细")
        lines.append("")
        lines.append("| 因子ID | 因子名 | 总分 | 评级 | 处置 | 否决理由 |")
        lines.append("|:-------|:-------|:-----|:-----|:-----|:----------|")
        for f in r["details"]["failed"]:
            veto = "; ".join(f["veto_reasons"]) if f["veto_reasons"] else "-"
            lines.append(
                f"| {f['factor_id']} | {f['factor_name']} | {f['total_score']:.1f} | {f['grade']} | {f['disposition']} | {veto} |"
            )
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📄 质检报告已保存: {report_path}")
    return report_path


# ─── 主入口 ──────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="精英因子全员质量巡检")
    parser.add_argument("--dry-run", action="store_true", help="仅展示结果，不执行出库")
    parser.add_argument("--market", choices=["stock", "futures", "all"], default="all", help="质检市场（默认 all）")
    parser.add_argument("--min-score", type=float, default=60.0, help="最低总分门槛（默认 60.0，即 B 级下限）")
    args = parser.parse_args()

    markets = ["stock", "futures"] if args.market == "all" else [args.market]

    all_results: list[dict] = []
    for market in markets:
        elite_dir = ELITE_DIRS.get(market)
        if elite_dir is None or not elite_dir.exists():
            print(f"⚠️  {market} 精英目录不存在: {elite_dir}")
            continue
        result = run_inspection(
            market=market,
            elite_dir=elite_dir,
            dry_run=args.dry_run,
            min_score=args.min_score,
        )
        all_results.append(result)

    # 保存报告
    output_dir = _PROJECT_ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = save_report(all_results, output_dir)

    # 汇总
    total_all = sum(r["total"] for r in all_results)
    total_pass = sum(r["passed"] for r in all_results)
    total_fail = sum(r["failed"] for r in all_results)
    total_retire = sum(r["retired"] for r in all_results)

    print(f"\n{'=' * 60}")
    print("  精英因子全员质量巡检完成")
    print(f"  总计: {total_all} | 合格: {total_pass} | 不合格: {total_fail} | 出库: {total_retire}")
    print(f"  报告: {report_path}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
