"""
fts.factor_engine.orthogonal_basis — 多因子正交基底维护（GAP-I206 补充，v2.72.0）。

机构级标准（plans/23 L202）："因子入库即去冗余：新因子与既有 elite 计算相关矩阵，
高相关（>0.9）→ 正交化残差入库或拒绝；维护因子正交基底"。

本模块实现 Gram-Schmidt 正交基底：
- 基底 = 一组两两近似正交的精英因子（按 Sharpe 降序保留上限成员）
- L2 准入时，候选因子对基底逐因子做 OLS 残差（迭代投影），得到与整个基底
  正交的残差信号；质量合格（残差与基底最大相关 < 阈值 且 保留比 > 阈值）
  则以正交化版本入库并注册为新基底成员
- 基底索引持久化到 ``{memory_dir}/orthogonal_basis.json``，供 L2/L3 复用，
  避免 elite 池相关性膨胀与 L3 重复去冗余

用法:
    from fts.factor_engine.orthogonal_basis import OrthogonalBasisManager
    mgr = OrthogonalBasisManager(basis_path="memory/orthogonal_basis.json")
    basis = mgr.load_basis()
    orth = mgr.orthogonalize(
        factor={"factor_id": "f1", "name": "mom", "code": "...", "params": {}},
        candidate_signal=y_signal,
        signal_getter=lambda code, params: exec_signal(code, params),
        sharpe=1.5,
    )
    if orth is not None:
        mgr.register(orth)   # 注册为新基底成员

版本: v0.1.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from fts.core.atomic import atomic_read, atomic_write

logger = logging.getLogger(__name__)

# 基底索引文件名（位于 memory_dir 下）
BASIS_INDEX_FILE = "orthogonal_basis.json"


class OrthogonalBasisManager:
    """Gram-Schmidt 多因子正交基底管理器。

    Args:
        basis_path: 基底索引 JSON 路径（默认 "memory/orthogonal_basis.json"）
        max_size: 基底最大成员数（超出时按 Sharpe 降序淘汰最弱成员）
        min_sharpe: 基底成员最小 Sharpe（低于该值不再入选基底）
        residual_corr_max: 残差与基底最大相关性阈值（低于视为已正交）
        min_retained_ratio: 残差最小保留比（std_residual / std_original）
    """

    def __init__(
        self,
        basis_path: str = "memory/orthogonal_basis.json",
        max_size: int = 10,
        min_sharpe: float = 1.0,
        residual_corr_max: float = 0.3,
        min_retained_ratio: float = 0.3,
    ) -> None:
        self._path = Path(basis_path)
        self._max_size = max(int(max_size), 1)
        self._min_sharpe = float(min_sharpe)
        self._residual_corr_max = float(residual_corr_max)
        self._min_retained_ratio = float(min_retained_ratio)

    # ─── 基底读写 ────────────────────────────────────────

    def load_basis(self) -> list[dict[str, Any]]:
        """加载基底索引。

        Returns:
            基底成员列表（按 Sharpe 降序），无索引时返回空列表。
        """
        data = atomic_read(str(self._path), default=None)
        if not isinstance(data, dict):
            return []
        members = data.get("members", [])
        if not isinstance(members, list):
            return []
        valid = [m for m in members if isinstance(m, dict) and m.get("factor_id")]
        return sorted(valid, key=lambda m: float(m.get("sharpe", 0.0)), reverse=True)

    def save_basis(self, members: list[dict[str, Any]]) -> None:
        """持久化基底索引（原子写）。

        Args:
            members: 基底成员列表
        """
        payload = {
            "version": "0.1.0",
            "members": [
                {
                    "factor_id": m.get("factor_id", ""),
                    "name": m.get("name", ""),
                    "sharpe": float(m.get("sharpe", 0.0)),
                    "orthogonalized": bool(m.get("orthogonalized", False)),
                    "basis_index": int(m.get("basis_index", 0)),
                    "registered_at": m.get("registered_at", ""),
                }
                for m in members
            ],
        }
        atomic_write(str(self._path), payload)

    def register(
        self,
        factor: dict[str, Any],
        basis_index: int = 0,
    ) -> dict[str, Any]:
        """将正交化因子注册为新基底成员。

        按 Sharpe 降序插入，超出 ``max_size`` 时淘汰最弱成员。

        Args:
            factor: 正交化版本因子（含 orthogonalized 元数据）
            basis_index: 基底序号（默认 0 自动分配）

        Returns:
            更新后的基底索引（dict：{"members": [...]}）
        """
        from datetime import datetime, timezone

        members = self.load_basis()
        fid = factor.get("factor_id", "")
        if not fid:
            return {"members": members}
        # 去重：同因子已注册则更新
        members = [m for m in members if m.get("factor_id") != fid]
        entry = {
            "factor_id": fid,
            "name": factor.get("name", ""),
            "sharpe": float(factor.get("sharpe", 0.0)),
            "orthogonalized": bool(factor.get("orthogonalized", False)),
            "basis_index": int(basis_index or len(members)),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        members.append(entry)
        members.sort(
            key=lambda m: (float(m.get("sharpe", 0.0)), m.get("registered_at", "")),
            reverse=True,
        )
        if len(members) > self._max_size:
            members = members[: self._max_size]
        self.save_basis(members)
        return {"members": members}

    def contains(self, factor_id: str) -> bool:
        """判断因子是否已在基底中。

        Args:
            factor_id: 因子 ID

        Returns:
            True 如果在基底中
        """
        return any(m.get("factor_id") == factor_id for m in self.load_basis())

    # ─── Gram-Schmidt 正交化 ─────────────────────────────

    def orthogonalize(
        self,
        factor: dict[str, Any],
        candidate_signal: np.ndarray,
        signal_getter: Callable[[dict[str, Any]], Any],
        sharpe: float = 0.0,
    ) -> Optional[dict[str, Any]]:
        """对候选信号关于正交基底做 Gram-Schmidt 迭代 OLS 残差化。

        依次用基底每个成员的信号对候选信号做一元线性回归并取残差，
        最终残差与整个基底近似正交。质量校验：
        - 残差与基底最大相关 < ``residual_corr_max``
        - 保留比 = std(残差)/std(原始) > ``min_retained_ratio``

        Args:
            factor: 候选因子（dict，含 code/params 等原字段）
            candidate_signal: 候选信号（ndarray）
            signal_getter: 基底成员信号执行器 ``callable(member) -> ndarray``，
                接收基底成员 dict（含 factor_id），从 elite 快照加载并执行
                因子代码；返回 None / 失败时跳过该成员
            sharpe: 候选因子 Sharpe（注册基底成员时使用）

        Returns:
            正交化版本因子 dict（含 orthogonalized 元数据 + orthogonal_signal
            残差快照 + orthogonal_basis 基底成员列表）；无基底或质量不合格
            返回 None。
        """
        basis = self.load_basis()
        if not basis:
            return None

        y = np.asarray(candidate_signal, dtype=float).copy()
        if y.ndim != 1 or len(y) < 20:
            return None

        used_members: list[dict[str, Any]] = []
        used_names: list[str] = []
        for member in basis:
            try:
                sig = signal_getter(member)
            except Exception:  # noqa: BLE001
                logger.debug("[orth-basis] 基底成员信号执行失败: %s", member.get("factor_id"))
                continue
            if not isinstance(sig, np.ndarray) or len(sig) != len(y):
                continue
            x = np.asarray(sig, dtype=float)
            valid = ~(np.isnan(x) | np.isnan(y))
            if int(valid.sum()) < 20:
                continue
            xv = x[valid]
            yv = y[valid]
            if float(np.std(xv)) < 1e-12:
                continue
            # 一元 OLS 残差: residual = yv - (a + b·xv)
            b = float(np.cov(xv, yv)[0, 1] / np.var(xv))
            a = float(np.mean(yv) - b * np.mean(xv))
            y = y.copy()
            y[valid] = yv - (a + b * xv)
            used_members.append(member)
            used_names.append(member.get("name", member.get("factor_id", "?")))

        if not used_members:
            return None

        # 质量校验：残差与基底最大相关
        resid = np.asarray(y, dtype=float)
        max_corr = 0.0
        for member in used_members:
            try:
                sig = signal_getter(member)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(sig, np.ndarray) or len(sig) != len(resid):
                continue
            xs = np.asarray(sig, dtype=float)
            v = ~(np.isnan(xs) | np.isnan(resid))
            if int(v.sum()) < 20:
                continue
            if float(np.std(xs[v])) < 1e-12 or float(np.std(resid[v])) < 1e-12:
                continue
            c = abs(float(np.corrcoef(resid[v], xs[v])[0, 1]))
            max_corr = max(max_corr, c)
        if max_corr > self._residual_corr_max:
            logger.info(
                "[orth-basis] %s 正交化残差仍与基底相关 %.3f > %.2f，残差不合格",
                factor.get("name", "?"),
                max_corr,
                self._residual_corr_max,
            )
            return None

        y0 = np.asarray(candidate_signal, dtype=float)
        v0 = ~np.isnan(y0)
        if int(v0.sum()) < 20 or float(np.std(y0[v0])) < 1e-12:
            return None
        retained_ratio = float(np.std(resid[v0]) / np.std(y0[v0]))
        if retained_ratio < self._min_retained_ratio:
            logger.info(
                "[orth-basis] %s 正交化残差保留比 %.3f < %.2f，独立信息不足",
                factor.get("name", "?"),
                retained_ratio,
                self._min_retained_ratio,
            )
            return None

        # 构造正交化因子
        residual_full = np.full(len(y0), np.nan)
        residual_full[~np.isnan(resid)] = resid[~np.isnan(resid)]
        orth = dict(factor)
        orth["orthogonalized"] = True
        orth["orthogonalized_against"] = ",".join(m.get("factor_id", "") for m in used_members)
        orth["orthogonalized_pearson"] = float(max_corr)
        orth["orthogonalized_basis"] = used_names
        orth["orthogonal_signal"] = [float(v) if np.isfinite(v) else None for v in residual_full]
        orth["sharpe"] = float(sharpe)
        return orth


__all__ = ["OrthogonalBasisManager", "BASIS_INDEX_FILE"]
