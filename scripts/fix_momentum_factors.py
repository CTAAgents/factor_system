"""
scripts/fix_momentum_factors.py — 修复 momentum 家族因子 IC 为负问题

通过反转因子方向（乘以 -1）重新计算 momentum 家族因子的 IC，
验证反转后是否改善因子表现。

用法:
    python scripts/fix_momentum_factors.py

输出:
    - 修复前后对比报告: reports/momentum_fix_report.json
    - 对比图表: reports/momentum_fix_comparison.png
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_factor_audit_real import (
    compute_factor_on_panel,
    get_real_ohlcv_data,
    load_factors_from_yaml,
)
from fts.data_futures import get_futures_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fix_momentum")


# ─── 因子方向反转 ──────────────────────────────────────────


def reverse_factor_code(factor_code: str) -> str:
    """反转因子代码的输出方向（乘以 -1）。

    通过在 return 语句前插入取反操作实现。

    Args:
        factor_code: 原始因子代码

    Returns:
        反转后的因子代码
    """
    lines = factor_code.split("\n")
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("return ") and "factor_program" not in stripped:
            # 在 return 语句前加 - (取反)
            indent = line[: len(line) - len(line.lstrip())]
            return_expr = stripped[7:]  # 去掉 "return "
            new_lines.append(f"{indent}return -{return_expr}")
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def create_reversed_factor(factor_meta: dict) -> dict:
    """创建反转版本的因子元数据。"""
    reversed_meta = factor_meta.copy()
    reversed_meta["name"] = f"{factor_meta.get('name', 'unknown')}_reversed"
    reversed_meta["_original_name"] = factor_meta.get("name", "unknown")
    reversed_meta["code"] = reverse_factor_code(factor_meta.get("code", ""))
    reversed_meta["_reversed"] = True
    return reversed_meta


# ─── 主流程 ──────────────────────────────────────────────


def main():
    logger.info("=" * 60)
    logger.info("修复 momentum 家族因子 IC 为负问题")
    logger.info("=" * 60)

    # 1. 加载 momentum 家族因子
    seeds_dir = PROJECT_ROOT / "seeds"
    all_factors = load_factors_from_yaml(seeds_dir, "futures")
    momentum_factors = [f for f in all_factors if f.get("_family") == "momentum"]

    if not momentum_factors:
        logger.error("未找到 momentum 家族因子")
        sys.exit(1)

    logger.info("找到 %d 个 momentum 因子", len(momentum_factors))
    for f in momentum_factors:
        logger.info("  - %s", f.get("name"))

    # 2. 获取真实数据
    logger.info("获取真实期货数据...")
    provider = get_futures_provider()
    panel = get_real_ohlcv_data(provider, days=500)
    logger.info("数据获取完成: %d 个品种", len(panel))

    # 3. 分别计算原始和反转版本
    comparison_results: list[dict] = []

    for factor_meta in momentum_factors:
        name = factor_meta.get("name", "unknown")
        logger.info("-" * 40)
        logger.info("处理因子: %s", name)

        # 原始版本
        logger.info("  计算原始版本...")
        original_ic = compute_factor_on_panel(factor_meta, panel)

        # 反转版本
        logger.info("  计算反转版本...")
        reversed_meta = create_reversed_factor(factor_meta)
        reversed_ic = compute_factor_on_panel(reversed_meta, panel)

        # 计算对比指标
        orig_mean_ic = float(np.mean([r["ic"] for r in original_ic.values()])) if original_ic else 0.0
        rev_mean_ic = float(np.mean([r["ic"] for r in reversed_ic.values()])) if reversed_ic else 0.0

        orig_positive_ratio = (
            sum(1 for r in original_ic.values() if r["ic"] > 0) / len(original_ic) if original_ic else 0.0
        )
        rev_positive_ratio = (
            sum(1 for r in reversed_ic.values() if r["ic"] > 0) / len(reversed_ic) if reversed_ic else 0.0
        )

        logger.info("  原始: IC=%.4f, 正收益比例=%.1f%%", orig_mean_ic, orig_positive_ratio * 100)
        logger.info("  反转: IC=%.4f, 正收益比例=%.1f%%", rev_mean_ic, rev_positive_ratio * 100)

        # 计算改善幅度
        ic_improvement = rev_mean_ic - orig_mean_ic
        ratio_improvement = rev_positive_ratio - orig_positive_ratio
        logger.info("  IC 改善: %+.4f, 比例改善: %+.1f%%", ic_improvement, ratio_improvement * 100)

        comparison_results.append(
            {
                "factor_name": name,
                "original": {
                    "mean_ic": orig_mean_ic,
                    "positive_ratio": orig_positive_ratio,
                    "n_symbols": len(original_ic),
                    "symbol_ics": {sym: r["ic"] for sym, r in original_ic.items()},
                },
                "reversed": {
                    "mean_ic": rev_mean_ic,
                    "positive_ratio": rev_positive_ratio,
                    "n_symbols": len(reversed_ic),
                    "symbol_ics": {sym: r["ic"] for sym, r in reversed_ic.items()},
                },
                "improvement": {
                    "ic_delta": ic_improvement,
                    "ratio_delta": ratio_improvement,
                    "improved": rev_mean_ic > orig_mean_ic,
                },
            }
        )

    # 4. 保存对比结果
    output_dir = PROJECT_ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report_path = output_dir / f"momentum_fix_report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(comparison_results, f, ensure_ascii=False, indent=2)
    logger.info("对比报告已保存: %s", report_path)

    # 5. 生成对比图表
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        chart_dir = output_dir / "audit_charts"
        chart_dir.mkdir(parents=True, exist_ok=True)

        # 对比柱状图
        factors = [r["factor_name"] for r in comparison_results]
        orig_ics = [r["original"]["mean_ic"] for r in comparison_results]
        rev_ics = [r["reversed"]["mean_ic"] for r in comparison_results]

        fig, ax = plt.subplots(figsize=(12, 6))
        x = np.arange(len(factors))
        width = 0.35

        bars1 = ax.bar(x - width / 2, orig_ics, width, label="原始 IC", color="#e74c3c")
        bars2 = ax.bar(x + width / 2, rev_ics, width, label="反转 IC", color="#2ecc71")

        ax.set_xlabel("因子")
        ax.set_ylabel("平均 IC")
        ax.set_title("Momentum 家族因子 IC 修复前后对比")
        ax.set_xticks(x)
        ax.set_xticklabels(factors, rotation=45, ha="right")
        ax.legend()
        ax.axhline(y=0, color="gray", linestyle="-", alpha=0.5)
        ax.axhline(y=0.03, color="green", linestyle="--", alpha=0.3, label="IC=0.03 基准")

        for bars in [bars1, bars2]:
            for bar in bars:
                h = bar.get_height()
                offset = 0.005 if h >= 0 else -0.015
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h + offset,
                    f"{h:.3f}",
                    ha="center",
                    va="bottom" if h >= 0 else "top",
                    fontsize=8,
                )

        plt.tight_layout()
        chart_path = chart_dir / "momentum_fix_comparison.png"
        fig.savefig(chart_path, dpi=100, bbox_inches="tight")
        plt.close()
        logger.info("对比图表已保存: %s", chart_path)

        # 跨品种 IC 热力图对比
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

        # 收集所有品种
        all_symbols = sorted(
            set(
                list(comparison_results[0]["original"]["symbol_ics"].keys())
                + list(comparison_results[0]["reversed"]["symbol_ics"].keys())
            )
        )

        orig_matrix = np.zeros((len(factors), len(all_symbols)))
        rev_matrix = np.zeros((len(factors), len(all_symbols)))

        for i, r in enumerate(comparison_results):
            for j, sym in enumerate(all_symbols):
                orig_matrix[i, j] = r["original"]["symbol_ics"].get(sym, 0)
                rev_matrix[i, j] = r["reversed"]["symbol_ics"].get(sym, 0)

        im1 = ax1.imshow(orig_matrix, cmap="RdYlGn", aspect="auto", vmin=-0.5, vmax=0.5)
        ax1.set_xticks(range(len(all_symbols)))
        ax1.set_xticklabels(all_symbols, rotation=45, ha="right", fontsize=8)
        ax1.set_yticks(range(len(factors)))
        ax1.set_yticklabels(factors)
        ax1.set_title("原始 IC 热力图")
        plt.colorbar(im1, ax=ax1)

        im2 = ax2.imshow(rev_matrix, cmap="RdYlGn", aspect="auto", vmin=-0.5, vmax=0.5)
        ax2.set_xticks(range(len(all_symbols)))
        ax2.set_xticklabels(all_symbols, rotation=45, ha="right", fontsize=8)
        ax2.set_yticks(range(len(factors)))
        ax2.set_yticklabels(factors)
        ax2.set_title("反转 IC 热力图")
        plt.colorbar(im2, ax=ax2)

        plt.tight_layout()
        heatmap_path = chart_dir / "momentum_fix_heatmap.png"
        fig.savefig(heatmap_path, dpi=100, bbox_inches="tight")
        plt.close()
        logger.info("热力图已保存: %s", heatmap_path)

    except Exception as e:
        logger.warning("图表生成失败: %s", e)

    # 6. 打印汇总
    logger.info("=" * 60)
    logger.info("修复结果汇总")
    logger.info("=" * 60)

    improved_count = sum(1 for r in comparison_results if r["improvement"]["improved"])
    logger.info("改善的因子: %d/%d", improved_count, len(comparison_results))

    for r in comparison_results:
        name = r["factor_name"]
        orig_ic = r["original"]["mean_ic"]
        rev_ic = r["reversed"]["mean_ic"]
        delta = r["improvement"]["ic_delta"]
        status = "✅ 改善" if delta > 0 else "❌ 未改善"
        logger.info(
            "  %s: 原始=%.4f → 反转=%.4f (Δ=%+.4f) %s",
            name,
            orig_ic,
            rev_ic,
            delta,
            status,
        )

    logger.info("=" * 60)
    logger.info("建议:")
    logger.info("  - 改善的因子可使用反转版本入库")
    logger.info("  - 未改善的因子需重新审视核心逻辑")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
