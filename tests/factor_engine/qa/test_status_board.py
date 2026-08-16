"""test_qa_status_board — 7 状态机 + 看板 + factor_db 落库测试（CTA 手册 6.8）。"""

from __future__ import annotations

from fts.factor_engine.qa.status_board import (
    FactorStatus,
    STATUS_TRANSITIONS,
    apply_status_transition,
    can_transition,
    max_weight_for_status,
    normalize_status,
    status_board,
)


def test_legal_transitions() -> None:
    """手册 6.8 流转图合法路径。"""
    assert can_transition("DRAFT", "PENDING_QA") is True
    assert can_transition("PENDING_QA", "CORE") is True
    assert can_transition("PENDING_QA", "CANDIDATE") is True
    assert can_transition("CORE", "OBSERVATION") is True
    assert can_transition("OBSERVATION", "CORE") is True
    assert can_transition("OBSERVATION", "SUSPENDED") is True
    assert can_transition("SUSPENDED", "OBSERVATION") is True
    assert can_transition("OBSERVATION", "RETIRED") is True
    assert can_transition("RETIRED", "PENDING_QA") is True  # 复审重新有效


def test_illegal_transitions() -> None:
    """非法流转被拒绝。"""
    assert can_transition("DRAFT", "CORE") is False  # 跳级
    assert can_transition("RETIRED", "CORE") is False  # 退役需重走质检
    assert can_transition("CORE", "DRAFT") is False  # 不可回退
    assert can_transition("UNKNOWN", "CORE") is False


def test_legacy_active_maps_to_core() -> None:
    """存量 'active' 状态归入 CORE。"""
    assert normalize_status("active") == "CORE"
    assert normalize_status("Active") == "CORE"
    assert can_transition("active", "OBSERVATION") is True


def test_status_alias_map_unifies_all() -> None:
    """契约层统一：各模块历史命名全量归一到唯一状态（消除一义多名/一名多义）。"""
    # 主表 factor_catalog.status
    assert normalize_status("active") == "CORE"
    assert normalize_status("degraded") == "OBSERVATION"
    assert normalize_status("retired") == "RETIRED"
    # reaudit 处置（含历史拼接怪名 active(shadow)）
    assert normalize_status("active(shadow)") == "OBSERVATION"
    assert normalize_status("shadow") == "OBSERVATION"
    assert normalize_status("retain") == "CORE"
    assert normalize_status("retire") == "RETIRED"
    # EliteFactorTracker 衰减快照
    assert normalize_status("observing") == "OBSERVATION"
    assert normalize_status("decaying") == "OBSERVATION"
    assert normalize_status("critical_decay") == "OBSERVATION"
    assert normalize_status("deprecated") == "RETIRED"
    # 未知值原样返回（不误归一）
    assert normalize_status("UNKNOWN_X") == "UNKNOWN_X"
    # 归一后参与合法流转判定（degraded 视同 OBSERVATION）
    assert can_transition("degraded", "CORE") is True
    assert can_transition("degraded", "SUSPENDED") is True
    assert can_transition("deprecated", "PENDING_QA") is True


def test_status_labels_complete() -> None:
    """每个唯一状态都有不混淆的中文名。"""
    from fts.factor_engine.qa.status_board import STATUS_LABELS

    assert len(STATUS_LABELS) == len(FactorStatus)
    for s in FactorStatus:
        assert STATUS_LABELS[s.value], f"状态 {s.value} 缺中文名"


def test_status_weight_limits() -> None:
    """状态权重上限（手册 6.8）。"""
    assert max_weight_for_status("CORE") == 0.30
    assert max_weight_for_status("CANDIDATE") == 0.15
    assert max_weight_for_status("OBSERVATION") == 0.50
    assert max_weight_for_status("DRAFT") == 0.0
    assert max_weight_for_status("SUSPENDED") == 0.0
    assert max_weight_for_status("RETIRED") == 0.0


def test_status_board_stats() -> None:
    """看板统计状态数量与预警清单。"""
    factors = [
        {"name": "f1", "status": "CORE"},
        {"name": "f2", "status": "CORE"},
        {"name": "f3", "status": "CANDIDATE"},
        {"name": "f4", "status": "OBSERVATION"},
        {"name": "f5", "status": "active"},  # 兼容存量
        {"name": "f6", "status": "RETIRED"},
    ]
    b = status_board(factors)
    assert b["total"] == 6
    assert b["counts"]["CORE"] == 3  # f1 + f2 + f5(active)
    assert b["counts"]["CANDIDATE"] == 1
    assert b["counts"]["OBSERVATION"] == 1
    assert b["serving"] == 4
    assert len(b["obs_warning"]) == 1


def test_transition_config_complete() -> None:
    """7 状态齐全，流转表键完整。"""
    assert set(STATUS_TRANSITIONS.keys()) == {s.value for s in FactorStatus}
    assert len(FactorStatus) == 7


def test_apply_transition_rejects_illegal() -> None:
    """非法流转落库被拒绝。"""

    class _FakeRepo:
        def __init__(self) -> None:
            self.calls = []

        def get_history(self, factor_id: str) -> list[dict]:
            return [{"to_status": "DRAFT"}]

        def log_transition(self, factor_id, frm, to, reason, snapshot=None) -> str:
            self.calls.append(("log", frm, to))
            return "h1"

        def update_factor_status(self, factor_id, status, **fields) -> bool:
            self.calls.append(("update", status))
            return True

    repo = _FakeRepo()
    r = apply_status_transition(repo, "f1", "CORE", "跳过质检直接入库")
    assert r["ok"] is False
    assert "非法状态流转" in r["error"]
    assert repo.calls == []  # 未落库


def test_apply_transition_persists_legal() -> None:
    """合法流转写 history + 更新 status。"""

    class _FakeRepo:
        def __init__(self) -> None:
            self.calls = []

        def log_transition(self, factor_id, frm, to, reason, snapshot=None) -> str:
            self.calls.append(("log", frm, to))
            return "h2"

        def update_factor_status(self, factor_id, status, **fields) -> bool:
            self.calls.append(("update", status))
            return True

    repo = _FakeRepo()
    r = apply_status_transition(repo, "f1", "CORE", "准入通过", from_status="PENDING_QA")
    assert r["ok"] is True
    assert r["history_id"] == "h2"
    assert repo.calls == [("log", "PENDING_QA", "CORE"), ("update", "CORE")]
