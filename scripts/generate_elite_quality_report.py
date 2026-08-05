"""scripts/generate_elite_quality_report.py — 生成精英因子最终质量报告

从 DuckDB 查询所有 active elite 因子，生成 elite_final_quality.json，
记录实际进入组合的因子质量指标，与初始种子评测 (quality_ranking.json) 区分。

用法:
    python scripts/generate_elite_quality_report.py [--market futures|stock|all]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb


def generate_report(db_path: str, market: str = "all", output_dir: str | None = None) -> Path:
    """生成精英因子最终质量报告。

    Args:
        db_path: DuckDB 数据库路径
        market: 市场过滤
        output_dir: 输出目录 (默认: memory/portfolio/)

    Returns:
        输出文件路径
    """
    conn = duckdb.connect(db_path)

    query = """
        SELECT factor_id, name, ic, sharpe, turnover_monthly, decay_6m,
               market, is_elite, status, created_at, updated_at
        FROM factor_catalog
        WHERE status = 'active' AND is_elite = TRUE
    """
    params: list = []
    if market != "all":
        query += " AND market = ?"
        params.append(market)
    query += " ORDER BY market, ic DESC"

    rows = conn.execute(query, params).fetchall()
    columns = [desc[0] for desc in conn.description]

    factors = []
    for row in rows:
        record = dict(zip(columns, row))
        factors.append({
            "factor_id": record["factor_id"],
            "name": record["name"],
            "market": record["market"],
            "ic": round(record["ic"], 4) if record["ic"] is not None else None,
            "sharpe": round(record["sharpe"], 4) if record["sharpe"] is not None else None,
            "turnover_monthly": round(record["turnover_monthly"], 4) if record["turnover_monthly"] is not None else None,
            "decay_6m": round(record["decay_6m"], 4) if record["decay_6m"] is not None else None,
            "is_elite": record["is_elite"],
            "status": record["status"],
        })

    # 统计
    by_market: dict[str, list] = {}
    for f in factors:
        m = f["market"]
        by_market.setdefault(m, []).append(f)

    summary: dict[str, dict] = {}
    for m, fs in by_market.items():
        ics = [f["ic"] for f in fs if f["ic"] is not None]
        sharpes = [f["sharpe"] for f in fs if f["sharpe"] is not None]
        summary[m] = {
            "count": len(fs),
            "ic_min": round(min(ics), 4) if ics else None,
            "ic_max": round(max(ics), 4) if ics else None,
            "ic_mean": round(sum(ics) / len(ics), 4) if ics else None,
            "sharpe_min": round(min(sharpes), 4) if sharpes else None,
            "sharpe_max": round(max(sharpes), 4) if sharpes else None,
            "sharpe_mean": round(sum(sharpes) / len(sharpes), 4) if sharpes else None,
            "below_ic_threshold": len([ic for ic in ics if abs(ic) < 0.03]),
            "below_sharpe_threshold": len([s for s in sharpes if s < 1.5]),
        }

    report = {
        "report_type": "elite_final_quality",
        "generated_at": datetime.now().isoformat(),
        "description": "精英因子最终质量报告 — 实际进入组合的因子质量指标",
        "thresholds": {
            "min_ic": 0.03,
            "min_sharpe": 1.5,
        },
        "summary": summary,
        "factors": factors,
    }

    # 输出
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "memory" / "portfolio"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d")
    out_file = out_dir / f"elite_final_quality_{timestamp}.json"

    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅ 报告已生成: {out_file}")
    print(f"   总因子数: {len(factors)}")
    for m, s in summary.items():
        print(f"   [{m}] {s['count']} 因子 | IC: [{s['ic_min']}, {s['ic_max']}] 均值={s['ic_mean']} | Sharpe: [{s['sharpe_min']}, {s['sharpe_max']}] 均值={s['sharpe_mean']}")
        if s["below_ic_threshold"] > 0:
            print(f"   ⚠️  [{m}] IC < 0.03: {s['below_ic_threshold']} 个因子")
        if s["below_sharpe_threshold"] > 0:
            print(f"   ⚠️  [{m}] Sharpe < 1.5: {s['below_sharpe_threshold']} 个因子")

    conn.close()
    return out_file


def main() -> int:
    parser = argparse.ArgumentParser(description="生成精英因子最终质量报告")
    parser.add_argument("--market", default="all", choices=["futures", "stock", "all"])
    parser.add_argument("--db", default="data/factor_catalog.duckdb")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return 1

    generate_report(str(db_path), args.market, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
