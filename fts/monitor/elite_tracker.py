"""
fts.monitor.elite_tracker — 精英因子样本外跟踪与自动淘汰。

Tracks elite factors post-insertion: weekly IC, decay detection, auto-retirement.

用法:
    tracker = EliteFactorTracker(tracking_dir="memory/tracking")
    tracker.init_tracker(factor_id="f_001", name="momentum", entry_ic=0.05, entry_sharpe=1.2)
    tracker.update("f_001", 0.03)
    decaying = tracker.get_decaying(max_consecutive=4)
    retired = tracker.auto_retire()
    report = tracker.report()

版本: v0.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from fts.core.atomic import atomic_read, atomic_write

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────


class TrackingSnapshot(TypedDict, total=False):
    """精英因子跟踪快照。

    存储位置: ``{tracking_dir}/{factor_id}.json``
    """
    factor_id: str                       # 因子唯一标识
    name: str                            # 人类可读名
    entry_ic: float                      # 入库时 IC
    entry_sharpe: float                  # 入库时夏普
    entry_at: str                        # ISO datetime 入库时间
    weekly_ic: list[float]               # 周度 IC 序列（追加）
    monthly_ic: list[float]              # 月度 IC 序列（追加）
    current_ic: float                    # 最近一期 IC
    current_sharpe: float                # 最近一期夏普
    consecutive_zero_ic: int             # IC <= 0 连续次数
    decay_6m: float                      # 6 个月衰减率（>0.30 表示显著衰减）
    status: str                          # "active" / "decaying" / "decayed" / "retired"
    last_updated: str                    # ISO datetime


# ─── EliteFactorTracker ─────────────────────────────────────


class EliteFactorTracker:
    """精英因子样本外跟踪器。

    为每个精英因子维护一个 ``TrackingSnapshot``，持久化到 ``tracking_dir`` 目录。
    支持 IC 追踪、衰减检测与自动淘汰。

    Args:
        tracking_dir: 跟踪快照存储目录（默认 "memory/tracking"）
    """

    def __init__(self, tracking_dir: str = "memory/tracking") -> None:
        self._tracking_dir = Path(tracking_dir)
        self._tracking_dir.mkdir(parents=True, exist_ok=True)

    # ─── 路径辅助 ────────────────────────────────────────

    def _path(self, factor_id: str) -> Path:
        return self._tracking_dir / f"{factor_id}.json"

    # ─── 初始化 ──────────────────────────────────────────

    def init_tracker(
        self,
        factor_id: str,
        name: str,
        entry_ic: float,
        entry_sharpe: float,
        entry_at: str | None = None,
    ) -> TrackingSnapshot:
        """创建新的跟踪记录。

        Args:
            factor_id: 因子唯一标识
            name: 人类可读名
            entry_ic: 入库时 IC
            entry_sharpe: 入库时夏普
            entry_at: 入库时间（ISO 格式，默认当前 UTC 时间）

        Returns:
            新创建的 TrackingSnapshot
        """
        now = entry_at or datetime.now(timezone.utc).isoformat()

        snapshot: TrackingSnapshot = TrackingSnapshot(
            factor_id=factor_id,
            name=name,
            entry_ic=entry_ic,
            entry_sharpe=entry_sharpe,
            entry_at=now,
            weekly_ic=[entry_ic],
            monthly_ic=[],
            current_ic=entry_ic,
            current_sharpe=entry_sharpe,
            consecutive_zero_ic=0,
            decay_6m=0.0,
            status="active",
            last_updated=now,
        )
        atomic_write(str(self._path(factor_id)), snapshot)
        logger.info("初始化跟踪记录 [factor_id=%s, name=%s, entry_ic=%.4f]", factor_id, name, entry_ic)
        return snapshot

    # ─── 更新 ────────────────────────────────────────────

    def update(
        self,
        factor_id: str,
        new_ic: float,
        new_sharpe: float | None = None,
    ) -> TrackingSnapshot | None:
        """更新因子跟踪数据。

        追加周度 IC、更新当前 IC/夏普、累计零值次数、计算衰减率。

        Args:
            factor_id: 因子唯一标识
            new_ic: 最新一期 IC 值
            new_sharpe: 最新一期夏普（可选）

        Returns:
            更新后的 TrackingSnapshot，因子不存在时返回 None
        """
        snapshot = self.get(factor_id)
        if snapshot is None:
            logger.warning("更新失败：跟踪记录不存在 [factor_id=%s]", factor_id)
            return None

        now = datetime.now(timezone.utc).isoformat()

        # 更新 IC 序列与当前值
        weekly_ic = list(snapshot.get("weekly_ic", []))
        weekly_ic.append(new_ic)
        snapshot["weekly_ic"] = weekly_ic
        snapshot["current_ic"] = new_ic
        if new_sharpe is not None:
            snapshot["current_sharpe"] = new_sharpe

        # 连续零值 IC 计数
        if new_ic <= 0:
            snapshot["consecutive_zero_ic"] = snapshot.get("consecutive_zero_ic", 0) + 1
        else:
            snapshot["consecutive_zero_ic"] = 0

        # 衰减率计算（至少 4 期数据）
        snapshot["decay_6m"] = _calc_decay_6m(weekly_ic)

        # 状态自动转换
        if snapshot.get("status") == "active" and snapshot["consecutive_zero_ic"] >= 4:
            snapshot["status"] = "decaying"

        snapshot["last_updated"] = now

        atomic_write(str(self._path(factor_id)), snapshot)
        return snapshot

    # ─── 读取 ────────────────────────────────────────────

    def get(self, factor_id: str) -> TrackingSnapshot | None:
        """从磁盘读取跟踪记录。

        Args:
            factor_id: 因子唯一标识

        Returns:
            TrackingSnapshot 或 None（不存在时）
        """
        return atomic_read(str(self._path(factor_id)), default=None)

    # ─── 衰减检测 ────────────────────────────────────────

    def get_decaying(self, max_consecutive: int = 4) -> list[TrackingSnapshot]:
        """返回处于衰减边缘的活跃因子列表。

        筛选条件：连续零值 IC 次数 >= ``max_consecutive`` 且状态为 "active"。

        Args:
            max_consecutive: 连续 IC <= 0 的阈值

        Returns:
            符合条件的 TrackingSnapshot 列表
        """
        decaying: list[TrackingSnapshot] = []
        for fp in sorted(self._tracking_dir.glob("*.json")):
            snapshot = atomic_read(str(fp), default=None)
            if (
                snapshot is not None
                and snapshot.get("status") == "active"
                and snapshot.get("consecutive_zero_ic", 0) >= max_consecutive
            ):
                decaying.append(snapshot)
        return decaying

    # ─── 自动淘汰 ────────────────────────────────────────

    def auto_retire(
        self,
        max_consecutive: int = 4,
        max_decay_6m: float = 0.30,
        min_active_days: int = 30,
    ) -> list[str]:
        """自动淘汰表现不佳的因子。

        淘汰条件（同时满足）：
        1. 因子状态为 "active" 或 "decaying"
        2. 连续零值 IC >= ``max_consecutive`` **或** 衰减率 >= ``max_decay_6m``
        3. 入库时间 >= ``min_active_days`` 天

        Args:
            max_consecutive: 连续零值 IC 阈值
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
            if status in ("retired", "decayed"):
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

            if consecutive_zero >= max_consecutive or decay_6m >= max_decay_6m:
                snapshot["status"] = "retired"
                snapshot["last_updated"] = now.isoformat()
                atomic_write(str(fp), snapshot)
                retired_ids.append(snapshot["factor_id"])
                logger.info(
                    "自动淘汰因子 [factor_id=%s, name=%s, consecutive_zero=%d, decay_6m=%.4f]",
                    snapshot["factor_id"], snapshot.get("name"),
                    consecutive_zero, decay_6m,
                )

        return retired_ids

    # ─── 报告 ────────────────────────────────────────────

    def report(self) -> dict:
        """返回所有因子的状态统计摘要。

        Returns:
            包含各状态计数的字典
        """
        counts: dict[str, int] = {
            "active": 0,
            "decaying": 0,
            "decayed": 0,
            "retired": 0,
            "total": 0,
        }
        for fp in self._tracking_dir.glob("*.json"):
            snapshot = atomic_read(str(fp), default=None)
            if snapshot is not None:
                status = snapshot.get("status", "active")
                counts[status] = counts.get(status, 0) + 1
                counts["total"] += 1

        return counts


# ─── AutoRetireManager ──────────────────────────────────────


@dataclass
class AutoRetireConfig:
    """自动淘汰配置。"""
    max_consecutive_zero_ic: int = 4         # 连续零值 IC 阈值
    max_decay_6m: float = 0.30              # 衰减率阈值
    min_active_days: int = 30               # 最小活跃天数
    cooldown_days: int = 7                  # 冷却期（淘汰后多久可重新评估）


class AutoRetireManager:
    """自动淘汰管理器。

    封装淘汰策略逻辑，支持冷却期检查和可配置参数。

    Args:
        tracker: EliteFactorTracker 实例
        config: AutoRetireConfig 配置（使用默认值若为 None）
    """

    def __init__(self, tracker: EliteFactorTracker, config: AutoRetireConfig | None = None) -> None:
        self._tracker = tracker
        self._config = config or AutoRetireConfig()

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

        if snapshot.get("status") not in ("retired", "decayed"):
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


__all__ = [
    "TrackingSnapshot",
    "EliteFactorTracker",
    "AutoRetireConfig",
    "AutoRetireManager",
]
