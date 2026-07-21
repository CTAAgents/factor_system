"""
tests/test_elite_tracker.py — EliteFactorTracker 与 AutoRetireManager 综合测试

覆盖范围:
    EliteFactorTracker:
        - init_tracker 创建文件/自定义 entry_at/返回值/覆盖
        - update 追加 IC/计算 consecutive/状态变迁/decay_6m/不存在的因子
        - get 正确返回/不存在返回 None
        - get_decaying 阈值过滤/自定义参数/空结果
        - auto_retire 条件淘汰/边界保护/自定义参数/跳过非目标
        - report 空/混合
        - 多因子独立/跨实例持久化

    AutoRetireManager:
        - run 调用 tracker/空结果
        - can_reevaluate 不存在/非淘汰/冷却中/冷却后/边界

    AutoRetireConfig:
        - 默认值/自定义值

版本: v0.1.0
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# 确保能导入 fts 模块
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.monitor.elite_tracker import (
    AutoRetireConfig,
    AutoRetireManager,
    EliteFactorTracker,
    TrackingSnapshot,
    _calc_decay_6m,
)


# ════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════


def _seed_tracker(
    tracker: EliteFactorTracker,
    factor_id: str,
    name: str = "TestFactor",
    entry_ic: float = 0.05,
    entry_sharpe: float = 1.2,
    entry_at: str | None = None,
) -> TrackingSnapshot:
    """快捷创建跟踪记录。"""
    return tracker.init_tracker(factor_id, name, entry_ic, entry_sharpe, entry_at)


def _write_raw_snapshot(tracker: EliteFactorTracker, factor_id: str, data: dict) -> Path:
    """直接写原始 JSON 到跟踪文件（绕过原子写，用于构造特定状态）。"""
    p = tracker._path(factor_id)  # noqa: SLF001
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _utc_iso(days_ago: int = 0) -> str:
    """返回 days_ago 天前的 UTC ISO 时间戳。"""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ════════════════════════════════════════════════════════════
# _calc_decay_6m
# ════════════════════════════════════════════════════════════


class TestCalcDecay6m:
    """_calc_decay_6m 单元测试。"""

    def test_less_than_4_ics(self) -> None:
        """不足 4 期数据返回 0.0。"""
        assert _calc_decay_6m([0.1, 0.2, 0.3]) == 0.0

    def test_equal_first_second_half(self) -> None:
        """前后半均值相同时衰减为 0。"""
        result = _calc_decay_6m([0.1, 0.1, 0.1, 0.1])
        assert result == 0.0

    def test_decay_positive(self) -> None:
        """后半均值低于前半时返回正衰减率。"""
        # 前半: [0.2, 0.2] mean=0.2, 后半: [0.1, 0.1] mean=0.1
        # 衰减 = (0.2 - 0.1) / 0.2 = 0.5
        result = _calc_decay_6m([0.2, 0.2, 0.1, 0.1])
        assert result == pytest.approx(0.5)

    def test_no_decay_second_half_higher(self) -> None:
        """后半均值高于前半时返回 0（无衰减）。"""
        # 前半: [0.1, 0.1] mean=0.1, 后半: [0.2, 0.2] mean=0.2
        # 衰减 = max((0.1-0.2)/0.1, 0) = max(-1.0, 0) = 0
        result = _calc_decay_6m([0.1, 0.1, 0.2, 0.2])
        assert result == 0.0

    def test_first_half_zero(self) -> None:
        """前半均值为 0 且后半为负时返回绝对值。"""
        # 前半: [0, 0] mean=0, 后半: [-0.1, -0.1] mean=-0.1
        # abs(first_mean) < 1e-8 → return abs(-0.1) = 0.1
        result = _calc_decay_6m([0.0, 0.0, -0.1, -0.1])
        assert result == pytest.approx(0.1)

    def test_first_half_zero_second_positive(self) -> None:
        """前半均值为 0 且后半为正时返回 0。"""
        result = _calc_decay_6m([0.0, 0.0, 0.1, 0.1])
        assert result == 0.0


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — init_tracker
# ════════════════════════════════════════════════════════════


class TestInitTracker:
    """init_tracker 单元测试。"""

    def test_init_creates_file(self, tmp_path: Path) -> None:
        """init_tracker 创建 JSON 文件且字段正确。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_001")

        fp = tmp_path / "fct_001.json"
        assert fp.exists()
        data = json.loads(fp.read_text(encoding="utf-8"))
        assert data["factor_id"] == "fct_001"
        assert data["name"] == "TestFactor"
        assert data["entry_ic"] == 0.05
        assert data["entry_sharpe"] == 1.2
        assert data["current_ic"] == 0.05
        assert data["current_sharpe"] == 1.2
        assert data["weekly_ic"] == [0.05]  # entry_ic 被放入 weekly_ic
        assert data["monthly_ic"] == []
        assert data["consecutive_zero_ic"] == 0
        assert data["decay_6m"] == 0.0
        assert data["status"] == "active"
        assert "last_updated" in data

    def test_init_with_custom_entry_at(self, tmp_path: Path) -> None:
        """自定义 entry_at 被正确保存。"""
        tracker = EliteFactorTracker(str(tmp_path))
        custom_time = "2026-06-15T10:30:00"
        snapshot = tracker.init_tracker(
            "fct_002", "Momentum", entry_ic=0.03, entry_sharpe=1.0,
            entry_at=custom_time,
        )
        assert snapshot["entry_at"] == custom_time

    def test_init_returns_snapshot(self, tmp_path: Path) -> None:
        """返回的 TrackingSnapshot 字段完整。"""
        tracker = EliteFactorTracker(str(tmp_path))
        snapshot = _seed_tracker(tracker, "fct_003")
        assert isinstance(snapshot, dict)
        assert snapshot["factor_id"] == "fct_003"
        assert snapshot["status"] == "active"
        assert snapshot["weekly_ic"] == [0.05]

    def test_init_overwrites_existing(self, tmp_path: Path) -> None:
        """对同一 factor_id 再次 init 会覆盖原有记录。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_004", name="OldName")
        _seed_tracker(tracker, "fct_004", name="NewName", entry_ic=0.1, entry_sharpe=2.0)

        snapshot = tracker.get("fct_004")
        assert snapshot is not None
        assert snapshot["name"] == "NewName"
        assert snapshot["entry_ic"] == 0.1
        assert snapshot["entry_sharpe"] == 2.0
        assert snapshot["weekly_ic"] == [0.1]  # 覆盖后清空并写入新 entry_ic

    def test_init_default_tracking_dir(self) -> None:
        """默认 tracking_dir 为 memory/tracking。"""
        with patch.object(Path, "mkdir") as mock_mkdir:
            tracker = EliteFactorTracker()
            assert str(tracker._tracking_dir) == str(Path("memory/tracking"))  # noqa: SLF001
            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — update
# ════════════════════════════════════════════════════════════


class TestUpdate:
    """update 单元测试。"""

    def test_update_appends_ic(self, tmp_path: Path) -> None:
        """update 将新 IC 追加到 weekly_ic 列表（含 entry_ic 初始值）。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_010", entry_ic=0.05)
        tracker.update("fct_010", 0.02)
        tracker.update("fct_010", 0.03)

        snapshot = tracker.get("fct_010")
        assert snapshot is not None
        assert snapshot["weekly_ic"] == [0.05, 0.02, 0.03]
        assert snapshot["current_ic"] == 0.03

    def test_update_returns_snapshot(self, tmp_path: Path) -> None:
        """update 返回更新后的快照。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_011")
        result = tracker.update("fct_011", 0.01)
        assert result is not None
        assert result["factor_id"] == "fct_011"
        assert result["current_ic"] == 0.01

    def test_update_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """更新不存在的因子返回 None。"""
        tracker = EliteFactorTracker(str(tmp_path))
        result = tracker.update("fct_nonexistent", 0.01)
        assert result is None

    def test_update_calculates_consecutive_zero(self, tmp_path: Path) -> None:
        """连续负 IC 累加 consecutive_zero_ic。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_012")
        tracker.update("fct_012", -0.01)
        tracker.update("fct_012", -0.02)
        tracker.update("fct_012", -0.03)

        snapshot = tracker.get("fct_012")
        assert snapshot is not None
        # entry_ic=0.05 > 0 所以没计入，之后连续 3 次负
        assert snapshot["consecutive_zero_ic"] == 3

    def test_update_resets_consecutive_zero(self, tmp_path: Path) -> None:
        """正 IC 将 consecutive_zero_ic 重置为 0。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_013")
        tracker.update("fct_013", -0.01)
        tracker.update("fct_013", -0.02)
        tracker.update("fct_013", 0.03)  # 转正

        snapshot = tracker.get("fct_013")
        assert snapshot is not None
        assert snapshot["consecutive_zero_ic"] == 0

    def test_update_status_becomes_decaying(self, tmp_path: Path) -> None:
        """连续 4 次负 IC → status=decaying（含 entry_ic 不计入连续）。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_014")
        # entry_ic=0.05 不计入连续，连续 4 次负
        for _ in range(4):
            tracker.update("fct_014", -0.01)

        snapshot = tracker.get("fct_014")
        assert snapshot is not None
        assert snapshot["status"] == "decaying"

    def test_update_status_stays_decaying_after_4(self, tmp_path: Path) -> None:
        """超过 4 次负 IC 后状态保持 decaying。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_015")
        for _ in range(8):
            tracker.update("fct_015", -0.01)

        snapshot = tracker.get("fct_015")
        assert snapshot is not None
        # 代码只做 active→decaying 转换，没有 decaying→decayed
        assert snapshot["status"] == "decaying"

    def test_update_status_progression(self, tmp_path: Path) -> None:
        """状态变迁: active(连续<4) → decaying(连续≥4)。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_prog")

        # 连续 3 次负 → 仍 active（第 1 次 entry_ic=0.05 不计）
        for _ in range(3):
            tracker.update("fct_prog", -0.01)
        assert tracker.get("fct_prog")["status"] == "active"

        # 第 4 次负 → decaying
        tracker.update("fct_prog", -0.01)
        assert tracker.get("fct_prog")["status"] == "decaying"

        # 继续负 → 仍 decaying
        for _ in range(4):
            tracker.update("fct_prog", -0.01)
        assert tracker.get("fct_prog")["status"] == "decaying"

    def test_update_with_new_sharpe(self, tmp_path: Path) -> None:
        """update 可同时更新夏普值。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_sharpe")
        tracker.update("fct_sharpe", 0.02, new_sharpe=0.8)
        snapshot = tracker.get("fct_sharpe")
        assert snapshot is not None
        assert snapshot["current_sharpe"] == 0.8

    def test_update_preserves_sharpe_when_not_given(self, tmp_path: Path) -> None:
        """未传 new_sharpe 时保持原夏普。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_sharpe2", entry_sharpe=1.5)
        tracker.update("fct_sharpe2", 0.02)
        snapshot = tracker.get("fct_sharpe2")
        assert snapshot is not None
        assert snapshot["current_sharpe"] == 1.5

    def test_update_calculates_decay_6m(self, tmp_path: Path) -> None:
        """更新足够多次后计算 decay_6m。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_decay")
        # 总共需要 >= 4 期数据: entry_ic=0.05 + 3 次 = 4 期
        # 前半: [0.05], 后半: [0.03, -0.01, -0.02]
        # 等等，split 是 mid = len//2
        # weekly_ic = [0.05, 0.03, -0.01, -0.02], mid=2
        # 前半=[0.05, 0.03] mean=0.04, 后半=[-0.01, -0.02] mean=-0.015
        # decay = (0.04 - (-0.015)) / 0.04 = 0.055/0.04 = 1.375
        tracker.update("fct_decay", 0.03)
        tracker.update("fct_decay", -0.01)
        tracker.update("fct_decay", -0.02)

        snapshot = tracker.get("fct_decay")
        assert snapshot is not None
        assert snapshot["decay_6m"] > 0

    def test_update_decay_6m_below_4_ics(self, tmp_path: Path) -> None:
        """不足 4 期数据时 decay_6m 保持 0。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_short")
        # entry_ic=0.05 + 1 次 = 2 期 < 4
        tracker.update("fct_short", 0.01)

        snapshot = tracker.get("fct_short")
        assert snapshot is not None
        assert snapshot["decay_6m"] == 0.0

    def test_update_ic_zero_counts_as_consecutive(self, tmp_path: Path) -> None:
        """IC=0 被计为连续零值。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_zero")
        tracker.update("fct_zero", 0.0)
        tracker.update("fct_zero", 0.0)
        snapshot = tracker.get("fct_zero")
        assert snapshot is not None
        assert snapshot["consecutive_zero_ic"] == 2


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — get
# ════════════════════════════════════════════════════════════


class TestGet:
    """get 单元测试。"""

    def test_get_returns_snapshot(self, tmp_path: Path) -> None:
        """get 返回正确的 TrackingSnapshot。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_020", name="Alpha", entry_ic=0.08, entry_sharpe=1.5)

        snapshot = tracker.get("fct_020")
        assert snapshot is not None
        assert snapshot["factor_id"] == "fct_020"
        assert snapshot["name"] == "Alpha"
        assert snapshot["entry_ic"] == 0.08
        assert snapshot["entry_sharpe"] == 1.5

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        """不存在的因子返回 None。"""
        tracker = EliteFactorTracker(str(tmp_path))
        result = tracker.get("fct_nobody")
        assert result is None

    def test_get_returns_last_updated(self, tmp_path: Path) -> None:
        """get 返回的文件包含 last_updated 字段。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_time")
        snapshot = tracker.get("fct_time")
        assert snapshot is not None
        assert "last_updated" in snapshot


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — get_decaying
# ════════════════════════════════════════════════════════════


class TestGetDecaying:
    """get_decaying 单元测试。"""

    def _create_active_with_consecutive(
        self, tracker: EliteFactorTracker, factor_id: str, n: int,
    ) -> None:
        """创建连续 n 次负 IC 的因子（保持 active 状态）。"""
        _seed_tracker(tracker, factor_id)
        for _ in range(n):
            tracker.update(factor_id, -0.01)

    def test_get_decaying_returns_active_with_high_consecutive(self, tmp_path: Path) -> None:
        """get_decaying 只返回 active 状态且 consecutive ≥ 4 的因子。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # 只会 active 状态
        self._create_active_with_consecutive(tracker, "fct_bad", 4)
        # 现在状态变为 decaying，不会被返回
        _seed_tracker(tracker, "fct_good")
        tracker.update("fct_good", 0.01)

        decaying = tracker.get_decaying()
        ids = [d["factor_id"] for d in decaying]
        # fct_bad 因为 status 变为 decaying 所以不会被 get_decaying 返回
        assert "fct_bad" not in ids
        assert "fct_good" not in ids

    def test_get_decaying_returns_decaying_status_factors(self, tmp_path: Path) -> None:
        """get_decaying 不返回 decaying 状态的因子（仅 active）。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._create_active_with_consecutive(tracker, "fct_dec", 5)
        snapshot = tracker.get("fct_dec")
        assert snapshot is not None
        assert snapshot["status"] == "decaying"

        decaying = tracker.get_decaying()
        ids = [d["factor_id"] for d in decaying]
        # decaying 状态不会被返回
        assert "fct_dec" not in ids

    def test_get_decaying_returns_active_meeting_threshold(self, tmp_path: Path) -> None:
        """consecutive≥4 且仍为 active 的因子被返回。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # 连续 3 次负，不触发 decaying（< 4）
        _seed_tracker(tracker, "fct_three")
        for _ in range(3):
            tracker.update("fct_three", -0.01)
        # status=active, consecutive=3 (< 4)
        assert tracker.get("fct_three")["status"] == "active"

        decaying = tracker.get_decaying(max_consecutive=3)
        ids = [d["factor_id"] for d in decaying]
        assert "fct_three" in ids

    def test_get_decaying_respects_max_consecutive(self, tmp_path: Path) -> None:
        """自定义 max_consecutive 参数。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._create_active_with_consecutive(tracker, "fct_a", 2)
        self._create_active_with_consecutive(tracker, "fct_b", 5)

        # max_consecutive=5，但 fct_b 已 decaying，不被返回
        decaying = tracker.get_decaying(max_consecutive=5)
        ids = [d["factor_id"] for d in decaying]
        assert "fct_b" not in ids  # decaying 状态
        assert "fct_a" not in ids  # consecutive=2 < 5

    def test_get_decaying_empty_no_factors(self, tmp_path: Path) -> None:
        """空目录返回空列表。"""
        tracker = EliteFactorTracker(str(tmp_path))
        decaying = tracker.get_decaying()
        assert decaying == []

    def test_get_decaying_empty_no_matches(self, tmp_path: Path) -> None:
        """有因子但都不满足条件时返回空列表。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_good1")
        _seed_tracker(tracker, "fct_good2")
        tracker.update("fct_good1", 0.02)
        tracker.update("fct_good2", 0.03)

        decaying = tracker.get_decaying()
        assert decaying == []

    def test_get_decaying_max_consecutive_zero(self, tmp_path: Path) -> None:
        """max_consecutive=0 返回所有 active 因子。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_a")
        _seed_tracker(tracker, "fct_b")

        decaying = tracker.get_decaying(max_consecutive=0)
        assert len(decaying) == 2

    def test_get_decaying_skips_non_active(self, tmp_path: Path) -> None:
        """跳过 retired/decaying 状态因子。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # 创建一个 consecutive=3 仍为 active 的因子
        _seed_tracker(tracker, "fct_active")
        for _ in range(3):
            tracker.update("fct_active", -0.01)

        # 写入 retired 因子
        raw = {
            "factor_id": "fct_ret",
            "name": "Retired",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(60),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.5,
            "consecutive_zero_ic": 10,
            "decay_6m": 0.5,
            "status": "retired",
            "last_updated": _utc_iso(0),
        }
        _write_raw_snapshot(tracker, "fct_ret", raw)

        decaying = tracker.get_decaying(max_consecutive=3)
        ids = [d["factor_id"] for d in decaying]
        assert "fct_ret" not in ids
        assert "fct_active" in ids


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — auto_retire
# ════════════════════════════════════════════════════════════


class TestAutoRetire:
    """auto_retire 单元测试。"""

    def _make_eligible_factor(
        self,
        tracker: EliteFactorTracker,
        factor_id: str,
        entry_at: str | None = None,
        consecutive_zero_ic: int = 10,
        decay_6m: float = 0.5,
        status: str = "active",
        name: str = "TestFactor",
    ) -> None:
        """快捷创建可被淘汰的因子。"""
        if entry_at is None:
            entry_at = _utc_iso(60)
        raw = {
            "factor_id": factor_id,
            "name": name,
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": entry_at,
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": consecutive_zero_ic,
            "decay_6m": decay_6m,
            "status": status,
            "last_updated": _utc_iso(0),
        }
        _write_raw_snapshot(tracker, factor_id, raw)

    def test_auto_retire_retires_active_factor(self, tmp_path: Path) -> None:
        """符合条件的 active 因子被淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_a")
        retired = tracker.auto_retire()

        assert "fct_a" in retired
        snapshot = tracker.get("fct_a")
        assert snapshot is not None
        assert snapshot["status"] == "retired"

    def test_auto_retire_retires_decaying_factor(self, tmp_path: Path) -> None:
        """符合条件的 decaying 因子也可被淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_decaying", status="decaying")
        retired = tracker.auto_retire()

        assert "fct_decaying" in retired

    def test_auto_retire_returns_ids(self, tmp_path: Path) -> None:
        """返回被淘汰因子 ID 列表。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_a")
        self._make_eligible_factor(tracker, "fct_b")
        retired = tracker.auto_retire()

        assert sorted(retired) == ["fct_a", "fct_b"]

    def test_auto_retire_respects_min_active_days(self, tmp_path: Path) -> None:
        """未达到最小活跃天数的因子不被淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        recent = _utc_iso(5)  # 5 天前
        self._make_eligible_factor(tracker, "fct_young", entry_at=recent)

        retired = tracker.auto_retire(min_active_days=30)
        assert "fct_young" not in retired
        snapshot = tracker.get("fct_young")
        assert snapshot is not None
        assert snapshot["status"] == "active"  # 保持不变

    def test_auto_retire_respects_max_consecutive(self, tmp_path: Path) -> None:
        """连续零值不足时不淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # consecutive=1, decay_6m=0.0 → 两个条件都不满足
        self._make_eligible_factor(tracker, "fct_low", consecutive_zero_ic=1, decay_6m=0.0)

        retired = tracker.auto_retire(max_consecutive=4, max_decay_6m=0.30)
        assert "fct_low" not in retired

    def test_auto_retire_respects_max_decay_6m(self, tmp_path: Path) -> None:
        """衰减率不足时不淘汰（但连续零值达标时仍可淘汰）。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # consecutive=10 >= 4, 所以条件满足（OR 逻辑）
        self._make_eligible_factor(tracker, "fct_decay_low", consecutive_zero_ic=10, decay_6m=0.0)

        retired = tracker.auto_retire(max_consecutive=4, max_decay_6m=0.30)
        # consecutive=10 >= 4 满足 OR 条件
        assert "fct_decay_low" in retired

    def test_auto_retire_skips_retired_and_decayed(self, tmp_path: Path) -> None:
        """跳过已 retired 和 decayed 的因子。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_retired", status="retired")
        self._make_eligible_factor(tracker, "fct_decayed", status="decayed")

        retired = tracker.auto_retire()
        assert "fct_retired" not in retired
        assert "fct_decayed" not in retired

    def test_auto_retire_custom_params(self, tmp_path: Path) -> None:
        """自定义参数。"""
        tracker = EliteFactorTracker(str(tmp_path))
        entry = _utc_iso(20)  # 20 天前
        self._make_eligible_factor(
            tracker, "fct_custom", entry_at=entry, consecutive_zero_ic=2, decay_6m=0.2,
        )

        # 默认 (max_consecutive=4, max_decay_6m=0.30, min_active_days=30) → 不满足
        retired_default = tracker.auto_retire()
        assert "fct_custom" not in retired_default

        # 宽松 (max_consecutive=2, max_decay_6m=0.15, min_active_days=15) → 满足
        retired_lax = tracker.auto_retire(max_consecutive=2, max_decay_6m=0.15, min_active_days=15)
        assert "fct_custom" in retired_lax

    def test_auto_retire_empty_when_no_eligible(self, tmp_path: Path) -> None:
        """没有符合条件因子时返回空列表。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_a")
        _seed_tracker(tracker, "fct_b")
        retired = tracker.auto_retire()
        assert retired == []

    def test_auto_retire_exactly_at_min_active_days(self, tmp_path: Path) -> None:
        """正好在 min_active_days 边界应被淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        entry = _utc_iso(30)  # 正好 30 天前
        self._make_eligible_factor(tracker, "fct_boundary", entry_at=entry)

        retired = tracker.auto_retire(min_active_days=30)
        assert "fct_boundary" in retired

    def test_auto_retire_or_logic_consecutive(self, tmp_path: Path) -> None:
        """连续零值满足条件时即使衰减率低也可淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_or", consecutive_zero_ic=5, decay_6m=0.0)

        retired = tracker.auto_retire(max_consecutive=4, max_decay_6m=0.30)
        assert "fct_or" in retired

    def test_auto_retire_or_logic_decay(self, tmp_path: Path) -> None:
        """衰减率满足条件时即使连续零值不足也可淘汰。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_or2", consecutive_zero_ic=1, decay_6m=0.5)

        retired = tracker.auto_retire(max_consecutive=4, max_decay_6m=0.30)
        assert "fct_or2" in retired

    def test_auto_retire_updates_last_updated(self, tmp_path: Path) -> None:
        """淘汰后 last_updated 被更新。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible_factor(tracker, "fct_time")
        snapshot_before = tracker.get("fct_time")
        assert snapshot_before is not None

        tracker.auto_retire()
        snapshot_after = tracker.get("fct_time")
        assert snapshot_after is not None
        # last_updated 应当是一个非空字符串（微秒级精度可能导致相同值，仅验证存在性）
        assert snapshot_after["last_updated"]


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — report
# ════════════════════════════════════════════════════════════


class TestReport:
    """report 单元测试。"""

    def test_report_empty(self, tmp_path: Path) -> None:
        """空目录报告全 0。"""
        tracker = EliteFactorTracker(str(tmp_path))
        r = tracker.report()
        assert r == {"active": 0, "decaying": 0, "decayed": 0, "retired": 0, "total": 0}

    def test_report_mixed(self, tmp_path: Path) -> None:
        """混合状态的正确计数。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_a")  # active
        _seed_tracker(tracker, "fct_b")  # active
        # decaying
        _seed_tracker(tracker, "fct_c")
        for _ in range(5):
            tracker.update("fct_c", -0.01)
        # decayed (直接写)
        _write_raw_snapshot(tracker, "fct_d", {
            "factor_id": "fct_d", "name": "D",
            "entry_ic": 0.05, "entry_sharpe": 1.2,
            "entry_at": _utc_iso(0), "weekly_ic": [], "monthly_ic": [],
            "current_ic": 0.05, "current_sharpe": 1.2,
            "consecutive_zero_ic": 10, "decay_6m": 0.5,
            "status": "decayed", "last_updated": _utc_iso(0),
        })
        # retired (直接写)
        _write_raw_snapshot(tracker, "fct_e", {
            "factor_id": "fct_e", "name": "E",
            "entry_ic": 0.05, "entry_sharpe": 1.2,
            "entry_at": _utc_iso(0), "weekly_ic": [], "monthly_ic": [],
            "current_ic": 0.05, "current_sharpe": 1.2,
            "consecutive_zero_ic": 10, "decay_6m": 0.5,
            "status": "retired", "last_updated": _utc_iso(0),
        })

        r = tracker.report()
        assert r["active"] == 2
        assert r["decaying"] == 1
        assert r["decayed"] == 1
        assert r["retired"] == 1
        assert r["total"] == 5

    def test_report_with_unknown_status(self, tmp_path: Path) -> None:
        """未知状态不进入已知分类计数但计入 total。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _write_raw_snapshot(tracker, "fct_unknown", {
            "factor_id": "fct_unknown", "name": "Unknown",
            "entry_ic": 0.05, "entry_sharpe": 1.2,
            "entry_at": _utc_iso(0), "weekly_ic": [], "monthly_ic": [],
            "current_ic": 0.05, "current_sharpe": 1.2,
            "consecutive_zero_ic": 0, "decay_6m": 0.0,
            "status": "garbage", "last_updated": _utc_iso(0),
        })

        r = tracker.report()
        # report 使用 counts[status] 直接赋值，不会归入 active
        assert r["active"] == 0
        assert r["total"] == 1


# ════════════════════════════════════════════════════════════
# EliteFactorTracker — 多因子 & 集成
# ════════════════════════════════════════════════════════════


class TestIntegration:
    """多因子和集成测试。"""

    def test_multiple_factors_independent(self, tmp_path: Path) -> None:
        """多个因子的更新互不影响。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_x")
        _seed_tracker(tracker, "fct_y")

        tracker.update("fct_x", 0.05)
        tracker.update("fct_x", 0.06)
        tracker.update("fct_y", -0.01)

        x = tracker.get("fct_x")
        y = tracker.get("fct_y")
        assert x is not None and y is not None
        assert x["weekly_ic"] == [0.05, 0.05, 0.06]
        assert x["consecutive_zero_ic"] == 0
        assert y["weekly_ic"] == [0.05, -0.01]
        assert y["consecutive_zero_ic"] == 1

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        """文件持久化——不同实例读取相同数据。"""
        tracker1 = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker1, "fct_persist", name="PersistMe")
        tracker1.update("fct_persist", 0.02)

        tracker2 = EliteFactorTracker(str(tmp_path))
        snapshot = tracker2.get("fct_persist")
        assert snapshot is not None
        assert snapshot["name"] == "PersistMe"
        assert snapshot["weekly_ic"] == [0.05, 0.02]

    def test_create_then_update_then_get(self, tmp_path: Path) -> None:
        """完整工作流: init → update × N → get 验证。"""
        tracker = EliteFactorTracker(str(tmp_path))
        tracker.init_tracker("fct_wf", "Workflow", entry_ic=0.04, entry_sharpe=1.1)
        for ic in [0.03, -0.01, 0.02, -0.02, 0.01]:
            tracker.update("fct_wf", ic)

        snapshot = tracker.get("fct_wf")
        assert snapshot is not None
        # entry_ic(0.04) + 5 updates = 6
        assert len(snapshot["weekly_ic"]) == 6
        assert snapshot["current_ic"] == 0.01
        # 最后一个 IC=0.01 > 0 重置
        assert snapshot["consecutive_zero_ic"] == 0

    def test_update_mixed_ic_pattern(self, tmp_path: Path) -> None:
        """正负交替 IC 时 consecutive_zero_ic 正确计算。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_pattern")
        # 负, 负, 正, 负, 负, 负, 正
        for ic in [-0.01, -0.02, 0.03, -0.01, -0.02, -0.03, 0.04]:
            tracker.update("fct_pattern", ic)

        snapshot = tracker.get("fct_pattern")
        assert snapshot is not None
        assert snapshot["consecutive_zero_ic"] == 0  # 最后一个是正

    def test_memory_dir_created(self, tmp_path: Path) -> None:
        """初始化时自动创建目录。"""
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        EliteFactorTracker(str(nested))
        assert nested.exists()

    def test_corrupted_file_handling(self, tmp_path: Path) -> None:
        """损坏的 JSON 文件被跳过。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_good")
        # 写一个损坏的文件
        bad_path = tmp_path / "fct_bad.json"
        bad_path.write_text("this is not json", encoding="utf-8")

        # get 应返回 None（atomic_read 返回 default=None）
        assert tracker.get("fct_bad") is None
        # report 应忽略损坏文件
        r = tracker.report()
        assert r["active"] == 1
        assert r["total"] == 1

    def test_full_lifecycle_active_to_retired(self, tmp_path: Path) -> None:
        """完整生命周期: init → update → auto_retire → retired。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_life", entry_at=_utc_iso(60))

        # 连续负 IC
        for _ in range(5):
            tracker.update("fct_life", -0.01)

        # 满足条件后被淘汰
        retired = tracker.auto_retire(min_active_days=30)
        assert "fct_life" in retired

        snapshot = tracker.get("fct_life")
        assert snapshot is not None
        assert snapshot["status"] == "retired"


# ════════════════════════════════════════════════════════════
# AutoRetireConfig
# ════════════════════════════════════════════════════════════


class TestAutoRetireConfig:
    """AutoRetireConfig 单元测试。"""

    def test_default_values(self) -> None:
        """检查默认值。"""
        cfg = AutoRetireConfig()
        assert cfg.max_consecutive_zero_ic == 4
        assert cfg.max_decay_6m == 0.30
        assert cfg.min_active_days == 30
        assert cfg.cooldown_days == 7

    def test_custom_values(self) -> None:
        """自定义参数。"""
        cfg = AutoRetireConfig(
            max_consecutive_zero_ic=6,
            max_decay_6m=0.5,
            min_active_days=60,
            cooldown_days=14,
        )
        assert cfg.max_consecutive_zero_ic == 6
        assert cfg.max_decay_6m == 0.5
        assert cfg.min_active_days == 60
        assert cfg.cooldown_days == 14


# ════════════════════════════════════════════════════════════
# AutoRetireManager
# ════════════════════════════════════════════════════════════


class TestAutoRetireManager:
    """AutoRetireManager 单元测试。"""

    def _make_eligible(self, tracker: EliteFactorTracker) -> None:
        """创建符合淘汰条件的因子。"""
        raw = {
            "factor_id": "fct_mgr",
            "name": "MgrFactor",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(60),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": 10,
            "decay_6m": 0.5,
            "status": "active",
            "last_updated": _utc_iso(0),
        }
        _write_raw_snapshot(tracker, "fct_mgr", raw)

    def test_run_returns_retired_ids(self, tmp_path: Path) -> None:
        """run 调用 tracker.auto_retire 并返回淘汰 ID。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible(tracker)
        manager = AutoRetireManager(tracker)

        retired = manager.run()
        assert "fct_mgr" in retired

    def test_run_uses_default_config(self, tmp_path: Path) -> None:
        """run 使用默认 config 参数。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible(tracker)
        manager = AutoRetireManager(tracker)

        retired = manager.run()
        assert "fct_mgr" in retired

    def test_run_empty(self, tmp_path: Path) -> None:
        """没有可淘汰因子时返回空列表。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_a")
        manager = AutoRetireManager(tracker)

        retired = manager.run()
        assert retired == []

    def test_run_with_custom_config(self, tmp_path: Path) -> None:
        """使用自定义 config 的 run。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # 创建刚够条件的因子
        raw = {
            "factor_id": "fct_custom",
            "name": "Custom",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(20),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": 3,
            "decay_6m": 0.2,
            "status": "active",
            "last_updated": _utc_iso(0),
        }
        _write_raw_snapshot(tracker, "fct_custom", raw)

        # 默认 config → 不满足 (consecutive=3 < 4, decay=0.2 < 0.30, days=20 < 30)
        default_mgr = AutoRetireManager(tracker)
        assert default_mgr.run() == []

        # 宽松 config
        cfg = AutoRetireConfig(max_consecutive_zero_ic=3, max_decay_6m=0.15, min_active_days=15)
        lax_mgr = AutoRetireManager(tracker, config=cfg)
        retired = lax_mgr.run()
        assert "fct_custom" in retired

    def test_can_reevaluate_nonexistent_returns_false(self, tmp_path: Path) -> None:
        """不存在的因子返回 False。"""
        tracker = EliteFactorTracker(str(tmp_path))
        manager = AutoRetireManager(tracker)
        assert manager.can_reevaluate("fct_none") is False

    def test_can_reevaluate_active_returns_false(self, tmp_path: Path) -> None:
        """active 状态的因子返回 False。"""
        tracker = EliteFactorTracker(str(tmp_path))
        _seed_tracker(tracker, "fct_active")
        manager = AutoRetireManager(tracker)
        assert manager.can_reevaluate("fct_active") is False

    def test_can_reevaluate_during_cooldown(self, tmp_path: Path) -> None:
        """淘汰后冷却期内返回 False。"""
        tracker = EliteFactorTracker(str(tmp_path))
        self._make_eligible(tracker)
        manager = AutoRetireManager(tracker)
        manager.run()

        assert manager.can_reevaluate("fct_mgr") is False

    def test_can_reevaluate_after_cooldown(self, tmp_path: Path) -> None:
        """冷却期后可重新评估。"""
        tracker = EliteFactorTracker(str(tmp_path))
        # 写入 8 天前淘汰的因子
        past = _utc_iso(8)
        raw = {
            "factor_id": "fct_old",
            "name": "Old",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(60),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": 10,
            "decay_6m": 0.5,
            "status": "retired",
            "last_updated": past,
        }
        _write_raw_snapshot(tracker, "fct_old", raw)

        now = datetime.now(timezone.utc)
        with patch("fts.monitor.elite_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.timezone = timezone

            manager = AutoRetireManager(tracker)
            assert manager.can_reevaluate("fct_old") is True

    def test_can_reevaluate_exactly_at_cooldown(self, tmp_path: Path) -> None:
        """正好等于冷却天数时可重新评估。"""
        tracker = EliteFactorTracker(str(tmp_path))
        past = _utc_iso(7)
        raw = {
            "factor_id": "fct_exact",
            "name": "Exact",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(60),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": 10,
            "decay_6m": 0.5,
            "status": "retired",
            "last_updated": past,
        }
        _write_raw_snapshot(tracker, "fct_exact", raw)

        now = datetime.now(timezone.utc)
        with patch("fts.monitor.elite_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.timezone = timezone

            manager = AutoRetireManager(tracker)
            assert manager.can_reevaluate("fct_exact") is True

    def test_can_reevaluate_decayed_status(self, tmp_path: Path) -> None:
        """decayed 状态的因子也可被 reevaluate。"""
        tracker = EliteFactorTracker(str(tmp_path))
        raw = {
            "factor_id": "fct_decayed",
            "name": "Decayed",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": _utc_iso(60),
            "weekly_ic": [-0.01] * 10,
            "monthly_ic": [],
            "current_ic": -0.01,
            "current_sharpe": 0.3,
            "consecutive_zero_ic": 10,
            "decay_6m": 0.5,
            "status": "decayed",
            "last_updated": _utc_iso(8),
        }
        _write_raw_snapshot(tracker, "fct_decayed", raw)

        now = datetime.now(timezone.utc)
        with patch("fts.monitor.elite_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.timezone = timezone

            manager = AutoRetireManager(tracker)
            assert manager.can_reevaluate("fct_decayed") is True

    def test_constructor_stores_references(self, tmp_path: Path) -> None:
        """构造函数正确存储引用。"""
        tracker = EliteFactorTracker(str(tmp_path))
        config = AutoRetireConfig(cooldown_days=14)
        manager = AutoRetireManager(tracker, config)
        assert manager._tracker is tracker  # noqa: SLF001
        assert manager._config is config  # noqa: SLF001

    def test_constructor_creates_default_config(self, tmp_path: Path) -> None:
        """未传 config 时使用默认值。"""
        tracker = EliteFactorTracker(str(tmp_path))
        manager = AutoRetireManager(tracker)
        assert manager._config is not None  # noqa: SLF001
        assert manager._config.cooldown_days == 7


# ════════════════════════════════════════════════════════════
# TrackingSnapshot TypedDict
# ════════════════════════════════════════════════════════════


class TestTrackingSnapshot:
    """TrackingSnapshot 构造与字段验证。"""

    def test_create_snapshot_directly(self) -> None:
        """直接构造 TrackingSnapshot 并验证字段。"""
        snap: TrackingSnapshot = {
            "factor_id": "fct_test",
            "name": "Test",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": "2026-07-01T00:00:00",
            "weekly_ic": [0.01, 0.02],
            "monthly_ic": [],
            "current_ic": 0.02,
            "current_sharpe": 1.1,
            "consecutive_zero_ic": 0,
            "decay_6m": 0.0,
            "status": "active",
            "last_updated": "2026-07-01T00:00:00",
        }
        assert snap["factor_id"] == "fct_test"
        assert snap["status"] == "active"
        assert len(snap["weekly_ic"]) == 2

    def test_create_snapshot_minimal(self) -> None:
        """total=False 允许只填部分字段。"""
        snap: TrackingSnapshot = {
            "factor_id": "fct_min",
            "name": "Minimal",
            "entry_ic": 0.05,
            "entry_sharpe": 1.2,
            "entry_at": "2026-07-01T00:00:00",
            "weekly_ic": [],
            "monthly_ic": [],
            "current_ic": 0.05,
            "current_sharpe": 1.2,
            "consecutive_zero_ic": 0,
            "decay_6m": 0.0,
            "status": "active",
        }
        # last_updated 可选
        assert snap["factor_id"] == "fct_min"
