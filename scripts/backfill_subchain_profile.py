"""
scripts/backfill_subchain_profile.py — 一次性回填子链适用性画像（plans/47 §A2）

背景
----
子链张量化（plans/47+48+49）实施后，energy 库存量因子晋升时未写入
``subchain_scope`` / ``subchain_ic_profile`` / ``subchain_specific``（0/327 因子带画像），
导致 ``l3.subchain_weight`` 开关打开后无画像可消费（全部走 scope_default="all"=m=1.0 空转）。

本脚本从 elite 快照（``memory/knowledge/factors/energy_chain_elite/*.json``，
196 个，均含 ``evaluation.level_1_backtest.symbol_ic`` 逐品种 IC）读取 symbol_ic，
复用 ``subchain_profile.build_subchain_metadata`` 计算画像，写回 ``factor_catalog.metadata``；
同时以 source=promotion 写 ``subchain_factor_quality`` 质量矩阵首行（plans/49 §A2 张量底座）。

幂等性：重复执行同一 factor_id 时 build_subchain_metadata 输出一致，metadata 合并覆盖，
质量矩阵 UPSERT（主键 factor×market×chain×evaluated_at）→ 可安全重跑。

用法（Powershell7）:
    & 'C:/Program Files/Python312/python.exe' scripts/backfill_subchain_profile.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 项目根（允许从任意 cwd 执行，遵循 5.9 禁止绝对路径硬编码）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_subchain_profile")

ELITE_DIR = PROJECT_ROOT / "memory" / "knowledge" / "factors" / "energy_chain_elite"


def load_symbol_ic(elite_fp: Path) -> dict[str, float] | None:
    """读取 elite 快照的逐品种 IC（evaluation.level_1_backtest.symbol_ic）。"""
    try:
        data = json.loads(elite_fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("elite JSON 读取失败 %s: %s", elite_fp.name, e)
        return None
    ev = (data.get("evaluation") or {}).get("level_1_backtest") or {}
    sic = ev.get("symbol_ic")
    if isinstance(sic, dict) and sic:
        return {str(k): float(v) for k, v in sic.items()}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 energy 因子子链适用性画像")
    parser.add_argument("--dry-run", action="store_true", help="仅统计将回填的因子，不写库")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个因子（0=全部）")
    args = parser.parse_args()

    if not ELITE_DIR.exists():
        logger.error("elite 目录不存在: %s", ELITE_DIR)
        return 1

    from fts.factor_engine.factor_db.repository import (
        FactorRepository,
        SubchainQualityRepository,
    )
    from fts.factor_engine.factor_db.schema import get_db_path, init_database
    from fts.factor_engine.subchain_profile import (
        build_subchain_metadata,
        build_subchain_quality_rows,
    )

    db_path = get_db_path("energy")
    # 幂等建表（含 subchain_factor_quality）：旧库文件已存在时 repository
    # 的 _get_conn 不会触发 init_database，schema 变更需显式迁移
    init_database(db_path)
    elites = sorted(ELITE_DIR.glob("*.json"))
    if args.limit > 0:
        elites = elites[: args.limit]

    updated = 0
    skipped = 0
    failed = 0
    quality_rows_total = 0

    repo = FactorRepository(market="energy", db_path=db_path)
    qrepo = SubchainQualityRepository(market="energy", db_path=db_path)
    try:
        for fp in elites:
            fid = None
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                fid = data.get("factor_id")
            except (json.JSONDecodeError, OSError):
                failed += 1
                continue
            sic = load_symbol_ic(fp)
            if not sic:
                logger.warning("跳过 %s（无 symbol_ic）", fp.name)
                skipped += 1
                continue

            meta_update = build_subchain_metadata(fid, sic)  # subchain_scope/ic_profile/specific
            if args.dry_run:
                logger.info(
                    "[dry-run] %s scope=%s specific=%s effective=%s",
                    fid,
                    meta_update["subchain_scope"],
                    meta_update["subchain_specific"],
                    {
                        c: st.get("effective")
                        for c, st in meta_update["subchain_ic_profile"].items()
                    },
                )
                updated += 1
                continue

            # 合并写回 metadata（保留原字段，只增补子链画像）
            row = repo.get_factor(fid)
            if not row:
                logger.warning("跳过 %s（catalog 无此因子）", fid)
                skipped += 1
                continue
            meta = dict(row.get("metadata") or {})
            meta.update(meta_update)
            repo.update_factor(fid, {"metadata": meta})

            # 质量矩阵首行（plans/49 §A2，source=promotion）
            qrows = build_subchain_quality_rows(
                fid, "energy", sic, source="promotion"
            )
            if qrows:
                qrepo.save_subchain_quality(qrows)
                quality_rows_total += len(qrows)

            updated += 1
            logger.info(
                "回填 %s scope=%s specific=%s quality_rows=%d",
                fid,
                meta_update["subchain_scope"],
                meta_update["subchain_specific"],
                len(qrows),
            )
    finally:
        repo.close()
        qrepo.close()

    logger.info(
        "完成: updated=%d skipped=%d failed=%d quality_rows=%d%s",
        updated,
        skipped,
        failed,
        quality_rows_total,
        " (dry-run)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
