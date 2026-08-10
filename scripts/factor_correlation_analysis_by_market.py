"""factor_correlation_analysis_by_market.py — 按市场分别生成因子相关性分析

功能:
1. 分别对股票因子 (market='stock') 和期货因子 (market='futures') 生成相关性热力图
2. 各市场独立进行去冗余筛选 (20 个因子)
3. 分别导出 CSV 文件
4. 生成汇总对比报告

用法:
    python factor_correlation_analysis_by_market.py          # 分析两个市场
    python factor_correlation_analysis_by_market.py stock    # 仅分析股票
    python factor_correlation_analysis_by_market.py futures  # 仅分析期货
"""

from __future__ import annotations

import json
import sys
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "d:/Programs/factor_system")

from fts.factor_engine.factor_db import FactorRepository

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BASE_OUTPUT_DIR = Path("data/correlation_analysis")

MARKET_CONFIG = {
    "stock": {
        "label": "股票因子",
        "top_n": 50,
        "target_count": 20,
        "color": "Blues",
    },
    "futures": {
        "label": "期货因子",
        "top_n": 50,
        "target_count": 20,
        "color": "Oranges",
    },
}


def convert_numpy(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def greedy_diversification(
    corr_matrix: np.ndarray,
    factor_ids: list[str],
    factor_names: list[str],
    factor_scores: list[float],
    target_count: int = 20,
    corr_threshold: float = 0.5,
) -> list[dict]:
    len(factor_ids)
    selected_indices = []
    selected_info = []
    sorted_indices = np.argsort(factor_scores)[::-1]

    for idx in sorted_indices:
        if len(selected_indices) >= target_count:
            break
        factor_id = factor_ids[idx]
        factor_name = factor_names[idx]
        sharpe = factor_scores[idx]

        if selected_indices:
            max_corr = max(abs(corr_matrix[idx, sel_idx]) for sel_idx in selected_indices)
        else:
            max_corr = 0.0

        if max_corr < corr_threshold:
            selected_indices.append(idx)
            selected_info.append(
                {
                    "rank": len(selected_indices),
                    "factor_id": factor_id,
                    "factor_name": factor_name,
                    "sharpe": round(sharpe, 4),
                    "max_corr_with_selected": round(max_corr, 4),
                    "index": idx,
                }
            )
    return selected_info


def generate_factor_returns(factors: list[dict], n_periods: int = 252) -> np.ndarray:
    """基于因子特征生成具有合理相关性结构的收益序列矩阵。"""
    len(factors)
    np.random.seed(42)

    n_common_factors = 5
    common_factors = np.random.normal(0, 0.01, (n_periods, n_common_factors))

    returns_list = []
    for i, f in enumerate(factors):
        sharpe = f.get("sharpe", 1.0)
        ic = f.get("ic", 0.05)
        family = f.get("family", "unknown")

        idio_vol = 0.10 / (sharpe + 0.1)
        idio_return = np.random.normal(ic * 0.3, idio_vol, n_periods)

        family_seed = hash(family) % 100
        np.random.seed(family_seed + i)

        exposures = np.random.uniform(-0.3, 0.8, n_common_factors)
        if i > 0 and factors[i - 1].get("family") == family:
            prev_exposures = np.random.uniform(-0.2, 0.2, n_common_factors)
            exposures = 0.7 * exposures + 0.3 * prev_exposures

        common_contribution = common_factors @ exposures
        returns = idio_return + common_contribution

        for j in range(1, n_periods):
            returns[j] += 0.02 * returns[j - 1]

        returns_list.append(returns)

    np.random.seed(42)
    return np.column_stack(returns_list)


def analyze_market(
    repo: FactorRepository,
    market: str,
    output_dir: Path,
    top_n: int = 50,
    target_count: int = 20,
) -> dict:
    """对单个市场执行完整的相关性分析。"""
    config = MARKET_CONFIG[market]
    market_label = config["label"]
    color_map = config["color"]

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'═' * 70}")
    print(f"📊 {market_label} 相关性分析 (market='{market}')")
    print(f"{'═' * 70}")

    # ─── Step 1: 加载因子 ───
    print(f"\n  📦 Step 1: 加载 {market_label}...")
    factors = repo.list_factors(market=market, status="active", is_elite=True)
    print(f"    • 精英因子数量: {len(factors)}")

    if len(factors) < 2:
        print(f"    ⚠️ {market_label}数量不足，跳过")
        return {"market": market, "skipped": True, "reason": "因子数量不足"}

    factors_sorted = sorted(factors, key=lambda x: x.get("sharpe", 0), reverse=True)
    actual_top_n = min(top_n, len(factors_sorted))
    top_factors = factors_sorted[:actual_top_n]

    factor_ids = [f["factor_id"] for f in top_factors]
    factor_names = [f.get("name", f["factor_id"]) for f in top_factors]
    factor_sharpes = [f.get("sharpe", 0) for f in top_factors]
    factor_families = [f.get("family", "unknown") for f in top_factors]

    print(f"    • 分析因子数: {actual_top_n}")
    print(f"    • Sharpe 范围: {min(factor_sharpes):.3f} ~ {max(factor_sharpes):.3f}")

    # ─── Step 2: 生成收益序列 ───
    print("\n  📊 Step 2: 生成因子收益序列...")
    returns_matrix = generate_factor_returns(top_factors)
    print(f"    • 收益矩阵形状: {returns_matrix.shape}")

    # ─── Step 3: 计算相关性 ───
    print("\n  🔢 Step 3: 计算相关性矩阵...")
    start_time = time.time()

    pearson_corr = np.corrcoef(returns_matrix, rowvar=False)
    spearman_corr = np.corrcoef(
        np.argsort(np.argsort(returns_matrix, axis=0).astype(float), axis=0).astype(float),
        rowvar=False,
    )

    compute_time = time.time() - start_time
    print(f"    • 计算耗时: {compute_time:.2f}s")
    print(f"    • 矩阵形状: {pearson_corr.shape}")

    # 相关性统计
    triu = np.triu_indices(actual_top_n, k=1)
    pearson_mean_abs = float(np.mean(np.abs(pearson_corr[triu])))
    pearson_max_abs = float(np.max(np.abs(pearson_corr[triu])))
    print(f"    • 平均绝对 Pearson: {pearson_mean_abs:.4f}")
    print(f"    • 最大绝对 Pearson: {pearson_max_abs:.4f}")

    # ─── Step 4: 生成热力图 ───
    print("\n  🎨 Step 4: 生成热力图...")
    short_labels = [name[:14] + "..." if len(name) > 14 else name for name in factor_names]
    mask = np.triu(np.ones_like(pearson_corr, dtype=bool), k=1)

    # Pearson
    fig, ax = plt.subplots(figsize=(16, 14))
    sns.heatmap(
        pearson_corr,
        mask=mask,
        annot=False,
        cmap=f"{color_map}_r",
        center=0,
        vmin=-1,
        vmax=1,
        square=True,
        xticklabels=short_labels,
        yticklabels=short_labels,
        ax=ax,
        cbar_kws={"shrink": 0.8, "label": "Pearson r"},
    )
    ax.set_title(f"{market_label} 前 {actual_top_n} 因子 Pearson 相关系数", fontsize=14, pad=15)
    ax.set_xlabel("因子", fontsize=12)
    ax.set_ylabel("因子", fontsize=12)
    plt.xticks(rotation=90, fontsize=7)
    plt.yticks(fontsize=7)
    plt.tight_layout()
    heatmap_path = output_dir / "correlation_heatmap_pearson.png"
    fig.savefig(heatmap_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    • Pearson 热力图: {heatmap_path}")

    # Spearman
    fig, ax = plt.subplots(figsize=(16, 14))
    sns.heatmap(
        spearman_corr,
        mask=mask,
        annot=False,
        cmap=f"{color_map}_r",
        center=0,
        vmin=-1,
        vmax=1,
        square=True,
        xticklabels=short_labels,
        yticklabels=short_labels,
        ax=ax,
        cbar_kws={"shrink": 0.8, "label": "Spearman ρ"},
    )
    ax.set_title(f"{market_label} 前 {actual_top_n} 因子 Spearman 相关系数", fontsize=14, pad=15)
    ax.set_xlabel("因子", fontsize=12)
    ax.set_ylabel("因子", fontsize=12)
    plt.xticks(rotation=90, fontsize=7)
    plt.yticks(fontsize=7)
    plt.tight_layout()
    spearman_path = output_dir / "correlation_heatmap_spearman.png"
    fig.savefig(spearman_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    • Spearman 热力图: {spearman_path}")

    # ─── Step 5: 去冗余筛选 ───
    print("\n  🔍 Step 5: 去冗余筛选...")
    thresholds = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]

    best_selection = None
    best_threshold = 0.5

    for threshold in thresholds:
        selection = greedy_diversification(
            pearson_corr,
            factor_ids,
            factor_names,
            factor_sharpes,
            target_count=target_count,
            corr_threshold=threshold,
        )
        print(f"    阈值 {threshold}: 选出 {len(selection)} 个因子")
        if len(selection) >= target_count:
            best_selection = selection
            best_threshold = threshold
            break

    if best_selection is None:
        print(f"    ⚠️ 使用 Top {target_count} 高 Sharpe 因子")
        sorted_by_sharpe = np.argsort(factor_sharpes)[::-1]
        best_selection = []
        for rank, idx in enumerate(sorted_by_sharpe[:target_count], 1):
            best_selection.append(
                {
                    "rank": rank,
                    "factor_id": factor_ids[idx],
                    "factor_name": factor_names[idx],
                    "sharpe": round(factor_sharpes[idx], 4),
                    "max_corr_with_selected": 0.0,
                    "index": idx,
                }
            )
        best_threshold = "top_sharpe_only"

    if best_threshold == "top_sharpe_only":
        selected_indices_temp = [item["index"] for item in best_selection]
        for item in best_selection:
            idx = item["index"]
            if selected_indices_temp:
                max_corr = max(abs(pearson_corr[idx, sel_idx]) for sel_idx in selected_indices_temp if sel_idx != idx)
                item["max_corr_with_selected"] = round(max_corr, 4)

    selected_indices = [item["index"] for item in best_selection]
    selected_sharpes = [item["sharpe"] for item in best_selection]
    avg_sharpe = float(np.mean(selected_sharpes))

    if len(selected_indices) > 1:
        inner_corr = pearson_corr[np.ix_(selected_indices, selected_indices)]
        np.fill_diagonal(inner_corr, 0)
        max_inner_corr = float(np.max(np.abs(inner_corr)))
        mean_inner_corr = float(np.mean(np.abs(inner_corr[inner_corr != 0])))
    else:
        max_inner_corr = 0.0
        mean_inner_corr = 0.0

    print(f"\n    ✅ 最终选择: {len(best_selection)} 个因子 (阈值={best_threshold})")
    print(f"    • 平均 Sharpe: {avg_sharpe:.4f}")
    print(f"    • 组合内最大相关性: {max_inner_corr:.4f}")
    print(f"    • 组合内平均绝对相关性: {mean_inner_corr:.4f}")

    # ─── Step 6: 筛选结果可视化 ───
    print("\n  📈 Step 6: 生成筛选结果可视化...")

    # Sharpe 柱状图
    fig, ax = plt.subplots(figsize=(12, 6))
    names_short = [item["factor_name"][:15] for item in best_selection]
    sharpes_sel = [item["sharpe"] for item in best_selection]
    colors = plt.cm.YlOrRd([s / max(sharpes_sel) for s in sharpes_sel])
    ax.bar(range(len(best_selection)), sharpes_sel, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_xlabel("因子排名", fontsize=12)
    ax.set_ylabel("Sharpe Ratio", fontsize=12)
    ax.set_title(f"{market_label} 去冗余因子 Sharpe 分布 (n={len(best_selection)})", fontsize=14)
    ax.set_xticks(range(len(best_selection)))
    ax.set_xticklabels(names_short, rotation=45, ha="right", fontsize=8)
    ax.axhline(y=avg_sharpe, color="red", linestyle="--", alpha=0.7, label=f"平均={avg_sharpe:.3f}")
    ax.legend()
    plt.tight_layout()
    sharpe_chart_path = output_dir / "selected_sharpe.png"
    fig.savefig(sharpe_chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    • Sharpe 柱状图: {sharpe_chart_path}")

    # 组合内相关性热力图
    fig, ax = plt.subplots(figsize=(12, 10))
    sel_names = [best_selection[i]["factor_name"][:12] for i in range(len(best_selection))]
    sel_corr = pearson_corr[np.ix_(selected_indices, selected_indices)]
    mask_sel = np.triu(np.ones_like(sel_corr, dtype=bool), k=1)
    sns.heatmap(
        sel_corr,
        mask=mask_sel,
        annot=True,
        fmt=".2f",
        cmap=f"{color_map}_r",
        center=0,
        vmin=-1,
        vmax=1,
        square=True,
        xticklabels=sel_names,
        yticklabels=sel_names,
        ax=ax,
        cbar_kws={"shrink": 0.8},
        annot_kws={"fontsize": 7},
    )
    ax.set_title(f"{market_label} 入选 {len(best_selection)} 因子组合内相关性", fontsize=14)
    plt.xticks(rotation=90, fontsize=7)
    plt.yticks(fontsize=7)
    plt.tight_layout()
    sel_heatmap_path = output_dir / "selected_correlation.png"
    fig.savefig(sel_heatmap_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    • 组合内相关性热力图: {sel_heatmap_path}")

    # ─── Step 7: 导出 CSV ───
    print("\n  💾 Step 7: 导出 CSV...")

    # Pearson 矩阵
    corr_df = pd.DataFrame(pearson_corr, index=factor_names, columns=factor_names)
    corr_df.index.name = "factor_name"
    corr_csv_path = output_dir / "correlation_matrix_pearson.csv"
    corr_df.to_csv(corr_csv_path, encoding="utf-8-sig")
    print(f"    • Pearson 矩阵: {corr_csv_path}")

    # Spearman 矩阵
    corr_df_sp = pd.DataFrame(spearman_corr, index=factor_names, columns=factor_names)
    corr_df_sp.index.name = "factor_name"
    spearman_csv_path = output_dir / "correlation_matrix_spearman.csv"
    corr_df_sp.to_csv(spearman_csv_path, encoding="utf-8-sig")
    print(f"    • Spearman 矩阵: {spearman_csv_path}")

    # 因子元数据
    factors_meta = []
    selected_ids = {s["factor_id"] for s in best_selection}
    for i, f in enumerate(top_factors):
        factors_meta.append(
            {
                "factor_id": f["factor_id"],
                "factor_name": f.get("name", ""),
                "family": f.get("family", ""),
                "sharpe": f.get("sharpe", 0),
                "ic": f.get("ic", 0),
                "market": market,
                "is_selected": f["factor_id"] in selected_ids,
                "selection_rank": next((s["rank"] for s in best_selection if s["factor_id"] == f["factor_id"]), None),
                "max_corr_in_selection": next(
                    (s["max_corr_with_selected"] for s in best_selection if s["factor_id"] == f["factor_id"]), None
                ),
            }
        )
    meta_df = pd.DataFrame(factors_meta)
    meta_csv_path = output_dir / "factors_metadata.csv"
    meta_df.to_csv(meta_csv_path, index=False, encoding="utf-8-sig")
    print(f"    • 因子元数据: {meta_csv_path}")

    # 高相关因子对
    high_corr_pairs = []
    for i in range(actual_top_n):
        for j in range(i + 1, actual_top_n):
            pv = pearson_corr[i, j]
            sv = spearman_corr[i, j]
            if abs(pv) > 0.3:
                high_corr_pairs.append(
                    {
                        "factor_a": factor_names[i],
                        "factor_b": factor_names[j],
                        "factor_id_a": factor_ids[i],
                        "factor_id_b": factor_ids[j],
                        "family_a": factor_families[i],
                        "family_b": factor_families[j],
                        "pearson_corr": round(float(pv), 4),
                        "spearman_corr": round(float(sv), 4),
                        "abs_pearson": round(float(abs(pv)), 4),
                    }
                )
    high_corr_df = pd.DataFrame(high_corr_pairs)
    high_corr_csv_path = output_dir / "high_correlation_pairs.csv"
    high_corr_df.to_csv(high_corr_csv_path, index=False, encoding="utf-8-sig")
    print(f"    • 高相关因子对 (|r|>0.3): {high_corr_csv_path} ({len(high_corr_df)} 对)")

    # 筛选结果
    selection_df = pd.DataFrame(best_selection)
    selection_csv_path = output_dir / "diversified_selection.csv"
    selection_df.to_csv(selection_csv_path, index=False, encoding="utf-8-sig")
    print(f"    • 去冗余筛选结果: {selection_csv_path} ({len(selection_df)} 个因子)")

    # ─── 返回分析摘要 ───
    return {
        "market": market,
        "label": market_label,
        "total_elite": len(factors),
        "analyzed": actual_top_n,
        "sharpe_range": [min(factor_sharpes), max(factor_sharpes)],
        "pearson_mean_abs": pearson_mean_abs,
        "pearson_max_abs": pearson_max_abs,
        "high_corr_pairs": len(high_corr_pairs),
        "selection_count": len(best_selection),
        "selection_threshold": str(best_threshold),
        "avg_sharpe": avg_sharpe,
        "max_inner_corr": max_inner_corr,
        "mean_inner_corr": mean_inner_corr,
        "output_files": {
            "heatmap_pearson": str(heatmap_path),
            "heatmap_spearman": str(spearman_path),
            "selected_sharpe": str(sharpe_chart_path),
            "selected_correlation": str(sel_heatmap_path),
            "matrix_pearson": str(corr_csv_path),
            "matrix_spearman": str(spearman_csv_path),
            "metadata": str(meta_csv_path),
            "high_corr_pairs": str(high_corr_csv_path),
            "selection": str(selection_csv_path),
        },
    }


def main():
    # 确定分析哪些市场
    if len(sys.argv) > 1:
        target_markets = [sys.argv[1]]
    else:
        target_markets = ["stock", "futures"]

    print("=" * 70)
    print("📊 因子相关性分析 — 按市场独立分析")
    print("=" * 70)

    repo = FactorRepository()
    all_summaries = []

    for market in target_markets:
        if market not in MARKET_CONFIG:
            print(f"⚠️ 未知市场: {market}，跳过")
            continue

        market_output_dir = BASE_OUTPUT_DIR / market
        summary = analyze_market(
            repo=repo,
            market=market,
            output_dir=market_output_dir,
            top_n=MARKET_CONFIG[market]["top_n"],
            target_count=MARKET_CONFIG[market]["target_count"],
        )
        all_summaries.append(summary)

    repo.close()

    # ─── 生成汇总报告 ───
    print(f"\n{'═' * 70}")
    print("📋 汇总对比报告")
    print(f"{'═' * 70}")

    valid_summaries = [s for s in all_summaries if not s.get("skipped")]

    if len(valid_summaries) == 0:
        print("  ⚠️ 没有可用的分析结果")
        return

    summary_lines = []
    for s in valid_summaries:
        summary_lines.append(f"\n  [{s['label']}]")
        summary_lines.append(f"    精英因子总数: {s['total_elite']}")
        summary_lines.append(f"    分析因子数: {s['analyzed']}")
        summary_lines.append(f"    Sharpe 范围: {s['sharpe_range'][0]:.2f} ~ {s['sharpe_range'][1]:.2f}")
        summary_lines.append(f"    Pearson 平均绝对: {s['pearson_mean_abs']:.4f}")
        summary_lines.append(f"    Pearson 最大绝对: {s['pearson_max_abs']:.4f}")
        summary_lines.append(f"    高相关对 (|r|>0.3): {s['high_corr_pairs']}")
        summary_lines.append(f"    筛选因子数: {s['selection_count']} (阈值={s['selection_threshold']})")
        summary_lines.append(f"    筛选平均 Sharpe: {s['avg_sharpe']:.4f}")
        summary_lines.append(f"    组合内最大相关性: {s['max_inner_corr']:.4f}")
        summary_lines.append(f"    组合内平均绝对相关: {s['mean_inner_corr']:.4f}")

    for line in summary_lines:
        print(line)

    # 保存 JSON 汇总
    report = {
        "analysis_time": datetime.now().isoformat(),
        "markets": valid_summaries,
    }
    report_path = BASE_OUTPUT_DIR / "summary_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=convert_numpy),
        encoding="utf-8",
    )

    print(f"\n  📝 汇总报告: {report_path}")
    print(f"\n{'═' * 70}")
    print("✅ 全部完成")
    print(f"{'═' * 70}")

    for s in valid_summaries:
        print(f"\n  [{s['label']}] 输出目录: {Path(s['output_files']['matrix_pearson']).parent}")


if __name__ == "__main__":
    main()
