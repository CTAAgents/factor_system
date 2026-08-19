"""plans/54 P0-3 — 市场前提监控（check_market_premise）单元测试。

覆盖：
  - 趋势制度（bull/bear）下趋势结构消失 → 前提告警
  - 高波动制度下 vol 分位回落 → 前提告警
  - 震荡制度下 vol 爆波 → 前提告警
  - 未知制度/面板不足 → 不误报（前提健康）
  - 前提健康场景
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.monitor.logic_monitor import check_market_premise


def _mk(close: list[float]) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    c = pd.Series(np.asarray(close, float), index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c}, index=idx)


def _panel(close_a: list[float], close_b: list[float] | None = None) -> dict[str, pd.DataFrame]:
    return {"A": _mk(close_a), "B": _mk(close_b or [c * 0.8 for c in close_a])}


def _rising(n: int = 120, daily: float = 0.002) -> list[float]:
    return [100.0 * (1.0 + daily) ** t for t in range(n)]


def _flat(n: int = 120) -> list[float]:
    rng = np.random.default_rng(7)
    out = [100.0]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + float(rng.normal(0, 0.0003))))
    return out


class TestMarketPremise:
    """市场前提监控（plans/54 P0-3，监控前提而非结果）。"""

    def test_trend_regime_premise_ok(self) -> None:
        """bull 制度 + 强趋势 → 前提健康。"""
        res = check_market_premise(_panel(_rising()), "bull")
        assert res.premise_ok is True
        assert res.trend_structure_ok is True
        assert res.alert is None

    def test_trend_regime_premise_lost(self) -> None:
        """bull 制度 + 横盘（趋势结构消失）→ 前提告警。"""
        res = check_market_premise(_panel(_flat()), "bull")
        assert res.premise_ok is False
        assert "趋势结构消失" in (res.alert or "")

    def test_high_vol_regime_vol_lost(self) -> None:
        """high_vol 制度 + 前高波后平稳（波动结构回落）→ 前提告警。"""
        close: list[float] = [100.0]
        for _ in range(90):
            close.append(close[-1] * 1.0005)
        for k in range(1, 31):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))  # 高波尾段
        # 再叠加平稳尾段 → vol 分位回落至低位
        flat_tail = [close[-1]]
        for _ in range(20):
            flat_tail.append(flat_tail[-1] * 1.0005)
        # 显式 vol_min_percentile=0.9（当前分位 0.595 已显著回落）验证告警逻辑
        res = check_market_premise(_panel(close + flat_tail[1:]), "high_vol", vol_min_percentile=0.9)
        assert res.premise_ok is False
        assert "波动结构异常" in (res.alert or "")

    def test_oscillate_regime_vol_spike(self) -> None:
        """oscillate 制度 + 波动爆波 → 前提告警。"""
        close: list[float] = [100.0]
        for _ in range(90):
            close.append(close[-1] * 1.0005)
        for k in range(1, 31):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))  # 高波动尾段
        res = check_market_premise(_panel(close), "oscillate")
        assert res.premise_ok is False
        assert "波动结构异常" in (res.alert or "")

    def test_unknown_regime_no_false_alarm(self) -> None:
        """未知制度 → 前提健康（不误报）。"""
        res = check_market_premise(_panel(_flat()), None)
        assert res.premise_ok is True

    def test_insufficient_panel_no_false_alarm(self) -> None:
        """面板不足（<2 品种）→ 前提健康（不误报）。"""
        res = check_market_premise({"A": _mk(_rising())}, "bull")
        assert res.premise_ok is True

    def test_high_vol_regime_premise_ok(self) -> None:
        """high_vol 制度 + 高波动尾段 → 前提健康。"""
        close: list[float] = [100.0]
        for _ in range(90):
            close.append(close[-1] * 1.0005)
        for k in range(1, 31):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))
        res = check_market_premise(_panel(close), "high_vol")
        assert res.premise_ok is True
        assert res.vol_structure_ok is True

    def test_vol_window_restricts_history(self) -> None:
        """vol_window 固定窗口：分位仅使用最近 vol_window 个有效波动点（消除全历史拖累）。"""
        rng = np.random.default_rng(42)
        close = [100.0]
        for _ in range(300):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.04))))  # 前段高波
        for _ in range(100):
            close.append(close[-1] * (1.0 + float(rng.normal(0, 0.005))))  # 近期回落
        panel = _panel(close)
        full = check_market_premise(panel, "high_vol")
        win = check_market_premise(panel, "high_vol", vol_window=60)
        # 手动重算窗口 60 分位作为 oracle（与实现同口径）
        mat = pd.DataFrame({s: pd.to_numeric(df["close"], errors="coerce") for s, df in panel.items()}).dropna(
            how="all"
        )
        fv = mat.apply(lambda s: s.dropna().iloc[0])
        idx = (mat.div(fv, axis=1) * 100.0).mean(axis=1).dropna()
        v20 = idx.pct_change().dropna().rolling(20).std() * np.sqrt(252)
        vh = v20.dropna().iloc[-60:]
        expect = float((vh <= v20.dropna().iloc[-1]).mean())
        assert abs(win.vol_percentile - expect) < 1e-6
        assert win.vol_percentile != full.vol_percentile  # 窗口截断确实改变分位

    def test_vol_window_short_panel_unchanged(self) -> None:
        """短面板（<vol_window）时窗口截取全部有效点，行为与全历史一致。"""
        close: list[float] = [100.0]
        for k in range(1, 61):
            close.append(close[-1] * (1.05 if k % 2 else 0.95))
        panel = _panel(close)
        default = check_market_premise(panel, "high_vol")
        explicit = check_market_premise(panel, "high_vol", vol_window=252)
        assert default.vol_percentile == explicit.vol_percentile
        assert default.premise_ok == explicit.premise_ok
