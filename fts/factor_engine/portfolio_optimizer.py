"""
fts.factor_engine.portfolio_optimizer — 组合优化器（GAP-F07，v2.60.0）。

机构级组合优化器的轻量落地，提供两种模式：

    1. mean_variance : 均值方差（最大化 μ'w - λ·w'Σw）
    2. risk_parity   : 风险平价（各资产风险贡献度趋于相等）

约束（scipy 可用时全部生效，不可用时降级近似）:
    - 杠杆: Σw <= max_leverage
    - 集中度: 0 <= w_i <= max_weight
    - 换手: Σ|w - prev| <= turnover_cap（相对上一期权重的 L1 换手）
    - VaR: 组合 95% VaR <= var_ceiling（需 returns 历史，可选）

降级路径（scipy 未安装）:
    - risk_parity: 对角近似 w_i ∝ 1/σ_i，再截断集中度 + 归一化杠杆
    - mean_variance: 无约束解析解 w = (Σ+εI)^{-1}μ，再截断 + 归一化
    - 换手约束: 向上一期权重线性收缩

设计原则:
    - 输入校验：协方差必须为方阵；权重数需与资产数一致
    - 数值兜底：奇异协方差加 jitter εI；非法输入抛 ValueError
    - 权重不满足约束时返回带约束的结果（不做静默失败）

用法:
    opt = PortfolioOptimizer(OptimizerConfig(mode="risk_parity"))
    w = opt.optimize(cov, expected_returns=mu, prev_weights=prev, returns=hist)

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 依赖探测
    import scipy.optimize as _sopt  # noqa: F401

    _HAS_SCIPY = True
except ImportError:  # pragma: no cover
    _HAS_SCIPY = False


# ─── 契约 ───────────────────────────────────────────────────


@dataclass
class OptimizerConfig:
    """组合优化器配置（GAP-F07 步骤①：约束参数可配置；GAP-L304 中性化扩展）。"""

    mode: str = "risk_parity"  # "risk_parity" | "mean_variance"
    risk_aversion: float = 1.0  # 均值方差模式的风险厌恶系数 λ
    max_leverage: float = 1.0  # 总杠杆上限（Σw）
    max_weight: float = 0.3  # 单资产权重上限（集中度）
    turnover_cap: Optional[float] = None  # 换手上限（None=不约束）
    turnover_penalty: float = 0.0  # 换手惩罚系数（L1 换手入目标，GAP-L305）
    cost_bps_per_turnover: float = 0.0  # 每单位换手的成本（基点，GAP-L305）
    var_ceiling: Optional[float] = None  # 组合 95% VaR 上限（None=不约束）
    var_confidence: float = 0.95  # VaR 置信度
    neutralization: Optional[str] = None  # 暴露中性化：None | "industry" | "style"（GAP-L304）
    exposure_tolerance: float = 0.05  # 暴露偏离容差（|B'w − target| ≤ tol，GAP-L304）
    capacity: Optional[list[float]] = None  # 单因子持仓市值上限（占日成交额比例，GAP-L305）
    capacity_coef: float = 1.0  # 容量约束系数（市值 ≤ 日均成交额 × coef，GAP-L305）


class PortfolioOptimizer:
    """组合权重优化器（均值方差 / 风险平价）。

    Args:
        config: 优化配置（None 用默认）
    """

    def __init__(self, config: Optional[OptimizerConfig] = None) -> None:
        self._config = config or OptimizerConfig()
        if self._config.mode not in ("risk_parity", "mean_variance"):
            raise ValueError(f"不支持的优化模式: {self._config.mode}")
        self._config = self._config

    @property
    def scipy_available(self) -> bool:
        """scipy 是否可用（决定是否走约束优化）。"""
        return _HAS_SCIPY

    def optimize(
        self,
        cov: np.ndarray,
        expected_returns: Optional[np.ndarray] = None,
        prev_weights: Optional[np.ndarray] = None,
        returns: Optional[np.ndarray] = None,
        exposure_matrix: Optional[np.ndarray] = None,
        target_exposure: Optional[np.ndarray] = None,
        capacity_limits: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """求解组合权重。

        Args:
            cov: 协方差矩阵 (n, n)
            expected_returns: 期望收益向量 (n,)（mean_variance 必需）
            prev_weights: 上一期权重 (n,)（启用换手约束/惩罚时传入）
            returns: 资产历史收益矩阵 (T, n)（启用 VaR 约束时传入）
            exposure_matrix: 因子对暴露维度的暴露矩阵 (n_factors, n_exposures)（GAP-L304，
                启用 neutralization 时传入；scipy 约束路径生效）
            target_exposure: 目标暴露向量 (n_exposures,)（默认全 0 = 中性）
            capacity_limits: 容量权重上限 (n,)（GAP-L305：单因子持仓市值 ≤
                品种日均成交额 × capacity_coef 对应的权重上限；None=不约束）

        Returns:
            权重向量 (n,)，满足杠杆/集中度/换手/VaR/暴露/容量约束。
        """
        cov = np.asarray(cov, dtype=float)
        n = cov.shape[0]
        if cov.shape != (n, n):
            raise ValueError(f"协方差必须为方阵，收到 {cov.shape}")
        if n == 0:
            return np.array([], dtype=float)

        mu = self._prepare_expected_returns(expected_returns, n)
        prev = self._prepare_prev_weights(prev_weights, n)
        exposure = self._prepare_exposure(exposure_matrix, target_exposure, n)
        capacity = self._prepare_capacity(capacity_limits, n)

        if self._config.mode == "risk_parity":
            # 风险平价用 numpy 迭代（收敛稳定，不依赖 scipy），约束经投影 + 降级处理
            w = self._solve_risk_parity(cov, prev, returns, capacity)
        elif _HAS_SCIPY:
            w = self._optimize_scipy(cov, mu, prev, returns, exposure, capacity)
        else:
            logger.warning(
                "[L3-Opt] scipy 未安装，使用 numpy 降级优化（无约束精确解 + 投影）"
            )
            w = self._optimize_numpy(cov, mu, prev, capacity)
            if exposure is not None:
                logger.warning(
                    "[L3-Opt] numpy 降级路径不校验暴露约束（GAP-L304 需 scipy）"
                )

        # 数值兜底：非有限值置 0
        w = np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0)
        return w

    # ─── 风险平价（numpy 迭代求解）────────────────────────

    def _solve_risk_parity(
        self,
        cov: np.ndarray,
        prev: Optional[np.ndarray],
        returns: Optional[np.ndarray],
        capacity: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """风险平价权重：等风险贡献迭代 + 约束投影。

        w_{k+1} = w_k * sqrt(mean(rc) / rc_k)，rc_i = w_i (Σw)_i，
        迭代至各资产风险贡献相等（对角协方差下收敛到 w ∝ 1/σ）。
        """
        n = cov.shape[0]
        w = np.ones(n) / n
        for _ in range(200):
            rc = w * (cov @ w)
            mean_rc = float(np.sum(rc)) / n
            if mean_rc <= 0:
                break
            w_new = w * np.sqrt(mean_rc / np.maximum(rc, 1e-12))
            w_new = w_new / max(float(np.sum(w_new)), 1e-12)
            if float(np.max(np.abs(w_new - w))) < 1e-10:
                w = w_new
                break
            w = w_new

        w = self._project(w, capacity)

        # 换手约束（降级近似）：向上一期权重线性收缩
        if prev is not None and self._config.turnover_cap is not None:
            turnover = float(np.sum(np.abs(w - prev)))
            cap = self._config.turnover_cap
            if turnover > cap and turnover > 0:
                alpha = min(1.0, cap / turnover)
                w = alpha * w + (1.0 - alpha) * prev

        # VaR 约束（降级近似）：向等权方向二分收缩直至组合 VaR 达标
        if returns is not None and self._config.var_ceiling is not None:
            w = self._enforce_var(w, returns)
            if capacity is not None:
                w = np.minimum(w, np.asarray(capacity, dtype=float))

        return w

    def _enforce_var(self, w: np.ndarray, returns: np.ndarray) -> np.ndarray:
        """二分收缩满足 VaR 约束（降级近似，向等权混合直至达标）。"""
        ceiling = self._config.var_ceiling
        z = self._z_score(self._config.var_confidence)
        w_eq = np.full_like(w, 1.0 / len(w))
        lo, hi = 0.0, 1.0  # alpha: 保留原权重的比例
        for _ in range(30):
            alpha = (lo + hi) / 2.0
            trial = alpha * w + (1.0 - alpha) * w_eq
            pr = np.asarray(returns, dtype=float) @ trial
            var = (
                -float(np.mean(pr)) - z * float(np.std(pr, ddof=1))
                if len(pr) > 1
                else 0.0
            )
            if var <= ceiling:
                lo = alpha
            else:
                hi = alpha
        return lo * w + (1.0 - lo) * w_eq

    # ─── scipy 约束优化 ───────────────────────────────────

    def _optimize_scipy(
        self,
        cov: np.ndarray,
        mu: np.ndarray,
        prev: Optional[np.ndarray],
        returns: Optional[np.ndarray],
        exposure: Optional[tuple[np.ndarray, np.ndarray]] = None,
        capacity: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """scipy SLSQP 约束优化。

        目标函数（GAP-L305）：max μ'w − λw'Σw − cost(w)
            - 换手惩罚: turnover_penalty × Σ|w − prev|（L1）
            - 成本项: cost_bps_per_turnover × Σ|w − prev| / 10000（基点转小数）
        """
        import scipy.optimize as sopt

        n = cov.shape[0]
        x0 = np.ones(n) / n
        if capacity is not None:
            x0 = np.minimum(x0, capacity)
            x0 = x0 / max(float(np.sum(x0)), 1e-12)

        # 目标函数（仅均值方差；风险平价走 numpy 迭代 _solve_risk_parity）
        lam = self._config.risk_aversion
        penalty = self._config.turnover_penalty
        cost_bps = self._config.cost_bps_per_turnover
        cost_coeff = cost_bps / 10000.0

        def objective(w: np.ndarray) -> float:
            base = -float(mu @ w) + lam * float(w @ cov @ w)
            if prev is not None and (penalty > 0 or cost_coeff > 0):
                turnover = float(np.sum(np.abs(w - prev)))
                base += (penalty + cost_coeff) * turnover
            return base

        # 约束
        constraints: list[Any] = [
            # 杠杆: Σw <= max_leverage
            {"type": "ineq", "fun": lambda w: self._config.max_leverage - np.sum(w)},
            # 集中度: w_i <= max_weight
            {"type": "ineq", "fun": lambda w: self._config.max_weight - w},
        ]
        # 容量约束（GAP-L305）: w_i <= capacity_i（收紧集中度上界）
        eff_max = capacity if capacity is not None else None
        if eff_max is not None:
            bounds = [
                (0.0, min(self._config.max_weight, float(lim)))
                for lim in eff_max
            ]
        else:
            bounds = [(0.0, self._config.max_weight)] * n

        if prev is not None and self._config.turnover_cap is not None:
            cap = self._config.turnover_cap
            # 换手: Σ|w - prev| <= cap
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w: cap - np.sum(np.abs(w - prev)),
                }
            )

        if returns is not None and self._config.var_ceiling is not None:
            ceiling = self._config.var_ceiling
            conf = self._config.var_confidence
            z = self._z_score(conf)

            # VaR 约束: -mean(r'w) - z*std(r'w) <= ceiling
            def var_con(w: np.ndarray) -> float:
                pr = np.asarray(returns, dtype=float) @ w
                mean_r = float(np.mean(pr))
                std_r = float(np.std(pr, ddof=1)) if len(pr) > 1 else 0.0
                return ceiling - (-mean_r - z * std_r)

            constraints.append({"type": "ineq", "fun": var_con})

        # 暴露中性化约束（GAP-L304）: |B'w - target| <= tolerance
        if exposure is not None:
            b_mat, target = exposure
            tol = self._config.exposure_tolerance

            def exposure_hi(w: np.ndarray) -> np.ndarray:
                return tol - (b_mat.T @ w - target)

            def exposure_lo(w: np.ndarray) -> np.ndarray:
                return tol + (b_mat.T @ w - target)

            constraints.append({"type": "ineq", "fun": exposure_hi})
            constraints.append({"type": "ineq", "fun": exposure_lo})
            logger.info(
                "[L3-Opt] 暴露中性化约束生效 [dim=%d, tol=%.4f, neutralization=%s]",
                b_mat.shape[1], tol, self._config.neutralization,
            )

        try:
            result = sopt.minimize(
                objective,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-8},
            )
            w = result.x if result.success else self._project(x0)
            logger.info(
                "[L3-Opt] %s 优化完成: success=%s fun=%.6g",
                self._config.mode,
                result.success,
                result.fun,
            )
            return w
        except Exception as e:  # noqa: BLE001
            logger.warning("[L3-Opt] scipy 优化失败 (%s)，降级投影", e)
            return self._project(x0, capacity)

    def _optimize_numpy(
        self,
        cov: np.ndarray,
        mu: np.ndarray,
        prev: Optional[np.ndarray],
        capacity: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """numpy 降级优化（均值方差）：无约束解析解 + 约束投影。"""
        n = cov.shape[0]
        # 均值方差无约束解: w = (Σ+εI)^{-1}μ
        diag = np.diag(np.maximum(np.diag(cov), 1e-12))
        sigma = cov + np.eye(n) * 1e-6 * float(np.mean(diag) + 1e-12)
        try:
            w = np.linalg.solve(sigma, mu)
        except np.linalg.LinAlgError:
            w = np.ones(n)
        w = self._project(w, capacity)
        # 换手约束降级：向上一期权重线性收缩
        if prev is not None and self._config.turnover_cap is not None:
            turnover = float(np.sum(np.abs(w - prev)))
            cap = self._config.turnover_cap
            if turnover > cap and turnover > 0:
                alpha = min(1.0, cap / turnover)
                w = alpha * w + (1.0 - alpha) * prev
        return w

    def _project(self, w: np.ndarray, capacity: Optional[np.ndarray] = None) -> np.ndarray:
        """投影到集中度 + 容量 + 杠杆约束（截断 → 归一化）。"""
        w = np.asarray(w, dtype=float)
        w = np.clip(w, 0.0, self._config.max_weight)
        if capacity is not None:
            w = np.minimum(w, np.asarray(capacity, dtype=float))
        total = float(np.sum(w))
        if total > self._config.max_leverage and total > 0:
            w = w * (self._config.max_leverage / total)
        if total <= 0:
            w = np.full_like(w, self._config.max_leverage / max(len(w), 1))
        return w

    # ─── 内部工具 ─────────────────────────────────────────

    def _prepare_expected_returns(
        self,
        mu: Optional[np.ndarray],
        n: int,
    ) -> np.ndarray:
        if mu is None:
            if self._config.mode == "mean_variance":
                raise ValueError("mean_variance 模式需要提供 expected_returns")
            return np.zeros(n)
        mu = np.asarray(mu, dtype=float).ravel()
        if mu.shape != (n,):
            raise ValueError(f"expected_returns 维度 {mu.shape} 与资产数 {n} 不一致")
        return mu

    def _prepare_prev_weights(
        self,
        prev: Optional[np.ndarray],
        n: int,
    ) -> Optional[np.ndarray]:
        if prev is None:
            return None
        prev = np.asarray(prev, dtype=float).ravel()
        if prev.shape != (n,):
            raise ValueError(f"prev_weights 维度 {prev.shape} 与资产数 {n} 不一致")
        return prev

    def _prepare_exposure(
        self,
        exposure_matrix: Optional[np.ndarray],
        target_exposure: Optional[np.ndarray],
        n: int,
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        """校验暴露矩阵并返回 (B, target)；未启用或未传入返回 None。

        Args:
            exposure_matrix: 暴露矩阵 (n_factors, n_exposures)
            target_exposure: 目标暴露 (n_exposures,)，默认全 0（中性）
            n: 资产（因子）数

        Returns:
            (B, target) 元组；未启用时 None
        """
        if exposure_matrix is None:
            return None
        if self._config.neutralization is None:
            logger.warning(
                "[L3-Opt] 传入 exposure_matrix 但 neutralization=None，忽略暴露约束"
            )
            return None
        b = np.asarray(exposure_matrix, dtype=float)
        if b.ndim != 2 or b.shape[0] != n:
            raise ValueError(f"暴露矩阵形状 {b.shape} 与资产数 {n} 不一致（需 (n, n_exposures)）")
        if target_exposure is None:
            target = np.zeros(b.shape[1])
        else:
            target = np.asarray(target_exposure, dtype=float).ravel()
            if target.shape[0] != b.shape[1]:
                raise ValueError(
                    f"目标暴露长度 {target.shape[0]} != 暴露维度 {b.shape[1]}"
                )
        return b, target

    def _prepare_capacity(
        self,
        capacity_limits: Optional[np.ndarray],
        n: int,
    ) -> Optional[np.ndarray]:
        """校验容量权重上限并返回 (n,) 数组；未启用或未传入返回 None。

        Args:
            capacity_limits: 容量权重上限 (n,)（GAP-L305）
            n: 资产（因子）数

        Returns:
            (n,) 容量数组；未启用时 None
        """
        if capacity_limits is None:
            return None
        c = np.asarray(capacity_limits, dtype=float).ravel()
        if c.shape != (n,):
            raise ValueError(f"容量上限长度 {c.shape[0]} != 资产数 {n}")
        if float(np.nanmax(c)) <= 0:
            return None
        return np.nan_to_num(c, nan=0.0, posinf=0.0, neginf=0.0)

    @staticmethod
    def _z_score(confidence: float) -> float:
        """标准正态分位数近似（避免依赖 scipy.stats）。"""
        # Abramowitz-Stegun 近似
        from math import sqrt

        p = confidence
        t = sqrt(-2.0 * np.log(max(1.0 - p, 1e-12)))
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        return float(
            t
            - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
        )


__all__ = [
    "OptimizerConfig",
    "PortfolioOptimizer",
]
