"""tests/test_factor_quality_card_config.py — 因子质量评分卡配置测试。

覆盖:
    1. 权重/阈值/映射各数据类默认契约
    2. FactorQualityCardFullConfig 序列化（to_dict / to_factor_quality_card_config）
    3. 全局单例 get_quality_card_config
    4. create_config 参数覆盖（含点号分隔 kwargs）
    5. 四种预设配置（futures/conservative/aggressive/permissive）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[1]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.config.factor_quality_card_config import (  # noqa: E402
    CalmarMappingConfig,
    CapacityMappingConfig,
    CorrelationMappingConfig,
    CoverageMappingConfig,
    DecayMappingConfig,
    DefaultValuesConfig,
    DimensionWeights,
    FactorQualityCardFullConfig,
    GradeThresholds,
    ICMappingConfig,
    ICIRMappingConfig,
    SharpeMappingConfig,
    TurnoverMappingConfig,
    create_config,
    get_aggressive_config,
    get_conservative_config,
    get_futures_config,
    get_permissive_config,
    get_quality_card_config,
)


# ─── 数据类默认契约 ────────────────────────────────────────


class TestDimensionWeights:
    def test_defaults_and_tuple_order(self):
        w = DimensionWeights()
        t = w.to_tuple()
        assert len(t) == 10
        assert t[0] == 1.0  # ic_score
        assert t[1] == 1.0  # sharpe_score
        assert t[2] == 0.8  # stability_score
        assert t[-1] == 0.4  # compatibility_score

    def test_sum(self):
        w = DimensionWeights()
        assert w.sum() == pytest.approx(sum(w.to_tuple()))

    def test_custom_values(self):
        w = DimensionWeights(ic_score=2.0, logic_score=1.0)
        assert w.ic_score == 2.0
        assert w.logic_score == 1.0
        assert w.sharpe_score == 1.0  # 未覆盖的保持默认


class TestGradeThresholds:
    def test_defaults(self):
        g = GradeThresholds()
        assert g.total_max == 50
        assert g.max_per_dimension == 5
        assert g.grade_A_threshold == 40.0
        assert g.grade_B_min == 30.0
        assert g.min_grade == "B"


class TestMappingConfigs:
    def test_ic_mapping(self):
        c = ICMappingConfig()
        assert (c.ic_high, c.ic_mid, c.ic_low) == (0.08, 0.03, 0.01)

    def test_icir_mapping(self):
        c = ICIRMappingConfig()
        assert (c.icir_high, c.icir_mid, c.icir_low) == (3.0, 2.0, 1.0)

    def test_sharpe_mapping(self):
        c = SharpeMappingConfig()
        assert (c.sharpe_high, c.sharpe_mid, c.sharpe_low) == (3.0, 1.5, 0.5)

    def test_calmar_mapping(self):
        c = CalmarMappingConfig()
        assert (c.calmar_high, c.calmar_mid, c.calmar_low) == (2.0, 1.0, 0.5)

    def test_decay_mapping(self):
        c = DecayMappingConfig()
        assert (c.decay_good, c.decay_mid, c.decay_bad) == (0.1, 0.3, 0.5)

    def test_capacity_mapping(self):
        c = CapacityMappingConfig()
        assert c.capacity_high == 100_000_000
        assert c.capacity_mid == 10_000_000
        assert c.capacity_low == 1_000_000

    def test_turnover_mapping(self):
        c = TurnoverMappingConfig()
        assert c.turnover_opt_low == 50.0
        assert c.turnover_opt_high == 500.0
        assert c.turnover_unit == "percent"

    def test_correlation_mapping(self):
        c = CorrelationMappingConfig()
        assert (c.corr_low, c.corr_mid, c.corr_high) == (0.3, 0.5, 0.7)

    def test_coverage_mapping(self):
        c = CoverageMappingConfig()
        assert (c.coverage_high, c.coverage_mid, c.coverage_low) == (0.9, 0.7, 0.5)


class TestDefaultValuesConfig:
    def test_defaults(self):
        d = DefaultValuesConfig()
        assert d.decay_rate == 0.2
        assert d.turnover == 0.3
        assert d.correlation_max == 0.5
        assert d.capacity_estimate == 10_000_000
        assert d.logic_score == 3
        assert d.data_frequency == "daily"
        assert d.icir == 0.0
        assert d.calmar == 0.0


# ─── 主配置类 ──────────────────────────────────────────────


class TestFactorQualityCardFullConfig:
    def test_default_construction(self):
        cfg = FactorQualityCardFullConfig()
        assert isinstance(cfg.weights, DimensionWeights)
        assert isinstance(cfg.grades, GradeThresholds)
        assert isinstance(cfg.ic_mapping, ICMappingConfig)
        assert isinstance(cfg.defaults, DefaultValuesConfig)

    def test_to_dict_all_sections(self):
        cfg = FactorQualityCardFullConfig()
        d = cfg.to_dict()
        assert set(d) == {
            "weights",
            "grades",
            "ic_mapping",
            "icir_mapping",
            "sharpe_mapping",
            "calmar_mapping",
            "decay_mapping",
            "capacity_mapping",
            "turnover_mapping",
            "correlation_mapping",
            "coverage_mapping",
            "defaults",
        }
        assert d["weights"]["ic_score"] == 1.0
        assert d["grades"]["grade_A_threshold"] == 40.0

    def test_to_factor_quality_card_config(self):
        cfg = FactorQualityCardFullConfig()
        d = cfg.to_factor_quality_card_config()
        assert d["max_per_dimension"] == 5
        assert d["total_max"] == 50
        assert d["grade_A_threshold"] == 40.0
        assert d["grade_B_min"] == 30.0
        assert d["decay_discount_rate"] == 0.3  # decay_mapping.decay_mid
        assert d["weights"]["logic_score"] == 0.8
        assert d["ic_mapping"]["ic_high"] == 0.08

    def test_singleton_get_quality_card_config(self):
        assert get_quality_card_config() is get_quality_card_config()


# ─── create_config ─────────────────────────────────────────


class TestCreateConfig:
    def test_default_returns_defaults(self):
        cfg = create_config()
        assert cfg.grades.grade_A_threshold == 40.0
        assert cfg.grades.min_grade == "B"

    def test_grade_overrides(self):
        cfg = create_config(min_grade="A", grade_A_threshold=45.0, grade_B_min=35.0, total_max=60)
        assert cfg.grades.min_grade == "A"
        assert cfg.grades.grade_A_threshold == 45.0
        assert cfg.grades.grade_B_min == 35.0
        assert cfg.grades.total_max == 60

    def test_dotted_kwargs(self):
        cfg = create_config(**{"weights.ic_score": 1.5, "grades.min_grade": "C"})
        assert cfg.weights.ic_score == 1.5
        assert cfg.grades.min_grade == "C"

    def test_dotted_kwargs_unknown_path_ignored(self):
        # 路径不存在 → 静默忽略，不抛异常
        cfg = create_config(**{"nonexistent.deep.field": 1})
        assert cfg is not None

    def test_creates_new_instance(self):
        a = create_config()
        b = create_config()
        assert a is not b


# ─── 预设配置 ──────────────────────────────────────────────


class TestPresetConfigs:
    def test_futures_config(self):
        cfg = get_futures_config()
        assert cfg.grades.grade_A_threshold == 38.0
        assert cfg.grades.grade_B_min == 28.0
        assert cfg.grades.min_grade == "B"
        # IC 阈值下调
        assert cfg.ic_mapping.ic_high == 0.06
        assert cfg.ic_mapping.ic_mid == 0.02
        # Sharpe 阈值下调
        assert cfg.sharpe_mapping.sharpe_high == 2.0
        assert cfg.sharpe_mapping.sharpe_mid == 1.0
        # 容量下调
        assert cfg.capacity_mapping.capacity_high == 50_000_000
        # 默认值调整
        assert cfg.defaults.turnover == 0.5
        assert cfg.defaults.capacity_estimate == 5_000_000

    def test_conservative_config(self):
        cfg = get_conservative_config()
        assert cfg.grades.grade_A_threshold == 42.0
        assert cfg.grades.grade_B_min == 32.0

    def test_aggressive_config(self):
        cfg = get_aggressive_config()
        assert cfg.grades.grade_A_threshold == 38.0
        assert cfg.grades.grade_B_min == 28.0

    def test_permissive_config_allows_c(self):
        cfg = get_permissive_config()
        assert cfg.grades.min_grade == "C"

    def test_presets_are_independent(self):
        assert get_futures_config() is not get_conservative_config()
        # 各预设互不影响
        f = get_futures_config()
        c = get_conservative_config()
        assert f.ic_mapping.ic_high == 0.06
        assert c.ic_mapping.ic_high == 0.08  # 保守配置保持默认
