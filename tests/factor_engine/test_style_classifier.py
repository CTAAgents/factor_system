"""
tests/factor_engine/test_style_classifier.py — FactorStyle 分类器测试（A.3 / v2.56.0）。

覆盖:
    - 显式 style_tags 优先
    - 名称关键词推断（momentum/mean_reversion/carry/value/low_vol/high_beta/defensive/growth/quality/sentiment/volatility/open_interest/cross_section/intraday）
    - 代码关键词推断（open_interest/cross_section/intraday）
    - 签名输入字段推断
    - 缺省 other
    - classify_primary 主风格
    - FactorStyle 枚举契约与 REGIME_STYLE_MULTIPLIERS 键一致性
"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import FactorStyle
from fts.factor_engine.portfolio_loop import (
    REGIME_STYLE_MULTIPLIERS,
    _infer_factor_style_from_name,
    regime_adaptive_weight_adjustment,
)
from fts.factor_engine.style_classifier import (
    FactorStyleClassifier,
    classify_style_tags,
)


def _factor(name: str = "", code: str = "", **extra) -> dict:
    f = {"name": name, "code": code or "def output(data, params):\n    return data['close']"}
    f.update(extra)
    return f


# ─── 名称关键词推断 ────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("fut_momentum_g1", "momentum"),
    ("mean_reversion_bias", "mean_reversion"),
    ("fut_carry_roll", "carry"),
    ("pe_value_factor", "value"),
    ("low_vol_strategy", "low_vol"),
    ("high_beta_exposure", "high_beta"),
    ("defensive_quality", "defensive"),
    ("earnings_growth", "growth"),
    ("roe_quality", "quality"),
    ("analyst_sentiment", "sentiment"),
    ("fut_volatility_atr", "volatility"),
    ("open_interest_change", "open_interest"),
    ("cs_rank_cross", "cross_section"),
    ("intraday_momentum", "intraday"),
    ("unknown_factor_xyz", "other"),
])
def test_infer_style_from_name(name: str, expected: str) -> None:
    assert _infer_factor_style_from_name(name) == expected


# ─── FactorStyleClassifier 全流程 ─────────────────────────

def test_classifier_explicit_style_tags_priority() -> None:
    """显式 style_tags 优先于名称推断。"""
    clf = FactorStyleClassifier()
    f = _factor(name="momentum_flow", style_tags=["value", "quality"])
    assert clf.classify(f) == ["value", "quality"]


def test_classifier_explicit_style_tags_filter_invalid() -> None:
    """无效 style_tags 被过滤，回退名称推断。"""
    clf = FactorStyleClassifier()
    f = _factor(name="fut_carry", style_tags=["not_a_style", 123])
    assert clf.classify(f) == ["carry"]


def test_classifier_name_keyword() -> None:
    clf = FactorStyleClassifier()
    assert clf.classify(_factor(name="breakout_follow")) == ["momentum"]
    assert clf.classify(_factor(name="reversal_bounce")) == ["mean_reversion"]


def test_classifier_code_keyword() -> None:
    """代码关键词：名称无命中时用 code。"""
    clf = FactorStyleClassifier()
    f = _factor(name="fut_signal_alpha", code="oi_change = data['open_interest']")
    assert clf.classify(f) == ["open_interest"]


def test_classifier_signature_field() -> None:
    """签名输入字段兜底。"""
    clf = FactorStyleClassifier()
    f = _factor(
        name="fut_composite",
        signature={"input_fields": ["close", "open_interest", "volume"]},
    )
    assert clf.classify(f) == ["open_interest"]


def test_classifier_default_other() -> None:
    clf = FactorStyleClassifier()
    assert clf.classify(_factor(name="zzz")) == ["other"]
    assert clf.classify_primary(_factor(name="zzz")) == "other"


def test_classify_style_tags_helper() -> None:
    assert classify_style_tags(_factor(name="fut_carry")) == ["carry"]


def test_classifier_empty_factor() -> None:
    clf = FactorStyleClassifier()
    assert clf.classify({}) == ["other"]


# ─── FactorStyle 契约 ─────────────────────────────────────

def test_factor_style_enum_contains_expected() -> None:
    """FactorStyle 枚举包含 A.3 设计声明的核心风格。"""
    styles = set(FactorStyle.__args__)  # type: ignore[attr-defined]
    for expected in ("momentum", "mean_reversion", "carry", "value",
                     "low_vol", "high_beta", "defensive", "growth",
                     "quality", "sentiment", "volatility", "open_interest",
                     "cross_section", "intraday", "other"):
        assert expected in styles


def test_regime_style_multipliers_cover_all_regimes() -> None:
    """REGIME_STYLE_MULTIPLIERS 覆盖全部 5 种制度。"""
    for r in ("bull", "bear", "oscillate", "high_vol", "low_vol"):
        assert r in REGIME_STYLE_MULTIPLIERS
        assert isinstance(REGIME_STYLE_MULTIPLIERS[r], dict)


def test_regime_style_multipliers_values_reasonable() -> None:
    """倍率值在合理范围 [0.3, 1.5]。"""
    for regime, styles in REGIME_STYLE_MULTIPLIERS.items():
        for style, mult in styles.items():
            assert 0.3 <= mult <= 1.5, f"{regime}/{style}={mult}"


# ─── regime_adaptive_weight_adjustment 双维度 ──────────────

def _make_signal(fid: str, name: str, weight: float = 0.1) -> dict:
    return {"factor_id": fid, "name": name, "weight": weight,
            "sharpe": 1.8, "ic": 0.05, "turnover": 0.3, "decay_6m": 0.05,
            "retained": True}


def test_adjustment_family_dimension_backward_compat() -> None:
    """dimension='family' 保持 v2.55.0 行为（趋势因子 bull 下 ×1.3）。"""
    signals = [_make_signal("f1", "fut_trend_alpha")]
    factors = [{"factor_id": "f1", "family": "trend", "name": "fut_trend_alpha"}]
    out = regime_adaptive_weight_adjustment(
        signals, {"regime": "bull"}, factors, dimension="family",
    )
    assert abs(out[0]["weight"] - 0.13) < 1e-6


def test_adjustment_style_dimension() -> None:
    """dimension='style' 按 style 倍率（carry 在 bull ×1.1）。"""
    signals = [_make_signal("f1", "fut_carry")]
    factors = [{"factor_id": "f1", "name": "fut_carry"}]
    out = regime_adaptive_weight_adjustment(
        signals, {"regime": "bull"}, factors, dimension="style",
    )
    assert abs(out[0]["weight"] - 0.11) < 1e-6


def test_adjustment_both_dimension_clamped() -> None:
    """dimension='both' family×style 乘积并 clamp 到 [0.5, 1.5]。"""
    # trend(1.3) × momentum(1.3) = 1.69 → clamp 到 1.5
    signals = [_make_signal("f1", "fut_trend_momentum", weight=0.1)]
    factors = [{"factor_id": "f1", "family": "trend", "name": "fut_trend_momentum"}]
    out = regime_adaptive_weight_adjustment(
        signals, {"regime": "bull"}, factors, dimension="both",
    )
    assert abs(out[0]["weight"] - 0.15) < 1e-6


def test_adjustment_style_tags_precedence() -> None:
    """显式 style_tags 优先于名称推断。"""
    signals = [_make_signal("f1", "fut_trend_momentum")]
    factors = [{"factor_id": "f1", "name": "fut_trend_momentum",
                "style_tags": ["value"]}]
    out = regime_adaptive_weight_adjustment(
        signals, {"regime": "bear"}, factors, dimension="style",
    )
    # value 在 bear ×1.2
    assert abs(out[0]["weight"] - 0.12) < 1e-6


def test_adjustment_high_vol_decay_penalty() -> None:
    """high_vol 下衰减因子额外 ×0.8。"""
    signals = [_make_signal("f1", "fut_trend")]
    signals[0]["decay_6m"] = 0.30
    factors = [{"factor_id": "f1", "family": "trend", "name": "fut_trend"}]
    out = regime_adaptive_weight_adjustment(
        signals, {"regime": "high_vol"}, factors, dimension="family",
    )
    # trend high_vol ×0.7，再衰减 ×0.8 → 0.056
    assert abs(out[0]["weight"] - 0.1 * 0.7 * 0.8) < 1e-6


def test_adjustment_empty_inputs() -> None:
    assert regime_adaptive_weight_adjustment([], {"regime": "bull"}, []) == []
    sig = [_make_signal("f1", "fut_trend")]
    assert regime_adaptive_weight_adjustment(sig, {}, []) == sig
