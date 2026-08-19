"""plans/56 §A 模块 — 拥挤度 6 信号合成器 + 联合门控 + 方向偏置单元测试。

覆盖：
  - 6 信号分别触发（corr/volume_stall/momentum_decay/vol_structure/oi/turnover）
  - 合成 score 加权 + 空数据兜底
  - 方向分解（多头拥挤/空头拥挤/中性，修复 G3 多空方向）
  - 缺失 hold → oi/turnover 降级不崩溃
  - build_joint_gate_scale 联合门控矩阵
  - apply_crowding_direction_bias 多空方向抑制
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.regime_crowding import (
    LONG_CROWDED,
    NEUTRAL,
    SHORT_CROWDED,
    CrowdingSignalConfig,
    apply_crowding_direction_bias,
    build_joint_gate_scale,
    compute_crowding_signals,
)


# ─── 面板辅助 ─────────────────────────────────────────────


def _make_ohlcv(
    close: list[float],
    volume: list[float] | None = None,
    hold: list[float] | None = None,
) -> pd.DataFrame:
    """由 close/volume/hold 构造最小 OHLCV DataFrame。"""
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    c = pd.Series(np.asarray(close, dtype=float), index=idx)
    df = pd.DataFrame(
        {"open": c, "high": c * 1.002, "low": c * 0.998, "close": c},
        index=idx,
    )
    df["volume"] = np.asarray(volume or [1000.0] * n, dtype=float)
    if hold is not None:
        df["hold"] = np.asarray(hold, dtype=float)
    return df


def _flat_close(n: int, level: float = 100.0, jitter: float = 0.0002) -> list[float]:
    rng = np.random.default_rng(7)
    out = [level]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + float(rng.normal(0, jitter))))
    return out


# ─── 6 信号触发 ───────────────────────────────────────────


def test_corr_convergence_triggers() -> None:
    """两品种收益完全同步（相关=1 创新高）→ corr_convergence 触发。"""
    close = _flat_close(120, jitter=0.003)
    panel = {"A": _make_ohlcv(close), "B": _make_ohlcv([c * 0.8 for c in close])}
    res = compute_crowding_signals(panel)
    assert res["signals"]["corr_convergence"] is True
    assert res["signal_values"]["corr_convergence"] > 0.9


def test_volume_stall_triggers() -> None:
    """放量滞涨：volume 尾部暴增 + 价格横盘 → volume_stall 触发。"""
    close = _flat_close(120, jitter=0.0003)  # 近横盘
    volume = [1000.0] * 115 + [10000.0] * 5
    panel = {"A": _make_ohlcv(close, volume=volume)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["volume_stall"] is True


def test_momentum_decay_triggers() -> None:
    """动量衰竭：慢涨→加速→急跌 → 短期动量显著弱于长期。"""
    close: list[float] = [100.0]
    for _ in range(80):
        close.append(close[-1] * 1.005)  # 慢涨
    for _ in range(20):
        close.append(close[-1] * 1.02)  # 加速
    for _ in range(20):
        close.append(close[-1] * 0.98)  # 急跌
    panel = {"A": _make_ohlcv(close)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["momentum_decay"] is True


def test_vol_structure_triggers() -> None:
    """波动突升：前段低波 + 后段 ±5% 高波 → vol_structure 触发。"""
    close: list[float] = [100.0]
    for _ in range(100):
        close.append(close[-1] * 1.0005)
    for k in range(1, 21):
        close.append(close[-1] * (1.05 if k % 2 else 0.95))
    panel = {"A": _make_ohlcv(close)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["vol_structure"] is True


def test_oi_concentration_triggers() -> None:
    """OI 天量：hold 尾部暴增 → oi_concentration 触发。"""
    close = _flat_close(120)
    hold = [5000.0] * 115 + [20000.0] * 5
    panel = {"A": _make_ohlcv(close, volume=[1000.0] * 120, hold=hold)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["oi_concentration"] is True


def test_turnover_overheat_triggers() -> None:
    """换手透支：volume/hold 尾部放大 → turnover_overheat 触发。"""
    close = _flat_close(120)
    hold = [5000.0] * 120
    volume = [1000.0] * 115 + [6000.0] * 5  # 换手率尾部暴增
    panel = {"A": _make_ohlcv(close, volume=volume, hold=hold)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["turnover_overheat"] is True


# ─── 合成与兜底 ───────────────────────────────────────────


def test_composite_score_weighted() -> None:
    """合成 score 加权：同步序列 + 尾部放量 + OI/换手暴增 → corr/volume_stall/oi/turnover 触发。

    可用信号 6 个（momentum/vol 在横盘低波下未触发但值有效），
    score = 触发权重和 / 可用权重和 = (0.20+0.15+0.15+0.15)/1.0 = 0.65。
    """
    close = _flat_close(120)
    panel = {
        "A": _make_ohlcv(close, volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
        "B": _make_ohlcv([c * 0.8 for c in close], volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
    }
    res = compute_crowding_signals(panel)
    assert res["crowding_score"] == pytest.approx(0.65, abs=0.02)
    assert res["n_signals_available"] >= 5


def test_empty_panel_fallback() -> None:
    """空面板 → score=0.0 / neutral / fallback。"""
    res = compute_crowding_signals({})
    assert res["crowding_score"] == 0.0
    assert res["direction"] == NEUTRAL
    assert res["method"] == "fallback"


def test_missing_hold_degrades() -> None:
    """缺失 hold → oi 降级跳过（False/0.0），turnover 用量能比代理，不崩溃。"""
    close = _flat_close(120)
    panel = {"A": _make_ohlcv(close, volume=[1000.0] * 115 + [6000.0] * 5)}
    res = compute_crowding_signals(panel)
    assert res["signals"]["oi_concentration"] is False
    assert res["signal_values"]["oi_concentration"] == 0.0
    assert res["crowding_score"] >= 0.0  # 不崩溃


# ─── 方向分解 ─────────────────────────────────────────────


def test_direction_long_crowded() -> None:
    """上涨市 + 高拥挤 → direction=long（多头拥挤）。"""
    close = [100.0 * (1.002) ** t for t in range(120)]
    panel = {
        "A": _make_ohlcv(close, volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
        "B": _make_ohlcv([c * 0.8 for c in close], volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
    }
    # 门槛覆盖至 0.3（避免与价格结构耦合），验证方向分解语义
    cfg = CrowdingSignalConfig(high_crowding=0.3)
    res = compute_crowding_signals(panel, cfg)
    assert res["crowding_score"] >= 0.3
    assert res["direction"] == LONG_CROWDED


def test_direction_short_crowded() -> None:
    """下跌市 + 高拥挤 → direction=short（空头拥挤/逼空）。"""
    close = [100.0 * (0.998) ** t for t in range(120)]
    panel = {
        "A": _make_ohlcv(close, volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
        "B": _make_ohlcv([c * 0.8 for c in close], volume=[1000.0] * 115 + [10000.0] * 5, hold=[5000.0] * 115 + [20000.0] * 5),
    }
    cfg = CrowdingSignalConfig(high_crowding=0.3)
    res = compute_crowding_signals(panel, cfg)
    assert res["crowding_score"] >= 0.3
    assert res["direction"] == SHORT_CROWDED


def test_direction_neutral_low_score() -> None:
    """低拥挤（无信号触发）→ direction=neutral。

    显式 high_crowding=0.6（校准前默认）验证"低拥挤→中性"原语义；
    默认 0.4 校准值下该面板可能判 direction（见决策门，属预期）。
    """
    close = _flat_close(120)
    panel = {"A": _make_ohlcv(close), "B": _make_ohlcv([c * 0.8 for c in close])}
    cfg = CrowdingSignalConfig(high_crowding=0.6)
    res = compute_crowding_signals(panel, cfg)
    assert res["crowding_score"] < 0.6
    assert res["direction"] == NEUTRAL


# ─── 联合门控 ─────────────────────────────────────────────


def test_joint_gate_matrix() -> None:
    """联合门控矩阵：高置信+高拥挤→0.5；低置信+高拥挤→0.0；其余→1.0。"""
    cfg = CrowdingSignalConfig(high_crowding=0.6, confidence_threshold=0.5)
    assert build_joint_gate_scale(0.8, 0.9, cfg) == pytest.approx(0.5)
    assert build_joint_gate_scale(0.8, 0.3, cfg) == pytest.approx(0.0)
    assert build_joint_gate_scale(0.3, 0.9, cfg) == pytest.approx(1.0)
    assert build_joint_gate_scale(0.3, 0.3, cfg) == pytest.approx(1.0)


# ─── 多空方向抑制 ─────────────────────────────────────────


def test_apply_bias_long_crowded() -> None:
    """多头拥挤：多头信号收缩（×0.70）、空头不动。"""
    cfg = CrowdingSignalConfig(long_crowd_suppress=0.30, short_crowd_suppress=0.30)
    out, bias = apply_crowding_direction_bias({"A": 1.0, "B": -1.0}, LONG_CROWDED, cfg)
    assert out["A"] == pytest.approx(0.70)
    assert out["B"] == pytest.approx(-1.0)
    assert bias["direction"] == "long_crowded"


def test_apply_bias_short_crowded() -> None:
    """空头拥挤（逼空防御）：空头信号收缩（×0.70）、多头不动。"""
    cfg = CrowdingSignalConfig(long_crowd_suppress=0.30, short_crowd_suppress=0.30)
    out, bias = apply_crowding_direction_bias({"A": 1.0, "B": -2.0}, SHORT_CROWDED, cfg)
    assert out["A"] == pytest.approx(1.0)
    assert out["B"] == pytest.approx(-2.0 * 0.70)
    assert bias["direction"] == "short_crowded"


def test_apply_bias_neutral_unchanged() -> None:
    """中性：不干预（×1.0），得分逐位不变。"""
    scores = {"A": 1.5, "B": -0.5}
    out, bias = apply_crowding_direction_bias(scores, NEUTRAL)
    assert out == scores
    assert bias["long_factor"] == 1.0
    assert bias["short_factor"] == 1.0
