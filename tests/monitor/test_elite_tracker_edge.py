"""
tests/monitor/test_elite_tracker_edge.py — elite_tracker 边缘路径覆盖测试。

覆盖范围:
    - snapshot is None (corrupted/disappeared JSON file)
    - bad date format in entry_at
    - no entry_at field
    - no last_updated_str
    - bad date in can_reevaluate

版本: v0.1.0
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# 确保能导入 fts 模块
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.monitor.elite_tracker import (
    AutoRetireManager,
    EliteFactorTracker,
)


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _write_raw_snapshot(tracker: EliteFactorTracker, factor_id: str, data: dict) -> Path:
    """直接写原始 JSON 到跟踪文件。"""
    p = tracker._path(factor_id)  # noqa: SLF001
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _utc_iso(days_ago: int = 0) -> str:
    """返回 days_ago 天前的 UTC ISO 时间戳。"""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ═══════════════════════════════════════════════════════════════
# auto_retire — snapshot is None
# ═══════════════════════════════════════════════════════════════


class TestAutoRetireSnapshotNone:
    """测试 auto_retire 中 snapshot 为 None 的路径。"""

    def test_auto_retire_skips_none_snapshot(self, tmp_path):
        """snapshot is None（corrupted file）时跳过并继续处理下一个（line 237）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        # 写一个正常文件
        _write_raw_snapshot(
            tracker,
            "fct_good",
            {
                "factor_id": "fct_good",
                "name": "Good",
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
            },
        )

        # 写一个损坏的 JSON 文件
        bad_path = tmp_path / "fct_bad.json"
        bad_path.write_text("this is not json", encoding="utf-8")

        # 再写一个正常文件
        _write_raw_snapshot(
            tracker,
            "fct_good2",
            {
                "factor_id": "fct_good2",
                "name": "Good2",
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
            },
        )

        # 不应抛出异常，应正常处理正常文件
        retired = tracker.auto_retire()
        assert "fct_good" in retired
        assert "fct_good2" in retired


# ═══════════════════════════════════════════════════════════════
# auto_retire — bad date format / no entry_at
# ═══════════════════════════════════════════════════════════════


class TestAutoRetireBadDate:
    """测试 entry_at 格式异常和缺失。"""

    def test_auto_retire_bad_date_format(self, tmp_path):
        """entry_at 格式错误时 age_days 置为 0（line 249-250）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        _write_raw_snapshot(
            tracker,
            "fct_bad_date",
            {
                "factor_id": "fct_bad_date",
                "name": "BadDate",
                "entry_ic": 0.05,
                "entry_sharpe": 1.2,
                "entry_at": "not-a-valid-date-string",  # 格式错误
                "weekly_ic": [-0.01] * 10,
                "monthly_ic": [],
                "current_ic": -0.01,
                "current_sharpe": 0.3,
                "consecutive_zero_ic": 10,
                "decay_6m": 0.5,
                "status": "active",
                "last_updated": _utc_iso(0),
            },
        )

        # age_days = 0 < min_active_days(30) → 不被淘汰
        retired = tracker.auto_retire()
        assert "fct_bad_date" not in retired

    def test_auto_retire_no_entry_at(self, tmp_path):
        """entry_at 缺失时 age_days 置为 0（line 251-252）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        _write_raw_snapshot(
            tracker,
            "fct_no_entry",
            {
                "factor_id": "fct_no_entry",
                "name": "NoEntry",
                "entry_ic": 0.05,
                "entry_sharpe": 1.2,
                # 没有 entry_at 字段
                "weekly_ic": [-0.01] * 10,
                "monthly_ic": [],
                "current_ic": -0.01,
                "current_sharpe": 0.3,
                "consecutive_zero_ic": 10,
                "decay_6m": 0.5,
                "status": "active",
                "last_updated": _utc_iso(0),
            },
        )

        # age_days = 0 < min_active_days(30) → 不被淘汰
        retired = tracker.auto_retire()
        assert "fct_no_entry" not in retired


# ═══════════════════════════════════════════════════════════════
# can_reevaluate — no last_updated_str / bad date
# ═══════════════════════════════════════════════════════════════


class TestCanReevaluateEdge:
    """测试 can_reevaluate 的边缘路径。"""

    def test_can_reevaluate_no_last_updated(self, tmp_path):
        """last_updated 缺失时返回 False（line 355）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        _write_raw_snapshot(
            tracker,
            "fct_no_update",
            {
                "factor_id": "fct_no_update",
                "name": "NoUpdate",
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
                # 没有 last_updated
            },
        )

        manager = AutoRetireManager(tracker)
        assert manager.can_reevaluate("fct_no_update") is False

    def test_can_reevaluate_bad_last_updated(self, tmp_path):
        """last_updated 格式错误时返回 False（line 361-362）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        _write_raw_snapshot(
            tracker,
            "fct_bad_update",
            {
                "factor_id": "fct_bad_update",
                "name": "BadUpdate",
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
                "last_updated": "not-a-valid-date",  # 格式错误
            },
        )

        manager = AutoRetireManager(tracker)
        assert manager.can_reevaluate("fct_bad_update") is False

    def test_can_reevaluate_no_last_updated_str_empty(self, tmp_path):
        """last_updated 为空字符串时返回 False（line 354-355）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        _write_raw_snapshot(
            tracker,
            "fct_empty_update",
            {
                "factor_id": "fct_empty_update",
                "name": "EmptyUpdate",
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
                "last_updated": "",  # 空字符串
            },
        )

        manager = AutoRetireManager(tracker)
        # 空字符串是 falsy，所以走 no last_updated_str 路径
        assert manager.can_reevaluate("fct_empty_update") is False


# ═══════════════════════════════════════════════════════════════
# can_reevaluate — bad date in fromisoformat
# ═══════════════════════════════════════════════════════════════


class TestCanReevaluateBadDate:
    """测试 fromisoformat 抛出异常时返回 False。"""

    def test_can_reevaluate_fromisoformat_error(self, tmp_path):
        """fromisoformat 抛出 ValueError 返回 False（line 361-362）。"""
        tracker = EliteFactorTracker(str(tmp_path))

        # 使用一个从 isoformat 会抛出异常的值
        _write_raw_snapshot(
            tracker,
            "fct_bad_iso",
            {
                "factor_id": "fct_bad_iso",
                "name": "BadIso",
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
                "last_updated": "2024-13-01T00:00:00",  # 无效月份 → ValueError
            },
        )

        manager = AutoRetireManager(tracker)
        assert manager.can_reevaluate("fct_bad_iso") is False
