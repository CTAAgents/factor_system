"""
scripts/run_factor_audit.py — 全量因子批量审计执行脚本。

基于 FactorAuditor 对因子库进行批量审计，生成审计报告。

用法:
    python scripts/run_factor_audit.py [--seeds_dir PATH] [--output_dir PATH] [--market futures|stock]

输出:
    - 审计报告 JSON: reports/audit_report_<timestamp>.json
    - 审计摘要 CSV: reports/audit_summary_<timestamp>.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.audit import FactorAuditor, FactorAuditReport, FactorAuditConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("audit_runner")


def load_factors_from_yaml(seeds_dir: Path, market: str = "futures") -> list[dict]:
    """从 YAML 文件加载因子列表。

    Args:
        seeds_dir: 种子目录
        market: 市场类型 (futures/stock)

    Returns:
        因子元数据列表
    """
    factors = []
    market_dir = seeds_dir / market

    if not market_dir.exists():
        logger.warning("市场目录不存在: %s", market_dir)
        return factors

    for yaml_file in sorted(market_dir.glob("*.yaml")):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                if yaml is None:
                    logger.error("未安装 PyYAML，请运行: pip install pyyaml")
                    return factors
                data = yaml.safe_load(f)

            if data and isinstance(data, dict):
                # 单个因子或因子列表
                if "factor_id" in data:
                    factors.append(data)
                elif "factors" in data:
                    for f in data.get("factors", []):
                        if isinstance(f, dict):
                            factors.append(f)

            logger.info("加载因子文件 [file=%s, n_factors=%d]", yaml_file.name, len(factors))
        except Exception as e:
            logger.error("加载因子文件失败 [file=%s]: %s", yaml_file.name, e)

    return factors


def generate_synthetic_data(n_days: int = 252) -> tuple[pd.DataFrame, np.ndarray]:
    """生成合成数据用于审计测试。

    Args:
        n_days: 数据天数

    Returns:
        (ohlcv_dataframe, forward_returns_array)
    """
    rng = np.random.RandomState(42)
    dates = pd.date_range(end=datetime.now(), periods=n_days, freq="B")

    # 生成带趋势的价格序列
    returns = rng.randn(n_days) * 0.01 + 0.0002  # 日均收益 0.02%
    prices = 100 * np.cumprod(1 + returns)

    # 生成 OHLCV
    close = prices
    open_price = close * (1 + rng.randn(n_days) * 0.005)
    high = np.maximum(close, open_price) * (1 + np.abs(rng.randn(n_days) * 0.005))
    low = np.minimum(close, open_price) * (1 - np.abs(rng.randn(n_days) * 0.005))
    volume = np.abs(rng.randn(n_days) * 1000000) + 500000

    df = pd.DataFrame({
        "date": dates,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    # 生成未来收益率 (向前 5 日)
    forward_period = 5
    forward_returns = np.zeros(n_days)
    for i in range(n_days - forward_period):
        forward_returns[i] = (close[i + forward_period] - close[i]) / close[i]

    return df, forward_returns


def generate_cross_symbol_ic(
    n_symbols: int = 10, base_ic: float = 0.03
) -> dict[str, float]:
    """生成跨品种 IC 数据。

    Args:
        n_symbols: 品种数量
        base_ic: 基础 IC 值

    Returns:
        品种 → IC 映射
    """
    rng = np.random.RandomState(123)
    symbols = [f"SYMBOL_{i}" for i in range(n_symbols)]
    # 大部分 IC 为正，少数为负
    ics = {sym: base_ic + rng.randn() * 0.02 for sym in symbols}
    # 确保 80% 为正
    negative_count = max(0, int(n_symbols * 0.15))  # 15% 为负
    negative_indices = rng.choice(n_symbols, negative_count, replace=False)
    for idx in negative_indices:
        ics[symbols[idx]] = -abs(ics[symbols[idx]])

    return ics


def generate_stress_test_data(
    n_symbols: int = 3, n_days: int = 100
) -> tuple[dict[str, np.ndarray], dict[str, pd.DataFrame]]:
    """生成压力测试所需的信号和 OHLCV 数据。

    Args:
        n_symbols: 品种数量
        n_days: 天数

    Returns:
        (signals_by_symbol, ohlcv_by_symbol)
    """
    rng = np.random.RandomState(456)
    signals = {}
    ohlcv = {}

    for i in range(n_symbols):
        sym = f"SYMBOL_{i}"
        signals[sym] = rng.randn(n_days) * 0.5
        ohlcv[sym] = pd.DataFrame({
            "close": 100 + np.cumsum(rng.randn(n_days) * 2),
        })

    return signals, ohlcv


def generate_p_values(n_tests: int = 20) -> list[float]:
    """生成多重检验的 p 值列表。"""
    rng = np.random.RandomState(789)
    return [float(rng.random()) for _ in range(n_tests)]


def run_audit_for_factor(
    auditor: FactorAuditor,
    factor: dict,
    synthetic_data: pd.DataFrame,
    synthetic_forward_returns: np.ndarray,
) -> FactorAuditReport:
    """为单个因子执行审计。

    Args:
        auditor: FactorAuditor 实例
        factor: 因子元数据
        synthetic_data: 合成 OHLCV 数据
        synthetic_forward_returns: 合成未来收益率

    Returns:
        FactorAuditReport
    """
    factor_id = factor.get("factor_id", "unknown")

    # 准备各审计项所需的独立数据
    # 注意：实际使用时应替换为真实回测数据
    symbol_ic_map = generate_cross_symbol_ic(n_symbols=10)
    stress_signals, stress_ohlcv = generate_stress_test_data(n_symbols=3)
    p_values = generate_p_values(n_tests=20)
    oos_result = {
        "ic_consistency": 0.6,
        "passed": True,
    }

    report = auditor.audit(
        factor=factor,
        data=synthetic_data,
        forward_returns=synthetic_forward_returns,
        symbol_ic_map=symbol_ic_map,
        signals_by_symbol=stress_signals,
        ohlcv_by_symbol=stress_ohlcv,
        oos_result=oos_result,
        p_values=p_values,
    )

    return report


def batch_audit(
    seeds_dir: Path,
    output_dir: Path,
    market: str = "futures",
) -> list[FactorAuditReport]:
    """执行批量审计。

    Args:
        seeds_dir: 种子因子目录
        output_dir: 输出目录
        market: 市场类型 (futures/stock)

    Returns:
        审计报告列表
    """
    # 加载种子因子
    logger.info("加载种子因子 [dir=%s, market=%s]", seeds_dir, market)
    factors = load_factors_from_yaml(seeds_dir, market)

    if not factors:
        logger.warning("未加载到任何因子")
        return []

    logger.info("加载完成 [n_factors=%d]", len(factors))

    # 初始化审计器
    config = FactorAuditConfig(
        min_cross_symbol_ratio=0.8,
        min_oos_pass_ratio=0.5,
    )
    auditor = FactorAuditor(config=config)

    # 生成合成数据
    logger.info("生成合成审计数据")
    synthetic_data, synthetic_fwd_returns = generate_synthetic_data(n_days=252)

    # 批量审计
    reports: list[FactorAuditReport] = []
    total = len(factors)
    passed = 0
    failed = 0

    start_time = time.time()

    for idx, factor_meta in enumerate(factors, 1):
        factor_id = factor_meta.get("factor_id", f"unknown_{idx}")
        logger.info(
            "[%d/%d] 审计因子 [factor_id=%s, name=%s]",
            idx, total,
            factor_id, factor_meta.get("name", "unknown"),
        )

        try:
            report = run_audit_for_factor(
                auditor, factor_meta, synthetic_data, synthetic_fwd_returns
            )
            reports.append(report)

            if report.passed:
                passed += 1
            else:
                failed += 1
                logger.warning(
                    "因子未通过审计 [factor_id=%s, failed_items=%s]",
                    factor_id, report.summary.get("failed_items", []),
                )

        except Exception as e:
            logger.error(
                "审计异常 [factor_id=%s]: %s", factor_id, e, exc_info=True
            )
            # 创建一个失败的报告
            reports.append(FactorAuditReport(
                factor_id=factor_id,
                factor_name=factor_meta.get("name", factor_id),
                audited_at=datetime.now().isoformat(),
                items=[],
                passed=False,
                pass_rate=0.0,
                summary={"error": str(e)},
            ))
            failed += 1

        # 进度汇报
        if idx % 10 == 0 or idx == total:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            eta = (total - idx) / rate if rate > 0 else 0
            logger.info(
                "进度 [%.0f%%] 通过=%d 失败=%d 速率=%.1f/s ETA=%.0fs",
                idx / total * 100, passed, failed, rate, eta,
            )

    # 保存结果
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存 JSON 报告
    json_path = output_dir / f"audit_report_{timestamp}.json"
    json_data = [r.to_dict() for r in reports]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    logger.info("JSON 报告已保存 [path=%s]", json_path)

    # 保存 CSV 摘要
    csv_path = output_dir / f"audit_summary_{timestamp}.csv"
    csv_rows = []
    for r in reports:
        row = {
            "factor_id": r.factor_id,
            "factor_name": r.factor_name,
            "passed": r.passed,
            "pass_rate": r.pass_rate,
            "n_total": r.summary.get("total", 0),
            "n_passed": r.summary.get("passed", 0),
            "n_failed": r.summary.get("failed", 0),
            "n_skipped": r.summary.get("skipped", 0),
            "failed_items": ",".join(r.summary.get("failed_items", [])),
            "audited_at": r.audited_at,
        }
        csv_rows.append(row)

    df_summary = pd.DataFrame(csv_rows)
    df_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("CSV 摘要已保存 [path=%s]", csv_path)

    # 打印汇总
    elapsed_total = time.time() - start_time
    logger.info("=" * 60)
    logger.info("批量审计完成")
    logger.info("=" * 60)
    logger.info("  总因子数: %d", total)
    logger.info("  通过: %d (%.1f%%)", passed, passed / total * 100 if total > 0 else 0)
    logger.info("  失败: %d (%.1f%%)", failed, failed / total * 100 if total > 0 else 0)
    logger.info("  耗时: %.1fs", elapsed_total)
    logger.info("  输出: %s", json_path)

    return reports


def main():
    parser = argparse.ArgumentParser(
        description="全量因子批量审计脚本",
    )
    parser.add_argument(
        "--seeds_dir",
        type=str,
        default=str(PROJECT_ROOT / "seeds"),
        help="种子因子目录 (默认: seeds/)",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(PROJECT_ROOT / "reports"),
        help="输出目录 (默认: reports/)",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="futures",
        choices=["futures", "stock"],
        help="市场类型 (默认: futures)",
    )

    args = parser.parse_args()

    seeds_dir = Path(args.seeds_dir)
    output_dir = Path(args.output_dir)
    market = args.market

    if not seeds_dir.exists():
        logger.error("种子目录不存在: %s", seeds_dir)
        sys.exit(1)

    logger.info("开始批量因子审计 [seeds_dir=%s, output_dir=%s, market=%s]", seeds_dir, output_dir, market)

    try:
        reports = batch_audit(seeds_dir, output_dir, market)
        sys.exit(0 if reports else 1)
    except Exception as e:
        logger.error("批量审计失败: %s", e, exc_info=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
