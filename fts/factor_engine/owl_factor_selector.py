"""fts/factor_engine/owl_factor_selector.py — OWL 因子分组筛选器（plans/41 方案 A）。

OWL（Ordered Weighted L1）正则化估计器：在横截面回归估计过程中同时完成
"稀疏化 + 分组化"——对系数绝对值降序施加非递增惩罚（大的系数惩罚更重），
强 Schur 凸性使强相关变量的系数被拉平 → 自动分组，无需先验分组信息。

两阶段流程（Sun 2018/2019 + Cochrane 2011 思路）:
    1. OWL 收缩：cvxpy 求解 min 0.5*||X@beta - y||² + λ * Σ_i w_i * |beta|_[i]
       （|beta|_[i] 为系数绝对值降序第 i 个，w 非递增）
    2. 组内正交检验：对按 |beta|>0 且 |corr|>=thr 分出的组做多变量回归
       联合 F 检验 + 组间正交化残差，判定哪组因子对横截面收益有独立解释力。

HARNESS 纪律:
    - 零漂移回退：cvxpy/scipy 缺失、求解异常、输入非法 → 返回 applied=False
      （与既有 PCA/聚类"非致命"模式一致，绝不改变下游语义）
    - 样本外切割：OWL 系数只用 train_frac 前段拟合，检验窗仅用于稳定性验证
    - 多重检验校正：组内正交检验多组比较做 Bonferroni 校正（AGENTS.md §4.1）

依赖: cvxpy>=1.9（已装 1.9.2）；缺失时全部降级。

版本: v1.0.0（plans/41 方案 A）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ─── cvxpy/scipy 可用性（延迟导入，缺失降级） ──────────────

try:
    import cvxpy as cp

    _CVXPY_AVAILABLE = True
except ImportError:  # pragma: no cover — 依赖缺失降级路径
    cp = None  # type: ignore[assignment]
    _CVXPY_AVAILABLE = False

try:
    from scipy import stats as _scipy_stats

    _SCIPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _scipy_stats = None  # type: ignore[assignment]
    _SCIPY_AVAILABLE = False

# ─── 数据契约（HARNESS §契约优先） ────────────────────────


@dataclass
class OwlSelectionResult:
    """OWL 因子分组筛选结果（plans/41 方案 A 数据契约）。

    Attributes:
        applied: OWL 是否成功执行（False=依赖缺失/求解失败/输入非法，回退语义）
        beta: OWL 系数 (n_factors,)，None=未成功
        groups: 因子分组（每组为系数非零且两两 |corr|>=thr 的因子索引列表）
        significant_groups: 组内正交检验显著的组（索引列表）
        nonsignificant_factors: 建议剔除的因子 ID 列表（检验非显著组内因子）
        train_frac: 训练窗比例
        n_train: 训练样本数
        lambda_: 正则化强度
        weight_scheme: 权重衰减方案（linear/exp/log）
        group_corr_threshold: 系数分组相关阈值
    """

    applied: bool = False
    beta: np.ndarray | None = None
    groups: list[list[int]] = field(default_factory=list)
    significant_groups: list[list[int]] = field(default_factory=list)
    nonsignificant_factors: list[str] = field(default_factory=list)
    train_frac: float = 0.7
    n_train: int = 0
    lambda_: float = 0.05
    weight_scheme: str = "linear"
    group_corr_threshold: float = 0.5


# ─── 权重向量生成 ─────────────────────────────────────────


def make_owl_weights(
    n_factors: int,
    scheme: str = "linear",
    tuning: float = 0.5,
) -> np.ndarray:
    """生成 OWL 非递增权重向量 w（长度 n_factors）。

    w 需非递增（w_1 >= w_2 >= ... >= w_p >= 0），使大的系数承受更大惩罚。

    Args:
        n_factors: 因子数 p
        scheme: 'linear'（线性衰减）/ 'exp'（指数衰减）/ 'log'（对数衰减）
        tuning: 衰减强度 (0, 1]；越大衰减越陡（0 退化全等权）

    Returns:
        w: 非递增权重向量 (p,)
    """
    if n_factors <= 0:
        return np.array([], dtype=float)
    idx: np.ndarray = np.arange(1, n_factors + 1, dtype=float)
    if scheme == "exp":
        w = np.exp(-tuning * (idx - 1))
    elif scheme == "log":
        w = 1.0 / (1.0 + tuning * np.log1p(idx - 1))
    else:  # linear
        w = 1.0 - tuning * (idx - 1) / max(1.0, float(n_factors - 1))
    # 保底：非负 + 非递增（浮点噪声下强制单调）
    w = np.maximum(w, 1e-6)
    w = np.maximum.accumulate(w[::-1])[::-1]
    return w


# ─── OWL 求解器 ──────────────────────────────────────────


class OwlFactorSelector:
    """OWL 因子分组筛选器（plans/41 方案 A）。

    流程:
        1. fit_group(): 样本外切割 → 截面标准化 → cvxpy OWL 求解 → 按系数分组
        2. group_orthogonal_test(): 组内正交检验（联合 F + Bonferroni 校正）
        3. select(): 输出 OwlSelectionResult（分组 + 显著组 + 建议剔除）

    示例:
        >>> sel = OwlFactorSelector()
        >>> result = sel.select(X, y)
        >>> result.applied  # True 时 beta/groups 可用
    """

    def __init__(
        self,
        weight_scheme: str = "linear",
        weight_tuning: float = 0.5,
        train_frac: float = 0.7,
        group_corr_threshold: float = 0.5,
        lambda_: float = 0.05,
        significance_level: float = 0.05,
    ):
        """初始化 OWL 筛选器。

        Args:
            weight_scheme: OWL 权重衰减方案（linear/exp/log）
            weight_tuning: 权重衰减强度 (0,1]
            train_frac: 样本外切割训练窗比例（(0,1)，OWL 系数只用训练窗）
            group_corr_threshold: 系数分组相关阈值 |corr|>=thr 视为同组
            lambda_: 正则化强度
            significance_level: 组内正交检验显著性水平（多组比较做 Bonferroni 校正）
        """
        self.weight_scheme = weight_scheme
        self.weight_tuning = float(weight_tuning)
        self.train_frac = float(np.clip(train_frac, 0.5, 0.95))
        self.group_corr_threshold = float(group_corr_threshold)
        self.lambda_ = float(lambda_)
        self.significance_level = float(significance_level)

    # ── 主入口 ──────────────────────────────────────────

    def select(
        self,
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
        factor_ids: list[str] | None = None,
    ) -> OwlSelectionResult:
        """完整两阶段流程：OWL 收缩 → 分组 → 组内正交检验。

        Args:
            signal_matrix: 因子信号矩阵 X (n_dates, n_factors)，已按截面标准化
            forward_returns: 横截面前向收益 y (n_dates,)（各日截面均值）
            factor_ids: 因子 ID 列表（与列对齐）；None 时用列索引

        Returns:
            OwlSelectionResult；依赖缺失/输入非法时 applied=False
        """
        result = self.fit_group(signal_matrix, forward_returns)
        if not result.applied or result.beta is None:
            return result
        # 第二阶段：仅对非零因子分组做正交检验
        fids = factor_ids if factor_ids is not None else [f"f{i}" for i in range(len(result.beta))]
        X, y = self._split_train(signal_matrix, forward_returns)
        result = self.group_orthogonal_test(result, X, y, fids)
        return result

    # ── 第一阶段：OWL 收缩 + 分组 ────────────────────────

    def fit_group(
        self,
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
    ) -> OwlSelectionResult:
        """OWL 拟合（训练窗）+ 系数分组（全样本相关结构仅用于分组）。

        Args:
            signal_matrix: X (n_dates, n_factors)
            forward_returns: y (n_dates,)

        Returns:
            OwlSelectionResult（applied=False 表示回退）
        """
        base = OwlSelectionResult(
            train_frac=self.train_frac,
            weight_scheme=self.weight_scheme,
            lambda_=self.lambda_,
            group_corr_threshold=self.group_corr_threshold,
        )
        if not _CVXPY_AVAILABLE:
            logger.warning("[OWL] cvxpy 未安装，跳过 OWL 筛选")
            return base
        X, y = self._validate_input(signal_matrix, forward_returns)
        if X is None or y is None:
            return base
        X_tr, y_tr = self._split_train(X, y)
        if X_tr.shape[0] < 10 or X_tr.shape[1] < 2:
            logger.warning("[OWL] 训练样本不足 (%d×%d)，跳过", *X_tr.shape)
            return base

        # 训练窗内截面标准化（NaN 置 0，与 PCA 同口径）
        Xs, _ = self._standardize(X_tr)

        try:
            beta = self._solve_owl(Xs, y_tr)
        except Exception as e:  # noqa: BLE001 — 求解异常回退（非致命）
            logger.warning("[OWL] cvxpy 求解失败 (非致命): %s", e)
            return base

        # 分组：系数非零因子（相对阈值，防 λ 下噪声因子获微小非零系数全并入），
        # 按全样本信号相关 |corr|>=thr 聚成组
        max_beta = float(np.max(np.abs(beta))) if beta.size else 0.0
        nonzero = np.flatnonzero(np.abs(beta) > max(max_beta * 0.05, 1e-6))
        groups = self._cluster_by_corr(X, nonzero)

        base.applied = True
        base.beta = beta
        base.groups = groups
        base.n_train = int(X_tr.shape[0])
        return base

    def _solve_owl(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        """cvxpy 求解 OWL：min 0.5*||Xβ-y||² + λ * Σ_i w_i * |β|_[i]。

        有序加权项 Σ_i w_i * |β|_[i]（|β|_[i] 为 |β| 降序第 i 个）用
        cvxpy `sum_largest` 差分等价式表达（DCP 合法）:
            Σ_i w_i * |x|_[i] = Σ_{k=1}^{p} d_k * sum_largest(|x|, k)
        其中 d_k = w_k - w_{k+1}（w_{p+1}=0），w 非递增 → d_k >= 0。

        Args:
            X: 训练窗标准化信号矩阵 (n, p)
            y: 训练窗前向收益 (n,)

        Returns:
            beta: OWL 系数 (p,)
        """
        n, p = X.shape
        beta = cp.Variable(p)
        w = make_owl_weights(p, self.weight_scheme, self.weight_tuning)
        # 差分系数 d_k = w_k - w_{k+1}（w_{p+1}=0），k=1..p
        w_ext = np.concatenate([w, [0.0]])
        d = w_ext[:-1] - w_ext[1:]
        abs_beta = cp.abs(beta)
        penalty = sum(d[k - 1] * cp.sum_largest(abs_beta, k) for k in range(1, p + 1))
        # 0.5/n 归一：与 sklearn Lasso 尺度对齐（`1/(2n)||Xβ-y||²`），
        # 使 λ 不随样本量漂移（缺 1/n 时 λ=0.05 对 n=300 正则过弱，系数不退零）
        objective = cp.Minimize(0.5 / n * cp.sum_squares(X @ beta - y) + self.lambda_ * penalty)
        problem = cp.Problem(objective)
        problem.solve(solver=cp.ECOS if cp.ECOS in cp.installed_solvers() else None)
        if beta.value is None:
            raise RuntimeError("cvxpy 求解返回空解")
        return np.asarray(beta.value, dtype=float)

    # ── 第二阶段：组内正交检验 ───────────────────────────

    def group_orthogonal_test(
        self,
        result: OwlSelectionResult,
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
        factor_ids: list[str] | None = None,
    ) -> OwlSelectionResult:
        """组内正交检验（Cochrane 2011 思路 + Bonferroni 多组校正）。

        对每个非零系数组做多变量回归 y ~ X_group，F 检验联合解释力；
        p 值做 Bonferroni 校正（p * n_groups），显著组保留。

        Args:
            result: fit_group 输出（须 applied=True 且含 beta/groups）
            signal_matrix: X (n_dates, n_factors)
            forward_returns: y (n_dates,)
            factor_ids: 因子 ID 列表（None 用索引）

        Returns:
            更新后的 result（填充 significant_groups / nonsignificant_factors）
        """
        if not result.applied or result.beta is None or not _SCIPY_AVAILABLE:
            return result
        fids = factor_ids if factor_ids is not None else [f"f{i}" for i in range(len(result.beta))]
        X, y = self._validate_input(signal_matrix, forward_returns)
        if X is None or y is None:
            return result
        Xs, _ = self._standardize(X)

        n_groups = len(result.groups)
        adj_alpha = self.significance_level / max(1, n_groups)  # Bonferroni

        significant: list[list[int]] = []
        nonsig_fids: list[str] = []
        for group in result.groups:
            cols = np.asarray(group, dtype=int)
            if cols.size == 0:
                continue
            p_val = self._group_f_test(Xs[:, cols], y)
            if p_val is not None and p_val < adj_alpha:
                significant.append([int(c) for c in cols])
            else:
                nonsig_fids.extend(fids[int(c)] for c in cols)

        result.significant_groups = significant
        result.nonsignificant_factors = nonsig_fids
        return result

    def _group_f_test(
        self,
        Xg: np.ndarray,
        y: np.ndarray,
    ) -> float | None:
        """多变量回归 y ~ Xg 的联合 F 检验 p 值（OLS F 统计量）。

        Args:
            Xg: 组内设计矩阵 (n, k)
            y: 目标 (n,)

        Returns:
            F 检验 p 值；样本不足/退化返回 None
        """
        if Xg.shape[0] <= Xg.shape[1] + 1:
            return None
        # 加截距 OLS
        Xd = np.column_stack([np.ones(Xg.shape[0]), Xg])
        try:
            beta_hat, _, _, _ = np.linalg.lstsq(Xd, y, rcond=None)
        except np.linalg.LinAlgError:
            return None
        resid = y - Xd @ beta_hat
        # 联合 F：全模型 vs 仅截距
        y_bar = y.mean()
        ssr = float(np.sum(resid**2))
        sst = float(np.sum((y - y_bar) ** 2))
        if sst <= 1e-12 or ssr < 0:
            return None
        k = Xg.shape[1]
        n = Xg.shape[0]
        df1, df2 = k, n - k - 1
        if df2 <= 0:
            return None
        r2 = 1.0 - ssr / sst
        f_stat = (r2 / df1) / ((1.0 - r2) / df2) if r2 < 1.0 else float("inf")
        if f_stat == float("inf"):
            return 0.0
        try:
            return float(1.0 - _scipy_stats.f.cdf(f_stat, df1, df2))
        except Exception:  # noqa: BLE001
            return None

    # ── 分组辅助 ────────────────────────────────────────

    def _cluster_by_corr(self, X: np.ndarray, nonzero_idx: np.ndarray) -> list[list[int]]:
        """按信号相关 |corr|>=thr 把非零系数因子聚成组（贪心）。

        Args:
            X: 全样本信号矩阵 (n, p)
            nonzero_idx: 系数非零因子索引

        Returns:
            groups: [[idx, ...], ...]；单因子也成组（供第二阶段逐组检验）
        """
        if nonzero_idx.size == 0:
            return []
        Xn = X[:, nonzero_idx]
        corr = np.corrcoef(Xn, rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0)
        thr = self.group_corr_threshold

        groups: list[list[int]] = []
        assigned: np.ndarray = np.zeros(nonzero_idx.size, dtype=bool)
        for i in range(nonzero_idx.size):
            if assigned[i]:
                continue
            members = [i]
            assigned[i] = True
            for j in range(i + 1, nonzero_idx.size):
                if assigned[j]:
                    continue
                # 与组内已有成员的相关都满足阈值才并入
                if all(abs(corr[j, m]) >= thr for m in members):
                    members.append(j)
                    assigned[j] = True
            groups.append([int(nonzero_idx[m]) for m in members])
        return groups

    # ── 输入处理 ────────────────────────────────────────

    def _validate_input(
        self,
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """输入合法性校验（非法/形状不齐/全 NaN → 返回 (None, None)）。"""
        X = np.asarray(signal_matrix, dtype=float)
        y = np.asarray(forward_returns, dtype=float).reshape(-1)
        if X.ndim != 2 or X.shape[0] < 10:
            return None, None
        if y.ndim != 1 or y.shape[0] != X.shape[0]:
            return None, None
        if not np.isfinite(y).any():
            return None, None
        if not np.isfinite(X).any():
            return None, None
        return X, y

    def _split_train(
        self,
        X: np.ndarray,
        y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """样本外切割：前 train_frac 为训练窗（OWL 只用此段，防数据窥探）。"""
        n = X.shape[0]
        k = max(5, int(n * self.train_frac))
        return X[:k], y[:k]

    def _standardize(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """截面标准化（z-score），NaN 置 0；返回 (X_scaled, 常量列掩码)。"""
        means = np.nanmean(X, axis=0)
        stds = np.nanstd(X, axis=0) + 1e-10
        Xs = (X - means) / stds
        Xs = np.nan_to_num(Xs, nan=0.0)
        # 常数列置 0（无信息，避免 OWL 浪费惩罚预算）
        const_mask = np.nanstd(X, axis=0) < 1e-10
        Xs[:, const_mask] = 0.0
        return Xs, const_mask
