"""tests/factor_engine/test_energy_qa_review.py — 能化链评审+质检统一管道测试。

覆盖：决策判据全分支（宁严勿松）、配置默认值（冷却期 30）、冷却期回归扫描、
dry-run 不落库。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fts.factor_engine.energy_qa_review import (
    EnergyQaReviewConfig,
    EnergyQaReviewPipeline,
    decide_factor,
)

CFG = EnergyQaReviewConfig()


# ─── 决策判据（纯函数，核心） ─────────────────────────────


def _decide(**kw):
    base = dict(
        factor_id="f1",
        name="测试因子",
        prev_status="active",
        reaudit_fail=False,
        curr_ic=0.08,
        hist_ic=0.10,
        slope_grade="normal",
        cfg=CFG,
    )
    base.update(kw)
    return decide_factor(**base)


def test_config_defaults_cooldown_30():
    assert CFG.cooldown_days == 30
    assert CFG.apply is False
    assert CFG.ic_threshold == 0.02
    assert CFG.drop_threshold == 0.30
    assert CFG.cooldown_max_attempts == 2


def test_decide_active():
    d = _decide()
    assert d.decision == "active"
    assert d.reasons == ["达标"]


def test_decide_shadow_weak_ic():
    d = _decide(curr_ic=0.01, hist_ic=0.015)  # |IC|<0.02 且降幅33%（<50% 非严重）→ shadow
    assert d.decision == "shadow"
    assert any("|IC|<" in r for r in d.reasons)


def test_decide_shadow_drop():
    d = _decide(curr_ic=0.06, hist_ic=0.10)  # 降幅 40%（>0.30 且 <0.50）→ shadow
    assert d.decision == "shadow"
    assert any("IC降幅" in r for r in d.reasons)


def test_decide_shadow_reaudit_fail():
    d = _decide(reaudit_fail=True)
    assert d.decision == "shadow"
    assert "重审不合格" in d.reasons


def test_decide_shadow_slope_observe():
    d = _decide(slope_grade="observe")
    assert d.decision == "shadow"
    assert "斜率观察" in d.reasons


def test_decide_degraded_severe_drop():
    d = _decide(curr_ic=0.02, hist_ic=0.10)  # 降幅 80% >= 0.50 严重
    assert d.decision == "degraded"
    assert any("严重" in r for r in d.reasons)


def test_decide_degraded_slope_retired():
    d = _decide(slope_grade="retired")
    assert d.decision == "degraded"
    assert "斜率退役" in d.reasons


def test_decide_shadow_regression_to_active():
    """shadow 观察池达标 → 回归 active（宁可错过但可回归）。"""
    d = _decide(prev_status="shadow", curr_ic=0.08, hist_ic=0.10)
    assert d.decision == "active"


# ─── 冷却期回归扫描 ───────────────────────────────────────


class _FakeRepo:
    def __init__(self, *a, **k):
        pass

    def get_factor(self, fid):
        return {
            "factor_id": fid,
            "code": "def f(df): return df['close'].pct_change()",
            "metadata": "{}",
        }

    def update_factor(self, fid, updates):
        return True

    def retire_factor(self, fid, reason="", elite_dir=None):
        return True

    def close(self):
        pass


class _FakeSRepo:
    def __init__(self, *a, **k):
        pass

    def update_factor_status(self, fid, status):
        return True

    def log_transition(self, *a, **k):
        return True

    def close(self):
        pass


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    import fts.factor_engine.factor_db.repository as repo_mod

    monkeypatch.setattr(repo_mod, "FactorRepository", _FakeRepo)
    monkeypatch.setattr(repo_mod, "FactorStatusRepository", _FakeSRepo)
    elite = tmp_path / "elite"
    (elite / "_deprecated").mkdir(parents=True)
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    return EnergyQaReviewPipeline(
        config=EnergyQaReviewConfig(apply=True),
        elite_dir=elite,
        tracking_dir=tracking,
    )


def _write_degraded_snapshot(tracking: Path, fid: str, last_dt: datetime, attempts: int = 0) -> None:
    (tracking / f"{fid}.json").write_text(
        json.dumps(
            {
                "factor_id": fid,
                "status": "degraded",
                "last_updated": last_dt.isoformat(),
                "cooldown_attempts": attempts,
                "decay_grade": "retired",
            }
        ),
        encoding="utf-8",
    )


def test_cooldown_not_expired_held(pipe, monkeypatch):
    """未到期（<30 日）→ 保持 degraded，不落库。"""
    _write_degraded_snapshot(pipe.tracking_dir, "f1", datetime.now(timezone.utc) - timedelta(days=10))
    regressed, to_retire, held = pipe._scan_cooldown_regression({}, [], "t", pipe.out_dir or pipe.elite_dir)
    assert regressed == []
    assert to_retire == []
    assert held == ["f1"]


def test_cooldown_expired_regression(pipe, monkeypatch):
    """到期且重新验证达标 → 回归 active。"""
    _write_degraded_snapshot(pipe.tracking_dir, "f1", datetime.now(timezone.utc) - timedelta(days=45))
    monkeypatch.setattr(pipe, "_compute_curr_ic", lambda *a, **k: 0.08)  # 达标
    regressed, to_retire, held = pipe._scan_cooldown_regression({}, [], "t", pipe.out_dir or pipe.elite_dir)
    assert "f1" in regressed
    snap = json.loads((pipe.tracking_dir / "f1.json").read_text(encoding="utf-8"))
    assert snap["status"] == "active"
    assert snap["cooldown_attempts"] == 0


def test_cooldown_expired_still_fail_then_retire(pipe, monkeypatch):
    """到期不达标两次 → retire。"""
    _write_degraded_snapshot(
        pipe.tracking_dir, "f1", datetime.now(timezone.utc) - timedelta(days=40), attempts=1
    )
    monkeypatch.setattr(pipe, "_compute_curr_ic", lambda *a, **k: 0.0)  # 不达标
    regressed, to_retire, held = pipe._scan_cooldown_regression({}, [], "t", pipe.out_dir or pipe.elite_dir)
    assert "f1" in to_retire
    snap = json.loads((pipe.tracking_dir / "f1.json").read_text(encoding="utf-8"))
    assert snap["status"] == "retired"


def test_cooldown_expired_fail_first_attempt_held(pipe, monkeypatch):
    """到期不达标第一次 → 计数+1 保持 degraded。"""
    _write_degraded_snapshot(pipe.tracking_dir, "f1", datetime.now(timezone.utc) - timedelta(days=40), attempts=0)
    monkeypatch.setattr(pipe, "_compute_curr_ic", lambda *a, **k: 0.0)
    regressed, to_retire, held = pipe._scan_cooldown_regression({}, [], "t", pipe.out_dir or pipe.elite_dir)
    assert held == ["f1"]
    snap = json.loads((pipe.tracking_dir / "f1.json").read_text(encoding="utf-8"))
    assert snap["cooldown_attempts"] == 1
    assert snap["status"] == "degraded"


# ─── dry-run 不落库 ───────────────────────────────────────


def test_pipeline_dryrun_does_not_write(monkeypatch, tmp_path):
    """apply=False：各阶段仅计算，落库层不产生写操作（reaudit/repo/inspector 均 mock）。"""
    from fts.factor_engine.energy_qa_review import EnergyQaReviewPipeline

    writes: list[str] = []

    class _NoWriteRepo(_FakeRepo):
        def update_factor(self, fid, updates):
            writes.append(fid)
            return True

        def retire_factor(self, fid, reason="", elite_dir=None):
            writes.append(fid)
            return True

    import fts.factor_engine.factor_db.repository as repo_mod

    monkeypatch.setattr(repo_mod, "FactorRepository", _NoWriteRepo)
    monkeypatch.setattr(repo_mod, "FactorStatusRepository", _FakeSRepo)

    pipe = EnergyQaReviewPipeline(
        config=EnergyQaReviewConfig(apply=False),
        elite_dir=tmp_path / "elite",
        tracking_dir=tmp_path / "tracking",
    )
    (pipe.tracking_dir).mkdir(parents=True, exist_ok=True)

    # mock 面板与重审
    import pandas as pd

    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=60, freq="D"), "close": 100.0})
    monkeypatch.setattr(pipe, "_prepare_panel", lambda: ({"SC0": df}, df.index, None))
    monkeypatch.setattr(pipe, "_stage_reaudit", lambda *a, **k: (set(), {"retain": 1}))
    monkeypatch.setattr(
        pipe, "_load_elite_with_hist",
        lambda: [{"factor_id": "f1", "name": "x", "code": "c", "_prev_status": "active", "_hist_ic": 0.1}],
    )
    monkeypatch.setattr(pipe, "_compute_curr_ic", lambda *a, **k: 0.01)
    monkeypatch.setattr(pipe, "_stage_inspector", lambda *a, **k: {"skipped": True})

    out = pipe.run(trace_id="t_dryrun")
    assert out["status"] == "completed"
    assert out["apply"] is False
    assert writes == []  # dry-run 不落库


# ─── 落库处置（apply=True） ───────────────────────────────


def test_apply_dispositions(pipe, monkeypatch):
    """apply=True：shadow/degraded 落库生效。"""
    from fts.factor_engine.energy_qa_review import FactorDisposition

    d1 = FactorDisposition(factor_id="f_ok", name="ok", prev_status="active", decision="active")
    d2 = FactorDisposition(factor_id="f_warn", name="warn", prev_status="active", decision="shadow", reasons=["|IC|<0.02"])
    d3 = FactorDisposition(factor_id="f_crit", name="crit", prev_status="active", decision="degraded", reasons=["严重"])
    stats = pipe._apply_dispositions([d1, d2, d3], "t", pipe.out_dir or pipe.elite_dir)
    assert stats["active"] == 1
    assert stats["shadow"] == 1
    assert stats["degraded"] == 1
