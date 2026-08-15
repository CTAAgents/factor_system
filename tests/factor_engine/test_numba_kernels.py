"""tests/factor_engine/test_numba_kernels.py — plans/38 4.2 ts_rank numba 内核对照测试。

38 号计划（numba 批 4）4.2 阶段接入 ts_rank（feature_ops RollingOps）numba 1D/2D 内核
（fts/factor_engine/numba_kernels.py）。oracle 照抄现值实现（pandas ``rolling.rank``：
平均秩法、分母=非 NaN 观测计数、NaN 位置与窗口保留语义）。

验收（plans/38 §6.1）：
- 算子级 wired vs oracle 零漂移（含 numba 关闭/不可用时的回退路径）；
- 内核级 1D 内核 vs pandas oracle 零漂移（仅 numba 可用时执行）；
- 接入断言：全有限输入实际走 numba 快速路径（非死代码）；
- DataFrame（面板列）输入回退既有路径不回归。

版本: v1.1.0（38-4.5 回退后，仅 ts_rank）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine import numba_kernels as _nbk
from fts.factor_engine.feature_ops import RollingOps

requires_numba = pytest.mark.skipif(
    not (_nbk._NUMBA_AVAILABLE and _nbk.enabled()),
    reason="numba 不可用或 ops_numba 开关关闭",
)


# ─── 现值 oracle（照抄非 numba 实现） ────────────────────────


def _oracle_rank(series: pd.Series, window: int) -> pd.Series:
    """现值 oracle：照抄 RollingOps.ts_rank 的非 numba 路径。"""
    return series.rolling(window=window).rank(pct=True)


# ─── 测试输入（随机/常数/单调/含 NaN/含 inf/短/空/单元素） ──


def _make_cases() -> list[tuple[str, pd.Series]]:
    rng = np.random.default_rng(7)
    nan_mixed = pd.Series(np.where(rng.random(120) < 0.25, np.nan, rng.standard_normal(120)))
    return [
        ("random", pd.Series(rng.standard_normal(120))),
        ("constant", pd.Series(np.full(120, 1.5))),
        ("monotonic", pd.Series(np.arange(120, dtype=float))),
        ("nan_mixed", nan_mixed),
        ("short", pd.Series(rng.standard_normal(5))),
        ("empty", pd.Series(np.array([], dtype=float))),
        ("single", pd.Series(np.array([1.0]))),
        (
            "with_inf",
            pd.Series(np.array([1.0, 2.0, np.inf, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
                                11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0])),
        ),
    ]


_CASES = _make_cases()
_WINDOWS = [5, 20, 60]
_CASE_IDS = [c[0] for c in _CASES]


def _assert_zero_drift(a: pd.Series, b: pd.Series) -> None:
    """逐位一致：NaN 位置一致 + 有限值容差一致。"""
    av, bv = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    assert av.shape == bv.shape, f"形状不一致: {av.shape} vs {bv.shape}"
    nan_a, nan_b = np.isnan(av), np.isnan(bv)
    assert (nan_a == nan_b).all(), f"NaN 位置不一致: {(nan_a != nan_b).sum()} 处"
    np.testing.assert_allclose(av[~nan_a], bv[~nan_b], atol=1e-12, rtol=1e-9)


# ─── 算子级 wired vs oracle 零漂移（含回退路径） ──────────────


@pytest.mark.parametrize("w", _WINDOWS)
@pytest.mark.parametrize("_name,series", _CASES, ids=_CASE_IDS)
def test_ts_rank_zero_drift(_name: str, series: pd.Series, w: int) -> None:
    _assert_zero_drift(RollingOps.ts_rank(series, w), _oracle_rank(series, w))


# ─── 内核级 1D vs pandas oracle（仅 numba 可用） ─────────────


@requires_numba
def test_rank_1d_matches_pandas() -> None:
    """rank_1d vs pandas ``rolling.rank``（含 ties）逐位一致（ts_rank 语义真源）。"""
    rng = np.random.default_rng(11)
    arr = np.round(rng.standard_normal(80), 1)  # 高频率 ties
    got = _nbk.rank_1d(arr, 20, 20, pct=True)
    assert got is not None
    expected = pd.Series(arr).rolling(20, min_periods=20).rank(pct=True)
    _assert_zero_drift(pd.Series(got), expected)


# ─── 接入断言：全有限输入实际走 numba 快速路径 ───────────────


@requires_numba
def test_numba_path_engaged(monkeypatch) -> None:
    """全有限输入 → 实际调用 njit 内核（快速路径非死代码）。"""
    calls: list[int] = []
    orig = _nbk._rank_1d_njit

    def spy(arr, *a, **k):  # noqa: ANN002, ANN003
        calls.append(1)
        return orig(arr, *a, **k)

    monkeypatch.setattr(_nbk, "_rank_1d_njit", spy)
    s = pd.Series(np.arange(1, 51, dtype=float))
    out = RollingOps.ts_rank(s, 10)
    assert calls, "rank_1d 未被调用：全有限输入未走 numba 快速路径"
    assert len(out) == len(s)


# ─── 4.3 面板 2D 路径：全有限 DataFrame → 2D njit 内核 ──────


def _panel_input(rows: int = 120, cols: int = 8, seed: int = 5) -> pd.DataFrame:
    """全有限面板（偏移避免 pct_change 除 0 产生 inf）。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(rng.standard_normal((rows, cols)) + 10.0, columns=list("abcdefgh")[:cols])


@requires_numba
@pytest.mark.parametrize("w", [5, 20, 60])
def test_panel_2d_zero_drift(w: int) -> None:
    """全有限面板：算子 2D njit 输出 vs oracle（panel 路径）逐位一致。"""
    df = _panel_input()
    _assert_zero_drift(RollingOps.ts_rank(df, w), _oracle_rank(df, w))


@requires_numba
def test_panel_2d_path_engaged(monkeypatch) -> None:
    """全有限 DataFrame → 实际调用 2D njit 内核（消除逐列循环，非死代码）。"""
    calls: list[int] = []
    orig = _nbk._rank_2d_njit

    def spy(arr, *a, **k):  # noqa: ANN002, ANN003
        calls.append(1)
        return orig(arr, *a, **k)

    monkeypatch.setattr(_nbk, "_rank_2d_njit", spy)
    df = _panel_input()
    out = RollingOps.ts_rank(df, 10)
    assert calls, "_rank_2d_njit 未被调用：全有限面板未走 2D numba 快速路径"
    assert list(out.columns) == list(df.columns)


@requires_numba
def test_panel_gap_fallback_zero_drift() -> None:
    """含 NaN 面板（缺口列）→ 不触发 2D 内核，回退既有逐列路径且逐位一致。"""
    rng = np.random.default_rng(9)
    df = pd.DataFrame(rng.standard_normal((80, 4)) + 10.0, columns=list("abcd"))
    df.iloc[5:8, 1] = np.nan  # 缺口列（须保持 pct_change 无 inf：行 0 非 0）
    df.iloc[20:25, 3] = np.nan
    for w in (10, 20):
        _assert_zero_drift(RollingOps.ts_rank(df, w), _oracle_rank(df, w))


# ─── DataFrame（面板列）输入回退既有路径不回归 ──────────────


def test_dataframe_fallback_no_regression() -> None:
    """DataFrame 输入回退既有路径（逐列），与现值逐位一致。"""
    rng = np.random.default_rng(3)
    df = pd.DataFrame(rng.standard_normal((60, 4)), columns=list("abcd"))
    _assert_zero_drift(RollingOps.ts_rank(df, 10), _oracle_rank(df, 10))


# ════════════════════════════════════════════════════════════
# plans/40 C 层：ts_zscore / ts_cvar numba 1D/2D 内核对照
# ════════════════════════════════════════════════════════════


def _oracle_zscore(series: pd.Series | pd.DataFrame, window: int):
    """现值 oracle：照抄 feature_ops._ts_zscore_vec（含 NaN skipna 语义）。"""
    from fts.factor_engine.feature_ops import _ts_zscore_vec

    arr = series.to_numpy(dtype=float)
    if arr.ndim == 2:
        cols = list(series.columns)
        res = np.stack([_ts_zscore_vec(arr[:, j], window) for j in range(len(cols))], axis=1)
        return pd.DataFrame(res, index=series.index, columns=cols)
    return pd.Series(_ts_zscore_vec(arr, window), index=series.index)


def _oracle_cvar(series: pd.Series, window: int, alpha: float) -> pd.Series:
    """现值 oracle：照抄 ops_library.ts_cvar_*（_ret + _native_apply + _cvar/_batch + fillna）。"""
    from fts.factor_engine.feature_ops import _rolling_apply_native
    from fts.factor_engine.ops_library import _ret

    r = _ret(series)
    arr = r.to_numpy(dtype=float)

    def _cvar(v: np.ndarray) -> float:
        q = np.nanquantile(v, alpha)
        tail = v[v <= q]
        return float(np.mean(tail)) if tail.size else float(q)

    def _batch(rows: np.ndarray) -> np.ndarray:
        s = np.sort(rows, axis=-1)
        pos = alpha * (rows.shape[1] - 1)
        lo, hi = int(np.floor(pos)), int(np.ceil(pos))
        q = s[:, lo] + (s[:, hi] - s[:, lo]) * (pos - lo)
        mask = rows <= q[:, None]
        cnt = mask.sum(axis=-1)
        return np.where(cnt > 0, np.where(mask, rows, 0.0).sum(axis=-1) / cnt, q)

    out = _rolling_apply_native(arr, window, 2, _cvar, _batch)
    return pd.Series(np.nan_to_num(out, nan=0.0), index=series.index)


@pytest.mark.parametrize("w", _WINDOWS)
@pytest.mark.parametrize("_name,series", _CASES, ids=_CASE_IDS)
def test_ts_zscore_zero_drift(_name: str, series: pd.Series, w: int) -> None:
    """wired ts_zscore vs 现值 oracle 逐位一致（全有限走 numba / 含 NaN 回退）。"""
    _assert_zero_drift(RollingOps.ts_zscore(series, w), _oracle_zscore(series, w))


@pytest.mark.parametrize("w", [5, 20, 60])
@pytest.mark.parametrize("_name,series", _CASES, ids=_CASE_IDS)
def test_ts_cvar_zero_drift(_name: str, series: pd.Series, w: int) -> None:
    """wired ts_cvar_95 vs 现值 oracle 逐位一致（全有限走 numba / 含 NaN 回退）。"""
    from fts.factor_engine.ops_library import D10Ops

    _assert_zero_drift(D10Ops.ts_cvar_95(series, w), _oracle_cvar(series, w, 0.05))
    _assert_zero_drift(D10Ops.ts_cvar_99(series, w), _oracle_cvar(series, w, 0.01))


@requires_numba
def test_zscore_1d_matches_oracle() -> None:
    """内核级 zscore_1d vs _ts_zscore_vec 逐位一致（含 NaN）。"""
    from fts.factor_engine.feature_ops import _ts_zscore_vec

    rng = np.random.default_rng(13)
    arr = np.where(rng.random(100) < 0.2, np.nan, rng.standard_normal(100))
    got = _nbk.zscore_1d(arr, 20)
    assert got is not None
    _assert_zero_drift(pd.Series(got), pd.Series(_ts_zscore_vec(arr, 20)))


@requires_numba
def test_cvar_1d_matches_oracle() -> None:
    """内核级 cvar_1d vs _oracle_cvar 逐位一致（含 NaN 缺口）。"""
    rng = np.random.default_rng(17)
    arr = np.where(rng.random(100) < 0.15, np.nan, rng.standard_normal(100))
    s = pd.Series(arr)
    from fts.factor_engine.ops_library import _ret

    r = _ret(s)
    got_r = _nbk.cvar_1d(r.to_numpy(dtype=float), 20, 0.05)
    assert got_r is not None
    # 算子层对前缀（cnt<min_periods）NaN 做 fillna(0.0)，内核原始输出需同口径比较
    _assert_zero_drift(
        pd.Series(np.nan_to_num(got_r, nan=0.0)),
        _oracle_cvar(s, 20, 0.05),
    )


@requires_numba
def test_zscore_path_engaged(monkeypatch) -> None:
    """全有限输入 → ts_zscore 实际调用 njit 内核（快速路径非死代码）。"""
    calls: list[int] = []
    orig = _nbk._ts_zscore_1d_njit

    def spy(arr, *a, **k):  # noqa: ANN002, ANN003
        calls.append(1)
        return orig(arr, *a, **k)

    monkeypatch.setattr(_nbk, "_ts_zscore_1d_njit", spy)
    s = pd.Series(np.arange(1, 61, dtype=float))
    out = RollingOps.ts_zscore(s, 10)
    assert calls, "zscore_1d 未被调用：全有限输入未走 numba 快速路径"
    assert len(out) == len(s)


@requires_numba
def test_cvar_path_engaged(monkeypatch) -> None:
    """全有限输入 → ts_cvar_95 实际调用 njit 内核（快速路径非死代码）。"""
    from fts.factor_engine.ops_library import D10Ops

    calls: list[int] = []
    orig = _nbk._ts_cvar_1d_njit

    def spy(arr, *a, **k):  # noqa: ANN002, ANN003
        calls.append(1)
        return orig(arr, *a, **k)

    monkeypatch.setattr(_nbk, "_ts_cvar_1d_njit", spy)
    s = pd.Series(np.arange(1, 61, dtype=float) + 10.0)
    out = D10Ops.ts_cvar_95(s, 10)
    assert calls, "cvar_1d 未被调用：全有限输入未走 numba 快速路径"
    assert len(out) == len(s)


@requires_numba
@pytest.mark.parametrize("w", [5, 20])
def test_zscore_panel_2d_zero_drift(w: int) -> None:
    """全有限面板：ts_zscore 2D njit 输出 vs oracle（panel 路径）逐位一致。"""
    df = _panel_input()
    _assert_zero_drift(RollingOps.ts_zscore(df, w), _oracle_zscore(df, w))
