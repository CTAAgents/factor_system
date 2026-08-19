"""plans/54 P0-3 — high_vol 标签前提交叉验证（high_vol_premise_check）单元测试。

覆盖：
  - 高波尾段 → 前提有效（ok=True，字段齐全）
  - 波动结构回落（高波→低波过渡）→ 前提失效（ok=False + reason）
  - EWMA≥q80 绝对口径判据
  - 分位阈值收紧不误杀（EWMA 判据独立兜底）
  - 数据不足 → 前提健康（不误报）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.regime import high_vol_premise_check


def _ohlcv(close: list[float]) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    c = pd.Series(np.asarray(close, float), index=idx)
    return pd.DataFrame(
        {"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1000.0},
        index=idx,
    )


class TestHighVolPremiseCheck:
    """high_vol 标签前提交叉验证（规则法 vol 维度复核）。"""

    def test_high_vol_premise_ok(self) -> None:
        """高波尾段（±5%）→ 前提有效，返回字段齐全。"""
        rng = np.random.default_rng(3)
        close = [100.0]
        for _ in range(240):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.0005))))
        for k in range(1, 61):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))
        r = high_vol_premise_check(_ohlcv(close))
        assert r["ok"] is True
        assert r["ewma_vol"] > 0
        assert r["eff_high"] > 0
        assert 0 <= r["vol_percentile"] <= 1
        assert r["reason"]

    def test_high_vol_premise_lost(self) -> None:
        """波动结构回落（高波→低波过渡）→ 前提失效。"""
        rng = np.random.default_rng(7)
        close = [100.0]
        for _ in range(200):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.03))))
        for amp in (0.015, 0.006, 0.002, 0.0003):
            for _ in range(10):
                close.append(close[-1] * (1.0 + float(rng.normal(0, amp))))
        for _ in range(22):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.0003))))
        r = high_vol_premise_check(_ohlcv(close))
        assert r["ok"] is False
        assert r["reason"]

    def test_ewma_threshold_judgement(self) -> None:
        """EWMA≥q80（绝对口径）判据：尾段高波下 ewma_vol 达有效高阈值。"""
        rng = np.random.default_rng(11)
        close = [100.0]
        for _ in range(250):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.0002))))
        for k in range(1, 21):
            close.append(close[-1] * (1.08 if k % 2 else 0.92))
        r = high_vol_premise_check(_ohlcv(close))
        assert r["ewma_vol"] >= r["eff_high"]
        assert r["ok"] is True

    def test_vol_min_percentile_not_false_alarm(self) -> None:
        """分位阈值收紧不误杀：EWMA 判据独立兜底。"""
        rng = np.random.default_rng(5)
        close = [100.0]
        for _ in range(240):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.0005))))
        for k in range(1, 61):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))
        r = high_vol_premise_check(_ohlcv(close), vol_min_percentile=0.999)
        assert r["ok"] is True  # EWMA 判据满足 → 即使分位门极紧也不误杀

    def test_insufficient_no_false_alarm(self) -> None:
        """数据不足 → 前提视为健康（不误报）。"""
        assert high_vol_premise_check(_ohlcv([100.0, 101.0, 102.0]))["ok"] is True
        assert high_vol_premise_check(_ohlcv([100.0] * 30))["ok"] is True
        assert high_vol_premise_check(pd.DataFrame())["ok"] is True
