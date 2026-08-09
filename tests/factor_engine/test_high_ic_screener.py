"""高IC筛查器单元测试（Phase B.4 集成）。"""
# pylint: disable=missing-function-docstring,too-many-locals

import pytest

from fts.factor_engine.high_ic_screener import (
    HighICScreener,
    HighICScreenConfig,
    HighICScreenReport,
    HighICCheckItem,
)


# ─── 工具: 构造评估数据 ────────────────────────────────────


def _base_evaluation(**overrides):
    """构造一份完整合格的评估数据。"""
    ev = {
        "factor_id": "fct_test001",
        "trace_id": "trace_test",
        "level_1_backtest": {
            "ic": 0.04,
            "icir": 0.8,
            "sharpe": 2.0,
            "max_drawdown": 0.08,
            "monotonicity": True,
            "oos_ratio": 0.3,
            "turnover_monthly": 0.3,
            "decay_6m": 0.2,
            "ic_volatility": 0.15,
        },
        "level_2_economic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 4,
            "dimensions_passed": 4,
        },
        "level_3_multiple": {
            "bonferroni_p": 0.01,
            "fdr_q": 0.01,
            "adjusted_t": 4.0,
            "passed": True,
        },
        "walk_forward": {
            "n_windows_completed": 4,
            "windows": [
                {"ic": 0.05},
                {"ic": 0.04},
                {"ic": 0.045},
                {"ic": 0.038},
            ],
        },
        "backtest_pipeline": {"net_excess_return": 0.02},
    }
    ev.update(overrides)
    return ev


def _base_factor(**overrides):
    f = {
        "factor_id": "fct_test001",
        "name": "fut_test_factor",
        "market": "futures",
        "family": "momentum",
    }
    f.update(overrides)
    return f


# ─── 1. 基础功能 ───────────────────────────────────────────


class TestBasicScreen:
    def test_screen_returns_report(self):
        screener = HighICScreener()
        report = screener.screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        assert isinstance(report, HighICScreenReport)
        assert report.factor_id == "fct_test001"
        assert report.market == "futures"

    def test_16_check_items_created(self):
        screener = HighICScreener()
        report = screener.screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        # 16 项打分 + 5 项一票否决 = 21 项
        assert len(report.items) == 21

    def test_qualified_factor_grade_a(self):
        screener = HighICScreener()
        report = screener.screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        assert report.grade == "A"
        assert report.disposition == "正常入库"
        assert report.veto_triggered is False
        assert report.total_score >= 85.0

    def test_to_dict_serializable(self):
        screener = HighICScreener()
        report = screener.screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["grade"] == "A"
        assert isinstance(d["items"], list)
        assert len(d["items"]) == 21


# ─── 2. 一票否决 ───────────────────────────────────────────


class TestVetoChecks:
    def test_veto_v1_oos_decay_over_30(self):
        ev = _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "decay_6m": 0.6,
            },
            walk_forward={
                "n_windows_completed": 2,
                "windows": [{"ic": 0.1}, {"ic": 0.02}],
            },
        )
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.veto_triggered is True
        assert report.grade == "C"
        assert any("V1" in r for r in report.veto_reasons)

    def test_veto_v2_extreme_perturb_over_25(self):
        ev = _base_evaluation(extreme_perturbation={"ic_drop": 0.4})
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.veto_triggered is True
        assert any("V2" in r for r in report.veto_reasons)

    def test_veto_v2_missing_data_not_fatal(self):
        # 无扰动数据 → 跳过，不误杀
        ev = _base_evaluation()
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        item = report.item("veto_extreme_perturb")
        assert item is not None
        assert item.passed is None
        assert report.veto_triggered is False

    def test_veto_v3_corr_over_70(self):
        report = HighICScreener().screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
            correlation_metadata={"max_corr_detected": 0.85},
        )
        assert report.veto_triggered is True
        assert any("V3" in r for r in report.veto_reasons)

    def test_veto_v4_net_excess_negative(self):
        report = HighICScreener().screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
            backtest_pipeline={"net_excess_return": -0.01},
        )
        assert report.veto_triggered is True
        assert any("V4" in r for r in report.veto_reasons)

    def test_veto_v5_no_logic(self):
        ev = _base_evaluation(level_2_economic={
            "theory": 1, "behavioral": 1,
            "microstructure": 1, "institutional": 1,
        })
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.veto_triggered is True
        assert any("V5" in r for r in report.veto_reasons)

    def test_veto_priority_over_scoring(self):
        # 即使打分离，一票否决仍然 C 级
        ev = _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "decay_6m": 0.8,
            },
        )
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.grade == "C"
        assert report.veto_triggered is True


# ─── 3. 评级边界 ───────────────────────────────────────────


class TestGradeBoundary:
    def _minimal_eval(self, ic=0.05, icir=0.6):
        """构造刚好达标的评估。"""
        return _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "ic": ic, "icir": icir,
            },
        )

    def test_grade_a_above_85(self):
        report = HighICScreener().screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        assert report.grade == "A"

    def test_grade_b_mid_range(self):
        # 降低 IC 合理性（0.01 → 低分）+ 低胜率 → B 级
        ev = _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "ic": 0.005, "icir": 0.35,
            },
            walk_forward={
                "n_windows_completed": 6,
                "windows": [
                    {"ic": 0.02}, {"ic": -0.01}, {"ic": 0.03},
                    {"ic": -0.02}, {"ic": 0.01}, {"ic": -0.01},
                ],
            },
        )
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.grade == "B"
        assert report.disposition == "暂缓优化"

    def test_grade_c_below_60(self):
        ev = _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "ic": 0.001, "icir": 0.05,
                "max_drawdown": 0.5, "monotonicity": False,
                "turnover_monthly": 0.95, "decay_6m": 0.8,
                "ic_volatility": 0.8,
            },
            level_2_economic={
                "theory": 1, "behavioral": 1,
                "microstructure": 1, "institutional": 1,
            },
            walk_forward={
                "n_windows_completed": 4,
                "windows": [
                    {"ic": 0.01}, {"ic": -0.01},
                    {"ic": 0.005}, {"ic": -0.005},
                ],
            },
        )
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.grade == "C"

    def test_all_skipped_pass_not_fatal(self):
        # 全部数据缺失 → 放行不拦截（不误杀原则）
        report = HighICScreener().screen(
            factor=_base_factor(), evaluation={"factor_id": "x"},
        )
        assert report.grade == "PASS"
        assert report.disposition == "数据不足放行"
        assert report.total_score == 0.0


# ─── 4. 得分逻辑细节 ───────────────────────────────────────


class TestScoringDetail:
    def test_ic_mean_center_full_score(self):
        ev = _base_evaluation(level_1_backtest={
            **_base_evaluation()["level_1_backtest"], "ic": 0.04,
        })
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        item = report.item("ic_mean")
        assert item is not None
        assert item.score == 8.0

    def test_ic_mean_over_alert_penalized(self):
        # |IC|=0.1 远高于 0.07 警戒 → 严重扣分（过拟合嫌疑）
        ev = _base_evaluation(level_1_backtest={
            **_base_evaluation()["level_1_backtest"], "ic": 0.10,
        })
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        item = report.item("ic_mean")
        assert item is not None
        assert item.score < 8.0

    def test_icir_low_pseudo_strong_penalty(self):
        # 高IC低ICIR = 伪强因子
        ev = _base_evaluation(level_1_backtest={
            **_base_evaluation()["level_1_backtest"], "ic": 0.09, "icir": 0.1,
        })
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        item = report.item("icir")
        assert item is not None
        assert item.score < 4.0

    def test_turnover_weekly_scaling(self):
        # 月度换手 0.8 ≈ 周度 0.185 → 满分
        ev = _base_evaluation(level_1_backtest={
            **_base_evaluation()["level_1_backtest"], "turnover_monthly": 0.8,
        })
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        item = report.item("turnover")
        assert item is not None
        assert item.raw_value is not None
        assert item.raw_value <= 0.2

    def test_missing_data_skipped_not_zero_scored(self):
        # 缺失 walk_forward → ic_win_rate/multi_regime 应为 skipped
        ev = _base_evaluation(walk_forward=None)
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.item("ic_win_rate").passed is None
        assert report.item("multi_regime").passed is None


# ─── 5. 市场统一性 ─────────────────────────────────────────


class TestMarketUniformity:
    @pytest.mark.parametrize("market", ["futures", "stock"])
    def test_same_config_all_markets(self, market):
        factor = _base_factor(market=market)
        report = HighICScreener().screen(
            factor=factor, evaluation=_base_evaluation(),
        )
        assert report.market == market
        assert report.grade == "A"

    def test_default_config_single_instance(self):
        # 默认配置阈值恒定：同一评估结果 → 相同评级，与市场无关
        config = HighICScreenConfig()
        assert config.grade_A_min == 85.0
        assert config.grade_B_min == 60.0
        assert config.ic_alert == 0.07


# ─── 6. B 级优化建议 ───────────────────────────────────────


class TestSuggestions:
    def test_grade_b_has_suggestions(self):
        ev = _base_evaluation(
            level_1_backtest={
                **_base_evaluation()["level_1_backtest"],
                "ic": 0.005, "icir": 0.35,
            },
            walk_forward={
                "n_windows_completed": 6,
                "windows": [
                    {"ic": 0.02}, {"ic": -0.01}, {"ic": 0.03},
                    {"ic": -0.02}, {"ic": 0.01}, {"ic": -0.01},
                ],
            },
        )
        report = HighICScreener().screen(factor=_base_factor(), evaluation=ev)
        assert report.grade == "B"
        assert len(report.improvement_suggestions) > 0

    def test_grade_a_no_suggestions(self):
        report = HighICScreener().screen(
            factor=_base_factor(), evaluation=_base_evaluation(),
        )
        assert report.grade == "A"
        assert report.improvement_suggestions == []
