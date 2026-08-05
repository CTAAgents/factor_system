"""
loop_engine/micro_evolution.py — 微观演化（optuna 贝叶斯调参）

factorengine 核心约束（三层分离）：
    LLM 只管逻辑，CPU 只管参数。
    微观演化 = 贝叶斯优化参数空间，连续 20 次无提升跳出。

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=import-outside-toplevel,broad-exception-caught,too-many-arguments,too-many-positional-arguments,too-many-locals

from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from .contracts import FactorProgram


# ─── optuna 导入兜底 ──────────────────────────────────────

try:
    import optuna
    from optuna.samplers import TPESampler, RandomSampler
    _HAS_OPTUNA = True
except ImportError:
    optuna = None  # type: ignore[assignment]
    _HAS_OPTUNA = False


# ─── 常量 ─────────────────────────────────────────────────

DEFAULT_N_TRIALS: int = 100
DEFAULT_EARLY_STOPPING_FAILURES: int = 20
RANDOM_SEARCH_TRIALS: int = 50
"""随机搜索的默认试验次数（比贝叶斯少，适用于参数空间简单时）。"""


class MicroEvolutionError(Exception):
    """微观演化失败。"""


# ─── 参数空间搜索 ─────────────────────────────────────────

def _suggest_param(trial, key: str, value: Any) -> Any:
    """根据参数默认值推断参数空间。

    int → suggest_int(value//2, value*2)
    float → suggest_float(value/2, value*2)
    """
    if isinstance(value, bool):
        return trial.suggest_categorical(key, [True, False])
    if isinstance(value, int):
        lo = max(1, value // 2)
        hi = max(value * 2, value + 1)
        return trial.suggest_int(key, lo, hi)
    if isinstance(value, float):
        lo = value / 2
        hi = value * 2
        return trial.suggest_float(key, lo, hi)
    # 字符串等不可搜索类型
    return value


def optimize_params(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    objective_fn: Optional[Callable[[np.ndarray, np.ndarray], float]] = None,
    n_trials: int = DEFAULT_N_TRIALS,
    early_stopping_failures: int = DEFAULT_EARLY_STOPPING_FAILURES,
    use_random_search: bool = False,
) -> tuple[dict[str, Any], float]:
    """贝叶斯优化因子参数。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率
        objective_fn: 目标函数（signal, returns）-> score，默认 IC
        n_trials: 最大试验次数
        early_stopping_failures: 连续无提升跳出阈值
        use_random_search: 使用随机搜索代替贝叶斯优化（参数空间简单时更高效）

    Returns:
        (best_params, best_score)
    """
    if not _HAS_OPTUNA:
        return factor.get("params", {}), 0.0

    if objective_fn is None:
        from scipy import stats as sp_stats
        def objective_fn(sig, ret):
            # 处理不同长度的数据（截取共同部分）
            min_len = min(len(sig), len(ret))
            if min_len < 2:
                return 0.0
            sig_aligned = sig[-min_len:]  # 取末尾对齐（最新数据）
            ret_aligned = ret[-min_len:]
            if np.std(sig_aligned) < 1e-10 or np.std(ret_aligned) < 1e-10:
                return 0.0
            ic, _ = sp_stats.spearmanr(sig_aligned, ret_aligned)
            return 0.0 if np.isnan(ic) else float(ic)

    from .factor_program import FactorExecutor
    executor = FactorExecutor(factor)
    executor.compile()  # 预编译

    base_params = factor.get("params", {})

    def optuna_objective(trial):
        # 构造本次试验的参数
        trial_params = {
            k: _suggest_param(trial, k, v) for k, v in base_params.items()
        }
        try:
            signal = executor.execute(data, trial_params)
            score = objective_fn(signal, forward_returns)
            return score
        except Exception:
            return -1.0  # 异常试验返回极差分数

    # 创建 study（支持随机搜索和贝叶斯优化两种模式）
    if use_random_search:
        sampler = RandomSampler(seed=42)
        # 随机搜索不用早停，直接跑满 n_trials
        actual_early_stop = 999999
    else:
        sampler = TPESampler(seed=42)
        actual_early_stop = early_stopping_failures

    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
    )

    # 早停回调
    best_score = -np.inf
    no_improve_count = 0

    def early_stop_callback(study, _trial):
        nonlocal best_score, no_improve_count
        current_best = study.best_value if study.trials else -np.inf
        if current_best > best_score + 1e-6:
            best_score = current_best
            no_improve_count = 0
        else:
            no_improve_count += 1
        if no_improve_count >= actual_early_stop:
            study.stop()

    try:
        study.optimize(
            optuna_objective,
            n_trials=n_trials,
            callbacks=[early_stop_callback],
            catch=(Exception,),
        )
    except Exception as e:
        raise MicroEvolutionError(f"optuna 优化失败: {e}") from e

    if not study.best_params:
        return base_params, 0.0

    return dict(study.best_params), float(study.best_value)


# ─── 微观演化主入口 ───────────────────────────────────────

def evolve_micro(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    n_trials: int = DEFAULT_N_TRIALS,
    use_random_search: bool = False,
) -> tuple[FactorProgram, float]:
    """微观演化主入口 — 优化因子参数。

    三层分离原则：仅修改 params，不修改 code。

    Args:
        factor: 待优化因子
        data: OHLCV 数据
        forward_returns: 未来收益率
        n_trials: optuna 试验次数
        use_random_search: 使用随机搜索代替贝叶斯优化

    Returns:
        (optimized_factor, best_score)
    """
    best_params, best_score = optimize_params(
        factor, data, forward_returns, n_trials=n_trials,
        use_random_search=use_random_search,
    )

    # 返回新因子实例（不修改原因子）
    evolved = FactorProgram(**{**factor, "params": best_params})  # type: ignore[typeddict-item]
    return evolved, best_score


__all__ = [
    "DEFAULT_N_TRIALS",
    "DEFAULT_EARLY_STOPPING_FAILURES",
    "RANDOM_SEARCH_TRIALS",
    "MicroEvolutionError",
    "optimize_params",
    "evolve_micro",
]
