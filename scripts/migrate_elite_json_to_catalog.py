"""P1 因子资产入库：elite JSON → DuckDB catalog 差量补齐 + 一致性校验（plans/29 §4 Phase 1）

背景:
    FTS 历史演进中 elite 因子以「JSON 快照 + DuckDB catalog」双写并存（GAP-032）。
    部分归档/退役因子仅有 JSON 快照、未进入 catalog（对账缺口：stock 389 / futures 139）。
    本脚本将全部 elite JSON（active/_archive/_retired/_deprecated）差量补齐至
    factor_catalog_{stock,futures}.duckdb，使 DuckDB 成为完整单一事实源（SSOT），
    JSON 降级为只读快照。

模式:
    --dry-run      只扫描 + 预估，不写入（幂等预估：migrated=需补齐数）
    --verify-only  仅对 catalog 已有因子做逐字段一致性校验，不写入
    默认           差量补齐 + 校验

用法:
    python scripts/migrate_elite_json_to_catalog.py --market stock --dry-run
    python scripts/migrate_elite_json_to_catalog.py --market all
    python scripts/migrate_elite_json_to_catalog.py --market futures --verify-only --json

HARNESS: trace_id 全链路（fts.migrate_elite.{market}.{ts}）；幂等可重入；失败透明。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

# 脚本独立运行时的导入引导（项目惯用法，ruff E402 豁免）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.factor_engine.factor_db.migrate_from_json import extract_evaluation_metrics, parse_factor_json  # noqa: E402
from fts.factor_engine.factor_db.repository import FactorRepository  # noqa: E402
from fts.factor_engine.factor_db.schema import get_db_path  # noqa: E402

logger = logging.getLogger("migrate_elite_json_to_catalog")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ELITE_DIRS: dict[str, Path] = {
    "stock": PROJECT_ROOT / "memory" / "knowledge" / "factors" / "stocks_elite",
    "futures": PROJECT_ROOT / "memory" / "knowledge" / "factors" / "futures_elite",
}

# 归档子目录 → catalog status 映射（JSON 显式 status 字段优先，缺省按子目录）
SUBDIR_STATUS: dict[str, str] = {
    "_archive": "archived",
    "_retired": "retired",
    "_deprecated": "deprecated",
}


def scan_elite_jsons(elite_dir: Path) -> list[tuple[Path, str]]:
    """扫描 elite 目录下全部因子 JSON（含归档子目录），返回 (文件路径, status)。

    以 _ 开头的元数据文件（_elite_index.json 等）跳过。
    """
    found: list[tuple[Path, str]] = []
    for status, sub in (("active", ""),):
        base = elite_dir if sub == "" else elite_dir / sub
        if base.exists():
            for fp in sorted(base.glob("*.json")):
                if fp.name.startswith("_"):
                    continue
                found.append((fp, status))
    for sub, status in SUBDIR_STATUS.items():
        subdir = elite_dir / sub
        if subdir.exists():
            for fp in sorted(subdir.glob("*.json")):
                if fp.name.startswith("_"):
                    continue
                found.append((fp, status))
    return found


def build_factor_dict(data: dict[str, Any], market: str, status: str) -> dict[str, Any]:
    """按 _write_to_duckdb 语义构建 factor_catalog 写入字典。

    对齐字段：ic/sharpe 等指标取自 evaluation.level_1_backtest；market 强制目标市场
    （JSON 显式 multi/other 或缺失时用目标市场，避免因子入错库）；is_elite 恒 True。
    """
    metrics = extract_evaluation_metrics(data)
    factor_market = str(data.get("market", "multi"))
    if factor_market in ("multi", "other"):
        factor_market = market
    return {
        "factor_id": str(data.get("factor_id", "")),
        "name": str(data.get("name", "")),
        "code": str(data.get("code", "")),
        "params": data.get("params", {}),
        "signature": data.get("signature", {}),
        "economic_logic": data.get("economic_logic", {}),
        "source": str(data.get("source", "seed")),
        "parent_id": data.get("parent_id"),
        "generation": int(data.get("generation", 0) or 0),
        "trace_id": str(data.get("trace_id", "")),
        "market": factor_market,
        "family": str(data.get("family") or "other"),
        "is_elite": True,
        "status": str(data.get("status", status)),
        "sharpe": float(metrics["sharpe"] or 0.0),
        "ic": float(metrics["ic"] or 0.0),
        "icir": float(metrics["icir"] or 0.0),
        "max_drawdown": float(metrics["max_drawdown"] or 0.0),
        "turnover_monthly": float(metrics["turnover_monthly"] or 0.0),
        "decay_6m": float(data.get("decay_6m", 0.05) or 0.05),
        "metadata": {
            "evaluation": data.get("evaluation"),
            "correlation_metadata": data.get("correlation_metadata", {}),
            "promoted_at": data.get("promoted_at"),
        },
        "style_tags": data.get("style_tags") or [],
    }


def build_eval_dict(data: dict[str, Any]) -> dict[str, Any]:
    """构建 factor_evaluations 写入字典（对齐 _write_to_duckdb eval_dict 字段）。"""
    metrics = extract_evaluation_metrics(data)
    return {
        "trace_id": data.get("evaluation", {}).get("trace_id"),
        "ic": metrics["ic"],
        "icir": metrics["icir"],
        "sharpe": metrics["sharpe"],
        "max_drawdown": metrics["max_drawdown"],
        "turnover": metrics["turnover_monthly"],
        "t_stat": metrics["t_stat"],
        "monotonicity": metrics["monotonicity"],
        "oos_ratio": metrics["oos_ratio"],
        "theory_score": metrics["l2_theory"],
        "behavioral_score": metrics["l2_behavioral"],
        "microstructure_score": metrics["l2_microstructure"],
        "institutional_score": metrics["l2_institutional"],
        "dims_passed": metrics["l2_dims_passed"],
        "bonferroni_p": metrics["l3_bonferroni_p"],
        "fdr_q": metrics["l3_fdr_q"],
        "effective_n": metrics["l3_effective_n"],
        "adjusted_t": metrics["l3_adjusted_t"],
        "l3_passed": metrics["l3_passed"],
        "overall_passed": metrics["overall_passed"],
        "failure_reasons": metrics["failure_reasons"],
        "evaluated_at": data.get("evaluation", {}).get("evaluated_at"),
    }


# 一致性校验字段（JSON 侧取值函数 + 容差）
_FLOAT_TOL = 1e-6


def _jf(data: dict[str, Any]) -> dict[str, Any]:
    return data


def verify_factor(row: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """逐字段校验 catalog 行与 JSON 源的一致性，返回差异列表（空 = 一致）。"""
    issues: list[str] = []
    if row.get("name") != data.get("name"):
        issues.append(f"name: catalog={row.get('name')!r} vs json={data.get('name')!r}")
    if row.get("code") != data.get("code"):
        issues.append("code: 不一致")
    for col, key in (("params", "params"), ("signature", "signature"), ("economic_logic", "economic_logic")):
        if (row.get(col) or {}) != (data.get(key) or {}):
            issues.append(f"{col}: 不一致")
    if row.get("source") != data.get("source", "seed"):
        issues.append(f"source: catalog={row.get('source')!r} vs json={data.get('source')!r}")
    if int(row.get("generation") or 0) != int(data.get("generation", 0) or 0):
        issues.append(f"generation: catalog={row.get('generation')} vs json={data.get('generation')}")
    metrics = extract_evaluation_metrics(data)
    for col, val in (
        ("ic", metrics["ic"]),
        ("sharpe", metrics["sharpe"]),
        ("icir", metrics["icir"]),
    ):
        row_val = float(row.get(col) or 0.0)
        if abs(row_val - float(val or 0.0)) > _FLOAT_TOL:
            issues.append(f"{col}: catalog={row_val} vs json={float(val or 0.0)}")
    return issues


def _catalog_ids_readonly(db_file: Path) -> set[str]:
    """只读获取既有 catalog 因子 ID 集合（库不存在/打不开返回空）。"""
    import duckdb

    if not db_file.exists():
        return set()
    try:
        con = duckdb.connect(str(db_file), read_only=True)
        try:
            return {r[0] for r in con.execute("SELECT factor_id FROM factor_catalog").fetchall()}
        finally:
            con.close()
    except Exception:
        return set()


def migrate_market(
    market: str,
    elite_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
    verify_only: bool = False,
    sync: bool = False,
    trace_id: str = "",
) -> dict[str, Any]:
    """执行单个市场（stock/futures）的差量补齐 + 一致性校验。

    Args:
        market: "stock" / "futures"
        elite_dir / db_path: 覆盖默认路径（测试隔离用）
        dry_run: 只读预估缺口，不写入
        verify_only: 仅校验既有记录一致性，不写入
        sync: 既有因子不一致时以 JSON 为准同步 catalog 内容字段
              （JSON 为更新权威——漂移源于 JSON 在 catalog 之后被加固/更新；
               不修改 status 等 lifecycle 字段，避免覆盖退役/删除状态）

    Returns:
        统计字典: total_files/migrated/skipped/synced/verified/field_mismatches/
                  failed/orphans/mismatch_samples/errors
    """
    elite_path = Path(elite_dir) if elite_dir else DEFAULT_ELITE_DIRS[market]
    db_file = Path(db_path) if db_path else get_db_path(market)
    if not elite_path.exists():
        raise FileNotFoundError(f"精英因子目录不存在: {elite_path}")

    stats: dict[str, Any] = {
        "market": market,
        "elite_dir": str(elite_path),
        "db_path": str(db_file),
        "trace_id": trace_id,
        "total_files": 0,
        "migrated": 0,
        "skipped": 0,
        "synced": 0,
        "verified": 0,
        "field_mismatches": 0,
        "failed": 0,
        "orphans": [],
        "mismatch_samples": [],
        "errors": [],
    }

    files = scan_elite_jsons(elite_path)
    stats["total_files"] = len(files)

    repo: FactorRepository | None = None
    if dry_run:
        # dry-run：只读比对既有 catalog，预估真实缺口（不写入）
        catalog_ids = _catalog_ids_readonly(db_file)
        for fp, _status in files:
            data = parse_factor_json(fp)
            if data is None:
                stats["failed"] += 1
                continue
            fid = str(data.get("factor_id", fp.stem))
            if fid in catalog_ids:
                stats["skipped"] += 1
            else:
                stats["migrated"] += 1
        json_ids = {fp.stem for fp, _ in files}
        stats["orphans"] = sorted(catalog_ids - json_ids)
        logger.info(
            "[%s] dry-run 预估：需补齐 %d / 已存在 %d / 孤儿 %d",
            trace_id,
            stats["migrated"],
            stats["skipped"],
            len(stats["orphans"]),
        )
        return stats
    repo = FactorRepository(db_file, market=market)

    try:
        # PK 唯一性全局判定：全量 factor_id（list_factors 默认 limit=100 会漏行导致
        # 重复插入主键冲突；且不能用 market 过滤——既有因子可能 market='multi'）
        result = repo._execute("SELECT factor_id FROM factor_catalog")
        catalog_ids = {r[0] for r in result.fetchall()}
    except Exception as e:
        logger.warning("查询既有 catalog 失败: %s", e)
        catalog_ids = set()
    migrated_ids: set[str] = set()

    for fp, status in files:
        data = parse_factor_json(fp)
        if data is None:
            stats["failed"] += 1
            stats["errors"].append(f"{fp.name}: 解析失败")
            continue
        factor_id = str(data.get("factor_id", fp.stem))
        if factor_id in catalog_ids:
            stats["skipped"] += 1
            # 既有记录：正常模式 = 校验并报告漂移；--sync 时以 JSON 为准同步内容字段
            row = repo.get_factor(factor_id)
            if not row:
                continue
            issues = verify_factor(row, data)
            if issues:
                if sync:
                    # 以 JSON 为准同步 catalog 内容字段（不触碰 status 等 lifecycle 字段）
                    sync_dict = build_factor_dict(data, market, status)
                    sync_dict.pop("factor_id", None)
                    sync_dict.pop("status", None)
                    repo.update_factor(factor_id, sync_dict)
                    stats["synced"] += 1
                    row2 = repo.get_factor(factor_id)
                    remaining = verify_factor(row2, data) if row2 else issues
                    if remaining:
                        stats["field_mismatches"] += 1
                        stats["mismatch_samples"].append({"factor_id": factor_id, "issues": remaining[:5]})
                    else:
                        stats["verified"] += 1
                        logger.debug("[%s] 已同步: %s", trace_id, factor_id)
                else:
                    stats["field_mismatches"] += 1
                    stats["mismatch_samples"].append({"factor_id": factor_id, "issues": issues[:5]})
            else:
                stats["verified"] += 1
            continue

        # ── 差量补齐：catalog 缺失 → 创建因子 + 评估记录 ──
        if verify_only:
            # verify-only 模式只校验既有记录，缺失因子不计入写入
            stats["skipped"] += 1
            continue
        try:
            factor_dict = build_factor_dict(data, market, status)
            factor_dict["factor_id"] = factor_id
            factor_dict["name"] = str(data.get("name", fp.stem))
            repo.create_factor(factor_dict)
            # update_catalog_status=False：归档/退役因子的 lifecycle 状态不被评估覆盖
            repo.add_evaluation(factor_id, build_eval_dict(data), update_catalog_status=False)
            stats["migrated"] += 1
            migrated_ids.add(factor_id)
            catalog_ids.add(factor_id)
            logger.debug("[%s] 已补齐: %s (status=%s)", trace_id, factor_id, factor_dict["status"])
        except Exception as e:
            stats["failed"] += 1
            stats["errors"].append(f"{fp.name}: 写入失败 — {e}")
            logger.warning("[%s] 写入失败 %s: %s", trace_id, fp.name, e)

    # ── 孤儿报告：catalog 有但 JSON 无 ──
    json_ids = {fp.stem for fp, _ in files}
    stats["orphans"] = sorted(catalog_ids - json_ids)

    if not verify_only:
        # 复核：仅对新迁移（补齐）因子做一致性复核（既有因子已在首轮校验/同步）
        for fp, status in files:
            data = parse_factor_json(fp)
            if data is None:
                continue
            factor_id = str(data.get("factor_id", fp.stem))
            if factor_id not in migrated_ids:
                continue
            row = repo.get_factor(factor_id)
            if row:
                issues = verify_factor(row, data)
                if issues:
                    stats["field_mismatches"] += 1
                    stats["mismatch_samples"].append({"factor_id": factor_id, "issues": issues[:5]})
                else:
                    stats["verified"] += 1

    if repo is not None:
        repo.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 因子资产入库：elite JSON → DuckDB catalog 差量补齐 + 校验")
    parser.add_argument("--market", choices=["stock", "futures", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="仅预估需补齐数，不写入")
    parser.add_argument("--verify-only", action="store_true", help="仅校验已存在因子一致性，不写入")
    parser.add_argument(
        "--sync", action="store_true", help="既有因子不一致时以 JSON 为准同步 catalog 内容字段（默认仅报告）"
    )
    parser.add_argument("--elite-dir", default="", help="覆盖 elite 目录（默认按市场内置路径）")
    parser.add_argument("--db-path", default="", help="覆盖 DuckDB 路径（默认 get_db_path(market)）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    markets = ["stock", "futures"] if args.market == "all" else [args.market]
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    all_stats: dict[str, Any] = {"markets": {}, "total_migrated": 0, "total_synced": 0, "total_field_mismatches": 0}

    for market in markets:
        trace_id = f"fts.migrate_elite.{market}.{ts}"
        try:
            stats = migrate_market(
                market,
                elite_dir=args.elite_dir or None,
                db_path=args.db_path or None,
                dry_run=args.dry_run,
                verify_only=args.verify_only,
                sync=args.sync,
                trace_id=trace_id,
            )
            all_stats["markets"][market] = stats
            all_stats["total_migrated"] += stats["migrated"]
            all_stats["total_synced"] += stats.get("synced", 0)
            all_stats["total_field_mismatches"] += stats["field_mismatches"]
            logger.info(
                "[%s] %s: total=%d migrated=%d synced=%d skipped=%d verified=%d mismatches=%d failed=%d orphans=%d",
                trace_id,
                market,
                stats["total_files"],
                stats["migrated"],
                stats.get("synced", 0),
                stats["skipped"],
                stats["verified"],
                stats["field_mismatches"],
                stats["failed"],
                len(stats["orphans"]),
            )
        except Exception as e:
            logger.error("[%s] %s 迁移失败: %s", trace_id, market, e)
            traceback.print_exc()
            all_stats["markets"][market] = {"error": str(e)}

    if args.json:
        print(json.dumps(all_stats, ensure_ascii=False, indent=2))
    return 0 if all_stats["total_field_mismatches"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
