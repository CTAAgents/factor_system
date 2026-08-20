"""
loop_engine/evaluation_chain.py — agentic 三级评估链

HARNESS §11-loop-engineering.md §4:
    Level 1 — 回测验证（IC>0.03 / 夏普>1.5 / 单调性 / 样本外≥30%）
    Level 2 — 经济逻辑（四维评分 ≥ 3/4）
    Level 3 — 多重检验（FDR + Bonferroni）
    WalkForward — 可选走航模式（多窗口样本外稳定性验证）

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=too-many-locals,too-few-public-methods

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
    MultipleTestResult,
)
from .factor_program import FactorExecutor
from .walk_forward import WalkForwardOptimizer, WalkForwardConfig, WalkForwardResult, DEFAULT_WALK_FORWARD_CONFIG
from .ir_thresholds import factor_ir_threshold

logger = logging.getLogger(__name__)


# ─── Level 1: 回测验证 ────────────────────────────────────


def _compute_ic(signal: np.ndarray, forward_returns: np.ndarray, method: str = "spearman") -> tuple[float, float]:
    """计算 IC（信息系数）和 ICIR。

    Args:
        signal: 因子信号数组
        forward_returns: 未来收益率数组
        method: "spearman" / "pearson"

    Returns:
        (ic_mean, icir)
    """
    if len(signal) != len(forward_returns) or len(signal) < 2:
        return 0.0, 0.0
    # NaN 掩码兜底（v2.50.0）：剔除含 NaN 的样本对后再计算相关系数，
    # 避免数据缺失（如鲁棒性缺失值测试注入 NaN）时 spearmanr/pearsonr 返回 NaN 致 IC 恒为 0
    sig_arr = np.asarray(signal, dtype=float)
    ret_arr = np.asarray(forward_returns, dtype=float)
    valid = ~(np.isnan(sig_arr) | np.isnan(ret_arr))
    sig_v = sig_arr[valid]
    ret_v = ret_arr[valid]
    if len(sig_v) < 2 or len(sig_v) != len(ret_v):
        return 0.0, 0.0
    # 常数输入检查：若任一输入为常数，相关系数无定义，返回 0.0
    if np.std(sig_v) < 1e-12 or np.std(ret_v) < 1e-12:
        return 0.0, 0.0
    if method == "spearman":
        ic, _ = sp_stats.spearmanr(sig_v, ret_v)
    else:
        ic, _ = sp_stats.pearsonr(sig_v, ret_v)
    if np.isnan(ic):
        return 0.0, 0.0
    # ICIR = IC 均值 / IC 标准差（这里简化为单期）
    return float(ic), float(ic)  # 多期时 icir = mean/std


def _compute_extreme_perturbation_ic(
    signal: np.ndarray,
    forward_returns: np.ndarray,
    pct: float = 0.01,
) -> Optional[dict[str, Any]]:
    """极值样本扰动 IC 重算（GAP-F15，v2.73.0）。

    剔除信号上下 pct 百分位极端样本后重算 IC，评估高 IC 是否仅由少数极端样本支撑。

    Args:
        signal: 因子信号数组
        forward_returns: 未来收益率数组
        pct: 上下剔除百分位（默认 0.01 = 上下各 1%）

    Returns:
        dict{ic_before, ic_after, ic_drop, n_total, n_removed}：
            ic_drop = (|ic_before| - |ic_after|) / |ic_before|，clip 到 [0, 1]。
            数据不足 / 常数输入 / IC 近零时返回 None（screener 按"扰动数据缺失"处理）。
    """
    if len(signal) != len(forward_returns) or len(signal) < 20:
        return None
    sig_arr = np.asarray(signal, dtype=float)
    ret_arr = np.asarray(forward_returns, dtype=float)
    valid = ~(np.isnan(sig_arr) | np.isnan(ret_arr))
    sig_v = sig_arr[valid]
    ret_v = ret_arr[valid]
    if len(sig_v) < 20 or np.std(sig_v) < 1e-12 or np.std(ret_v) < 1e-12:
        return None
    ic_before, _ = _compute_ic(sig_v, ret_v)
    if not np.isfinite(ic_before) or abs(ic_before) < 1e-6:
        return None
    lo, hi = np.percentile(sig_v, [pct * 100, (1 - pct) * 100])
    mask = (sig_v >= lo) & (sig_v <= hi)
    if int(mask.sum()) < 20:
        return None
    ic_after, _ = _compute_ic(sig_v[mask], ret_v[mask])
    if not np.isfinite(ic_after):
        return None
    ic_drop = (abs(ic_before) - abs(ic_after)) / abs(ic_before)
    return {
        "ic_before": float(ic_before),
        "ic_after": float(ic_after),
        "ic_drop": float(np.clip(ic_drop, 0.0, 1.0)),
        "n_total": int(len(sig_v)),
        "n_removed": int(len(sig_v) - int(mask.sum())),
    }


def _compute_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """计算年化夏普比率（假设无风险利率=0）。"""
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    if std < 1e-10:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(periods_per_year))


def _compute_max_drawdown(cumulative: np.ndarray) -> float:
    """计算最大回撤（0~1）。

    将 cumsum 收益转为组合净值（1 + return），确保峰值始终为正。
    """
    if len(cumulative) < 2:
        return 0.0
    # 转为组合净值（避免 cumsum 负值导致分母除峰值）
    nav = 1.0 + cumulative
    peak = np.maximum.accumulate(nav)
    drawdown = (peak - nav) / np.maximum(peak, 1e-10)
    return float(np.max(drawdown))


def _check_monotonicity(signal: np.ndarray, returns: np.ndarray, n_buckets: int = 10) -> bool:
    """检查因子信号的预测单调性（Spearman 秩相关 >= 0.5）。

    将信号分为 n_buckets 组，检查组序与组收益的 Spearman 秩相关。
    相比严格单调性检查，更适合时序单标的场景。
    """
    if len(signal) < n_buckets * 10:
        return False
    df = pd.DataFrame({"signal": signal, "return": returns}).dropna()
    if len(df) < n_buckets:
        return False
    df["bucket"] = pd.qcut(df["signal"], n_buckets, labels=False, duplicates="drop")
    bucket_returns = df.groupby("bucket")["return"].mean()
    if len(bucket_returns) < 3:
        return False
    # Spearman 秩相关：桶序 vs 收益
    corr, p_value = sp_stats.spearmanr(range(len(bucket_returns)), bucket_returns.values)
    if np.isnan(corr):
        return False
    return bool(abs(corr) >= 0.5 and p_value < 0.05)


def _max_consecutive_losses(returns: np.ndarray) -> int:
    """最大连续亏损天数（GAP-062）。"""
    best = cur = 0
    for r in returns:
        if r < 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return int(best)


def _block_ic_stats(signal: np.ndarray, forward_returns: np.ndarray, block_size: int = 20) -> Optional[tuple[float, float, float]]:
    """块状 IC 序列的 t 统计量、胜率与 ICIR（GAP-062 + G4）。

    将样本切分为非重叠块，逐块计算 IC，得到 IC 序列：
        ic_t = IC均值 / (IC标准差 / sqrt(块数))   —— IC 均值显著性检验
        win_rate = 正 IC 块占比（日度/块级 IC 胜率）
        icir_block = IC均值 / IC标准差             —— 块级 ICIR（G4 硬门槛口径）

    Returns:
        (ic_t_stat, win_rate, icir_block)；IC 序列不足 2 块或无常量差异返回 None。
    """
    ics: list[float] = []
    n = len(signal)
    for s in range(0, n - block_size + 1, block_size):
        e = s + block_size
        ic, _ = _compute_ic(signal[s:e], forward_returns[s:e])
        ics.append(ic)
    if len(ics) < 2:
        return None
    arr = np.asarray(ics, dtype=float)
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    if sd < 1e-10:
        return None
    ic_t = mu / (sd / np.sqrt(len(arr)))
    return float(ic_t), float(np.mean(arr > 0)), float(mu / sd)


def _cs_quintile_returns(signal_mat: np.ndarray, ret_mat: np.ndarray) -> dict:
    """横截面 Q1-Q5 完整分组收益（GAP-062）。

    每期按因子值把品种分 5 组（等分），计算各组未来收益均值，跨期平均；
    输出 {1..5: mean_ret, q5_q1_spread, monotonic}。样本不足返回空 dict。

    Args:
        signal_mat: (dates, symbols) 信号矩阵
        ret_mat: (dates, symbols) 未来收益矩阵
    """
    n_dates, n_syms = signal_mat.shape
    if n_dates < 2 or n_syms < 10:
        return {}
    groups: dict[int, list[float]] = {g: [] for g in range(1, 6)}
    for d in range(n_dates):
        sig = signal_mat[d]
        ret = ret_mat[d]
        valid = np.isfinite(sig) & np.isfinite(ret)
        if int(valid.sum()) < 10:
            continue
        s = sig[valid]
        r = ret[valid]
        order = np.argsort(s)
        n = len(s)
        bounds = np.linspace(0, n, 6).astype(int)
        for gi in range(5):
            idx = order[bounds[gi] : bounds[gi + 1]]
            if len(idx) > 0:
                groups[gi + 1].append(float(np.mean(r[idx])))
    result: dict = {}
    for g in range(1, 6):
        if groups[g]:
            result[g] = float(np.mean(groups[g]))
    if 1 in result and 5 in result:
        result["q5_q1_spread"] = result[5] - result[1]
        vals = [result[g] for g in range(1, 6) if g in result]
        if len(vals) >= 3:
            corr, _ = sp_stats.spearmanr(range(len(vals)), vals)
            result["monotonic"] = bool(not np.isnan(corr) and abs(corr) >= 0.5)
    return result


def compute_cs_multi_horizon_ic(
    oos_signal: np.ndarray,
    panel_data: dict[str, pd.DataFrame],
    symbols_list: list[str],
    common_dates: pd.DatetimeIndex,
    oos_n: int,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    min_dates: int = 10,
    block_size: int = 20,
    use_panel_vector: Optional[bool] = None,
) -> Optional[Any]:
    """横截面多持有期 IC（GAP-060 股票/横截面接入，v2.90.0）。

    对每个持有期 h 构建 h 日前向收益矩阵，逐期计算截面 Spearman IC，
    输出与 `horizon_analysis.HorizonAnalysisResult.to_dict()` 对齐的结构：
    horizons / ic_by_horizon / icir_by_horizon / win_rate_by_horizon /
    best_horizon / decay_curve / monotonic_decay。

    与时序路径（evaluate_backtest → compute_multi_horizon_ic）的区别：
    本函数作用于截面信号矩阵，IC 是"每个截面期一次秩相关"的时序聚合，
    而非单标的信号与未来收益的时序相关。

    use_panel_vector（plans/37 延伸，P0 性能）：块状截面 IC 经
    `_cs_compute_ics_dispatch` 分派——True=panel_vector 全矩阵化（联合掩码
    rank + 行内 Pearson），False=逐日 spearmanr；None=读配置
    （cross_section_panel_vector，默认 true）。两路径逐位一致
    （tests/factor_engine/test_cross_section_horizon.py 对照验证）。

    Args:
        oos_signal: 中性化后的信号矩阵 (n_dates, n_stocks)
        panel_data: {symbol: OHLCV DataFrame} 字典
        symbols_list: 与 oos_signal 列顺序一致的标的列表
        common_dates: 面板共同日期
        oos_n: 样本外天数（= oos_signal.shape[0]）
        horizons: 持有期集合（天）
        min_dates: 有效截面期数下限（不足返回 None）
        block_size: 非重叠块大小（块状 IC 序列）
        use_panel_vector: 面板向量 IC 开关（None=读配置，默认 true）

    Returns:
        HorizonAnalysisResult 或 None（数据不足/全部持有期无效）
    """
    from .horizon_analysis import HorizonAnalysisResult

    if use_panel_vector is None:
        try:
            from ..config import get_config

            use_panel_vector = bool(getattr(get_config(), "cross_section_panel_vector", False))
        except Exception:  # noqa: BLE001 — 配置读取失败降级关闭
            use_panel_vector = False

    n_dates = int(oos_signal.shape[0])
    if n_dates < min_dates:
        return None

    result = HorizonAnalysisResult(horizons=[h for h in horizons if h > 0])
    if not result.horizons:
        return None

    # 各标的 close 对齐共同日期 → 收盘价矩阵 (n_dates_total, n_stocks)
    n_total = len(common_dates)
    close_matrix = np.zeros((n_total, len(symbols_list)))
    for j, sym in enumerate(symbols_list):
        df = panel_data.get(sym)
        if df is None or "close" not in df.columns:
            close_matrix[:, j] = np.nan
        else:
            close_matrix[:, j] = df["close"].reindex(common_dates).values
    oos_close = close_matrix[-oos_n:, :]

    for h in result.horizons:
        fwd = np.full_like(oos_close, np.nan)
        if h < oos_n:
            denom = np.maximum(np.abs(oos_close[: oos_n - h, :]), 1e-10)
            fwd[: oos_n - h, :] = (oos_close[h:, :] - oos_close[: oos_n - h, :]) / denom
        # 块状截面 IC 序列（非重叠块；P0 面板向量分派，on/off 逐位一致）
        ics: list[float] = []
        n_blocks = max(n_dates // block_size, 1)
        for b in range(n_blocks):
            s = b * block_size
            e = min(s + block_size, n_dates)
            ics.extend(_cs_compute_ics_dispatch(oos_signal[s:e, :], fwd[s:e, :], use_panel_vector))
        if not ics:
            # 块状失败退化全段
            ics = _cs_compute_ics_dispatch(oos_signal, fwd, use_panel_vector)
        if not ics:
            continue
        ic_arr = np.asarray(ics, dtype=float)
        result.ic_series_by_horizon[h] = [float(v) for v in ics]
        result.ic_by_horizon[h] = float(np.mean(ic_arr))
        result.win_rate_by_horizon[h] = float(np.mean(ic_arr > 0))
        std = float(np.std(ic_arr))
        result.icir_by_horizon[h] = float(np.mean(ic_arr) / max(std, 1e-10))

    if not result.ic_by_horizon:
        return None

    # 最佳持有期：|ICIR| 最大（平局取较短持有期）
    valid = [(h, abs(result.icir_by_horizon[h])) for h in result.horizons if h in result.icir_by_horizon]
    if valid:
        result.best_horizon = min(valid, key=lambda x: (-x[1], x[0]))[0]

    # 衰减曲线：IC(h)/IC(1)（绝对 IC 归一化）
    base_h = result.horizons[0]
    base_abs = abs(result.ic_by_horizon.get(base_h, 0.0))
    for h in result.horizons:
        ic_h = abs(result.ic_by_horizon.get(h, 0.0))
        result.decay_curve[h] = float(ic_h / base_abs) if base_abs > 1e-10 else 0.0

    # 单调衰减判定：IC 绝对值随持有期非增
    abs_ics = [abs(result.ic_by_horizon.get(h, 0.0)) for h in result.horizons if h in result.ic_by_horizon]
    result.monotonic_decay = bool(
        len(abs_ics) >= 2 and all(abs_ics[i] >= abs_ics[i + 1] - 1e-9 for i in range(len(abs_ics) - 1))
    )
    return result


def evaluate_backtest(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    oos_ratio: float = 0.3,
    periods_per_year: int = 252,
    horizons: Optional[tuple[int, ...]] = None,
    signal_cache: Optional[Any] = None,
) -> BacktestMetrics:
    """Level 1 — 回测验证。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率（与 data 等长）
        oos_ratio: 样本外比例
        periods_per_year: 年化系数
        horizons: 多持有期 IC 分析（GAP-060）；None 不执行（默认关闭，避免影响既有评估路径）
        signal_cache: 可选信号缓存（GAP-070），命中后跳过因子沙箱执行

    Returns:
        BacktestMetrics
    """
    executor = FactorExecutor(factor, signal_cache=signal_cache)
    signal = executor.execute(data, factor.get("params", {}))

    n = len(signal)
    oos_n = max(int(n * oos_ratio), 1)

    # 样本外切片
    oos_signal = signal[-oos_n:]
    oos_returns = forward_returns[-oos_n:]
    in_sample_signal = signal[:-oos_n]
    in_sample_returns = forward_returns[:-oos_n]

    # IC（样本外）
    ic, icir = _compute_ic(oos_signal, oos_returns)
    # 全样本 ICIR（多期近似）
    if len(in_sample_signal) > 0:
        ic_in, _ = _compute_ic(in_sample_signal, in_sample_returns)
        icir = float(np.mean([ic_in, ic]) / max(np.std([ic_in, ic]), 1e-10))

    # 信号分组构建多空组合收益（时变信号）
    if len(oos_signal) > 0:
        # 用信号分位数构建时变多空权重，非恒定收益
        ranked = sp_stats.rankdata(oos_signal, method="average")
        positions = 2.0 * (ranked / len(ranked)) - 1.0  # 归一化到 [-1, 1]
        ls_returns = positions * oos_returns
    else:
        ls_returns = np.zeros(oos_n)

    sharpe = _compute_sharpe(ls_returns, periods_per_year)
    cumulative = np.cumsum(ls_returns)
    max_dd = _compute_max_drawdown(cumulative)
    monotonic = _check_monotonicity(signal, forward_returns)

    # t 统计量
    if len(ls_returns) > 1 and np.std(ls_returns) > 1e-10:
        t_stat = float(np.mean(ls_returns) / np.std(ls_returns, ddof=1) * np.sqrt(len(ls_returns)))
    else:
        t_stat = 0.0

    # 月度换手率（简化估计）
    if len(signal) > 1:
        signal_changes = np.abs(np.diff(np.sign(signal)))
        turnover = float(np.mean(signal_changes) * 21)  # 日均换手 * 21 = 月度
    else:
        turnover = 0.0
    # G11（35-gap-closure-plan §5.4）：日换手口径 = 信号翻转率 = mean(|Δsign|)/2（= 月度/21/2）
    turnover_daily = turnover / (21.0 * 2.0)

    # 6 个月 IC 衰减率计算: OOS 期前后两半的 IC 对比
    decay_6m = 0.0
    sign_flip_half_split = False  # G4（35-gap-closure-plan）：前后半段 IC 符号反转标记
    if len(oos_signal) >= 20:
        half = len(oos_signal) // 2
        ic_first, _ = _compute_ic(oos_signal[:half], oos_returns[:half])
        ic_second, _ = _compute_ic(oos_signal[half:], oos_returns[half:])
        if abs(ic_first) > 0.01:
            decay_6m = max(0.0, min(1.0, 1.0 - abs(ic_second) / abs(ic_first)))
            if ic_first * ic_second < 0:
                sign_flip_half_split = True

    metrics: BacktestMetrics = BacktestMetrics(
        ic=ic,
        icir=icir,
        sharpe=sharpe,
        max_drawdown=max_dd,
        monotonicity=monotonic,
        oos_ratio=oos_ratio,
        t_stat=t_stat,
        turnover_monthly=turnover,
        decay_6m=decay_6m,
    )
    metrics["sign_flip_half_split"] = sign_flip_half_split  # G4：前后半段 IC 符号反转
    metrics["turnover_daily"] = turnover_daily  # G11：日换手口径（信号翻转率）

    # GAP-062 统计补全（时序路径）：信号翻转频率 / 最大连续亏损 / IC t 值 / 胜率
    if len(signal) > 1:
        metrics["sign_flip_rate"] = float(np.mean(np.abs(np.diff(np.sign(signal)))) / 2)  # 归一化 [0,1]：0/2=不翻转，2/2=每日翻转
    if len(ls_returns) > 0:
        metrics["max_consecutive_losses"] = _max_consecutive_losses(ls_returns)
    block_stats = _block_ic_stats(signal, forward_returns)
    if block_stats is not None:
        metrics["ic_t_stat"], metrics["win_rate"], metrics["icir_block"] = block_stats  # G4：块级 ICIR（硬门槛口径）

    # GAP-060 多持有期 IC 体系：显式传入或配置 FTS_EVAL_HORIZONS 时附加（默认 1,5,10,20）
    if horizons is None:
        try:
            from ..config import get_config

            horizons = getattr(get_config(), "eval_horizons", ()) or None
        except Exception:  # noqa: BLE001 — 配置读取失败降级关闭
            horizons = None
    if horizons is not None and "close" in getattr(data, "columns", []):
        try:
            from .horizon_analysis import compute_multi_horizon_ic

            hr = compute_multi_horizon_ic(signal, data["close"].values, horizons=horizons)
            if hr is not None:
                metrics["multi_horizon"] = hr.to_dict()
        except Exception:  # noqa: BLE001 — 多持有期分析失败降级，不阻断既有评估
            pass

    # GAP-061 可交易性压力层：配置 FTS_COST_SENSITIVITY_ENABLED=1 时附加（默认关闭）
    try:
        from ..config import get_config as _get_cfg

        _cs_enabled = bool(getattr(_get_cfg(), "cost_sensitivity_enabled", False))
    except Exception:  # noqa: BLE001
        _cs_enabled = False
    if _cs_enabled and "close" in getattr(data, "columns", []):
        try:
            from .cost_sensitivity import run_slippage_stress

            cs = run_slippage_stress(signal, data["close"].values, market="futures")
            if cs is not None:
                metrics["cost_sensitivity"] = cs.to_dict()
        except Exception:  # noqa: BLE001 — 成本敏感性失败降级，不阻断既有评估
            pass
    return metrics


# ─── Level 2: 经济逻辑评分 ────────────────────────────────


def evaluate_economic_logic(factor: FactorProgram) -> EconomicScore:
    """Level 2 — 经济逻辑评分（四维）。

    直接从 factor.economic_logic 读取，并计算达标维度数。
    """
    el = factor.get("economic_logic", {})
    theory = int(el.get("theory", 3))
    behavioral = int(el.get("behavioral", 3))
    microstructure = int(el.get("microstructure", 3))
    institutional = int(el.get("institutional", 3))

    threshold = 3  # 每维达标阈值 3/5
    dims_passed = sum(
        [
            1 if theory >= threshold else 0,
            1 if behavioral >= threshold else 0,
            1 if microstructure >= threshold else 0,
            1 if institutional >= threshold else 0,
        ]
    )

    return EconomicScore(
        theory=theory,
        behavioral=behavioral,
        microstructure=microstructure,
        institutional=institutional,
        dimensions_passed=dims_passed,
        narrative=el.get("narrative", ""),
    )


# ─── Level 3: 多重检验校正 ────────────────────────────────


def evaluate_multiple_tests(
    factors_evaluations: list[FactorEvaluation],
    correlation_matrix: Optional[np.ndarray] = None,
    alpha: float = 0.01,
    fdr_q: float = 0.05,
    n_tested: Optional[int] = None,
) -> MultipleTestResult:
    """Level 3 — 多重检验校正。

    Bonferroni: p_adj = p * n
    FDR (Benjamini-Hochberg): 控制假阳性比例

    Args:
        factors_evaluations: 全部因子的评估结果
        correlation_matrix: 因子相关性矩阵（用于有效因子数调整）
        alpha: 显著性水平
        fdr_q: FDR 阈值
        n_tested: 本批次实际被检验的候选因子总数（GAP-121 评估链修复）。
            多重检验校正应基于"同一选择过程中被检验的全部候选数"，
            而非传入列表长度——演化逐因子评估时列表仅含当前/已评估因子，
            若以列表长度代替，第一个因子 n=1 使 Bonferroni 退化为无校正。
            None = 回退 len(factors_evaluations)。

    Returns:
        MultipleTestResult（针对当前批次的统计）
    """
    n = max(1, n_tested if n_tested is not None else len(factors_evaluations))

    # 收集所有 t 统计量
    t_stats = []
    for ev in factors_evaluations:
        bt = ev.get("level_1_backtest", {})
        t = bt.get("t_stat", 0.0)
        t_stats.append(t)

    # 当前因子的 t（取最后一个）
    current_t = t_stats[-1] if t_stats else 0.0

    # 双侧 p 值（正态近似）
    if current_t != 0:
        p_value = 2 * (1 - sp_stats.norm.cdf(abs(current_t)))
    else:
        p_value = 1.0

    # Bonferroni 校正
    bonferroni_p = min(1.0, p_value * n)

    # 有效因子数（考虑相关性）
    if correlation_matrix is not None and len(correlation_matrix) > 0:
        # 主成分分析近似：特征值 > 1 的数量
        try:
            eigenvalues = np.linalg.eigvalsh(correlation_matrix)
            effective_n = int(np.sum(eigenvalues > 1.0))
        except np.linalg.LinAlgError:
            effective_n = n
    else:
        effective_n = n

    # 调整后 t 统计量
    adjusted_t = current_t / np.sqrt(max(1, effective_n))

    # FDR 通过条件
    fdr_passed = bonferroni_p < alpha or p_value < (fdr_q / n)

    passed = bool(bonferroni_p < alpha and adjusted_t > 2.0 and fdr_passed)

    return MultipleTestResult(
        bonferroni_p=float(bonferroni_p),
        fdr_q=float(fdr_q),
        effective_n_factors=int(effective_n),
        adjusted_t=float(adjusted_t),
        passed=passed,
    )


# ─── 三级评估链 ───────────────────────────────────────────


class EvaluationChain:
    """agentic 三级评估链。

    Usage:
        chain = EvaluationChain()
        evaluation = chain.evaluate(factor, data, forward_returns, all_evaluations)
    """

    def __init__(
        self,
        oos_ratio: float = 0.3,
        periods_per_year: int = 252,
        walk_forward_config: Optional[WalkForwardConfig] = None,
        extreme_perturb_pct: float = 0.01,
    ):
        self.oos_ratio = oos_ratio
        self.periods_per_year = periods_per_year
        self._walk_forward_config = walk_forward_config or dict(DEFAULT_WALK_FORWARD_CONFIG)
        # GAP-F15 (v2.73.0): 极值扰动剔除百分位（默认上下各 1%）
        self.extreme_perturb_pct = extreme_perturb_pct

    def evaluate(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        prior_evaluations: Optional[list[FactorEvaluation]] = None,
        correlation_matrix: Optional[np.ndarray] = None,
        walk_forward_config: Optional[WalkForwardConfig] = None,
        signal_cache: Optional[Any] = None,
    ) -> FactorEvaluation:
        """执行三级评估链（WalkForward 强制走航）。

        Args:
            factor: 待评估因子
            data: OHLCV 数据
            forward_returns: 未来收益率
            prior_evaluations: 之前所有因子的评估结果（用于多重检验）
            correlation_matrix: 因子相关性矩阵
            walk_forward_config: 走航配置（覆盖默认值）
            signal_cache: 可选信号缓存（GAP-070），L1/极值扰动/走航共享信号

        Returns:
            FactorEvaluation
        """
        # Level 1
        bt = evaluate_backtest(
            factor, data, forward_returns, self.oos_ratio, self.periods_per_year, signal_cache=signal_cache
        )
        # GAP-F15 (v2.73.0): 极值扰动 IC 重算——剔除信号上下 pct 百分位极端样本后重算 IC，
        # 供 HighICScreener 的 V2 极值扰动一票否决消费（ic_drop > 25% 拦截）。
        try:
            _executor = FactorExecutor(factor, signal_cache=signal_cache)
            _signal = _executor.execute(data, factor.get("params", {}))
            extreme_perturbation = _compute_extreme_perturbation_ic(
                _signal, forward_returns, pct=self.extreme_perturb_pct
            )
        except Exception:
            extreme_perturbation = None
        # Level 2
        ec = evaluate_economic_logic(factor)
        # Level 3
        all_evals = list(prior_evaluations or [])
        # 当前因子的临时评估（无 Level 3）用于多重检验
        temp_eval = FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=factor["trace_id"],
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=MultipleTestResult(),  # 占位
            passed=False,
            failure_reasons=[],
            evaluated_at=datetime.now().isoformat(),
        )
        all_evals.append(temp_eval)
        mt = evaluate_multiple_tests(all_evals, correlation_matrix)

        # 强制走航验证（多窗口 OOS 评估，替代单窗口切片）
        wf_config = walk_forward_config or self._walk_forward_config
        walk_forward_result = evaluate_walk_forward(
            factor,
            data,
            forward_returns,
            config=wf_config,
            signal_cache=signal_cache,
        )

        # 如果走航成功，用多窗口 IC 均值/标准差更新 BacktestMetrics
        if walk_forward_result and walk_forward_result.get("n_windows_completed", 0) > 0:
            wf_ic_values = [w["ic"] for w in walk_forward_result.get("windows", [])]
            if wf_ic_values:
                import statistics as _stat

                bt["ic"] = _stat.mean(wf_ic_values)
                bt["ic_volatility"] = _stat.stdev(wf_ic_values) if len(wf_ic_values) > 1 else 0.0
                bt["n_walk_windows"] = walk_forward_result.get("n_windows_completed", 0)
                # 更新 backtest 中的 decay_6m 为走航窗口间衰减
                wf_consistency = walk_forward_result.get("ic_consistency", 0.0)
                bt["decay_6m"] = max(0.0, 1.0 - wf_consistency)

        # 失败原因汇总
        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            reasons.append(f"Level 1: IC={bt.get('ic', 0):.4f} < 0.03")
        # G4（35-gap-closure-plan）：IC 显著性硬门槛——块数感知的 t 统计量
        # |ic_t| = |ICIR|×√N（N=IC 块数，块长 20），自动适配样本长度：短样本块数少，
        # 同样 0.30 的 ICIR 显著性不足不再放行。ic_t_stat 由 _block_ic_stats 提供（L458）；
        # 缺失（样本 <2 块）时回退旧 |ICIR|≥0.30 口径，避免短样本误杀。
        ic_t_gate = bt.get("ic_t_stat")
        if ic_t_gate is None:
            icir_fb = abs(float(bt.get("icir_block", bt.get("icir", 0.0)) or 0.0))
            # CTA 手册阶段4（v2.104.0+19）：IR 按因子类别分级门槛（量价 0.30/基本面 0.40/期限结构 0.35）
            ir_gate = factor_ir_threshold(factor)
            if icir_fb < ir_gate:
                reasons.append(f"Level 1: |ICIR|={icir_fb:.4f} < {ir_gate:.2f}（样本不足，回退口径）")
        elif abs(float(ic_t_gate)) < 1.65:
            reasons.append(f"Level 1: |ic_t|={abs(float(ic_t_gate)):.4f} < 1.65")
        # G4（35-gap-closure-plan）：前后半段 IC 符号反转一票否决（过拟合局部最优典型特征）
        if bt.get("sign_flip_half_split", False):
            reasons.append("Level 1: 前后半段 IC 符号反转（不稳定）")
        # G11（35-gap-closure-plan §5.4）：日换手硬剔除（阈值经 FTSConfig 可配，
        # 默认 None=关闭观察——库中换手历史未回填，待真实分布复核后启用）
        try:
            from ..config import get_config as _get_cfg

            _td_max = getattr(_get_cfg(), "factor_turnover_daily_max", None)
        except Exception:  # noqa: BLE001
            _td_max = None
        if _td_max is not None and float(bt.get("turnover_daily", 0.0) or 0.0) > float(_td_max):
            reasons.append(f"Level 1: 日换手={float(bt.get('turnover_daily', 0.0) or 0.0):.4f} > {_td_max}")
        # vwap 近似因子通用 IC 门槛（v2.50.0 审计层统一，覆盖种子+演化全路径）
        factor_code = factor.get("code") or ""
        if "vwap" in str(factor_code).lower() and abs(bt.get("ic", 0)) < 0.08:
            reasons.append(f"Level 1: vwap 近似因子 IC={bt.get('ic', 0):.4f} < 0.08")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"Level 1: 夏普={bt.get('sharpe', 0):.4f} < 1.5")
        if not bt.get("monotonicity", False):
            reasons.append("Level 1: 非单调")
        if ec.get("dimensions_passed", 0) < 3:
            reasons.append(f"Level 2: 维度达标={ec.get('dimensions_passed', 0)} < 3")
        if not mt.get("passed", False):
            reasons.append("Level 3: 多重检验未通过")
        if walk_forward_result is not None and not walk_forward_result.get("passed", False):
            score = walk_forward_result.get("consistency_score", 0)
            reasons.append(f"走航: 稳定性评分={score:.1f} < 60")

        passed = len(reasons) == 0
        return FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=factor["trace_id"],
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            walk_forward=walk_forward_result,
            extreme_perturbation=extreme_perturbation,
            passed=passed,
            failure_reasons=reasons,
            evaluated_at=datetime.now().isoformat(),
        )


def evaluate_walk_forward(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    config: Optional[WalkForwardConfig] = None,
    signal_cache: Optional[Any] = None,
) -> WalkForwardResult:
    """走航验证 — 多窗口样本外稳定性评估。

    使用 WalkForwardOptimizer 进行多窗口滚动验证，
    评估因子在不同时间段的表现稳定性。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率（保留参数以兼容既有签名）
        config: 走航配置（None=使用默认配置）
        signal_cache: 可选信号缓存（GAP-070）

    Returns:
        WalkForwardResult
    """
    optimizer = WalkForwardOptimizer(config=config)

    def _evaluate_window(train_data: pd.DataFrame, oos_data: pd.DataFrame) -> dict[str, float]:
        """单窗口评估函数（注入到 WalkForwardOptimizer）。

        GAP-070（v2.98.0）修正: 样本外收益在 oos 段内自行计算
        （close 差分），而非取全局 forward_returns 尾部——走航窗口是
        全量的中间切片，全局尾部不等于该窗口，原口径导致非末窗口
        IC 失真。修正后与审计侧 `_run_walkforward_oos` 完全同口径，
        支撑审计直接复用评估链走航结果（双重 WalkForward 合并）。
        """
        executor = FactorExecutor(factor, signal_cache=signal_cache)
        params = factor.get("params", {})

        # 样本外信号
        oos_signal = executor.execute(oos_data, params)

        min_len = min(len(oos_signal), len(oos_data))
        if min_len < 2:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

        oos_sig = np.asarray(oos_signal[:min_len], dtype=float)
        # oos 段内自算前向收益（与审计 _run_walkforward_oos 同口径）
        close = oos_data["close"].to_numpy(dtype=float)[:min_len]
        fwd = np.zeros(min_len)
        if min_len > 1:
            fwd[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
        mask = np.isfinite(oos_sig) & np.isfinite(fwd)
        if int(np.sum(mask)) < 10:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
        oos_sig_v = oos_sig[mask]
        oos_ret_v = fwd[mask]

        ic, _ = _compute_ic(oos_sig_v, oos_ret_v)

        # 多空组合收益
        sorted_idx = np.argsort(oos_sig_v)
        top_n = max(1, len(oos_sig_v) // 5)
        long_ret = np.mean(oos_ret_v[sorted_idx[-top_n:]])
        short_ret = np.mean(oos_ret_v[sorted_idx[:top_n]])
        ls_returns = np.full(len(oos_sig_v), long_ret - short_ret)
        sharpe = _compute_sharpe(ls_returns)

        # 换手率
        if len(oos_sig_v) > 1:
            sig_changes = np.abs(np.diff(np.sign(oos_sig_v)))
            turnover = float(np.mean(sig_changes) * 21)
        else:
            turnover = 0.0

        return {"ic": float(ic), "sharpe": float(sharpe), "turnover": float(turnover)}

    return optimizer.evaluate(data, _evaluate_window)


def cross_section_walk_forward(
    factor: FactorProgram,
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    config: Optional[WalkForwardConfig] = None,
    industry_map: Optional[dict[str, str]] = None,
    cap_map: Optional[dict[str, float]] = None,
    style_exposures: Optional[dict[str, Any]] = None,
    vol_map: Optional[dict[str, float]] = None,
) -> WalkForwardResult:
    """横截面走航验证 — 滚动窗口内截面 IC 稳定性（GAP-121 评估链修复）。

    横截面分支（_evaluate_cross_section）此前仅单一 OOS 尾部切片、无多窗口
    滚动验证，walk_forward 为空导致审计 oos_consistency 全部 skipped 放行，
    单一切片样本内 IC 无法排除演化数据窥探。本函数复用 WalkForwardOptimizer
    对共同日期滚动切窗，窗口内调用 cross_section_evaluate_backtest
    （oos_ratio=1.0，窗口本身即评估段）产出多窗口截面 IC。

    Args:
        factor: 因子程序
        panel_data: {symbol: OHLCV DataFrame} 面板
        common_dates: 面板共同日期索引
        config: 走航配置（None=默认；短样本建议 _build_wf_config 适配）
        industry_map / cap_map / style_exposures / vol_map: 中性化参数（透传）

    Returns:
        WalkForwardResult（n_windows_completed < 2 表示无法产出多窗口验证）
    """
    skeleton = pd.DataFrame({"close": 1.0}, index=pd.DatetimeIndex(common_dates))
    optimizer = WalkForwardOptimizer(config=config)

    def _eval_window(train_df: pd.DataFrame, oos_df: pd.DataFrame) -> dict[str, float]:
        oos_dates = pd.DatetimeIndex(oos_df.index)
        if len(oos_dates) < 10:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
        sub_panel = {sym: df[df.index.isin(oos_dates)] for sym, df in panel_data.items()}
        common = oos_dates
        for df in sub_panel.values():
            common = common.intersection(pd.DatetimeIndex(df.index))
        if len(common) < 10:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
        try:
            bt = cross_section_evaluate_backtest(
                factor,
                sub_panel,
                pd.DatetimeIndex(common),
                oos_ratio=1.0,
                industry_map=industry_map,
                cap_map=cap_map,
                style_exposures=style_exposures,
                vol_map=vol_map,
            )
        except Exception as e:  # noqa: BLE001 — 单窗口失败不阻断走航
            logger.warning("横截面走航窗口评估失败: %s", e)
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
        return {
            "ic": float(bt.get("ic", 0.0) or 0.0),
            "sharpe": float(bt.get("sharpe", 0.0) or 0.0),
            "turnover": float(bt.get("turnover_monthly", 0.0) or 0.0),
        }

    return optimizer.evaluate(skeleton, _eval_window)


# ══════════════════════════════════════════════════════════
# 横截面评估（多标的）
# ══════════════════════════════════════════════════════════


def cross_section_evaluate_backtest(
    factor: FactorProgram,
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    oos_ratio: float = 0.3,
    industry_map: Optional[dict[str, str]] = None,
    cap_map: Optional[dict[str, float]] = None,
    style_exposures: Optional[dict[str, Any]] = None,
    vol_map: Optional[dict[str, float]] = None,
    long_only: bool = False,
    horizons: Optional[tuple[int, ...]] = None,
    holdout_ratio: float = 0.2,
    use_panel_vector: Optional[bool] = None,
    holdout_panel_data: Optional[dict[str, pd.DataFrame]] = None,
) -> BacktestMetrics:
    """横截面回测评估 — 单因子在多个标的上跨 section IC。

    流程:
        1. 对每只股票运行因子程序，得到信号数组
        2. 对齐到共同日期，构建信号矩阵 (n_dates, n_stocks)
        3. 计算 forward_returns 矩阵 (n_dates, n_stocks)
        4. 在每期计算截面 Spearman IC
        5. 聚合所有期的 IC 均值/标准差

    自动检测信号方向:
        - 如果多空组合收益均值为负，自动翻转信号并重新计算指标
        - 确保因子方向与预测目标一致

    use_panel_vector（plans/37 Phase 1；plans/39 §11 回退后仅作用于 IC 计算）:
        True=用 panel_vector 全矩阵化 IC（联合掩码 rank + 行内 Pearson）替代
        逐日 spearmanr，产出与旧路径逐位一致；None=读 FTSConfig 配置
        （cross_section_panel_vector，默认 true）；False=旧路径。
        信号/收益构建恒逐品种执行——算子因子面板化 execute_factor_panel 经
        plans/39 §11 实测真实缺口面板 0.3x <5x 门槛登记豁免摘除。

    GAP-075（跨标的稳健性检查）:
        - 输出 `symbol_ic`（逐标的时序 IC，方向翻转同步），供审计 cross_symbol 激活
        - 输出 `symbol_holdout`（行业分层留出验证，`holdout_ratio` 控制留出比例）

    Args:
        factor: 因子程序
        panel_data: {symbol: OHLCV DataFrame} 字典
        common_dates: 共同日期索引
        oos_ratio: 样本外比例
        industry_map: {symbol: industry_name} 行业映射字典（可选，启用后做行业中性化）
        cap_map: {symbol: market_cap} 市值映射字典（可选，配合 industry_map 做双重中性化，GAP-S06 启用分层 IC）
        style_exposures: {style_name: DataFrame} Barra 风格暴露（可选，GAP-S02）。
            启用后在行业中性化基础上叠加风格回归残差（剥离风格暴露）。
        vol_map: {symbol: 年化波动率}（G10，可选）。开启后做波动率截面中性化；
            日期传入时同时做时序月度去季节化（日历季节性剥离）。
        long_only: 仅做多口径（GAP-S07），默认 False（期货横截面）
        horizons: 多持有期 IC 分析（GAP-060 横截面接入）；None 时从配置 eval_horizons 读取（空=关闭）
        holdout_ratio: 标的留出比例（GAP-075，默认 20%；行业分层，缺失回退随机）
        use_panel_vector: 全矩阵化 IC 开关（None=读配置，默认关闭）
        holdout_panel_data: 盲测池 panel（GAP-160，v3.0.0+7）——非空且
            SymbolHoldoutConfig.use_blind_pool=true 时，symbol_holdout 改用盲测池
            品种逐品种时序 IC（与训练池零重叠、真外延泛化），替代训练池内部留出；
            None/关闭时回退训练池内行业分层留出（GAP-075 语义）

    Returns:
        BacktestMetrics
    """
    if use_panel_vector is None:
        try:
            from ..config import get_config

            use_panel_vector = bool(getattr(get_config(), "cross_section_panel_vector", False))
        except Exception:  # noqa: BLE001 — 配置读取失败降级关闭
            use_panel_vector = False
    params = factor.get("params", {})

    # Step 1: 信号与收益构建 — 恒逐品种执行（plans/39 §11 回退，v2.104.0+58）：
    # 真实缺口面板算子因子面板化实测 0.3x（<5x 门槛，98% 列内部缺口致压缩-散射
    # 开销反超），execute_factor_panel 从主链路摘除并登记豁免；use_panel_vector
    # 仅用于 IC 计算分派（_cs_compute_ics_dispatch → 全矩阵化，见 Step 3）。
    executor = FactorExecutor(factor)
    signal_dict, ret_dict = _cs_execute_factors(executor, params, panel_data)
    if len(signal_dict) < 5:
        return _cs_empty_metrics(oos_ratio)
    oos_n = max(int(len(common_dates) * oos_ratio), 5)
    oos_signal, oos_ret = _cs_build_matrices(signal_dict, ret_dict, common_dates, oos_n)
    # GAP-060 横截面接入：标的列表统一定义（中性化/多持有期共用）
    symbols_list = list(signal_dict.keys())

    # Step 2.5: 行业中性化（可选）— GAP-S01: 记录中性化前 IC 供对比
    ic_pre_neutral: Optional[float] = None
    if industry_map is not None:
        # 中性化前 IC（方向检测前，供报告对比剥离效果）
        pre_ics = _cs_compute_ics_dispatch(oos_signal, oos_ret, use_panel_vector)
        if pre_ics:
            ic_pre_neutral = float(np.mean(pre_ics))
        oos_signal = _neutralize_signal_matrix(
            oos_signal,
            symbols_list,
            industry_map,
            cap_map,
        )

    # Step 2.6: Barra 风格中性化（可选）— GAP-S02: 行业去均值后叠加风格回归残差
    # G10: 叠加波动率截面中性化 + 时序月度去季节化（vol_map 非空时）
    if style_exposures is not None or vol_map is not None:
        symbols_list = list(signal_dict.keys())
        from .barra.barra_neutralizer import barra_neutralize_matrix

        oos_signal = barra_neutralize_matrix(
            oos_signal,
            symbols_list,
            style_exposures if style_exposures is not None else {},
            industry_map=None,  # 行业已在 Step 2.5 处理，此处仅风格
            vol_map=vol_map,
            dates=common_dates[-oos_n:],
        )

    # Step 3: 每期截面 IC
    ics = _cs_compute_ics_dispatch(oos_signal, oos_ret, use_panel_vector)
    if not ics:
        return _cs_empty_metrics(oos_ratio)

    # GAP-075: 逐标的时序 IC（供审计 cross_symbol ≥80% 标的 IC 为正；方向翻转后同步取反）
    symbol_ic: dict[str, float] = {}
    for j, sym in enumerate(symbols_list):
        sig_col, ret_col = oos_signal[:, j], oos_ret[:, j]
        valid = ~(np.isnan(sig_col) | np.isnan(ret_col))
        if np.sum(valid) < 5:
            continue
        s, r = sig_col[valid], ret_col[valid]
        if np.std(s) < 1e-10 or np.std(r) < 1e-10:
            continue
        ic_val, _ = sp_stats.spearmanr(s, r)
        if not np.isnan(ic_val):
            symbol_ic[sym] = float(ic_val)

    ic_mean = float(np.mean(ics))
    ic_std = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    icir = float(ic_mean / max(ic_std, 1e-10))

    # Step 3.5: 分层 IC（GAP-S06）— 按市值三分位计算各层 IC
    layer_ic: dict[str, float] = {}
    if cap_map is not None:
        symbols_list = list(signal_dict.keys())
        cap_values = np.array([cap_map.get(sym, np.nan) for sym in symbols_list], dtype=float)
        layer_ic = _cs_compute_layer_ics(oos_signal, oos_ret, cap_values)

    # Step 4: 多空组合收益 + 方向检测
    ls_returns = _cs_long_short_returns(oos_signal, oos_ret)
    ls_mean = float(np.mean(ls_returns))

    # 信号方向翻转标志（plans/53 §A2）：regime 画像需用方向校正后的信号
    _was_flipped = False

    # 如果多空收益为负，翻转信号方向
    if ls_mean < 0:
        oos_signal_flipped = -oos_signal
        _was_flipped = True
        ls_returns = _cs_long_short_returns(oos_signal_flipped, oos_ret)
        # 重新计算 IC（翻转后 Spearman 相关取反）
        ics_flipped = [-ic for ic in ics]
        ic_mean = -ic_mean  # Spearman 相关取反
        icir = -icir
        ics = ics_flipped
        # 中性化前 IC 同步翻转（保持同一方向语义）
        if ic_pre_neutral is not None:
            ic_pre_neutral = -ic_pre_neutral
        # GAP-075: 逐标的 IC 同步翻转（信号方向取反 → 各标的 IC 取反）
        symbol_ic = {k: -v for k, v in symbol_ic.items()}

    sharpe = _compute_sharpe(ls_returns)
    cumulative = np.cumsum(ls_returns)
    max_dd = _compute_max_drawdown(cumulative)
    t_stat = _cs_t_stat(ls_returns)

    # Step 4b: 仅做多夏普（GAP-S07）— 独立于方向检测，反映真实多头收益
    lo_sharpe: Optional[float] = None
    if long_only:
        lo_returns = _cs_long_short_returns(oos_signal, oos_ret, long_only=True)
        lo_sharpe = _compute_sharpe(lo_returns)

    metrics = BacktestMetrics(
        ic=ic_mean,
        icir=icir,
        sharpe=sharpe,
        max_drawdown=max_dd,
        monotonicity=True,
        oos_ratio=oos_ratio,
        t_stat=t_stat,
        turnover_monthly=0.0,
    )
    if ic_pre_neutral is not None:
        metrics["ic_pre_neutral"] = ic_pre_neutral
    if layer_ic:
        metrics["layer_ic"] = layer_ic
    if lo_sharpe is not None:
        metrics["long_only_sharpe"] = lo_sharpe

    # GAP-062 统计补全（横截面路径）：IC t 值 / IC 胜率 / 截面分散度 / Q1-Q5 分组
    if len(ics) > 1:
        ic_std_dd = float(np.std(ics, ddof=1))
        metrics["ic_t_stat"] = float(ic_mean / max(ic_std_dd / np.sqrt(len(ics)), 1e-10))
        metrics["win_rate"] = float(np.mean(np.asarray(ics) > 0))
    disp_vals: list[float] = []
    for d in range(oos_signal.shape[0]):
        row = oos_signal[d]
        row_valid = row[np.isfinite(row)]
        if len(row_valid) >= 2:
            disp_vals.append(float(np.std(row_valid)))
    if disp_vals:
        metrics["cs_dispersion"] = float(np.mean(disp_vals))
    qr = _cs_quintile_returns(oos_signal, oos_ret)
    if qr:
        metrics["quintile_returns"] = qr

    # GAP-062 补充（横截面路径）：信号翻转频率 + 最大连续亏损（时序路径已实现）
    metrics["sign_flip_rate"] = float(np.mean(np.abs(np.diff(np.sign(oos_signal), axis=0))) / 2) if oos_signal.shape[0] > 1 else 0.0
    # G11（35-gap-closure-plan §5.4）：横截面日换手口径——与 sign_flip_rate 同源
    # （时间轴信号翻转率 mean(|Δsign|)/2）；月度 turnover = 日口径 × 42（= mean(|Δsign|) × 21）。
    # NaN 安全：真实面板存在缺失行（NaN），翻转差分后需剔除无效对再取均值。
    if oos_signal.shape[0] > 1:
        _cs_diffs = np.abs(np.diff(np.sign(oos_signal), axis=0))
        if np.isfinite(_cs_diffs).any():
            _turnover_daily_cs = float(np.nanmean(_cs_diffs)) / 2.0
            metrics["turnover_daily"] = _turnover_daily_cs
            metrics["turnover_monthly"] = _turnover_daily_cs * 42.0
    if len(ls_returns) > 0:
        metrics["max_consecutive_losses"] = _max_consecutive_losses(ls_returns)

    # GAP-060 横截面多持有期 IC：配置 eval_horizons 控制（空=关闭）；显式传入优先
    if horizons is None:
        try:
            from ..config import get_config

            horizons = getattr(get_config(), "eval_horizons", ()) or None
        except Exception:  # noqa: BLE001 — 配置读取失败降级关闭
            horizons = None
    if horizons is not None:
        try:
            hr = compute_cs_multi_horizon_ic(
                oos_signal,
                panel_data,
                symbols_list,
                common_dates,
                oos_n,
                horizons=horizons,
                use_panel_vector=use_panel_vector,
            )
            if hr is not None:
                metrics["multi_horizon"] = hr.to_dict()
        except Exception:  # noqa: BLE001 — 多持有期分析失败降级，不阻断既有评估
            pass

    # GAP-075: 跨标的稳健性检查输出
    if symbol_ic:
        metrics["symbol_ic"] = symbol_ic

    # GAP-160 (v3.0.0+7): 板块级覆盖率（cross_symbol 软门控D 通道输入）
    metrics["cross_symbol_sector_coverage"] = _cs_sector_coverage(symbol_ic, industry_map)

    # GAP-075/160: 跨标的留出验证——use_blind_pool 默认接入盲测池（FUTURES_HOLDOUT）
    try:
        from .symbol_holdout import SymbolHoldoutConfig, run_symbol_holdout

        ho_cfg = SymbolHoldoutConfig(holdout_ratio=holdout_ratio)
        if ho_cfg.use_blind_pool and holdout_panel_data:
            # 盲测池模式：训练池 vs 盲测池逐品种时序 IC（全期原始信号，方向同步取反）
            direction = -1.0 if _was_flipped else 1.0
            tr_ics = _cs_symbol_ts_ics(signal_dict, ret_dict, direction)
            ho_sig, ho_ret = _cs_execute_factors(executor, params, holdout_panel_data)
            ho_ics = _cs_symbol_ts_ics(ho_sig, ho_ret, direction)
            metrics["symbol_holdout"] = _cs_blind_holdout_metrics(
                tr_ics,
                ho_ics,
                ho_cfg,
                list(holdout_panel_data.keys()),
            )
        else:
            ho = run_symbol_holdout(
                signal_dict,
                ret_dict,
                ho_cfg,
                industry_map,
            )
            metrics["symbol_holdout"] = ho.to_dict() if ho is not None else None
    except Exception:  # noqa: BLE001 — 留出验证失败降级为 None，不阻断既有评估
        logger.warning("标的留出验证失败，降级为 None")
        metrics["symbol_holdout"] = None

    # GAP-121 补全（横截面路径）：跨品种 IC 正向占比（供 HighICScreener
    # industry_coverage 消费；此前横截面评估不产出该字段导致该项恒 skipped）。
    if symbol_ic:
        _pos_cnt = sum(1 for v in symbol_ic.values() if v > 0)
        metrics["cross_symbol_positive_ratio"] = float(_pos_cnt) / float(len(symbol_ic))

    # plans/47 §C2：子链 IC 画像报告段（A 模块产物——每因子×子链显著性画像）。
    # 仅当四子链中至少一个品种命中（n_symbols>0）时输出，避免污染无子链语义的评估
    # （股票 symbol_ic 键不匹配 ENERGY_CHAIN_SUB_SYMBOLS → 全 n=0 → 不输出）。
    if symbol_ic:
        try:
            from .subchain_profile import build_subchain_metadata

            _scm = build_subchain_metadata(str(factor.get("factor_id", "")), symbol_ic)
            _any_hit = any(
                st.get("n_symbols", 0) > 0 for st in _scm["subchain_ic_profile"].values()
            )
            if _any_hit:
                metrics["subchain_ic_report"] = _scm
        except Exception as e:  # noqa: BLE001 — 画像生成失败降级，不阻断既有评估
            logger.warning("[subchain_ic_report] 生成失败（非致命，跳过）: %s", e)

    # plans/53 §A2：Regime 画像报告段（因子×制度显著性画像，开关 l3_regime_ic_report_enabled）。
    # 市场级信号 = 横截面均值（方向校正后），regime_series = 面板合成 OHLCV 滚动检测。
    # 仅 energy 面板（symbol 命中 ENERGY_CHAIN_SYMBOLS）且开关开启时构建
    # （RegimeSeriesBuilder 滚动检测有秒级成本，批量评估场景不默认开启，防性能退化）。
    try:
        from fts.config.settings import get_config as _rp_cfg

        _rp_enabled = bool(getattr(_rp_cfg(), "l3_regime_ic_report_enabled", False))
    except Exception:  # noqa: BLE001
        _rp_enabled = False
    if _rp_enabled and panel_data:
        try:
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS

            _is_energy = bool(set(panel_data.keys()) & set(ENERGY_CHAIN_SYMBOLS))
        except Exception:  # noqa: BLE001
            _is_energy = False
        if _is_energy:
            try:
                from .regime_profile import RegimeSeriesBuilder, build_regime_metadata

                _builder = RegimeSeriesBuilder()
                _regime_series = _builder.build_from_panel(panel_data, sorted(panel_data.keys()))
                if not _regime_series.empty:
                    _oos_dates = common_dates[-oos_n:]
                    _sig_arr = -oos_signal if _was_flipped else oos_signal
                    _mkt_sig = pd.Series(np.nanmean(_sig_arr, axis=1), index=_oos_dates)
                    _mkt_ret = pd.Series(np.nanmean(oos_ret, axis=1), index=_oos_dates)
                    _al = pd.DataFrame({"sig": _mkt_sig, "fwd": _mkt_ret}).reindex(
                        _regime_series.index
                    )
                    _valid = _al.dropna()
                    if len(_valid) >= 20:
                        _rm = build_regime_metadata(
                            str(factor.get("factor_id", "")),
                            _valid["sig"].to_numpy(),
                            _valid["fwd"].to_numpy(),
                            _regime_series.loc[_valid.index].to_numpy(),
                        )
                        metrics["regime_ic_report"] = _rm
            except Exception as e:  # noqa: BLE001 — 画像生成失败降级，不阻断既有评估
                logger.warning("[regime_ic_report] 生成失败（非致命，跳过）: %s", e)

    # GAP-121 补全（横截面路径）：极值扰动 IC 重算（供 HighICScreener
    # V2 / extreme_perturb 消费）。将全部品种的截面信号/收益扁平化为长数组，
    # 剔除极端信号样本后重算 IC（|IC| 口径，方向翻转不影响）。
    try:
        metrics["extreme_perturbation"] = _compute_extreme_perturbation_ic(
            oos_signal.reshape(-1).astype(float),
            oos_ret.reshape(-1).astype(float),
            pct=0.01,
        )
    except Exception:  # noqa: BLE001 — 计算失败保持缺失，由 screener skipped 兜底
        pass

    # GAP-121 补全（横截面路径）：扣成本后净超额（月频，供 HighICScreener
    # V4 / net_excess 消费）。多空组合月度均值 − 月度双边成本（单边 0.05%，
    # 与 FactorVerifier 成本口径一致）。
    try:
        _monthly_gross = float(np.mean(ls_returns)) * 21.0
        _monthly_cost = float(metrics.get("turnover_monthly", 0.0) or 0.0) * 2.0 * 0.0005
        metrics["net_excess_return"] = _monthly_gross - _monthly_cost
    except Exception:  # noqa: BLE001 — 成本估算失败保持缺失
        pass
    return metrics


def _cs_execute_factors(
    executor: FactorExecutor,
    params: dict,
    panel_data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """横截面 Step 1: 对每只股票运行因子 + forward_return。"""
    signal_dict: dict[str, pd.Series] = {}
    ret_dict: dict[str, pd.Series] = {}
    for sym, df in panel_data.items():
        try:
            sig_arr = executor.execute(df, params)
            signal_dict[sym] = pd.Series(sig_arr, index=df.index)
            closes = df["close"].values
            fwd_ret = np.zeros(len(closes))
            if len(closes) > 5:
                fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
            ret_dict[sym] = pd.Series(fwd_ret, index=df.index)
        except Exception:  # noqa: BLE001
            continue
    return signal_dict, ret_dict


def _cs_symbol_ts_ics(
    signal_dict: dict[str, pd.Series],
    ret_dict: dict[str, pd.Series],
    direction: float = 1.0,
    min_valid: int = 5,
) -> list[float]:
    """逐品种时序 Spearman IC（全期原始信号，与 symbol_ic 同构；direction 同步取反）。

    不要求品种间共同日期窗口——晚上市品种（如 EC0）仅用自身有效期，规避
    共同窗口被新上市品种拖累（GAP-160 盲测池接入的关键口径选择）。
    """
    ics: list[float] = []
    for sym in signal_dict:
        sig = signal_dict[sym]
        ret = ret_dict.get(sym)
        if ret is None:
            continue
        valid = ~(sig.isna() | ret.isna())
        if int(valid.sum()) < min_valid:
            continue
        s = sig[valid].to_numpy(dtype=float)
        r = ret[valid].to_numpy(dtype=float)
        if np.std(s) < 1e-10 or np.std(r) < 1e-10:
            continue
        ic_val, _ = sp_stats.spearmanr(s, r)
        if not np.isnan(ic_val):
            ics.append(float(ic_val) * direction)
    return ics


def _cs_blind_holdout_metrics(
    train_ics: list[float],
    holdout_ics: list[float],
    cfg: Any,
    holdout_symbols: list[str],
) -> Optional[dict[str, Any]]:
    """盲测池留出验证指标（GAP-160，v3.0.0+7）。

    train_ic = 训练池品种时序 IC 均值（同口径），holdout_ic = 盲测池品种时序 IC 均值；
    retention = holdout_ic / |train_ic|；弱信号（|train_ic| < min_train_ic）返回 None
    （审计项 skipped，GAP-116 弱信号保护）。判定与训练池内留出模式同阈值。
    """
    if not train_ics or not holdout_ics:
        return None
    train_ic = float(np.mean(train_ics))
    holdout_ic = float(np.mean(holdout_ics))
    if abs(train_ic) < cfg.min_train_ic:
        return None
    retention = holdout_ic / abs(train_ic) if abs(train_ic) > 1e-10 else (1.0 if holdout_ic >= 0 else -1.0)
    passed = holdout_ic >= cfg.min_holdout_ic and retention >= cfg.min_ic_retention
    return {
        "n_train": int(len(train_ics)),
        "n_holdout": int(len(holdout_ics)),
        "train_ic": float(train_ic),
        "holdout_ic": float(holdout_ic),
        "ic_retention": float(retention),
        "passed": bool(passed),
        "holdout_symbols": list(holdout_symbols),
        "detail": {"n_dates": 0, "stratified": False, "blind_pool": True},
    }


def _cs_sector_coverage(
    symbol_ic: Optional[dict[str, float]],
    industry_map: Optional[dict[str, str]],
) -> Optional[dict[str, Any]]:
    """板块级覆盖率（GAP-160，cross_symbol 软门控D 通道输入）。

    按 industry_map（{symbol: sector}）将品种归板块，统计"板块内品种 IC 为正
    比例 ≥ 50%"的板块数（covered）与训练池覆盖板块总数（total）。无映射返回 None
    （审计通道降级不启用）。
    """
    if not symbol_ic or not industry_map:
        return None
    by_sector: dict[str, list[float]] = {}
    for sym, ic in symbol_ic.items():
        sec = industry_map.get(sym)
        if sec:
            by_sector.setdefault(sec, []).append(float(ic))
    if not by_sector:
        return None
    covered = 0
    for ics in by_sector.values():
        if ics and sum(1 for ic in ics if ic > 0) / len(ics) >= 0.5:
            covered += 1
    return {"covered_sectors": int(covered), "total_sectors": int(len(by_sector))}


def _cs_build_matrices(
    signal_dict: dict[str, pd.Series],
    ret_dict: dict[str, pd.Series],
    common_dates: pd.DatetimeIndex,
    oos_n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """横截面 Step 2: 对齐到共同日期，构建矩阵 + OOS 切片。"""
    symbols_list = list(signal_dict.keys())
    n_dates = len(common_dates)
    n_stocks = len(symbols_list)
    signal_matrix = np.zeros((n_dates, n_stocks))
    ret_matrix = np.zeros((n_dates, n_stocks))
    for j, sym in enumerate(symbols_list):
        signal_matrix[:, j] = signal_dict[sym].reindex(common_dates).values
        ret_matrix[:, j] = ret_dict[sym].reindex(common_dates).values
    return signal_matrix[-oos_n:, :], ret_matrix[-oos_n:, :]


def _cs_compute_ics(oos_signal: np.ndarray, oos_ret: np.ndarray) -> list[float]:
    """横截面 Step 3: 每期 Spearman IC。"""
    ics: list[float] = []
    for t in range(oos_signal.shape[0]):
        sig_t = oos_signal[t, :]
        ret_t = oos_ret[t, :]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        if np.sum(valid) < 5:
            continue
        sig_valid = sig_t[valid]
        ret_valid = ret_t[valid]
        if np.std(sig_valid) < 1e-10 or np.std(ret_valid) < 1e-10:
            continue
        ic_val, _ = sp_stats.spearmanr(sig_valid, ret_valid)
        if not np.isnan(ic_val):
            ics.append(ic_val)
    return ics


def _cs_compute_ics_dispatch(
    oos_signal: np.ndarray,
    oos_ret: np.ndarray,
    use_panel_vector: bool,
) -> list[float]:
    """截面 IC 计算分派：panel_vector 全矩阵化 或 旧路径逐日 spearmanr。

    panel_vector 路径（plans/37 Phase 1，fts/factor_engine/panel_vector.py）与
    `_cs_compute_ics` 逐日一致（联合掩码 rank + 行内 Pearson，常数守卫/有效样本
    下限同口径，经 tests/factor_engine/test_panel_vector.py 对照验证）；返回
    list[float] 与 `_cs_compute_ics` 同构，下游消费零改动。

    Args:
        oos_signal / oos_ret: (n_dates, n_symbols) 矩阵
        use_panel_vector: True=全矩阵化；False=旧路径逐日 spearmanr

    Returns:
        逐日 IC 序列（跳过无效日）
    """
    if use_panel_vector:
        from .panel_vector import compute_cs_ics_vectorized

        ics_arr, _ = compute_cs_ics_vectorized(oos_signal, oos_ret)
        return [float(v) for v in ics_arr if not np.isnan(v)]
    return _cs_compute_ics(oos_signal, oos_ret)


def _cs_compute_layer_ics(
    oos_signal: np.ndarray,
    oos_ret: np.ndarray,
    cap_values: np.ndarray,
) -> dict[str, float]:
    """横截面分层 IC（GAP-S06）：按市值三分位计算各层 IC。

    Args:
        oos_signal: (n_dates, n_stocks) 信号矩阵
        oos_ret: (n_dates, n_stocks) 收益矩阵
        cap_values: (n_stocks,) 市值数组（列对应标的）

    Returns:
        {layer_name: ic_mean}，如 {"large": 0.03, "mid": 0.02, "small": 0.01}
    """
    n_dates = oos_signal.shape[0]
    n_stocks = len(cap_values)
    if n_stocks < 6:
        return {}
    # 按市值三分位分组
    valid_cap = np.where(np.isfinite(cap_values), cap_values, np.nanmedian(cap_values))
    terciles = np.percentile(valid_cap, [33.3, 66.7])
    large_mask = valid_cap >= terciles[1]
    mid_mask = (valid_cap >= terciles[0]) & (valid_cap < terciles[1])
    small_mask = valid_cap < terciles[0]
    layers: dict[str, np.ndarray] = {
        "large": large_mask,
        "mid": mid_mask,
        "small": small_mask,
    }
    result: dict[str, float] = {}
    for layer_name, mask in layers.items():
        layer_ics: list[float] = []
        for t in range(n_dates):
            sig_t = oos_signal[t, mask]
            ret_t = oos_ret[t, mask]
            valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
            if np.sum(valid) < 5:
                continue
            sig_v = sig_t[valid]
            ret_v = ret_t[valid]
            if np.std(sig_v) < 1e-10 or np.std(ret_v) < 1e-10:
                continue
            ic_val, _ = sp_stats.spearmanr(sig_v, ret_v)
            if not np.isnan(ic_val):
                layer_ics.append(ic_val)
        if layer_ics:
            result[layer_name] = float(np.mean(layer_ics))
    return result


def _cs_long_short_returns(
    oos_signal: np.ndarray,
    oos_ret: np.ndarray,
    long_only: bool = False,
) -> np.ndarray:
    """横截面: 多空组合收益（每期 top 20% - bottom 20%）。

    Args:
        oos_signal: (n_dates, n_stocks) 信号矩阵
        oos_ret: (n_dates, n_stocks) 收益矩阵
        long_only: 仅做多模式（GAP-S07），仅取 top 20% 多头收益

    Returns:
        每期组合收益数组
    """
    oos_n = oos_signal.shape[0]
    ls_returns = np.zeros(oos_n)
    for t in range(oos_n):
        sig_t = oos_signal[t, :]
        ret_t = oos_ret[t, :]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        valid_count = int(np.sum(valid))
        if valid_count < 3:
            continue
        sig_v = sig_t[valid]
        ret_v = ret_t[valid]
        sorted_idx = np.argsort(sig_v)
        top_n = max(1, len(sorted_idx) // 5)
        long_ret = np.mean(ret_v[sorted_idx[-top_n:]])
        if long_only:
            ls_returns[t] = long_ret
        else:
            short_ret = np.mean(ret_v[sorted_idx[:top_n]])
            ls_returns[t] = long_ret - short_ret
    return ls_returns


def _cs_t_stat(ls_returns: np.ndarray) -> float:
    """横截面: t 统计量。"""
    if np.std(ls_returns) > 1e-10:
        return float(np.mean(ls_returns) / np.std(ls_returns, ddof=1) * np.sqrt(len(ls_returns)))
    return 0.0


def _cs_empty_metrics(oos_ratio: float) -> BacktestMetrics:
    """横截面: 数据不足时的空指标。"""
    return BacktestMetrics(
        ic=0.0,
        icir=0.0,
        sharpe=0.0,
        max_drawdown=0.0,
        monotonicity=False,
        oos_ratio=oos_ratio,
        t_stat=0.0,
        turnover_monthly=0.0,
    )


def _neutralize_signal_matrix(
    signal_matrix: np.ndarray,
    symbols_list: list[str],
    industry_map: dict[str, str],
    cap_map: Optional[dict[str, float]] = None,
) -> np.ndarray:
    """横截面信号矩阵行业中性化。

    对每期（每行）信号，按行业分组去均值，消除行业系统性偏差。
    cap_map 提供时额外做市值加权去均值（双重中性化）。

    Args:
        signal_matrix: (n_dates, n_stocks) 信号矩阵
        symbols_list: 标的列表，与 signal_matrix 列顺序一致
        industry_map: {symbol: industry_name} 行业映射
        cap_map: {symbol: market_cap} 市值映射（可选）

    Returns:
        中性化后的信号矩阵，保持原形状
    """
    from collections import defaultdict

    n_stocks = len(symbols_list)
    if n_stocks == 0:
        return signal_matrix

    # 构建行业标签数组 (n_stocks,)
    industry_labels: list[str] = []
    for sym in symbols_list:
        ind = industry_map.get(sym, "UNKNOWN")
        industry_labels.append(ind)

    # 构建市值权重数组 (n_stocks,)，可选
    cap_weights: Optional[np.ndarray] = None
    if cap_map is not None:
        caps = np.array([cap_map.get(sym, 0.0) for sym in symbols_list], dtype=float)
        total_cap = float(np.sum(caps))
        if total_cap > 0:
            cap_weights = caps / total_cap

    result = signal_matrix.copy()

    for t in range(result.shape[0]):
        sig_t = result[t, :]
        valid = ~np.isnan(sig_t)

        if np.sum(valid) < 3:
            continue

        # 按行业分组计算均值并去均值
        industry_vals: dict[str, list[float]] = defaultdict(list)
        for j in range(n_stocks):
            if valid[j]:
                industry_vals[industry_labels[j]].append(sig_t[j])

        industry_means = {ind: np.mean(vals) for ind, vals in industry_vals.items()}

        # 行业去均值
        for j in range(n_stocks):
            if valid[j]:
                result[t, j] = sig_t[j] - industry_means.get(industry_labels[j], 0.0)

        # 市值加权去均值（双重中性化）
        if cap_weights is not None:
            residual = result[t, :].copy()
            valid_residual = residual[valid]
            valid_weights = cap_weights[valid]
            w_sum = float(np.sum(valid_weights))
            if w_sum > 0:
                weighted_mean = np.sum(valid_residual * valid_weights) / w_sum
                result[t, valid] = residual[valid] - weighted_mean

    return result


__all__ = [
    "evaluate_backtest",
    "evaluate_economic_logic",
    "evaluate_multiple_tests",
    "evaluate_walk_forward",
    "cross_section_evaluate_backtest",
    "compute_cs_multi_horizon_ic",
    "EvaluationChain",
    "_neutralize_signal_matrix",
]
