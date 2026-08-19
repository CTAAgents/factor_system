"""tests/scripts/test_review_legacy_factors.py — P0 存量因子集中重审管道测试（plans/57 §6.8）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from review_legacy_factors import (  # noqa: E402
    stratify, bh_correction, build_conclusion, audit_sample, FAMILY, FDR_ALPHA,
)


def _factors(statuses):
    return [
        {"factor_id": f"f{i}", "status": s, "ic": 0.05, "icir": 0.3, "sharpe": 1.2}
        for i, s in enumerate(statuses)
    ]


class TestStratify:
    def test_group_by_status(self):
        layers = stratify(_factors(["active", "shadow", "degraded", "active"]))
        assert len(layers["active"]) == 2
        assert len(layers["shadow"]) == 1
        assert len(layers["degraded"]) == 1


class TestBHCorrection:
    def test_all_pass_strong_pvalues(self):
        r = bh_correction([0.001, 0.002, 0.003])
        assert r["passed"].all()
        assert (r["q_values"] <= FDR_ALPHA).all()

    def test_none_pass_weak_pvalues(self):
        r = bh_correction([0.5, 0.6, 0.7])
        assert not r["passed"].any()

    def test_empty(self):
        r = bh_correction([])
        assert len(r["passed"]) == 0

    def test_q_monotonic(self):
        r = bh_correction([0.01, 0.04, 0.05, 0.5, 0.7, 0.9])
        q = r["q_values"]
        assert np.all(q[1:] >= q[:-1] - 1e-12)  # 单调非降


class TestBuildConclusion:
    def test_strong_active_promoted(self):
        factors = _factors(["active"])
        # 主检验 p 通过 FDR
        conclusion, fdr = build_conclusion(factors, {"active": [0.001]})
        assert conclusion["f0"] == "promote"
        assert fdr["active"]["passed_fdr"] == 1

    def test_weak_active_observed(self):
        factors = _factors(["active"])
        conclusion, _ = build_conclusion(factors, {"active": [0.5]})
        assert conclusion["f0"] == "observe"  # 复检未过 → 观察

    def test_weak_shadow_retired(self):
        factors = _factors(["shadow"])
        conclusion, _ = build_conclusion(factors, {"shadow": [0.6]})
        assert conclusion["f0"] == "retire"

    def test_deleted_always_retire(self):
        factors = _factors(["deleted"])
        conclusion, _ = build_conclusion(factors, {"deleted": [0.001]})
        assert conclusion["f0"] == "retire"

    def test_fdr_family_reported(self):
        _, fdr = build_conclusion(_factors(["active", "shadow"]), {"active": [0.01], "shadow": [0.02]})
        assert fdr["active"]["family"] == FAMILY["active"][0]
        assert fdr["shadow"]["family"] == FAMILY["shadow"][0]


class TestAuditSample:
    def test_promote_100_percent(self):
        factors = _factors(["active", "active", "shadow"])
        conclusion = {"f0": "promote", "f1": "promote", "f2": "retire"}
        a = audit_sample(factors, conclusion)
        assert a["promote_audit"] == a["promote_total"] == 2
