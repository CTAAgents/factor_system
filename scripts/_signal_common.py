"""
scripts/_signal_common.py — 信号管道公共模块（GAP-S04）

共享函数（从 futures_signal_pipeline.py 提取，供期货信号管道复用）：
    - _compute_factor_sign_flips: 截面 IC 方向校正
    - _compute_ridge_weights: Ridge 回归权重学习（含相关性惩罚）
    - _compute_composite_scores: 加权合成（方向校正 + 权重）

用法:
    from _signal_common import _compute_factor_sign_flips, ...

版本: v1.0.0 (GAP-S04)
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
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


# ─── 截面标准化 ──────────────────────────────────────────


def normalize_signal_matrix(
    signal_matrix: dict[str, dict[str, np.ndarray]],
    panel: dict[str, pd.DataFrame],
    common_dates: list[str],
    method: str = "none",
) -> None:
    """对每个因子按交易日做截面标准化（z-score / rank），原地修改 signal_matrix。

    消除因子值非零中心/量纲偏置对加权合成符号的主导影响：
    - zscore: 每交易日截面 (x - mean) / std；std<1e-12 的常数截面置 0（无信息不贡献）
    - rank:   每交易日截面百分比秩映射到 [-1, 1]（等价 Spearman 口径，不改变方向校正符号）
    - none:   不处理（向后兼容，保持原行为）

    Args:
        signal_matrix: 信号矩阵 {symbol: {factor_name: array}}（原位修改）
        panel: 行情面板 {symbol: DataFrame}
        common_dates: 共同交易日列表
        method: "none" / "zscore" / "rank"；非法值抛 ValueError
    """
    if method in (None, "", "none"):
        return
    if method not in ("zscore", "rank"):
        raise ValueError(f"normalize method 非法: {method!r}，可选 none/zscore/rank")

    if not signal_matrix or len(common_dates) == 0:
        return

    syms = list(signal_matrix.keys())
    first_sym = next(iter(signal_matrix))
    factor_names = list(signal_matrix[first_sym].keys())
    dates_index = pd.DatetimeIndex(common_dates)

    for fname in factor_names:
        # 构建 (交易日 × 股票) 截面矩阵
        cs = pd.DataFrame(index=dates_index, columns=syms, dtype=np.float64)
        for sym in syms:
            sig = signal_matrix[sym].get(fname)
            if sig is None:
                continue
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            try:
                pos = df.index.get_indexer(dates_index)
            except (TypeError, ValueError):
                continue
            valid = (pos >= 0) & (pos < len(sig))
            if valid.any():
                cs.loc[dates_index[valid], sym] = np.asarray(sig)[pos[valid]]

        if method == "zscore":
            mu = cs.mean(axis=1)
            sd = cs.std(axis=1, ddof=0).replace(0.0, 1.0)
            normed = cs.sub(mu, axis=0).div(sd, axis=0)
        else:  # rank
            normed = cs.rank(axis=1, pct=True) * 2.0 - 1.0

        # 写回 signal_matrix（NaN -> 0，与合成阶段 isfinite 兜底语义一致）
        for sym in syms:
            sig = signal_matrix[sym].get(fname)
            if sig is None:
                continue
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            try:
                pos = df.index.get_indexer(dates_index)
            except (TypeError, ValueError):
                continue
            valid = (pos >= 0) & (pos < len(sig))
            if not valid.any():
                continue
            vals = normed.loc[dates_index[valid], sym].to_numpy(dtype=np.float64)
            sig_arr = np.asarray(sig)
            sig_arr[pos[valid]] = np.where(np.isfinite(vals), vals, 0.0)

    print(f"      [标准化] 截面 {method} 已应用到 {len(factor_names)} 个因子（{len(common_dates)} 交易日）")


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

    # 因子覆盖率过滤：标的数较多时，因子在不同标的上的执行成功集合必然不同，
    # "全标的交集"几乎恒为空，导致权重静默回退等权（factor_weights={}）。
    # 改为保留覆盖 >= 50% 标的的因子；缺失标的在训练样本级跳过
    # （下方逐样本有效性检查已兜底）。
    factor_coverage: dict[str, int] = {}
    for sym in signal_matrix:
        for fname in signal_matrix[sym]:
            factor_coverage[fname] = factor_coverage.get(fname, 0) + 1
    min_coverage = max(1, math.ceil(len(signal_matrix) * 0.5))
    factor_names = sorted(f for f, c in factor_coverage.items() if c >= min_coverage)
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


# ─── 权重快照（GAP-072，v2.99.0：解绑 L3 与信号管道）────────


def save_weight_snapshot(
    path: Path | str,
    factor_weights: dict[str, float],
    factor_sign_flips: dict[str, float] | None = None,
    per_variety_weights: dict[str, dict[str, float]] | None = None,
    per_variety_ic: dict[str, dict[str, float]] | None = None,
    recomputed_at: str | None = None,
    normalize: str = "none",
    neutralize: str = "none",
    regime: str = "none",
) -> Path:
    """持久化信号管道权重快照（重算日写入，冻结日读取复用）。

    Args:
        path: 快照文件路径
        factor_weights: 全局 Ridge 权重 {factor_name: weight}
        factor_sign_flips: 方向校正 {factor_name: +1/-1}
        per_variety_weights: 品种级权重 {variety: {factor: weight}}（期货可选）
        per_variety_ic: 品种-因子 IC 矩阵 {factor_name: {variety: ic}}（期货可选，
            每周重算日计算并持久化，供冻结日报告复用）
        recomputed_at: 重算日期字符串（None 用今天）
        normalize: 重算日使用的截面标准化方式（none/zscore/rank），
            冻结日读取快照同值应用，保证重算日与冻结日口径一致
        neutralize: 重算日使用的截面中性化方式（none/industry/size/both，D.2），
            冻结日读取快照同值应用，保证口径一致
        regime: 重算日使用的 Regime 自适应方式（none/auto，D.2 偏差 b），
            冻结日读取快照同值应用，保证口径一致
    """
    import json
    from datetime import date as _date

    fp = Path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": "signal_weights_v1",
        "recomputed_at": recomputed_at or _date.today().isoformat(),
        "factor_weights": {k: float(v) for k, v in (factor_weights or {}).items()},
        "factor_sign_flips": {k: float(v) for k, v in (factor_sign_flips or {}).items()},
        "normalize": normalize,
        "neutralize": neutralize,
        "regime": regime,
    }
    if per_variety_weights:
        payload["per_variety_weights"] = per_variety_weights
    if per_variety_ic:
        # 过滤非有限 IC（常数信号 Spearman 未定义 → NaN），保证 JSON 为标准格式
        cleaned_ic: dict[str, dict[str, float]] = {}
        for fname, vics in per_variety_ic.items():
            finite = {v: float(ic) for v, ic in vics.items() if math.isfinite(ic)}
            if finite:
                cleaned_ic[fname] = finite
        if cleaned_ic:
            payload["per_variety_ic"] = cleaned_ic
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def load_weight_snapshot(path: Path | str) -> dict[str, Any] | None:
    """读取信号管道权重快照；缺失/损坏返回 None（触发冷启动重算）。

    Args:
        path: 快照文件路径

    Returns:
        快照 dict（含 factor_weights/factor_sign_flips/per_variety_weights）；
        缺失、损坏或权重为空时返回 None。
    """
    import json

    fp = Path(path)
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not data.get("factor_weights"):
            return None
        return data
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def filter_factors_by_weights(
    factors: list[dict[str, Any]],
    factor_weights: dict[str, float],
) -> list[dict[str, Any]]:
    """过滤因子池：仅保留在冻结权重快照中的因子（新因子等待下次重算进入）。

    Args:
        factors: 全部精英因子列表
        factor_weights: 冻结权重 {factor_name: weight}

    Returns:
        仅含快照内因子的列表（保持输入顺序）
    """
    known = set(factor_weights.keys())
    kept = [f for f in factors if f.get("name") in known]
    dropped = len(factors) - len(kept)
    if dropped:
        print(f"      [权重冻结] 排除 {dropped} 个快照外因子（等待下次重算进入）")
    return kept
