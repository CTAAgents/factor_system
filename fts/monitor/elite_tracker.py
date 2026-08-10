"""
fts.monitor.elite_tracker — 精英因子样本外跟踪与自动淘汰。

Tracks elite factors post-insertion: weekly IC, decay detection, auto-retirement.
Phase A.2: 增加分级准入（A/B/C级）、增强衰减判定、观察期机制。

用法:
    tracker = EliteFactorTracker(tracking_dir="memory/tracking")
    # 分级准入 (A 级直接 active, B 级进入观察期, C 级淘汰)
    tracker.init_tracker(factor_id="f_001", name="momentum", entry_ic=0.05,
                         entry_sharpe=1.2, grade="A", quality_score=42.0)
    tracker.update("f_001", 0.03)
    decaying = tracker.get_decaying(max_consecutive=4)
    retired = tracker.auto_retire()
    report = tracker.report()

版本: v0.2.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np

from fts.core.atomic import atomic_read, atomic_write

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────

FactorGrade = Literal["A", "B", "C"]
"""因子质量等级。A(优秀)/B(合格)/C(不合格)"""

FactorStatus = Literal[
    "active",  # 活跃
    "observing",  # 观察期 (B级因子)
    "decaying",  # 衰减中
    "critical_decay",  # 严重衰减
    "retired",  # 已淘汰
    "deprecated",  # 已废弃 (保留历史)
    "rejected",  # 被拒绝准入
]
"""因子生命周期状态。"""

# 衰减分级（GAP-I305，v2.72.0）：基于滚动 6M IC 线性回归斜率划分
DecayGrade = Literal["normal", "observe", "retired"]
"""衰减分级：normal(正常) / observe(观察) / retired(退役)。"""


class TrackingSnapshot(dict):
    """精英因子跟踪快照。

    存储位置: ``{tracking_dir}/{factor_id}.json``

    状态转换:
    - A级 (score>=40): active
    - B级 (30<=score<40): observing → 观察期结束 → active/decaying
    - C级 (score<30): rejected
    - active: 连续3月IC<0 → decaying → 连续6月Sharpe降>50% → critical_decay → retired
    """

    pass  # 使用 dict 保持向后兼容


# ─── 配置 ──────────────────────────────────────────────────


@dataclass
class GradeThreshold:
    """分级准入阈值配置。"""

    a_threshold: float = 40.0  # A级下限 (总分)
    b_threshold: float = 30.0  # B级下限 (总分)
    observation_months: int = 3  # B级观察期 (月)
    ic_decay_months: int = 3  # 连续IC<0 衰减判定
    sharpe_decline_months: int = 6  # 连续Sharpe下降 严重衰减判定
    sharpe_decline_ratio: float = 0.5  # Sharpe下降比例阈值


@dataclass
class AutoRetireConfig:
    """自动淘汰配置。"""

    max_consecutive_zero_ic: int = 4  # 周度连续零值 IC 阈值
    max_decay_6m: float = 0.30  # 衰减率阈值
    min_active_days: int = 30  # 最小活跃天数
    cooldown_days: int = 7  # 冷却期（淘汰后多久可重新评估）
    grade_threshold: GradeThreshold = field(default_factory=GradeThreshold)
    # ── GAP-I305 衰减分级（v2.72.0）──
    # 滚动 6M IC 线性回归斜率（负值=衰减）。slope <= -observe_slope 进入观察；
    # slope <= -retire_slope 进入退役。归一化区间 [-1.0, 1.0]。
    observe_slope: float = 0.10  # 观察斜率阈值（|slope| >= 0.10 → observe）
    retire_slope: float = 0.20  # 退役斜率阈值（|slope| >= 0.20 → retired）
    # 衰减分级最小 IC 序列长度（不足则视为 normal）
    slope_min_points: int = 6


# ─── EliteFactorTracker ─────────────────────────────────────


class EliteFactorTracker:
    """精英因子样本外跟踪器。

    为每个精英因子维护一个 ``TrackingSnapshot``，持久化到 ``tracking_dir`` 目录。
    支持 IC 追踪、衰减检测、分级准入与自动淘汰。

    Args:
        tracking_dir: 跟踪快照存储目录（默认 "memory/tracking"）
        grade_threshold: 分级准入阈值配置
    """

    def __init__(
        self,
        tracking_dir: str = "memory/tracking",
        grade_threshold: Optional[GradeThreshold] = None,
        retire_config: Optional[AutoRetireConfig] = None,
    ) -> None:
        self._tracking_dir = Path(tracking_dir)
        self._tracking_dir.mkdir(parents=True, exist_ok=True)
        self._threshold = grade_threshold or GradeThreshold()
        self._retire = retire_config or AutoRetireConfig()

    # ─── 路径辅助 ────────────────────────────────────────

    def _path(self, factor_id: str) -> Path:
        return self._tracking_dir / f"{factor_id}.json"

    def _write_snapshot(self, factor_id: str, snapshot: dict) -> None:
        """将快照原子写盘（供外部回调持久化反馈等补充字段）。

        Args:
            factor_id: 因子唯一标识
            snapshot: 完整快照 dict
        """
        atomic_write(str(self._path(factor_id)), snapshot)

    # ─── 分级判定 ─────────────────────────────────────────

    def determine_grade(self, quality_score: float) -> FactorGrade:
        """根据质量评分确定因子等级。

        Args:
            quality_score: 因子质量评分 (0-50)

        Returns:
            FactorGrade: "A" / "B" / "C"
        """
        if quality_score >= self._threshold.a_threshold:
            return "A"
        elif quality_score >= self._threshold.b_threshold:
            return "B"
        else:
            return "C"

    # ─── 初始化 (增强版) ──────────────────────────────────

    def init_tracker(
        self,
        factor_id: str,
        name: str,
        entry_ic: float,
        entry_sharpe: float,
        entry_at: Optional[str] = None,
        grade: Optional[FactorGrade] = None,
        quality_score: Optional[float] = None,
    ) -> dict:
        """创建新的跟踪记录（支持分级准入）。

        Args:
            factor_id: 因子唯一标识
            name: 人类可读名
            entry_ic: 入库时 IC
            entry_sharpe: 入库时夏普
            entry_at: 入库时间（ISO 格式，默认当前 UTC 时间）
            grade: 质量等级（A/B/C），若提供 quality_score 则自动判定
            quality_score: 质量评分（0-50），用于自动判定等级

        Returns:
            新创建的 TrackingSnapshot

        Raises:
            ValueError: C级因子被拒绝准入时
        """
        now = entry_at or datetime.now(timezone.utc).isoformat()

        # 确定等级
        if grade is None and quality_score is not None:
            grade = self.determine_grade(quality_score)
        elif grade is None:
            grade = "A"  # 默认 A 级（向后兼容）

        # 分级准入逻辑
        if grade == "C":
            status = "rejected"
        elif grade == "B":
            status = "observing"
        else:
            status = "active"

        # 计算观察期结束时间
        observation_end = None
        if grade == "B":
            obs_end_dt = datetime.fromisoformat(now) + timedelta(days=self._threshold.observation_months * 30)
            observation_end = obs_end_dt.isoformat()

        snapshot: dict = {
            "factor_id": factor_id,
            "name": name,
            "entry_ic": entry_ic,
            "entry_sharpe": entry_sharpe,
            "entry_at": now,
            "weekly_ic": [entry_ic] if status != "rejected" else [],
            "monthly_ic": [],
            "monthly_sharpe": [],
            "current_ic": entry_ic,
            "current_sharpe": entry_sharpe,
            "consecutive_zero_ic": 0,
            "consecutive_zero_months": 0,
            "consecutive_sharpe_decline_months": 0,
            "decay_6m": 0.0,
            "decay_grade": "normal",
            "ic_slope_6m": 0.0,
            "status": status,
            "grade": grade,
            "quality_score": quality_score,
            "observation_end": observation_end,
            "last_updated": now,
        }
        atomic_write(str(self._path(factor_id)), snapshot)

        if status == "rejected":
            logger.warning(
                "因子被拒绝准入 [factor_id=%s, name=%s, grade=C, score=%.2f]",
                factor_id,
                name,
                quality_score or 0,
            )
        elif status == "observing":
            logger.info(
                "因子进入观察期 [factor_id=%s, name=%s, grade=B, obs_end=%s]",
                factor_id,
                name,
                observation_end,
            )
        else:
            logger.info(
                "因子准入 [factor_id=%s, name=%s, grade=%s, entry_ic=%.4f]",
                factor_id,
                name,
                grade,
                entry_ic,
            )
        return snapshot

    # ─── 衰减分级 (GAP-I305, v2.72.0) ───────────────────

    def decay_grade(self, ic_series: list[float]) -> DecayGrade:
        """基于滚动 IC 线性回归斜率判定衰减分级。

        使用最近 ``slope_min_points`` 个 IC 点做线性回归，斜率归一化到
        [-1.0, 1.0]（负值=衰减）。分级：
        - normal:   |slope| < ``observe_slope``
        - observe:  observe_slope <= |slope| < ``retire_slope``
        - retired:  |slope| >= ``retire_slope``

        Args:
            ic_series: 周度 IC 序列（按时间先后）

        Returns:
            DecayGrade: normal / observe / retired
        """
        slope = _calc_ic_slope_6m(ic_series, min_points=self._retire.slope_min_points)
        abs_slope = abs(slope)
        if abs_slope >= self._retire.retire_slope:
            return "retired"
        if abs_slope >= self._retire.observe_slope:
            return "observe"
        return "normal"

    def _apply_decay_grade(self, snapshot: dict, ic_series: list[float]) -> str:
        """将衰减分级写入快照并返回分级结果。

        Args:
            snapshot: 跟踪快照（可变）
            ic_series: 周度 IC 序列

        Returns:
            DecayGrade: 本次判定的分级
        """
        grade = self.decay_grade(ic_series)
        snapshot["decay_grade"] = grade
        snapshot["ic_slope_6m"] = _calc_ic_slope_6m(
            ic_series,
            min_points=self._retire.slope_min_points,
        )
        return grade

    # ─── 更新 (增强版) ────────────────────────────────────

    def update(
        self,
        factor_id: str,
        new_ic: float,
        new_sharpe: Optional[float] = None,
        is_monthly: bool = False,
    ) -> Optional[dict]:
        """更新因子跟踪数据（支持月度评估）。

        Args:
            factor_id: 因子唯一标识
            new_ic: 最新一期 IC 值
            new_sharpe: 最新一期夏普（可选）
            is_monthly: 是否为月度更新（触发月度衰减检测）

        Returns:
            更新后的 TrackingSnapshot，因子不存在时返回 None
        """
        snapshot = self.get(factor_id)
        if snapshot is None:
            logger.warning("更新失败：跟踪记录不存在 [factor_id=%s]", factor_id)
            return None

        # 被拒绝的因子不允许更新
        if snapshot.get("status") == "rejected":
            logger.debug("跳过更新：因子已被拒绝 [factor_id=%s]", factor_id)
            return snapshot

        now = datetime.now(timezone.utc).isoformat()

        # 更新周度 IC 序列
        weekly_ic = list(snapshot.get("weekly_ic", []))
        weekly_ic.append(new_ic)
        snapshot["weekly_ic"] = weekly_ic
        snapshot["current_ic"] = new_ic

        if new_sharpe is not None:
            snapshot["current_sharpe"] = new_sharpe

        # 周度连续零值 IC 计数
        if new_ic <= 0:
            snapshot["consecutive_zero_ic"] = snapshot.get("consecutive_zero_ic", 0) + 1
        else:
            snapshot["consecutive_zero_ic"] = 0

        # 衰减率计算
        snapshot["decay_6m"] = _calc_decay_6m(weekly_ic)

        # 月度更新：触发月度衰减检测
        if is_monthly:
            self._update_monthly_metrics(snapshot, new_ic, new_sharpe)

        # 状态自动转换
        self._check_state_transition(snapshot)

        snapshot["last_updated"] = now
        atomic_write(str(self._path(factor_id)), snapshot)
        return snapshot

    def _update_monthly_metrics(
        self,
        snapshot: dict,
        new_ic: float,
        new_sharpe: Optional[float],
    ) -> None:
        """更新月度指标。

        Args:
            snapshot: 跟踪快照（可变）
            new_ic: 月度 IC
            new_sharpe: 月度 Sharpe
        """
        # 追加月度 IC
        monthly_ic = list(snapshot.get("monthly_ic", []))
        monthly_ic.append(new_ic)
        snapshot["monthly_ic"] = monthly_ic

        # 月度 IC 衰减计数
        if new_ic <= 0:
            snapshot["consecutive_zero_months"] = snapshot.get("consecutive_zero_months", 0) + 1
        else:
            snapshot["consecutive_zero_months"] = 0

        # 月度 Sharpe 衰减计数
        if new_sharpe is not None:
            monthly_sharpe = list(snapshot.get("monthly_sharpe", []))
            if monthly_sharpe:
                prev_sharpe = monthly_sharpe[-1]
                if prev_sharpe > 0:
                    decline = (prev_sharpe - new_sharpe) / prev_sharpe
                    if decline > self._threshold.sharpe_decline_ratio:
                        snapshot["consecutive_sharpe_decline_months"] = (
                            snapshot.get("consecutive_sharpe_decline_months", 0) + 1
                        )
                    else:
                        snapshot["consecutive_sharpe_decline_months"] = 0
                else:
                    snapshot["consecutive_sharpe_decline_months"] = 0
            monthly_sharpe.append(new_sharpe)
            snapshot["monthly_sharpe"] = monthly_sharpe

    def _check_state_transition(self, snapshot: dict) -> None:
        """检查并执行状态转换。

        Args:
            snapshot: 跟踪快照（可变）
        """
        status = snapshot.get("status", "active")

        # 观察期 → active / decaying
        if status == "observing":
            obs_end = snapshot.get("observation_end")
            if obs_end and _is_past(obs_end):
                # 观察期结束，检查表现
                quality_score = snapshot.get("quality_score", 0)
                if quality_score is not None and quality_score >= self._threshold.b_threshold:
                    snapshot["status"] = "active"
                    logger.info(
                        "因子观察期结束，转为 active [factor_id=%s]",
                        snapshot["factor_id"],
                    )
                else:
                    snapshot["status"] = "decaying"
                    logger.warning(
                        "因子观察期结束，转为 decaying [factor_id=%s]",
                        snapshot["factor_id"],
                    )
            return

        # active / decaying → critical_decay
        if status in ("active", "decaying"):
            # 连续月度 IC < 0 → decaying
            zero_months = snapshot.get("consecutive_zero_months", 0)
            if zero_months >= self._threshold.ic_decay_months:
                snapshot["status"] = "decaying"

            # 连续月度 Sharpe 降 > 50% → critical_decay
            sharpe_decline = snapshot.get("consecutive_sharpe_decline_months", 0)
            if sharpe_decline >= self._threshold.sharpe_decline_months:
                snapshot["status"] = "critical_decay"
                logger.warning(
                    "因子进入严重衰减状态 [factor_id=%s, decline_months=%d]",
                    snapshot["factor_id"],
                    sharpe_decline,
                )

        # active → decaying (周度快速衰减)
        if status == "active":
            consec_zero = snapshot.get("consecutive_zero_ic", 0)
            if consec_zero >= 4:
                snapshot["status"] = "decaying"

    # ─── 读取 ────────────────────────────────────────────

    def get(self, factor_id: str) -> Optional[dict]:
        """从磁盘读取跟踪记录。

        Args:
            factor_id: 因子唯一标识

        Returns:
            TrackingSnapshot 或 None（不存在时）
        """
        return atomic_read(str(self._path(factor_id)), default=None)

    def list_all(self) -> list[dict]:
        """列出所有因子跟踪快照。

        Returns:
            所有 TrackingSnapshot 列表
        """
        snapshots: list[dict] = []
        for fp in sorted(self._tracking_dir.glob("*.json")):
            snapshot = atomic_read(str(fp), default=None)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    # ─── 衰减检测 ────────────────────────────────────────

    def get_decaying(self, max_consecutive: int = 4) -> list[dict]:
        """返回处于衰减边缘的活跃因子列表。

        筛选条件：连续零值 IC 次数 >= ``max_consecutive`` 且状态为 "active"。

        Args:
            max_consecutive: 连续 IC <= 0 的阈值

        Returns:
            符合条件的 TrackingSnapshot 列表
        """
        decaying: list[dict] = []
        for fp in sorted(self._tracking_dir.glob("*.json")):
            snapshot = atomic_read(str(fp), default=None)
            if (
                snapshot is not None
                and snapshot.get("status") == "active"
                and snapshot.get("consecutive_zero_ic", 0) >= max_consecutive
            ):
                decaying.append(snapshot)
        return decaying

    def get_by_status(self, status: FactorStatus) -> list[dict]:
        """按状态筛选因子。

        Args:
            status: 因子状态

        Returns:
            符合状态的 TrackingSnapshot 列表
        """
        results: list[dict] = []
        for fp in sorted(self._tracking_dir.glob("*.json")):
            snapshot = atomic_read(str(fp), default=None)
            if snapshot is not None and snapshot.get("status") == status:
                results.append(snapshot)
        return results

    # ─── 自动淘汰 (增强版) ────────────────────────────────

    def auto_retire(
        self,
        max_consecutive: int = 4,
        max_decay_6m: float = 0.30,
        min_active_days: int = 30,
    ) -> list[str]:
        """自动淘汰表现不佳的因子。

        淘汰条件（满足任一）：
        1. 连续零值 IC >= ``max_consecutive``（周度）
        2. 衰减率 >= ``max_decay_6m``
        3. 状态为 "critical_decay"
        4. 连续月度 IC < 0 超过 12 个月

        Args:
            max_consecutive: 周度连续零值 IC 阈值
            max_decay_6m: 衰减率阈值
            min_active_days: 最小活跃天数（防止过早淘汰）

        Returns:
            被淘汰的 factor_id 列表
        """
        retired_ids: list[str] = []
        now = datetime.now(timezone.utc)

        for fp in sorted(self._tracking_dir.glob("*.json")):
            snapshot = atomic_read(str(fp), default=None)
            if snapshot is None:
                continue

            status = snapshot.get("status", "active")
            if status in ("retired", "deprecated", "rejected"):
                continue

            # 检查最小活跃天数
            entry_at_str = snapshot.get("entry_at")
            if entry_at_str:
                try:
                    entry_dt = datetime.fromisoformat(entry_at_str)
                    age_days = (now - entry_dt).days
                except (ValueError, TypeError):
                    age_days = 0
            else:
                age_days = 0

            if age_days < min_active_days:
                continue

            # 判定是否应淘汰
            consecutive_zero = snapshot.get("consecutive_zero_ic", 0)
            decay_6m = snapshot.get("decay_6m", 0.0)
            zero_months = snapshot.get("consecutive_zero_months", 0)
            sharpe_decline = snapshot.get("consecutive_sharpe_decline_months", 0)
            # GAP-I305: 衰减分级 retired（滚动 IC 斜率触发）
            decay_grade = snapshot.get("decay_grade", "normal")

            should_retire = (
                consecutive_zero >= max_consecutive
                or decay_6m >= max_decay_6m
                or status == "critical_decay"
                or zero_months >= 12
                or sharpe_decline >= 12
                or decay_grade == "retired"
            )

            if should_retire:
                snapshot["status"] = "retired"
                snapshot["last_updated"] = now.isoformat()
                atomic_write(str(fp), snapshot)
                retired_ids.append(snapshot["factor_id"])
                logger.info(
                    "自动淘汰因子 [factor_id=%s, name=%s, status=%s, consec_zero=%d, "
                    "decay_6m=%.4f, zero_months=%d, sharpe_decline=%d, decay_grade=%s]",
                    snapshot["factor_id"],
                    snapshot.get("name"),
                    status,
                    consecutive_zero,
                    decay_6m,
                    zero_months,
                    sharpe_decline,
                    decay_grade,
                )

        return retired_ids

    def run_monthly_evaluation(self) -> dict:
        """执行月度增量评估。

        遍历所有因子，检查衰减状态，生成月度报告。

        Returns:
            月度评估报告摘要
        """
        all_snapshots = self.list_all()
        report: dict[str, Any] = {
            "total": len(all_snapshots),
            "status_changes": [],
            "grade_distribution": {"A": 0, "B": 0, "C": 0, "unknown": 0},
            "retired": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for snap in all_snapshots:
            grade = snap.get("grade", "unknown")
            if grade in report["grade_distribution"]:
                report["grade_distribution"][grade] += 1
            else:
                report["grade_distribution"]["unknown"] += 1

            # 检查状态转换
            old_status = snap.get("status", "active")
            self._check_state_transition(snap)
            new_status = snap.get("status", old_status)

            if old_status != new_status:
                report["status_changes"].append(
                    {
                        "factor_id": snap["factor_id"],
                        "name": snap.get("name", ""),
                        "from": old_status,
                        "to": new_status,
                    }
                )
                snap["last_updated"] = datetime.now(timezone.utc).isoformat()
                atomic_write(str(self._path(snap["factor_id"])), snap)

        # 执行自动淘汰
        report["retired"] = self.auto_retire()

        logger.info(
            "月度评估完成 [total=%d, changes=%d, retired=%d]",
            report["total"],
            len(report["status_changes"]),
            len(report["retired"]),
        )
        return report

    # ─── 报告 ────────────────────────────────────────────

    def report(self) -> dict:
        """返回所有因子的状态统计摘要。

        Returns:
            包含各状态计数的字典
        """
        counts: dict[str, int] = {
            "active": 0,
            "observing": 0,
            "decaying": 0,
            "critical_decay": 0,
            "retired": 0,
            "deprecated": 0,
            "rejected": 0,
            "total": 0,
        }
        grade_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "unknown": 0}

        for fp in self._tracking_dir.glob("*.json"):
            snapshot = atomic_read(str(fp), default=None)
            if snapshot is not None:
                status = snapshot.get("status", "active")
                counts[status] = counts.get(status, 0) + 1
                counts["total"] += 1

                grade = snapshot.get("grade", "unknown")
                grade_counts[grade] = grade_counts.get(grade, 0) + 1

        return {
            "status_counts": counts,
            "grade_distribution": grade_counts,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ─── AutoRetireManager ──────────────────────────────────────


class AutoRetireManager:
    """自动淘汰管理器。

    封装淘汰策略逻辑，支持冷却期检查和可配置参数。

    Args:
        tracker: EliteFactorTracker 实例
        config: AutoRetireConfig 配置（使用默认值若为 None）
    """

    def __init__(
        self,
        tracker: EliteFactorTracker,
        config: Optional[AutoRetireConfig] = None,
    ) -> None:
        self._tracker = tracker
        self._config = config or AutoRetireConfig()
        # GAP-I305: 若 tracker 未显式配置分级阈值，将本管理器配置同步过去，
        # 保证 decay_grade 判定与 auto_retire 使用同一套阈值。
        if tracker._retire is None or tracker._retire == AutoRetireConfig():
            tracker._retire = self._config

    def run(self) -> list[str]:
        """执行自动淘汰。

        Returns:
            被淘汰的 factor_id 列表
        """
        return self._tracker.auto_retire(
            max_consecutive=self._config.max_consecutive_zero_ic,
            max_decay_6m=self._config.max_decay_6m,
            min_active_days=self._config.min_active_days,
        )

    def can_reevaluate(self, factor_id: str) -> bool:
        """检查因子是否已过冷却期，可以重新评估。

        Args:
            factor_id: 因子唯一标识

        Returns:
            True 如果因子已淘汰且超过冷却期
        """
        snapshot = self._tracker.get(factor_id)
        if snapshot is None:
            return False

        if snapshot.get("status") not in ("retired", "deprecated"):
            return False

        last_updated_str = snapshot.get("last_updated")
        if not last_updated_str:
            return False

        try:
            last_dt = datetime.fromisoformat(last_updated_str)
            days_since = (datetime.now(timezone.utc) - last_dt).days
            return days_since >= self._config.cooldown_days
        except (ValueError, TypeError):
            return False


# ─── 内部工具 ───────────────────────────────────────────────


def _calc_decay_6m(weekly_ic: list[float]) -> float:
    """计算 IC 衰减率。

    将周度 IC 序列平分为前后两半，比较后半均值相对前半均值的下降幅度。
    公式: ``(first_half_mean - second_half_mean) / max(|first_half_mean|, 1e-8)``

    Args:
        weekly_ic: 周度 IC 序列

    Returns:
        衰减率（0 表示无衰减，正值表示衰减）
    """
    if len(weekly_ic) < 4:
        return 0.0

    mid = len(weekly_ic) // 2
    first_half = weekly_ic[:mid]
    second_half = weekly_ic[mid:]

    first_mean = sum(first_half) / len(first_half)
    second_mean = sum(second_half) / len(second_half)

    if abs(first_mean) < 1e-8:
        return abs(second_mean) if second_mean < 0 else 0.0

    decay = (first_mean - second_mean) / abs(first_mean)
    return max(decay, 0.0)


def _calc_ic_slope_6m(weekly_ic: list[float], min_points: int = 6) -> float:
    """计算滚动 6M IC 线性回归斜率（GAP-I305）。

    对最近 ``min_points`` 个 IC 点做 OLS 线性回归（x=时间序号, y=IC），
    斜率归一化到 [-1.0, 1.0]（负值=IC 衰减）。归一化尺度取 IC 序列的
    极差，避免 IC 绝对量级影响。

    Args:
        weekly_ic: 周度 IC 序列（按时间先后）
        min_points: 参与回归的最小点数（不足视为 0.0）

    Returns:
        归一化斜率（负值表示衰减，范围 [-1.0, 1.0]）
    """
    ic = list(weekly_ic)
    if len(ic) < max(min_points, 2):
        return 0.0
    y = np.asarray(ic[-min_points:], dtype=float)
    x: np.ndarray = np.arange(len(y), dtype=float)
    denom = float(np.sum((x - x.mean()) ** 2))
    if denom < 1e-12:
        return 0.0
    slope = float(np.sum((x - x.mean()) * (y - y.mean())) / denom)
    # 归一化到 [-1, 1]
    span = float(np.max(y) - np.min(y))
    if span < 1e-12:
        return 0.0
    norm = slope / span
    return float(max(-1.0, min(1.0, norm)))


def _is_past(iso_datetime: str) -> bool:
    """检查 ISO 时间戳是否已过。

    Args:
        iso_datetime: ISO 格式时间字符串

    Returns:
        True 如果时间已过
    """
    try:
        dt = datetime.fromisoformat(iso_datetime)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except (ValueError, TypeError):
        return False


__all__ = [
    "TrackingSnapshot",
    "FactorGrade",
    "FactorStatus",
    "DecayGrade",
    "GradeThreshold",
    "AutoRetireConfig",
    "EliteFactorTracker",
    "AutoRetireManager",
    "_calc_decay_6m",
    "_calc_ic_slope_6m",
]
