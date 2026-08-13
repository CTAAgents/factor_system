"""tests/factor_engine/test_regime_split_validation.py — G7 5-Regime 因子拆分检验测试（35-gap-closure-plan §4.4）。

HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.regime_validation import validate_factor_across_regimes

REGIMES = ("bull", "bear", "oscillate", "high_vol", "low_vol")


def _make_panel(n_per_regime: int = 100, seed: int = 1) -> pd.DataFrame:
    """构造 5 制度 × n 样本面板：signal 与 fwd 全制度正相关。"""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for r in REGIMES:
        sig = rng.normal(0, 1, n_per_regime)
        fwd = sig * 0.5 + rng.normal(0, 0.3, n_per_regime)
        for i in range(n_per_regime):
            rows.append({"regime": r, "signal": sig[i], "fwd": fwd[i]})
    return pd.DataFrame(rows)


def test_all_regimes_positive_passes():
    """全 5 制度正向 → 覆盖 5、正向 ≥3 → passed。"""
    df = _make_panel()
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"])
    assert res["n_regimes_covered"] == 5
    assert res["n_positive"] >= 3
    assert res["passed"] is True
    assert res["regime_dependent"] is False


def test_bear_negative_flags_dependent():
    """bear 制度 ICIR 明显为负 → regime_dependent=True（环境依赖标记，不否决）。"""
    df = _make_panel()
    # 反转 bear 制度的信号-收益关系
    mask = df["regime"] == "bear"
    df.loc[mask, "signal"] = -df.loc[mask, "signal"]
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"])
    assert res["regime_dependent"] is True
    # 其余 4 制度正向 → 正向制度 4 ≥3 → 仍通过（标记不否决）
    assert res["passed"] is True


def test_three_negative_regimes_rejected():
    """3 个制度负向（正向仅 2 制度 <3）→ 不通过 + regime_dependent 标记。"""
    df = _make_panel()
    for bad in ("bear", "high_vol", "oscillate"):
        mask = df["regime"] == bad
        df.loc[mask, "signal"] = -df.loc[mask, "signal"]
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"])
    assert res["regime_dependent"] is True
    assert res["n_positive"] == 2
    assert res["passed"] is False


def test_bear_negative_but_still_passes_if_enough_positive():
    """bear 负向但其余 4 制度正向 → 正向制度 4 ≥3 → passed 仍通过（环境依赖标记保留）。"""
    df = _make_panel()
    mask = df["regime"] == "bear"
    df.loc[mask, "signal"] = -df.loc[mask, "signal"] * 0.1  # bear 轻微负向不构成强依赖
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"], min_positive_regimes=4)
    assert res["n_positive"] >= 4
    assert res["passed"] is True


def test_only_one_regime_covered_rejected():
    """仅 1 制度有足够样本 → 覆盖 <3 → 不通过。"""
    df = _make_panel(n_per_regime=100)
    # 其他制度样本不足
    df.loc[df["regime"] != "bull", "signal"] = np.nan
    df.loc[df["regime"] != "bull", "fwd"] = np.nan
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"])
    assert res["n_regimes_covered"] == 1
    assert res["passed"] is False


def test_empty_input_rejected():
    """空数据 → 不通过不崩溃。"""
    res = validate_factor_across_regimes(pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=str))
    assert res["passed"] is False
    assert res["per_regime"] == {}


def test_small_regime_sample_skipped():
    """制度样本 < min_regime_samples → 该制度不计入覆盖。"""
    df = _make_panel(n_per_regime=100)
    # 把 low_vol 缩减到 5 样本
    lv = df[df["regime"] == "low_vol"].head(5)
    df = pd.concat([df[df["regime"] != "low_vol"], lv])
    res = validate_factor_across_regimes(df["signal"], df["fwd"], df["regime"])
    assert res["n_regimes_covered"] == 4
    assert "low_vol" not in res["per_regime"]
