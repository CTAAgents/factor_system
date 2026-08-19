"""tests/scripts/test_reconcile_dual_track.py — 双轨对账机器测试（plans/57 §5.2）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from reconcile_dual_track import (  # noqa: E402
    cosine_similarity, direction_consistency, exposure_diff, turnover_diff,
    rolling_return_diff, drawdown_diff, reconcile, _demo, GATES,
)


def _mk_pos(seed=0, n=200, cols=("RB", "CU", "TA")):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(rng.normal(0.2, 0.1, (n, len(cols))),
                        index=dates, columns=list(cols))


def _mk_ret(seed=0, n=200):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0005, 0.01, n), index=pd.bdate_range("2025-01-01", periods=n))


class TestMetrics:
    def test_cosine_identical(self):
        assert cosine_similarity({"a": 1.0}, {"a": 1.0}) == pytest.approx(1.0)

    def test_cosine_orthogonal(self):
        c = cosine_similarity({"a": 1.0}, {"a": -1.0})
        assert c == pytest.approx(-1.0)

    def test_cosine_partial_overlap(self):
        c = cosine_similarity({"a": 1.0}, {"a": 1.0, "b": 1.0})
        assert 0.5 < c < 1.0

    def test_direction_consistency(self):
        pos_a = _mk_pos(seed=1)
        pos_b = _mk_pos(seed=1)
        assert direction_consistency(pos_a, pos_b) == pytest.approx(1.0)

    def test_exposure_and_turnover_zero_when_identical(self):
        pos_a = _mk_pos()
        pos_b = _mk_pos()
        assert exposure_diff(pos_a, pos_b) == pytest.approx(0.0)
        assert turnover_diff(pos_a, pos_b) == pytest.approx(0.0)

    def test_rolling_and_drawdown_identical(self):
        r = _mk_ret()
        assert rolling_return_diff(r, r) == pytest.approx(0.0)
        assert drawdown_diff(r, r) == pytest.approx(0.0)


class TestReconcile:
    def test_identical_tracks_pass(self):
        w = {"f1": 0.4, "f2": 0.3, "f3": 0.3}
        pos = _mk_pos()
        ret = _mk_ret()
        r = reconcile(w, w, pos, pos, ret, ret)
        assert r["pass"] is True
        assert r["gates"]["signal_cosine"] is True

    def test_divergent_fails_signal_gate(self):
        w_a = {"f1": 1.0}
        w_b = {"f1": 0.0, "f2": 1.0}
        pos = _mk_pos()
        ret = _mk_ret()
        r = reconcile(w_a, w_b, pos, pos, ret, ret)
        assert r["pass"] is False
        assert r["gates"]["signal_cosine"] is False

    def test_demo_runs(self):
        r = _demo()
        assert "metrics" in r and "gates" in r
        assert 0.0 <= r["metrics"]["direction_rate"] <= 1.0
