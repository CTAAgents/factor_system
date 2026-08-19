"""plans/54 P2（头寸公式/对称化/Alpha 缓冲）+ P3-1（假设书论证一致性）单元测试。

覆盖：
  - compute_position_target：信号/置信度/波动率/预算四变量影响 + clip + 波动率缺失保守
  - shrink_factor_diversity：低置信砍半、高置信不干预、空权重
  - alpha_buffer_scale：三档映射（高/中/低置信）
  - L1Verifier require_argument_consistency 开启 → 高分低论证被拦；关闭 → 不拦
"""

from __future__ import annotations

import pytest

from fts.factor_engine.capital_allocator import compute_position_target, shrink_factor_diversity
from fts.factor_engine.regime_beta_layer import alpha_buffer_scale


class TestPositionTarget:
    """头寸公式 f(信号, 置信度, 波动率, 风险预算)（plans/54 P2-3）。"""

    def test_strength_scales_position(self) -> None:
        """信号强度线性放大仓位。"""
        base = dict(confidence=1.0, realized_vol=0.20, risk_budget=0.10)
        p1 = compute_position_target(0.5, **base)  # 0.5×1×0.5 = 0.25
        p2 = compute_position_target(1.0, **base)  # 1.0×1×0.5 = 0.5
        assert p1 == pytest.approx(0.25)
        assert p2 == pytest.approx(0.5)

    def test_confidence_scales_position(self) -> None:
        """置信度调制仓位。"""
        base = dict(signal_strength=1.0, realized_vol=0.20, risk_budget=0.10)
        assert compute_position_target(confidence=0.5, **base) == pytest.approx(0.25)
        assert compute_position_target(confidence=1.0, **base) == pytest.approx(0.5)

    def test_vol_inverse_scales_position(self) -> None:
        """波动率越高仓位越小（vol targeting）。"""
        base = dict(signal_strength=1.0, confidence=1.0, risk_budget=0.10)
        p_low = compute_position_target(realized_vol=0.10, **base)  # 1.0
        p_high = compute_position_target(realized_vol=0.40, **base)  # 0.25
        assert p_low == pytest.approx(1.0)
        assert p_high == pytest.approx(0.25)

    def test_clip_bounds(self) -> None:
        """clip 到 [min_position, max_position]。"""
        assert compute_position_target(5.0, 1.0, 0.1, 0.1, max_position=0.8) == pytest.approx(0.8)
        assert compute_position_target(0.0, 1.0, 0.1, 0.1) == pytest.approx(0.0)

    def test_bad_vol_conservative(self) -> None:
        """波动率缺失/非正 → 不放大（保守）。"""
        assert compute_position_target(1.0, 1.0, 0.0, 0.1) == pytest.approx(1.0)
        assert compute_position_target(1.0, 1.0, float("nan"), 0.1) == pytest.approx(1.0)

    def test_negative_signal_abs(self) -> None:
        """负信号取绝对值。"""
        base = dict(confidence=1.0, realized_vol=0.20, risk_budget=0.10)
        assert compute_position_target(-0.5, **base) == pytest.approx(0.25)


class TestShrinkDiversity:
    """对称化仓位（plans/54 P2-1，文档 §7.1 低置信缩小策略种类）。"""

    def test_low_conf_shrinks(self) -> None:
        """低置信度 → 保留 top 50% 因子（砍半）。"""
        weights = {"a": 0.5, "b": 0.3, "c": 0.15, "d": 0.05}
        out = shrink_factor_diversity(weights, confidence=0.3, keep_ratio=0.5)
        assert len(out) == 2
        assert "a" in out and "b" in out
        # 保留权重和 = 原权重和（重归一化）
        assert sum(abs(v) for v in out.values()) == pytest.approx(sum(abs(v) for v in weights.values()))

    def test_high_conf_no_change(self) -> None:
        """高置信度 → 不干预（原样）。"""
        weights = {"a": 0.5, "b": 0.5}
        out = shrink_factor_diversity(weights, confidence=0.8)
        assert out == weights

    def test_empty_weights(self) -> None:
        """空权重 → 原样。"""
        assert shrink_factor_diversity({}, 0.1) == {}


class TestAlphaBuffer:
    """Alpha 缓冲（plans/54 P2-2，文档 §7.3 动态 Beta-Alpha 分配）。"""

    def test_high_conf_full_beta(self) -> None:
        assert alpha_buffer_scale(0.8) == 1.0

    def test_mid_conf_half_beta(self) -> None:
        assert alpha_buffer_scale(0.5) == 0.5

    def test_low_conf_alpha_mode(self) -> None:
        assert alpha_buffer_scale(0.2) == 0.0

    def test_boundaries(self) -> None:
        """阈值边界归高侧。"""
        assert alpha_buffer_scale(0.6) == 1.0
        assert alpha_buffer_scale(0.3) == 0.5


class TestArgumentConsistencyGate:
    """P3-1 策略假设书论证-评分一致性（GAP-123 P2④，开启后防高分低论证）。"""

    @staticmethod
    def _candidate(economic: dict) -> dict:
        return {
            "factor_id": "fct_00000001",
            "economic_logic": economic,
            "is_executable": True,
        }

    @classmethod
    def _run(cls, economic: dict, enabled: bool) -> list[str]:
        from fts.factor_engine.meta_loop import L1Verifier

        from fts.factor_engine.contracts import DEFAULT_L1_VERIFIER_CONFIG

        cfg = dict(DEFAULT_L1_VERIFIER_CONFIG)
        cfg["require_argument_consistency"] = enabled
        # require_executable=False 隔离执行性维度（测试仅聚焦论证一致性）
        cfg["require_executable"] = False
        verifier = L1Verifier(config=cfg)
        # seed_pool 未使用于论证一致性分支，mock 空对象
        result = verifier.check(cls._candidate(economic), None)
        return result.get("failure_reasons", [])

    def test_enabled_blocks_high_score_low_argument(self) -> None:
        """开启时：theory 评分 3 但 narrative 无机制关键词 → 被拦。"""
        economic = {"theory": 3, "behavioral": 0, "microstructure": 0, "institutional": 0,
                    "narrative": "该因子在历史数据上表现稳定，建议纳入。"}  # 无"定价/溢价/均衡"等机制词
        reasons = self._run(economic, enabled=True)
        assert any("缺乏该维度机制论证" in r for r in reasons)

    def test_enabled_passes_with_argument(self) -> None:
        """开启时：narrative 含机制关键词 → 不拦该维度。"""
        economic = {"theory": 3, "behavioral": 0, "microstructure": 0, "institutional": 0,
                    "narrative": "基于流动性风险溢价的定价补偿，均衡下溢价随流动性恶化上升。"}
        reasons = self._run(economic, enabled=True)
        assert not any("缺乏该维度机制论证" in r for r in reasons)

    def test_disabled_no_consistency_gate(self) -> None:
        """关闭时（现状默认）：高分低论证不被拦。"""
        economic = {"theory": 3, "behavioral": 0, "microstructure": 0, "institutional": 0,
                    "narrative": "该因子在历史数据上表现稳定。"}
        reasons = self._run(economic, enabled=False)
        assert not any("缺乏该维度机制论证" in r for r in reasons)
