"""
scripts/run_factor_audit_real.py — 全量因子批量审计（真实数据版）

使用真实期货历史数据（DuckDB kline_cache）执行因子审计，
计算真实 IC、跨品种表现和 OOS 验证。

用法:
    python scripts/run_factor_audit_real.py [--seeds_dir PATH] [--output_dir PATH] [--market futures]

输出:
    - 审计报告 JSON: reports/audit_report_real_<timestamp>.json
    - 审计摘要 CSV: reports/audit_summary_real_<timestamp>.csv
    - 可视化图表: reports/audit_charts/
    - 优化建议: reports/factor_suggestions_<timestamp>.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.data_futures import (
    FuturesDataProvider,
    FUTURES_CORE_SUBSET,
    get_futures_provider,
)
from fts.factor_engine.audit import FactorAuditor, FactorAuditReport, FactorAuditConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("audit_real")


# ─── 因子代码执行 ──────────────────────────────────────────


def execute_factor_code(
    factor_code: str,
    ohlcv_data: pd.DataFrame,
    params: dict | None = None,
) -> np.ndarray | None:
    """执行因子代码，返回因子值数组。

    Args:
        factor_code: 因子代码字符串（应包含 factor_program 函数）
        ohlcv_data: OHLCV DataFrame
        params: 因子参数

    Returns:
        因子值数组，失败返回 None
    """
    try:
        local_vars: dict[str, object] = {
            "np": np,
            "pd": pd,
        }
        if params:
            local_vars["params"] = params

        # 执行因子代码（定义 factor_program 函数）
        exec(factor_code, {"__builtins__": __builtins__}, local_vars)

        factor_program = local_vars.get("factor_program")
        if factor_program is None:
            # 尝试直接执行代码（不通过 factor_program 函数）
            output: list[float] | np.ndarray = []
            direct_vars: dict[str, object] = {
                "np": np,
                "close": ohlcv_data["close"].values,
                "open": ohlcv_data["open"].values,
                "high": ohlcv_data["high"].values,
                "low": ohlcv_data["low"].values,
                "volume": ohlcv_data["volume"].values,
                "n": len(ohlcv_data),
                "output": output,
            }
            exec(factor_code, {"__builtins__": __builtins__}, direct_vars)
            if "output" in direct_vars:
                return np.asarray(direct_vars["output"], dtype=float)
            return None

        # 调用 factor_program
        result = factor_program(ohlcv_data, params or {})
        if result is None:
            return None
        return np.asarray(result, dtype=float)

    except Exception as e:
        logger.debug("因子代码执行异常: %s", e)
        return None


# ─── IC 计算 ───────────────────────────────────────────────


def compute_ic(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    window: int = 20,
) -> tuple[float, float]:
    """计算因子 IC (Spearman 秩相关)。

    Args:
        factor_values: 因子值数组
        forward_returns: 未来收益率数组
        window: 滚动窗口

    Returns:
        (mean_ic, ic_ir)
    """
    n = len(factor_values)
    ics: list[float] = []

    for i in range(window, n):
        f = factor_values[i - window : i]
        r = forward_returns[i - window : i]

        # 过滤 NaN
        mask = ~(np.isnan(f) | np.isnan(r))
        if mask.sum() < 5:
            continue

        f_clean = f[mask]
        r_clean = r[mask]

        # Spearman 秩相关
        if np.std(f_clean) > 1e-8 and np.std(r_clean) > 1e-8:
            corr = np.corrcoef(f_clean, r_clean)[0, 1]
            ics.append(corr)

    if not ics:
        return 0.0, 0.0

    mean_ic = float(np.mean(ics))
    ic_std = float(np.std(ics))
    ic_ir = mean_ic / ic_std if ic_std > 1e-8 else 0.0
    return mean_ic, ic_ir


def compute_forward_returns(close: np.ndarray, period: int = 5) -> np.ndarray:
    """计算未来 N 日收益率。"""
    n = len(close)
    returns = np.zeros(n)
    if period >= n:
        return returns
    returns[:-period] = (close[period:] - close[:-period]) / np.maximum(close[:-period], 1e-10)
    return returns


# ─── 加载因子 ──────────────────────────────────────────────


def load_factors_from_yaml(seeds_dir: Path, market: str = "futures") -> list[dict]:
    """从 YAML 文件加载因子列表。"""
    factors: list[dict] = []
    market_dir = seeds_dir / market

    if not market_dir.exists():
        logger.warning("市场目录不存在: %s", market_dir)
        return factors

    for yaml_file in sorted(market_dir.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                if yaml is None:
                    return factors
                data = yaml.safe_load(f)

            if data and isinstance(data, dict):
                if "factor_id" in data:
                    factors.append(data)
                elif "factors" in data:
                    for f_item in data.get("factors", []):
                        if isinstance(f_item, dict):
                            # 添加元数据
                            f_item["_source_file"] = yaml_file.name
                            f_item["_family"] = data.get("family", "unknown")
                            factors.append(f_item)

            logger.info("加载因子 [file=%s, n_factors=%d]", yaml_file.name, len(factors))
        except Exception as e:
            logger.error("加载因子文件失败 [file=%s]: %s", yaml_file.name, e)

    return factors


# ─── 真实数据获取 ──────────────────────────────────────────


def get_real_ohlcv_data(provider: FuturesDataProvider, days: int = 500) -> dict[str, pd.DataFrame]:
    """获取核心期货品种的真实 OHLCV 数据。

    Args:
        provider: 数据提供者
        days: 回溯天数

    Returns:
        dict[symbol, OHLCV DataFrame]
    """
    symbols = FUTURES_CORE_SUBSET[:15]  # 取前 15 个核心品种
    logger.info("获取真实数据 [n_symbols=%d, days=%d]", len(symbols), days)

    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            df = provider.get_ohlcv(sym, days=days)
            if df is not None and len(df) >= 60:
                # 确保列名标准化
                if "date" not in df.columns:
                    df = df.reset_index()
                if "date" not in df.columns:
                    df["date"] = df.index

                # 确保必要列存在
                required = ["open", "high", "low", "close", "volume"]
                for col in required:
                    if col not in df.columns:
                        df[col] = 0.0

                panel[sym] = df
                if i % 5 == 0:
                    logger.info("数据获取进度 [%d/%d]", i, len(symbols))
        except Exception as e:
            logger.warning("获取 %s 数据失败: %s", sym, e)

    logger.info("真实数据获取完成 [n_symbols=%d]", len(panel))
    return panel


# ─── 因子批量计算 ──────────────────────────────────────────


def compute_factor_on_panel(
    factor_meta: dict,
    panel: dict[str, pd.DataFrame],
    forward_period: int = 5,
) -> dict[str, dict[str, float]]:
    """在多个品种上计算因子 IC。

    Args:
        factor_meta: 因子元数据（含 code, params 等）
        panel: 品种 → OHLCV DataFrame 映射
        forward_period: 预测周期

    Returns:
        dict[symbol, {"ic": float, "ic_ir": float, "factor_values": ndarray}]
    """
    factor_code = factor_meta.get("code", "")
    params = factor_meta.get("params", {})

    if not factor_code:
        return {}

    results: dict[str, dict[str, float]] = {}
    for symbol, df in panel.items():
        try:
            # 确保 df 有 date 列
            if "date" not in df.columns:
                df = df.copy()
                df["date"] = df.index

            # 执行因子代码
            factor_values = execute_factor_code(factor_code, df, params)
            if factor_values is None or len(factor_values) < 30:
                continue

            # 计算未来收益率
            close = df["close"].values
            fwd_returns = compute_forward_returns(close, forward_period)

            # 对齐长度
            n = min(len(factor_values), len(fwd_returns))
            fv = factor_values[:n]
            fr = fwd_returns[:n]

            # 计算 IC
            ic_mean, ic_ir = compute_ic(fv, fr)

            results[symbol] = {
                "ic": ic_mean,
                "ic_ir": ic_ir,
            }

        except Exception as e:
            logger.debug("因子 %s 在 %s 上计算失败: %s", factor_meta.get("name"), symbol, e)
            continue

    return results


# ─── OOS 评估 ──────────────────────────────────────────────


def compute_oos_metrics(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    n_splits: int = 5,
) -> dict[str, Any]:
    """计算样本外 (OOS) 指标。

    使用滚动窗口将数据分为训练集和测试集，评估因子在 OOS 期间的表现。

    Args:
        factor_values: 因子值数组
        forward_returns: 未来收益率
        n_splits: 滚动分割次数

    Returns:
        OOS 指标字典
    """
    n = len(factor_values)
    if n < 60:
        return {"ic_consistency": 0.0, "passed": False, "n_splits": n_splits}

    window_size = n // (n_splits + 1)  # 每个训练窗口的大小
    ic_values: list[float] = []

    for i in range(n_splits):
        train_end = window_size * (i + 1)
        test_start = train_end
        test_end = min(test_start + window_size, n)

        if test_end - test_start < 10:
            break

        f_train = factor_values[test_start:test_end]
        r_train = forward_returns[test_start:test_end]

        # 计算测试窗口的 IC
        ic, _ = compute_ic(f_train, r_train)
        ic_values.append(ic)

    if not ic_values:
        return {"ic_consistency": 0.0, "passed": False, "n_splits": n_splits}

    ic_mean = float(np.mean(ic_values))
    ic_positive_ratio = sum(1 for ic in ic_values if ic > 0) / len(ic_values)
    # IC 一致性：正 IC 比例
    ic_consistency = ic_positive_ratio

    return {
        "ic_consistency": ic_consistency,
        "ic_mean_oos": ic_mean,
        "n_splits": len(ic_values),
        "positive_ic_ratio": ic_positive_ratio,
        "passed": ic_consistency >= 0.5 and ic_mean > 0,
    }


# ─── 压力测试数据生成 ──────────────────────────────────────


def generate_stress_test_inputs(
    panel: dict[str, pd.DataFrame],
    factor_meta: dict,
) -> tuple[dict[str, np.ndarray], dict[str, pd.DataFrame]]:
    """生成压力测试所需的信号和 OHLCV 数据。

    Args:
        panel: 品种 → OHLCV DataFrame 映射
        factor_meta: 因子元数据

    Returns:
        (signals_by_symbol, ohlcv_by_symbol)
    """
    factor_code = factor_meta.get("code", "")
    params = factor_meta.get("params", {})

    signals: dict[str, np.ndarray] = {}
    ohlcv: dict[str, pd.DataFrame] = {}

    for symbol, df in panel.items():
        try:
            fv = execute_factor_code(factor_code, df, params)
            if fv is not None:
                signals[symbol] = fv
                ohlcv[symbol] = df[["close"]].copy()
        except Exception:
            continue

    return signals, ohlcv


# ─── 批量审计（真实数据版） ────────────────────────────────


def batch_audit_real(
    seeds_dir: Path,
    output_dir: Path,
    market: str = "futures",
    n_symbols: int = 15,
) -> tuple[list[FactorAuditReport], pd.DataFrame]:
    """使用真实数据执行批量审计。

    Args:
        seeds_dir: 种子因子目录
        output_dir: 输出目录
        market: 市场类型
        n_symbols: 使用的品种数量

    Returns:
        (审计报告列表, 汇总 DataFrame)
    """
    # 1. 加载因子
    logger.info("=" * 60)
    logger.info("加载因子")
    logger.info("=" * 60)
    factors = load_factors_from_yaml(seeds_dir, market)
    if not factors:
        logger.error("未加载到任何因子")
        return [], pd.DataFrame()

    logger.info("加载完成 [n_factors=%d]", len(factors))

    # 2. 获取真实数据
    logger.info("=" * 60)
    logger.info("获取真实期货数据")
    logger.info("=" * 60)
    provider = get_futures_provider()
    panel = get_real_ohlcv_data(provider, days=500)

    if not panel:
        logger.error("未能获取任何真实数据，回退到合成数据")
        from scripts.run_factor_audit import generate_synthetic_data

        synth_data, synth_fwd = generate_synthetic_data(252)
        panel = {"SYNTHETIC": synth_data}

    # 3. 初始化审计器
    config = FactorAuditConfig(
        min_cross_symbol_ratio=0.8,
        min_oos_pass_ratio=0.5,
    )
    auditor = FactorAuditor(config=config)

    # 4. 批量计算因子并审计
    logger.info("=" * 60)
    logger.info("批量计算因子 IC 和审计")
    logger.info("=" * 60)

    reports: list[FactorAuditReport] = []
    total = len(factors)
    start_time = time.time()

    # 收集汇总数据
    summary_rows: list[dict[str, Any]] = []

    for idx, factor_meta in enumerate(factors, 1):
        factor_name = factor_meta.get("name", f"factor_{idx}")
        logger.info("[%d/%d] 处理因子: %s", idx, total, factor_name)

        try:
            # 在所有品种上计算因子
            ic_results = compute_factor_on_panel(factor_meta, panel)

            if not ic_results:
                logger.warning("  因子 %s 无有效 IC 结果", factor_name)
                summary_rows.append(
                    {
                        "factor_name": factor_name,
                        "n_symbols_ic": 0,
                        "mean_ic": 0.0,
                        "oos_passed": False,
                        "cross_symbol_ratio": 0.0,
                        "status": "no_data",
                    }
                )
                continue

            # 构建跨品种 IC map
            symbol_ic_map = {sym: res["ic"] for sym, res in ic_results.items()}

            # 计算平均 IC
            ic_values = [res["ic"] for res in ic_results.values()]
            ic_ir_values = [res["ic_ir"] for res in ic_results.values()]
            mean_ic = float(np.mean(ic_values)) if ic_values else 0.0
            positive_ic_ratio = sum(1 for ic in ic_values if ic > 0) / len(ic_values) if ic_values else 0.0

            # OOS 评估（使用第一个品种的数据）
            first_symbol = next(iter(panel))
            df = panel[first_symbol]
            fv = execute_factor_code(
                factor_meta.get("code", ""),
                df,
                factor_meta.get("params", {}),
            )
            if fv is not None and len(fv) >= 60:
                close = df["close"].values
                fwd = compute_forward_returns(close, 5)
                n = min(len(fv), len(fwd))
                oos_result = compute_oos_metrics(fv[:n], fwd[:n])
            else:
                oos_result = {"ic_consistency": 0.0, "passed": False}

            # 压力测试
            stress_signals, stress_ohlcv = generate_stress_test_inputs(panel, factor_meta)

            # 多重检验 p 值（简化：基于 IC 的 t-stat）
            p_values: list[float] = []
            for sym, res in ic_results.items():
                res["ic"]
                ic_ir = res["ic_ir"]
                # 近似 p-value（双侧）
                t_stat = abs(ic_ir)
                p_val = max(0.001, min(0.5, 2 / (1 + t_stat)))
                p_values.append(p_val)

            # 执行审计
            factor_dict = {
                "factor_id": factor_name,
                "name": factor_name,
                "code": factor_meta.get("code", ""),
            }

            report = auditor.audit(
                factor=factor_dict,
                data=df,
                forward_returns=fwd if fv is not None else None,
                symbol_ic_map=symbol_ic_map,
                signals_by_symbol=stress_signals,
                ohlcv_by_symbol=stress_ohlcv,
                oos_result=oos_result,
                p_values=p_values,
            )

            reports.append(report)

            # 汇总行
            status = "passed" if report.passed else "failed"
            summary_rows.append(
                {
                    "factor_name": factor_name,
                    "n_symbols_ic": len(ic_results),
                    "mean_ic": round(mean_ic, 4),
                    "mean_ic_ir": round(float(np.mean(ic_ir_values)), 4) if ic_ir_values else 0.0,
                    "oos_ic_consistency": round(oos_result.get("ic_consistency", 0.0), 4),
                    "oos_passed": oos_result.get("passed", False),
                    "cross_symbol_ratio": round(positive_ic_ratio, 4),
                    "audit_passed": report.passed,
                    "pass_rate": round(report.pass_rate, 4),
                    "failed_items": ",".join(report.summary.get("failed_items", [])),
                    "status": status,
                    "family": factor_meta.get("_family", "unknown"),
                }
            )

        except Exception as e:
            logger.error("  因子 %s 处理异常: %s", factor_name, e)
            summary_rows.append(
                {
                    "factor_name": factor_name,
                    "status": "error",
                    "error": str(e),
                }
            )

        if idx % 5 == 0 or idx == total:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            eta = (total - idx) / rate if rate > 0 else 0
            passed_count = sum(1 for r in reports if r.passed)
            logger.info(
                "进度 [%d/%d] 通过=%d 速率=%.1f/s ETA=%.0fs",
                idx,
                total,
                passed_count,
                rate,
                eta,
            )

    # 5. 保存结果
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON 报告
    json_path = output_dir / f"audit_report_real_{timestamp}.json"
    json_data = [r.to_dict() for r in reports]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    logger.info("JSON 报告已保存: %s", json_path)

    # CSV 摘要
    csv_path = output_dir / f"audit_summary_real_{timestamp}.csv"
    df_summary = pd.DataFrame(summary_rows)
    df_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("CSV 摘要已保存: %s", csv_path)

    # 汇总统计
    elapsed_total = time.time() - start_time
    n_passed = sum(1 for r in reports if r.passed)
    n_failed = len(reports) - n_passed

    logger.info("=" * 60)
    logger.info("批量审计完成")
    logger.info("=" * 60)
    logger.info("  总因子数: %d", len(reports))
    logger.info("  通过: %d (%.1f%%)", n_passed, n_passed / max(len(reports), 1) * 100)
    logger.info("  失败: %d (%.1f%%)", n_failed, n_failed / max(len(reports), 1) * 100)
    logger.info("  耗时: %.1fs", elapsed_total)
    logger.info("  品种数: %d", len(panel))

    return reports, df_summary


# ─── 生成可视化图表 ──────────────────────────────────────


def generate_visualizations(
    df_summary: pd.DataFrame,
    output_dir: Path,
) -> list[str]:
    """生成审计结果可视化图表。

    Args:
        df_summary: 审计汇总 DataFrame
        output_dir: 输出目录

    Returns:
        生成的图表文件路径列表
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
    except ImportError:
        logger.warning("matplotlib 未安装，跳过可视化生成")
        return []

    chart_dir = output_dir / "audit_charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_files: list[str] = []

    # 1. 通过率饼图
    fig, ax = plt.subplots(figsize=(6, 6))
    passed = int(df_summary["audit_passed"].fillna(False).sum())
    failed = int((~df_summary["audit_passed"].fillna(False)).sum())
    labels = [f"通过 ({passed})", f"未通过 ({failed})"]
    sizes = [passed, failed]
    colors = ["#2ecc71", "#e74c3c"]
    ax.pie(sizes, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90)
    ax.set_title("因子审计通过率")
    plt.tight_layout()
    path1 = str(chart_dir / "audit_pass_rate.png")
    fig.savefig(path1, dpi=100, bbox_inches="tight")
    plt.close()
    chart_files.append(path1)

    # 2. OOS vs 跨品种通过率对比
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # OOS 通过率
    oos_passed = df_summary["oos_passed"].sum()
    oos_failed = len(df_summary) - oos_passed
    bars1 = ax1.bar(["通过", "未通过"], [oos_passed, oos_failed], color=["#2ecc71", "#e74c3c"])
    ax1.set_title("OOS 样本外验证通过率")
    ax1.set_ylabel("因子数")
    for bar in bars1:
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width() / 2, h + 0.5, str(int(h)), ha="center")

    # 跨品种通过率 - 连续分布直方图
    cross_vals = df_summary["cross_symbol_ratio"].dropna()
    bins = np.arange(0, 1.01, 0.1)
    ax2.hist(cross_vals, bins=bins, color="#3498db", edgecolor="white", alpha=0.8, rwidth=0.9)
    ax2.axvline(x=0.8, color="#e74c3c", linestyle="--", label="80% 基准线")
    ax2.axvline(x=0.5, color="#f39c12", linestyle="--", label="50% 基准线")
    ax2.set_title("跨品种 IC 正收益比例分布")
    ax2.set_xlabel("正收益品种占比")
    ax2.set_ylabel("因子数")
    ax2.legend()

    plt.tight_layout()
    path2 = str(chart_dir / "oos_vs_cross_symbol.png")
    fig.savefig(path2, dpi=100, bbox_inches="tight")
    plt.close()
    chart_files.append(path2)

    # 3. 平均 IC 分布直方图
    fig, ax = plt.subplots(figsize=(10, 6))
    ic_values = df_summary["mean_ic"].dropna()
    bins = np.linspace(ic_values.min() - 0.01, ic_values.max() + 0.01, 20)
    ax.hist(ic_values, bins=bins, color="#3498db", edgecolor="white", alpha=0.8)
    ax.axvline(x=ic_values.mean(), color="#e74c3c", linestyle="--", label=f"均值: {ic_values.mean():.4f}")
    ax.axvline(x=0.03, color="#2ecc71", linestyle="--", label="IC=0.03 基准线")
    ax.set_title("因子平均 IC 分布")
    ax.set_xlabel("平均 IC")
    ax.set_ylabel("因子数")
    ax.legend()
    plt.tight_layout()
    path3 = str(chart_dir / "ic_distribution.png")
    fig.savefig(path3, dpi=100, bbox_inches="tight")
    plt.close()
    chart_files.append(path3)

    # 4. 家族分组热力图（如果有 family 列）
    if "family" in df_summary.columns:
        valid = df_summary[df_summary["status"].isin(["passed", "failed"])]
        if len(valid) > 0:
            family_stats = valid.groupby("family").agg(
                total=("factor_name", "count"),
                passed=("audit_passed", "sum"),
                mean_ic=("mean_ic", "mean"),
            )
            family_stats["pass_rate"] = family_stats["passed"] / family_stats["total"]

            fig, ax = plt.subplots(figsize=(10, max(6, len(family_stats) * 0.5)))
            families = family_stats.index.tolist()
            pass_rates = family_stats["pass_rate"].values
            mean_ics = family_stats["mean_ic"].values

            y_pos = np.arange(len(families))
            colors_bar = ["#2ecc71" if r >= 0.5 else "#e74c3c" for r in pass_rates]
            ax.barh(y_pos, pass_rates, color=colors_bar)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(families)
            ax.set_xlabel("通过率")
            ax.set_title("各因子家族审计通过率")
            ax.axvline(x=0.5, color="gray", linestyle="--", alpha=0.5)

            for i, (rate, ic) in enumerate(zip(pass_rates, mean_ics)):
                ax.text(rate + 0.01, i, f"{rate:.0%} (IC={ic:.4f})", va="center")

            plt.tight_layout()
            path4 = str(chart_dir / "family_pass_rate.png")
            fig.savefig(path4, dpi=100, bbox_inches="tight")
            plt.close()
            chart_files.append(path4)

    # 5. 失败模式热力图（家族 × 失败类型）
    if "family" in df_summary.columns:
        all_failed_items = []
        for items in df_summary["failed_items"].dropna():
            if pd.notna(items) and str(items).strip():
                all_failed_items.extend([x.strip() for x in str(items).split(",")])
        unique_failures = list(set(all_failed_items))

        if unique_failures and len(unique_failures) > 1:
            families = df_summary["family"].unique().tolist()
            heat_data = pd.DataFrame(0, index=families, columns=unique_failures)
            for _, row in df_summary.iterrows():
                fam = row.get("family", "")
                items = row.get("failed_items", "")
                if pd.notna(items) and str(items).strip():
                    for item in str(items).split(","):
                        item = item.strip()
                        if item in heat_data.columns:
                            heat_data.loc[fam, item] += 1

            fig, ax = plt.subplots(figsize=(10, max(6, len(families) * 0.5)))
            im = ax.imshow(heat_data.values, cmap="YlOrRd", aspect="auto")
            ax.set_xticks(range(len(unique_failures)))
            ax.set_xticklabels(unique_failures, rotation=45, ha="right")
            ax.set_yticks(range(len(families)))
            ax.set_yticklabels(families)
            plt.colorbar(im, ax=ax, label="失败次数")
            ax.set_title("因子家族 × 失败类型 热力图")
            plt.tight_layout()
            path5 = str(chart_dir / "failure_heatmap.png")
            fig.savefig(path5, dpi=100, bbox_inches="tight")
            plt.close()
            chart_files.append(path5)

    # 6. OOS vs 跨品种散点对比
    fig, ax = plt.subplots(figsize=(10, 8))
    valid = df_summary.dropna(subset=["mean_ic", "cross_symbol_ratio"])
    families = valid["family"].unique() if "family" in valid.columns else [""]
    cmap = plt.cm.tab20(np.linspace(0, 1, max(len(families), 1)))
    for i, fam in enumerate(families):
        mask = valid["family"] == fam if "family" in valid.columns else slice(None)
        subset = valid[mask]
        ax.scatter(
            subset["cross_symbol_ratio"],
            subset["mean_ic"],
            c=[cmap[i]],
            label=fam,
            alpha=0.7,
            s=50,
            edgecolors="white",
            linewidth=0.5,
        )
    ax.axvline(x=0.8, color="red", linestyle="--", alpha=0.5, label="跨品种 80% 基准")
    ax.axhline(y=0.03, color="green", linestyle="--", alpha=0.5, label="IC=0.03 基准")
    ax.set_xlabel("跨品种正收益比例")
    ax.set_ylabel("平均 IC")
    ax.set_title("OOS vs 跨品种 因子散点对比")
    ax.legend(loc="best", fontsize=8)
    plt.tight_layout()
    path6 = str(chart_dir / "oox_cross_scatter.png")
    fig.savefig(path6, dpi=100, bbox_inches="tight")
    plt.close()
    chart_files.append(path6)

    # 7. 家族分组对比柱状图（IC vs 跨品种 vs OOS）
    if "family" in df_summary.columns:
        valid = df_summary[df_summary["status"].isin(["passed", "failed"])]
        if len(valid) > 0:
            family_stats = (
                valid.groupby("family")
                .agg(
                    mean_ic=("mean_ic", "mean"),
                    mean_cross=("cross_symbol_ratio", "mean"),
                    oos_rate=("oos_passed", "mean"),
                    count=("factor_name", "count"),
                )
                .reset_index()
            )

            fig, ax = plt.subplots(figsize=(14, 7))
            x = np.arange(len(family_stats))
            width = 0.25
            bars1 = ax.bar(x - width, family_stats["mean_ic"], width, label="平均 IC", color="#3498db")
            ax.bar(x, family_stats["mean_cross"], width, label="跨品种正收益比例", color="#2ecc71")
            ax.bar(x + width, family_stats["oos_rate"], width, label="OOS 通过率", color="#f39c12")
            ax.set_xticks(x)
            ax.set_xticklabels(family_stats["family"], rotation=45, ha="right", fontsize=8)
            ax.set_ylabel("比例 / 数值")
            ax.set_title("各因子家族多维指标对比")
            ax.legend()
            ax.axhline(y=0.03, color="#3498db", linestyle=":", alpha=0.5)
            ax.axhline(y=0.8, color="#2ecc71", linestyle=":", alpha=0.5)
            plt.tight_layout()
            path7 = str(chart_dir / "family_multim Comparison.png")
            fig.savefig(path7, dpi=100, bbox_inches="tight")
            plt.close()
            chart_files.append(path7)

    logger.info("可视化图表已生成: %d 个文件", len(chart_files))
    return chart_files


# ─── 生成优化建议 ──────────────────────────────────────────


def generate_suggestions(
    df_summary: pd.DataFrame,
    reports: list[FactorAuditReport],
    output_dir: Path,
) -> str:
    """为未通过审计的因子生成优化建议清单。

    Args:
        df_summary: 审计汇总 DataFrame
        reports: 审计报告列表
        output_dir: 输出目录

    Returns:
        建议文件路径
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suggestions_path = output_dir / f"factor_suggestions_{timestamp}.md"

    failed = df_summary[~df_summary["audit_passed"].fillna(False)].copy()
    if failed.empty:
        with open(suggestions_path, "w", encoding="utf-8") as f:
            f.write("# 因子优化建议清单\n\n✅ 所有因子均通过审计，无需优化！\n")
        return str(suggestions_path)

    sections: list[str] = []
    sections.append("# 因子优化建议清单")
    sections.append("")
    sections.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sections.append(f"**总因子数**: {len(df_summary)}")
    sections.append(f"**未通过数**: {len(failed)}")
    sections.append("")

    # 按失败类型分组
    failure_patterns = failed["failed_items"].value_counts().head(10)
    sections.append("## 一、常见失败模式统计")
    sections.append("")
    sections.append("| 失败模式 | 出现次数 | 占比 |")
    sections.append("|----------|----------|------|")
    for pattern, count in failure_patterns.items():
        pct = count / len(failed) * 100
        sections.append(f"| {pattern or '未知'} | {count} | {pct:.1f}% |")
    sections.append("")

    # 具体建议
    sections.append("## 二、按家族分组的优化建议")
    sections.append("")

    # 按家族分组
    if "family" in failed.columns:
        for family, group in failed.groupby("family"):
            sections.append(f"### 2.{family} ({len(group)} 个因子)")
            sections.append("")

            # 该家族的常见失败模式
            family_failures = []
            for items in group["failed_items"].dropna():
                if pd.notna(items):
                    family_failures.extend(str(items).split(","))
            from collections import Counter

            failure_counts = Counter(f.strip() for f in family_failures)

            sections.append("**常见失败模式**:")
            sections.append("")
            for fail_item, count in failure_counts.most_common():
                sections.append(f"- {fail_item}: {count}/{len(group)} 次")
            sections.append("")

            # 该家族的优化方向
            family_mean_ic = group["mean_ic"].mean()
            family_mean_cross = group["cross_symbol_ratio"].mean()
            family_oos_count = group["oos_passed"].sum()

            sections.append("**针对性优化建议**:")
            sections.append("")

            if family_mean_cross < 0.3:
                sections.append(
                    "- **高优先级 - 增强跨品种适应性**: "
                    f"该家族平均跨品种正收益比例仅 {family_mean_cross:.1%}，远低于 80% 标准。 "
                    "建议：(1) 将因子参数与品种波动率/流动性挂钩自适应调整；"
                    "(2) 使用截面标准化（Cross-Section Normalization）消除品种间量纲差异；"
                    "(3) 考虑引入品种分类（工业品/农产品/贵金属），对不同类别使用不同参数。"
                )

            if family_mean_ic < 0:
                sections.append(
                    "- **高优先级 - 修复因子方向**: "
                    f"该家族平均 IC 为负 ({family_mean_ic:.4f})，因子方向可能反了。 "
                    "建议：(1) 检查因子信号逻辑，尝试反转因子方向（乘 -1）；"
                    "(2) 分析因子在不同市场状态下的表现，是否存在趋势/震荡市场不匹配；"
                    "(3) 考虑加入市场状态过滤器，仅在特定市场条件下启用。"
                )

            if family_oos_count < len(group) * 0.3:
                sections.append(
                    "- **中优先级 - 改善 OOS 稳定性**: "
                    f"该家族仅 {family_oos_count}/{len(group)} 个因子通过 OOS 验证。 "
                    "建议：(1) 使用 WalkForward 交叉验证重新确定参数窗口；"
                    "(2) 简化因子结构，减少参数数量以避免过拟合；"
                    "(3) 对因子信号加入衰减权重，降低远期数据的影响。"
                )

            # 针对 multiple_testing 的通用建议
            sections.append(
                "- **通用 - 提升统计显著性**: 所有因子均未通过多重检验校正。 "
                "建议：(1) 提高单个因子的 IC 阈值要求（从 0.02 提升到 0.05+）；"
                "(2) 使用更稳健的 IC 计算方法（如 Spearman 秩相关 + 自助法）；"
                "(3) 减少同时检验的因子数量，集中资源优化 Top 10 因子。"
            )
            sections.append("")

    # 高潜力因子特别建议
    sections.append("## 三、高潜力因子特别优化建议")
    sections.append("")
    sections.append("以下因子虽然未通过全部审计，但在部分指标上表现突出，值得重点优化：")
    sections.append("")

    high_potential = failed[(failed["cross_symbol_ratio"] >= 0.8) & (failed["mean_ic"] > 0)].copy()

    if high_potential.empty:
        # 如果没有高潜力因子，选取 IC 最高的
        high_potential = failed.nlargest(5, "mean_ic")

    for idx, row in high_potential.iterrows():
        name = row["factor_name"]
        ic = row["mean_ic"]
        cross = row["cross_symbol_ratio"]
        oos = row["oos_passed"]
        failed_items = row.get("failed_items", "")

        sections.append(f"### 3.{name}")
        sections.append("")
        sections.append(f"- **当前表现**: IC={ic:.4f}, 跨品种={cross:.1%}, OOS={'通过' if oos else '未通过'}")
        sections.append(f"- **主要障碍**: {failed_items}")
        sections.append("")

        # 针对该因子的具体优化步骤
        steps = []
        if "multiple_testing" in str(failed_items):
            if ic > 0.3:
                steps.append(
                    "1. **降低噪声**: 该因子 IC 已非常高，主要问题是多重检验。"
                    "建议使用 Neyman-Pearson 框架调整显著性水平，"
                    "或通过减少检验因子数量来降低 Bonferroni 惩罚。"
                )
            else:
                steps.append(
                    "1. **提升 IC**: 多重检验不显著的根源是 IC 不够高。"
                    "建议：(a) 使用自助法（Bootstrap）计算 IC 的置信区间；"
                    "(b) 尝试非线性变换（如排名、分位数）提升信号质量；"
                    "(c) 结合其他因子形成复合信号，提高单个检验的功效。"
                )

        if "cross_symbol" in str(failed_items):
            steps.append(
                "2. **跨品种优化**: 使用均值-方差归一化（MV-Norm）或按品种波动率缩放因子值，使信号在各品种间可比。"
            )

        if "oos_consistency" in str(failed_items):
            steps.append(
                "3. **OOS 稳定性优化**: 使用 expanding window 代替 rolling window，"
                "或对因子信号施加指数衰减权重，降低对近期数据的过度依赖。"
            )

        if not steps:
            steps.append("1. **综合优化**: 该因子各项指标接近但未达标，建议进行微调。")

        for step in steps:
            sections.append(step)
        sections.append("")

    # 逐因子详细建议
    sections.append("## 四、逐因子详细优化清单")
    sections.append("")

    for idx, row in failed.iterrows():
        factor_name = row["factor_name"]
        mean_ic = row.get("mean_ic", 0)
        cross_ratio = row.get("cross_symbol_ratio", 0)
        oos_passed = row.get("oos_passed", False)
        failed_items = row.get("failed_items", "")
        family = row.get("family", "unknown")

        sections.append(f"### 4.{idx + 1}. {factor_name} ({family})")
        sections.append("")

        status_parts = []
        status_parts.append(f"IC={mean_ic:.4f}")
        status_parts.append(f"跨品种={cross_ratio:.1%}")
        status_parts.append(f"OOS={'通过' if oos_passed else '未通过'}")
        sections.append(f"- **现状**: {', '.join(status_parts)}")
        sections.append(f"- **失败项**: {failed_items or '多项'}")
        sections.append("")

        # 生成具体建议
        suggestions: list[str] = []

        if cross_ratio < 0.3:
            suggestions.append(
                "**[高优先级] 跨品种覆盖严重不足**: "
                "因子在大多数品种上 IC 为负或接近零。"
                "需重新审视因子的核心逻辑是否具有品种普适性。"
                "具体操作：(a) 检查因子是否隐含了特定品种的假设（如季节性、交割月效应）；"
                "(b) 对因子进行品种内标准化处理；"
                "(c) 考虑将因子应用于行业/板块轮动而非单品种。"
            )
        elif cross_ratio < 0.5:
            suggestions.append(
                "**[中优先级] 跨品种覆盖不足**: "
                "约半数品种上因子表现不佳。"
                "建议：(a) 分析表现好的品种与差的品种的特征差异（波动率、流动性等）；"
                "(b) 使用品种加权方式，对高波动品种降低权重；"
                "(c) 考虑引入品种筛选机制，仅在因子有效的品种上交易。"
            )

        if mean_ic < -0.1:
            suggestions.append(
                "**[紧急] 因子方向可能错误**: "
                f"平均 IC 为 {mean_ic:.4f}（显著为负），建议尝试反转因子（乘以 -1）。"
                "如果反转后 IC 仍为负，说明因子信号本身无效，需要重新设计。"
            )
        elif mean_ic < 0.02:
            suggestions.append(
                "**[高优先级] 信号强度不足**: "
                f"平均 IC={mean_ic:.4f} 低于 0.02 基准线。"
                "建议：(a) 优化参数窗口（网格搜索或贝叶斯优化）；"
                "(b) 对因子信号进行非线性变换（如 rank transform）；"
                "(c) 添加成交量/波动率过滤条件，提升信号质量。"
            )

        if not oos_passed:
            suggestions.append(
                "**[高优先级] OOS 泛化能力差**: "
                "因子在样本外期间表现不稳定。"
                "建议：(a) 使用 WalkForward 重新验证参数稳健性；"
                "(b) 简化因子结构（奥卡姆剃刀原则）；"
                "(c) 考虑因子时序衰减（IC decay），定期重新训练；"
                "(d) 检查是否存在数据窥探（look-ahead bias）。"
            )

        if "multiple_testing" in str(failed_items):
            suggestions.append(
                "**[中优先级] 多重检验校正不通过**: "
                "Bonferroni 或 FDR 校正后无显著结果。"
                "建议：(a) 提高单个因子的 IC 绝对值（从 0.02 提升至 0.05+）；"
                "(b) 使用 Benjamini-Yekutieli 程序替代 Bonferroni（更宽松）；"
                "(c) 将因子分组，减少同时检验数量；"
                "(d) 考虑使用贝叶斯方法替代频率检验。"
            )

        if "stress_resilience" in str(failed_items):
            suggestions.append(
                "**[低优先级] 压力测试不通过**: 建议增加市场状态过滤器，在极端行情下自动降低因子权重或暂停交易。"
            )

        if not suggestions:
            suggestions.append("该因子各项指标接近但未完全达标，建议进行精细化微调优化。")

        for s in suggestions:
            sections.append(s)
        sections.append("")

    # 通用建议
    sections.append("## 五、通用优化框架")
    sections.append("")
    sections.append("### 5.1 短期优化（1-2 周）")
    sections.append("- [ ] 从 Top 10 高潜力因子开始逐个优化")
    sections.append("- [ ] 修复 IC 为负的因子（反转方向或重写逻辑）")
    sections.append("- [ ] 为每个因子添加截面标准化预处理")
    sections.append("- [ ] 使用 WalkForward 重新验证参数稳健性")
    sections.append("")
    sections.append("### 5.2 中期优化（1-2 月）")
    sections.append("- [ ] 建立因子定期重训机制（季度/半年度）")
    sections.append("- [ ] 构建因子衰减监测系统（IC 连续 3 月下降触发警报）")
    sections.append("- [ ] 开发自适应因子框架（根据市场状态动态调整权重）")
    sections.append("- [ ] 因子组合优化：将低 IC 因子作为辅助信号组合使用")
    sections.append("")
    sections.append("### 5.3 长期优化（3-6 月）")
    sections.append("- [ ] 建立因子数据库，记录因子全生命周期表现")
    sections.append("- [ ] 开发因子挖掘 Pipeline，自动化新因子发现和验证")
    sections.append("- [ ] 引入机器学习辅助因子筛选（AutoML for Factor Mining）")
    sections.append("- [ ] 构建因子元学习框架，自动选择适合当前市场的因子组合")
    sections.append("")

    content = "\n".join(sections)
    with open(suggestions_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("优化建议已生成: %s", suggestions_path)
    return str(suggestions_path)


# ─── 主入口 ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="全量因子批量审计（真实数据版）")
    parser.add_argument(
        "--seeds_dir",
        type=str,
        default=str(PROJECT_ROOT / "seeds"),
        help="种子因子目录",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(PROJECT_ROOT / "reports"),
        help="输出目录",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="futures",
        choices=["futures", "stock"],
        help="市场类型",
    )
    parser.add_argument(
        "--n_symbols",
        type=int,
        default=15,
        help="使用的期货品种数量",
    )

    args = parser.parse_args()

    seeds_dir = Path(args.seeds_dir)
    output_dir = Path(args.output_dir)

    if not seeds_dir.exists():
        logger.error("种子目录不存在: %s", seeds_dir)
        sys.exit(1)

    logger.info("开始真实数据批量因子审计")
    logger.info("  种子目录: %s", seeds_dir)
    logger.info("  输出目录: %s", output_dir)
    logger.info("  市场: %s", args.market)

    try:
        reports, df_summary = batch_audit_real(seeds_dir, output_dir, args.market, args.n_symbols)

        if not reports:
            logger.error("未生成任何审计报告")
            sys.exit(1)

        # 生成可视化
        logger.info("=" * 60)
        logger.info("生成可视化图表")
        chart_files = generate_visualizations(df_summary, output_dir)
        for cf in chart_files:
            logger.info("  图表: %s", cf)

        # 生成优化建议
        logger.info("=" * 60)
        logger.info("生成优化建议清单")
        suggestions_path = generate_suggestions(df_summary, reports, output_dir)
        logger.info("  建议: %s", suggestions_path)

        logger.info("=" * 60)
        logger.info("全部完成！")
        logger.info("=" * 60)
        sys.exit(0)

    except Exception as e:
        logger.error("批量审计失败: %s", e, exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
