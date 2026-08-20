"""
scripts/collect_specific_fields.py — 品种特有字段采集脚本（GAP-162 骨架，v3.0.0+16）

从外部数据源采集注册表中的品种特有字段（如 SC0 中东原油到岸贴水），
写入 {cache_dir}/{symbol}.parquet（date + 字段列，与 kline 面板 date 对齐），
供 enrich_specific_fields 按启用清单注入因子面板。

数据源：
  - manual：从本地 csv/json 导入（--input，列 date + 字段值），幂等 upsert；
  - placeholder：真实数据源（AKShare/iFinD 等）接入前的占位报告（dry-run 只报告
    注册表启用清单与缺失数据，不产生数据文件）。
真实行情源接入（需外部授权）在 placeholder 源位置扩展。

用法：
  python scripts/collect_specific_fields.py --dry-run
  python scripts/collect_specific_fields.py --source manual --field SC0.sc_freight_premium --input data/sc_premium.csv
  python scripts/collect_specific_fields.py --source manual --symbols SC0 --input data/sc_premium.csv --out-dir memory/cache/specific_fields

幂等：同 (symbol, date) 以新值覆盖；写入 parquet 前 date 去重。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _trace_id() -> str:
    return f"fts.specific_fields.collect_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def load_registry(path: Optional[str] = None) -> dict[str, dict[str, dict[str, Any]]]:
    """读取 specific_fields 注册表（复用 scope_domain 加载器）。"""
    from fts.factor_engine.scope_domain.specific_fields import load_specific_fields

    return load_specific_fields(path)


def _parse_manual_input(input_path: Path, field: str) -> list[tuple[str, float]]:
    """解析 manual 源（csv: date,<field> 或 json: [{date, <field>}]）。

    Returns:
        [(date_str, value), ...]；空/无效 → []。
    """
    rows: list[tuple[str, float]] = []
    if input_path.suffix.lower() == ".json":
        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
            for rec in data if isinstance(data, list) else []:
                d = rec.get("date")
                v = rec.get(field)
                if d and v is not None:
                    rows.append((str(d), float(v)))
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("[%s] JSON 解析失败: %s", input_path.name, e)
    else:  # csv
        try:
            import csv

            with input_path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    d, v = row.get("date"), row.get(field)
                    if d and v is not None and v != "":
                        try:
                            rows.append((str(d), float(v)))
                        except ValueError:
                            continue
        except OSError as e:
            logger.warning("[%s] CSV 读取失败: %s", input_path.name, e)
    return rows


def upsert_parquet(out_dir: Path, symbol: str, rows: list[tuple[str, float]], field: str) -> int:
    """幂等写入 {symbol}.parquet（date 去重 upsert，date 升序）。

    Returns:
        生效行数（新增/更新）。
    """
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    pf = out_dir / f"{symbol}.parquet"
    df_new = pd.DataFrame({"date": pd.to_datetime([r[0] for r in rows]), field: [r[1] for r in rows]})
    df_new = df_new.drop_duplicates(subset="date", keep="last").sort_values("date")

    n_eff = 0
    if pf.exists():
        try:
            df_old = pd.read_parquet(pf)
            if "date" in df_old.columns:
                df_old["date"] = pd.to_datetime(df_old["date"])
                merged = pd.concat([df_old, df_new]).drop_duplicates(subset="date", keep="last")
                before = len(df_old)
                merged = merged.sort_values("date")
                n_eff = len(merged) - before + df_new.duplicated(subset="date").sum() - (
                    0 if not df_old.empty else 0
                )
                # 简化口径：生效 = 新增日期数 + 更新日期数
                n_eff = len(merged[merged["date"].isin(df_new["date"])])
                merged.to_parquet(pf, index=False)
                return n_eff
        except Exception as e:  # noqa: BLE001 — 旧文件损坏则重建
            logger.warning("[%s] 旧 parquet 读取失败（重建）: %s", pf.name, e)
    df_new.to_parquet(pf, index=False)
    n_eff = len(df_new)
    return n_eff


def main() -> int:
    parser = argparse.ArgumentParser(description="品种特有字段采集（GAP-162 骨架）")
    parser.add_argument("--source", default="placeholder", choices=["manual", "placeholder"],
                        help="数据源：manual=本地 csv/json 导入；placeholder=真实源接入前占位报告")
    parser.add_argument("--field", default="", help="字段（如 SC0.sc_freight_premium）；空=处理全部启用字段")
    parser.add_argument("--symbols", nargs="+", default=None, help="限定品种")
    parser.add_argument("--input", default=None, help="manual 源文件（csv: date,<field> / json）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 memory/cache/specific_fields）")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    trace = _trace_id()
    logger.info("[%s] 启动 source=%s dry_run=%s", trace, args.source, args.dry_run)

    reg = load_registry()
    active = {
        sym: {f: m for f, m in items.items() if bool(m.get("enabled"))}
        for sym, items in reg.items()
    }
    if args.symbols:
        active = {s: active[s] for s in args.symbols if s in active}
    if args.field:
        sym, _, f = args.field.partition(".")
        if sym in active and f in active[sym]:
            active = {sym: {f: active[sym][f]}}
        else:
            logger.error("[%s] 字段 %s 不存在或未启用", trace, args.field)
            return 2
    if not active:
        logger.warning("[%s] 无启用字段（注册表为空或未配置 enabled=true）", trace)
        return 0

    logger.info("[%s] 待采集字段: %s", trace, {s: list(fs) for s, fs in active.items()})

    if args.source == "placeholder":
        # 真实数据源接入位（需外部授权）：当前仅报告启用清单与缺失数据
        logger.warning(
            "[%s] placeholder 源：真实数据源接入待授权（GAP-162），无数据产出；"
            "请使用 --source manual --input <csv/json> 导入", trace,
        )
        return 0

    # manual 源
    if not args.input:
        logger.error("[%s] manual 源必须提供 --input", trace)
        return 2
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("[%s] 输入文件不存在: %s", trace, input_path)
        return 2
    out_dir = Path(args.out_dir or "memory/cache/specific_fields")
    total = 0
    for sym, fields in active.items():
        for field in fields:
            rows = _parse_manual_input(input_path, field)
            if not rows:
                logger.warning("[%s] %s.%s 无有效数据行（跳过）", trace, sym, field)
                continue
            if args.dry_run:
                logger.info("[%s] [dry-run] %s.%s 将写入 %d 行 → %s/%s.parquet",
                            trace, sym, field, len(rows), out_dir, sym)
                total += len(rows)
                continue
            n = upsert_parquet(out_dir, sym, rows, field)
            logger.info("[%s] %s.%s 写入 %d 行（生效 %d）→ %s/%s.parquet",
                        trace, sym, field, len(rows), n, out_dir, sym)
            total += n
    logger.info("[%s] 完成：共 %d 行", trace, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
