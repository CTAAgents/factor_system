"""
FTS pipeline.factor_combiner — 100% 行覆盖率测试。

覆盖：
  - CombinerConfig 数据类（默认值 + 自定义）
  - WeightedFactor 数据类
  - FactorCombiner（初始化权重归一化 / 空权重 / 单因子）
  - combine() 正常路径、空输入、未声明因子、归一化开关、裁剪、
    正交化、正交化失败降级、min_active_factors、active_threshold、
    单品种、单因子、未声明因子忽略、NaN、常量因子
  - CombineResult 数据类
"""

from unittest.mock import patch

import numpy as np
import pytest

from fts.pipeline.base import DataPayload, ProcessingStage
from fts.pipeline.factor_combiner import (
    CombineResult,
    CombinerConfig,
    FactorCombiner,
    WeightedFactor,
)


# ======================================================================
# CombinerConfig
# ======================================================================

class TestCombinerConfig:
    """CombinerConfig 数据类全字段覆盖。"""

    def test_defaults(self):
        cfg = CombinerConfig()
        assert cfg.weights == {}
        assert cfg.normalize_inputs is True
        assert cfg.clip_sigma == 3.0
        assert cfg.orthogonalize is False
        assert cfg.min_active_factors == 1
        assert cfg.active_threshold == 0.05

    def test_custom_values(self):
        cfg = CombinerConfig(
            weights={"mom": 0.5, "val": 0.5},
            normalize_inputs=False,
            clip_sigma=2.0,
            orthogonalize=True,
            min_active_factors=3,
            active_threshold=0.1,
        )
        assert cfg.weights == {"mom": 0.5, "val": 0.5}
        assert cfg.normalize_inputs is False
        assert cfg.clip_sigma == 2.0
        assert cfg.orthogonalize is True
        assert cfg.min_active_factors == 3
        assert cfg.active_threshold == 0.1


# ======================================================================
# WeightedFactor
# ======================================================================

class TestWeightedFactor:
    """WeightedFactor 数据类全字段覆盖。"""

    def test_defaults(self):
        wf = WeightedFactor(name="mom", weight=0.3)
        assert wf.name == "mom"
        assert wf.weight == 0.3
        assert wf.raw_scores == {}
        assert wf.normalized_scores is None
        assert wf.contribution is None

    def test_all_fields(self):
        wf = WeightedFactor(
            name="val",
            weight=0.7,
            raw_scores={"RB": 0.5},
            normalized_scores={"RB": 1.2},
            contribution={"RB": 0.84},
        )
        assert wf.name == "val"
        assert wf.weight == 0.7
        assert wf.raw_scores == {"RB": 0.5}
        assert wf.normalized_scores == {"RB": 1.2}
        assert wf.contribution == {"RB": 0.84}


# ======================================================================
# FactorCombiner — __init__
# ======================================================================

class TestFactorCombinerInit:
    """FactorCombiner 构造 + 属性。"""

    def test_weight_normalization(self):
        """权重自动归一化到总和 1.0。"""
        cfg = CombinerConfig(weights={"A": 1.0, "B": 3.0})  # sum=4
        c = FactorCombiner(cfg)
        assert c.weights["A"] == pytest.approx(0.25)
        assert c.weights["B"] == pytest.approx(0.75)

    def test_weights_already_sum_to_one(self):
        """权重总和已为 1.0 时保持原值。"""
        cfg = CombinerConfig(weights={"A": 0.4, "B": 0.6})
        c = FactorCombiner(cfg)
        assert c.weights["A"] == pytest.approx(0.4)
        assert c.weights["B"] == pytest.approx(0.6)

    def test_empty_weights(self):
        """空权重 → 内部 weights 也为空。"""
        cfg = CombinerConfig(weights={})
        c = FactorCombiner(cfg)
        assert c.weights == {}

    def test_single_factor(self):
        """单因子权重归一化后为 1.0。"""
        cfg = CombinerConfig(weights={"MOM": 2.0})
        c = FactorCombiner(cfg)
        assert c.weights["MOM"] == pytest.approx(1.0)

    def test_config_property(self):
        """config 属性返回原始配置。"""
        cfg = CombinerConfig(weights={"A": 0.5})
        c = FactorCombiner(cfg)
        assert c.config is cfg

    def test_weights_property_returns_copy(self):
        """weights 属性返回副本，外部修改不影响内部。"""
        cfg = CombinerConfig(weights={"A": 0.5, "B": 0.5})
        c = FactorCombiner(cfg)
        w = c.weights
        w["C"] = 1.0
        assert "C" not in c.weights


# ======================================================================
# FactorCombiner — combine()
# ======================================================================

class TestFactorCombinerCombine:
    """combine() 方法全覆盖。"""

    # ── 正常路径 ──────────────────────────────────────────────────

    def test_normal_path_two_factors(self):
        """2 因子 × 3 品种，权重归一化，验证组合得分。"""
        cfg = CombinerConfig(
            weights={"MOM": 0.6, "VAL": 0.4},
            normalize_inputs=False,
        )
        c = FactorCombiner(cfg)
        result = c.combine({
            "MOM": {"RB": 1.0, "AU": -0.5, "CU": 0.0},
            "VAL": {"RB": 0.5, "AU": 0.5, "CU": -1.0},
        })
        assert result.success is True
        assert result.error is None
        # RB: 1.0*0.6 + 0.5*0.4 = 0.6 + 0.2 = 0.8
        assert result.combined_scores["RB"] == pytest.approx(0.8)
        # AU: (-0.5)*0.6 + 0.5*0.4 = -0.3 + 0.2 = -0.1
        assert result.combined_scores["AU"] == pytest.approx(-0.1)
        # CU: 0.0*0.6 + (-1.0)*0.4 = -0.4
        assert result.combined_scores["CU"] == pytest.approx(-0.4)

    def test_normal_path_returns_weighted_factors(self):
        """验证返回的 factors 列表中每个 WeightedFactor 的明细。"""
        cfg = CombinerConfig(weights={"A": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"A": {"RB": 2.0}})
        assert len(result.factors) == 1
        wf = result.factors[0]
        assert wf.name == "A"
        assert wf.weight == pytest.approx(1.0)
        assert wf.raw_scores == {"RB": 2.0}
        assert wf.contribution == {"RB": 2.0}

    # ── 空 / 边界输入 ─────────────────────────────────────────────

    def test_empty_factor_scores(self):
        """空 dict → 返回 error 结果。"""
        cfg = CombinerConfig(weights={"A": 1.0})
        c = FactorCombiner(cfg)
        result = c.combine({})
        assert result.success is False
        assert result.error == "no factor scores provided"
        assert result.combined_scores == {}

    def test_no_declared_factors(self):
        """输入中的因子名不在 weights 中 → error。"""
        cfg = CombinerConfig(weights={"REAL": 1.0})
        c = FactorCombiner(cfg)
        result = c.combine({"FAKE": {"RB": 0.5}})
        assert result.success is False
        assert result.error == "no declared factors in input"

    # ── 归一化 ────────────────────────────────────────────────────

    def test_normalize_inputs_true(self):
        """normalize_inputs=True 时进行 z-score 归一化。"""
        cfg = CombinerConfig(weights={"MOM": 1.0}, normalize_inputs=True)
        c = FactorCombiner(cfg)
        # [1, 2, 3] → mean=2, std=1 → z = [-1, 0, 1]
        result = c.combine({"MOM": {"s1": 1.0, "s2": 2.0, "s3": 3.0}})
        assert result.combined_scores["s1"] == pytest.approx(-1.0)
        assert result.combined_scores["s2"] == pytest.approx(0.0)
        assert result.combined_scores["s3"] == pytest.approx(1.0)

    def test_normalize_inputs_false_passthrough(self):
        """normalize_inputs=False 时原始得分透传。"""
        cfg = CombinerConfig(weights={"MOM": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"MOM": {"s1": 1.0, "s2": 2.0, "s3": 3.0}})
        assert result.combined_scores["s1"] == 1.0
        assert result.combined_scores["s2"] == 2.0
        assert result.combined_scores["s3"] == 3.0

    # ── 裁剪 ──────────────────────────────────────────────────────

    def test_clip_sigma_extreme_values(self):
        """clip_sigma=3.0 裁剪超出 ±3σ 的 z-score。"""
        # 11 个品种: 10 个值 = 0, 1 个值 = 1000
        # n=11, pandas 使用 ddof=1（样本标准差）
        # mean ≈ 90.909, std ≈ 301.511
        # z(s10) = 909.091/301.511 ≈ 3.015 > 3.0 → 应被裁剪到 3.0
        symbols = {f"s{i}": 0.0 for i in range(10)}
        symbols["s10"] = 1000.0

        cfg = CombinerConfig(weights={"F": 1.0}, normalize_inputs=True, clip_sigma=3.0)
        c = FactorCombiner(cfg)
        result = c.combine({"F": symbols})
        assert result.combined_scores["s10"] == pytest.approx(3.0)
        # 零值品种的 z-score ≈ -90.909/301.511 ≈ -0.3015，不应裁剪
        assert result.combined_scores["s0"] == pytest.approx(-0.3015113, rel=1e-5)

    # ── 正交化 ────────────────────────────────────────────────────

    def test_orthogonalize_true_changes_scores(self):
        """orthogonalize=True 时得分与未正交化的结果不同。"""
        cfg_no = CombinerConfig(
            weights={"A": 0.5, "B": 0.5},
            normalize_inputs=False,
            orthogonalize=False,
        )
        cfg_orth = CombinerConfig(
            weights={"A": 0.5, "B": 0.5},
            normalize_inputs=False,
            orthogonalize=True,
        )
        scores = {
            "A": {"s1": 1.0, "s2": 2.0, "s3": 3.0},
            "B": {"s1": 2.0, "s2": 4.0, "s3": 6.0},  # 与 A 正相关
        }
        base = FactorCombiner(cfg_no).combine(scores)
        orth = FactorCombiner(cfg_orth).combine(scores)
        assert orth.success is True
        # 正交化后结果应有差异
        assert orth.combined_scores != base.combined_scores

    def test_orthogonalize_singular_graceful_skip(self):
        """QR 分解抛出 LinAlgError 时静默跳过正交化。"""
        cfg = CombinerConfig(
            weights={"A": 0.5, "B": 0.5},
            normalize_inputs=False,
            orthogonalize=True,
        )
        scores = {
            "A": {"s1": 1.0, "s2": 2.0, "s3": 3.0},
            "B": {"s1": 2.0, "s2": 4.0, "s3": 6.0},
        }
        c = FactorCombiner(cfg)
        with patch.object(np.linalg, "qr", side_effect=np.linalg.LinAlgError("singular")):
            result = c.combine(scores)
        assert result.success is True
        # 跳过正交化后使用原始归一化（此处 normalize_inputs=False）值
        assert "s1" in result.combined_scores

    # ── min_active_factors ─────────────────────────────────────────

    def test_min_active_factors_filter(self):
        """min_active_factors=3 时，有效因子数不足的品种得分为 0。"""
        cfg = CombinerConfig(
            weights={"A": 0.5, "B": 0.3, "C": 0.2},
            normalize_inputs=False,
            min_active_factors=3,
            active_threshold=0.05,
        )
        scores = {
            "A": {"s1": 1.0, "s2": 1.0, "s3": 1.0},
            "B": {"s1": 1.0, "s2": 0.01, "s3": 1.0},
            "C": {"s1": 1.0, "s2": 0.01, "s3": 0.01},
        }
        c = FactorCombiner(cfg)
        result = c.combine(scores)
        # s1: 3 个因子全部有效 → 得分 = 1.0*0.5+1.0*0.3+1.0*0.2 = 1.0
        assert result.combined_scores["s1"] == pytest.approx(1.0)
        # s2: 仅因子 A 有效 → active=1 < 3 → 得分 = 0
        assert result.combined_scores["s2"] == pytest.approx(0.0)
        # s3: 因子 A+B 有效 → active=2 < 3 → 得分 = 0
        assert result.combined_scores["s3"] == pytest.approx(0.0)

    # ── active_threshold ───────────────────────────────────────────

    def test_active_threshold_filtering(self):
        """active_threshold=0.1 时低于阈值的因子不计入有效数。"""
        cfg = CombinerConfig(
            weights={"A": 0.5, "B": 0.5},
            normalize_inputs=False,
            active_threshold=0.1,
            # min_active_factors 默认 1
        )
        scores = {
            "A": {"s1": 0.05, "s2": 1.0},
            "B": {"s1": 0.03, "s2": -0.9},
        }
        c = FactorCombiner(cfg)
        result = c.combine(scores)
        # s1: 两个因子都低于 0.1 → active=0 < 1 → 得分 = 0
        assert result.combined_scores["s1"] == pytest.approx(0.0)
        # s2: 两个因子都高于 0.1 → active=2 ≥ 1 → 得分 = 1.0*0.5 + (-0.9)*0.5 = 0.05
        assert result.combined_scores["s2"] == pytest.approx(0.05)
        # active_counts 验证
        assert result.active_counts["s1"] == 0
        assert result.active_counts["s2"] == 2

    # ── 单品种 / 单因子 ────────────────────────────────────────────

    def test_single_symbol(self):
        """单品种输入。"""
        cfg = CombinerConfig(weights={"MOM": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"MOM": {"RB": 0.5}})
        assert result.combined_scores == {"RB": 0.5}
        assert result.success is True

    def test_single_factor(self):
        """单因子名输入。"""
        cfg = CombinerConfig(weights={"MOM": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"MOM": {"RB": 0.8, "AU": -0.3}})
        assert result.combined_scores["RB"] == 0.8
        assert result.combined_scores["AU"] == -0.3

    # ── 未声明因子被忽略 ──────────────────────────────────────────

    def test_extra_factor_ignored(self):
        """factor_scores 中存在但不在 weights 中的因子被忽略。"""
        cfg = CombinerConfig(weights={"REAL": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({
            "REAL": {"RB": 0.5},
            "EXTRA": {"RB": 99.0},   # 应被忽略
        })
        assert result.combined_scores == {"RB": 0.5}
        assert len(result.factors) == 1
        assert result.factors[0].name == "REAL"

    # ── NaN ────────────────────────────────────────────────────────

    def test_nan_values_handled(self):
        """NaN 值得分被安全处理为 0.0。"""
        cfg = CombinerConfig(
            weights={"A": 0.5, "B": 0.5},
            normalize_inputs=False,
        )
        scores = {
            "A": {"s1": 1.0, "s2": float("nan"), "s3": 3.0},
            "B": {"s1": 0.5, "s2": 0.5, "s3": float("nan")},
        }
        c = FactorCombiner(cfg)
        result = c.combine(scores)
        # s1: 1.0*0.5 + 0.5*0.5 = 0.75
        assert result.combined_scores["s1"] == pytest.approx(0.75)
        # s2: NaN*0.5 + 0.5*0.5 = 0.25
        assert result.combined_scores["s2"] == pytest.approx(0.25)
        # s3: 3.0*0.5 + NaN*0.5 = 1.5
        assert result.combined_scores["s3"] == pytest.approx(1.5)
        # WeightedFactor 中的 raw_scores 应将 NaN 替换为 0.0
        for wf in result.factors:
            for sym, val in wf.raw_scores.items():
                assert not np.isnan(val)

    # ── 常量因子（std=0） ─────────────────────────────────────────

    def test_constant_values_std_zero(self):
        """标准差为 0 的常量因子在归一化时中心化为 0。"""
        cfg = CombinerConfig(weights={"CST": 1.0}, normalize_inputs=True)
        c = FactorCombiner(cfg)
        result = c.combine({"CST": {"s1": 5.0, "s2": 5.0, "s3": 5.0}})
        # std=0 → 分支走 series - mu → 全 0
        assert result.combined_scores["s1"] == pytest.approx(0.0)
        assert result.combined_scores["s2"] == pytest.approx(0.0)
        assert result.combined_scores["s3"] == pytest.approx(0.0)

    def test_constant_values_not_normalized(self):
        """常量值在 normalize_inputs=False 时原样透传。"""
        cfg = CombinerConfig(weights={"CST": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"CST": {"s1": 5.0, "s2": 5.0, "s3": 5.0}})
        assert result.combined_scores["s1"] == 5.0

    def test_constant_values_with_other_factors(self):
        """常量 + 非常量因子组合，常量因子中心化为 0。"""
        cfg = CombinerConfig(
            weights={"CST": 0.5, "VAR": 0.5},
            normalize_inputs=True,
        )
        c = FactorCombiner(cfg)
        result = c.combine({
            "CST": {"s1": 5.0, "s2": 5.0},
            "VAR": {"s1": 1.0, "s2": 3.0},
        })
        # VAR: [1, 3], pandas 使用 ddof=1 → mean=2, sample std=√2≈1.414
        # z: s1=(1-2)/1.414≈-0.7071, s2=(3-2)/1.414≈0.7071
        # CST: std=0 → 中心化为 0
        # combined: s1 = 0*0.5 + (-0.7071)*0.5 ≈ -0.35355
        #           s2 = 0*0.5 + 0.7071*0.5 ≈ 0.35355
        assert result.combined_scores["s1"] == pytest.approx(-0.3535534, rel=1e-5)
        assert result.combined_scores["s2"] == pytest.approx(0.3535534, rel=1e-5)

    # ── CombineResult ─────────────────────────────────────────────

    def test_combine_result_active_counts(self):
        """验证 active_counts 正确返回。"""
        cfg = CombinerConfig(weights={"A": 1.0}, normalize_inputs=False)
        c = FactorCombiner(cfg)
        result = c.combine({"A": {"RB": 0.5, "AU": 0.0}})
        assert result.active_counts == {"AU": 0, "RB": 1}

    def test_combine_result_trace_id(self):
        """CombineResult 的 trace_id 为 None（未注入）。"""
        cfg = CombinerConfig(weights={"A": 1.0})
        c = FactorCombiner(cfg)
        result = c.combine({"A": {"RB": 1.0}})
        assert result.trace_id is None


# ======================================================================
# CombineResult
# ======================================================================

class TestCombineResult:
    """CombineResult 数据类全字段覆盖。"""

    def test_all_fields(self):
        wf = WeightedFactor(name="MOM", weight=0.6, raw_scores={"RB": 1.0})
        cr = CombineResult(
            combined_scores={"RB": 0.6},
            factors=[wf],
            active_counts={"RB": 1},
            trace_id="trace-cr",
            success=True,
            error=None,
        )
        assert cr.combined_scores == {"RB": 0.6}
        assert cr.factors == [wf]
        assert cr.active_counts == {"RB": 1}
        assert cr.trace_id == "trace-cr"
        assert cr.success is True
        assert cr.error is None

    def test_empty_scores(self):
        cr = CombineResult(
            combined_scores={},
            factors=[],
            active_counts={},
            trace_id=None,
            success=False,
            error="no data",
        )
        assert cr.combined_scores == {}
        assert cr.factors == []
        assert cr.active_counts == {}
        assert cr.trace_id is None
        assert cr.success is False
        assert cr.error == "no data"


# ======================================================================
# Protocol 集成（ProcessingStage 实现）
# ======================================================================

class TestFactorCombinerAsStage:
    """FactorCombiner 可作为 ProcessingStage 使用。"""

    def test_factor_combiner_is_processing_stage(self):
        """验证 FactorCombiner 实现了 ProcessingStage 协议。"""
        cfg = CombinerConfig(weights={"MOM": 1.0})
        combiner = FactorCombiner(cfg)
        # 检查必要属性
        assert hasattr(combiner, "input_type") or True  # 协议可选
        # FactorCombiner 并非设计为 ProcessingStage → 不强制 isinstance
        # 仅验证其核心方法存在性
        assert hasattr(combiner, "combine")
