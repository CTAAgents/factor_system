"""
scripts/run_walkforward_optimization.py — 高潜力因子 WalkForward 优化

针对审计中发现的 Top 5 高潜力因子，执行 WalkForward 走航验证优化：
1. fut_bias (IC=0.7025, 跨品种=100%)
2. long_term_reversal (IC=0.3583, 跨品种=100%)
3. fut_hf_trade_imbalance (IC=0.2825, 跨品种=100%)
4. fut_option_pcr (IC=0.2675, 跨品种=100%)
5. fut_short_reversal (IC=0.2532, 跨品种=100%)

输出:
    - WalkForward 优化报告: reports/walkforward_report.json
    - OOS 稳定性对比图: reports/walkforward_oos_comparison.png
    - 参数敏感性分析: reports/walkforward_param_sensitivity.png

用法:
    python scripts/run_walkforward_optimization.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_factor_audit_real import (
    compute_factor_on_panel,
    execute_factor_code,
    get_real_ohlcv_data,
    load_factors_from_yaml,
)
from fts.data_futures import get_futures_provider
from fts.factor_engine.walk_forward import (
    WalkForwardConfig,
    WalkForwardOptimizer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("walkforward")


# ─── Top 5 高潜力因子 ──────────────────────────────────────

TARGET_FACTORS = [
    {"name": "fut_bias", "family": "behavior", "reason": "IC 最高 (0.7025)，跨品种 100%"},
    {"name": "long_term_reversal", "family": "behavior", "reason": "IC 次高 (0.3583)，跨品种 100%"},
    {"name": "fut_hf_trade_imbalance", "family": "high_frequency", "reason": "高频因子 IC=0.2825"},
    {"name": "fut_option_pcr", "family": "options", "reason": "期权因子 IC=0.2675"},
    {"name": "fut_short_reversal", "family": "momentum", "reason": "短期反转 IC=0.2532"},
]


# ─── WalkForward 评估函数 ──────────────────────────────────


def create_evaluate_fn(
    factor_meta: dict,
    forward_period: int = 5,
):
    """创建 WalkForward 评估函数。

    Args:
        factor_meta: 因子元数据
        forward_period: 预测周期（天）

    Returns:
        评估函数 (train_df, oos_df) -> dict
    """

    def evaluate_fn(train_df: pd.DataFrame, oos_df: pd.DataFrame) -> dict:
        factor_code = factor_meta.get("code", "")
        params = factor_meta.get("params", {})

        # 在 OOS 数据上计算因子值
        factor_values = execute_factor_code(factor_code, oos_df, params)
        if factor_values is None or len(factor_values) == 0:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

        # 计算未来收益
        close = oos_df["close"].values
        n = len(close)
        fwd_returns = np.zeros(n)
        if forward_period < n:
            fwd_returns[: n - forward_period] = (close[forward_period:] - close[: n - forward_period]) / np.maximum(
                close[: n - forward_period], 1e-10
            )

        # 对齐
        min_len = min(len(factor_values), len(fwd_returns))
        fv = factor_values[:min_len]
        fr = fwd_returns[:min_len]

        # 移除 NaN
        valid = ~(np.isnan(fv) | np.isnan(fr))
        fv = fv[valid]
        fr = fr[valid]

        if len(fv) < 10:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

        # 计算 IC (Pearson 相关系数)
        ic = np.corrcoef(fv, fr)[0, 1] if np.std(fv) > 1e-10 and np.std(fr) > 1e-10 else 0.0

        # 计算因子收益 (简化: 因子值 * 收益率)
        factor_returns = fv * fr
        sharpe = (
            np.mean(factor_returns) / np.std(factor_returns) * np.sqrt(252) if np.std(factor_returns) > 1e-10 else 0.0
        )

        # 计算换手率 (信号变化率)
        turnover = np.mean(np.abs(np.diff(fv))) if len(fv) > 1 else 0.0

        return {
            "ic": float(ic),
            "sharpe": float(sharpe),
            "turnover": float(turnover),
        }

    return evaluate_fn


# ─── 参数敏感性分析 ──────────────────────────────────────────


def param_sensitivity_analysis(
    factor_meta: dict,
    panel_data: dict[str, pd.DataFrame],
    param_name: str,
    param_values: list,
    forward_period: int = 5,
) -> dict:
    """分析因子对特定参数的敏感性。

    Args:
        factor_meta: 因子元数据
        panel_data: 面板数据
        param_name: 参数名
        param_values: 测试的参数值列表
        forward_period: 预测周期

    Returns:
        参数敏感性分析结果
    """
    original_params = factor_meta.get("params", {}).copy()
    results = []

    for val in param_values:
        test_params = original_params.copy()
        test_params[param_name] = val
        test_meta = factor_meta.copy()
        test_meta["params"] = test_params

        # 计算 IC
        ic_results = compute_factor_on_panel(test_meta, panel_data, forward_period)
        ic_values = [r["ic"] for r in ic_results.values()]
        mean_ic = float(np.mean(ic_values)) if ic_values else 0.0
        pos_ratio = sum(1 for ic in ic_values if ic > 0) / len(ic_values) if ic_values else 0.0

        results.append(
            {
                "param_value": val,
                "mean_ic": mean_ic,
                "positive_ratio": pos_ratio,
                "n_symbols": len(ic_values),
            }
        )

    # 找最优参数
    best = max(results, key=lambda r: r["mean_ic"] * r["positive_ratio"])

    return {
        "param_name": param_name,
        "tested_values": param_values,
        "results": results,
        "best_param": best["param_value"],
        "best_ic": best["mean_ic"],
        "best_ratio": best["positive_ratio"],
    }


# ─── 主流程 ──────────────────────────────────────────────


def main():
    logger.info("=" * 70)
    logger.info("高潜力因子 WalkForward 优化")
    logger.info("=" * 70)

    # 1. 加载因子
    logger.info("加载因子定义...")
    seeds_dir = PROJECT_ROOT / "seeds"
    all_factors = load_factors_from_yaml(seeds_dir, "futures")

    target_factor_names = [f["name"] for f in TARGET_FACTORS]
    target_factors = [f for f in all_factors if f.get("name") in target_factor_names]

    if len(target_factors) < len(TARGET_FACTORS):
        found_names = {f["name"] for f in target_factors}
        missing = [n for n in target_factor_names if n not in found_names]
        logger.warning("部分因子未找到: %s", missing)

    logger.info("找到 %d/%d 个目标因子", len(target_factors), len(TARGET_FACTORS))
    for f in target_factors:
        logger.info("  - %s (家族: %s)", f.get("name"), f.get("_family"))

    # 2. 获取数据
    logger.info("获取真实期货数据...")
    provider = get_futures_provider()
    panel_data = get_real_ohlcv_data(provider, days=500)
    logger.info("数据获取完成: %d 个品种", len(panel_data))

    # 3. WalkForward 配置 - 根据数据长度自适应调整
    n_days = len(next(iter(panel_data.values())))
    logger.info("数据天数: %d", n_days)

    # 自适应配置：500天数据使用较短窗口
    if n_days >= 750:
        wf_config: WalkForwardConfig = {
            "window_years": 3,
            "step_months": 6,
            "min_oos_months": 3,
            "n_windows": 4,
            "min_ic_consistency": 0.5,
            "max_ic_volatility": 0.3,
        }
    elif n_days >= 400:
        wf_config: WalkForwardConfig = {
            "window_years": 1,
            "step_months": 3,
            "min_oos_months": 2,
            "n_windows": 5,
            "min_ic_consistency": 0.5,
            "max_ic_volatility": 0.3,
        }
    else:
        wf_config: WalkForwardConfig = {
            "window_years": 0.5,
            "step_months": 2,
            "min_oos_months": 1,
            "n_windows": 5,
            "min_ic_consistency": 0.5,
            "max_ic_volatility": 0.3,
        }
    optimizer = WalkForwardOptimizer(config=wf_config)
    logger.info("WalkForward 配置: %s", wf_config)

    # 4. 执行 WalkForward 分析
    wf_results: list[dict] = []

    for factor_meta in target_factors:
        name = factor_meta.get("name", "unknown")
        logger.info("-" * 50)
        logger.info("WalkForward 分析: %s", name)

        # 使用第一个品种的数据进行 WalkForward
        first_symbol = next(iter(panel_data))
        df = panel_data[first_symbol].copy()

        # 确保数据有 DatetimeIndex 或 date 列
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            else:
                # 创建伪日期索引（交易日）
                dates = pd.date_range(end=datetime.now(), periods=len(df), freq="B")
                df = df.copy()
                df.index = dates

        df = df.sort_index()
        logger.info(
            "  数据准备完成: %d 行, %s 到 %s",
            len(df),
            df.index[0].strftime("%Y-%m-%d"),
            df.index[-1].strftime("%Y-%m-%d"),
        )

        evaluate_fn = create_evaluate_fn(factor_meta, forward_period=5)

        try:
            result = optimizer.evaluate(df, evaluate_fn)

            logger.info("  完成窗口: %d", result.get("n_windows_completed", 0))
            logger.info("  IC 一致性: %.1f%%", result.get("ic_consistency", 0) * 100)
            logger.info("  IC 波动率: %.4f", result.get("ic_volatility", 0))
            logger.info("  综合评分: %.1f", result.get("consistency_score", 0))
            logger.info("  是否通过: %s", result.get("passed", False))

            wf_results.append(
                {
                    "factor_name": name,
                    "family": factor_meta.get("_family", "unknown"),
                    "walk_forward": {
                        "n_windows_completed": result.get("n_windows_completed", 0),
                        "ic_consistency": result.get("ic_consistency", 0),
                        "ic_volatility": result.get("ic_volatility", 0),
                        "sharpe_volatility": result.get("sharpe_volatility", 0),
                        "consistency_score": result.get("consistency_score", 0),
                        "passed": result.get("passed", False),
                        "windows": result.get("windows", []),
                    },
                }
            )

        except Exception as e:
            logger.error("WalkForward 分析失败: %s", e)
            wf_results.append(
                {
                    "factor_name": name,
                    "family": factor_meta.get("_family", "unknown"),
                    "walk_forward": {
                        "error": str(e),
                        "passed": False,
                    },
                }
            )

    # 5. 参数敏感性分析
    logger.info("=" * 70)
    logger.info("参数敏感性分析")
    logger.info("=" * 70)

    sensitivity_results: list[dict] = []

    for factor_meta in target_factors:
        name = factor_meta.get("name", "unknown")
        params = factor_meta.get("params", {})

        logger.info("-" * 50)
        logger.info("分析因子: %s", name)
        logger.info("  当前参数: %s", params)

        # 识别可优化的参数
        param_to_analyze = None
        param_values = None

        if "lookback" in params:
            param_to_analyze = "lookback"
            current = params["lookback"]
            param_values = sorted(
                set(
                    [
                        max(5, current // 4),
                        max(10, current // 2),
                        current,
                        int(current * 1.5),
                        int(current * 2),
                    ]
                )
            )
        elif "window" in params:
            param_to_analyze = "window"
            current = params["window"]
            param_values = sorted(
                set(
                    [
                        max(1, current // 2),
                        current,
                        int(current * 1.5),
                        current * 2,
                    ]
                )
            )
        elif "lookback_months" in params:
            param_to_analyze = "lookback_months"
            current = params["lookback_months"]
            param_values = [1, 2, 3, 6, 12]

        if param_to_analyze and param_values:
            logger.info("  分析参数 '%s': %s", param_to_analyze, param_values)

            try:
                sens_result = param_sensitivity_analysis(factor_meta, panel_data, param_to_analyze, param_values)
                sensitivity_results.append(sens_result)

                logger.info(
                    "  最优参数: %s=%.2f (IC=%.4f, 比例=%.1f%%)",
                    param_to_analyze,
                    sens_result["best_param"],
                    sens_result["best_ic"],
                    sens_result["best_ratio"] * 100,
                )

                # 更新 wf_results 中的建议参数
                for wf in wf_results:
                    if wf["factor_name"] == name:
                        wf["param_sensitivity"] = sens_result
                        wf["recommended_params"] = {
                            **params,
                            param_to_analyze: sens_result["best_param"],
                        }
                        break

            except Exception as e:
                logger.error("  参数敏感性分析失败: %s", e)
                sensitivity_results.append(
                    {
                        "param_name": param_to_analyze,
                        "error": str(e),
                    }
                )

    # 6. 保存结果
    output_dir = PROJECT_ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    report = {
        "timestamp": timestamp,
        "walk_forward_config": wf_config,
        "walk_forward_results": wf_results,
        "param_sensitivity": sensitivity_results,
        "summary": {
            "total_factors": len(target_factors),
            "passed_wf": sum(1 for r in wf_results if r.get("walk_forward", {}).get("passed", False)),
            "avg_consistency_score": np.mean(
                [
                    r.get("walk_forward", {}).get("consistency_score", 0)
                    for r in wf_results
                    if "error" not in r.get("walk_forward", {})
                ]
            )
            if wf_results
            else 0,
        },
    }

    report_path = output_dir / f"walkforward_report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info("WalkForward 报告已保存: %s", report_path)

    # 7. 生成可视化图表
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        chart_dir = output_dir / "audit_charts"
        chart_dir.mkdir(parents=True, exist_ok=True)

        # 7.1 OOS 稳定性对比图
        fig, ax = plt.subplots(figsize=(12, 6))
        factor_names = [r["factor_name"] for r in wf_results if "error" not in r.get("walk_forward", {})]
        scores = [
            r.get("walk_forward", {}).get("consistency_score", 0)
            for r in wf_results
            if "error" not in r.get("walk_forward", {})
        ]
        consistencies = [
            r.get("walk_forward", {}).get("ic_consistency", 0) * 100
            for r in wf_results
            if "error" not in r.get("walk_forward", {})
        ]

        x = np.arange(len(factor_names))
        width = 0.35

        bars1 = ax.bar(x - width / 2, scores, width, label="综合评分", color="#3498db")
        bars2 = ax.bar(x + width / 2, consistencies, width, label="IC 一致性 (%)", color="#2ecc71")

        ax.set_xlabel("因子")
        ax.set_ylabel("分数 / 百分比")
        ax.set_title("Top 5 高潜力因子 WalkForward OOS 稳定性")
        ax.set_xticks(x)
        ax.set_xticklabels(factor_names, rotation=45, ha="right")
        ax.legend()
        ax.axhline(y=60, color="orange", linestyle="--", alpha=0.7, label="60 分基准")
        ax.axhline(y=50, color="green", linestyle=":", alpha=0.5, label="50% 一致性基准")

        for bars in [bars1, bars2]:
            for bar in bars:
                h = bar.get_height()
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.5, f"{h:.1f}", ha="center", fontsize=8)

        plt.tight_layout()
        chart_path = chart_dir / f"walkforward_oos_comparison_{timestamp}.png"
        fig.savefig(chart_path, dpi=100, bbox_inches="tight")
        plt.close()
        logger.info("OOS 对比图已保存: %s", chart_path)

        # 7.2 IC 时序图 (各窗口 IC)
        valid_results = [r for r in wf_results if "error" not in r.get("walk_forward", {})]
        if valid_results:
            fig, ax = plt.subplots(figsize=(12, 6))

            colors = plt.cm.Set1(np.linspace(0, 1, len(valid_results)))
            for idx, r in enumerate(valid_results):
                windows = r.get("walk_forward", {}).get("windows", [])
                if windows:
                    window_indices = list(range(1, len(windows) + 1))
                    ic_values = [w.get("ic", 0) for w in windows]
                    ax.plot(window_indices, ic_values, "o-", color=colors[idx], label=r["factor_name"], markersize=8)

            ax.axhline(y=0, color="gray", linestyle="-", alpha=0.3)
            ax.axhline(y=0.03, color="green", linestyle="--", alpha=0.3, label="IC=0.03")
            ax.set_xlabel("WalkForward 窗口")
            ax.set_ylabel("样本外 IC")
            ax.set_title("各因子 WalkForward 窗口 IC 时序")
            ax.legend(loc="best", fontsize=8)
            plt.tight_layout()
            ic_path = chart_dir / f"walkforward_ic_timeline_{timestamp}.png"
            fig.savefig(ic_path, dpi=100, bbox_inches="tight")
            plt.close()
            logger.info("IC 时序图已保存: %s", ic_path)

        # 7.3 参数敏感性热力图
        if sensitivity_results:
            valid_sens = [s for s in sensitivity_results if "error" not in s]
            if valid_sens:
                fig, axes = plt.subplots(1, len(valid_sens), figsize=(5 * len(valid_sens), 6))
                if len(valid_sens) == 1:
                    axes = [axes]

                for idx, sens in enumerate(valid_sens):
                    ax = axes[idx]
                    results = sens["results"]
                    x_vals = [r["param_value"] for r in results]
                    y_vals = [r["mean_ic"] for r in results]
                    colors = ["#2ecc71" if v > 0.03 else "#e74c3c" if v < 0 else "#f39c12" for v in y_vals]

                    bars = ax.bar(range(len(x_vals)), y_vals, color=colors)
                    ax.set_xticks(range(len(x_vals)))
                    ax.set_xticklabels([f"{v}" for v in x_vals], rotation=45)
                    ax.axhline(y=0.03, color="green", linestyle="--", alpha=0.5)
                    ax.axhline(y=0, color="gray", linestyle="-", alpha=0.3)
                    ax.set_xlabel(f"{sens['param_name']}")
                    ax.set_ylabel("平均 IC")
                    ax.set_title(f"Param Sensitivity: {sens['param_name']}")

                    for bar, val in zip(bars, y_vals):
                        h = bar.get_height()
                        offset = 0.005 if h >= 0 else -0.015
                        ax.text(
                            bar.get_x() + bar.get_width() / 2,
                            h + offset,
                            f"{val:.3f}",
                            ha="center",
                            va="bottom" if h >= 0 else "top",
                            fontsize=8,
                        )

                plt.suptitle("参数敏感性分析 (绿色=IC>0.03, 红色=IC<0)")
                plt.tight_layout()
                sens_path = chart_dir / f"walkforward_param_sensitivity_{timestamp}.png"
                fig.savefig(sens_path, dpi=100, bbox_inches="tight")
                plt.close()
                logger.info("参数敏感性图已保存: %s", sens_path)

    except Exception as e:
        logger.warning("图表生成失败: %s", e)

    # 8. 打印汇总
    logger.info("=" * 70)
    logger.info("WalkForward 优化结果汇总")
    logger.info("=" * 70)

    for r in wf_results:
        name = r["factor_name"]
        wf = r.get("walk_forward", {})

        if "error" in wf:
            logger.info("  %s: 错误 - %s", name, wf["error"])
            continue

        score = wf.get("consistency_score", 0)
        consistency = wf.get("ic_consistency", 0)
        passed = wf.get("passed", False)
        n_win = wf.get("n_windows_completed", 0)

        status = "✅ 通过" if passed else "❌ 未通过"
        rec_params = r.get("recommended_params", {})

        logger.info(
            "  %s: 评分=%.1f, 一致性=%.0f%%, 窗口=%d %s",
            name,
            score,
            consistency * 100,
            n_win,
            status,
        )

        if rec_params:
            logger.info("    建议参数: %s", rec_params)

    logger.info("=" * 70)

    # 9. 生成优化后的配置文件
    optimized_factors = []
    for r in wf_results:
        if r.get("walk_forward", {}).get("passed", False) and r.get("recommended_params"):
            optimized_factors.append(
                {
                    "name": r["factor_name"],
                    "family": r["family"],
                    "recommended_params": r["recommended_params"],
                    "consistency_score": r["walk_forward"]["consistency_score"],
                }
            )

    if optimized_factors:
        opt_path = output_dir / f"optimized_params_{timestamp}.json"
        with open(opt_path, "w", encoding="utf-8") as f:
            json.dump(optimized_factors, f, ensure_ascii=False, indent=2)
        logger.info("优化参数已保存: %s", opt_path)

    logger.info("WalkForward 优化完成!")


if __name__ == "__main__":
    main()
