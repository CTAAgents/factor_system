"""阶段2 混合信号合成单元测试（mhf_signals）。

覆盖：反转合成、截面多空选择、权重边界、空输入降级。
纯逻辑测试（合成数据）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.mhf_signals import (  # noqa: E402
    MhfSignalConfig,
    build_hybrid_signals,
)


def _scores_panel() -> dict[str, dict[str, pd.Series]]:
    """3 品种 × 2 因子：A 因子高（应做空）、C 因子低（应做多）、B 居中。"""
    idx = pd.date_range("2026-01-05 09:00", periods=10, freq="15min")
    base = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], index=idx)
    return {
        "A": {"intraday_mom": base * 5.0, "rev_mid": base * 0.0},
        "B": {"intraday_mom": base * 1.0, "rev_mid": base * 0.0},
        "C": {"intraday_mom": base * -5.0, "rev_mid": base * 0.0},
    }


class TestHybridSignals:
    def test_reversal_direction(self) -> None:
        """反转逻辑：因子高（intraday_mom=5）→ 得分低 → 做空；因子低 → 做多。"""
        sig = build_hybrid_signals(
            _scores_panel(), MhfSignalConfig(max_positions=2)
        )
        assert "A" in sig and "B" in sig and "C" in sig
        # A 因子最高 → 得分最低 → 空头 -1
        assert set(sig["A"].dropna().unique()) == {-1.0}
        # C 因子最低 → 得分最高 → 多头 +1
        assert set(sig["C"].dropna().unique()) == {1.0}
        # 中间品种不持仓（max_positions=2 → 多空各 1）
        assert set(sig["B"].dropna().unique()) == {0.0}

    def test_market_neutral_counts(self) -> None:
        """每时间点多空数量对称。"""
        idx = pd.date_range("2026-01-05 09:00", periods=10, freq="15min")
        panel = {
            f"S{i}": {"intraday_mom": pd.Series(i, index=idx)}
            for i in range(6)
        }
        sig = build_hybrid_signals(panel, MhfSignalConfig(max_positions=4))
        mat = pd.DataFrame(sig)
        t = mat.iloc[0]
        assert (t == 1.0).sum() == 2   # 多头 4//2
        assert (t == -1.0).sum() == 2  # 空头 4//2
        assert (t == 0.0).sum() == 2

    def test_empty_panel(self) -> None:
        assert build_hybrid_signals({}) == {}

    def test_missing_factors_skip_symbol(self) -> None:
        """品种缺少全部权重因子 → 不参与。"""
        panel = {"A": {"intraday_mom": pd.Series([1.0])}, "B": {"other": pd.Series([1.0])}}
        sig = build_hybrid_signals(panel, MhfSignalConfig(max_positions=2))
        assert "A" in sig
        assert "B" not in sig

    def test_config_validation(self) -> None:
        with pytest.raises(ValueError):
            MhfSignalConfig(max_positions=1)
        with pytest.raises(ValueError):
            MhfSignalConfig(weights={})
