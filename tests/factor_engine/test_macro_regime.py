"""Bridgewater 增长×通胀四象限宏观制度层测试（GAP-092，plans/28 §6 远期落地）。

覆盖:
  - 四象限判定：增长/通胀高低的 4 种组合 → overheat/goldilocks/stagflation/recession
  - 边界：得分恰为 0 归"高"侧（>=0 语义）
  - 置信度与联合软概率：和=1、主象限概率=置信度、单调性
  - 空数据/全 NaN → None（无法判定）
  - 时序取最新有效值（前段 NaN 后段有效）
  - 默认配置与自定义配置（阈值/带宽可调）
  - 象限画像完整性
"""

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.macro_regime import (
    DEFAULT_MACRO_REGIME_CONFIG,
    QUADRANT_PROFILES,
    MacroQuadrant,
    MacroRegimeConfig,
    MacroRegimeDetector,
)


def _s(values: list[float]) -> pd.Series:
    """构造月度时序（索引为月份）。"""
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values), freq="MS"))


# ─── 四象限判定 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("growth", "inflation", "expected"),
    [
        (55.0, 4.0, MacroQuadrant.OVERHEAT),  # 增长↑ 通胀↑ → 过热
        (55.0, 1.0, MacroQuadrant.GOLDILOCKS),  # 增长↑ 通胀↓ → 金发女孩
        (45.0, 4.0, MacroQuadrant.STAGFLATION),  # 增长↓ 通胀↑ → 滞胀
        (45.0, 1.0, MacroQuadrant.RECESSION),  # 增长↓ 通胀↓ → 衰退
    ],
)
def test_quadrant_detection(growth: float, inflation: float, expected: MacroQuadrant) -> None:
    det = MacroRegimeDetector()
    result = det.detect(_s([growth]), _s([inflation]))
    assert result is not None
    assert result["quadrant"] == expected.value
    assert result["quadrant_probs"][expected.value] == pytest.approx(result["confidence"])


def test_boundary_zero_score_counts_as_high() -> None:
    """得分恰为 0（PMI=50、CPI=目标）归'高'侧（>=0 语义，不落入全低象限）。"""
    det = MacroRegimeDetector()
    result = det.detect(_s([50.0]), _s([2.0]))  # growth_score=0, inflation_score=0
    assert result is not None
    assert result["quadrant"] == MacroQuadrant.OVERHEAT.value
    assert result["growth_score"] == pytest.approx(0.0)
    assert result["inflation_score"] == pytest.approx(0.0)


# ─── 置信度与联合软概率 ───────────────────────────────────


def test_quadrant_probs_sum_to_one() -> None:
    det = MacroRegimeDetector()
    for growth, inflation in [(55, 4), (55, 1), (45, 4), (45, 1), (50, 2), (48, 3)]:
        result = det.detect(_s([float(growth)]), _s([float(inflation)]))
        assert result is not None
        probs = result["quadrant_probs"]
        assert sum(probs.values()) == pytest.approx(1.0)
        assert set(probs) == {q.value for q in MacroQuadrant}
        assert all(0.0 <= v <= 1.0 for v in probs.values())


def test_confidence_increases_with_signal_strength() -> None:
    """信号越强（远离阈值）置信度越高。"""
    det = MacroRegimeDetector()
    weak = det.detect(_s([51.0]), _s([2.5]))
    strong = det.detect(_s([60.0]), _s([5.0]))
    assert weak is not None and strong is not None
    assert strong["confidence"] > weak["confidence"]


def test_joint_prob_semantics() -> None:
    """联合概率语义：增长↑概率 = P(overheat) + P(goldilocks)，通胀↑ = P(overheat)+P(stagflation)。"""
    det = MacroRegimeDetector()
    result = det.detect(_s([58.0]), _s([4.0]))
    assert result is not None
    p_g = result["quadrant_probs"]["overheat"] + result["quadrant_probs"]["goldilocks"]
    p_i = result["quadrant_probs"]["overheat"] + result["quadrant_probs"]["stagflation"]
    # growth_score=(58-50)/5=1.6→clip 1.0 → p_g=1.0；inflation_score=(4-2)/2=1.0 → p_i=1.0
    assert p_g == pytest.approx(1.0)
    assert p_i == pytest.approx(1.0)


def test_scores_clipped_to_unit_range() -> None:
    """得分 clip 到 [-1, 1]。"""
    det = MacroRegimeDetector()
    result = det.detect(_s([80.0]), _s([-10.0]))  # 极端值
    assert result is not None
    assert result["growth_score"] == 1.0
    assert result["inflation_score"] == -1.0


# ─── 数据健壮性 ───────────────────────────────────────────


def test_empty_series_returns_none() -> None:
    det = MacroRegimeDetector()
    assert det.detect(pd.Series(dtype=float), pd.Series(dtype=float)) is None
    assert det.detect(pd.Series(dtype=float), _s([1.0])) is None


def test_all_nan_returns_none() -> None:
    det = MacroRegimeDetector()
    assert det.detect(_s([np.nan, np.nan]), _s([np.nan, np.nan])) is None


def test_uses_latest_valid_value() -> None:
    """前段 NaN 后段有效时取最新有效值（dropna 后尾部）。"""
    det = MacroRegimeDetector()
    growth = pd.Series([np.nan, np.nan, 46.0], index=pd.date_range("2026-01-01", periods=3, freq="MS"))
    inflation = pd.Series([np.nan, 3.5, np.nan], index=pd.date_range("2026-01-01", periods=3, freq="MS"))
    result = det.detect(growth, inflation)
    assert result is not None
    assert result["growth_value"] == pytest.approx(46.0)
    assert result["inflation_value"] == pytest.approx(3.5)
    assert result["quadrant"] == MacroQuadrant.STAGFLATION.value


# ─── 配置 ─────────────────────────────────────────────────


def test_default_config() -> None:
    cfg = DEFAULT_MACRO_REGIME_CONFIG
    assert cfg["growth_threshold"] == 50.0
    assert cfg["inflation_target"] == 2.0
    assert cfg["inflation_band"] == 2.0


def test_custom_config_shift_thresholds() -> None:
    """自定义阈值改变判定：增长目标 45 + 通胀目标 3.0 时 (46, 3.5) 判为增长↑通胀↑。"""
    cfg: MacroRegimeConfig = MacroRegimeConfig(
        growth_threshold=45.0,
        inflation_target=3.0,
        inflation_band=1.0,
        growth_scale=5.0,
        inflation_scale=2.0,
    )
    det = MacroRegimeDetector(cfg)
    result = det.detect(_s([46.0]), _s([3.5]))
    assert result is not None
    assert result["quadrant"] == MacroQuadrant.OVERHEAT.value


# ─── 象限画像 ─────────────────────────────────────────────


def test_quadrant_profiles_complete() -> None:
    """四个象限均有画像（标签/描述/偏好板块）。"""
    for q in MacroQuadrant:
        profile = QUADRANT_PROFILES[q.value]
        assert profile["label"]
        assert profile["description"]
        assert profile["favored"]
