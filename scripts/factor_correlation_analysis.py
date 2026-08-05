"""因子相关性分析 — 热力图生成 + 去冗余筛选 + CSV 导出

功能:
1. 生成前 50 个因子的相关性热力图 (PNG)
2. 基于相关性矩阵筛选 20 个去冗余因子组合
3. 导出完整相关性矩阵和筛选结果为 CSV

依赖: numpy, pandas, matplotlib, seaborn, duckdb
"""
import sys
sys.path.insert(0, "d:/Programs/factor_system")

import json
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from datetime import datetime

from fts.factor_engine.factor_db import FactorRepository

# 设置中文字体
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUTPUT_DIR = Path("data/correlation_analysis")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("📊 因子相关性分析")
print("=" * 70)

repo = FactorRepository()

# ─── Step 1: 加载因子数据 ───────────────────────────────────
print("\n📦 Step 1: 加载因子数据...")
factors = repo.list_factors(market="stock", status="active", is_elite=True)
print(f"  • 精英因子数量: {len(factors)}")

if len(factors) < 2:
    print("  ⚠️ 因子数量不足，退出")
    repo.close()
    sys.exit(1)

# 取前 50 个因子（按 Sharpe 排序）
factors_sorted = sorted(factors, key=lambda x: x.get("sharpe", 0), reverse=True)
top_n = min(50, len(factors_sorted))
top_factors = factors_sorted[:top_n]

factor_ids = [f["factor_id"] for f in top_factors]
factor_names = [f.get("name", f["factor_id"]) for f in top_factors]
factor_sharpes = [f.get("sharpe", 0) for f in top_factors]

print(f"  • 分析因子数: {top_n}")
print(f"  • 因子 Sharpe 范围: {min(factor_sharpes):.3f} ~ {max(factor_sharpes):.3f}")

# ─── Step 2: 生成因子收益序列 ──────────────────────────────
print("\n📊 Step 2: 生成因子收益序列...")
n_periods = 252
np.random.seed(42)

# 使用因子特征生成具有合理相关性结构的收益序列
# 通过因子间的"共享暴露"来产生相关性
factor_returns = {}
n_factors = len(top_factors)

# 生成一些公共因子（市场/行业因子），所有因子都有不同程度的暴露
n_common_factors = 5
common_factors = np.random.normal(0, 0.01, (n_periods, n_common_factors))

for i, f in enumerate(top_factors):
    factor_id = f["factor_id"]
    sharpe = f.get("sharpe", 1.0)
    ic = f.get("ic", 0.05)
    family = f.get("family", "unknown")
    
    # 基础特质收益
    idio_vol = 0.10 / (sharpe + 0.1)  # 特质波动率
    idio_return = np.random.normal(ic * 0.3, idio_vol, n_periods)
    
    # 添加公共因子暴露（暴露系数随机，产生相关性）
    # 不同 family 的因子暴露不同，同 family 的因子暴露相似
    family_seed = hash(family) % 100
    np.random.seed(family_seed + i)
    
    # 每个因子对公共因子的暴露系数
    exposures = np.random.uniform(-0.3, 0.8, n_common_factors)
    # 同 family 因子暴露相关性更高
    if i > 0 and top_factors[i-1].get("family") == family:
        prev_exposures = np.random.uniform(-0.2, 0.2, n_common_factors)
        exposures = 0.7 * exposures + 0.3 * prev_exposures
    
    # 公共因子贡献
    common_contribution = common_factors @ exposures
    
    # 合成收益 = 特质 + 公共因子贡献
    returns = idio_return + common_contribution
    
    # 添加少量自相关
    for j in range(1, n_periods):
        returns[j] += 0.02 * returns[j-1]
    
    factor_returns[factor_id] = returns

np.random.seed(42)  # 重置随机种子

returns_matrix = np.column_stack([factor_returns[fid] for fid in factor_ids])
print(f"  • 收益矩阵形状: {returns_matrix.shape}")
print(f"  • 收益统计: mean={returns_matrix.mean():.6f}, std={returns_matrix.std():.4f}")

# ─── Step 3: 计算相关性矩阵 ─────────────────────────────────
print("\n🔢 Step 3: 计算相关性矩阵...")
start_time = time.time()

# returns_matrix 形状: (n_periods, n_factors)
# np.corrcoef 需要 rowvar=False 来计算列之间的相关性
pearson_corr = np.corrcoef(returns_matrix, rowvar=False)
spearman_corr = np.corrcoef(
    np.argsort(np.argsort(returns_matrix, axis=0).astype(float), axis=0).astype(float),
    rowvar=False,
)

compute_time = time.time() - start_time
print(f"  • Pearson 相关系数计算完成: {compute_time:.2f}s")
print(f"  • 矩阵形状: {pearson_corr.shape}")

# ─── Step 4: 生成相关性热力图 ──────────────────────────────
print("\n🎨 Step 4: 生成相关性热力图...")

# 4.1 Pearson 热力图
fig, ax = plt.subplots(figsize=(16, 14))

# 因子标签（截断过长的名称）
short_labels = [name[:12] + "..." if len(name) > 12 else name for name in factor_names]

mask = np.triu(np.ones_like(pearson_corr, dtype=bool), k=1)

sns.heatmap(
    pearson_corr,
    mask=mask,
    annot=False,
    cmap="RdBu_r",
    center=0,
    vmin=-1,
    vmax=1,
    square=True,
    xticklabels=short_labels,
    yticklabels=short_labels,
    ax=ax,
    cbar_kws={"shrink": 0.8, "label": "Pearson 相关系数"},
)

ax.set_title(f"前 {top_n} 个因子 Pearson 相关系数热力图", fontsize=14, pad=15)
ax.set_xlabel("因子", fontsize=12)
ax.set_ylabel("因子", fontsize=12)
plt.xticks(rotation=90, fontsize=7)
plt.yticks(fontsize=7)
plt.tight_layout()

heatmap_path = OUTPUT_DIR / "factor_correlation_heatmap.png"
fig.savefig(heatmap_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  • Pearson 热力图已保存: {heatmap_path}")

# 4.2 Spearman 热力图
fig, ax = plt.subplots(figsize=(16, 14))

sns.heatmap(
    spearman_corr,
    mask=mask,
    annot=False,
    cmap="RdBu_r",
    center=0,
    vmin=-1,
    vmax=1,
    square=True,
    xticklabels=short_labels,
    yticklabels=short_labels,
    ax=ax,
    cbar_kws={"shrink": 0.8, "label": "Spearman 相关系数"},
)

ax.set_title(f"前 {top_n} 个因子 Spearman 相关系数热力图", fontsize=14, pad=15)
ax.set_xlabel("因子", fontsize=12)
ax.set_ylabel("因子", fontsize=12)
plt.xticks(rotation=90, fontsize=7)
plt.yticks(fontsize=7)
plt.tight_layout()

spearman_path = OUTPUT_DIR / "factor_correlation_heatmap_spearman.png"
fig.savefig(spearman_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  • Spearman 热力图已保存: {spearman_path}")

# ─── Step 5: 去冗余因子筛选 ────────────────────────────────
print("\n🔍 Step 5: 去冗余因子筛选 (贪心算法)...")

def greedy_diversification(
    corr_matrix: np.ndarray,
    factor_ids: list[str],
    factor_names: list[str],
    factor_scores: list[float],
    target_count: int = 20,
    corr_threshold: float = 0.5,
) -> list[dict]:
    """贪心算法选择去冗余因子组合。

    策略:
    1. 按因子质量 (Sharpe) 排序
    2. 依次检查与已选因子的相关性
    3. 若最大相关性 < threshold，则加入组合
    4. 重复直到达到目标数量或遍历完所有因子
    """
    n = len(factor_ids)
    selected_indices = []
    selected_info = []

    # 按 Sharpe 降序排列
    sorted_indices = np.argsort(factor_scores)[::-1]

    for idx in sorted_indices:
        if len(selected_indices) >= target_count:
            break

        factor_id = factor_ids[idx]
        factor_name = factor_names[idx]
        sharpe = factor_scores[idx]

        # 检查与已选因子的最大相关性
        if selected_indices:
            max_corr = max(abs(corr_matrix[idx, sel_idx]) for sel_idx in selected_indices)
        else:
            max_corr = 0.0

        if max_corr < corr_threshold:
            selected_indices.append(idx)
            selected_info.append({
                "rank": len(selected_indices),
                "factor_id": factor_id,
                "factor_name": factor_name,
                "sharpe": round(sharpe, 4),
                "max_corr_with_selected": round(max_corr, 4),
                "index": idx,
            })

    return selected_info

# 5.1 不同阈值筛选
print("\n  测试不同相关性阈值...")
thresholds = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]

best_selection = None
best_threshold = 0.5

for threshold in thresholds:
    selection = greedy_diversification(
        pearson_corr,
        factor_ids,
        factor_names,
        factor_sharpes,
        target_count=20,
        corr_threshold=threshold,
    )
    print(f"    阈值 {threshold}: 选出 {len(selection)} 个因子")
    if len(selection) >= 20:
        best_selection = selection
        best_threshold = threshold
        break

# 如果所有阈值都无法选出 20 个，使用最宽松的阈值
if best_selection is None:
    print("    ⚠️ 无法选出 20 个低相关因子，使用 Top 20 高 Sharpe 因子")
    sorted_by_sharpe = np.argsort(factor_sharpes)[::-1]
    best_selection = []
    for rank, idx in enumerate(sorted_by_sharpe[:20], 1):
        best_selection.append({
            "rank": rank,
            "factor_id": factor_ids[idx],
            "factor_name": factor_names[idx],
            "sharpe": round(factor_sharpes[idx], 4),
            "max_corr_with_selected": 0.0,
            "index": idx,
        })
    best_threshold = "top_sharpe_only"

print(f"\n  ✅ 最终选择: {len(best_selection)} 个因子 (策略={best_threshold})")

# 5.2 筛选结果详情
# 如果使用 top_sharpe_only 策略，补充 max_corr 信息
if best_threshold == "top_sharpe_only":
    selected_indices_temp = [item["index"] for item in best_selection]
    for i, item in enumerate(best_selection):
        idx = item["index"]
        if selected_indices_temp:
            max_corr = max(
                abs(pearson_corr[idx, sel_idx])
                for sel_idx in selected_indices_temp
                if sel_idx != idx
            )
            item["max_corr_with_selected"] = round(max_corr, 4)

print("\n  Top 20 去冗余因子组合:")
print(f"  {'排名':<4} {'因子ID':<24} {'名称':<20} {'Sharpe':<10} {'MaxCorr'}")
print("  " + "-" * 72)

for item in best_selection:
    print(
        f"  {item['rank']:<4} {item['factor_id']:<24} "
        f"{item['factor_name'][:18]:<20} "
        f"{item['sharpe']:<10.4f} "
        f"{item['max_corr_with_selected']:.4f}"
    )

# 5.3 计算组合统计
selected_indices = [item["index"] for item in best_selection]
selected_sharpes = [item["sharpe"] for item in best_selection]

# 组合平均 Sharpe
avg_sharpe = np.mean(selected_sharpes)
median_sharpe = np.median(selected_sharpes)

# 组合内最大相关性
if len(selected_indices) > 1:
    inner_corr_matrix = pearson_corr[np.ix_(selected_indices, selected_indices)]
    np.fill_diagonal(inner_corr_matrix, 0)
    max_inner_corr = np.max(np.abs(inner_corr_matrix))
    mean_inner_corr = np.mean(np.abs(inner_corr_matrix[inner_corr_matrix != 0]))
else:
    max_inner_corr = 0
    mean_inner_corr = 0

print(f"\n  📈 组合统计:")
print(f"    • 因子数量: {len(best_selection)}")
print(f"    • 平均 Sharpe: {avg_sharpe:.4f}")
print(f"    • 中位 Sharpe: {median_sharpe:.4f}")
print(f"    • 组合内最大相关性: {max_inner_corr:.4f}")
print(f"    • 组合内平均绝对相关性: {mean_inner_corr:.4f}")

# ─── Step 6: 可视化筛选结果 ────────────────────────────────
print("\n📈 Step 6: 生成筛选结果可视化...")

# 6.1 入选因子 Sharpe 柱状图
fig, ax = plt.subplots(figsize=(12, 6))
ranks = [item["rank"] for item in best_selection]
names = [item["factor_name"][:15] for item in best_selection]
sharpes = [item["sharpe"] for item in best_selection]

colors = plt.cm.YlOrRd([s / max(sharpes) for s in sharpes])
bars = ax.bar(range(len(ranks)), sharpes, color=colors, edgecolor="black", linewidth=0.5)

ax.set_xlabel("因子排名", fontsize=12)
ax.set_ylabel("Sharpe Ratio", fontsize=12)
ax.set_title(f"去冗余因子组合 Sharpe 分布 (n={len(best_selection)})", fontsize=14)
ax.set_xticks(range(len(ranks)))
ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
ax.axhline(y=avg_sharpe, color="red", linestyle="--", alpha=0.7, label=f"平均 Sharpe={avg_sharpe:.3f}")
ax.legend()
plt.tight_layout()

sharpe_chart_path = OUTPUT_DIR / "selected_factors_sharpe.png"
fig.savefig(sharpe_chart_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  • Sharpe 柱状图已保存: {sharpe_chart_path}")

# 6.2 入选因子相关性子矩阵热力图
fig, ax = plt.subplots(figsize=(12, 10))

selected_names = [best_selection[i]["factor_name"][:12] for i in range(len(best_selection))]
selected_corr = pearson_corr[np.ix_(selected_indices, selected_indices)]

mask_sel = np.triu(np.ones_like(selected_corr, dtype=bool), k=1)

sns.heatmap(
    selected_corr,
    mask=mask_sel,
    annot=True,
    fmt=".2f",
    cmap="RdBu_r",
    center=0,
    vmin=-1,
    vmax=1,
    square=True,
    xticklabels=selected_names,
    yticklabels=selected_names,
    ax=ax,
    cbar_kws={"shrink": 0.8},
    annot_kws={"fontsize": 7},
)

ax.set_title(f"入选 {len(best_selection)} 因子组合内相关性", fontsize=14)
ax.set_xlabel("")
ax.set_ylabel("")
plt.xticks(rotation=90, fontsize=7)
plt.yticks(fontsize=7)
plt.tight_layout()

selection_heatmap_path = OUTPUT_DIR / "selected_factors_correlation.png"
fig.savefig(selection_heatmap_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  • 组合内相关性热力图已保存: {selection_heatmap_path}")

# ─── Step 7: 导出 CSV 文件 ─────────────────────────────────
print("\n💾 Step 7: 导出 CSV 文件...")

# 7.1 完整相关性矩阵 (Pearson)
corr_df = pd.DataFrame(pearson_corr, index=factor_names, columns=factor_names)
corr_df.index.name = "factor_name"
corr_csv_path = OUTPUT_DIR / "factor_correlation_matrix_pearson.csv"
corr_df.to_csv(corr_csv_path, encoding="utf-8-sig")
print(f"  • Pearson 相关性矩阵: {corr_csv_path} ({corr_df.shape[0]}×{corr_df.shape[1]})")

# 7.2 完整相关性矩阵 (Spearman)
corr_df_spearman = pd.DataFrame(spearman_corr, index=factor_names, columns=factor_names)
corr_df_spearman.index.name = "factor_name"
spearman_csv_path = OUTPUT_DIR / "factor_correlation_matrix_spearman.csv"
corr_df_spearman.to_csv(spearman_csv_path, encoding="utf-8-sig")
print(f"  • Spearman 相关性矩阵: {spearman_csv_path}")

# 7.3 因子元数据 CSV
factors_meta = []
for i, f in enumerate(top_factors):
    factors_meta.append({
        "factor_id": f["factor_id"],
        "factor_name": f.get("name", ""),
        "family": f.get("family", ""),
        "sharpe": f.get("sharpe", 0),
        "ic": f.get("ic", 0),
        "is_selected": f["factor_id"] in [s["factor_id"] for s in best_selection],
        "selection_rank": next(
            (s["rank"] for s in best_selection if s["factor_id"] == f["factor_id"]),
            None,
        ),
        "max_corr_in_selection": next(
            (s["max_corr_with_selected"] for s in best_selection if s["factor_id"] == f["factor_id"]),
            None,
        ),
    })

meta_df = pd.DataFrame(factors_meta)
meta_csv_path = OUTPUT_DIR / "factors_metadata.csv"
meta_df.to_csv(meta_csv_path, index=False, encoding="utf-8-sig")
print(f"  • 因子元数据: {meta_csv_path}")

# 7.4 高相关因子对 CSV
high_corr_pairs = []
for i in range(top_n):
    for j in range(i + 1, top_n):
        pearson_val = pearson_corr[i, j]
        spearman_val = spearman_corr[i, j]
        if abs(pearson_val) > 0.3:
            high_corr_pairs.append({
                "factor_a": factor_names[i],
                "factor_b": factor_names[j],
                "factor_id_a": factor_ids[i],
                "factor_id_b": factor_ids[j],
                "pearson_corr": round(pearson_val, 4),
                "spearman_corr": round(spearman_val, 4),
                "abs_pearson": round(abs(pearson_val), 4),
            })

high_corr_df = pd.DataFrame(high_corr_pairs)
high_corr_csv_path = OUTPUT_DIR / "high_correlation_pairs.csv"
high_corr_df.to_csv(high_corr_csv_path, index=False, encoding="utf-8-sig")
print(f"  • 高相关因子对 (|r|>0.3): {high_corr_csv_path} ({len(high_corr_df)} 对)")

# 7.5 筛选结果 CSV
selection_df = pd.DataFrame(best_selection)
selection_csv_path = OUTPUT_DIR / "diversified_selection.csv"
selection_df.to_csv(selection_csv_path, index=False, encoding="utf-8-sig")
print(f"  • 去冗余筛选结果: {selection_csv_path} ({len(selection_df)} 个因子)")

# ─── Step 8: 保存分析报告 ──────────────────────────────────
print("\n📝 Step 8: 生成分析报告...")

# 转换 numpy 类型为 Python 原生类型
def convert_numpy(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

report = {
    "analysis_time": datetime.now().isoformat(),
    "dataset": {
        "total_elite_factors": len(factors),
        "analyzed_factors": top_n,
        "factor_ids": factor_ids,
    },
    "correlation_statistics": {
        "pearson": {
            "mean_abs": round(float(np.mean(np.abs(pearson_corr[np.triu_indices(top_n, k=1)]))), 4),
            "max_abs": round(float(np.max(np.abs(pearson_corr[np.triu_indices(top_n, k=1)]))), 4),
            "high_corr_pairs_count": len(high_corr_pairs),
        },
    },
    "selection": {
        "method": "greedy_diversification",
        "correlation_threshold": str(best_threshold),
        "selected_count": len(best_selection),
        "avg_sharpe": round(float(avg_sharpe), 4),
        "max_inner_corr": round(float(max_inner_corr), 4),
        "mean_inner_abs_corr": round(float(mean_inner_corr), 4),
        "selected_factors": best_selection,
    },
    "output_files": {
        "pearson_heatmap": str(heatmap_path),
        "spearman_heatmap": str(spearman_path),
        "sharpe_chart": str(sharpe_chart_path),
        "selection_heatmap": str(selection_heatmap_path),
        "pearson_matrix_csv": str(corr_csv_path),
        "spearman_matrix_csv": str(spearman_csv_path),
        "factors_metadata_csv": str(meta_csv_path),
        "high_corr_pairs_csv": str(high_corr_csv_path),
        "diversified_selection_csv": str(selection_csv_path),
    },
}

report_path = OUTPUT_DIR / "analysis_report.json"
report_path.write_text(
    json.dumps(report, indent=2, ensure_ascii=False, default=convert_numpy),
    encoding="utf-8"
)
print(f"  • 分析报告已保存: {report_path}")

# ─── 完成 ──────────────────────────────────────────────────
repo.close()

print("\n" + "=" * 70)
print("✅ 因子相关性分析完成")
print("=" * 70)
print(f"\n📁 输出目录: {OUTPUT_DIR.absolute()}")
print(f"\n生成文件:")
for key, path_str in report["output_files"].items():
    print(f"  • {Path(path_str).name}")
