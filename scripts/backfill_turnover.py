"""backfill_turnover.py — G11 换手回填与校准（35-gap-closure-plan §5.4 / §9.1）。

背景：横截面评估路径（cross_section_evaluate_backtest）曾硬编码 turnover_monthly=0.0，
导致库中绝大多数因子换手缺失（见 08-gap-analysis GAP-109 遗留）。本脚本：

1. 用统一期货横截面面板（复用 `_prepare_futures_data`，真实数据源降级链）对
   factor_catalog_futures.duckdb 中 **active** 因子重算日换手：
     turnover_daily   = 时间轴信号翻转率 mean(|Δsign|)/2（G11 口径，与 sign_flip_rate 同源）
     turnover_monthly = turnover_daily × 42（= mean(|Δsign|) × 21）
2. 回填 factor_catalog.turnover_monthly 与 factor_evaluations.level_1_turnover
   （仅覆盖 0 值或按 --force 全量覆盖；写入走 duckdb_write_lock 短连接，E.4 S1 合规）
3. 输出分位数分布（P25/P50/P75/P90/P95）与候选阈值通过率，供
   FTSConfig.factor_turnover_daily_max 定值（禁止直接套外部硬值，35 计划 §9）。

用法:
    python scripts/backfill_turnover.py [--db-path data/factor_catalog_futures.duckdb]
        [--days 200] [--max-symbols 30] [--dry-run] [--force] [--out <报告路径>]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

logger = logging.getLogger("backfill_turnover")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "factor_catalog_futures.duckdb"
TRADING_DAYS_PER_MONTH = 21
CANDIDATE_DAILY_TURNOVER: tuple[float, ...] = (0.10, 0.15, 0.20, 0.25, 0.30)


def _percentiles(values: np.ndarray, ps: tuple[float, ...] = (25.0, 50.0, 75.0, 90.0, 95.0)) -> dict[str, float]:
    if values.size == 0:
        return {f"p{int(p)}": float("nan") for p in ps}
    return {f"p{int(p)}": float(np.percentile(values, p)) for p in ps}


def _pass_rate(values: np.ndarray, threshold: float) -> tuple[int, int, float]:
    """日换手 ≤ 阈值通过率。返回 (通过数, 总数, 比率)。"""
    if values.size == 0:
        return 0, 0, 0.0
    passed = int((values <= threshold).sum())
    return passed, int(values.size), passed / values.size


def _load_active_factors(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT factor_id, name, code, params, signature, economic_logic
        FROM factor_catalog
        WHERE market = 'futures' AND status = 'active'
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for fid, name, code, params, signature, economic_logic in rows:
        if not code:
            continue
        sig = json.loads(signature) if isinstance(signature, str) else (signature or {})
        eco = json.loads(economic_logic) if isinstance(economic_logic, str) else (economic_logic or {})
        p = json.loads(params) if isinstance(params, str) else (params or {})
        out.append(
            {
                "factor_id": fid,
                "name": name,
                "code": code,
                "params": p or {},
                "signature": sig,
                "economic_logic": eco,
            }
        )
    return out


def _recompute_turnover(factor: dict[str, Any], panel: dict[str, Any], dates: Any) -> tuple[float, float, str]:
    """对单因子重算日换手/月度换手（横截面口径）。返回 (daily, monthly, status)。"""
    from fts.factor_engine.contracts import EconomicLogic, FactorSignature
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
    from fts.factor_engine.factor_program import create_factor_program

    sig = factor["signature"]
    signature = FactorSignature(
        input_fields=sig.get("input_fields") or ["close"],
        output_type=sig.get("output_type", "signal"),
        frequency=sig.get("frequency", "daily"),
        lookback=int(sig.get("lookback") or 5),
    )
    eco = factor["economic_logic"]
    economic_logic = EconomicLogic(
        theory=int(eco.get("theory", 3)),
        behavioral=int(eco.get("behavioral", 3)),
        microstructure=int(eco.get("microstructure", 3)),
        institutional=int(eco.get("institutional", 3)),
        narrative=eco.get("narrative", "回填重算"),
    )
    try:
        prog = create_factor_program(
            name=factor["name"],
            code=factor["code"],
            params=factor["params"],
            signature=signature,
            economic_logic=economic_logic,
        )
    except Exception as e:  # noqa: BLE001
        return 0.0, 0.0, f"program_error:{type(e).__name__}"
    try:
        bt = cross_section_evaluate_backtest(prog, panel, dates)
    except Exception as e:  # noqa: BLE001 — 单因子失败不阻断整批
        return 0.0, 0.0, f"eval_error:{type(e).__name__}"
    daily = float(bt.get("turnover_daily", 0.0) or 0.0)
    monthly = float(bt.get("turnover_monthly", 0.0) or 0.0)
    status = "ok" if daily > 0 else ("constant_or_empty" if "turnover_daily" in bt else "empty_metrics")
    return daily, monthly, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G11 换手回填与校准：对 active 期货因子重算日换手并回填")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB), help="因子库 DuckDB 路径")
    parser.add_argument("--days", type=int, default=200, help="横截面面板回溯天数")
    parser.add_argument("--max-symbols", type=int, default=30, help="横截面面板品种数")
    parser.add_argument("--dry-run", action="store_true", help="只统计不回填")
    parser.add_argument("--force", action="store_true", help="全量覆盖库中 turnover（默认仅覆盖 0 值）")
    parser.add_argument("--out", type=str, default="", help="报告输出路径（缺省 reports/gap/turnover_backfill_<date>.md）")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error("数据库不存在: %s", db_path)
        return 1

    # ── 面板构建（复用 CLI 期货横截面面板，真实数据源降级链；面板 pickle 缓存复用） ──
    t0 = time.time()
    import pickle

    cache_path = PROJECT_ROOT / "memory" / "cache" / f"cross_section_panel_{args.days}_{args.max_symbols}.pkl"
    panel: dict[str, Any] | None = None
    common_dates: Any = None
    if cache_path.exists():
        try:
            with open(cache_path, "rb") as _f:
                panel, common_dates = pickle.load(_f)
            logger.info("复用面板缓存: %s", cache_path)
        except Exception:  # noqa: BLE001
            panel = None
    if panel is None:
        from fts.cli import _prepare_futures_data

        logger.info("构建期货横截面面板: days=%d, max_symbols=%d ...", args.days, args.max_symbols)
        panel, common_dates, _fwd = _prepare_futures_data(days=args.days, max_symbols=args.max_symbols)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as _f:
            pickle.dump((panel, common_dates), _f)
        logger.info("面板完成: symbols=%d, dates=%d (%.1fs)", len(panel), len(common_dates), time.time() - t0)

    # ── 读取 active 因子 ──
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        factors = _load_active_factors(conn)
    finally:
        conn.close()
    logger.info("active 因子数: %d", len(factors))
    if not factors:
        logger.error("无 active 因子可回填")
        return 1

    # ── 重算 turnover ──
    results: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for i, f in enumerate(factors, 1):
        daily, monthly, status = _recompute_turnover(f, panel, common_dates)
        status_counts[status] = status_counts.get(status, 0) + 1
        results.append(
            {
                "factor_id": f["factor_id"],
                "name": f["name"],
                "turnover_daily": daily,
                "turnover_monthly": monthly,
            }
        )
        if i % 20 == 0:
            logger.info("进度 %d/%d", i, len(factors))

    values = np.asarray([r["turnover_daily"] for r in results], dtype=float)
    values = values[np.isfinite(values)]
    logger.info("状态分类: %s", status_counts)
    logger.info("=== 日换手分布（n=%d） ===", int(values.size))
    for k, v in _percentiles(values).items():
        logger.info("  %s=%.4f", k, v)
    for t in CANDIDATE_DAILY_TURNOVER:
        p, n, rate = _pass_rate(values, t)
        logger.info("  日换手≤%.2f 通过率: %d/%d = %.1f%%", t, p, n, rate * 100)

    # ── 回填 ──
    if not args.dry_run:
        from fts.store.duckdb_lock import duckdb_write_lock

        with duckdb_write_lock(db_path):
            w = duckdb.connect(str(db_path))
            try:
                updated = 0
                for r in results:
                    if r["turnover_monthly"] <= 0:
                        continue
                    if not args.force:
                        cur = w.execute(
                            "SELECT turnover_monthly FROM factor_catalog WHERE factor_id = ?", [r["factor_id"]]
                        ).fetchone()
                        if cur is not None and float(cur[0] or 0.0) > 0:
                            continue
                    w.execute(
                        "UPDATE factor_catalog SET turnover_monthly = ? WHERE factor_id = ?",
                        [r["turnover_monthly"], r["factor_id"]],
                    )
                    w.execute(
                        "UPDATE factor_evaluations SET level_1_turnover = ? WHERE factor_id = ?",
                        [r["turnover_monthly"], r["factor_id"]],
                    )
                    updated += 1
                w.close()
                logger.info("回填完成: %d 个因子更新 turnover（dry-run=%s, force=%s）", updated, args.dry_run, args.force)
            except Exception:
                w.close()
                raise

    # ── 落盘报告 ──
    out_path = (
        Path(args.out)
        if args.out
        else PROJECT_ROOT / "reports" / "gap" / f"turnover_backfill_{__import__('datetime').date.today():%Y%m%d}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pct = _percentiles(values)
    cand_rows = "".join(
        f"| ≤{t} | {_pass_rate(values, t)[0]} | {_pass_rate(values, t)[2]:.1%} |\n" for t in CANDIDATE_DAILY_TURNOVER
    )
    pct_rows = "".join(f"| {k} | {v:.4f} |\n" for k, v in pct.items())
    md = f"""# G11 换手回填与校准报告（35-gap-closure-plan §5.4 / §9.1）

- 数据库: `{db_path}`
- 面板: days={args.days}, max_symbols={args.max_symbols}（真实数据源降级链）
- active 因子数: {len(factors)}，成功重算: {int(values.size)}
- dry-run: {args.dry_run}
- 状态分类: {status_counts}

## 日换手分布（turnover_daily = 时间轴信号翻转率 mean(|Δsign|)/2）

| 分位 | 值 |
|---|---|
{pct_rows}
## 候选阈值通过率（日换手 ≤ 阈值通过）

| 阈值 | 通过数 | 通过率 |
|---|---|---|
{cand_rows}
## 结论

> 阈值定值由人工依据上述分布决定，禁止直接套用外部硬值（35-gap-closure-plan.md §9）。
> 定值后回填 35-gap-closure-plan.md §5.4（turnover_daily_max）与
> FTSConfig.factor_turnover_daily_max（config/settings.py 默认值）并登记 08-gap-analysis.md。
"""
    out_path.write_text(md, encoding="utf-8")
    logger.info("报告已写入: %s", out_path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    raise SystemExit(main())
