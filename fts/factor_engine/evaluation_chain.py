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


# ─── Level 1: 回测验证 ────────────────────────────────────

def _compute_ic(
    signal: np.ndarray, forward_returns: np.ndarray, method: str = "spearman"
) -> tuple[float, float]:
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


def evaluate_backtest(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    oos_ratio: float = 0.3,
    periods_per_year: int = 252,
) -> BacktestMetrics:
    """Level 1 — 回测验证。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率（与 data 等长）
        oos_ratio: 样本外比例
        periods_per_year: 年化系数

    Returns:
        BacktestMetrics
    """
    executor = FactorExecutor(factor)
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

    # 6 个月 IC 衰减率计算: OOS 期前后两半的 IC 对比
    decay_6m = 0.0
    if len(oos_signal) >= 20:
        half = len(oos_signal) // 2
        ic_first, _ = _compute_ic(oos_signal[:half], oos_returns[:half])
        ic_second, _ = _compute_ic(oos_signal[half:], oos_returns[half:])
        if abs(ic_first) > 0.01:
            decay_6m = max(0.0, min(1.0, 1.0 - abs(ic_second) / abs(ic_first)))

    return BacktestMetrics(
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
    dims_passed = sum([
        1 if theory >= threshold else 0,
        1 if behavioral >= threshold else 0,
        1 if microstructure >= threshold else 0,
        1 if institutional >= threshold else 0,
    ])

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
) -> MultipleTestResult:
    """Level 3 — 多重检验校正。

    Bonferroni: p_adj = p * n
    FDR (Benjamini-Hochberg): 控制假阳性比例

    Args:
        factors_evaluations: 全部因子的评估结果
        correlation_matrix: 因子相关性矩阵（用于有效因子数调整）
        alpha: 显著性水平
        fdr_q: FDR 阈值

    Returns:
        MultipleTestResult（针对当前批次的统计）
    """
    n = max(1, len(factors_evaluations))

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
    ):
        self.oos_ratio = oos_ratio
        self.periods_per_year = periods_per_year
        self._walk_forward_config = walk_forward_config or dict(DEFAULT_WALK_FORWARD_CONFIG)

    def evaluate(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        prior_evaluations: Optional[list[FactorEvaluation]] = None,
        correlation_matrix: Optional[np.ndarray] = None,
        walk_forward_config: Optional[WalkForwardConfig] = None,
    ) -> FactorEvaluation:
        """执行三级评估链（WalkForward 强制走航）。

        Args:
            factor: 待评估因子
            data: OHLCV 数据
            forward_returns: 未来收益率
            prior_evaluations: 之前所有因子的评估结果（用于多重检验）
            correlation_matrix: 因子相关性矩阵
            walk_forward_config: 走航配置（覆盖默认值）

        Returns:
            FactorEvaluation
        """
        # Level 1
        bt = evaluate_backtest(
            factor, data, forward_returns, self.oos_ratio, self.periods_per_year
        )
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
            factor, data, forward_returns,
            config=wf_config,
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
            passed=passed,
            failure_reasons=reasons,
            evaluated_at=datetime.now().isoformat(),
        )


def evaluate_walk_forward(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    config: Optional[WalkForwardConfig] = None,
) -> WalkForwardResult:
    """走航验证 — 多窗口样本外稳定性评估。

    使用 WalkForwardOptimizer 进行多窗口滚动验证，
    评估因子在不同时间段的表现稳定性。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率
        config: 走航配置（None=使用默认配置）

    Returns:
        WalkForwardResult
    """
    optimizer = WalkForwardOptimizer(config=config)

    def _evaluate_window(train_data: pd.DataFrame,
                         oos_data: pd.DataFrame) -> dict[str, float]:
        """单窗口评估函数（注入到 WalkForwardOptimizer）。"""
        executor = FactorExecutor(factor)
        params = factor.get("params", {})

        # 训练集信号
        train_signal = executor.execute(train_data, params)
        # 样本外信号
        oos_signal = executor.execute(oos_data, params)

        min_len = min(len(oos_signal), len(oos_data))
        if min_len < 2:
            return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

        oos_sig = oos_signal[:min_len]
        oos_ret = forward_returns[-min_len:] if len(forward_returns) >= min_len else np.zeros(min_len)

        ic, _ = _compute_ic(oos_sig, oos_ret)

        # 多空组合收益
        sorted_idx = np.argsort(oos_sig)
        top_n = max(1, len(oos_sig) // 5)
        long_ret = np.mean(oos_ret[sorted_idx[-top_n:]])
        short_ret = np.mean(oos_ret[sorted_idx[:top_n]])
        ls_returns = np.full(len(oos_sig), long_ret - short_ret)
        sharpe = _compute_sharpe(ls_returns)

        # 换手率
        if len(train_signal) > 1:
            sig_changes = np.abs(np.diff(np.sign(oos_sig)))
            turnover = float(np.mean(sig_changes) * 21)
        else:
            turnover = 0.0

        return {"ic": float(ic), "sharpe": float(sharpe), "turnover": float(turnover)}

    return optimizer.evaluate(data, _evaluate_window)


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

    Args:
        factor: 因子程序
        panel_data: {symbol: OHLCV DataFrame} 字典
        common_dates: 共同日期索引
        oos_ratio: 样本外比例
        industry_map: {symbol: industry_name} 行业映射字典（可选，启用后做行业中性化）
        cap_map: {symbol: market_cap} 市值映射字典（可选，配合 industry_map 做双重中性化）
        style_exposures: {style_name: DataFrame} Barra 风格暴露（可选，GAP-S02）。
            启用后在行业中性化基础上叠加风格回归残差（剥离风格暴露）。

    Returns:
        BacktestMetrics
    """
    executor = FactorExecutor(factor)
    params = factor.get("params", {})

    # Step 1: 每只股票运行因子 + 计算 forward_return
    signal_dict, ret_dict = _cs_execute_factors(executor, params, panel_data)
    if len(signal_dict) < 5:
        return _cs_empty_metrics(oos_ratio)

    # Step 2: 对齐到共同日期，构建矩阵 + OOS 切片
    oos_n = max(int(len(common_dates) * oos_ratio), 5)
    oos_signal, oos_ret = _cs_build_matrices(signal_dict, ret_dict, common_dates, oos_n)

    # Step 2.5: 行业中性化（可选）— GAP-S01: 记录中性化前 IC 供对比
    ic_pre_neutral: Optional[float] = None
    if industry_map is not None:
        symbols_list = list(signal_dict.keys())
        # 中性化前 IC（方向检测前，供报告对比剥离效果）
        pre_ics = _cs_compute_ics(oos_signal, oos_ret)
        if pre_ics:
            ic_pre_neutral = float(np.mean(pre_ics))
        oos_signal = _neutralize_signal_matrix(
            oos_signal, symbols_list, industry_map, cap_map,
        )

    # Step 2.6: Barra 风格中性化（可选）— GAP-S02: 行业去均值后叠加风格回归残差
    if style_exposures is not None:
        symbols_list = list(signal_dict.keys())
        from .barra.barra_neutralizer import barra_neutralize_matrix

        oos_signal = barra_neutralize_matrix(
            oos_signal,
            symbols_list,
            style_exposures,
            industry_map=None,  # 行业已在 Step 2.5 处理，此处仅风格
        )

    # Step 3: 每期截面 IC
    ics = _cs_compute_ics(oos_signal, oos_ret)
    if not ics:
        return _cs_empty_metrics(oos_ratio)

    ic_mean = float(np.mean(ics))
    ic_std = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
    icir = float(ic_mean / max(ic_std, 1e-10))

    # Step 4: 多空组合收益 + 方向检测
    ls_returns = _cs_long_short_returns(oos_signal, oos_ret)
    ls_mean = float(np.mean(ls_returns))

    # 如果多空收益为负，翻转信号方向
    if ls_mean < 0:
        oos_signal_flipped = -oos_signal
        ls_returns = _cs_long_short_returns(oos_signal_flipped, oos_ret)
        # 重新计算 IC（翻转后 Spearman 相关取反）
        ics_flipped = [-ic for ic in ics]
        ic_mean = -ic_mean  # Spearman 相关取反
        icir = -icir
        ics = ics_flipped
        # 中性化前 IC 同步翻转（保持同一方向语义）
        if ic_pre_neutral is not None:
            ic_pre_neutral = -ic_pre_neutral

    sharpe = _compute_sharpe(ls_returns)
    cumulative = np.cumsum(ls_returns)
    max_dd = _compute_max_drawdown(cumulative)
    t_stat = _cs_t_stat(ls_returns)

    metrics = BacktestMetrics(
        ic=ic_mean, icir=icir, sharpe=sharpe, max_drawdown=max_dd,
        monotonicity=True, oos_ratio=oos_ratio, t_stat=t_stat, turnover_monthly=0.0,
    )
    if ic_pre_neutral is not None:
        metrics["ic_pre_neutral"] = ic_pre_neutral
    return metrics


def _cs_execute_factors(
    executor: FactorExecutor, params: dict, panel_data: dict[str, pd.DataFrame],
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


def _cs_long_short_returns(oos_signal: np.ndarray, oos_ret: np.ndarray) -> np.ndarray:
    """横截面: 多空组合收益（每期 top 20% - bottom 20%）。"""
    oos_n = oos_signal.shape[0]
    ls_returns = np.zeros(oos_n)
    for t in range(oos_n):
        sig_t = oos_signal[t, :]
        ret_t = oos_ret[t, :]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        valid_count = np.sum(valid)
        if valid_count < 3:
            continue
        sig_v = sig_t[valid]
        ret_v = ret_t[valid]
        sorted_idx = np.argsort(sig_v)
        top_n = max(1, len(sorted_idx) // 5)
        long_ret = np.mean(ret_v[sorted_idx[-top_n:]])
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
        ic=0.0, icir=0.0, sharpe=0.0, max_drawdown=0.0,
        monotonicity=False, oos_ratio=oos_ratio, t_stat=0.0, turnover_monthly=0.0,
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
        total_cap = np.sum(caps)
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

        industry_means = {
            ind: np.mean(vals) for ind, vals in industry_vals.items()
        }

        # 行业去均值
        for j in range(n_stocks):
            if valid[j]:
                result[t, j] = sig_t[j] - industry_means.get(industry_labels[j], 0.0)

        # 市值加权去均值（双重中性化）
        if cap_weights is not None:
            residual = result[t, :].copy()
            valid_residual = residual[valid]
            valid_weights = cap_weights[valid]
            w_sum = np.sum(valid_weights)
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
    "EvaluationChain",
    "_neutralize_signal_matrix",
]
