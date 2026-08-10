"""
scripts/hmm_grid_search — HMM 超参网格搜索（P1.1）

对 n_components / covariance_type / lookback 进行 3 维网格搜索，
使用滚动 5 折交叉验证评估，以制度匹配 F1 和信号夏普比率为目标。

用法:
    python scripts/hmm_grid_search.py
    python scripts/hmm_grid_search.py --quick          # 快速测试（小网格）
    python scripts/hmm_grid_search.py --output results.json

输出:
    - 控制台打印排名表
    - JSON 文件保存完整结果
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 确保能找到 fts 包
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# 抑制 hmmlearn 的 noisy warnings（零转移矩阵行、不收敛）
logging.getLogger("hmmlearn").setLevel(logging.ERROR)
import warnings

warnings.filterwarnings("ignore", message=".*zero sum.*")
warnings.filterwarnings("ignore", message=".*not converging.*")
logger = logging.getLogger("hmm_grid_search")

# ─── hmmlearn 可选 ────────────────────────────────────────
_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm

    _HMM_AVAILABLE = True
except ImportError:
    logger.warning("hmmlearn 未安装，无法执行网格搜索")
    logger.warning("请运行: pip install hmmlearn")


# ─── 数据生成 ──────────────────────────────────────────────


def _generate_regime_data(
    n_days: int = 1000,
    seed: int = 42,
) -> pd.DataFrame:
    """生成带已知制度模式的合成 OHLCV 数据。

    制度时间线:
      0-200  : bull     (趋势 +0.15%)
      200-400: bear     (趋势 -0.12%)
      400-600: oscillate(趋势 0%, 波动 0.8%)
      600-800: high_vol (趋势 0%, 波动 2.5%)
      800-1000: bull    (趋势 +0.10%)
    """
    rng = np.random.default_rng(seed)
    n = n_days
    close = np.zeros(n)
    close[0] = 3000.0

    regimes_truth: list[str] = []

    for i in range(1, n):
        if i < 200:
            ret = rng.normal(0.0015, 0.008)
            regimes_truth.append("bull")
        elif i < 400:
            ret = rng.normal(-0.0012, 0.010)
            regimes_truth.append("bear")
        elif i < 600:
            ret = rng.normal(0.0, 0.008)
            regimes_truth.append("oscillate")
        elif i < 800:
            ret = rng.normal(0.0, 0.025)
            regimes_truth.append("high_vol")
        else:
            ret = rng.normal(0.0010, 0.008)
            regimes_truth.append("bull")
        close[i] = close[i - 1] * (1 + ret)

    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    ohlcv = pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.005, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
            "close": close,
            "volume": rng.integers(800, 1200, n).astype(float),
        },
        index=dates,
    )

    return ohlcv, regimes_truth


# ─── 评估指标 ──────────────────────────────────────────────


@dataclass
class EvalResult:
    params: dict[str, Any]
    log_likelihood: float = 0.0
    bic: float = 0.0
    aic: float = 0.0
    regime_f1: float = 0.0
    regime_accuracy: float = 0.0
    stability_score: float = 0.0
    signal_sharpe: float = 0.0
    composite_score: float = 0.0
    n_params: int = 0
    fit_time: float = 0.0
    errors: list[str] = field(default_factory=list)


def _compute_bic(model: hmm.GaussianHMM, X: np.ndarray, n_params: int) -> float:
    """计算 BIC: -2 * logL + n_params * log(n_samples)"""
    logL = model.score(X)
    n = X.shape[0]
    return -2 * logL + n_params * np.log(n)


def _compute_aic(model: hmm.GaussianHMM, X: np.ndarray, n_params: int) -> float:
    """计算 AIC: -2 * logL + 2 * n_params"""
    logL = model.score(X)
    return -2 * logL + 2 * n_params


def _n_params_hmm(n_components: int, n_features: int, covariance_type: str) -> int:
    """估计 HMM 参数数量（近似）。"""
    # 初始状态概率: n_components - 1
    # 转移矩阵: n_components * (n_components - 1)
    # 均值: n_components * n_features
    # 协方差: 取决于 covariance_type
    n = n_components
    f = n_features
    total = (n - 1) + n * (n - 1) + n * f
    if covariance_type == "diag":
        total += n * f
    elif covariance_type == "full":
        total += n * f * f
    elif covariance_type == "spherical":
        total += n
    elif covariance_type == "tied":
        total += f * f
    return total


def _evaluate_config(
    n_components: int,
    covariance_type: str,
    lookback: int,
    ohlcv: pd.DataFrame,
    regimes_truth: list[str],
    n_splits: int = 5,
) -> EvalResult:
    """评估单个 HMM 配置。"""
    result = EvalResult(
        params={
            "n_components": n_components,
            "covariance_type": covariance_type,
            "lookback": lookback,
        }
    )

    close = ohlcv["close"].dropna()
    rets = close.pct_change().dropna()
    if len(rets) < lookback + 20:
        result.errors.append("数据不足")
        return result

    # 构建特征 [收益率, 20d 波动率]
    vol = rets.rolling(20).std().fillna(0)
    features = np.column_stack([rets.values, vol.values])

    n_features = 2
    n_params = _n_params_hmm(n_components, n_features, covariance_type)
    result.n_params = n_params

    # 滚动交叉验证
    fold_scores: list[float] = []
    fold_f1s: list[float] = []
    fold_accs: list[float] = []
    fold_sharpes: list[float] = []
    stability_scores: list[float] = []
    t0 = time.time()

    total_len = len(features)
    fold_size = total_len // n_splits

    for fold in range(n_splits):
        val_start = fold * fold_size
        val_end = (fold + 1) * fold_size if fold < n_splits - 1 else total_len

        # 训练集: 从 val_start - lookback 到 val_start
        train_start = max(0, val_start - lookback)
        train_end = max(val_start, 20)  # 至少 20 个样本
        if train_end - train_start < 50:
            continue

        train_X = features[train_start:train_end]
        val_X = features[val_start:val_end]

        if len(train_X) < 50 or len(val_X) < 5:
            continue

        try:
            model = hmm.GaussianHMM(
                n_components=n_components,
                covariance_type=covariance_type,
                n_iter=200,
                tol=1e-4,
                random_state=42 + fold,
            )
            model.fit(train_X)
            logL = model.score(train_X)
            _compute_bic(model, train_X, n_params)
            _compute_aic(model, train_X, n_params)

            # 预测验证集状态
            val_states = model.predict(val_X)

            # 推断状态映射（按收益率排序）
            state_stats: list[dict] = []
            for s in range(n_components):
                mask = val_states == s
                if mask.sum() == 0:
                    continue
                state_stats.append(
                    {
                        "state": s,
                        "mean_ret": float(val_X[mask, 0].mean()),
                        "mean_vol": float(val_X[mask, 1].mean()),
                    }
                )

            if not state_stats:
                continue

            sorted_by_ret = sorted(state_stats, key=lambda x: x["mean_ret"], reverse=True)
            state_map: dict[int, str] = {}
            if len(sorted_by_ret) >= 1:
                state_map[sorted_by_ret[0]["state"]] = "bull"
            if len(sorted_by_ret) >= 2:
                state_map[sorted_by_ret[-1]["state"]] = "bear"
            remaining = [s for s in sorted_by_ret if s["state"] not in state_map]
            if remaining:
                remaining.sort(key=lambda x: x["mean_vol"], reverse=True)
                state_map[remaining[0]["state"]] = "high_vol"
                for s in remaining[1:]:
                    state_map[s["state"]] = "oscillate"

            # 预测每个验证点的制度
            val_regimes = [state_map.get(s, "oscillate") for s in val_states]

            # 真实制度（取验证集对应的真实标签）
            truth_start = val_start
            truth_end = min(val_end, len(regimes_truth))
            if truth_end <= truth_start:
                continue
            truth_segment = regimes_truth[truth_start:truth_end]

            # 准确率
            min_len = min(len(val_regimes), len(truth_segment))
            if min_len == 0:
                continue
            correct = sum(1 for i in range(min_len) if val_regimes[i] == truth_segment[i])
            accuracy = correct / min_len
            fold_accs.append(accuracy)

            # F1-score（macro）
            from sklearn.metrics import f1_score

            all_regimes = ["bull", "bear", "oscillate", "high_vol"]
            try:
                f1 = f1_score(
                    truth_segment[:min_len],
                    val_regimes[:min_len],
                    labels=all_regimes,
                    average="macro",
                    zero_division=0,
                )
                fold_f1s.append(f1)
            except Exception:
                pass

            # 稳定性：相邻预测的切换次数
            switches = sum(1 for i in range(1, len(val_regimes)) if val_regimes[i] != val_regimes[i - 1])
            stability = 1.0 - min(1.0, switches / max(1, len(val_regimes)))
            stability_scores.append(stability)

            # 信号夏普：如果预测为 bull 则做多，bear 做空，其他空仓
            signals = []
            for r in val_regimes[:min_len]:
                if r == "bull":
                    signals.append(1.0)
                elif r == "bear":
                    signals.append(-1.0)
                else:
                    signals.append(0.0)
            signals = np.array(signals)
            truth_rets = rets.values[val_start : val_start + min_len]
            if len(signals) > 0 and len(truth_rets) > 0:
                strategy_rets = signals * truth_rets
                if len(strategy_rets) > 1 and np.std(strategy_rets) > 1e-10:
                    sharpe = np.mean(strategy_rets) / np.std(strategy_rets) * np.sqrt(252)
                    fold_sharpes.append(sharpe)

            fold_scores.append(logL)

        except Exception as e:
            result.errors.append(f"fold {fold}: {e}")
            continue

    elapsed = time.time() - t0
    result.fit_time = round(elapsed, 2)

    if not fold_scores:
        result.errors.append("所有 fold 均失败")
        return result

    result.log_likelihood = round(float(np.mean(fold_scores)), 4)
    result.regime_accuracy = round(float(np.mean(fold_accs)), 4) if fold_accs else 0.0
    result.regime_f1 = round(float(np.mean(fold_f1s)), 4) if fold_f1s else 0.0
    result.stability_score = round(float(np.mean(stability_scores)), 4) if stability_scores else 0.0
    result.signal_sharpe = round(float(np.mean(fold_sharpes)), 4) if fold_sharpes else 0.0

    # 综合评分: 归一化 F1 * 0.4 + 稳定性 * 0.2 + Sharpe * 0.3 + (1 - BIC_rank) * 0.1
    result.composite_score = round(
        result.regime_f1 * 0.4
        + result.stability_score * 0.2
        + min(1.0, max(0.0, result.signal_sharpe / 3.0)) * 0.3
        + result.regime_accuracy * 0.1,
        4,
    )

    return result


# ─── 主流程 ────────────────────────────────────────────────


def run_grid_search(
    param_grid: dict[str, list[Any]],
    ohlcv: pd.DataFrame,
    regimes_truth: list[str],
    n_splits: int = 5,
) -> list[EvalResult]:
    """执行网格搜索。"""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    total = 1
    for v in values:
        total *= len(v)

    logger.info("网格搜索: %d 种配置", total)
    results: list[EvalResult] = []

    for idx, combo in enumerate(product(*values)):
        params = dict(zip(keys, combo))
        logger.info("[%d/%d] 评估: %s", idx + 1, total, params)

        result = _evaluate_config(
            n_components=params.get("n_components", 4),
            covariance_type=params.get("covariance_type", "diag"),
            lookback=params.get("lookback", 252),
            ohlcv=ohlcv,
            regimes_truth=regimes_truth,
            n_splits=n_splits,
        )
        results.append(result)

        status = f"F1={result.regime_f1:.4f} Acc={result.regime_accuracy:.4f} "
        status += f"Sharpe={result.signal_sharpe:.4f} Stab={result.stability_score:.4f} "
        status += f"Comp={result.composite_score:.4f}"
        if result.errors:
            status += f" ERR={result.errors[-1]}"
        logger.info("  => %s (%.1fs)", status, result.fit_time)

    # 按综合评分降序排列
    results.sort(key=lambda r: r.composite_score, reverse=True)
    return results


def print_results_table(results: list[EvalResult], top_n: int = 20) -> None:
    """打印排名表。"""
    print(f"\n{'=' * 100}")
    print(f"  HMM 超参网格搜索结果排名（Top {min(top_n, len(results))}）")
    print(f"{'=' * 100}")
    print(
        f"{'排名':>4} {'n_comp':>6} {'cov_type':>12} {'lookback':>8} "
        f"{'F1':>8} {'Acc':>8} {'Sharpe':>8} {'Stab':>8} {'Comp':>8} "
        f"{'BIC':>10} {'AIC':>10} {'时间(秒)':>8}"
    )
    print(f"{'-' * 100}")

    for rank, r in enumerate(results[:top_n], 1):
        p = r.params
        print(
            f"{rank:>4} {p['n_components']:>6} {p['covariance_type']:>12} "
            f"{p['lookback']:>8} {r.regime_f1:>8.4f} {r.regime_accuracy:>8.4f} "
            f"{r.signal_sharpe:>8.4f} {r.stability_score:>8.4f} "
            f"{r.composite_score:>8.4f} {r.bic:>10.2f} {r.aic:>10.2f} "
            f"{r.fit_time:>8.1f}"
        )

    print(f"{'=' * 100}\n")


def save_results(results: list[EvalResult], output_path: str) -> None:
    """保存结果到 JSON。"""
    data = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "n_configs": len(results),
        "top_config": asdict(results[0]) if results else None,
        "results": [asdict(r) for r in results],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("结果已保存到 %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="HMM 超参网格搜索")
    parser.add_argument("--quick", action="store_true", help="快速测试（小网格）")
    parser.add_argument("--output", default="hmm_grid_search_results.json", help="输出 JSON 路径")
    parser.add_argument("--n-splits", type=int, default=5, help="交叉验证折数")
    parser.add_argument("--n-days", type=int, default=1000, help="合成数据天数")
    args = parser.parse_args()

    if not _HMM_AVAILABLE:
        logger.error("hmmlearn 未安装，无法执行网格搜索")
        sys.exit(1)

    # 生成合成数据
    logger.info("生成 %d 天合成数据...", args.n_days)
    ohlcv, regimes_truth = _generate_regime_data(n_days=args.n_days, seed=42)
    logger.info(
        "数据生成完成: %d 行, 制度分布: bull=%d bear=%d osc=%d hv=%d",
        len(ohlcv),
        regimes_truth.count("bull"),
        regimes_truth.count("bear"),
        regimes_truth.count("oscillate"),
        regimes_truth.count("high_vol"),
    )

    # 定义网格
    if args.quick:
        param_grid = {
            "n_components": [3, 4],
            "covariance_type": ["diag", "spherical"],
            "lookback": [126, 252],
        }
    else:
        param_grid = {
            "n_components": [3, 4, 5, 6],
            "covariance_type": ["diag", "full", "spherical", "tied"],
            "lookback": [126, 189, 252, 378],
        }

    # 执行网格搜索
    results = run_grid_search(param_grid, ohlcv, regimes_truth, n_splits=args.n_splits)

    # 输出结果
    print_results_table(results, top_n=20)
    save_results(results, args.output)

    # 最佳配置建议
    best = results[0]
    print(
        f"\n最佳配置: n_components={best.params['n_components']}, "
        f"covariance_type={best.params['covariance_type']}, "
        f"lookback={best.params['lookback']}"
    )
    print(
        f"  F1={best.regime_f1:.4f}, Acc={best.regime_accuracy:.4f}, "
        f"Sharpe={best.signal_sharpe:.4f}, Composite={best.composite_score:.4f}"
    )

    # 稳定性前 5 配置
    by_stability = sorted(results, key=lambda r: r.stability_score, reverse=True)
    top_stable = by_stability[0]
    print(
        f"\n最稳定配置: n_components={top_stable.params['n_components']}, "
        f"covariance_type={top_stable.params['covariance_type']}, "
        f"lookback={top_stable.params['lookback']}"
    )
    print(f"  Stability={top_stable.stability_score:.4f}, F1={top_stable.regime_f1:.4f}")

    # 按 lookback 汇总
    print(f"\n{'=' * 60}")
    print("  按 lookback 分组的最佳 F1")
    print(f"{'=' * 60}")
    from itertools import groupby

    sorted_by_lb = sorted(results, key=lambda r: r.params["lookback"])
    for lb, group in groupby(sorted_by_lb, key=lambda r: r.params["lookback"]):
        best_in_group = max(group, key=lambda r: r.regime_f1)
        print(
            f"  lookback={lb:>3d}: 最佳 F1={best_in_group.regime_f1:.4f} "
            f"(n_comp={best_in_group.params['n_components']}, "
            f"cov={best_in_group.params['covariance_type']})"
        )

    print(f"\n{'=' * 60}")
    print("  按 covariance_type 分组的最佳 F1")
    print(f"{'=' * 60}")
    sorted_by_cov = sorted(results, key=lambda r: r.params["covariance_type"])
    for cov, group in groupby(sorted_by_cov, key=lambda r: r.params["covariance_type"]):
        best_in_group = max(group, key=lambda r: r.regime_f1)
        print(
            f"  cov={cov:>12s}: 最佳 F1={best_in_group.regime_f1:.4f} "
            f"(n_comp={best_in_group.params['n_components']}, "
            f"lb={best_in_group.params['lookback']})"
        )


if __name__ == "__main__":
    main()
