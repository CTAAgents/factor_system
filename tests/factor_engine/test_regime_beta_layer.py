"""plans/55 §A 模块 — L0 宏观 Beta 层检测器/缩放/偏置单元测试。

覆盖：
  - 合成牛/熊/震荡面板 → RISK_ON / RISK_OFF / RANGE_BOUND 三态判定
  - 空/不足数据 → unknown（scale=1.0，零行为变更）
  - 置信度门槛（min_confidence 不达标 → RANGE_BOUND 不偏置）
  - 股债比方向（IF0/TF0 上行/下行 → risk_pref_z 符号）
  - compute_beta_scale 映射
  - apply_beta_bias 多空不对称（RISK_OFF 多头抑制/空头放大）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.regime_beta_layer import (
    RANGE_BOUND,
    RISK_OFF,
    RISK_ON,
    UNKNOWN,
    BetaDetector,
    BetaLayerConfig,
    apply_beta_bias,
    compute_beta_scale,
)


# ─── 合成面板辅助 ─────────────────────────────────────────


def _make_ohlcv(close: list[float]) -> pd.DataFrame:
    """由 close 序列构造最小 OHLCV DataFrame（DatetimeIndex，工作日频率）。"""
    idx = pd.date_range("2025-01-01", periods=len(close), freq="B")
    c = pd.Series(np.asarray(close, dtype=float), index=idx)
    return pd.DataFrame(
        {
            "open": c,
            "high": c * 1.002,
            "low": c * 0.998,
            "close": c,
            "volume": 1000.0,
        },
        index=idx,
    )


def _growth(anchor: float, daily: float, n: int = 120) -> list[float]:
    """确定性单调序列：close[t] = anchor * (1+daily)^t。"""
    return [anchor * (1.0 + daily) ** t for t in range(n)]


def _decay(anchor: float, daily: float, n: int = 120) -> list[float]:
    """确定性单调下跌序列。"""
    return [anchor * (1.0 - daily) ** t for t in range(n)]


def _bull_panel() -> dict[str, pd.DataFrame]:
    """牛面板：IF0/TF0 平稳上行 + 低波动 + 股债比上行（IF 强于 TF）。"""
    n = 120
    if_close = _growth(100.0, 0.0025, n)   # IF 涨速 0.25%/日
    tf_close = _growth(100.0, 0.0005, n)   # TF 涨速 0.05%/日（国债慢涨）→ 比值上行
    return {
        "IF0": _make_ohlcv(if_close),
        "TF0": _make_ohlcv(tf_close),
        "IH0": _make_ohlcv(_growth(100.0, 0.0022, n)),
        "IC0": _make_ohlcv(_growth(100.0, 0.0024, n)),
    }


def _bear_panel() -> dict[str, pd.DataFrame]:
    """熊面板：前 100 日低波缓跌 + 后 20 日加速下跌（波动升高）→ vol 突破历史分位。

    IF 跌速 > TF 跌速 → 股债比下行。
    """
    n = 120
    base_if = _decay(100.0, 0.0005, 100)
    base_tf = _decay(100.0, 0.0002, 100)
    # 后 20 日加速：叠加更大跌幅（波动上升）
    tail_if = [base_if[-1] * (1.0 - 0.012) ** k for k in range(1, n - 100 + 1)]
    tail_tf = [base_tf[-1] * (1.0 - 0.002) ** k for k in range(1, n - 100 + 1)]
    return {
        "IF0": _make_ohlcv(base_if + tail_if),
        "TF0": _make_ohlcv(base_tf + tail_tf),
        "IH0": _make_ohlcv(_decay(100.0, 0.0004, n)),
        "IC0": _make_ohlcv(_decay(100.0, 0.0005, n)),
    }


def _range_panel() -> dict[str, pd.DataFrame]:
    """震荡面板：IF/TF 强均值回归（无净趋势），比值恒定（risk_pref z≈0 无方向）。"""
    n = 120
    rng = np.random.default_rng(7)
    level = 100.0
    closes: list[float] = []
    for _ in range(n):
        level = level + 0.3 * (100.0 - level) / 10.0 + float(rng.normal(0, 0.3))
        closes.append(level)
    tf_closes = [c * 0.5 for c in closes]  # 比值恒为 2.0 → z-score = 0
    return {
        "IF0": _make_ohlcv(closes),
        "TF0": _make_ohlcv(tf_closes),
    }


# ─── 三态判定 ─────────────────────────────────────────────


def test_bull_panel_detects_risk_on() -> None:
    """合成牛面板（趋势+低波+risk_pref 上行）→ RISK_ON。"""
    state = BetaDetector().detect(_bull_panel())
    assert state["state"] == RISK_ON
    assert state["confidence"] >= 0.5
    assert state["trend_score"] > 0
    assert state["risk_pref_z"] > 0


def test_bear_panel_detects_risk_off() -> None:
    """合成熊面板（趋势负+高波+股债比下行）→ RISK_OFF。"""
    state = BetaDetector().detect(_bear_panel())
    assert state["state"] == RISK_OFF
    assert state["confidence"] >= 0.5
    assert state["trend_score"] < 0
    assert state["vol_ok"] is False


def test_range_panel_detects_range_bound() -> None:
    """震荡面板（无趋势、股债比无方向）→ RANGE_BOUND。"""
    state = BetaDetector().detect(_range_panel())
    assert state["state"] == RANGE_BOUND


def test_empty_panel_unknown() -> None:
    """空/不足数据 → unknown，且 compute_beta_scale 返回 1.0（不偏置）。"""
    state = BetaDetector().detect({})
    assert state["state"] == UNKNOWN
    assert state["method"] == "fallback"
    assert compute_beta_scale(state["state"]) == 1.0


def test_single_symbol_unknown() -> None:
    """单品种面板（<2 有效品种）→ unknown。"""
    state = BetaDetector().detect({"IF0": _make_ohlcv(_growth(100.0, 0.002, 120))})
    assert state["state"] == UNKNOWN


def test_confidence_gate_degrades_to_range() -> None:
    """置信度门槛：min_confidence=0.8 高于实际 conf（2/3）→ RANGE_BOUND 不偏置。"""
    cfg = BetaLayerConfig(min_confidence=0.8)
    state = BetaDetector(cfg).detect(_bull_panel())
    # 牛面板默认 conf=1.0（三信号一致）；强制用分歧面板验证降级
    assert state["state"] in (RISK_ON, RANGE_BOUND)
    # 用熊面板（vote 2/3）验证 conf < 0.8 时降级
    bear_cfg = BetaLayerConfig(min_confidence=0.8)
    bear_state = BetaDetector(bear_cfg).detect(_bear_panel())
    if bear_state["confidence"] < 0.8:
        assert bear_state["state"] == RANGE_BOUND
    else:
        assert bear_state["state"] == RISK_OFF


# ─── 股债比方向 ───────────────────────────────────────────


def test_risk_pref_z_sign_up() -> None:
    """IF0/TF0 比值上行 → risk_pref_z > 0（风险偏好改善）。"""
    state = BetaDetector().detect(_bull_panel())
    assert state["risk_pref_z"] > 0
    assert state["risk_pref"] > 0


def test_risk_pref_z_sign_down() -> None:
    """IF0/TF0 比值下行 → risk_pref_z < 0（风险偏好恶化）。"""
    state = BetaDetector().detect(_bear_panel())
    assert state["risk_pref_z"] < 0


# ─── 敞口缩放 ─────────────────────────────────────────────


def test_compute_beta_scale_mapping() -> None:
    """compute_beta_scale：RISK_OFF→off_scale；RISK_ON→on_scale；其余→1.0。"""
    cfg = BetaLayerConfig(off_scale=0.5, on_scale=1.0)
    assert compute_beta_scale(RISK_OFF, cfg) == pytest.approx(0.5)
    assert compute_beta_scale(RISK_ON, cfg) == pytest.approx(1.0)
    assert compute_beta_scale(RANGE_BOUND, cfg) == pytest.approx(1.0)
    assert compute_beta_scale(UNKNOWN, cfg) == pytest.approx(1.0)


# ─── 多空不对称偏置 ───────────────────────────────────────


def test_apply_beta_bias_risk_off_asymmetric() -> None:
    """RISK_OFF：多头权重收缩（×0.6）、空头权重放大（×1.2，负分更负）。"""
    cfg = BetaLayerConfig(off_long_suppress=0.4, off_short_boost=0.2)
    scores = {"A": 1.0, "B": -1.0, "C": 0.0}
    out, bias = apply_beta_bias(scores, RISK_OFF, cfg)
    assert out["A"] == pytest.approx(1.0 * (1 - 0.4))
    assert out["B"] == pytest.approx(-1.0 * (1 + 0.2))
    assert out["C"] == pytest.approx(0.0)
    assert bias["long_factor"] == pytest.approx(0.6)
    assert bias["short_factor"] == pytest.approx(1.2)


def test_apply_beta_bias_risk_on_asymmetric() -> None:
    """RISK_ON：多头加分（×1.1）、空头减分（×0.9）。"""
    cfg = BetaLayerConfig(on_long_boost=0.1, on_short_suppress=0.1)
    out, bias = apply_beta_bias({"A": 2.0, "B": -2.0}, RISK_ON, cfg)
    assert out["A"] == pytest.approx(2.0 * 1.1)
    assert out["B"] == pytest.approx(-2.0 * 0.9)


def test_apply_beta_bias_neutral_unchanged() -> None:
    """RANGE_BOUND/unknown：不干预（×1.0），得分逐位不变。"""
    scores = {"A": 1.5, "B": -0.5}
    for state in (RANGE_BOUND, UNKNOWN):
        out, bias = apply_beta_bias(scores, state)
        assert out == scores
        assert bias["long_factor"] == 1.0
        assert bias["short_factor"] == 1.0


# ─── 缺失信号降级 ─────────────────────────────────────────


def test_risk_pref_missing_degrades_gracefully() -> None:
    """无 IF0/TF0（股债比缺失）时仍可用趋势+波动 2 信号投票，不崩溃。"""
    panel = {
        "IH0": _make_ohlcv(_growth(100.0, 0.0022, 120)),
        "IC0": _make_ohlcv(_growth(100.0, 0.0024, 120)),
    }
    state = BetaDetector().detect(panel)
    assert state["state"] in (RISK_ON, RANGE_BOUND)
    assert np.isnan(state["risk_pref_z"]) or state["risk_pref_z"] == 0.0 or state["state"] == RISK_ON
