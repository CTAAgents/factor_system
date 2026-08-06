"""tests/factor_engine/test_factor_quality_card.py — 因子质量评分卡测试。

覆盖范围:
    1. 评分映射函数 (IC, Sharpe, 衰减率等)
    2. FactorQualityCard 核心计算逻辑
    3. 分级准入判定
    4. 便捷函数
    5. 边界条件

版本: v1.0.0
"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_quality_card import (
    FactorQualityCard,
    FactorQualityCardConfig,
    FactorQualityScore,
    DimensionScore,
    _map_ic_to_score,
    _map_icir_to_score,
    _map_sharpe_to_score,
    _map_calmar_to_score,
    _map_stability_to_score,
    _map_decay_to_score,
    _map_capacity_to_score,
    _map_turnover_to_score,
    _map_correlation_to_score,
    _map_logic_to_score,
    _map_frequency_to_score,
    _map_coverage_to_score,
    compute_total_score,
    determine_grade,
)
from fts.factor_engine.walk_forward import WalkForwardResult


# ══════════════════════════════════════════════════════════
# 评分映射函数测试
# ══════════════════════════════════════════════════════════


class TestICMapping:
    """IC → 有效性分映射测试。"""

    def test_ic_zero(self) -> None:
        assert _map_ic_to_score(0.0) == 0.0

    def test_ic_negative(self) -> None:
        assert _map_ic_to_score(-0.05) == 0.0

    def test_ic_001(self) -> None:
        assert _map_ic_to_score(0.01) == 1.0

    def test_ic_003(self) -> None:
        assert _map_ic_to_score(0.03) == 3.0

    def test_ic_008(self) -> None:
        assert _map_ic_to_score(0.08) == 5.0

    def test_ic_above_008(self) -> None:
        assert _map_ic_to_score(0.10) == 5.0

    def test_ic_between_001_and_003(self) -> None:
        # 0.02 在 0.01-0.03 之间，应该线性插值到 1-3 分
        score = _map_ic_to_score(0.02)
        assert 1.0 <= score <= 3.0

    def test_ic_between_0_and_001(self) -> None:
        score = _map_ic_to_score(0.005)
        assert 0.0 <= score <= 1.0


class TestICIRMapping:
    """ICIR → 有效性补充分映射测试。"""

    def test_icir_zero(self) -> None:
        assert _map_icir_to_score(0.0) == 0.0

    def test_icir_negative(self) -> None:
        assert _map_icir_to_score(-1.0) == 0.0

    def test_icir_1(self) -> None:
        assert _map_icir_to_score(1.0) == 1.0

    def test_icir_2(self) -> None:
        assert _map_icir_to_score(2.0) == 3.0

    def test_icir_3(self) -> None:
        assert _map_icir_to_score(3.0) == 5.0

    def test_icir_above_3(self) -> None:
        assert _map_icir_to_score(5.0) == 5.0


class TestSharpeMapping:
    """Sharpe → 收益性分映射测试。"""

    def test_sharpe_zero(self) -> None:
        assert _map_sharpe_to_score(0.0) == 0.0

    def test_sharpe_negative(self) -> None:
        assert _map_sharpe_to_score(-1.0) == 0.0

    def test_sharpe_05(self) -> None:
        assert _map_sharpe_to_score(0.5) == 1.0

    def test_sharpe_15(self) -> None:
        assert _map_sharpe_to_score(1.5) == 3.0

    def test_sharpe_3(self) -> None:
        assert _map_sharpe_to_score(3.0) == 5.0

    def test_sharpe_above_3(self) -> None:
        assert _map_sharpe_to_score(4.0) == 5.0

    def test_sharpe_at_10(self) -> None:
        """Sharpe=10 是惩罚边界，应得 5 分（P1 过拟合保护）。"""
        assert _map_sharpe_to_score(10.0) == 5.0

    def test_sharpe_above_10_penalty(self) -> None:
        """Sharpe>10 时线性减分（P1 过拟合保护）。"""
        assert _map_sharpe_to_score(12) == 4.0    # (12-10)*0.5=1.0 penalty → 5-1=4
        assert _map_sharpe_to_score(15) == 2.5    # (15-10)*0.5=2.5 penalty → 5-2.5=2.5
        assert _map_sharpe_to_score(20) == 0.0    # (20-10)*0.5=5.0 penalty → 5-5=0
        assert _map_sharpe_to_score(25) == 0.0    # 惩罚上限 5.0

    def test_sharpe_between_05_and_15(self) -> None:
        score = _map_sharpe_to_score(1.0)
        assert 1.0 <= score <= 3.0


class TestCalmarMapping:
    """Calmar → 收益性补充分映射测试。"""

    def test_calmar_zero(self) -> None:
        assert _map_calmar_to_score(0.0) == 0.0

    def test_calmar_negative(self) -> None:
        assert _map_calmar_to_score(-0.5) == 0.0

    def test_calmar_05(self) -> None:
        assert _map_calmar_to_score(0.5) == 1.0

    def test_calmar_1(self) -> None:
        assert _map_calmar_to_score(1.0) == 3.0

    def test_calmar_2(self) -> None:
        assert _map_calmar_to_score(2.0) == 5.0

    def test_calmar_above_2(self) -> None:
        assert _map_calmar_to_score(3.0) == 5.0


class TestDecayMapping:
    """衰减率 → 鲁棒性分映射测试。"""

    def test_decay_low(self) -> None:
        assert _map_decay_to_score(0.05) == 5.0

    def test_decay_at_01(self) -> None:
        assert _map_decay_to_score(0.1) == 5.0

    def test_decay_mid(self) -> None:
        assert _map_decay_to_score(0.2) == 3.0

    def test_decay_at_03(self) -> None:
        assert _map_decay_to_score(0.3) == 3.0

    def test_decay_high(self) -> None:
        assert _map_decay_to_score(0.4) == 1.0

    def test_decay_at_05(self) -> None:
        assert _map_decay_to_score(0.5) == 1.0

    def test_decay_above_05(self) -> None:
        assert _map_decay_to_score(0.6) == 0.0

    def test_decay_negative(self) -> None:
        # 负衰减表示改善，应该给最高分
        assert _map_decay_to_score(-0.05) == 5.0


class TestCapacityMapping:
    """容量估算 → 容量分映射测试 (期货优化阈值)。"""

    def test_capacity_zero(self) -> None:
        assert _map_capacity_to_score(0.0) == 0.0

    def test_capacity_low(self) -> None:
        # 5M < 10M → 2分
        assert _map_capacity_to_score(5_000_000) == 2.0

    def test_capacity_10m(self) -> None:
        # 10M = 10M → 3分
        assert _map_capacity_to_score(10_000_000) == 3.0

    def test_capacity_50m(self) -> None:
        # 50M = 50M → 4分
        assert _map_capacity_to_score(50_000_000) == 4.0

    def test_capacity_100m(self) -> None:
        # 100M = 1亿 → 5分
        assert _map_capacity_to_score(100_000_000) == 5.0

    def test_capacity_above_100m(self) -> None:
        assert _map_capacity_to_score(200_000_000) == 5.0


class TestTurnoverMapping:
    """换手率 → 交易性分映射测试 (期货高频因子优化)。"""

    def test_very_low_turnover(self) -> None:
        # 0.01 = 1%, < 10% → 1分
        assert _map_turnover_to_score(0.01) == 1.0

    def test_low_turnover(self) -> None:
        # 0.05 = 5%, < 10% → 1分
        assert _map_turnover_to_score(0.05) == 1.0

    def test_optimal_low(self) -> None:
        # 0.1 = 10%, 10%-1000% → 3分
        assert _map_turnover_to_score(0.1) == 3.0

    def test_optimal_mid(self) -> None:
        # 0.3 = 30%, 10%-1000% → 3分
        assert _map_turnover_to_score(0.3) == 3.0

    def test_optimal_high(self) -> None:
        # 0.5 = 50%, 50%-500% → 5分
        assert _map_turnover_to_score(0.5) == 5.0

    def test_high_turnover(self) -> None:
        # 0.8 = 80%, 50%-500% → 5分
        assert _map_turnover_to_score(0.8) == 5.0

    def test_very_high_turnover(self) -> None:
        # 1.5 = 150%, 50%-500% → 5分
        assert _map_turnover_to_score(1.5) == 5.0

    def test_percentage_format(self) -> None:
        # 百分比格式: 572.34 = 572.34%, 10%-1000% → 3分
        assert _map_turnover_to_score(572.34) == 3.0

    def test_percentage_optimal(self) -> None:
        # 百分比格式: 100 = 100%, 50%-500% → 5分
        assert _map_turnover_to_score(100.0) == 5.0


class TestCorrelationMapping:
    """最大相关性 → 多样性分映射测试。"""

    def test_low_correlation(self) -> None:
        assert _map_correlation_to_score(0.2) == 5.0

    def test_correlation_03(self) -> None:
        assert _map_correlation_to_score(0.3) == 5.0

    def test_mid_correlation(self) -> None:
        assert _map_correlation_to_score(0.4) == 3.0

    def test_correlation_05(self) -> None:
        assert _map_correlation_to_score(0.5) == 3.0

    def test_high_correlation(self) -> None:
        assert _map_correlation_to_score(0.6) == 1.0

    def test_correlation_07(self) -> None:
        assert _map_correlation_to_score(0.7) == 1.0

    def test_very_high_correlation(self) -> None:
        assert _map_correlation_to_score(0.8) == 0.0


class TestLogicMapping:
    """经济逻辑分 → 逻辑性分映射测试。"""

    def test_logic_zero(self) -> None:
        assert _map_logic_to_score(0) == 0.0

    def test_logic_negative(self) -> None:
        assert _map_logic_to_score(-1) == 0.0

    def test_logic_1(self) -> None:
        assert _map_logic_to_score(1) == 1.0

    def test_logic_3(self) -> None:
        assert _map_logic_to_score(3) == 3.0

    def test_logic_5(self) -> None:
        assert _map_logic_to_score(5) == 5.0

    def test_logic_above_5(self) -> None:
        assert _map_logic_to_score(10) == 5.0


class TestFrequencyMapping:
    """数据频率 → 实时性分映射测试 (期货优化)。"""

    def test_tick(self) -> None:
        assert _map_frequency_to_score("tick") == 5.0

    def test_minute(self) -> None:
        assert _map_frequency_to_score("minute") == 4.0

    def test_hour(self) -> None:
        assert _map_frequency_to_score("hour") == 3.0

    def test_daily(self) -> None:
        # 期货 daily 频率给 2 分 (之前为 1 分)
        assert _map_frequency_to_score("daily") == 2.0


class TestCoverageMapping:
    """跨品种覆盖率 → 兼容性分映射测试 (期货优化)。"""

    def test_low_coverage(self) -> None:
        # 0.3 = 30%, 2分 (之前为 0 分)
        assert _map_coverage_to_score(0.3) == 2.0

    def test_coverage_05(self) -> None:
        # 0.5 = 50%, 3分 (之前为 1 分)
        assert _map_coverage_to_score(0.5) == 3.0

    def test_mid_coverage(self) -> None:
        # 0.7 = 70%, 4分 (之前为 3 分)
        assert _map_coverage_to_score(0.7) == 4.0

    def test_high_coverage(self) -> None:
        assert _map_coverage_to_score(0.9) == 5.0

    def test_full_coverage(self) -> None:
        assert _map_coverage_to_score(1.0) == 5.0


class TestStabilityMapping:
    """WalkForward 结果 → 稳定性分映射测试。"""

    def test_perfect_stability(self) -> None:
        wf_result: WalkForwardResult = {
            "ic_consistency": 0.9,
            "ic_volatility": 0.1,
            "consistency_score": 80.0,
            "n_windows_completed": 4,
        }
        score = _map_stability_to_score(wf_result)
        assert score >= 4.0  # 高分 (max=5.0)

    def test_low_stability(self) -> None:
        wf_result: WalkForwardResult = {
            "ic_consistency": 0.3,
            "ic_volatility": 0.8,
        }
        score = _map_stability_to_score(wf_result)
        assert score <= 3.0  # 低分

    def test_missing_fields(self) -> None:
        wf_result: WalkForwardResult = {}
        score = _map_stability_to_score(wf_result)
        assert score >= 0.0

    def test_consistency_score_boosts_rating(self) -> None:
        """高 consistency_score 应提升稳定性评分。"""
        wf_low: WalkForwardResult = {
            "ic_consistency": 0.5,
            "ic_volatility": 0.3,
            "consistency_score": 30.0,
            "n_windows_completed": 2,
        }
        wf_high: WalkForwardResult = {
            "ic_consistency": 0.5,
            "ic_volatility": 0.3,
            "consistency_score": 80.0,
            "n_windows_completed": 4,
        }
        score_low = _map_stability_to_score(wf_low)
        score_high = _map_stability_to_score(wf_high)
        assert score_high > score_low

    def test_more_windows_improve_score(self) -> None:
        """更多窗口数应提高稳定性评分。"""
        wf_few: WalkForwardResult = {
            "ic_consistency": 0.6,
            "ic_volatility": 0.2,
            "consistency_score": 50.0,
            "n_windows_completed": 1,
        }
        wf_many: WalkForwardResult = {
            "ic_consistency": 0.6,
            "ic_volatility": 0.2,
            "consistency_score": 50.0,
            "n_windows_completed": 4,
        }
        score_few = _map_stability_to_score(wf_few)
        score_many = _map_stability_to_score(wf_many)
        assert score_many > score_few

    def test_maximum_possible_score(self) -> None:
        """所有指标最优时，分数应接近 5 分上限。"""
        wf: WalkForwardResult = {
            "ic_consistency": 1.0,
            "ic_volatility": 0.0,
            "consistency_score": 100.0,
            "n_windows_completed": 4,
        }
        score = _map_stability_to_score(wf)
        assert score == 5.0

    def test_all_zero_is_zero(self) -> None:
        """所有指标为 0 时，分数应为 0。"""
        wf: WalkForwardResult = {
            "ic_consistency": 0.0,
            "ic_volatility": 1.0,
            "consistency_score": 0.0,
            "n_windows_completed": 0,
        }
        score = _map_stability_to_score(wf)
        assert score == 0.0


# ══════════════════════════════════════════════════════════
# FactorQualityCard 核心计算测试
# ══════════════════════════════════════════════════════════


class TestFactorQualityCard:
    """FactorQualityCard 类测试。"""

    def test_default_config(self) -> None:
        card = FactorQualityCard()
        assert card._config.get("total_max", 50) == 50

    def test_custom_config(self) -> None:
        config: FactorQualityCardConfig = {
            "total_max": 100,
            "grade_A_threshold": 80.0,
            "grade_B_min": 60.0,
        }
        card = FactorQualityCard(config)
        assert card._config.get("total_max") == 100

    def test_evaluate_returns_valid_score(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_test",
            ic=0.05,
            sharpe=2.1,
        )
        assert score["factor_id"] == "fct_test"
        assert 0 <= score["total_score"] <= 50
        assert score["grade"] in ("A", "B", "C")
        assert len(score["dimension_scores"]) == 10

    def test_evaluate_with_walk_forward(self) -> None:
        card = FactorQualityCard()
        wf_result: WalkForwardResult = {
            "ic_consistency": 0.8,
            "ic_volatility": 0.2,
            "consistency_score": 75.0,
        }
        score = card.evaluate(
            factor_id="fct_wf",
            ic=0.05,
            sharpe=2.1,
            walk_forward_result=wf_result,
        )
        assert score["factor_id"] == "fct_wf"

    def test_evaluate_all_dimensions(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_full",
            ic=0.06,
            sharpe=2.5,
            decay_rate=0.1,
            turnover=0.2,
            correlation_max=0.35,
            logic_score=4,
            data_frequency="minute",
            cross_symbol_coverage=0.85,
            capacity_estimate=80_000_000,
            icir=2.5,
            calmar=1.5,
        )
        dim_names = {d["name"] for d in score["dimension_scores"]}
        expected_names = {
            "ic_score", "sharpe_score", "stability_score",
            "robustness_score", "capacity_score", "tradability_score",
            "diversity_score", "logic_score", "timeliness_score",
            "compatibility_score",
        }
        assert dim_names == expected_names

    def test_dimension_scores_in_range(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_range",
            ic=0.05,
            sharpe=2.1,
        )
        for ds in score["dimension_scores"]:
            assert 0 <= ds["score"] <= 5

    def test_perfect_factor_gets_high_score(self) -> None:
        card = FactorQualityCard()
        wf_result: WalkForwardResult = {
            "ic_consistency": 0.9,
            "ic_volatility": 0.1,
        }
        score = card.evaluate(
            factor_id="fct_perfect",
            ic=0.08,
            sharpe=3.5,
            walk_forward_result=wf_result,
            decay_rate=0.05,
            turnover=0.2,
            correlation_max=0.2,
            logic_score=5,
            data_frequency="tick",
            cross_symbol_coverage=0.95,
            capacity_estimate=150_000_000,
            icir=4.0,
            calmar=2.5,
        )
        assert score["total_score"] > 40  # 应该是 A 级
        assert score["grade"] == "A"

    def test_poor_factor_gets_low_score(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_poor",
            ic=0.005,
            sharpe=0.3,
            decay_rate=0.6,
            turnover=0.02,
            correlation_max=0.85,
            logic_score=1,
            data_frequency="daily",
            cross_symbol_coverage=0.3,
            capacity_estimate=1_000_000,
        )
        assert score["total_score"] < 30  # 应该是 C 级
        assert score["grade"] == "C"

    def test_score_has_metadata(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_meta",
            ic=0.05,
            sharpe=2.1,
        )
        assert "score_id" in score
        assert "evaluated_at" in score
        assert "score_version" in score
        assert score["score_version"] == "v1"


# ══════════════════════════════════════════════════════════
# 分级准入测试
# ══════════════════════════════════════════════════════════


class TestGradeClassification:
    """分级准入判定测试。"""

    def test_grade_A(self) -> None:
        assert determine_grade(45.0) == "A"
        assert determine_grade(40.0) == "A"

    def test_grade_B(self) -> None:
        assert determine_grade(35.0) == "B"
        assert determine_grade(30.0) == "B"

    def test_grade_C(self) -> None:
        assert determine_grade(25.0) == "C"
        assert determine_grade(0.0) == "C"

    def test_custom_thresholds(self) -> None:
        assert determine_grade(35.0, th_A=35.0, th_B_min=25.0) == "A"
        assert determine_grade(30.0, th_A=35.0, th_B_min=25.0) == "B"
        assert determine_grade(20.0, th_A=35.0, th_B_min=25.0) == "C"

    def test_card_grade_A(self) -> None:
        config: FactorQualityCardConfig = {
            "grade_A_threshold": 40.0,
            "grade_B_min": 30.0,
        }
        card = FactorQualityCard(config)
        assert card._determine_grade(40.0) == "A"

    def test_card_grade_B(self) -> None:
        card = FactorQualityCard()
        assert card._determine_grade(35.0) == "B"

    def test_card_grade_C(self) -> None:
        card = FactorQualityCard()
        assert card._determine_grade(20.0) == "C"


# ══════════════════════════════════════════════════════════
# 便捷函数测试
# ══════════════════════════════════════════════════════════


class TestComputeTotalScore:
    """compute_total_score 便捷函数测试。"""

    def test_all_max(self) -> None:
        dims: list[DimensionScore] = [
            {"name": f"dim_{i}", "score": 5.0} for i in range(10)
        ]
        weights = [1.0] * 10
        total = compute_total_score(dims, weights)
        assert total == 50.0

    def test_all_zero(self) -> None:
        dims: list[DimensionScore] = [
            {"name": f"dim_{i}", "score": 0.0} for i in range(10)
        ]
        weights = [1.0] * 10
        total = compute_total_score(dims, weights)
        assert total == 0.0

    def test_custom_total_max(self) -> None:
        dims: list[DimensionScore] = [
            {"name": f"dim_{i}", "score": 5.0} for i in range(10)
        ]
        weights = [1.0] * 10
        total = compute_total_score(dims, weights, total_max=100)
        assert total == 100.0

    def test_with_weights(self) -> None:
        dims: list[DimensionScore] = [
            {"name": f"dim_{i}", "score": 5.0} for i in range(3)
        ]
        weights = [2.0, 1.0, 0.5]
        total = compute_total_score(dims, weights)
        # raw_total = 5*2 + 5*1 + 5*0.5 = 17.5
        # weight_sum = 3.5
        # normalized = 17.5 / (5*3.5) * 50 = 17.5/17.5*50 = 50
        assert total == 50.0


# ══════════════════════════════════════════════════════════
# 边界条件测试
# ══════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界条件测试。"""

    def test_extreme_negative_ic(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_edge",
            ic=-0.5,
            sharpe=-2.0,
            decay_rate=0.6,
            turnover=0.01,
            correlation_max=0.9,
            logic_score=0,
            data_frequency="daily",
            cross_symbol_coverage=0.2,
            capacity_estimate=1_000,
        )
        # 所有维度都是最差，总分应该很低
        assert score["total_score"] < 10
        assert score["grade"] == "C"

    def test_very_large_values(self) -> None:
        card = FactorQualityCard()
        score = card.evaluate(
            factor_id="fct_large",
            ic=1.0,
            sharpe=100.0,
            decay_rate=-0.5,
            turnover=100.0,
            correlation_max=-0.5,
            logic_score=100,
            cross_symbol_coverage=2.0,
            capacity_estimate=1_000_000_000,
        )
        # 分数应该被限制在合理范围内
        assert 0 <= score["total_score"] <= 50

    def test_immutable_config(self) -> None:
        """确保 config 不被修改。"""
        config: FactorQualityCardConfig = {"total_max": 30}
        card = FactorQualityCard(config)
        card.evaluate(factor_id="fct_imm", ic=0.05, sharpe=2.0)
        assert config["total_max"] == 30

    def test_default_arguments(self) -> None:
        """测试默认参数行为。"""
        card = FactorQualityCard()
        # 只提供必要参数，其余用默认值
        score = card.evaluate(
            factor_id="fct_default",
            ic=0.05,
            sharpe=2.0,
        )
        assert score["factor_id"] == "fct_default"
        assert len(score["dimension_scores"]) == 10