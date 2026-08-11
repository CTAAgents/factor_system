"""tests/factor_engine/test_report_generator.py — 报告生成器 IC 累计曲线/衰减曲线测试（GAP-060 报告呈现）。

HARNESS §测试随重构: 覆盖成功路径 / 缺数据降级。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.report_generator import ReportGenerator


def _report_dict(ic_series: pd.Series | None = None, multi_horizon: dict | None = None) -> dict:
    """构造报告 dict（含 metrics 中的 multi_horizon 可选）。"""
    metrics = {}
    if multi_horizon is not None:
        metrics["multi_horizon"] = multi_horizon
    report = {
        "metrics": metrics,
        "ic_series": ic_series,
        "equity_curve": pd.Series(
            np.linspace(1.0, 1.5, 30),
            index=pd.date_range("2024-01-01", periods=30, freq="D"),
        ),
    }
    if ic_series is not None:
        report["equity_curve"] = pd.Series(
            np.linspace(1.0, 1.5, len(ic_series)),
            index=ic_series.index,
        )
    return report


def _rising_ic() -> pd.Series:
    """单调上升的 IC 序列（累计曲线应持续上升）。"""
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series(np.full(n, 0.02), index=idx)


@pytest.fixture
def gen(tmp_path) -> ReportGenerator:
    return ReportGenerator(output_dir=str(tmp_path / "reports"))


# ─── IC 累计曲线 ─────────────────────────────────────────


def test_ic_cumulative_rising(gen):
    """单调正 IC → 累计曲线期末累计为正且持续上升。"""
    out = gen._generate_ic_cumulative(_report_dict(ic_series=_rising_ic()))
    assert "## IC 累计曲线" in out
    assert "期末累计 IC" in out
    assert "1.2000" in out  # 0.02 × 60 期 = 1.2


def test_ic_cumulative_missing(gen):
    """无 IC 数据 → 降级提示。"""
    out = gen._generate_ic_cumulative(_report_dict(ic_series=None))
    assert "（无 IC 数据）" in out


# ─── IC 衰减曲线 ─────────────────────────────────────────


def test_ic_decay_with_multi_horizon(gen):
    """含 multi_horizon → 各持有期 IC/ICIR 表 + 最佳持有期。"""
    mh = {
        "horizons": [1, 5, 10, 20],
        "ic_by_horizon": {1: 0.05, 5: 0.04, 10: 0.03, 20: 0.02},
        "icir_by_horizon": {1: 2.0, 5: 1.8, 10: 1.5, 20: 1.2},
        "best_horizon": 1,
        "decay_curve": {1: 1.0, 5: 0.8, 10: 0.6, 20: 0.4},
    }
    out = gen._generate_ic_decay(_report_dict(ic_series=_rising_ic(), multi_horizon=mh))
    assert "## IC 衰减曲线" in out
    assert "| 1 日 | 0.0500 | 2.0000 |" in out
    assert "| 20 日 | 0.0200 | 1.2000 |" in out
    assert "最佳持有期: 1 日" in out


def test_ic_decay_missing(gen):
    """无 multi_horizon → 降级提示（提示启用配置）。"""
    out = gen._generate_ic_decay(_report_dict(ic_series=_rising_ic()))
    assert "（无多持有期数据" in out
    assert "FTS_EVAL_HORIZONS" in out


def test_full_report_contains_new_sections(gen, tmp_path):
    """完整报告生成含 IC 累计曲线与 IC 衰减曲线节。"""
    mh = {
        "horizons": [1, 5],
        "ic_by_horizon": {1: 0.05, 5: 0.03},
        "icir_by_horizon": {1: 2.0, 5: 1.2},
        "best_horizon": 1,
        "decay_curve": {1: 1.0, 5: 0.6},
    }
    path = gen.generate(_report_dict(ic_series=_rising_ic(), multi_horizon=mh), output_dir=str(tmp_path / "reports"))
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "## IC 累计曲线" in content
    assert "## IC 衰减曲线" in content
    assert "## 回测摘要" in content
