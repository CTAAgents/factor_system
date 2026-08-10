"""
scripts/_signal_common.py — 信号管道公共模块（GAP-S04）

共享函数（从 futures_signal_pipeline.py 提取，供股票/期货信号管道复用）：
    - _compute_factor_sign_flips: 截面 IC 方向校正
    - _compute_ridge_weights: Ridge 回归权重学习（含相关性惩罚）
    - _compute_composite_scores: 加权合成（方向校正 + 权重）

用法:
    from _signal_common import _compute_factor_sign_flips, ...

版本: v1.0.0 (GAP-S04)
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*treating keys as positions is deprecated.*")
warnings.filterwarnings(
    "ignore", category=FutureWarning, message=".*Series.__setitem__ treating keys as positions is deprecated.*"
)
try:
    from scipy.stats import ConstantInputWarning

    warnings.filterwarnings("ignore", category=ConstantInputWarning)
except ImportError:
    pass
warnings.filterwarnings("ignore", message=".*An input array is constant.*")


# ─── 方向校正 ────────────────────────────────────────────────


def compute_factor_sign_flips(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, pd.DataFrame],
    common_dates: list[str],
    ic_lookback: int = 20,
) -> dict[str, float]:
    """用截面 IC 法计算每个因子是否需要反转信号。

    对每个因子，遍历最近 ic_lookback 个交易日，收集该日所有标的的
    因子信号值与未来 5 日收益率，计算 Spearman 秩相关性（截面 IC），
    取平均。如果平均 IC < 0，反转因子信号（flip = -1.0）。

    Args:
        signal_matrix: 信号矩阵 {symbol: {factor_name: array}}
        panel: 行情面板 {symbol: DataFrame}
        common_dates: 共同交易日列表（字符串格式）
        ic_lookback: 使用最近多少天的数据计算截面 IC

    Returns:
        dict[factor_name, sign_flip]  # +1=正常, -1=需反转
    """
    from scipy.stats import spearmanr

    if not signal_matrix:
        return {}
    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())

    n_dates = len(common_dates)
    start_idx = max(0, n_dates - ic_lookback - 5)

    factor_sign_flips: dict[str, float] = {}
    for fname in factor_names:
        daily_ics: list[float] = []
        for t in range(start_idx, n_dates - 5):
            signals_t: dict[str, float] = {}
            future_rets: dict[str, float] = {}
            t_date = common_dates[t]
            for sym in signal_matrix:
                sig = signal_matrix[sym].get(fname)
                if sig is None:
                    continue
                df = panel.get(sym)
                if df is None or df.empty:
                    continue
                try:
                    pos = df.index.get_loc(t_date)
                except (KeyError, TypeError):
                    continue
                if pos >= len(sig) or not np.isfinite(sig[pos]):
                    continue
                signals_t[sym] = float(sig[pos])

                closes = df["close"].values
                if pos + 5 >= len(closes):
                    continue
                p_t = closes[pos]
                if not np.isfinite(p_t) or p_t <= 1e-10:
                    continue
                ret = (closes[pos + 5] - p_t) / p_t
                if np.isfinite(ret):
                    future_rets[sym] = ret

            common = set(signals_t.keys()) & set(future_rets.keys())
            if len(common) >= 5:
                s_vals = [signals_t[s] for s in common]
                r_vals = [future_rets[s] for s in common]
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    try:
                        from scipy.stats import ConstantInputWarning

                        warnings.filterwarnings("ignore", category=ConstantInputWarning)
                    except ImportError:
                        pass
                    r, _ = spearmanr(s_vals, r_vals)
                if not np.isnan(r):
                    daily_ics.append(r)

        if daily_ics:
            avg_ic = np.mean(daily_ics)
            factor_sign_flips[fname] = -1.0 if avg_ic < 0 else 1.0
        else:
            factor_sign_flips[fname] = 1.0

    return factor_sign_flips


# ─── Ridge 权重学习 ──────────────────────────────────────────


def compute_ridge_weights(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, pd.DataFrame],
    common_dates: list[str],
    factor_sign_flips: dict[str, float],
    lookback: int = 120,
    alpha: float | None = None,
    corr_penalty_lambda: float = 0.5,
    extreme_threshold: float = 0.90,
) -> dict[str, float]:
    """用 Ridge 回归学习因子权重（替代等权合成）。

    以方向校正后的因子信号值为特征、未来 5 日收益为目标，
    Ridge 回归拟合系数取绝对值作为因子权重。
    强因子自动获得高权重，弱因子获得接近零的权重但不被丢弃。

    Args:
        signal_matrix: 信号矩阵 {symbol: {factor_name: array}}
        panel: 行情面板 {symbol: DataFrame}
        common_dates: 共同交易日列表
        factor_sign_flips: 方向校正 {+1=正常, -1=需反转}
        lookback: 训练窗口天数
        alpha: Ridge 正则化强度，None 则用 RidgeCV 自动选择
        corr_penalty_lambda: 相关性惩罚强度（0=关闭）
        extreme_threshold: 极端相关硬删除阈值（默认 0.90）

    Returns:
        dict[factor_name, weight]  权重（已归一化，和为 1）
    """
    try:
        from sklearn.linear_model import RidgeCV
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("      [Ridge] sklearn 不可用，回退到等权")
        first_sym = next(iter(signal_matrix))
        factor_names = list(signal_matrix[first_sym].keys())
        n = len(factor_names)
        return {f: 1.0 / n for f in factor_names}

    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())
    n_factors = len(factor_names)

    if n_factors <= 1:
        return {f: 1.0 for f in factor_names}

    # 取所有品种共有的因子交集
    common_factors = set(factor_names)
    for sym in signal_matrix:
        common_factors &= set(signal_matrix[sym].keys())
    factor_names = sorted(common_factors)
    n_factors = len(factor_names)
    if n_factors <= 1:
        fallback = {f: 1.0 / n_factors for f in factor_names} if n_factors > 0 else {}
        return fallback

    # 过滤训练窗口内 NaN 率过高的因子
    n_dates = len(common_dates)
    train_start = max(0, n_dates - lookback - 5)
    train_end = n_dates - 5
    valid_factor_names: list[str] = []
    for fname in factor_names:
        nan_count = 0
        total = 0
        for sym in signal_matrix:
            arr = signal_matrix[sym].get(fname)
            if arr is None:
                continue
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            for t in (train_start, train_end - 1):
                if t >= train_end:
                    continue
                try:
                    pos = df.index.get_loc(common_dates[t])
                except (KeyError, TypeError):
                    continue
                if pos < len(arr):
                    total += 1
                    if not np.isfinite(arr[pos]):
                        nan_count += 1
        if total > 0 and nan_count / total < 0.5:
            valid_factor_names.append(fname)

    if valid_factor_names:
        dropped = set(factor_names) - set(valid_factor_names)
        if dropped:
            print(f"      [Ridge] 排除 {len(dropped)} 个高 NaN 因子: {', '.join(sorted(dropped))}")
        factor_names = valid_factor_names
    n_factors = len(factor_names)
    if n_factors <= 1:
        fallback = {f: 1.0 / n_factors for f in factor_names} if n_factors > 0 else {}
        return fallback

    # 构建训练数据
    X_list: list[list[float]] = []
    y_list: list[float] = []

    n_dates = len(common_dates)
    start_idx = max(0, n_dates - lookback - 5)

    for t in range(start_idx, n_dates - 5):
        for sym in signal_matrix:
            sig = signal_matrix[sym]
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            try:
                pos = df.index.get_loc(common_dates[t])
            except (KeyError, TypeError):
                continue

            features: list[float] = []
            valid = True
            for fname in factor_names:
                arr = sig.get(fname)
                if arr is None or pos >= len(arr) or not np.isfinite(arr[pos]):
                    valid = False
                    break
                flip = factor_sign_flips.get(fname, 1.0)
                features.append(float(arr[pos]) * flip)
            if not valid:
                continue

            closes = df["close"].values
            if pos + 5 >= len(closes):
                continue
            p_t = closes[pos]
            if not np.isfinite(p_t) or p_t <= 1e-10:
                continue
            ret = (closes[pos + 5] - p_t) / p_t
            if not np.isfinite(ret):
                continue

            X_list.append(features)
            y_list.append(ret)

    min_samples = max(n_factors * 3, 30)
    if len(X_list) < min_samples:
        print(f"      [Ridge] 训练样本不足 ({len(X_list)} < {min_samples})，回退到等权")
        return {f: 1.0 / n_factors for f in factor_names}

    X = np.array(X_list)
    y = np.array(y_list)

    # 标准化特征
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # ── 因子间相关性惩罚 ──
    corr_matrix = np.corrcoef(X_scaled, rowvar=False)
    corr_matrix = np.nan_to_num(corr_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

    corr_threshold = 0.5
    penalty_matrix = np.zeros_like(corr_matrix)
    high_corr_pairs = []
    for i in range(n_factors):
        for j in range(i + 1, n_factors):
            c = abs(corr_matrix[i, j])
            if c > corr_threshold:
                penalty_matrix[i, j] = c
                penalty_matrix[j, i] = c
                high_corr_pairs.append((factor_names[i], factor_names[j], c))

    if high_corr_pairs:
        print(f"      [相关性惩罚] 检测到 {len(high_corr_pairs)} 个高相关因子对 (|corr|>{corr_threshold}):")
        for f1, f2, c in high_corr_pairs[:5]:
            print(f"        - {f1} × {f2}: {c:.3f}")
        if len(high_corr_pairs) > 5:
            print(f"        ... 及其他 {len(high_corr_pairs) - 5} 对")

    penalty_features_list = []
    penalty_targets = []
    for i in range(n_factors):
        for j in range(i + 1, n_factors):
            c = penalty_matrix[i, j]
            if c > 0:
                feat = np.zeros(n_factors)
                feat[i] = np.sqrt(corr_penalty_lambda) * c
                feat[j] = np.sqrt(corr_penalty_lambda) * c
                penalty_features_list.append(feat)
                penalty_targets.append(0.0)

    if penalty_features_list:
        penalty_X = np.array(penalty_features_list)
        penalty_y = np.array(penalty_targets)
        X_augmented = np.vstack([X_scaled, penalty_X])
        y_augmented = np.concatenate([y, penalty_y])
        print(
            f"      [相关性惩罚] 追加 {len(penalty_features_list)} 个惩罚样本，"
            f"训练集从 {len(X_scaled)} 增至 {len(X_augmented)}"
        )
    else:
        X_augmented = X_scaled
        y_augmented = y

    # Ridge 回归
    if alpha is not None:
        from sklearn.linear_model import Ridge

        ridge = Ridge(alpha=alpha, fit_intercept=True)
    else:
        ridge = RidgeCV(
            alphas=[0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 5000.0, 10000.0],
            fit_intercept=True,
        )
    ridge.fit(X_augmented, y_augmented)

    coefs = np.abs(ridge.coef_)
    total = coefs.sum()
    if total < 1e-10:
        return {f: 1.0 / n_factors for f in factor_names}

    weights = {fname: float(coef) / float(total) for fname, coef in zip(factor_names, coefs)}

    # ── 极端相关因子硬删除 ──
    extreme_pairs = [(f1, f2, c) for f1, f2, c in high_corr_pairs if c > extreme_threshold]
    removed_factors: set[str] = set()

    if extreme_pairs:
        print(f"      [硬删除] 检测到 {len(extreme_pairs)} 个极端相关因子对 (|corr|>{extreme_threshold}):")
        for f1, f2, c in extreme_pairs:
            print(f"        {f1} × {f2} = {c:.4f}")

        for f1, f2, c in extreme_pairs:
            w1 = weights.get(f1, 0)
            w2 = weights.get(f2, 0)
            if f1 not in removed_factors and f2 not in removed_factors:
                keep = f1 if w1 >= w2 else f2
                drop = f2 if keep == f1 else f1
                weights[keep] += weights[drop]
                weights[drop] = 0.0
                removed_factors.add(drop)
                print(f"        → 保留 {keep} (w={weights[keep]:.4f}), 剔除 {drop} (w=0)")
            elif f1 in removed_factors and f2 not in removed_factors:
                weights[f2] += weights[f1]
                removed_factors.add(f1)
            elif f2 in removed_factors and f1 not in removed_factors:
                weights[f1] += weights[f2]
                removed_factors.add(f2)

        weights = {k: v for k, v in weights.items() if k not in removed_factors}
        factor_names = [f for f in factor_names if f not in removed_factors]
        n_factors = len(factor_names)
        print(f"      [硬删除] 已剔除 {len(removed_factors)} 个冗余因子, 剩余 {n_factors} 个因子")

    # ── 高相关因子对权重调整 (0.7 < |corr| <= extreme_threshold) ──
    corr_adjusted = 0
    for f1, f2, c in high_corr_pairs:
        if 0.7 < c <= extreme_threshold:
            if f1 in removed_factors or f2 in removed_factors:
                continue
            w1 = weights.get(f1, 0)
            w2 = weights.get(f2, 0)
            if w1 + w2 > 0.01:
                keep_factor = f1 if w1 >= w2 else f2
                drop_factor = f2 if keep_factor == f1 else f1
                shift_amount = weights.get(drop_factor, 0.0) * 0.5
                weights[keep_factor] = weights.get(keep_factor, 0.0) + shift_amount
                weights[drop_factor] = weights.get(drop_factor, 0.0) - shift_amount
                corr_adjusted += 1

    if corr_adjusted > 0:
        print(f"      [相关性调整] 对 {corr_adjusted} 个高相关因子对进行权重转移")

    total_w = sum(w for w in weights.values() if w > 0)
    if total_w > 0:
        weights = {k: max(0.0, v) / total_w for k, v in weights.items()}

    # 输出权重分布
    w_sorted = sorted(weights.items(), key=lambda x: -x[1])
    top3_str = ", ".join(f"{n}({w:.3f})" for n, w in w_sorted[:3])
    bottom3_str = ", ".join(f"{n}({w:.3f})" for n, w in w_sorted[-3:])
    alpha_used = ridge.alpha_ if hasattr(ridge, "alpha_") else alpha
    print(f"      Ridge α={alpha_used:.2f} | 权重 Top3: {top3_str} | Bottom3: {bottom3_str}")

    return weights


# ─── 加权合成 ────────────────────────────────────────────────


def compute_composite_scores(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    factor_sign_flips: dict[str, float],
    factors: list[dict[str, Any]],
    factor_weights: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """合成因子信号（带方向校正 + 可选 Ridge 权重）。

    Args:
        signal_matrix: 信号矩阵 {symbol: {factor_name: array}}
        factor_sign_flips: 方向校正 {+1=正常, -1=需反转}
        factors: 因子数据列表
        factor_weights: 因子权重（Ridge 学习结果），None 则等权

    Returns:
        sym_scores: 标的 → 综合得分
        sym_details: 标的 → {因子名 → 信号值}
    """
    n_factors = len(factors)
    default_weight = 1.0 / n_factors if n_factors > 0 else 0.0

    sym_scores: dict[str, float] = {}
    sym_details: dict[str, dict[str, float]] = {}

    for sym, sym_signals in signal_matrix.items():
        signal_sum = 0.0
        weight_sum = 0.0
        details: dict[str, float] = {}

        for factor_data in factors:
            name = factor_data.get("name", "?")
            sig = sym_signals.get(name)
            if sig is None or len(sig) == 0:
                continue
            val = float(sig[-1]) if np.isfinite(sig[-1]) else 0.0
            # 方向校正
            flip = factor_sign_flips.get(name, 1.0)
            val *= flip
            w = factor_weights.get(name, default_weight) if factor_weights else default_weight
            signal_sum += val * w
            weight_sum += w
            details[name] = val

        if weight_sum > 0:
            composite = signal_sum / weight_sum
            sym_scores[sym] = composite
            sym_details[sym] = details

    return sym_scores, sym_details
