"""因子相关性矩阵计算脚本 - 生成可视化报告数据"""

import sys

sys.path.insert(0, "d:/Programs/factor_system")

import numpy as np
import pandas as pd
from pathlib import Path
import json

from scripts.futures_signal_pipeline import (
    load_futures_elite_factors,
    _compute_signal_matrix,
)
from fts.data import FTSDataProvider
from fts.data_futures import FUTURES_SUBSET


def compute_correlation_matrix():
    """计算因子相关性矩阵并导出"""
    print("=" * 60)
    print("  因子相关性矩阵分析")
    print("=" * 60)

    # Step 1: 加载因子
    factors = load_futures_elite_factors(ic_threshold=0)
    print(f"\n[1] 加载因子: {len(factors)} 个")

    factor_info = []
    for f in factors:
        fid = f.get("factor_id", "")
        name = f.get("name", "")
        display = name if name else fid[-8:]
        factor_info.append(
            {
                "display": display,
                "factor_id": fid,
                "name": name,
            }
        )

    for i, fi in enumerate(factor_info):
        print(f"    {i + 1}. {fi['display']} ({fi['factor_id']})")

    # Step 2: 获取数据
    provider = FTSDataProvider()
    FINANCIAL = {"IF0", "TF0", "IH0", "IC0", "TS0", "IM0"}
    symbols = [s for s in FUTURES_SUBSET if s not in FINANCIAL][:72]
    print(f"\n[2] 获取期货数据: {len(symbols)} 个品种")

    panel, common_dates = provider.get_futures_panel(symbols=symbols, days=120)
    print(f"    共同交易日: {len(common_dates)} 天")

    # Step 3: 计算信号矩阵
    print("\n[3] 计算因子信号矩阵...")
    signal_matrix = _compute_signal_matrix(panel, factors)

    # 检查 signal_matrix 的 key 格式
    first_sym = list(signal_matrix.keys())[0]
    first_factor_key = list(signal_matrix[first_sym].keys())[0] if signal_matrix[first_sym] else None
    print(f"    signal_matrix key sample: {first_factor_key}")

    # 确定使用 factor_id 还是 name 作为 key
    use_factor_id = any(first_factor_key == fi["factor_id"] for fi in factor_info)
    use_name = any(first_factor_key == fi["name"] for fi in factor_info)
    print(f"    使用 key 类型: {'factor_id' if use_factor_id else 'name' if use_name else 'unknown'}")

    # Step 4: 构建因子特征矩阵（v2.105.0：方向校正由 L3 组合负责，此处不反转信号）
    print("[4] 构建因子特征矩阵...")

    # 确定每个因子在 signal_matrix 中的 key
    factor_features = {}
    factor_display_list = []

    for fi in factor_info:
        fid = fi["factor_id"]
        display = fi["display"]

        # 查找 signal_matrix 中对应的 key
        matrix_key = fid if use_factor_id else (fi["name"] if use_name else fid)

        all_signals = []
        for sym in signal_matrix:
            if matrix_key in signal_matrix[sym]:
                arr = signal_matrix[sym][matrix_key]
                if arr is not None and len(arr) > 0:
                    corrected = np.array([v if np.isfinite(v) else 0.0 for v in arr])
                    all_signals.append(corrected)

        if all_signals:
            combined = np.concatenate(all_signals)
            factor_features[fid] = combined
            factor_display_list.append(display)

    if not factor_features:
        print("    ERROR: 无有效因子特征")
        return None, None, None

    min_len = min(len(v) for v in factor_features.values())
    if min_len == 0:
        print("    ERROR: 所有因子特征长度为 0")
        return None, None, None

    valid_fids = list(factor_features.keys())
    valid_displays = factor_display_list

    # 构建矩阵
    X = np.zeros((min_len, len(valid_fids)))
    for i, fid in enumerate(valid_fids):
        feat = factor_features[fid][:min_len]
        X[:, i] = feat

    print(f"    有效因子: {len(valid_fids)} 个")
    print(f"    特征矩阵: {X.shape[0]} 样本 × {X.shape[1]} 因子")

    # Step 6: 计算相关性矩阵
    print("[6] 计算 Pearson 相关矩阵...")
    corr_matrix = np.corrcoef(X, rowvar=False)
    corr_matrix = np.nan_to_num(corr_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

    # 转换为 DataFrame
    corr_df = pd.DataFrame(corr_matrix, index=valid_displays, columns=valid_displays)

    # Step 7: 导出数据
    output_dir = Path("d:/Programs/factor_system/reports/correlation")
    output_dir.mkdir(parents=True, exist_ok=True)

    corr_df.to_csv(output_dir / "factor_correlation_matrix.csv")
    print(f"\n[7] 导出相关矩阵到: {output_dir}")

    # Step 8: 分析高相关因子对
    print("\n" + "=" * 60)
    print("  高相关因子对分析")
    print("=" * 60)

    high_corr_pairs = []
    threshold = 0.6
    extreme_threshold = 0.95

    for i in range(len(valid_displays)):
        for j in range(i + 1, len(valid_displays)):
            c = abs(corr_matrix[i, j])
            if c > threshold:
                level = "EXTREME" if c > extreme_threshold else ("HIGH" if c > 0.7 else "MODERATE")
                high_corr_pairs.append(
                    {
                        "factor_1": valid_displays[i],
                        "factor_2": valid_displays[j],
                        "factor_id_1": valid_fids[i],
                        "factor_id_2": valid_fids[j],
                        "correlation": round(float(c), 4),
                        "level": level,
                    }
                )

    high_corr_pairs.sort(key=lambda x: -x["correlation"])

    print(f"\n发现 {len(high_corr_pairs)} 个高相关因子对 (|corr| > {threshold}):")
    print("-" * 80)

    extreme_count = 0
    high_count = 0
    moderate_count = 0

    for pair in high_corr_pairs:
        level_icon = {"EXTREME": "🔴", "HIGH": "🟡", "MODERATE": "🟢"}[pair["level"]]
        print(
            f"  {level_icon} {pair['factor_1']:30s} × {pair['factor_2']:30s} = {pair['correlation']:.4f}  [{pair['level']}]"
        )

        if pair["level"] == "EXTREME":
            extreme_count += 1
        elif pair["level"] == "HIGH":
            high_count += 1
        else:
            moderate_count += 1

    print(f"\n  统计: 🔴 EXTREME={extreme_count} | 🟡 HIGH={high_count} | 🟢 MODERATE={moderate_count}")

    # Step 9: 识别需要剔除的因子
    print("\n" + "=" * 60)
    print("  建议剔除的冗余因子 (相关性 > 0.95)")
    print("=" * 60)

    extreme_pairs = [p for p in high_corr_pairs if p["level"] == "EXTREME"]
    to_remove = set()
    kept = set()

    if extreme_pairs:
        factor_degree = {}
        for pair in extreme_pairs:
            f1, f2 = pair["factor_1"], pair["factor_2"]
            factor_degree[f1] = factor_degree.get(f1, 0) + 1
            factor_degree[f2] = factor_degree.get(f2, 0) + 1

        for pair in extreme_pairs:
            f1, f2 = pair["factor_1"], pair["factor_2"]
            if f1 not in kept and f2 not in kept:
                if factor_degree.get(f1, 0) >= factor_degree.get(f2, 0):
                    kept.add(f1)
                    to_remove.add(f2)
                else:
                    kept.add(f2)
                    to_remove.add(f1)
            elif f1 in kept and f2 not in kept:
                to_remove.add(f2)
            elif f2 in kept and f1 not in kept:
                to_remove.add(f1)

        print(f"\n  ❌ 建议剔除 {len(to_remove)} 个冗余因子:")
        for f in sorted(to_remove):
            print(f"      - {f}")

        print(f"\n  ✅ 保留因子: {', '.join(sorted(kept))}")
    else:
        print("\n  ✅ 无极端相关因子对，无需剔除")

    # 导出分析结果
    analysis_result = {
        "total_factors": len(valid_displays),
        "high_corr_pairs": high_corr_pairs,
        "extreme_pairs": extreme_pairs,
        "recommended_removal": sorted(list(to_remove)),
        "kept_factors": sorted(list(kept)) if extreme_pairs else [],
        "statistics": {
            "extreme_count": extreme_count,
            "high_count": high_count,
            "moderate_count": moderate_count,
        },
    }

    with open(output_dir / "correlation_analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis_result, f, ensure_ascii=False, indent=2)
    print(f"\n[8] 导出分析结果到: {output_dir}")

    # 导出完整矩阵数据
    matrix_data = {
        "labels": valid_displays,
        "matrix": [
            [round(float(corr_matrix[i, j]), 4) for j in range(len(valid_displays))] for i in range(len(valid_displays))
        ],
    }

    with open(output_dir / "full_matrix.json", "w", encoding="utf-8") as f:
        json.dump(matrix_data, f, ensure_ascii=False)

    print(f"[9] 导出完整矩阵数据到: {output_dir}")

    return analysis_result, corr_df, valid_displays


if __name__ == "__main__":
    result, corr_df, factor_names = compute_correlation_matrix()

    if result:
        print("\n" + "=" * 60)
        print("  总结")
        print("=" * 60)
        print(f"  因子总数: {len(factor_names)}")
        print(f"  高相关因子对 (>0.6): {len(result['high_corr_pairs'])}")
        print(f"  极端相关因子对 (>0.95): {len(result['extreme_pairs'])}")
        print(f"  建议剔除: {len(result['recommended_removal'])} 个因子")
        print("\n  输出文件:")
        print("    - reports/correlation/factor_correlation_matrix.csv")
        print("    - reports/correlation/correlation_analysis.json")
        print("    - reports/correlation/full_matrix.json")
