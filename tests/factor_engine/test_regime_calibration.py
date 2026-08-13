"""Regime 置信度熵标定器测试（28 计划 Task 5）+ GAP-094 统计概率校准器测试。

覆盖：
  - 高熵（平坦）分布被折扣、低熵（尖锐）分布保持高置信；
  - 无制度分布时直接透传并 clip；
  - 规则伪概率构造归一化（T1 回归保护）。
  - GAP-094 StatisticalRegimeCalibrator: isotonic/platt/binning 拟合与预测、
    未拟合透传、非法标签拒绝、save/load 往返、Brier 评估。
"""

import json

import numpy as np
import pytest

from fts.factor_engine.regime_calibration import (
    RegimeConfidenceCalibrator,
    StatisticalRegimeCalibrator,
    build_rule_regime_probs,
)


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


# ─── GAP-094: StatisticalRegimeCalibrator ─────────────────


def _synth_samples(n: int = 240, seed: int = 42):
    """合成 (confidence, hit) 样本：真实命中率 p = 0.1 + 0.8×conf（线性可校准）。"""
    rng = np.random.default_rng(seed)
    conf = rng.uniform(0.05, 0.95, size=n)
    p = 0.1 + 0.8 * conf
    hits = (rng.random(n) < p).astype(int)
    return conf.tolist(), hits.tolist()


@pytest.mark.parametrize("method", ["isotonic", "platt", "binning"])
def test_statistical_calibrator_fits_and_predicts(method):
    """三种方法均能拟合：预测单调且向真实命中率靠拢（[0,1]）。"""
    conf, hits = _synth_samples()
    cal = StatisticalRegimeCalibrator(method=method, min_samples=30).fit(conf, hits)
    assert cal.calibrated
    lo, hi = cal.predict(0.2), cal.predict(0.8)
    assert 0.0 <= lo <= hi <= 1.0
    # 0.8 置信度对应的真实命中率 ≈ 0.74，校准输出应高于低置信度
    assert hi > 0.5
    assert lo < 0.5


def test_statistical_calibrator_unfitted_passthrough():
    """未拟合（样本不足）时 predict 透传 clip 后的原始置信度。"""
    cal = StatisticalRegimeCalibrator(min_samples=100)
    cal.fit([0.5, 0.6], [1, 0])  # 仅 2 样本 < min_samples
    assert not cal.calibrated
    assert abs(cal.predict(0.7) - 0.7) < 1e-9
    assert abs(cal.predict(1.5) - 1.0) < 1e-9  # clip 上界
    assert abs(cal.predict(-0.2) - 0.0) < 1e-9  # clip 下界


def test_statistical_calibrator_rejects_invalid_labels():
    """标签非 0/1（如软概率）拒绝拟合，predict 透传。"""
    cal = StatisticalRegimeCalibrator().fit([0.3, 0.5, 0.7], [0.1, 0.6, 0.9])
    assert not cal.calibrated
    assert abs(cal.predict(0.6) - 0.6) < 1e-9


def test_statistical_calibrator_rejects_nan():
    """NaN 置信度拒绝拟合（安全透传）。"""
    cal = StatisticalRegimeCalibrator().fit([0.3, float("nan"), 0.7], [1, 0, 1])
    assert not cal.calibrated


def test_statistical_calibrator_unknown_method_falls_back_binning():
    """未知方法安全降级为 binning（仍可拟合预测）。"""
    cal = StatisticalRegimeCalibrator(method="bogus").fit(*_synth_samples())
    assert cal.method == "binning"
    assert cal.calibrated


def test_statistical_calibrator_save_load_roundtrip(tmp_path):
    """save/load 往返：映射一致，跨进程可复用（load 不依赖 sklearn）。"""
    conf, hits = _synth_samples()
    cal = StatisticalRegimeCalibrator(method="isotonic").fit(conf, hits)
    path = tmp_path / "regime_calibration.json"
    cal.save(path)
    assert path.exists()
    loaded = StatisticalRegimeCalibrator.load(path)
    assert loaded.calibrated
    assert loaded.method == "isotonic"
    for c in (0.1, 0.3, 0.5, 0.7, 0.9):
        assert abs(loaded.predict(c) - cal.predict(c)) < 1e-9
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["gap"] == "GAP-094"
    assert payload["schema_version"] == 1


def test_statistical_calibrator_load_corrupt(tmp_path):
    """缺失/损坏文件 load 返回未拟合实例（predict 透传，不抛异常）。"""
    missing = tmp_path / "nope.json"
    assert not StatisticalRegimeCalibrator.load(missing).calibrated
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert not StatisticalRegimeCalibrator.load(bad).calibrated


def test_statistical_calibrator_brier_score():
    """Brier 得分在 [0, 0.25] 内（优于随机）；未拟合时退化为原始置信度基线。"""
    conf, hits = _synth_samples()
    cal = StatisticalRegimeCalibrator().fit(conf, hits)
    score = cal.brier_score(conf, hits)
    assert 0.0 <= score <= 0.25
    raw = StatisticalRegimeCalibrator()  # 未拟合
    assert raw.calibrated is False
    raw_score = raw.brier_score(conf, hits)
    assert score <= raw_score + 1e-9  # 校准后不劣于原始基线
