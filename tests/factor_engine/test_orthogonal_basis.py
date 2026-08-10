"""tests/factor_engine/test_orthogonal_basis.py — GAP-I206 补充（v2.72.0）正交基底测试。

覆盖:
    1. _calc_ic_slope_6m 滚动 IC 线性回归斜率（GAP-I305 共用工具）
    2. EliteFactorTracker.decay_grade 衰减分级（normal/observe/retired）
    3. OrthogonalBasisManager 基底读写 / 注册上限 / Gram-Schmidt 正交化
    4. L2 准入 _orthogonalize_via_basis 基底优先 + 回退单参照
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from fts.factor_engine.orthogonal_basis import OrthogonalBasisManager
from fts.monitor.elite_tracker import (
    AutoRetireConfig,
    EliteFactorTracker,
    _calc_ic_slope_6m,
)


# ─── 1. 滚动 IC 斜率 ──────────────────────────────────────


class TestIcSlope:
    def test_ascending_ic_positive_slope(self):
        ic = [0.01, 0.03, 0.05, 0.07, 0.09, 0.11, 0.13]
        slope = _calc_ic_slope_6m(ic)
        assert slope > 0

    def test_descending_ic_negative_slope(self):
        ic = [0.13, 0.11, 0.09, 0.07, 0.05, 0.03, 0.01]
        slope = _calc_ic_slope_6m(ic)
        assert slope < 0

    def test_flat_ic_near_zero_slope(self):
        ic = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05]
        assert abs(_calc_ic_slope_6m(ic)) < 1e-9

    def test_insufficient_points_returns_zero(self):
        assert _calc_ic_slope_6m([0.1, 0.2]) == 0.0
        assert _calc_ic_slope_6m([]) == 0.0

    def test_slope_normalized_to_unit_range(self):
        # 剧烈上升序列，斜率归一化到 <= 1.0
        ic = [-0.5, -0.4, -0.2, 0.1, 0.3, 0.5, 0.7]
        assert -1.0 <= _calc_ic_slope_6m(ic) <= 1.0


# ─── 2. 衰减分级 ──────────────────────────────────────────


class TestDecayGrade:
    @pytest.fixture
    def tracker(self, tmp_path: Path) -> EliteFactorTracker:
        cfg = AutoRetireConfig(observe_slope=0.10, retire_slope=0.20, slope_min_points=6)
        return EliteFactorTracker(
            tracking_dir=str(tmp_path / "tracking"),
            retire_config=cfg,
        )

    def test_normal_grade(self, tracker: EliteFactorTracker):
        ic = [0.05, 0.05, 0.06, 0.05, 0.06, 0.05, 0.06]
        assert tracker.decay_grade(ic) == "normal"

    def test_observe_grade(self, tracker: EliteFactorTracker):
        # 明显下行但未达退役阈值
        ic = [0.10, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02]
        assert tracker.decay_grade(ic) in ("observe", "normal")

    def test_retired_grade(self, tracker: EliteFactorTracker):
        # 剧烈下行触发退役
        ic = [0.20, 0.15, 0.10, 0.05, 0.0, -0.05, -0.10]
        assert tracker.decay_grade(ic) == "retired"

    def test_update_writes_decay_grade_field(self, tracker: EliteFactorTracker):
        tracker.init_tracker(factor_id="f_decay", name="d", entry_ic=0.1, entry_sharpe=1.2)
        for v in (0.08, 0.06, 0.04, 0.02, 0.0, -0.02):
            tracker.update("f_decay", v)
        snap = tracker.get("f_decay")
        assert "decay_grade" in snap
        assert "ic_slope_6m" in snap

    def test_auto_retire_via_grade(self, tracker: EliteFactorTracker):
        # 构造历史入库日期满足最小活跃天数
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        tracker.init_tracker(
            factor_id="f_retire",
            name="r",
            entry_ic=0.2,
            entry_sharpe=1.5,
            entry_at=old,
        )
        for v in (0.18, 0.14, 0.10, 0.05, 0.0, -0.06, -0.10):
            tracker.update("f_retire", v, is_monthly=True)
        retired = tracker.auto_retire()
        assert "f_retire" in retired


# ─── 3. OrthogonalBasisManager ────────────────────────────


class TestOrthogonalBasis:
    @pytest.fixture
    def mgr(self, tmp_path: Path) -> OrthogonalBasisManager:
        return OrthogonalBasisManager(
            basis_path=str(tmp_path / "orthogonal_basis.json"),
            max_size=3,
            min_sharpe=0.5,
            residual_corr_max=0.3,
            min_retained_ratio=0.2,
        )

    def test_empty_basis(self, mgr: OrthogonalBasisManager):
        assert mgr.load_basis() == []

    def test_register_and_load(self, mgr: OrthogonalBasisManager):
        mgr.register({"factor_id": "f1", "name": "a", "sharpe": 1.5, "orthogonalized": True})
        members = mgr.load_basis()
        assert len(members) == 1
        assert members[0]["factor_id"] == "f1"
        assert mgr.contains("f1")

    def test_max_size_eviction(self, mgr: OrthogonalBasisManager):
        for i in range(5):
            mgr.register(
                {
                    "factor_id": f"f{i}",
                    "name": f"n{i}",
                    "sharpe": float(5 - i),
                    "orthogonalized": True,
                }
            )
        members = mgr.load_basis()
        assert len(members) == 3
        # Sharpe 最高的保留
        assert members[0]["sharpe"] == 5.0

    def test_duplicate_register_updates(self, mgr: OrthogonalBasisManager):
        mgr.register({"factor_id": "f1", "name": "a", "sharpe": 1.0, "orthogonalized": True})
        mgr.register({"factor_id": "f1", "name": "a2", "sharpe": 2.0, "orthogonalized": True})
        members = mgr.load_basis()
        assert len(members) == 1
        assert members[0]["sharpe"] == 2.0

    def test_gram_schmidt_orthogonalization(self, mgr: OrthogonalBasisManager):
        # 基底成员：线性上升序列
        def _sig_a(_member):
            return np.arange(200, dtype=float) + 100.0

        mgr.register({"factor_id": "fa", "name": "a", "sharpe": 2.0, "orthogonalized": True})
        # 候选信号与基底成员高相关（corr≈0.93）：标准化基底 + 0.4·噪声
        rng = np.random.default_rng(3)
        base = np.arange(200, dtype=float) + 100.0
        base = (base - base.mean()) / base.std()
        candidate = base + 0.4 * rng.normal(size=200)
        orth = mgr.orthogonalize(
            factor={"factor_id": "fc", "name": "c", "code": ""},
            candidate_signal=candidate,
            signal_getter=_sig_a,
            sharpe=1.3,
        )
        assert orth is not None
        assert orth["orthogonalized"] is True
        # 残差与基底成员近似正交
        resid = np.asarray([v if v is not None else np.nan for v in orth["orthogonal_signal"]], dtype=float)
        v = ~np.isnan(resid)
        corr = abs(float(np.corrcoef(resid[v], (np.arange(200, dtype=float) + 100.0)[v])[0, 1]))
        assert corr < 0.3
        assert "orthogonalized_basis" in orth

    def test_weak_candidate_rejected(self, mgr: OrthogonalBasisManager):
        # 候选几乎 = 基底成员（噪声极小）→ 保留比不足拒绝
        def _sig_a(_member):
            return np.arange(200, dtype=float) + 100.0

        mgr.register({"factor_id": "fa", "name": "a", "sharpe": 2.0, "orthogonalized": True})
        candidate = np.arange(200, dtype=float) + 100.0 + 1e-4 * np.sin(np.arange(200))
        orth = mgr.orthogonalize(
            factor={"factor_id": "fc", "name": "c", "code": ""},
            candidate_signal=candidate,
            signal_getter=_sig_a,
            sharpe=1.3,
        )
        assert orth is None

    def test_no_basis_returns_none(self, mgr: OrthogonalBasisManager):
        orth = mgr.orthogonalize(
            factor={"factor_id": "fc", "name": "c", "code": ""},
            candidate_signal=np.arange(100, dtype=float),
            signal_getter=lambda m: None,
            sharpe=1.0,
        )
        assert orth is None


# ─── 4. L2 准入基底正交化（evolution_loop 集成） ─────────


class TestOrthogonalizeViaBasis:
    def test_basis_disabled_returns_none(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from fts.factor_engine.evolution_loop import EvolutionLoop

        loop = MagicMock(spec=EvolutionLoop)
        loop._l2_orthogonal_basis_enabled = False
        # 直接调用未绑定方法
        result = EvolutionLoop._orthogonalize_via_basis(loop, {"factor_id": "f", "name": "n"})
        assert result is None

    def test_basis_getter_skips_missing_elite(self, tmp_path: Path):
        """基底成员 elite 快照缺失时优雅降级（不抛异常）。"""
        from unittest.mock import MagicMock

        from fts.factor_engine.evolution_loop import EvolutionLoop

        loop = MagicMock(spec=EvolutionLoop)
        loop._l2_orthogonal_basis_enabled = True
        loop.elite_dir = tmp_path
        loop._l2_orthogonal_basis_max_size = 10
        loop._l2_orthogonal_basis_min_sharpe = 1.0
        loop._l2_orthogonal_residual_corr_max = 0.3
        loop._l2_orthogonal_min_retained_ratio = 0.3
        # 基底为空 → orthogonalize 返回 None → 回退单参照路径
        from fts.factor_engine.orthogonal_basis import OrthogonalBasisManager

        loop.orthogonal_basis = OrthogonalBasisManager(
            basis_path=str(tmp_path / "ob.json"),
            max_size=10,
        )
        # 空基底 → None（无成员可正交化）
        result = EvolutionLoop._orthogonalize_via_basis(
            loop,
            {"factor_id": "f", "name": "n", "code": "def f(d,p):\n return d"},
        )
        assert result is None
