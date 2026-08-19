"""一次性回填 energy elite 因子 Regime 适用性画像（plans/53 §A2）。

背景
----
2026-08-17 实盘灰度开启 `l3.regime_conditional` 前，存量 elite 因子（196 个）
`factor_catalog.metadata` 无 `regime_ic_profile`/`regime_scope`/`regime_dependent`
（评估链画像开关此前默认关），导致 B 模块消费时全部走 scope_default="all"=m=1.0
空转（与 backfill_subchain_profile.py 记录的同类教训一致）。

本脚本为存量因子回填 Regime 画像：
  1. 加载 energy 面板（ENERGY_CHAIN_SYMBOLS ∪ HOLDOUT，500 天）→ 合成 OHLCV
     → RegimeSeriesBuilder 构建制度序列；
  2. 逐因子**复用评估链** `cross_section_evaluate_backtest`（临时启用
     `l3_regime_ic_report_enabled`）——方向翻转 / oos 窗口 / 横截面语义与主链
     100% 一致（避免自实现信号口径漂移，对齐"组件执行参数必须与主链一致"）；
  3. 从评估产物取 `regime_ic_report` → 合并写回 factor_catalog.metadata（原 fid）。

幂等性：同一因子重复执行时评估确定性、画像输出一致，metadata 合并覆盖。

用法:
    & 'C:/Program Files/Python312/python.exe' scripts/backfill_regime_profile.py \
        [--dry-run] [--limit N] [--factor-id fct_xxx]

版本: v0.2.0（plans/53，v0.1.0 自实现信号口径漂移废弃——横截面均值信号未做
方向翻转/oos 窗口导致画像全 unknown，改用评估链保证口径一致）
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data import FTSDataProvider
from fts.data_futures import ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_SYMBOLS

logger = logging.getLogger("backfill_regime_profile")

LOOKBACK: int = 60  # 制度检测窗口（plans/53 §D 实测规则检测 60 天有区分力）


def _evaluate_regime_report(
    factor_row: dict[str, Any],
    panel: dict[str, Any],
    common_dates: Any,
    cfg: Any,
) -> Optional[dict[str, Any]]:
    """复用评估链产出 regime_ic_report（临时启用画像开关，口径与主链一致）。

    Args:
        factor_row: factor_catalog 行（含 factor_id/name/code/params/signature/economic_logic）
        panel: {symbol: DataFrame(OHLCV)}
        common_dates: 共同日期索引
        cfg: FTSConfig 实例（临时置 l3_regime_ic_report_enabled=True，用完还原）

    Returns:
        regime_ic_report（评估链产出）或 None（评估链未产出画像）。
    """
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
    from fts.factor_engine.factor_program import create_factor_program

    fid = factor_row.get("factor_id", "")
    sig = factor_row.get("signature") or {
        "input_fields": ["close"],
        "output_type": "signal",
        "frequency": "daily",
        "lookback": 10,
    }
    elogic = factor_row.get("economic_logic") or {
        "theory": 4,
        "behavioral": 3,
        "microstructure": 3,
        "institutional": 4,
        "narrative": "regime 画像回填",
    }
    fp = create_factor_program(
        name=factor_row.get("name", fid),
        code=factor_row.get("code", ""),
        params=factor_row.get("params") or {},
        signature=sig,
        economic_logic=elogic,
        source=factor_row.get("source", "manual"),
    )
    fp["factor_id"] = fid  # 保持原 factor_id（generate 的 id 仅评估用）

    orig = getattr(cfg, "l3_regime_ic_report_enabled", False)
    cfg.l3_regime_ic_report_enabled = True
    try:
        bt = cross_section_evaluate_backtest(fp, panel, common_dates)
    finally:
        cfg.l3_regime_ic_report_enabled = orig
    return bt.get("regime_ic_report")


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 energy elite 因子 Regime 适用性画像")
    parser.add_argument("--dry-run", action="store_true", help="仅统计将回填的因子，不写库")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个因子（0=全部）")
    parser.add_argument("--factor-id", default=None, help="仅回填指定 factor_id")
    args = parser.parse_args()

    from fts.config.settings import get_config
    from fts.factor_engine.factor_db.repository import FactorRepository
    from fts.factor_engine.regime_profile import RegimeSeriesBuilder

    cfg = get_config()

    # ── 1. 加载面板（评估链需要 panel + common_dates）──
    symbols = sorted(set(ENERGY_CHAIN_SYMBOLS) | set(ENERGY_CHAIN_HOLDOUT))
    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(symbols=symbols, days=500)
    if not panel or len(common_dates) < LOOKBACK + 20:
        logger.error("energy 面板加载不足（common_dates=%d），退出", len(common_dates))
        return 1

    # 预热制度序列（评估链每因子独立构建，此处仅预检数据可用性 + 日志）
    from fts.factor_engine.regime import SectorRegimeSelector

    ohlcv = SectorRegimeSelector._build_sector_ohlcv(panel, symbols)
    if ohlcv is None or ohlcv.empty:
        logger.error("合成 OHLCV 构建失败，退出")
        return 1
    probe_series = RegimeSeriesBuilder(lookback_days=LOOKBACK).build_from_ohlcv(ohlcv)
    if probe_series.empty:
        logger.error("制度序列构建失败，退出")
        return 1
    logger.info(
        "面板 %d 品种 × %d 交易日，制度序列 %d 点（%s）",
        len(panel), len(common_dates), len(probe_series), sorted(set(probe_series)),
    )

    # ── 2. 加载因子 ──
    repo = FactorRepository(market="energy")
    try:
        if args.factor_id:
            row = repo.get_factor(args.factor_id)
            rows = [row] if row else []
        else:
            rows = repo.list_factors(market="energy", status="active", is_elite=True, limit=10000)
        if args.limit > 0:
            rows = rows[: args.limit]
    finally:
        repo.close()
    if not rows:
        logger.error("无 energy elite 因子可回填，退出")
        return 1
    logger.info("待回填因子 %d 个", len(rows))

    # ── 3. 逐因子回填（复用评估链，口径一致）──
    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        fid = row.get("factor_id")
        try:
            if not row.get("code"):
                logger.warning("跳过 %s（无 code）", fid)
                skipped += 1
                continue
            report = _evaluate_regime_report(row, panel, common_dates, cfg)
            if not report:
                logger.warning("跳过 %s（评估链未产出 regime_ic_report）", fid)
                skipped += 1
                continue
            if args.dry_run:
                logger.info(
                    "[dry-run] %s scope=%s dependent=%s effective=%s",
                    fid,
                    report["regime_scope"],
                    report["regime_dependent"],
                    {r: st.get("effective") for r, st in report["regime_ic_profile"].items()},
                )
                updated += 1
                continue

            repo2 = FactorRepository(market="energy")
            try:
                cur = repo2.get_factor(fid)
                if not cur:
                    logger.warning("跳过 %s（catalog 无此因子）", fid)
                    skipped += 1
                    continue
                meta = dict(cur.get("metadata") or {})
                meta.update(report)
                repo2.update_factor(fid, {"metadata": meta})
            finally:
                repo2.close()
            updated += 1
            logger.info(
                "✅ %s scope=%s（effective=%s）",
                fid,
                report["regime_scope"],
                {r: st.get("effective") for r, st in report["regime_ic_profile"].items()},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("失败 %s: %s", fid, e)
            failed += 1

    logger.info(
        "回填完成: updated=%d skipped=%d failed=%d%s",
        updated, skipped, failed, "（dry-run）" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    raise SystemExit(main())

