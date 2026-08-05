"""
tests/monitor/test_elite_tracker.py — A.2 因子衰减追踪与分级准入测试

覆盖:
1. 分级准入逻辑 (A/B/C级)
2. 观察期机制
3. 增强衰减判定 (IC连续<0, Sharpe连续下降)
4. 状态转换 (active→observing→decaying→critical_decay→retired)
5. 月度评估流程
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fts.monitor.elite_tracker import (
    AutoRetireConfig,
    AutoRetireManager,
    EliteFactorTracker,
    FactorGrade,
    GradeThreshold,
    _calc_decay_6m,
    _is_past,
)


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def temp_dir(tmp_path: Path) -> str:
    """创建临时目录。"""
    return str(tmp_path / "tracking")


@pytest.fixture
def tracker(temp_dir: str) -> EliteFactorTracker:
    """创建 EliteFactorTracker 实例。"""
    return EliteFactorTracker(tracking_dir=temp_dir)


@pytest.fixture
def custom_tracker(temp_dir: str) -> EliteFactorTracker:
    """创建使用自定义阈值的 EliteFactorTracker。"""
    threshold = GradeThreshold(
        a_threshold=35.0,
        b_threshold=25.0,
        observation_months=2,
        ic_decay_months=2,
        sharpe_decline_months=3,
        sharpe_decline_ratio=0.4,
    )
    return EliteFactorTracker(tracking_dir=temp_dir, grade_threshold=threshold)


# ─── 分级准入测试 ──────────────────────────────────────────


class TestGradeDetermination:
    """测试分级判定逻辑。"""

    def test_a_grade(self, tracker: EliteFactorTracker):
        """高质量评分应为 A 级。"""
        assert tracker.determine_grade(45.0) == "A"
        assert tracker.determine_grade(40.0) == "A"
        assert tracker.determine_grade(50.0) == "A"

    def test_b_grade(self, tracker: EliteFactorTracker):
        """中等质量评分应为 B 级。"""
        assert tracker.determine_grade(35.0) == "B"
        assert tracker.determine_grade(30.0) == "B"
        assert tracker.determine_grade(39.9) == "B"

    def test_c_grade(self, tracker: EliteFactorTracker):
        """低质量评分应为 C 级。"""
        assert tracker.determine_grade(25.0) == "C"
        assert tracker.determine_grade(0.0) == "C"
        assert tracker.determine_grade(29.9) == "C"

    def test_custom_threshold(self, custom_tracker: EliteFactorTracker):
        """自定义阈值应正确应用。"""
        assert custom_tracker.determine_grade(38.0) == "A"
        assert custom_tracker.determine_grade(30.0) == "B"
        assert custom_tracker.determine_grade(20.0) == "C"


class TestGradeAdmission:
    """测试分级准入机制。"""

    def test_a_grade_becomes_active(self, tracker: EliteFactorTracker):
        """A 级因子应直接成为 active。"""
        snap = tracker.init_tracker(
            factor_id="f_a",
            name="factor_a",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        assert snap["status"] == "active"
        assert snap["grade"] == "A"
        assert snap["quality_score"] == 45.0

    def test_b_grade_enters_observing(self, tracker: EliteFactorTracker):
        """B 级因子应进入观察期。"""
        snap = tracker.init_tracker(
            factor_id="f_b",
            name="factor_b",
            entry_ic=0.08,
            entry_sharpe=1.5,
            quality_score=35.0,
        )
        assert snap["status"] == "observing"
        assert snap["grade"] == "B"
        assert snap["observation_end"] is not None

    def test_c_grade_is_rejected(self, tracker: EliteFactorTracker):
        """C 级因子应被拒绝准入。"""
        snap = tracker.init_tracker(
            factor_id="f_c",
            name="factor_c",
            entry_ic=0.03,
            entry_sharpe=0.5,
            quality_score=20.0,
        )
        assert snap["status"] == "rejected"
        assert snap["grade"] == "C"
        assert snap["quality_score"] == 20.0

    def test_explicit_grade_overrides_score(self, tracker: EliteFactorTracker):
        """显式 grade 应覆盖 quality_score 自动判定。"""
        snap = tracker.init_tracker(
            factor_id="f_exp",
            name="factor_exp",
            entry_ic=0.1,
            entry_sharpe=2.0,
            grade="A",
            quality_score=20.0,  # 低分但显式 A 级
        )
        assert snap["status"] == "active"
        assert snap["grade"] == "A"

    def test_backward_compatibility(self, tracker: EliteFactorTracker):
        """无 grade/score 参数时应默认为 A 级 (向后兼容)。"""
        snap = tracker.init_tracker(
            factor_id="f_old",
            name="factor_old",
            entry_ic=0.05,
            entry_sharpe=1.2,
        )
        assert snap["status"] == "active"
        assert snap["grade"] == "A"

    def test_rejected_factor_cannot_be_updated(self, tracker: EliteFactorTracker):
        """被拒绝的因子不应允许更新。"""
        tracker.init_tracker(
            factor_id="f_rej",
            name="factor_rej",
            entry_ic=0.03,
            entry_sharpe=0.5,
            quality_score=20.0,
        )
        result = tracker.update("f_rej", 0.1)
        assert result is not None
        assert result["status"] == "rejected"
        assert len(result.get("weekly_ic", [])) == 0


# ─── 观察期机制测试 ──────────────────────────────────────────


class TestObservationPeriod:
    """测试 B 级因子观察期机制。"""

    def test_observation_end_set(self, tracker: EliteFactorTracker):
        """观察期结束时间应被设置。"""
        snap = tracker.init_tracker(
            factor_id="f_obs",
            name="factor_obs",
            entry_ic=0.08,
            entry_sharpe=1.5,
            quality_score=35.0,
        )
        obs_end = snap["observation_end"]
        assert obs_end is not None
        # 验证时间差约为 3 个月 (90 天)
        entry_at = datetime.fromisoformat(snap["entry_at"])
        obs_dt = datetime.fromisoformat(obs_end)
        days_diff = (obs_dt - entry_at).days
        assert 85 <= days_diff <= 95  # 3 个月 ≈ 90 天

    def test_observation_custom_period(self, custom_tracker: EliteFactorTracker):
        """自定义观察期 (2个月) 应正确设置。"""
        snap = custom_tracker.init_tracker(
            factor_id="f_obs2",
            name="factor_obs2",
            entry_ic=0.08,
            entry_sharpe=1.5,
            quality_score=30.0,
        )
        obs_end = snap["observation_end"]
        assert obs_end is not None
        entry_at = datetime.fromisoformat(snap["entry_at"])
        obs_dt = datetime.fromisoformat(obs_end)
        days_diff = (obs_dt - entry_at).days
        assert 55 <= days_diff <= 65  # 2 个月 ≈ 60 天

    def test_observing_to_active_after_period(self, tracker: EliteFactorTracker):
        """观察期结束且质量分达标应转为 active。"""
        # 设置一个已过期的观察期
        past_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        snap = tracker.init_tracker(
            factor_id="f_obs_active",
            name="factor_obs_active",
            entry_ic=0.08,
            entry_sharpe=1.5,
            quality_score=35.0,
        )
        # 手动设置观察期为过去
        import fts.core.atomic as atomic
        snap["observation_end"] = past_date
        atomic.atomic_write(str(tracker._path("f_obs_active")), snap)

        # 触发更新以检查状态转换
        updated = tracker.update("f_obs_active", 0.09, is_monthly=True)
        assert updated is not None
        assert updated["status"] == "active"

    def test_observing_to_decaying_after_period(self, tracker: EliteFactorTracker):
        """观察期结束但质量分不足应转为 decaying。"""
        past_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        snap = tracker.init_tracker(
            factor_id="f_obs_decay",
            name="factor_obs_decay",
            entry_ic=0.08,
            entry_sharpe=1.5,
            quality_score=35.0,  # B 级
        )
        import fts.core.atomic as atomic
        snap["observation_end"] = past_date
        # 降低质量分以触发 decaying 路径
        snap["quality_score"] = 25.0
        atomic.atomic_write(str(tracker._path("f_obs_decay")), snap)

        updated = tracker.update("f_obs_decay", 0.09, is_monthly=True)
        assert updated is not None
        assert updated["status"] == "decaying"


# ─── 增强衰减判定测试 ──────────────────────────────────────────


class TestEnhancedDecayDetection:
    """测试增强的衰减检测逻辑。"""

    def test_monthly_ic_decay_triggers_transition(self, custom_tracker: EliteFactorTracker):
        """连续月度 IC < 0 达到阈值应转为 decaying。"""
        snap = custom_tracker.init_tracker(
            factor_id="f_ic_decay",
            name="factor_ic_decay",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        # 模拟连续 2 个月 IC < 0
        custom_tracker.update("f_ic_decay", -0.01, is_monthly=True)
        custom_tracker.update("f_ic_decay", -0.02, is_monthly=True)
        # 自定义阈值 ic_decay_months=2，应触发转换
        updated = custom_tracker.get("f_ic_decay")
        assert updated is not None
        assert updated["status"] == "decaying"
        assert updated["consecutive_zero_months"] == 2

    def test_sharpe_decline_triggers_critical(self, custom_tracker: EliteFactorTracker):
        """连续 Sharpe 下降达到阈值应转为 critical_decay。"""
        snap = custom_tracker.init_tracker(
            factor_id="f_sharpe_decay",
            name="factor_sharpe_decay",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        # 模拟连续 Sharpe 下降 > 40% (自定义阈值 sharpe_decline_ratio=0.4)
        # 首次更新建立 prev_sharpe 基准，后续 3 次下降累计达 sharpe_decline_months=3
        custom_tracker.update("f_sharpe_decay", 0.1, new_sharpe=1.5, is_monthly=True)  # 首次，基准
        custom_tracker.update("f_sharpe_decay", 0.1, new_sharpe=0.8, is_monthly=True)  # -47% (vs 1.5)
        custom_tracker.update("f_sharpe_decay", 0.1, new_sharpe=0.4, is_monthly=True)  # -50% (vs 0.8)
        custom_tracker.update("f_sharpe_decay", 0.1, new_sharpe=0.15, is_monthly=True)  # -63% (vs 0.4)
        # sharpe_decline_months=3, 应触发 critical_decay
        updated = custom_tracker.get("f_sharpe_decay")
        assert updated is not None
        assert updated["status"] == "critical_decay"
        assert updated["consecutive_sharpe_decline_months"] == 3

    def test_sharpe_partial_recovery_resets_counter(self, custom_tracker: EliteFactorTracker):
        """Sharpe 部分恢复应重置连续下降计数。"""
        custom_tracker.init_tracker(
            factor_id="f_recover",
            name="factor_recover",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        custom_tracker.update("f_recover", 0.1, new_sharpe=2.0, is_monthly=True)
        custom_tracker.update("f_recover", 0.1, new_sharpe=1.1, is_monthly=True)  # 下降
        custom_tracker.update("f_recover", 0.1, new_sharpe=1.8, is_monthly=True)  # 恢复
        updated = custom_tracker.get("f_recover")
        assert updated is not None
        assert updated["consecutive_sharpe_decline_months"] == 0

    def test_weekly_zero_ic_triggers_decaying(self, tracker: EliteFactorTracker):
        """周度连续 IC <= 0 达 4 次应转为 decaying。"""
        tracker.init_tracker(
            factor_id="f_weekly",
            name="factor_weekly",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        for _ in range(4):
            tracker.update("f_weekly", -0.01)
        updated = tracker.get("f_weekly")
        assert updated is not None
        assert updated["status"] == "decaying"
        assert updated["consecutive_zero_ic"] == 4


# ─── 自动淘汰测试 (增强版) ──────────────────────────────────


class TestAutoRetireEnhanced:
    """测试增强版自动淘汰逻辑。"""

    def test_critical_decay_is_retired(self, tracker: EliteFactorTracker):
        """critical_decay 状态的因子应被淘汰。"""
        # 创建一个 critical_decay 的因子
        tracker.init_tracker(
            factor_id="f_crit",
            name="factor_crit",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_crit")
        snap["status"] = "critical_decay"
        atomic.atomic_write(str(tracker._path("f_crit")), snap)

        # 使用 min_active_days=0 跳过活跃天数检查
        retired = tracker.auto_retire(min_active_days=0)
        assert "f_crit" in retired

    def test_12_month_ic_zero_is_retired(self, tracker: EliteFactorTracker):
        """连续 12 个月 IC < 0 应被淘汰。"""
        tracker.init_tracker(
            factor_id="f_12m",
            name="factor_12m",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_12m")
        snap["consecutive_zero_months"] = 12
        atomic.atomic_write(str(tracker._path("f_12m")), snap)

        retired = tracker.auto_retire(min_active_days=0)
        assert "f_12m" in retired

    def test_12_month_sharpe_decline_is_retired(self, tracker: EliteFactorTracker):
        """连续 12 个月 Sharpe 下降应被淘汰。"""
        tracker.init_tracker(
            factor_id="f_sharpe_12m",
            name="factor_sharpe_12m",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_sharpe_12m")
        snap["consecutive_sharpe_decline_months"] = 12
        atomic.atomic_write(str(tracker._path("f_sharpe_12m")), snap)

        retired = tracker.auto_retire(min_active_days=0)
        assert "f_sharpe_12m" in retired

    def test_min_active_days_respected(self, tracker: EliteFactorTracker):
        """活跃天数不足的因子不应被淘汰。"""
        tracker.init_tracker(
            factor_id="f_new",
            name="factor_new",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_new")
        snap["status"] = "critical_decay"
        atomic.atomic_write(str(tracker._path("f_new")), snap)

        # min_active_days=30, 因子刚创建 (< 30 天)
        retired = tracker.auto_retire(min_active_days=30)
        assert "f_new" not in retired

        # 降低阈值后应被淘汰
        retired = tracker.auto_retire(min_active_days=0)
        assert "f_new" in retired

    def test_skip_already_retired(self, tracker: EliteFactorTracker):
        """已淘汰的因子不应再次淘汰。"""
        tracker.init_tracker(
            factor_id="f_done",
            name="factor_done",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_done")
        snap["status"] = "retired"
        atomic.atomic_write(str(tracker._path("f_done")), snap)

        retired = tracker.auto_retire()
        assert "f_done" not in retired


# ─── 月度评估流程测试 ──────────────────────────────────────────


class TestMonthlyEvaluation:
    """测试月度增量评估流程。"""

    def test_monthly_eval_generates_report(self, tracker: EliteFactorTracker):
        """月度评估应生成包含状态变化的报告。"""
        # 创建多个不同状态的因子
        tracker.init_tracker(
            factor_id="f1", name="factor_1", entry_ic=0.1,
            entry_sharpe=2.0, quality_score=45.0,
        )
        tracker.init_tracker(
            factor_id="f2", name="factor_2", entry_ic=0.08,
            entry_sharpe=1.5, quality_score=35.0,
        )
        tracker.init_tracker(
            factor_id="f3", name="factor_3", entry_ic=0.03,
            entry_sharpe=0.5, quality_score=20.0,
        )

        report = tracker.run_monthly_evaluation()
        assert "total" in report
        assert "status_changes" in report
        assert "grade_distribution" in report
        assert report["total"] == 3
        assert report["grade_distribution"]["A"] == 1
        assert report["grade_distribution"]["B"] == 1
        assert report["grade_distribution"]["C"] == 1

    def test_monthly_eval_updates_status(self, tracker: EliteFactorTracker):
        """月度评估应更新因子状态。"""
        tracker.init_tracker(
            factor_id="f_monthly",
            name="factor_monthly",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        # 模拟 3 个月 IC < 0
        for _ in range(3):
            tracker.update("f_monthly", -0.01, is_monthly=True)

        # 执行月度评估
        report = tracker.run_monthly_evaluation()
        factor = tracker.get("f_monthly")
        assert factor is not None
        assert factor["status"] == "decaying"


# ─── 报告生成测试 ──────────────────────────────────────────


class TestReportGeneration:
    """测试报告生成功能。"""

    def test_report_shows_grade_distribution(self, tracker: EliteFactorTracker):
        """报告应包含等级分布。"""
        tracker.init_tracker(
            factor_id="f_a", name="factor_a", entry_ic=0.1,
            entry_sharpe=2.0, quality_score=45.0,
        )
        tracker.init_tracker(
            factor_id="f_b", name="factor_b", entry_ic=0.08,
            entry_sharpe=1.5, quality_score=35.0,
        )
        tracker.init_tracker(
            factor_id="f_c", name="factor_c", entry_ic=0.03,
            entry_sharpe=0.5, quality_score=20.0,
        )

        report = tracker.report()
        assert "grade_distribution" in report
        gd = report["grade_distribution"]
        assert gd["A"] == 1
        assert gd["B"] == 1
        assert gd["C"] == 1

    def test_report_shows_status_counts(self, tracker: EliteFactorTracker):
        """报告应包含状态计数。"""
        tracker.init_tracker(
            factor_id="f_active", name="active_factor", entry_ic=0.1,
            entry_sharpe=2.0, quality_score=45.0,
        )
        tracker.init_tracker(
            factor_id="f_obs", name="obs_factor", entry_ic=0.08,
            entry_sharpe=1.5, quality_score=35.0,
        )
        tracker.init_tracker(
            factor_id="f_rej", name="rej_factor", entry_ic=0.03,
            entry_sharpe=0.5, quality_score=20.0,
        )

        report = tracker.report()
        sc = report["status_counts"]
        assert sc["active"] == 1
        assert sc["observing"] == 1
        assert sc["rejected"] == 1
        assert sc["total"] == 3


# ─── 工具函数测试 ──────────────────────────────────────────


class TestUtilityFunctions:
    """测试内部工具函数。"""

    def test_calc_decay_6m_no_decay(self):
        """稳定 IC 序列衰减率应为 0。"""
        ic_series = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        assert _calc_decay_6m(ic_series) == 0.0

    def test_calc_decay_6m_significant_decay(self):
        """前高后低的 IC 序列应有显著衰减。"""
        ic_series = [0.2, 0.2, 0.2, 0.2, 0.05, 0.05, 0.05, 0.05]
        decay = _calc_decay_6m(ic_series)
        assert decay > 0.3  # 衰减率应 > 30%

    def test_calc_decay_6m_insufficient_data(self):
        """数据不足时衰减率应为 0。"""
        assert _calc_decay_6m([0.1, 0.2]) == 0.0

    def test_is_past_future(self):
        """未来时间戳不应被判定为已过。"""
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        assert not _is_past(future)

    def test_is_past_past(self):
        """过去时间戳应被判定为已过。"""
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        assert _is_past(past)

    def test_is_past_invalid(self):
        """无效时间戳应返回 False。"""
        assert not _is_past("invalid-date")


# ─── AutoRetireManager 测试 ──────────────────────────────────


class TestAutoRetireManager:
    """测试 AutoRetireManager 封装。"""

    def test_manager_runs_retirement(self, tracker: EliteFactorTracker):
        """管理器应能执行淘汰。"""
        tracker.init_tracker(
            factor_id="f_retire",
            name="factor_retire",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_retire")
        snap["status"] = "critical_decay"
        atomic.atomic_write(str(tracker._path("f_retire")), snap)

        config = AutoRetireConfig(min_active_days=0)
        manager = AutoRetireManager(tracker, config)
        retired = manager.run()
        assert "f_retire" in retired

    def test_can_reevaluate_after_cooldown(self, tracker: EliteFactorTracker):
        """冷却期过后应允许重新评估。"""
        tracker.init_tracker(
            factor_id="f_reeval",
            name="factor_reeval",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_reeval")
        snap["status"] = "retired"
        # 设置 last_updated 为 10 天前
        snap["last_updated"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        atomic.atomic_write(str(tracker._path("f_reeval")), snap)

        manager = AutoRetireManager(tracker, AutoRetireConfig(cooldown_days=7))
        assert manager.can_reevaluate("f_reeval") is True

    def test_cannot_reevaluate_before_cooldown(self, tracker: EliteFactorTracker):
        """冷却期内不应允许重新评估。"""
        tracker.init_tracker(
            factor_id="f_wait",
            name="factor_wait",
            entry_ic=0.1,
            entry_sharpe=2.0,
            quality_score=45.0,
        )
        import fts.core.atomic as atomic
        snap = tracker.get("f_wait")
        snap["status"] = "retired"
        # 设置 last_updated 为 1 天前
        snap["last_updated"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        atomic.atomic_write(str(tracker._path("f_wait")), snap)

        manager = AutoRetireManager(tracker, AutoRetireConfig(cooldown_days=7))
        assert manager.can_reevaluate("f_wait") is False


# ─── 边缘情况测试 ──────────────────────────────────────────


class TestEdgeCases:
    """测试边缘情况。"""

    def test_update_nonexistent_factor(self, tracker: EliteFactorTracker):
        """更新不存在的因子应返回 None。"""
        result = tracker.update("nonexistent", 0.1)
        assert result is None

    def test_get_nonexistent_factor(self, tracker: EliteFactorTracker):
        """获取不存在的因子应返回 None。"""
        result = tracker.get("nonexistent")
        assert result is None

    def test_multiple_factors_independent(self, tracker: EliteFactorTracker):
        """多个因子应相互独立。"""
        tracker.init_tracker(
            factor_id="f1", name="factor_1", entry_ic=0.1,
            entry_sharpe=2.0, quality_score=45.0,
        )
        tracker.init_tracker(
            factor_id="f2", name="factor_2", entry_ic=0.05,
            entry_sharpe=1.0, quality_score=40.0,
        )
        tracker.update("f1", 0.2)
        tracker.update("f2", -0.05)

        f1 = tracker.get("f1")
        f2 = tracker.get("f2")
        assert f1 is not None
        assert f2 is not None
        assert f1["current_ic"] == 0.2
        assert f2["current_ic"] == -0.05
        assert f1["consecutive_zero_ic"] == 0
        assert f2["consecutive_zero_ic"] == 1

    def test_list_all_after_multiple_inits(self, tracker: EliteFactorTracker):
        """list_all 应返回所有初始化的因子。"""
        for i in range(5):
            tracker.init_tracker(
                factor_id=f"f{i}",
                name=f"factor_{i}",
                entry_ic=0.1,
                entry_sharpe=2.0,
                quality_score=45.0,
            )
        all_snapshots = tracker.list_all()
        assert len(all_snapshots) == 5

    def test_get_by_status_filter(self, tracker: EliteFactorTracker):
        """get_by_status 应按状态筛选。"""
        tracker.init_tracker(
            factor_id="f_active", name="fa", entry_ic=0.1,
            entry_sharpe=2.0, quality_score=45.0,
        )
        tracker.init_tracker(
            factor_id="f_obs", name="fb", entry_ic=0.08,
            entry_sharpe=1.5, quality_score=35.0,
        )
        tracker.init_tracker(
            factor_id="f_rej", name="fc", entry_ic=0.03,
            entry_sharpe=0.5, quality_score=20.0,
        )

        assert len(tracker.get_by_status("active")) == 1
        assert len(tracker.get_by_status("observing")) == 1
        assert len(tracker.get_by_status("rejected")) == 1
        assert len(tracker.get_by_status("decaying")) == 0
