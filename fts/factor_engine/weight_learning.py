"""
fts.factor_engine.weight_learning — 机构级权重学习增强（v2.74.0）。

在 Elastic Net 截面回归权重之上补齐头部机构的三层处理，学习样本空间
根据目标交易市场自动匹配：

    ① 风险调整权重: Ledoit-Wolf 收缩协方差 → 波动率缩放 / 等风险贡献（风险平价），
       让权重反映"每单位风险的信号贡献"（联动 risk_model.py / factor_returns.py）。
    ② 滚动样本外验证: 滚动窗口 re-fit，报告权重稳定性 / OOS 组合 IC / 权重衰减，
       替代一次性全样本学习。
    ③ 学习样本空间自动匹配: panel_market="auto" 时跟随目标交易市场
       （market=futures → 期货核心面板，market=stock → CSI300 股票面板），
       并输出跨市场迁移 IC 对比验证。

用法（由 portfolio_loop._compute_elastic_net_weights 调用）:
    cfg = WeightLearningConfig()
    resolved = resolve_panel_market(cfg.panel_market, market)  # "auto" → market

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ─────────────────────────────────────────────────


@dataclass
class WeightLearningConfig:
    """机构级权重学习配置。

    Attributes:
        risk_adjust: 风险调整方式 "none" | "volatility_scaling" | "risk_parity"
            （默认 risk_parity：L1 选择 + Ledoit-Wolf 协方差等风险贡献）
        rolling_validation: 是否启用滚动样本外验证（默认 True）
        rolling_windows: 滚动窗口段数（默认 5）
        min_window_dates: 单窗口最小训练样本数，不足跳过该窗口（默认 40）
        panel_market: 学习面板市场 "auto" | "stock" | "futures"
            （默认 auto = 根据目标交易市场自动匹配）
        cross_market_ic: 是否输出跨市场迁移 IC 对比（默认 False = 关闭，避免加载对侧市场替代面板）
    """

    risk_adjust: str = "risk_parity"
    rolling_validation: bool = True
    rolling_windows: int = 5
    min_window_dates: int = 40
    panel_market: str = "auto"
    cross_market_ic: bool = False


# ─── 面板市场解析 ─────────────────────────────────────────


def resolve_panel_market(panel_market: str, market: str) -> str:
    """解析学习面板市场：panel_market="auto" 时跟随目标交易市场。

    Args:
        panel_market: "auto" | "stock" | "futures"
        market: 目标交易市场（PortfolioLoop.market）

    Returns:
        "stock" 或 "futures"（非法值回退 stock）
    """
    if panel_market == "auto":
        return market if market in ("stock", "futures") else "stock"
    if panel_market not in ("stock", "futures"):
        logger.warning("[L3-WEIGHT] 非法 panel_market=%s，回退 stock", panel_market)
        return "stock"
    return panel_market


def alternate_market(panel_market: str) -> str:
    """返回跨市场迁移 IC 对比的对侧市场。"""
    return "futures" if panel_market == "stock" else "stock"


# ─── ① 风险调整权重 ──────────────────────────────────────


def risk_adjust_weights(
    weights: dict[str, float],
    factor_returns: pd.DataFrame,
    mode: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    """在 Elastic Net 系数权重上叠加风险调整（Ledoit-Wolf 收缩协方差）。

    Args:
        weights: {factor_id: weight}（Elastic Net 系数归一化，稀疏）
        factor_returns: 因子收益矩阵 (T × N)，columns 覆盖 weights 的 factor_id
        mode: "none" | "volatility_scaling" | "risk_parity"

    Returns:
        (调整后权重, meta)；任一步骤失败返回 (原权重, {})，由调用方降级。
    """
    if mode == "none" or not weights:
        return dict(weights), {}

    from .risk_model import RiskModelEstimator

    estimator = RiskModelEstimator()
    try:
        result = estimator.estimate(factor_returns)
    except ValueError as e:
        logger.warning("[L3-WEIGHT] 风险模型估计失败，跳过风险调整: %s", e)
        return dict(weights), {}

    cov = result.cov
    fids = [f for f in weights if f in factor_returns.columns]
    if len(fids) < 1:
        logger.warning("[L3-WEIGHT] 因子收益矩阵缺列，跳过风险调整")
        return dict(weights), {}

    w = np.array([weights[f] for f in fids], dtype=float)
    if float(np.sum(np.abs(w))) <= 0:
        return dict(weights), {}

    if mode == "volatility_scaling":
        # w_i ∝ |coef_i| / σ_i：每单位风险的信号贡献
        idx = [list(factor_returns.columns).index(f) for f in fids]
        vol = np.maximum(result.realized_vol[idx], 1e-12)
        w_adj = w / vol
    elif mode == "risk_parity":
        # 等风险贡献（ERC）求解于非零权重因子的收缩协方差，再与系数权重融合
        idx = [list(factor_returns.columns).index(f) for f in fids]
        sub_cov = cov[np.ix_(idx, idx)]
        erc = _risk_parity_weights(sub_cov)
        w_adj = erc * w  # 零系数因子（w=0）保持权重 0，仅对入选因子做风险平价
    else:
        logger.warning("[L3-WEIGHT] 未知 risk_adjust=%s，跳过风险调整", mode)
        return dict(weights), {}

    total = float(np.sum(w_adj))
    if total <= 0 or not np.isfinite(total):
        logger.warning("[L3-WEIGHT] 风险调整权重异常（sum=%s），跳过", total)
        return dict(weights), {}
    w_adj = w_adj / total

    adjusted = {f: float(w_adj[i]) for i, f in enumerate(fids)}
    meta: dict[str, Any] = {
        "method": mode,
        "shrinkage": float(result.shrinkage),
        "condition_number": float(result.condition_number),
        "n_obs": int(result.n_obs),
    }
    logger.info(
        "[L3-WEIGHT] 风险调整完成 [%s]: shrinkage=%.4f cond=%.2f n_obs=%d",
        mode,
        result.shrinkage,
        result.condition_number,
        result.n_obs,
    )
    return adjusted, meta


def risk_adjust_from_panel(
    weights: dict[str, float],
    signal_matrix: np.ndarray,
    forward_returns: np.ndarray,
    dates: Sequence[Any],
    factor_ids: Sequence[str],
    mode: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    """从横截面面板构建因子收益后执行风险调整（① 的完整入口）。"""
    if mode == "none" or not weights:
        return dict(weights), {}

    from .factor_returns import FactorReturnsBuilder

    try:
        builder = FactorReturnsBuilder()
        fr_result = builder.build_from_panel(
            signal_matrix=signal_matrix,
            forward_returns=forward_returns,
            dates=dates,
            factor_ids=factor_ids,
        )
    except (ValueError, Exception) as e:  # noqa: BLE001
        logger.warning("[L3-WEIGHT] 因子收益构建失败，跳过风险调整: %s", e)
        return dict(weights), {}
    return risk_adjust_weights(weights, fr_result.returns, mode)


def _risk_parity_weights(cov: np.ndarray, max_iter: int = 100, tol: float = 1e-8) -> np.ndarray:
    """等风险贡献（ERC）权重 — 循环坐标下降（Maillard, Roncalli, Teiletche 2010）。

    Args:
        cov: 收缩协方差矩阵 (n, n)

    Returns:
        权重向量（和 = 1）
    """
    n = cov.shape[0]
    w = 1.0 / np.sqrt(np.maximum(np.diag(cov), 1e-12))
    w = w / w.sum()
    for _ in range(max_iter):
        sw = cov @ w
        rc_total = float(w @ sw)
        if rc_total <= 0:
            break
        target = rc_total / n
        # 直接更新 w_i = target / (Σw)_i（RC_i = w_i·(Σw)_i = target 的解析解），
        # 与上一轮 0.5 阻尼混合保证收敛（对角协方差下退化为逆波动率）
        w_direct = target / np.maximum(sw, 1e-12)
        w_direct = np.nan_to_num(w_direct, nan=0.0, posinf=0.0, neginf=0.0)
        w_new = 0.5 * w + 0.5 * w_direct
        s = float(w_new.sum())
        if s <= 0 or not np.isfinite(s):
            break
        w_new = w_new / s
        if float(np.max(np.abs(w_new - w))) < tol:
            w = w_new
            break
        w = w_new
    return w


# ─── ② 滚动样本外验证 ────────────────────────────────────


def _fit_elasticnet_coefs(
    X: np.ndarray,
    y: np.ndarray,
    l1_ratio: float = 0.5,
    cv_folds: int = 5,
) -> Optional[np.ndarray]:
    """逐日 Elastic Net 截面回归 → 平均系数向量。

    Args:
        X: 信号矩阵 (T, S, F)
        y: 前向收益矩阵 (T, S)

    Returns:
        平均系数 (F,)；有效回归日不足 5 返回 None
    """
    from sklearn.linear_model import ElasticNetCV

    n_dates, _, n_factors = X.shape
    all_coefs = np.zeros((n_dates, n_factors))
    valid_dates = 0
    for t in range(n_dates):
        Xt = X[t]
        yt = y[t]
        valid = ~np.isnan(Xt).any(axis=1) & ~np.isnan(yt)
        n_valid = int(valid.sum())
        if n_valid < 10:
            continue
        Xv = Xt[valid]
        yv = yt[valid]
        X_mean = Xv.mean(axis=0)
        X_std = Xv.std(axis=0) + 1e-10
        X_scaled = (Xv - X_mean) / X_std
        model = ElasticNetCV(
            l1_ratio=[l1_ratio],
            cv=min(cv_folds, n_valid),
            max_iter=5000,
            random_state=42,
        )
        model.fit(X_scaled, yv)
        all_coefs[t] = model.coef_ / X_std  # 还原到原始尺度
        valid_dates += 1
    if valid_dates < 5:
        return None
    return np.nanmean(all_coefs, axis=0)


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """纯 numpy Spearman 秩相关（并列取平均秩）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")

    def _rankdata(a: np.ndarray) -> np.ndarray:
        sorter = np.argsort(a, kind="stable")
        ranks: np.ndarray = np.empty(n, dtype=float)
        ranks[sorter] = np.arange(1, n + 1)
        _, inverse = np.unique(a, return_inverse=True)
        for u in range(inverse.max() + 1):
            mask = inverse == u
            if int(mask.sum()) > 1:
                ranks[mask] = float(ranks[mask].mean())
        return ranks

    rx = _rankdata(x)
    ry = _rankdata(y)
    denom = float(np.std(rx, ddof=1) * np.std(ry, ddof=1))
    if denom <= 0:
        return float("nan")
    return float(np.cov(rx, ry, ddof=1)[0, 1] / denom)


def _combo_oos_ic(sig: np.ndarray, fwd: np.ndarray, coefs: np.ndarray) -> float:
    """OOS 组合 IC：逐日 Spearman(组合得分, 前向收益) 均值。"""
    n_dates = sig.shape[0]
    ics: list[float] = []
    for t in range(n_dates):
        X = sig[t]
        y = fwd[t]
        valid = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
        if int(valid.sum()) < 10:
            continue
        score = X[valid] @ coefs
        rho = _spearman(score, y[valid])
        if np.isfinite(rho):
            ics.append(float(rho))
    return float(np.mean(ics)) if ics else float("nan")


def rolling_oos_validate(
    signal_matrix: np.ndarray,
    forward_returns: np.ndarray,
    factor_ids: Sequence[str],
    config: WeightLearningConfig,
    l1_ratio: float = 0.5,
    cv_folds: int = 5,
) -> dict[str, Any]:
    """滚动窗口样本外验证（②）。

    时间轴等分为 rolling_windows 段；每段内部 70/30 切分 train/test，
    train 拟合权重 → test 计算 OOS 组合 IC（逐日截面 Spearman 均值）。

    Returns:
        {"n_windows", "oos_ic_mean", "oos_ic_std", "weight_stability",
         "weight_decay"}；样本不足时字段缺省 NaN。
    """
    n_dates = signal_matrix.shape[0]
    bounds = np.linspace(0, n_dates, config.rolling_windows + 1).astype(int)
    window_weights: list[np.ndarray] = []
    oos_ics: list[float] = []

    for k in range(config.rolling_windows):
        t0 = int(bounds[k])
        t1 = int(bounds[k + 1])
        split = t0 + int(0.7 * (t1 - t0))
        if split - t0 < config.min_window_dates or t1 - split < 10:
            continue
        try:
            coefs = _fit_elasticnet_coefs(signal_matrix[t0:split], forward_returns[t0:split], l1_ratio, cv_folds)
        except Exception:  # noqa: BLE001
            continue
        if coefs is None or float(np.max(np.abs(coefs))) <= 0:
            continue
        w = np.abs(coefs)
        w = w / w.sum()
        window_weights.append(w)
        ic = _combo_oos_ic(signal_matrix[split:t1], forward_returns[split:t1], coefs)
        if np.isfinite(ic):
            oos_ics.append(ic)

    meta: dict[str, Any] = {
        "n_windows": len(window_weights),
        "oos_ic_mean": float("nan"),
        "oos_ic_std": float("nan"),
        "weight_stability": float("nan"),
        "weight_decay": float("nan"),
    }
    if oos_ics:
        meta["oos_ic_mean"] = float(np.mean(oos_ics))
        meta["oos_ic_std"] = float(np.std(oos_ics)) if len(oos_ics) > 1 else float("nan")
    if len(window_weights) >= 2:
        corrs = [
            float(np.corrcoef(window_weights[i], window_weights[i + 1])[0, 1]) for i in range(len(window_weights) - 1)
        ]
        meta["weight_stability"] = float(np.nanmean(corrs)) if corrs else float("nan")
        meta["weight_decay"] = float(np.mean(np.abs(window_weights[0] - window_weights[-1])))
    logger.info(
        "[L3-WEIGHT] 滚动样本外验证: 窗口=%d OOS_IC=%.4f(±%.4f) 稳定性=%.4f 衰减=%.4f",
        meta["n_windows"],
        meta["oos_ic_mean"] if np.isfinite(meta["oos_ic_mean"]) else 0.0,
        meta["oos_ic_std"] if np.isfinite(meta["oos_ic_std"]) else 0.0,
        meta["weight_stability"] if np.isfinite(meta["weight_stability"]) else 0.0,
        meta["weight_decay"] if np.isfinite(meta["weight_decay"]) else 0.0,
    )
    return meta


# ─── ③ 跨市场迁移 IC 对比验证 ────────────────────────────


def _factor_cs_ic(signal_matrix: np.ndarray, forward_returns: np.ndarray, j: int) -> float:
    """单因子在面板上的平均截面 IC。"""
    n_dates = signal_matrix.shape[0]
    ics: list[float] = []
    for t in range(n_dates):
        x = signal_matrix[t, :, j]
        y = forward_returns[t]
        valid = ~np.isnan(x) & ~np.isnan(y)
        if int(valid.sum()) < 10:
            continue
        rho = _spearman(x[valid], y[valid])
        if np.isfinite(rho):
            ics.append(float(rho))
    return float(np.mean(ics)) if ics else float("nan")


def _panel_factor_ic(
    panel: dict[str, pd.DataFrame],
    common_dates: Sequence[Any],
    fdata: dict[str, Any],
    horizon: int = 5,
) -> float:
    """单因子在替代面板上的平均截面 IC（执行因子代码 → 逐日 Spearman）。"""
    from .factor_program import FactorExecutor

    try:
        executor = FactorExecutor(fdata)
    except Exception:  # noqa: BLE001
        return float("nan")

    signals: dict[str, np.ndarray] = {}
    closes: dict[str, np.ndarray] = {}
    for sym, df in panel.items():
        try:
            sig = executor.execute(df, fdata.get("params", {}))
        except Exception:  # noqa: BLE001
            continue
        if sig is None or len(sig) == 0:
            continue
        signals[sym] = np.asarray(sig, dtype=float)
        closes[sym] = np.asarray(df["close"].values, dtype=float)

    if len(signals) < 10:
        return float("nan")

    ics: list[float] = []
    for d in common_dates:
        x: list[float] = []
        y: list[float] = []
        for sym, sig in signals.items():
            df = panel[sym]
            if d not in df.index:
                continue
            i = list(df.index).index(d)
            cl = closes[sym]
            if i + horizon >= len(cl):
                continue
            s = sig[i]
            if not np.isfinite(s):
                continue
            fwd = (cl[i + horizon] - cl[i]) / max(float(cl[i]), 1e-10)
            x.append(float(s))
            y.append(fwd)
        if len(x) < 10:
            continue
        rho = _spearman(np.array(x), np.array(y))
        if np.isfinite(rho):
            ics.append(float(rho))
    return float(np.mean(ics)) if ics else float("nan")


def cross_market_ic_check(
    provider: Any,
    factor_codes: dict[str, dict[str, Any]],
    factor_ids: Sequence[str],
    signal_matrix: np.ndarray,
    forward_returns: np.ndarray,
    dates: Sequence[Any],
    panel_market: str,
) -> dict[str, Any]:
    """跨市场迁移 IC 对比验证（③ 的对比部分）。

    在学习面板（primary）上计算各因子截面 IC，并在对侧市场（alternate）面板上
    重新执行因子计算 IC，输出迁移差距与信号相关性汇总。

    Returns:
        {"factor_ic": {fid: {"ic_primary", "ic_alternate"}},
         "migration_gap_mean", "signal_corr_mean"}；替代面板加载失败返回 {}。
    """
    if not factor_codes or not factor_ids:
        return {}

    ic_primary = {fid: _factor_cs_ic(signal_matrix, forward_returns, j) for j, fid in enumerate(factor_ids)}

    alt = alternate_market(panel_market)
    try:
        if alt == "futures":
            from ..data_futures import get_dynamic_core_subset

            panel_alt, dates_alt = provider.get_futures_panel(symbols=get_dynamic_core_subset(), days=500, trace_id="")
        else:
            panel_alt, dates_alt = provider.get_csi300_panel(days=500, max_stocks=0, fundamental=True, trace_id="")
    except Exception as e:  # noqa: BLE001
        logger.warning("[L3-WEIGHT] 替代面板加载失败，跳过跨市场 IC 对比: %s", e)
        return {}

    if not panel_alt or dates_alt is None or len(dates_alt) == 0:
        logger.info("[L3-WEIGHT] 替代面板（%s）为空，跳过跨市场 IC 对比", alt)
        return {}

    factor_ic: dict[str, dict[str, float]] = {}
    gaps: list[float] = []
    for fid in factor_ids:
        fdata = factor_codes.get(fid)
        if not fdata:
            continue
        ic_alt = _panel_factor_ic(panel_alt, dates_alt, fdata)
        ic_p = float(ic_primary.get(fid, float("nan")))
        factor_ic[fid] = {"ic_primary": ic_p, "ic_alternate": ic_alt}
        if np.isfinite(ic_p) and np.isfinite(ic_alt):
            gaps.append(abs(ic_p) - abs(ic_alt))

    meta: dict[str, Any] = {
        "factor_ic": factor_ic,
        "migration_gap_mean": float(np.mean(gaps)) if gaps else float("nan"),
        "n_compared": len(gaps),
    }
    logger.info(
        "[L3-WEIGHT] 跨市场 IC 对比 [%s→%s]: 对比=%d 迁移差距均值=%.4f",
        panel_market,
        alt,
        len(gaps),
        meta["migration_gap_mean"] if np.isfinite(meta["migration_gap_mean"]) else 0.0,
    )
    return meta


__all__ = [
    "WeightLearningConfig",
    "resolve_panel_market",
    "alternate_market",
    "risk_adjust_weights",
    "risk_adjust_from_panel",
    "rolling_oos_validate",
    "cross_market_ic_check",
]
