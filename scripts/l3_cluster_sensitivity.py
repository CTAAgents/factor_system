"""L3 P1 因子聚类阈值敏感性分析（plans/36 改进项 4）。

对 cluster_threshold ∈ [0.60, 0.65, 0.70, 0.75, 0.80] 扫描：
  1. 加载指定市场（futures/energy）精英因子 + 500 交易日面板
  2. 计算因子综合评分（_factor_composite_score，与 L3 Step 1.7/1.8 同口径）
  3. 对每个阈值运行 FactorClusteringEngine（簇内 top-N）
  4. 输出各阈值入选因子集 + 与基线阈值 0.70 的 Jaccard 重合度 + 综合评分摘要
  5. 写入 reports/{market}/l3_cluster_sensitivity_{YYYYMMDD}.md

用法:
    python scripts/l3_cluster_sensitivity.py --market energy
    python scripts/l3_cluster_sensitivity.py --market energy --thresholds 0.60,0.65,0.70,0.75,0.80 --top-n 2
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

DEFAULT_THRESHOLDS = [0.60, 0.65, 0.70, 0.75, 0.80]
BASE_THRESHOLD = 0.70


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 重合度：|A∩B| / |A∪B|（空并集返回 1.0）。"""
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def main() -> int:
    parser = argparse.ArgumentParser(description="L3 P1 因子聚类阈值敏感性分析")
    parser.add_argument("--market", default="energy", choices=["futures", "energy"], help="目标市场")
    parser.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS), help="扫描阈值，逗号分隔")
    parser.add_argument("--top-n", type=int, default=1, help="簇内保留代表数（plans/36 改进项 4）")
    parser.add_argument("--trace-id", default="", help="trace_id（HARNESS 全链路）")
    args = parser.parse_args()

    trace_id = args.trace_id or f"l3_sens_{date.today():%Y%m%d}"
    thresholds = sorted({float(x) for x in args.thresholds.split(",") if x.strip()})
    if not thresholds:
        print("阈值列表为空", file=sys.stderr)
        return 2

    # 1. 加载配置 / 因子 / 面板
    try:
        from fts.config.settings import get_config

        cfg = get_config()
        elite_dir = cfg.get_elite_dir(args.market)
        print(f"[sens] market={args.market} elite_dir={elite_dir} thresholds={thresholds} top_n={args.top_n}")
        print(f"[sens] trace_id={trace_id}")

        from fts.data import FTSDataProvider
        from fts.factor_engine.factor_clustering import FactorClusteringEngine
        from fts.factor_engine.portfolio_loop import _factor_composite_score, load_elite_factors

        factors = load_elite_factors(elite_dir, market=args.market)
        if not factors:
            print(f"[sens] 无 elite 因子 [market={args.market}]，跳过", file=sys.stderr)
            return 1
        print(f"[sens] 加载 {len(factors)} 个 elite 因子")

        provider = FTSDataProvider()
        panel_data, _cdates = provider.get_futures_panel(days=500)
        print(f"[sens] 面板 {len(panel_data)} 品种 × {len(_cdates)} 交易日")
    except Exception as e:
        print(f"[sens] 数据准备失败: {e}", file=sys.stderr)
        return 2

    # 2. 综合评分（与 L3 同口径）
    score_map = _factor_composite_score(factors)

    # 3. 阈值扫描
    results: dict[float, list[str]] = {}
    for t in thresholds:
        try:
            engine = FactorClusteringEngine(cluster_threshold=t, linkage_method="average")
            selected = engine.run(factors, panel_data, score_map=score_map, cluster_top_n=args.top_n)
            results[t] = [f.get("name", f.get("factor_id", "?")) for f in selected]
            print(f"[sens] threshold={t:.2f}: {len(selected)} 因子")
        except Exception as e:
            print(f"[sens] threshold={t:.2f} 聚类失败: {e}", file=sys.stderr)
            results[t] = []

    # 4. Jaccard 重合度 vs 基线 0.70
    base_set = set(results.get(BASE_THRESHOLD, []))
    md_lines = [
        "# L3 P1 因子聚类阈值敏感性分析",
        "",
        f"- 日期: {date.today():%Y-%m-%d}",
        f"- market: {args.market}",
        f"- trace_id: {trace_id}",
        f"- 基线阈值: {BASE_THRESHOLD}",
        f"- 簇内代表数 top_n: {args.top_n}",
        "- 因子综合评分口径: sharpe_cap/icir/ic/turnover_inv（plans/36 改进项 2）",
        "",
        "## 阈值扫描结果",
        "",
        "| 阈值 | 入选因子数 | 入选因子 | 与基线 Jaccard |",
        "|:---:|:---:|:---|:---:|",
    ]
    for t in sorted(results):
        names = results[t]
        jac = _jaccard(set(names), base_set)
        md_lines.append(f"| {t:.2f} | {len(names)} | {', '.join(names) if names else '-'} | {jac:.3f} |")

    md_lines += [
        "",
        "## 结论",
        "",
    ]
    if len(results.get(BASE_THRESHOLD, [])) > 0:
        jac_of = {t: _jaccard(set(results[t]), base_set) for t in results if t != BASE_THRESHOLD}
        stable = all(v >= 0.8 for v in jac_of.values()) if jac_of else False
        md_lines.append(
            f"- 基线 {BASE_THRESHOLD} 邻域 Jaccard 分布: "
            + ", ".join(f"{t:.2f}={v:.3f}" for t, v in sorted(jac_of.items()))
        )
        md_lines.append(f"- 判定: {'✅ 稳定（邻域重合度均 ≥0.8，阈值稳健）' if stable else '⚠️ 敏感（存在邻域重合度 <0.8，需复核阈值）'}")
    else:
        md_lines.append("- 基线阈值无入选因子，无法评估稳定性")

    out_dir = PROJECT_ROOT / "reports" / args.market
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"l3_cluster_sensitivity_{date.today():%Y%m%d}.md"
    out_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"[sens] 报告已写入: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
