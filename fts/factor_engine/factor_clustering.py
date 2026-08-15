"""
fts/factor_engine/factor_clustering.py — 因子聚类与降维（P1/P2）

P1: 因子聚类 + 代表因子选择 — 系统性降低冗余
    对因子按信号相关性进行层次聚类，从每个簇中选择 Sharpe 最高的代表因子。
    替代 ACTIVE_FACTOR_CAP 的简单排序过滤，保留更多样化的因子来源。

P2: PCA 降维 — 信号源压缩
    对因子信号矩阵进行 PCA，保留解释 95% 方差的主成分，
    将原始因子信号投影到 PCA 空间，输出压缩后的信号权重。

HARNESS §契约优先：本模块的输入/输出契约定义在 contracts.py 中。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ─── 常量 ──────────────────────────────────────────────────

DEFAULT_CLUSTER_THRESHOLD: float = 0.7
"""层次聚类距离阈值（1 - |correlation|）。默认 0.7 等价于 |corr| >= 0.3 为同一簇。"""

DEFAULT_PCA_VARIANCE_RATIO: float = 0.95
"""PCA 保留方差比例。默认 0.95 表示保留解释 95% 方差的主成分。"""

DEFAULT_MIN_CLUSTER_SIZE: int = 1
"""最小簇大小（1 表示允许单因子簇）。"""


# ─── P1: 因子聚类 ─────────────────────────────────────────


class FactorClusteringEngine:
    """因子聚类引擎（P1）— 信号相关性聚类 + 代表因子选择。

    流程:
        1. 使用 FactorExecutor 在参考品种上计算每个因子的信号序列
        2. 计算信号间的 Pearson 相关系数矩阵
        3. 基于相关系数距离 (1 - |corr|) 进行层次聚类
        4. 在指定距离阈值处切割树状图得到簇
        5. 从每个簇中选择 Sharpe 最高的因子作为代表

    示例:
        >>> engine = FactorClusteringEngine()
        >>> reduced = engine.run(factors, panel_data)
        >>> len(reduced) <= len(factors)
        True
    """

    def __init__(
        self,
        cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
        linkage_method: str = "average",
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    ):
        """初始化因子聚类引擎。

        Args:
            cluster_threshold: 层次聚类切割阈值（距离，默认 0.7）。
                较小值产生更多簇、保留更多因子；较大值产生更少簇、合并更多因子。
            linkage_method: 层次聚类链接方法（'average'/'complete'/'single'/'ward'）。
                'average' 对噪声鲁棒，推荐用于因子聚类。
            min_cluster_size: 最小簇大小（默认 1，单因子簇直接保留）。
        """
        self.cluster_threshold = cluster_threshold
        self.linkage_method = linkage_method
        self.min_cluster_size = min_cluster_size

    def compute_signal_correlations(
        self,
        factors: list[dict[str, Any]],
        panel_data: dict[str, Any],
        min_valid_points: int = 10,
        signal_cache: Any = None,
    ) -> tuple[np.ndarray, list[str]]:
        """计算因子对的信号相关系数矩阵。

        Args:
            factors: 因子列表（需含 factor_id, code, params）
            panel_data: {symbol: DataFrame} 市场数据面板
            min_valid_points: 最少有效数据点
            signal_cache: 可选信号缓存（plans/40 A 层），避免与全流程重复重算

        Returns:
            (corr_matrix, factor_ids): 相关系数矩阵和因子 ID 列表
        """
        from .factor_program import FactorExecutor, FactorCompileError

        if not panel_data:
            logger.warning("[P1] 面板数据为空，无法计算信号相关性")
            return np.array([]), []

        # 选择参考品种（第一个可用）
        ref_symbol = next(iter(panel_data))
        ref_df = panel_data[ref_symbol]

        signals: dict[str, np.ndarray] = {}
        errors: list[str] = []
        for f in factors:
            fid = f.get("factor_id", f.get("name", "?"))
            code = f.get("code", "")
            if not code or not isinstance(code, str):
                errors.append(f"{fid}: 代码为空或类型异常")
                continue
            try:
                executor = FactorExecutor(f, signal_cache=signal_cache)
                sig = executor.execute(ref_df, f.get("params", {}))
                if sig is not None and len(sig) > 0 and not np.all(np.isnan(sig)):
                    signals[fid] = sig
                else:
                    errors.append(f"{fid}: 信号为空或全 NaN")
            except (FactorCompileError, Exception) as exc:
                errors.append(f"{fid}: {type(exc).__name__}: {str(exc)[:80]}")

        if errors:
            for e in errors[:5]:
                logger.debug("[P1] 信号计算跳过: %s", e)
            if len(errors) > 5:
                logger.debug("[P1] ... 还有 %d 个错误", len(errors) - 5)

        if len(signals) < 2:
            logger.warning("[P1] 有效信号不足（%d < 2），无法计算相关性", len(signals))
            return np.array([]), []

        fids = list(signals.keys())
        n = len(fids)
        corr_matrix = np.full((n, n), np.nan)
        np.fill_diagonal(corr_matrix, 1.0)

        for i in range(n):
            for j in range(i + 1, n):
                s1, s2 = signals[fids[i]], signals[fids[j]]
                # 对齐长度
                min_len = min(len(s1), len(s2))
                valid = ~(np.isnan(s1[:min_len]) | np.isnan(s2[:min_len]))
                if valid.sum() > min_valid_points:
                    c = float(np.corrcoef(s1[:min_len][valid], s2[:min_len][valid])[0, 1])
                    corr_matrix[i, j] = c
                    corr_matrix[j, i] = c

        return corr_matrix, fids

    def cluster_by_correlation(
        self,
        corr_matrix: np.ndarray,
        factor_ids: list[str],
    ) -> list[list[int]]:
        """基于相关系数距离进行层次聚类。

        Args:
            corr_matrix: 相关系数矩阵 (n×n)
            factor_ids: 因子 ID 列表（仅用于日志）

        Returns:
            clusters: [[idx_in_fids, ...], ...] 每个簇的因子索引列表
        """
        from scipy.cluster.hierarchy import fcluster, linkage

        n = len(factor_ids)
        if n < 2:
            return [[i] for i in range(n)]

        # 距离 = 1 - |correlation|
        distance = np.clip(1.0 - np.abs(corr_matrix), 0.0, 1.0)

        # 取上三角非对角线元素作为距离向量
        condensed = distance[np.triu_indices(n, k=1)]
        # 处理 NaN（将 NaN 视为最大距离 1.0）
        condensed = np.nan_to_num(condensed, nan=1.0)

        try:
            Z = linkage(condensed, method=self.linkage_method)
            labels = fcluster(Z, t=self.cluster_threshold, criterion="distance")
        except Exception as e:
            logger.warning("[P1] 层次聚类失败 (%s)，回退到单因子簇", e)
            return [[i] for i in range(n)]

        # 按标签分组
        clusters: dict[int, list[int]] = {}
        for i, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(i)

        result = sorted(clusters.values(), key=len, reverse=True)
        logger.info(
            "[P1] 层次聚类完成: %d 个因子 → %d 个簇 (threshold=%.2f, method=%s)",
            n,
            len(result),
            self.cluster_threshold,
            self.linkage_method,
        )
        return result

    def select_representative_factors(
        self,
        factors: list[dict[str, Any]],
        clusters: list[list[int]],
        factor_ids: list[str],
        score_map: dict[str, float] | None = None,
        cluster_top_n: int = 1,
        corr_matrix: np.ndarray | None = None,
    ) -> list[dict[str, Any]]:
        """从每个簇中选择代表因子。

        Args:
            factors: 原始因子列表
            clusters: 簇索引列表
            factor_ids: 因子 ID 列表（与 clusters 中的索引对应）
            score_map: 因子综合评分 {factor_id: score}（plans/36 改进项 2，
                替代裸 Sharpe 排序；None=回退 abs(sharpe)）
            cluster_top_n: 簇内保留代表数上限（plans/36 改进项 4，默认 1=每簇仅取最优）
            corr_matrix: 因子信号相关矩阵（compute_signal_correlations 输出，
                与 factor_ids 对齐）；cluster_top_n>1 时用于簇内代表互相关约束
                （与已选代表 |corr| < 0.5 才允许多保留，防高相关重复暴露）

        Returns:
            代表因子列表
        """
        # 构建 fid → factor 映射
        fid_to_factor: dict[str, dict[str, Any]] = {}
        for f in factors:
            fid = f.get("factor_id", f.get("name", "?"))
            fid_to_factor[fid] = f
        # 预建 fid → 相关矩阵索引
        fid_to_pos: dict[str, int] = {fid: i for i, fid in enumerate(factor_ids)}

        selected: list[dict[str, Any]] = []
        cluster_info: list[str] = []

        for cluster_idx, cluster in enumerate(clusters):
            if len(cluster) == 1:
                # 单因子簇直接保留
                fid = factor_ids[cluster[0]]
                factor = fid_to_factor.get(fid)
                if factor:
                    selected.append(factor)
                    cluster_info.append(f"  簇{cluster_idx}: [{factor.get('name', fid)}] (单因子簇)")
                continue

            # 多因子簇：按综合评分（回退 Sharpe）降序排列
            cluster_factors = []
            for idx in cluster:
                fid = factor_ids[idx]
                factor = fid_to_factor.get(fid)
                if factor:
                    cluster_factors.append(factor)

            if not cluster_factors:
                continue

            def _sort_key(f: dict[str, Any]) -> float:
                if score_map is not None:
                    return float(score_map.get(f.get("factor_id", f.get("name", "?")), 0.0))
                return abs(f.get("sharpe", 0.0))

            cluster_factors.sort(key=_sort_key, reverse=True)

            # 簇内保留 cluster_top_n 个代表；top_n>1 时要求与已选代表 |corr|<0.5
            kept: list[dict[str, Any]] = []
            for cand in cluster_factors:
                if len(kept) >= cluster_top_n:
                    break
                if kept and corr_matrix is not None:
                    c_pos = fid_to_pos.get(cand.get("factor_id", ""))
                    if c_pos is None:
                        continue
                    if any(
                        abs(corr_matrix[fid_to_pos[s.get("factor_id", "")]][c_pos]) >= 0.5
                        for s in kept
                        if s.get("factor_id", "") in fid_to_pos
                    ):
                        continue
                kept.append(cand)
            selected.extend(kept)

            # 日志
            names = [f.get("name", f.get("factor_id", "?")) for f in cluster_factors]
            sharpes = [f.get("sharpe", 0.0) for f in cluster_factors]
            sort_label = "score" if score_map is not None else "sharpe"
            kept_names = ", ".join(k.get("name", "?") for k in kept)
            cluster_info.append(
                f"  簇{cluster_idx}: [{', '.join(f'{n}(s={s:.2f})' for n, s in zip(names, sharpes))}]"
                f" → {kept_names} ({sort_label} 排序)"
            )

        # 日志
        removed_count = len(factors) - len(selected)
        logger.info(
            "[P1] 代表因子选择完成: %d → %d 因子 (移除 %d 个冗余)",
            len(factors),
            len(selected),
            removed_count,
        )
        for info in cluster_info:
            logger.info("[P1] %s", info)

        return selected

    def run(
        self,
        factors: list[dict[str, Any]],
        panel_data: dict[str, Any] | None = None,
        score_map: dict[str, float] | None = None,
        cluster_top_n: int = 1,
        signal_cache: Any = None,
    ) -> list[dict[str, Any]]:
        """执行完整的 P1 因子聚类流程。

        Args:
            factors: 待聚类的因子列表
            panel_data: 面板数据（用于计算信号相关性）。为 None 时跳过聚类，直接返回。
            score_map: 因子综合评分 {factor_id: score}（plans/36 改进项 2；
                None=回退 abs(sharpe) 选代表）
            cluster_top_n: 簇内保留代表数上限（plans/36 改进项 4，默认 1）
            signal_cache: 可选信号缓存（plans/40 A 层），避免与全流程重复重算

        Returns:
            聚类后的代表因子列表
        """
        if not factors:
            return factors

        if panel_data is None:
            logger.info("[P1] 无面板数据，跳过因子聚类（保留全部 %d 个因子）", len(factors))
            return factors

        if len(factors) < 3:
            logger.info("[P1] 因子数 %d < 3，跳过聚类", len(factors))
            return factors

        # Step 1: 计算信号相关性
        corr_matrix, fids = self.compute_signal_correlations(factors, panel_data)
        if corr_matrix.size == 0 or len(fids) < 2:
            logger.warning("[P1] 相关性计算失败，跳过聚类（保留全部 %d 个因子）", len(factors))
            return factors

        # Step 2: 层次聚类
        clusters = self.cluster_by_correlation(corr_matrix, fids)

        # Step 3: 选择代表因子（综合评分 + 簇内 top-N）
        selected = self.select_representative_factors(
            factors,
            clusters,
            fids,
            score_map=score_map,
            cluster_top_n=cluster_top_n,
            corr_matrix=corr_matrix,
        )

        return selected


# ─── P2: PCA 降维 ─────────────────────────────────────────


class PCASignalCompressor:
    """PCA 信号降维压缩器（P2）— 信号源压缩。

    将因子信号矩阵通过 PCA 压缩为少量主成分，
    保留原始因子信号中 95% 以上的方差信息。

    流程:
        1. 计算每个因子在参考品种上的信号序列
        2. 构建信号矩阵 (n_dates × n_factors)
        3. 标准化（z-score）
        4. PCA 拟合，保留解释 variance_ratio 方差的主成分
        5. 输出主成分权重（通过因子载荷矩阵推算）

    示例:
        >>> compressor = PCASignalCompressor()
        >>> result = compressor.run(factors, panel_data)
        >>> len(result["pca_signals"]) <= len(factors)
        True
    """

    def __init__(
        self,
        variance_ratio: float = DEFAULT_PCA_VARIANCE_RATIO,
        max_components: int = 10,
    ):
        """初始化 PCA 信号压缩器。

        Args:
            variance_ratio: 保留方差比例（默认 0.95）
            max_components: 最大主成分数（默认 10，防止高维数据过拟合）
        """
        self.variance_ratio = variance_ratio
        self.max_components = max_components

    def compute_signal_matrix(
        self,
        factors: list[dict[str, Any]],
        panel_data: dict[str, Any],
        min_valid_points: int = 10,
        signal_cache: Any = None,
    ) -> tuple[np.ndarray, list[str], np.ndarray]:
        """计算因子信号矩阵。

        Args:
            factors: 因子列表
            panel_data: {symbol: DataFrame} 市场数据
            min_valid_points: 最少有效数据点
            signal_cache: 可选信号缓存（plans/40 A 层），避免与全流程重复重算

        Returns:
            (signal_matrix, factor_ids, dates):
                signal_matrix: (n_dates, n_factors) 信号矩阵
                factor_ids: 因子 ID 列表
                dates: 日期索引
        """
        from .factor_program import FactorExecutor

        if not panel_data:
            logger.warning("[P2] 面板数据为空，无法计算信号矩阵")
            return np.array([]), [], np.array([])

        ref_symbol = next(iter(panel_data))
        ref_df = panel_data[ref_symbol]

        signals: dict[str, np.ndarray] = {}
        for f in factors:
            fid = f.get("factor_id", f.get("name", "?"))
            code = f.get("code", "")
            if not code:
                continue
            try:
                executor = FactorExecutor(f, signal_cache=signal_cache)
                sig = executor.execute(ref_df, f.get("params", {}))
                if sig is not None and len(sig) > 0 and not np.all(np.isnan(sig)):
                    signals[fid] = sig
            except Exception:
                continue

        if len(signals) < 2:
            logger.warning("[P2] 有效信号不足（%d < 2）", len(signals))
            return np.array([]), [], np.array([])

        # 对齐所有信号到相同长度
        fids = list(signals.keys())
        min_len = min(len(s) for s in signals.values())
        if min_len < 10:
            logger.warning("[P2] 信号长度不足（%d < 10）", min_len)
            return np.array([]), [], np.array([])

        matrix = np.full((min_len, len(fids)), np.nan)
        for j, fid in enumerate(fids):
            matrix[:, j] = signals[fid][:min_len]

        # 提取日期索引
        dates = np.array([])
        if hasattr(ref_df, "index"):
            dates = np.array(ref_df.index[:min_len])

        return matrix, fids, dates

    def run(
        self,
        factors: list[dict[str, Any]],
        panel_data: dict[str, Any] | None = None,
        signal_cache: Any = None,
    ) -> dict[str, Any]:
        """执行完整的 P2 PCA 降维流程。

        Args:
            factors: 因子列表
            panel_data: 面板数据（用于计算信号矩阵）。为 None 时跳过 PCA。
            signal_cache: 可选信号缓存（plans/40 A 层），避免与全流程重复重算

        Returns:
            dict:
                - pca_applied: bool 是否成功应用 PCA
                - n_components: int 主成分数
                - explained_variance_ratio: float 解释方差比例
                - pca_signals: list[dict] PCA 压缩后的信号列表
                - factor_loadings: dict 因子载荷矩阵
                - n_original: int 原始因子数
        """
        result: dict[str, Any] = {
            "pca_applied": False,
            "n_components": 0,
            "explained_variance_ratio": 0.0,
            "pca_signals": [],
            "factor_loadings": {},
            "n_original": len(factors),
        }

        if not factors or panel_data is None:
            logger.info("[P2] 无面板数据，跳过 PCA 降维")
            return result

        if len(factors) < 3:
            logger.info("[P2] 因子数 %d < 3，跳过 PCA 降维", len(factors))
            return result

        # Step 1: 计算信号矩阵
        signal_matrix, fids, _ = self.compute_signal_matrix(factors, panel_data)
        if signal_matrix.size == 0 or len(fids) < 3:
            logger.warning("[P2] 信号矩阵构建失败，跳过 PCA")
            return result

        n_dates, n_factors = signal_matrix.shape

        # Step 2: 标准化（z-score）
        means = np.nanmean(signal_matrix, axis=0)
        stds = np.nanstd(signal_matrix, axis=0) + 1e-10
        X_scaled = (signal_matrix - means) / stds

        # 处理 NaN
        X_scaled = np.nan_to_num(X_scaled, nan=0.0)

        # Step 3: PCA 拟合
        try:
            from sklearn.decomposition import PCA
        except ImportError:
            logger.warning("[P2] scikit-learn 未安装，跳过 PCA")
            return result

        n_components = min(n_factors, self.max_components)

        pca = PCA(n_components=n_components)
        pca.fit(X_scaled)

        # 确定保留的主成分数（解释方差 >= variance_ratio）
        cumsum = np.cumsum(pca.explained_variance_ratio_)
        n_keep = int(np.searchsorted(cumsum, self.variance_ratio) + 1)
        n_keep = min(n_keep, n_components)

        if n_keep < 1:
            n_keep = 1

        explained = float(np.sum(pca.explained_variance_ratio_[:n_keep]))

        logger.info(
            "[P2] PCA 完成: %d 因子 → %d 主成分 (解释方差=%.1f%%)",
            n_factors,
            n_keep,
            explained * 100,
        )

        # Step 4: 构建 PCA 信号（通过载荷矩阵映射回因子权重）
        # 载荷矩阵: (n_factors, n_keep) — 每个因子对每个主成分的贡献
        loadings = pca.components_[:n_keep].T  # (n_factors, n_keep)

        # 每个因子的 PCA 权重 = 各主成分载荷的绝对值加权和
        # 权重 = Σ|载荷_j| * 解释方差_j / 总解释方差
        ev_ratios = pca.explained_variance_ratio_[:n_keep]
        ev_total = ev_ratios.sum()
        if ev_total > 0:
            weights = np.sum(np.abs(loadings) * ev_ratios, axis=1) / ev_total
        else:
            weights = np.ones(n_factors) / n_factors

        # 归一化
        w_sum = weights.sum()
        if w_sum > 0:
            weights = weights / w_sum

        # 构建 PCA 信号
        pca_signals = []
        fid_to_factor = {f.get("factor_id", f.get("name", "?")): f for f in factors}

        for j, fid in enumerate(fids):
            f = fid_to_factor.get(fid)
            if f is None:
                continue
            pca_signals.append(
                {
                    "factor_id": fid,
                    "name": f.get("name", fid),
                    "weight": float(weights[j]),
                    "sharpe": f.get("sharpe", 0.0),
                    "ic": f.get("ic", 0.0),
                    "turnover": f.get("turnover", 0.0),
                    "decay_6m": f.get("decay_6m", 0.0),
                    "orthogonalized": True,  # PCA 主成分天然正交
                    "retained": weights[j] > 0.001,
                    "pca_loading": float(np.sum(np.abs(loadings[j]))),
                    "pca_component": int(np.argmax(np.abs(loadings[j]))),
                }
            )

        # 构建因子载荷映射
        factor_loadings = {}
        for j, fid in enumerate(fids):
            factor_loadings[fid] = {f"pc_{k}": float(loadings[j, k]) for k in range(n_keep)}

        result["pca_applied"] = True
        result["n_components"] = n_keep
        result["explained_variance_ratio"] = round(explained, 4)
        result["pca_signals"] = pca_signals
        result["factor_loadings"] = factor_loadings

        return result


# ─── 工具函数 ─────────────────────────────────────────────


def compute_cluster_summary(
    factors: list[dict[str, Any]],
    reduced_factors: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算聚类缩减摘要。

    Args:
        factors: 原始因子列表
        reduced_factors: 聚类后的代表因子列表

    Returns:
        dict: 缩减摘要
    """
    return {
        "n_original": len(factors),
        "n_reduced": len(reduced_factors),
        "reduction_ratio": round(1.0 - len(reduced_factors) / len(factors), 4) if factors else 0.0,
        "removed_count": len(factors) - len(reduced_factors),
    }


def cluster_factors_by_signal(
    code_factors: list[dict[str, Any]],
    *,
    ref_symbols: tuple[str, ...] = ("RB0", "CU0", "IF0"),
    days: int = 500,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> dict[str, Any] | None:
    """基于信号相关性对因子做层次聚类（全流程统一分组入口）。

    UI 因子列表分组、CLI factor stats / list、repository 多样性配额均调用本函数，
    保证"按信号相关性聚类分组"在 FTS 全流程语义一致。

    Args:
        code_factors: 含 factor_id/code/params 的因子列表
        ref_symbols: 参考品种优先序（因子代码列依赖差异大，多品种提高信号可计算率）
        days: 参考品种回溯天数
        cluster_threshold: 层次聚类距离阈值（1 - |corr|），
            默认 0.7 等价于 |corr| >= 0.3 视为同一簇

    Returns:
        dict:
            - assign: {factor_id: cluster_id}
            - cluster_order: 按成员数降序的簇 ID 列表
            - cluster_members: {cluster_id: [factor_id, ...]}
        信号不足 / 数据源不可用 / 异常时返回 None（调用方降级为不分组）。
    """
    import numpy as _np

    if len(code_factors) < 2:
        logger.info("[cluster] 因子数 %d < 2，跳过聚类", len(code_factors))
        return None

    try:
        from fts.data import FTSDataProvider
        from fts.factor_engine.factor_program import FactorExecutor
    except Exception as exc:  # noqa: BLE001 — 聚类依赖缺失属非致命
        logger.warning("[cluster] 因子聚类依赖导入失败: %s", exc)
        return None

    provider = FTSDataProvider()
    panels: dict[str, Any] = {}
    for sym in ref_symbols:
        try:
            df = provider.get_futures_ohlcv(sym, days=days, trace_id="factor-cluster")
            if df is not None and len(df) >= 100:
                panels[sym] = df
        except Exception:  # noqa: BLE001
            continue
    if not panels:
        logger.warning("[cluster] 参考品种行情均不可用，降级不分组")
        return None

    # 逐因子按品种优先序执行信号，取首个成功信号
    signals: dict[str, Any] = {}
    for f in code_factors:
        fid = f.get("factor_id") or ""
        code = f.get("code") or ""
        if not fid or not code:
            continue
        prog = {"factor_id": fid, "code": code}
        params = f.get("params") or {}
        for sym, df in panels.items():
            try:
                sig = FactorExecutor(prog).execute(df, params)
                if sig is not None and len(sig) > 0 and not bool(_np.all(_np.isnan(sig))):
                    signals[fid] = sig
                    break
            except Exception:  # noqa: BLE001 — 单因子/单品种失败不阻断聚类
                continue

    if len(signals) < 2:
        logger.warning("[cluster] 有效信号 %d < 2，降级不分组", len(signals))
        return None

    # 信号两两 Pearson 相关矩阵（缺失对留 NaN，聚类按最大距离处理）
    fids = list(signals.keys())
    n = len(fids)
    corr = _np.full((n, n), _np.nan)
    _np.fill_diagonal(corr, 1.0)
    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = signals[fids[i]], signals[fids[j]]
            m = min(len(s1), len(s2))
            valid = ~(_np.isnan(s1[:m]) | _np.isnan(s2[:m]))
            if valid.sum() > 10:
                c = float(_np.corrcoef(s1[:m][valid], s2[:m][valid])[0, 1])
                corr[i, j] = c
                corr[j, i] = c

    engine = FactorClusteringEngine(cluster_threshold=cluster_threshold, linkage_method="average")
    clusters = engine.cluster_by_correlation(corr, fids)

    assign: dict[str, int] = {}
    cluster_members: dict[int, list[str]] = {}
    for cid, idxs in enumerate(clusters):
        members = [fids[i] for i in idxs]
        cluster_members[cid] = members
        for mfid in members:
            assign[mfid] = cid
    # 无信号因子各自独立成簇（保证全量因子均有归属）
    next_cid = len(cluster_members)
    for f in code_factors:
        fid = f.get("factor_id") or ""
        if fid and fid not in assign:
            assign[fid] = next_cid
            cluster_members[next_cid] = [fid]
            next_cid += 1

    cluster_order = sorted(cluster_members.keys(), key=lambda c: -len(cluster_members[c]))
    logger.info(
        "[cluster] 因子聚类完成: %d 因子 → %d 簇 (可算信号 %d)",
        len(code_factors),
        len(cluster_members),
        len(signals),
    )
    return {"assign": assign, "cluster_order": cluster_order, "cluster_members": cluster_members}


def compute_pca_summary(pca_result: dict[str, Any]) -> dict[str, Any]:
    """计算 PCA 降维摘要。

    Args:
        pca_result: PCASignalCompressor.run() 的输出

    Returns:
        dict: 降维摘要
    """
    n_original = pca_result.get("n_original", 0)
    n_components = pca_result.get("n_components", 0)
    return {
        "pca_applied": pca_result.get("pca_applied", False),
        "n_original": n_original,
        "n_components": n_components,
        "compression_ratio": round(1.0 - n_components / n_original, 4) if n_original > 0 else 0.0,
        "explained_variance_ratio": pca_result.get("explained_variance_ratio", 0.0),
    }


__all__ = [
    "FactorClusteringEngine",
    "PCASignalCompressor",
    "compute_cluster_summary",
    "compute_pca_summary",
    "cluster_factors_by_signal",
    "DEFAULT_CLUSTER_THRESHOLD",
    "DEFAULT_PCA_VARIANCE_RATIO",
]
