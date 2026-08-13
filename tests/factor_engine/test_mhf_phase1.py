"""阶段1 分钟因子与评估模块单元测试（mhf_factors / mhf_evaluation）。

覆盖：因子零未来、边界降级、数值兜底、IC 评估、时间切割、FDR 校正。
纯逻辑测试（合成数据，不依赖 TDX / DuckDB）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.mhf_evaluation import (  # noqa: E402
    bh_fdr,
    evaluate_factor,
    factor_ic,
    forward_returns,
    split_time_series,
    summarize_ic,
)
from fts.factor_engine.mhf_factors import (  # noqa: E402
    MhfFactorConfig,
    compute_mhf_factor_panel,
    compute_mhf_factors,
)


def _synth_ohlcv(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """合成分钟 K 线：随机游走 + 成交量为正。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05 09:00", periods=n, freq="5min")
    ret = rng.normal(0, 0.001, n)
    close = 3000 * np.exp(np.cumsum(ret))
    open_ = close * (1 + rng.normal(0, 0.0002, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.001, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.001, n))
    volume = rng.integers(100, 10000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class TestComputeFactors:
    """因子计算：数量、零未来、兜底、降级。"""

    def test_returns_all_factors(self) -> None:
        df = _synth_ohlcv()
        factors = compute_mhf_factors(df)
        assert set(factors.keys()) == {
            "mom_short", "mom_mid", "rev_short", "rev_mid", "vol_std",
            "vol_regime", "vol_ratio", "vp_sync", "ret_skew",
            "intraday_mom", "pos_range",
        }
        for s in factors.values():
            assert len(s) == len(df)
            assert s.index.equals(df.index)
            assert s.replace([np.inf, -np.inf], np.nan).notna().all()

    def test_no_future_leak_mom(self) -> None:
        """mom_short 在 t 时刻只依赖 ≤t 数据：截断后保留段与全量一致。"""
        df = _synth_ohlcv(n=200)
        factors_full = compute_mhf_factors(df, MhfFactorConfig(mom_short=5))
        truncated = df.iloc[:150]
        factors_trunc = compute_mhf_factors(truncated, MhfFactorConfig(mom_short=5))
        assert factors_full["mom_short"].iloc[:150].equals(factors_trunc["mom_short"])

    def test_insufficient_rows_returns_empty(self) -> None:
        df = _synth_ohlcv(n=30)
        assert compute_mhf_factors(df) == {}

    def test_empty_input_returns_empty(self) -> None:
        assert compute_mhf_factors(pd.DataFrame()) == {}
        assert compute_mhf_factors(None) == {}

    def test_missing_columns_returns_empty(self) -> None:
        df = _synth_ohlcv().drop(columns=["volume"])
        assert compute_mhf_factors(df) == {}

    def test_panel_skip_bad_symbol(self) -> None:
        panel = {"RB0": _synth_ohlcv(), "BAD": pd.DataFrame()}
        out = compute_mhf_factor_panel(panel)
        assert "RB0" in out["mom_short"]
        assert "BAD" not in out["mom_short"]

    def test_config_validation(self) -> None:
        with pytest.raises(ValueError):
            MhfFactorConfig(mom_short=0)


class TestEvaluation:
    """评估：IC/摘要/切割/FDR。"""

    def test_forward_returns(self) -> None:
        close = pd.Series([1.0, 2.0, 4.0], index=range(3))
        fwd = forward_returns(close, 1)
        assert fwd.iloc[0] == pytest.approx(1.0)
        assert np.isnan(fwd.iloc[-1])  # 最后一根无前视收益

    def test_factor_ic_empty(self) -> None:
        ic = factor_ic(pd.Series(dtype=float), pd.Series(dtype=float))
        assert ic.empty

    def test_summarize_ic_empty(self) -> None:
        s = summarize_ic(pd.Series(dtype=float))
        assert s.n_periods == 0 and s.ic_mean == 0.0

    def test_summarize_ic_known(self) -> None:
        ic = pd.Series([0.1, 0.2, 0.15, 0.05, 0.3])
        s = summarize_ic(ic)
        assert s.n_periods == 5
        assert s.ic_mean == pytest.approx(0.16)
        assert s.win_rate == 1.0
        assert s.ir > 0

    def test_evaluate_factor_runs(self) -> None:
        df = _synth_ohlcv()
        factors = compute_mhf_factors(df)
        s = evaluate_factor(factors["mom_short"], df["close"], horizon=5)
        assert s.n_periods >= 1
        assert len(s.ic_decay) >= 1

    def test_split_time_series(self) -> None:
        f = pd.Series(np.arange(100, dtype=float), index=pd.date_range("2026-01-01", periods=100))
        r = pd.Series(np.ones(100, dtype=float), index=f.index)
        tf, tr, vf, vr = split_time_series(f, r, train_ratio=0.7)
        assert len(tf) == 70 and len(vf) == 30
        assert tf.index.max() < vf.index.min()  # 严格时间顺序

    def test_bh_fdr_basic(self) -> None:
        q = bh_fdr(np.array([0.001, 0.01, 0.5]))
        assert q.shape == (3,)
        assert q[0] <= q[1]  # 单调性
        assert q[0] < 0.05

    def test_bh_fdr_empty(self) -> None:
        assert len(bh_fdr(np.array([]))) == 0
