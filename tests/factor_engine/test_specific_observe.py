"""test_specific_observe — plans/59 OPT-03（GAP-163）特异因子观察期与 OOS 前瞻复核测试。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from fts.factor_engine.factor_db import FactorRepository, init_database
from fts.factor_engine.factor_inspector import FactorReviewWorkflow
from fts.factor_engine.specific_observe import (
    PHASE_OBSERVING,
    PHASE_REVIEW_DUE,
    STATUS_CONFIRMED,
    STATUS_REVOKED,
    SpecificObserveConfig,
    build_observe_marker,
    observe_phase,
    review_specific_oos,
    shrink_scope_ic,
)

_TODAY = date(2026, 8, 20)


# ─── build_observe_marker ───────────────────────────────────


def test_build_marker_structure() -> None:
    """观察期标记结构完整。"""
    m = build_observe_marker(datetime(2026, 8, 1, 9, 0), 0.12, SpecificObserveConfig())
    assert m["status"] == "observing"
    assert m["promoted_at"] == "2026-08-01"
    assert m["observe_until"] == "2026-08-21"  # +20 天
    assert m["baseline_domain_ic"] == 0.12
    assert m["observe_days"] == 20


def test_build_marker_none_ic() -> None:
    """基线 IC 缺失 → None 保留。"""
    m = build_observe_marker("2026-08-01", None, SpecificObserveConfig())
    assert m["baseline_domain_ic"] is None


# ─── observe_phase ──────────────────────────────────────────


def test_phase_observing_before_due() -> None:
    """观察期内 → observing。"""
    m = build_observe_marker("2026-08-10", 0.1, SpecificObserveConfig(observe_days=20))
    assert observe_phase(m, date(2026, 8, 15), SpecificObserveConfig()) == PHASE_OBSERVING


def test_phase_review_due_at_due() -> None:
    """到期当日 → review_due。"""
    m = build_observe_marker("2026-08-01", 0.1, SpecificObserveConfig(observe_days=20))
    assert observe_phase(m, date(2026, 8, 21), SpecificObserveConfig()) == PHASE_REVIEW_DUE


def test_phase_terminal_states() -> None:
    """已确认/已撤销为终态。"""
    m = build_observe_marker("2026-08-01", 0.1, SpecificObserveConfig())
    m["status"] = STATUS_CONFIRMED
    assert observe_phase(m, date(2026, 9, 1), SpecificObserveConfig()) == STATUS_CONFIRMED
    m["status"] = STATUS_REVOKED
    assert observe_phase(m, date(2026, 9, 1), SpecificObserveConfig()) == STATUS_REVOKED


def test_phase_broken_marker_observing() -> None:
    """标记损坏（无 observe_until）→ 保守保持观察。"""
    assert observe_phase({}, _TODAY, SpecificObserveConfig()) == PHASE_OBSERVING
    assert observe_phase(None, _TODAY, SpecificObserveConfig()) == PHASE_OBSERVING


# ─── shrink_scope_ic（小样本贝叶斯收缩） ───────────────────


def test_shrink_no_contract_large_sample() -> None:
    """n_symbols >= 3 不收缩。"""
    assert shrink_scope_ic(0.12, 3, 0.02, SpecificObserveConfig()) == pytest.approx(0.12)


def test_shrink_small_sample() -> None:
    """n=1 收缩 k 比例；n=2 收缩 k/2。"""
    cfg = SpecificObserveConfig(shrink_k=0.5)
    # n=1: 0.12×(1-0.5) + 0.02×0.5 = 0.07
    assert shrink_scope_ic(0.12, 1, 0.02, cfg) == pytest.approx(0.07)
    # n=2: k=0.25 → 0.12×0.75 + 0.02×0.25 = 0.095
    assert shrink_scope_ic(0.12, 2, 0.02, cfg) == pytest.approx(0.095)


def test_shrink_no_input_returns_original() -> None:
    """无域内 IC / n<1 → 原样。"""
    assert shrink_scope_ic(None, 1, 0.02, SpecificObserveConfig()) is None
    assert shrink_scope_ic(0.12, 0, 0.02, SpecificObserveConfig()) == 0.12


# ─── review_specific_oos ────────────────────────────────────


def _due_marker(baseline: float = 0.10) -> dict:
    m = build_observe_marker("2026-07-01", baseline, SpecificObserveConfig(observe_days=20))
    return m


def test_oos_confirm_when_due_ic_high() -> None:
    """观察期满且域内 |IC| 达标 → confirm（固化）。"""
    decision, detail = review_specific_oos(_due_marker(), 0.15, SpecificObserveConfig())
    assert decision == "confirm"
    assert "固化" in detail["reason"]


def test_oos_revoke_when_decayed() -> None:
    """观察期满且域内 IC 大幅衰减 → revoke。"""
    decision, detail = review_specific_oos(_due_marker(0.10), 0.01, SpecificObserveConfig())
    assert decision == "revoke"
    assert "撤销" in detail["reason"]


def test_oos_hold_when_no_ic() -> None:
    """数据不可得 → hold 顺延。"""
    decision, detail = review_specific_oos(_due_marker(), None, SpecificObserveConfig())
    assert decision == "hold"
    assert "顺延" in detail["reason"]


def test_oos_hold_when_midzone() -> None:
    """衰减未达撤销线且 |IC| 未达固化线 → hold。

    confirm_min_ic=0.06 > base×0.5=0.05 时存在 midzone：[0.05, 0.06)。
    """
    cfg = SpecificObserveConfig(confirm_min_ic=0.06, revoke_ic_decay=0.50)
    decision, detail = review_specific_oos(_due_marker(0.10), 0.055, cfg)
    assert decision == "hold"
    assert "维持观察" in detail["reason"]


def test_oos_hold_before_due() -> None:
    """观察期未到期 → hold。"""
    m = _due_marker()
    m["observe_until"] = (date.today() + timedelta(days=30)).isoformat()
    decision, _ = review_specific_oos(m, 0.15, SpecificObserveConfig())
    assert decision == "hold"


def test_oos_terminal_states() -> None:
    """已确认/已撤销直接返回终态判定。"""
    m = _due_marker()
    m["status"] = STATUS_CONFIRMED
    assert review_specific_oos(m, 0.0, SpecificObserveConfig())[0] == "confirm"
    m["status"] = STATUS_REVOKED
    assert review_specific_oos(m, 0.15, SpecificObserveConfig())[0] == "revoke"


# ─── FactorReviewWorkflow.review_specific_observations ──────


@pytest.fixture
def wf(tmp_path) -> FactorReviewWorkflow:
    db_path = tmp_path / "test_specific_observe.db"
    init_database(str(db_path))
    repo = FactorRepository(str(db_path))
    return FactorReviewWorkflow(repo=repo)


def _create_specific_factor(repo: FactorRepository, fid: str, marker: dict, with_scope: bool = True) -> None:
    meta: dict = {"specific_observe": marker, "subchain_specific": True}
    if with_scope:
        meta["scope_domain"] = {
            "scope": {"kind": "symbol", "symbols": ["SC0"], "evidence": {}},
            "ic": 0.10,
        }
    repo.create_factor(
        {
            "factor_id": fid,
            "name": f"Factor {fid}",
            "code": "close",
            "market": "futures",
            "is_elite": True,
            "status": "active",
            "metadata": meta,
        }
    )


def _overdue_marker(baseline: float = 0.10) -> dict:
    m = build_observe_marker("2026-07-01", baseline, SpecificObserveConfig(observe_days=20))
    return m


def test_review_confirm_updates_metadata(wf: FactorReviewWorkflow) -> None:
    """confirm 落库：标记 status=confirmed。"""
    _create_specific_factor(wf._repo, "f_spec_ok", _overdue_marker())
    stats = wf.review_specific_observations(
        market="futures", ic_provider=lambda fid, m: 0.15, commit=True, today=date(2026, 8, 20)
    )
    assert stats["confirmed"] == 1
    assert stats["scanned"] == 1
    row = wf._repo.get_factor("f_spec_ok")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert meta["specific_observe"]["status"] == STATUS_CONFIRMED


def test_review_revoke_clears_specific(wf: FactorReviewWorkflow) -> None:
    """revoke 落库：删观察标记 + 清特异画像 + 留痕。"""
    _create_specific_factor(wf._repo, "f_spec_decay", _overdue_marker(0.10))
    stats = wf.review_specific_observations(
        market="futures", ic_provider=lambda fid, m: 0.005, commit=True, today=date(2026, 8, 20)
    )
    assert stats["revoked"] == 1
    row = wf._repo.get_factor("f_spec_decay")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert "specific_observe" not in meta
    assert meta["subchain_specific"] is False
    assert "specific_revoke" in meta
    sd = meta.get("scope_domain")
    assert sd["scope"]["kind"] == "all"


def test_review_hold_no_ic_extends(wf: FactorReviewWorkflow) -> None:
    """hold（数据不可得）→ observe_until 顺延。"""
    m = _overdue_marker()
    _create_specific_factor(wf._repo, "f_spec_hold", m)
    stats = wf.review_specific_observations(
        market="futures", ic_provider=None, commit=True, today=date(2026, 8, 20)
    )
    assert stats["held"] == 1
    row = wf._repo.get_factor("f_spec_hold")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    from datetime import date as _d

    new_until = _d.fromisoformat(meta["specific_observe"]["observe_until"])
    old_until = _d.fromisoformat(m["observe_until"])
    assert (new_until - old_until).days == SpecificObserveConfig().hold_grace_days


def test_review_observing_skipped(wf: FactorReviewWorkflow) -> None:
    """观察期未满 → 跳过。"""
    m = build_observe_marker("2026-08-15", 0.1, SpecificObserveConfig(observe_days=20))
    _create_specific_factor(wf._repo, "f_spec_new", m)
    stats = wf.review_specific_observations(
        market="futures", ic_provider=lambda fid, mm: 0.15, commit=True, today=date(2026, 8, 20)
    )
    assert stats["observing"] == 1
    assert stats["review_due"] == 0
    assert stats["confirmed"] == 0


def test_review_dry_run_no_commit(wf: FactorReviewWorkflow) -> None:
    """dry-run（commit=False）不落库。"""
    _create_specific_factor(wf._repo, "f_spec_dry", _overdue_marker())
    stats = wf.review_specific_observations(
        market="futures", ic_provider=lambda fid, m: 0.15, commit=False, today=date(2026, 8, 20)
    )
    assert stats["confirmed"] == 1  # 判定发生
    row = wf._repo.get_factor("f_spec_dry")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert meta["specific_observe"]["status"] == "observing"  # 未落库
