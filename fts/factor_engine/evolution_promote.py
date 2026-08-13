"""
loop_engine/evolution_promote.py — EliteStore 协作类：精英晋升与持久化

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47c：
B 阶段产物 EvolutionPromoteMixin 组合式重构为 EliteStore 协作类，行为等价、
公开 API 不变。领域独享状态（_repo / _cluster_* / _l2_* / orthogonal_basis /
high_ic_screener / elite_tracker）随迁本类并在构造内装配（原主类 __init__
对应段迁移）；全局上下文（data / market / budget / elite_dir / inject_dir /
factor_db_path / _trace_id）经 owner（主类实例）动态读取，兼容运行时重赋值
（34 §8.3 可变上下文修订）。主类 EvolutionLoop 组合持有本类实例，保留 11
方法转发桩 + 16 属性 property 转发（兼容测试零改动，见 34 §8.5）。

跨域共享模块级符号（_build_shadow_pool / _SHADOW_OBSERVE_TRADING_DAYS /
_log_consistency_event 定义于 evolution_loop.py）经函数体内延迟导入获取，
避免模块级循环导入。

跨组件约束（34 §8.3）：协作类不 import evolution_loop（防循环导入），
owner 仅经 Any 标注，运行时经主类组装注入。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

import numpy as np

from .audit import FactorAuditReport  # noqa: E402 — 延迟导入规避循环依赖
from .contracts import (  # noqa: E402 — 延迟导入规避循环依赖
    FactorCorrelation,
    FactorEvaluation,
    FactorProgram,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    pass

logger = logging.getLogger(__name__)


class EliteStore:
    """领域 C：精英晋升与持久化（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：领域独享状态（_repo/_cluster_*/_l2_*/orthogonal_basis/
    high_ic_screener/elite_tracker）随迁本类并在构造内装配（原主类 __init__
    对应段迁移）；全局上下文（data/market/budget/elite_dir/inject_dir/
    factor_db_path/_trace_id）经 owner（主类实例）动态读取，兼容运行时重赋值
    （34 §8.3 可变上下文修订）。主类 EvolutionLoop 组合持有本类实例，保留
    11 方法转发桩 + 16 属性 property 转发（兼容测试零改动，见 34 §8.5）。
    """

    def __init__(self, owner: Any) -> None:
        self._owner: Any = owner
        # ── 领域独享状态：L2 准入去冗余/正交化/结构簇配置（原主类 __init__ 迁移） ──
        self._repo: Optional[Any] = None
        try:
            from fts.config.settings import get_config

            _micro_cfg = get_config()
            # GAP-I206 (v2.71.0): L2 准入去冗余配置
            self._l2_elite_corr_threshold = float(getattr(_micro_cfg, "l2_elite_corr_threshold", 0.9))
            self._l2_elite_corr_max_scan = int(getattr(_micro_cfg, "l2_elite_corr_max_scan", 50))
            self._l2_elite_corr_debug = bool(getattr(_micro_cfg, "l2_elite_corr_debug", False))
            # GAP-I206 补充（v2.71.0）：正交化闭环配置
            self._l2_elite_orthogonalize = bool(getattr(_micro_cfg, "l2_elite_orthogonalize", True))
            self._l2_orthogonal_residual_corr_max = float(getattr(_micro_cfg, "l2_orthogonal_residual_corr_max", 0.3))
            self._l2_orthogonal_min_retained_ratio = float(getattr(_micro_cfg, "l2_orthogonal_min_retained_ratio", 0.3))
            # GAP-I206 补充（v2.72.0）：正交基底配置
            self._l2_orthogonal_basis_enabled = bool(getattr(_micro_cfg, "l2_orthogonal_basis_enabled", True))
            self._l2_orthogonal_basis_max_size = int(getattr(_micro_cfg, "l2_orthogonal_basis_max_size", 10))
            self._l2_orthogonal_basis_min_sharpe = float(getattr(_micro_cfg, "l2_orthogonal_basis_min_sharpe", 1.0))
            # GAP-XXX (v2.102.0): 结构性聚类配额配置（family 为来源标签非结构维度，
            # 多样性控制改由信号相关性承担）
            self._cluster_quota_enabled = bool(getattr(_micro_cfg, "structure_cluster_quota_enabled", True))
            self._cluster_max = int(getattr(_micro_cfg, "structure_cluster_max", 15))
            self._cluster_corr_threshold = float(getattr(_micro_cfg, "structure_cluster_corr_threshold", 0.85))
            self._cluster_max_scan = int(getattr(_micro_cfg, "l2_elite_corr_max_scan", 50))
        except Exception:
            # 配置读取失败时采用模块默认值，不阻断演化
            self._l2_elite_corr_threshold = 0.9
            self._l2_elite_corr_max_scan = 50
            self._l2_elite_corr_debug = False
            self._l2_elite_orthogonalize = True
            self._l2_orthogonal_residual_corr_max = 0.3
            self._l2_orthogonal_min_retained_ratio = 0.3
            self._l2_orthogonal_basis_enabled = True
            self._l2_orthogonal_basis_max_size = 10
            self._l2_orthogonal_basis_min_sharpe = 1.0
            self._cluster_quota_enabled = True
            self._cluster_max = 15
            self._cluster_corr_threshold = 0.85
            self._cluster_max_scan = 50

        # ── 组件实例化（原主类 __init__ L507-558 迁移；owner 提供 market/memory_dir/_decay_*） ──
        from .high_ic_screener import HighICScreener, HighICScreenConfig

        if owner.market == "futures":
            # 期货市场放宽 V5 经济逻辑维度最低分（LLM 演化因子 L2 评分偏低）
            futures_config = HighICScreenConfig(logic_min_score=1.0)
            self.high_ic_screener = HighICScreener(config=futures_config)
        else:
            self.high_ic_screener = HighICScreener()
        from ..monitor.elite_tracker import AutoRetireConfig, EliteFactorTracker

        self.elite_tracker = EliteFactorTracker(
            tracking_dir=str(owner.memory_dir / "tracking"),
            retire_config=AutoRetireConfig(
                observe_slope=owner._decay_observe_slope,
                retire_slope=owner._decay_retire_slope,
                slope_min_points=owner._decay_slope_min_points,
            ),
        )
        from .orthogonal_basis import OrthogonalBasisManager

        self.orthogonal_basis = OrthogonalBasisManager(
            basis_path=str(owner.memory_dir / "orthogonal_basis.json"),
            max_size=self._l2_orthogonal_basis_max_size,
            min_sharpe=self._l2_orthogonal_basis_min_sharpe,
            residual_corr_max=self._l2_orthogonal_residual_corr_max,
            min_retained_ratio=self._l2_orthogonal_min_retained_ratio,
        )

    def _write_seed_correlation_index(
        self,
        seed_correlations: list[FactorCorrelation],
        trace_id: str,
    ) -> None:
        """将 L2 种子因子相关性预检结果写入 elite 目录的共享索引文件。

        该文件供 L3 Portfolio Loop 批量读取，作为相关性管理的先验数据。
        """
        index_path = self._owner.elite_dir / "_l2_seed_correlation_index.json"
        index_data = {
            "source": "l2_seed_correlation_check",
            "trace_id": trace_id,
            "created_at": datetime.now().isoformat(),
            "threshold": 0.95,
            "total_pairs": len(seed_correlations),
            "correlations": [
                {
                    "factor_id_a": sc.get("factor_id_a", ""),
                    "factor_id_b": sc.get("factor_id_b", ""),
                    "pearson": sc.get("pearson", 0),
                    "spearman": sc.get("spearman", 0),
                }
                for sc in seed_correlations
            ],
        }
        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[evo] L2 相关性索引已写入: {index_path} ({len(seed_correlations)} 对高相关因子)")

    # ── GAP-I206 (v2.71.0): L2 准入去冗余 — 与既有 elite 相关性检查 ──

    def _scan_elite_correlations(
        self,
        factor: FactorProgram,
        threshold: float,
        max_scan: int,
    ) -> list[dict[str, Any]]:
        """扫描既有 elite，返回与新因子信号 |corr| ≥ threshold 的相关性对。

        新因子信号只计算一次；既有 elite 执行失败/NaN 兜底跳过；索引文件跳过。
        L2 准入去冗余（_check_elite_correlation）与结构簇配额（_count_cluster_members）
        共用本扫描，避免重复实现。

        Args:
            factor: 待检查因子
            threshold: 相关性判定阈值
            max_scan: 扫描上限（容量护栏）

        Returns:
            [{"factor_name_b", "factor_id_b", "pearson", "abs_pearson"}, ...]
            按 abs_pearson 降序；无命中返回 []
        """
        from .backtest_pipeline import BacktestPipeline

        if not self._owner.elite_dir.exists():
            return []

        # 新因子信号只计算一次，避免对每个既有 elite 重复执行
        try:
            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                self._owner.data,
                factor.get("params", {}),
            )
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(new_signal, np.ndarray) or len(new_signal) != len(self._owner.data):
            return []

        correlations: list[dict[str, Any]] = []
        scanned = 0
        for fp in sorted(self._owner.elite_dir.glob("*.json")):
            if fp.name == "_l2_seed_correlation_index.json":
                continue
            if scanned >= max_scan:
                break
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not (isinstance(data, dict) and data.get("code") and data.get("factor_id")):
                continue
            if data.get("factor_id") == factor.get("factor_id"):
                continue
            scanned += 1
            try:
                other_signal = BacktestPipeline._execute_factor_code(
                    data.get("code", ""),
                    self._owner.data,
                    data.get("params", {}),
                )
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(other_signal, np.ndarray) or len(other_signal) != len(new_signal):
                continue
            valid = ~(np.isnan(other_signal) | np.isnan(new_signal))
            if valid.sum() < 10:
                continue
            pearson = float(np.corrcoef(other_signal[valid], new_signal[valid])[0, 1])
            if np.isnan(pearson):
                continue
            if abs(pearson) >= threshold:
                correlations.append(
                    {
                        "factor_name_b": data.get("name", data.get("factor_name", "?")),
                        "factor_id_b": data.get("factor_id", "?"),
                        "pearson": pearson,
                        "abs_pearson": abs(pearson),
                    }
                )
        correlations.sort(key=lambda c: c["abs_pearson"], reverse=True)
        return correlations

    def _check_elite_correlation(self, factor: FactorProgram) -> Optional[dict[str, Any]]:
        """L2 准入去冗余：新演化因子晋升前与既有 elite 因子的信号相关性检查。

        对既有 elite 池（self._owner.elite_dir 下已晋升 JSON）逐个执行因子代码计算
        信号，与新因子信号做 Pearson 相关；存在相关绝对值 ≥ 阈值
        （l2_elite_corr_threshold，默认 0.9）的高相关对时返回最高相关对列表，
        否则返回 None（放行）。种子因子（shadow_observe=False）不经过本检查，
        由 _promote_to_elite 调用侧控制。

        Args:
            factor: 待晋升的新演化因子

        Returns:
            None: 无既有 elite / 无高相关命中（放行）
            dict: {"correlations": [{factor_name_b, factor_id_b, pearson,
                  abs_pearson}, ...]} 相关 ≥ 阈值的对（按 abs_pearson 降序）
        """
        correlations = self._owner._scan_elite_correlations(
            factor,
            self._l2_elite_corr_threshold,
            self._l2_elite_corr_max_scan,
        )
        if not correlations:
            return None
        return {"correlations": correlations}

    def _count_cluster_members(self, factor: FactorProgram) -> int:
        """结构簇规模代理：与既有 elite 信号 |corr| ≥ cluster_corr_threshold 的成员数。

        结构性聚类配额（GAP-XXX）替代 max_per_family 家族配额：family 为知识注入
        来源标签（非正交结构维度），多样性控制改由信号相关性承担。复用
        _scan_elite_correlations 扫描逻辑；无既有 elite / 信号异常返回 0（放行）。

        Args:
            factor: 待晋升因子

        Returns:
            同类成员数（0 = 放行）
        """
        return len(
            self._owner._scan_elite_correlations(
                factor,
                self._cluster_corr_threshold,
                self._cluster_max_scan,
            )
        )

    def _orthogonalize_via_basis(
        self,
        factor: FactorProgram,
    ) -> Optional[dict[str, Any]]:
        """多因子正交基底正交化（GAP-I206 补充，v2.72.0）。

        候选因子与既有 elite 高相关时，优先对正交基底（Gram-Schmidt）做
        迭代 OLS 残差化：依次剥离候选信号与基底每个成员的线性成分，得到
        与整个基底近似正交的残差信号。质量合格（残差与基底最大相关 <
        ``l2_orthogonal_residual_corr_max`` 且保留比 > ``l2_orthogonal_min_retained_ratio``）
        则返回正交化因子 dict 并注册为新基底成员；否则返回 None（回退
        单参照 OLS 或拒绝兜底）。

        Args:
            factor: 待晋升的演化因子

        Returns:
            dict: 基底正交化版本因子；None: 基底不可用/失败/质量不合格
        """
        if not self._l2_orthogonal_basis_enabled:
            return None
        # 兼容类级未绑定调用（测试 EvolutionLoop._orthogonalize_via_basis(mock_loop, ...)）：
        # self 无 _owner 时回退为 self（Mock 场景直接访问其注入属性）
        owner = getattr(self, "_owner", self)
        try:
            from .backtest_pipeline import BacktestPipeline

            def _basis_signal_getter(member: dict[str, Any]):
                """从 elite 快照读取基底成员代码并执行（失败返回 None）。"""
                fid = member.get("factor_id", "")
                if not fid:
                    return None
                fp = owner.elite_dir / f"{fid}.json"
                if not fp.exists():
                    return None
                data = json.loads(fp.read_text(encoding="utf-8"))
                if not (isinstance(data, dict) and data.get("code")):
                    return None
                return BacktestPipeline._execute_factor_code(
                    data.get("code", ""),
                    owner.data,
                    data.get("params", {}),
                )

            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                owner.data,
                factor.get("params", {}),
            )
            if not isinstance(new_signal, np.ndarray):
                return None
            sharpe = 0.0
            eval_info = factor.get("evaluation")
            if isinstance(eval_info, dict):
                bt = eval_info.get("level_1_backtest", {})
                if isinstance(bt, dict):
                    sharpe = float(bt.get("sharpe", 0.0))
            orth = self.orthogonal_basis.orthogonalize(
                factor=factor,
                candidate_signal=new_signal,
                signal_getter=_basis_signal_getter,
                sharpe=sharpe,
            )
            if orth is not None:
                # 注册为新基底成员（保持基底随 elite 池动态扩充）
                self.orthogonal_basis.register(orth)
                logger.warning(
                    "[orth-basis] 因子 %s 基底正交化入库（basis=%d 成员, pearson %.3f, GAP-I206 补充）",
                    factor.get("name", "?"),
                    len(orth.get("orthogonalized_basis", [])),
                    orth.get("orthogonalized_pearson", 0.0),
                )
            return orth
        except Exception as e:  # noqa: BLE001
            logger.debug("[orth-basis] 基底正交化失败回退单参照: %s", e)
            return None

    def _orthogonalize_candidate(
        self,
        factor: FactorProgram,
        pair: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """正交化闭环（GAP-I206 补充）：对候选因子信号关于参照 elite 做 OLS 残差。

        候选因子与既有 elite 高相关（_check_elite_correlation 命中）时，
        不直接拒绝——对候选信号关于参照 elite 信号做一元线性回归取残差；
        残差质量合格（与参照因子相关性 < ``l2_orthogonal_residual_corr_max``
        且保留比 > ``l2_orthogonal_min_retained_ratio``）时返回正交化因子
        dict（保留原字段 + orthogonalized 元数据 + ``orthogonal_signal`` 残差
        快照），由调用方以正交化版本入库；否则返回 None（拒绝兜底）。

        Args:
            factor: 待晋升的演化因子
            pair: _check_elite_correlation 返回的高相关对（含 factor_id_b）

        Returns:
            dict: 正交化版本因子；None: 残差质量不合格 / 信号不可算
        """
        from .backtest_pipeline import BacktestPipeline

        fid_b = pair.get("factor_id_b", "")
        if not fid_b:
            return None
        ref_fp = self._owner.elite_dir / f"{fid_b}.json"
        if not ref_fp.exists():
            return None
        try:
            ref_data = json.loads(ref_fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        try:
            new_signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                self._owner.data,
                factor.get("params", {}),
            )
            other_signal = BacktestPipeline._execute_factor_code(
                ref_data.get("code", ""),
                self._owner.data,
                ref_data.get("params", {}),
            )
        except Exception:  # noqa: BLE001
            return None
        if not (isinstance(new_signal, np.ndarray) and isinstance(other_signal, np.ndarray)):
            return None
        if len(new_signal) != len(other_signal):
            return None
        valid = ~(np.isnan(new_signal) | np.isnan(other_signal))
        if int(valid.sum()) < 20:
            return None
        y = new_signal[valid].astype(float)
        x = other_signal[valid].astype(float)
        if float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
            return None
        # OLS 残差: residual = y - (a + b·x)
        b = float(np.cov(x, y)[0, 1] / np.var(x))
        a = float(np.mean(y) - b * np.mean(x))
        residual = y - (a + b * x)
        # 质量校验：残差与参照因子正交性 + 独立信息保留比
        resid_corr = abs(float(np.corrcoef(residual, x)[0, 1])) if float(np.std(residual)) > 1e-12 else 1.0
        retained_ratio = float(np.std(residual) / np.std(y))
        if resid_corr > self._l2_orthogonal_residual_corr_max:
            logger.info(
                "[L2-redun] %s 正交化残差仍与 %s 相关 %.3f > %.2f，残差不合格",
                factor.get("name", "?"),
                pair.get("factor_name_b", "?"),
                resid_corr,
                self._l2_orthogonal_residual_corr_max,
            )
            return None
        if retained_ratio < self._l2_orthogonal_min_retained_ratio:
            logger.info(
                "[L2-redun] %s 正交化残差保留比 %.3f < %.2f，独立信息不足",
                factor.get("name", "?"),
                retained_ratio,
                self._l2_orthogonal_min_retained_ratio,
            )
            return None
        # 构造正交化因子：保留原字段 + 正交化元数据 + 残差信号快照（对齐全长度）
        residual_full = np.full(len(new_signal), np.nan)
        residual_full[valid] = residual
        orth = dict(factor)
        orth["orthogonalized"] = True
        orth["orthogonalized_against"] = fid_b
        orth["orthogonalized_pearson"] = float(pair.get("pearson", 0.0))
        orth["orthogonal_signal"] = [float(v) if np.isfinite(v) else None for v in residual_full]
        return orth

    def _load_elite_parent_factors(self) -> list[dict[str, Any]]:
        """从 elite 快照目录加载因子作为父因子池。

        场景: 种子因子全部已存在 elite 快照（去重跳过、无新晋升）时，
        无合格父因子导致演化循环 0 代跳过。回退使用既有精英因子继续
        演化（种子重复晋升由 _promote_to_elite 去重保护）。
        """
        parents: list[dict[str, Any]] = []
        for fp in sorted(self._owner.elite_dir.glob("*.json")):
            if fp.name == "_l2_seed_correlation_index.json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("code") and data.get("factor_id"):
                parents.append(data)
        return parents

    def _release_repo_after(func):
        """E.4 S1: release L3 repo write lock after method exits (decorator)."""
        from functools import wraps

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            finally:
                if getattr(self, "_repo", None) is not None:
                    try:
                        self._repo.close()
                    except Exception:
                        pass
                    self._repo = None

        return wrapper

    def _get_repo(self):
        """延迟初始化 DuckDB 仓储（GAP-030: 支持 factor_db_path 注入隔离库）。"""
        if self._repo is None:
            from .factor_db import FactorRepository

            self._repo = (
                FactorRepository(db_path=self._owner.factor_db_path, market=self._owner.market)
                if self._owner.factor_db_path
                else FactorRepository(market=self._owner.market)
            )
        return self._repo

    @_release_repo_after
    def _promote_to_elite(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        quality_score: Optional[dict] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_observe: Optional[bool] = None,
    ) -> Optional[Path]:
        """将因子晋升到精英池。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            seed_correlations: L2 种子因子相关性标记（可选）
            quality_score: 质量评分卡结果（Phase A.1 集成）
            audit_report: 因子审计报告（Phase B.3 集成）
            shadow_observe: 是否进入影子池观察（默认 None → 读 FTS_EVOLUTION_SHADOW_OBSERVE，
                            默认 "0" = 关闭观察期直接进正式组合；种子因子/初始池导入
                            显式传 False；设 env=1 可恢复观察期模式）

        Returns:
            Path: 晋升成功
            None: 因子名称重复，跳过晋升
        """
        # 2026-08-13: 新晋级精英因子观察期默认关闭（env 可恢复）
        if shadow_observe is None:
            import os

            shadow_observe = os.getenv("FTS_EVOLUTION_SHADOW_OBSERVE", "0") == "1"
        # 去重检查：DuckDB 是权威数据源，通过 factor_catalog 表检查
        factor_name = factor.get("name", "")
        try:
            repo = self._owner._get_repo()
            existing = repo.get_factor_by_name(factor_name, market=self._owner.market)
            if existing:
                # GAP-F10 (v2.73.0): 被拒因子结构化记录（分级日志，替代 print）
                logger.info(
                    "[evo] 跳过重复因子: %s (DuckDB 已存在, market=%s, trace_id=%s)",
                    factor_name,
                    self._owner.market,
                    getattr(self._owner, "_trace_id", ""),
                )
                return None
        except Exception:
            pass

        # ── 多样性配额检查（GAP-077 v2.102.0）：结构簇配额替代 max_per_family 家族配额 ──
        # family 是知识注入来源标签（非正交结构维度），多样性控制改由信号相关性承担：
        # 统计与既有 elite |corr| ≥ cluster_corr_threshold 的同类成员数，≥ 上限拒绝晋升。
        # 开关关闭时回退 max_per_family 旧逻辑（平滑迁移）。
        if self._cluster_quota_enabled:
            cluster_size = self._owner._count_cluster_members(factor)
            if cluster_size >= self._cluster_max:
                logger.warning(
                    "[evo] 结构簇配额拒绝晋升 [%s]: 同类成员 %d ≥ 上限 %d (corr≥%.2f, trace_id=%s)",
                    factor_name,
                    cluster_size,
                    self._cluster_max,
                    self._cluster_corr_threshold,
                    getattr(self._owner, "_trace_id", ""),
                )
                return None
        else:
            # ── 回退：max_per_family 家族配额（旧逻辑，平滑迁移） ──
            factor_family = factor.get("family", "unknown")
            max_per_family = self._owner.budget.get("max_per_family", 15)
            # GAP-070 (v2.98.0): 兜底家族 'other'/'unknown' 永久豁免上限——它们是
            # "无法归类"的回收站家族，对其设限等价于对整个演化新因子晋升通道设总量
            # 上限，压制演化空间；逻辑同质化保护已由 L2 准入去冗余（GAP-I206 相关性
            # 预检 + 正交化闭环 + Gram-Schmidt 基底）承担。
            if factor_family not in ("other", "unknown"):
                try:
                    repo = self._owner._get_repo()
                    existing_family = repo.get_by_family(
                        family=factor_family,
                        market=self._owner.market,
                        limit=100,
                    )
                    if len(existing_family) >= max_per_family:
                        # GAP-F10 (v2.73.0): 家族拦截升级分级日志 + 结构化拒绝记录
                        logger.warning(
                            "[evo] 家族多样性限制拒绝晋升 [%s]: 家族 '%s' 已有 %d 个因子 (上限 %d, trace_id=%s)",
                            factor_name,
                            factor_family,
                            len(existing_family),
                            max_per_family,
                            getattr(self._owner, "_trace_id", ""),
                        )
                        return None
                except Exception:
                    pass

        fp = self._owner.elite_dir / f"{factor['factor_id']}.json"
        # 将 factor 字段展开到顶层，方便 cli 直接读取
        record = dict(factor)
        # 确保 market 字段正确：若因子为默认 "multi"，使用演化上下文的市场
        if record.get("market", "multi") in ("multi", "other") and self._owner.market in ("futures",):
            record["market"] = self._owner.market
        record["evaluation"] = evaluation

        # ── ★ GAP-I206 (v2.71.0): L2 准入去冗余 — 与既有 elite 相关性检查 ──
        # 演化因子（shadow_observe=True）晋升前与既有 elite 计算信号相关性，
        # 超过阈值拒绝晋升（防 elite 池相关性膨胀稀释组合夏普）。种子因子
        # （shadow_observe=False 首轮导入）跳过——初始入库全量放行。
        if shadow_observe:
            elite_corr = self._owner._check_elite_correlation(factor)
            if elite_corr is not None:
                _pairs = elite_corr.get("correlations", [])
                _max = _pairs[0] if _pairs else {}
                _name_b = _max.get("factor_name_b", "?")
                _corr = _max.get("pearson", 0.0)
                if self._l2_elite_orthogonalize and _pairs:
                    # 正交化闭环（GAP-I206 补充）：高相关因子先尝试 OLS 残差化，
                    # 残差质量合格则以正交化版本入库；不合格拒绝兜底。
                    # v2.72.0: 优先走多因子正交基底（Gram-Schmidt），
                    # 基底不可用/失败时回退单参照 OLS。
                    orth_factor = self._owner._orthogonalize_via_basis(factor)
                    if orth_factor is None:
                        orth_factor = self._owner._orthogonalize_candidate(factor, _max)
                    if orth_factor is not None:
                        factor = cast(FactorProgram, orth_factor)
                        record = dict(factor)
                        if record.get("market", "multi") in ("multi", "other") and self._owner.market in ("futures",):
                            record["market"] = self._owner.market
                        record["evaluation"] = evaluation
                        _basis_tag = (
                            f"正交基底({len(factor.get('orthogonalized_basis', []))}成员)"
                            if factor.get("orthogonalized_basis")
                            else f"参照 {_name_b}"
                        )
                        print(
                            f"[evo] ★ L2 正交化闭环 [{factor.get('name', '?')}]: "
                            f"与既有 elite 相关 {_corr:.3f} ≥ 阈值 "
                            f"{self._l2_elite_corr_threshold}，{_basis_tag} 正交化残差入库"
                        )
                        logger.warning(
                            "[L2-redun] 因子 %s 正交化残差入库（against %s, pearson %.3f, GAP-I206 补充）",
                            factor.get("name", "?"),
                            _name_b,
                            _corr,
                        )
                    else:
                        print(
                            f"[evo] ★ L2 准入去冗余拦截 [{factor.get('name', '?')}]: "
                            f"与既有 elite {_name_b} 相关 {_corr:.3f} ≥ 阈值 "
                            f"{self._l2_elite_corr_threshold}，正交化残差不合格，拒绝晋升"
                        )
                        logger.warning(
                            "[L2-redun] 因子 %s 与既有 elite %s 相关 %.3f ≥ %.2f，正交化残差不合格拒绝（GAP-I206）",
                            factor.get("name", "?"),
                            _name_b,
                            _corr,
                            self._l2_elite_corr_threshold,
                        )
                        return None
                else:
                    print(
                        f"[evo] ★ L2 准入去冗余拦截 [{factor.get('name', '?')}]: "
                        f"与既有 elite {_name_b} 相关 {_corr:.3f} ≥ 阈值 {self._l2_elite_corr_threshold}，拒绝晋升"
                    )
                    logger.warning(
                        "[L2-redun] 因子 %s 与既有 elite %s 相关 %.3f ≥ %.2f，拒绝晋升（GAP-I206）",
                        factor.get("name", "?"),
                        _name_b,
                        _corr,
                        self._l2_elite_corr_threshold,
                    )
                    return None
            if self._l2_elite_corr_debug:
                # 无既有 elite 或检查失败时静默放行（首次晋升场景）
                logger.debug(
                    "[L2-redun] %s 无既有 elite 相关性命中，放行",
                    factor.get("name", "?"),
                )

        # ── ★ Phase B.4: 高IC筛查强制门（所有市场统一） ──
        # 前置计算: 从种子相关性标记提取 max_corr（若已传入）
        max_corr_detected = None
        if seed_correlations:
            factor_id = factor.get("factor_id", "")
            corr_vals = [
                max(abs(sc.get("pearson", 0)), abs(sc.get("spearman", 0)))
                for sc in seed_correlations
                if factor_id in (sc.get("factor_id_a", ""), sc.get("factor_id_b", ""))
            ]
            if corr_vals:
                max_corr_detected = max(corr_vals)
        high_ic_screen = self.high_ic_screener.screen(
            factor=record,
            evaluation=evaluation,
            correlation_metadata=({"max_corr_detected": max_corr_detected} if max_corr_detected is not None else {}),
            backtest_pipeline=(
                evaluation.get("backtest_pipeline", {}) if isinstance(evaluation.get("backtest_pipeline"), dict) else {}
            ),
            trace_id=getattr(self._owner, "_trace_id", ""),
        )
        if high_ic_screen.grade == "C":
            veto_info = (
                "；".join(high_ic_screen.veto_reasons)
                if high_ic_screen.veto_reasons
                else f"总分 {high_ic_screen.total_score:.1f} < 60"
            )
            print(
                f"[evo] ★ 高IC筛查拦截 [{factor_name}]: "
                f"grade={high_ic_screen.grade}, 总分={high_ic_screen.total_score:.1f}, "
                f"原因={veto_info}"
            )
            return None
        record["high_ic_screen"] = high_ic_screen.to_dict()

        # ── 多重检验强制门: 拒绝未通过多重检验校正的因子 ──
        level_3 = evaluation.get("level_3_multiple", {})
        if not level_3.get("passed", False):
            bonf_p = level_3.get("bonferroni_p", "N/A")
            adj_t = level_3.get("adjusted_t", "N/A")
            p_str = f"{bonf_p:.4f}" if isinstance(bonf_p, float) else str(bonf_p)
            t_str = f"{adj_t:.4f}" if isinstance(adj_t, float) else str(adj_t)
            print(f"[evo] 多重检验未通过 [{factor_name}]: Bonferroni p={p_str}, adjusted_t={t_str}")
            return None

        # ── 写入质量评分卡 (Phase A.1 集成) ──
        if quality_score is not None:
            record["quality_score"] = quality_score

        # ── 写入审计报告 (Phase B.3 集成) ──
        if audit_report is not None:
            record["audit_report"] = audit_report.to_dict()

        # ── 写入 L2 相关性元数据（供 L3 参考） ──
        if seed_correlations:
            factor_id = factor.get("factor_id", "")
            corr_flags: list[dict[str, Any]] = []

            for sc in seed_correlations:
                a, b = sc.get("factor_id_a", ""), sc.get("factor_id_b", "")
                pearson = sc.get("pearson", 0)
                spearman = sc.get("spearman", 0)
                max_abs = max(abs(pearson), abs(spearman))

                if factor_id == a or factor_id == b:
                    partner = b if factor_id == a else a
                    corr_flags.append(
                        {
                            "partner_factor_id": partner,
                            "pearson": pearson,
                            "spearman": spearman,
                            "max_abs": max_abs,
                            "source": "l2_seed_correlation_check",
                        }
                    )

            if corr_flags:
                record["correlation_metadata"] = {
                    "l2_seed_flags": corr_flags,
                    "flag_count": len(corr_flags),
                    "max_corr_detected": max((f["max_abs"] for f in corr_flags), default=0),
                }
                print(f"[evo] 因子 {factor.get('name', '?')} 写入 L2 相关性标记: {len(corr_flags)} 个高相关对")

        # ── 影子池标记（L2 晋升节奏控制）：新演化因子先进影子池观察 ──
        if shadow_observe:
            from .evolution_loop import _build_shadow_pool, _SHADOW_OBSERVE_TRADING_DAYS

            record["shadow_pool"] = _build_shadow_pool()
            print(f"[evo] 因子 {factor.get('name', '?')} 进入影子池观察 ({_SHADOW_OBSERVE_TRADING_DAYS} 个交易日)")

        # ── 晋升时间戳（用于纯外推验证，P2 差距修复） ──
        record["promoted_at"] = datetime.now().isoformat()

        # ── 写入 DuckDB（主存储，SSOT；plans/29 P1 写路径反转） ──
        # GAP-032 严格一致：DuckDB 是主存储。P1 起 JSON 仅降级为只读快照——
        # 先写 DuckDB，成功后写 JSON（JSON 写失败不阻断晋升）；DuckDB 失败
        # 则不写 JSON 直接判定晋升失败，杜绝"快照有、catalog 无"孤儿数据
        write_ok = self._owner._write_to_duckdb(
            factor,
            evaluation,
            quality_score,
            seed_correlations,
            audit_report,
            shadow_pool=record.get("shadow_pool"),
        )
        if not write_ok:
            print(f"[evo] ❌ 晋升失败 [{factor.get('name', '?')}]: DuckDB 写入失败（未写 JSON 快照）{fp.name}")
            return None

        # ── 写入 JSON 快照（只读备份，非阻塞） ──
        try:
            fp.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("[evo] JSON 快照写入失败（不影响晋升）: %s, err=%s", fp.name, e)

        # ── ★ GAP-036: 激进清理 — L1 注入候选晋升精英后删除 l1_injected 文件 ──
        # 非阻塞：删除失败不影响晋升，仅记录 warning
        f_source = factor.get("source", "")
        f_parent_id = factor.get("parent_id")
        if f_source == "bootstrapping" and f_parent_id and self._owner.inject_dir.exists():
            cand_file = self._owner.inject_dir / f"{f_parent_id}.json"
            try:
                if cand_file.exists():
                    cand_file.unlink()
                    logger.info(
                        "[GAP-036] 已删除 L1 注入候选文件: %s (factor=%s, source=%s)",
                        cand_file.name,
                        factor.get("name", "?"),
                        f_source,
                    )
            except OSError as e:
                logger.warning("[GAP-036] 删除 L1 候选文件失败: %s, err=%s", cand_file.name, e)

        # ── ★ 种子溯源写入 (seed_lineage) ──
        # 非阻塞：溯源写入失败不影响晋升，仅记录 warning
        try:
            repo = self._owner._get_repo()
            f_id = factor.get("factor_id", "")
            f_name = factor.get("name", "")
            f_source = factor.get("source", "")
            f_gen = factor.get("generation", 0)
            f_family = factor.get("family", "unknown")
            f_parent_id = factor.get("parent_id")
            f_trace_id = factor.get("trace_id", "")

            lineage = repo.resolve_seed_lineage(
                factor_id=f_id,
                factor_name=f_name,
                factor_source=f_source,
                factor_generation=f_gen,
                factor_family=f_family,
                factor_parent_id=f_parent_id,
                market=self._owner.market,
            )
            repo.write_seed_lineage(
                factor_id=f_id,
                factor_name=f_name,
                seed_name=lineage["seed_name"],
                seed_family=lineage["seed_family"],
                seed_market=lineage["seed_market"],
                generation=lineage["generation"],
                parent_id=f_parent_id,
                trace_id=f_trace_id,
            )
        except Exception as e:
            logger.debug("[seed_lineage] 溯源写入非阻塞异常: %s", e)

        # ── Phase A.2: 注册到精英因子追踪器 ──
        try:
            factor_id = factor.get("factor_id", "")
            factor_name = factor.get("name", "?")
            sharpe = 0.0
            ic = 0.0
            if isinstance(evaluation, dict):
                bt = evaluation.get("level_1_backtest", {})
                if isinstance(bt, dict):
                    ic = bt.get("ic", 0.0)
                    sharpe = bt.get("sharpe", 0.0)
            grade = None
            quality_score_value = None
            if quality_score is not None:
                grade = quality_score.get("grade")
                quality_score_value = quality_score.get("total_score")
            self.elite_tracker.init_tracker(
                factor_id=factor_id,
                name=factor_name,
                entry_ic=ic,
                entry_sharpe=sharpe,
                grade=grade,
                quality_score=quality_score_value,
            )
        except Exception as e:
            logger.debug("精英因子追踪器注册失败: %s", e)

        # ── 记录一致性日志（P4） ──
        from .evolution_loop import _log_consistency_event

        _log_consistency_event(
            event_type="promote",
            factor_id=factor.get("factor_id", ""),
            factor_name=factor.get("name", ""),
            market=self._owner.market,
            status="active",
            json_path=str(fp),
            trace_id=factor.get("trace_id", ""),
        )

        # E.4 S1: promotion done, release L3 repo write lock
        if self._repo is not None:
            try:
                self._repo.close()
            except Exception:
                pass
            self._repo = None

        return fp

    def _write_to_duckdb(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        quality_score: Optional[dict] = None,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        audit_report: Optional[FactorAuditReport] = None,
        shadow_pool: Optional[dict] = None,
    ) -> bool:
        """将因子写入 DuckDB（主存储层）。

        支持幂等写入：若 factor_id 已存在则更新，不存在则创建。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            quality_score: 质量评分卡结果
            seed_correlations: L2 种子因子相关性标记
            audit_report: 因子审计报告（Phase B.3 集成）
            shadow_pool: 影子池标记（L2 晋升节奏控制，可选）

        Returns:
            True: 写入成功
            False: 写入失败（GAP-032 严格一致：失败不再吞异常，
                   由调用方决定是否回滚 JSON 快照）
        """
        try:
            repo = self._owner._get_repo()
            factor_id = factor.get("factor_id")
            factor_name = factor.get("name", "?")
            factor_market: str = factor.get("market", "multi")
            # 若因子未显式指定有效市场（multi/other 为默认值），使用演化上下文的市场
            if factor_market in ("multi", "other") and self._owner.market in ("futures",):
                factor_market = self._owner.market
            factor_family = factor.get("family", "other")

            l1 = evaluation.get("level_1_backtest", {})

            factor_dict = {
                "factor_id": factor_id,
                "name": factor_name,
                "code": factor.get("code", ""),
                "params": factor.get("params", {}),
                "signature": factor.get("signature", {}),
                "economic_logic": factor.get("economic_logic", {}),
                "source": factor.get("source", "macro_evolution"),
                "parent_id": factor.get("parent_id"),
                "generation": factor.get("generation", 0),
                "trace_id": factor.get("trace_id"),
                "market": factor_market,
                "family": factor_family,
                "is_elite": True,
                "sharpe": l1.get("sharpe", 0.0),
                "ic": l1.get("ic", 0.0),
                "icir": l1.get("icir", 0.0),
                "max_drawdown": l1.get("max_drawdown", 0.0),
                "turnover_monthly": l1.get("turnover_monthly", 0.0),
                "decay_6m": l1.get("decay_6m", 0.0),
                "metadata": {
                    "quality_score": quality_score,
                    "correlation_metadata": factor.get("correlation_metadata", {}),
                    "symbols": factor.get("symbols", []),
                    "risk_tag": factor.get("risk_tag"),
                    "factor_version": factor.get("factor_version", "v2"),
                    "audit_report": audit_report.to_dict() if audit_report else None,
                    "shadow_pool": shadow_pool,
                    # 正交化闭环（GAP-I206 补充，v2.71.0/v2.72.0 基底）
                    "orthogonalized": factor.get("orthogonalized", False),
                    "orthogonalized_against": factor.get("orthogonalized_against", ""),
                    "orthogonalized_pearson": factor.get("orthogonalized_pearson", 0.0),
                    "orthogonalized_basis": factor.get("orthogonalized_basis", []),
                    "orthogonal_signal": factor.get("orthogonal_signal", []),
                },
            }

            # ── 幂等写入：已存在则更新，不存在则创建 ──
            existing = repo.get_factor(factor_id)
            if existing:
                repo.update_factor(factor_id, factor_dict)
                print(f"[evo] 🔄 更新已有因子 {factor_name} 到 DuckDB [market={factor_market}]")
            else:
                repo.create_factor(factor_dict)
                print(f"[evo] ✅ 新建因子 {factor_name} 到 DuckDB [market={factor_market}]")

            # ── 写入/更新评估记录 ──
            l2 = evaluation.get("level_2_economic", {})
            l3 = evaluation.get("level_3_multiple", {})

            eval_dict = {
                "trace_id": evaluation.get("trace_id"),
                "ic": l1.get("ic", 0),
                "icir": l1.get("icir", 0),
                "sharpe": l1.get("sharpe", 0),
                "max_drawdown": l1.get("max_drawdown", 0),
                "turnover": l1.get("turnover_monthly", 0),
                "t_stat": l1.get("t_stat", 0),
                "monotonicity": l1.get("monotonicity", False),
                "oos_ratio": l1.get("oos_ratio", 0),
                "theory_score": l2.get("theory", 0),
                "behavioral_score": l2.get("behavioral", 0),
                "microstructure_score": l2.get("microstructure", 0),
                "institutional_score": l2.get("institutional", 0),
                "dims_passed": l2.get("dims_passed", 0),
                "bonferroni_p": l3.get("bonferroni_p", 1.0),
                "fdr_q": l3.get("fdr_q", 0.05),
                "effective_n": l3.get("effective_n_factors", 1),
                "adjusted_t": l3.get("adjusted_t", 0),
                "l3_passed": l3.get("passed", False),
                "overall_passed": evaluation.get("passed", False),
                "failure_reasons": evaluation.get("failure_reasons", []),
                "evaluated_at": evaluation.get("evaluated_at"),
            }

            repo.add_evaluation(factor_id, eval_dict)
            return True

        except Exception as e:
            factor_name = factor.get("name", "?")
            print(f"[evo] ⚠️ DuckDB 写入失败 [{factor_name}]: {e}")
            import traceback

            traceback.print_exc()
            return False
