"""plans/54 P1-3 — Regime 观察期机制（状态跳变不立即切换）单元测试。

覆盖：
  - observe_days=0（默认）行为不变（兼容现状，零回归）
  - observe_days=N：候选新制度连续 N 次保持才确认切换；观察期内维持旧制度（observed 标记）
  - 观察期内新候选中途变回 → 观察计数重置
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.regime import RegimeAwareSelector


def _mk(close: list[float]) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    c = pd.Series(np.asarray(close, float), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1000.0}, index=idx)


def _bull(n: int = 120) -> list[float]:
    return [100.0 * (1.003) ** t for t in range(n)]


def _bear(n: int = 120) -> list[float]:
    return [100.0 * (0.997) ** t for t in range(n)]


def _flat(n: int = 120) -> list[float]:
    rng = np.random.default_rng(7)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + float(rng.normal(0, 0.0003))))
    return out


def _sel(observe_days: int = 0) -> RegimeAwareSelector:
    return RegimeAwareSelector(
        use_hmm=False, use_multi_hmm=False, use_msm=False, observe_days=observe_days
    )


class TestObserveWindow:
    """观察期机制（plans/54 P1-3，文档 §7.1 跳变违背持续性）。"""

    def test_default_no_observe_compat(self) -> None:
        """observe_days=0（默认）→ 行为不变（直接切换，无 observed 标记）。"""
        sel = _sel(observe_days=0)
        r1 = sel.detect(_mk(_bull()))
        r2 = sel.detect(_mk(_bear()))
        assert r1["regime"] == "bull"
        assert r2["regime"] == "bear"
        assert not r2.get("observed")

    def test_observe_holds_old_regime(self) -> None:
        """observe_days=3：跳变后前 2 次维持旧制度（observed + candidate 标记）。"""
        sel = _sel(observe_days=3)
        assert sel.detect(_mk(_bull()))["regime"] == "bull"
        r2 = sel.detect(_mk(_bear()))
        assert r2["regime"] == "bull"  # 观察期维持旧制度
        assert r2.get("observed") is True
        assert r2.get("candidate_regime") == "bear"
        assert r2.get("observe_count") == 1
        r3 = sel.detect(_mk(_bear()))
        assert r3["regime"] == "bull"
        assert r3.get("observe_count") == 2

    def test_observe_confirms_after_n(self) -> None:
        """连续 N 次保持 → 确认切换（confirmed 标记）。"""
        sel = _sel(observe_days=3)
        assert sel.detect(_mk(_bull()))["regime"] == "bull"
        sel.detect(_mk(_bear()))  # count=1（维持 bull）
        sel.detect(_mk(_bear()))  # count=2（维持 bull）
        r4 = sel.detect(_mk(_bear()))  # count=3 → 确认
        assert r4["regime"] == "bear"
        assert r4.get("confirmed") is True

    def test_candidate_revert_resets_count(self) -> None:
        """观察期内新候选中途变回旧制度 → 观察计数重置。"""
        sel = _sel(observe_days=3)
        assert sel.detect(_mk(_bull()))["regime"] == "bull"
        sel.detect(_mk(_bear()))  # count=1 候选 bear
        r3 = sel.detect(_mk(_bull()))  # 回到 bull → 重置
        assert r3["regime"] == "bull"
        assert r3.get("observed") is None  # 无观察标记（同制度不观察）
        r4 = sel.detect(_mk(_bear()))  # 重新观察 count=1
        assert r4["regime"] == "bull"
        assert r4.get("observe_count") == 1

    def test_flat_regime_no_observe_noise(self) -> None:
        """同制度连续检测不触发观察（无候选）。"""
        sel = _sel(observe_days=3)
        r1 = sel.detect(_mk(_bull()))
        r2 = sel.detect(_mk(_bull()))
        assert r1["regime"] == "bull" and r2["regime"] == "bull"
        assert not r2.get("observed")
