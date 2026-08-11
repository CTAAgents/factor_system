"""Regime 置信度熵标定器测试（28 计划 Task 5）。

覆盖：
  - 高熵（平坦）分布被折扣、低熵（尖锐）分布保持高置信；
  - 无制度分布时直接透传并 clip；
  - 规则伪概率构造归一化（T1 回归保护）。
"""

from fts.factor_engine.regime_calibration import RegimeConfidenceCalibrator, build_rule_regime_probs


def test_calibrator_penalizes_entropy():
    cal = RegimeConfidenceCalibrator(entropy_penalty=0.5, scale_min=0.3)
    sharp = {"bull": 0.95, "bear": 0.01, "oscillate": 0.02, "high_vol": 0.01, "low_vol": 0.01}
    flat = {"bull": 0.2, "bear": 0.2, "oscillate": 0.2, "high_vol": 0.2, "low_vol": 0.2}
    s = cal.calibrate(0.9, sharp)
    f = cal.calibrate(0.9, flat)
    assert s > f  # 尖锐分布保持高置信，平坦分布被折扣
    assert 0.3 <= s <= 1.0 and 0.3 <= f <= 1.0


def test_calibrator_no_probs_passthrough():
    cal = RegimeConfidenceCalibrator()
    assert abs(cal.calibrate(0.8, None) - 0.8) < 1e-9


def test_rule_regime_probs_normalized():
    probs = build_rule_regime_probs(trend_score=0.5, vol_score=0.2)
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert probs["bull"] > probs["bear"]
