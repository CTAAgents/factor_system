"""
tests/factor_engine/test_panel_vector.py — 全矩阵化横截面评估单元测试

验证 panel_vector 模块（预对齐面板 + 全矩阵化 IC）与旧路径（逐日 spearmanr）
语义逐位一致，覆盖正确性、边界与完整流水线对照。

参考 oracle：本文件内的 `_reference_ics` 逐日实现等价于
evaluation_chain._cs_compute_ics（联合有效子集 + 常数守卫 + 有效样本下限）。

版本: v1.0.0（panel_vector 随测）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats

from fts.factor_engine.panel_vector import (
    AlignedPanel,
    _arr_equal_nan,
    build_forward_return_matrix,
    compute_cs_ics_vectorized,
    execute_factor_panel,
    prealign_panel,
)


# ── 参考 oracle：旧路径逐日 spearmanr（等价 evaluation_chain._cs_compute_ics） ──


def _reference_ics(
    signal: np.ndarray,
    fwd: np.ndarray,
    min_valid: int = 5,
    std_floor: float = 1e-10,
) -> tuple[list[float], list[int]]:
    """逐日 Spearman IC 参考实现：联合有效子集 + 常数守卫 + 有效样本下限。"""
    ics: list[float] = []
    rows: list[int] = []
    for t in range(signal.shape[0]):
        sig_t = signal[t]
        ret_t = fwd[t]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        if np.sum(valid) < min_valid:
            continue
        sv = sig_t[valid]
        rv = ret_t[valid]
        if np.std(sv) < std_floor or np.std(rv) < std_floor:
            continue
        ic_val, _ = sp_stats.spearmanr(sv, rv)
        if not np.isnan(ic_val):
            ics.append(float(ic_val))
            rows.append(t)
    return ics, rows


def _assert_matches_reference(
    vec_ics: np.ndarray,
    ref_ics: list[float],
    ref_rows: list[int],
    rtol: float = 1e-9,
    atol: float = 1e-12,
) -> None:
    """断言向量化 IC 与参考一致：跳过位置一致 + 值逐位近似。"""
    ics = np.asarray(vec_ics, dtype=np.float64)
    non_nan_rows = list(np.nonzero(~np.isnan(ics))[0])
    assert non_nan_rows == ref_rows, f"跳过模式不一致: {non_nan_rows} vs {ref_rows}"
    if ref_rows:
        assert np.allclose(ics[ref_rows], np.asarray(ref_ics, dtype=np.float64), rtol=rtol, atol=atol)


def _make_matrix(
    n_dates: int,
    n_syms: int,
    seed: int,
    nan_ratio: float = 0.0,
    constant_rows: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """构造信号/收益矩阵；nan_ratio 注入随机缺失，constant_rows 前 N 行信号置常数。"""
    rng = np.random.default_rng(seed)
    sig = rng.standard_normal((n_dates, n_syms))
    fwd = rng.standard_normal((n_dates, n_syms))
    if nan_ratio > 0:
        sig[rng.random((n_dates, n_syms)) < nan_ratio] = np.nan
        fwd[rng.random((n_dates, n_syms)) < nan_ratio] = np.nan
    for t in range(min(constant_rows, n_dates)):
        sig[t, :] = 1.0
    return sig, fwd


# ── IC 正确性 ─────────────────────────────────────────────


def test_clean_matrix_matches_reference() -> None:
    sig, fwd = _make_matrix(500, 60, seed=1)
    ics, _ = compute_cs_ics_vectorized(sig, fwd)
    ref_ics, ref_rows = _reference_ics(sig, fwd)
    _assert_matches_reference(ics, ref_ics, ref_rows)


def test_nan_gaps_match_reference() -> None:
    """带随机缺失（模拟品种上市时间差异）时与参考逐位一致。"""
    sig, fwd = _make_matrix(800, 120, seed=7, nan_ratio=0.10)
    ics, mask = compute_cs_ics_vectorized(sig, fwd)
    ref_ics, ref_rows = _reference_ics(sig, fwd)
    _assert_matches_reference(ics, ref_ics, ref_rows)
    # 掩码 = 信号与收益联合有限
    assert mask.shape == sig.shape
    assert np.array_equal(mask, np.isfinite(sig) & np.isfinite(fwd))


def test_constant_signal_rows_skipped() -> None:
    sig, fwd = _make_matrix(300, 40, seed=3, constant_rows=15)
    ics, _ = compute_cs_ics_vectorized(sig, fwd)
    ref_ics, ref_rows = _reference_ics(sig, fwd)
    _assert_matches_reference(ics, ref_ics, ref_rows)
    # 前 15 行（常数信号）必须被跳过（NaN）
    assert all(np.isnan(ics[t]) for t in range(15))


def test_low_coverage_rows_skipped() -> None:
    """有效列 < min_valid 的行跳过。"""
    sig, fwd = _make_matrix(200, 30, seed=4)
    # 将第 0 行信号大量置 NaN，使有效列 < 5
    sig[0, 3:] = np.nan
    ics, _ = compute_cs_ics_vectorized(sig, fwd, min_valid=5)
    assert np.isnan(ics[0])
    ref_ics, ref_rows = _reference_ics(sig, fwd)
    _assert_matches_reference(ics, ref_ics, ref_rows)


def test_all_nan_row_no_error() -> None:
    """全 NaN 行不报错，置 NaN。"""
    sig, fwd = _make_matrix(100, 20, seed=5)
    sig[50, :] = np.nan
    fwd[60, :] = np.nan
    ics, _ = compute_cs_ics_vectorized(sig, fwd)
    assert np.isnan(ics[50]) and np.isnan(ics[60])


def test_shape_mismatch_raises() -> None:
    sig, _ = _make_matrix(100, 20, seed=6)
    with pytest.raises(ValueError, match="形状不一致"):
        compute_cs_ics_vectorized(sig, sig[:, :10])


@pytest.mark.parametrize("seed", [11, 12, 13])
@pytest.mark.parametrize("nan_ratio", [0.0, 0.05, 0.20])
def test_randomized_regression(seed: int, nan_ratio: float) -> None:
    """多种缺失比例随机回归：与逐日参考一致。"""
    sig, fwd = _make_matrix(600, 80, seed=seed, nan_ratio=nan_ratio)
    ics, _ = compute_cs_ics_vectorized(sig, fwd)
    ref_ics, ref_rows = _reference_ics(sig, fwd)
    _assert_matches_reference(ics, ref_ics, ref_rows)


# ── 预对齐面板 ────────────────────────────────────────────


def _make_panel_with_gaps(
    n_symbols: int,
    n_dates: int,
    seed: int,
    drop_ratio: float = 0.05,
) -> dict[str, pd.DataFrame]:
    """构造带随机缺口的合成面板（模拟真实品种上市时间差异）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-07-31", periods=n_dates)
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n_dates)))
        df = pd.DataFrame(
            {
                "open": close * (1.0 + rng.normal(0.0, 0.002, n_dates)),
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.lognormal(11.0, 0.5, n_dates),
                "amount": close,
            },
            index=dates,
        )
        drop_idx = rng.choice(n_dates, size=int(n_dates * drop_ratio), replace=False)
        keep = dates.delete(drop_idx)
        panel[f"S{i}"] = df.loc[keep]
    return panel


def test_prealign_coverage_dates() -> None:
    """共同日期按「至少 min_symbols 个品种有数据」口径（非全量交集）。"""
    panel = _make_panel_with_gaps(20, 500, seed=21, drop_ratio=0.3)
    ap = prealign_panel(panel, min_symbols=5)
    # 全量交集必然为空（每品种都缺不同日期），覆盖率口径必须非空
    assert len(ap.dates) > 0
    assert len(ap.symbols) == 20
    assert ap.values.shape == (len(ap.dates), 20, 6)
    assert list(ap.cols) == ["open", "high", "low", "close", "volume", "amount"]


def test_prealign_values_match_manual_reindex() -> None:
    """预对齐 close 矩阵与逐品种手工 reindex 一致。"""
    panel = _make_panel_with_gaps(10, 300, seed=22)
    ap = prealign_panel(panel, min_symbols=3)
    manual = np.column_stack([panel[s]["close"].reindex(ap.dates).to_numpy(dtype=np.float64) for s in ap.symbols])
    close = ap.col("close")
    assert np.array_equal(np.nan_to_num(close), np.nan_to_num(manual))
    assert np.allclose(close, manual, equal_nan=True)


def test_prealign_forward_returns_5d() -> None:
    """5 日前向收益口径：fwd[t] = (close[t+5]-close[t])/max(close[t], 1e-10)。"""
    panel = _make_panel_with_gaps(5, 200, seed=23, drop_ratio=0.0)
    ap = prealign_panel(panel, min_symbols=1, forward_days=5)
    close = ap.col("close")
    expected = np.zeros_like(close)
    expected[:-5] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)
    assert np.allclose(ap.fwd_returns, expected, equal_nan=True)


def test_prealign_empty_panel_raises() -> None:
    with pytest.raises(ValueError, match="面板为空"):
        prealign_panel({})


def test_prealign_no_common_dates_raises() -> None:
    panel = _make_panel_with_gaps(2, 100, seed=24, drop_ratio=0.99)
    with pytest.raises(ValueError, match="没有找到"):
        prealign_panel(panel, min_symbols=3)


# ── 完整流水线对照（panel → prealign → 2D 因子 → 向量化 IC vs 旧路径语义） ──


def _factor_1d(df: pd.DataFrame, window: int) -> np.ndarray:
    c = df["close"]
    mu = c.rolling(window).mean()
    sd = c.rolling(window).std()
    return ((c - mu) / sd.replace(0.0, np.nan)).to_numpy(dtype=np.float64)


def _factor_2d(ap: AlignedPanel, window: int) -> np.ndarray:
    df = pd.DataFrame(ap.col("close"), columns=ap.symbols)
    mu = df.rolling(window).mean()
    sd = df.rolling(window).std()
    return ((df - mu) / sd.replace(0.0, np.nan)).to_numpy(dtype=np.float64)


def _old_full_semantics(
    panel: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    window: int,
) -> tuple[list[float], list[int]]:
    """旧路径完整语义：逐品种 1D 因子 + 5 日前向收益 → 对齐 → 逐日 spearmanr。"""
    sig_mat: list[np.ndarray] = []
    ret_mat: list[np.ndarray] = []
    for sym, df in panel.items():
        df_a = df.reindex(common_dates)
        sig_mat.append(_factor_1d(df_a, window))
        closes = df_a["close"].to_numpy(dtype=np.float64)
        fwd = np.zeros(len(closes))
        if len(closes) > 5:
            fwd[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
        ret_mat.append(fwd)
    sig = np.column_stack(sig_mat)
    ret = np.column_stack(ret_mat)
    return _reference_ics(sig, ret)


def test_full_pipeline_matches_old_semantics() -> None:
    """完整流水线（预对齐 + 2D 因子 + 向量化 IC）与旧路径逐日 IC 一致。"""
    panel = _make_panel_with_gaps(30, 600, seed=31, drop_ratio=0.10)
    ap = prealign_panel(panel, min_symbols=5)
    window = 20
    ics, _ = compute_cs_ics_vectorized(_factor_2d(ap, window), ap.fwd_returns)
    ref_ics, ref_rows = _old_full_semantics(panel, ap.dates, window)
    _assert_matches_reference(ics, ref_ics, ref_rows)


def test_full_pipeline_multiple_windows() -> None:
    """不同参数窗下完整流水线仍与旧路径一致。"""
    panel = _make_panel_with_gaps(20, 400, seed=32, drop_ratio=0.15)
    ap = prealign_panel(panel, min_symbols=4)
    for window in (5, 40):
        ics, _ = compute_cs_ics_vectorized(_factor_2d(ap, window), ap.fwd_returns)
        ref_ics, ref_rows = _old_full_semantics(panel, ap.dates, window)
        _assert_matches_reference(ics, ref_ics, ref_rows)


# ── 主链路接入对照（evaluation_chain 开关 on/off） ──


def _simple_factor_program() -> dict:
    """极简沙箱因子：信号 = close（随机游走，信号多样）。"""
    return {
        "factor_id": "fct_pv_switch_test",
        "trace_id": "test",
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    return np.asarray(data['close'], dtype=np.float64)\n"
        ),
        "params": {},
    }


def test_cross_section_eval_switch_identical() -> None:
    """接入开关对照：cross_section_evaluate_backtest use_panel_vector on/off 产出逐位一致。

    plans/37 Phase 1 验收：全矩阵化 IC 接入主链路后无语义漂移。
    """
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    panel = _make_panel_with_gaps(30, 600, seed=41, drop_ratio=0.10)
    common = prealign_panel(panel, min_symbols=5).dates
    kw = {"factor": _simple_factor_program(), "panel_data": panel, "common_dates": common, "oos_ratio": 0.3}
    bt_old = cross_section_evaluate_backtest(**kw, use_panel_vector=False)
    bt_new = cross_section_evaluate_backtest(**kw, use_panel_vector=True)

    for key in (
        "ic",
        "icir",
        "t_stat",
        "win_rate",
        "ic_t_stat",
        "sharpe",
        "max_drawdown",
        "sign_flip_rate",
        "turnover_daily",
    ):
        assert bt_old.get(key) == pytest.approx(bt_new.get(key), abs=1e-12, nan_ok=True), f"指标不一致: {key}"
    # 方向翻转路径（多空收益为负时）同样一致
    assert bt_old["sharpe"] * bt_new["sharpe"] >= 0 or bt_old["sharpe"] == bt_new["sharpe"]


def test_cross_section_eval_switch_with_neutralization() -> None:
    """中性化路径下开关 on/off 产出一致（行业中性化后 IC 亦走分派）。"""
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    panel = _make_panel_with_gaps(25, 500, seed=42, drop_ratio=0.10)
    common = prealign_panel(panel, min_symbols=5).dates
    industry_map = {sym: f"G{i % 5}" for i, sym in enumerate(panel)}
    kw = {
        "factor": _simple_factor_program(),
        "panel_data": panel,
        "common_dates": common,
        "oos_ratio": 0.3,
        "industry_map": industry_map,
    }
    bt_old = cross_section_evaluate_backtest(**kw, use_panel_vector=False)
    bt_new = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    assert bt_old["ic"] == pytest.approx(bt_new["ic"], abs=1e-12, nan_ok=True)
    assert bt_old.get("ic_pre_neutral") == pytest.approx(bt_new.get("ic_pre_neutral"), abs=1e-12, nan_ok=True)


# ── 面板化因子执行引擎（plans/37 Phase 2 Step 1） ──


def _make_panel_staggered(n_symbols: int, n_dates: int, seed: int) -> dict[str, pd.DataFrame]:
    """构造首尾缺口面板：每品种在其活跃区间内连续（模拟上市/退市），无内部缺口。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-07-31", periods=n_dates)
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        start = int(rng.integers(0, n_dates // 4))
        end = int(rng.integers(n_dates * 3 // 4, n_dates))
        active = dates[start:end]
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, len(active))))
        panel[f"S{i}"] = pd.DataFrame(
            {
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.lognormal(11.0, 0.5, len(active)),
                "amount": close,
            },
            index=active,
        )
    return panel


def _make_panel_with_forced_gap(n_symbols: int, n_dates: int, seed: int) -> dict[str, pd.DataFrame]:
    """构造含内部缺口的面板：强制最早上市品种（抽样必选）在中间删除一段。"""
    panel = _make_panel_staggered(n_symbols, n_dates, seed)
    earliest = min(panel, key=lambda s: panel[s].index[0])
    df = panel[earliest]
    mid = len(df) // 3
    keep = df.index.delete(list(range(mid, mid + 10)))
    panel[earliest] = df.loc[keep]
    return panel


def _operator_factor(expression: str) -> dict:
    """算子因子：kind=="operator" + expression（code 为可过沙箱校验的桩）。"""
    return {
        "factor_id": "fct_op_test",
        "trace_id": "t",
        "kind": "operator",
        "expression": expression,
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    return np.asarray(data['close'], dtype=np.float64)\n"
        ),
        "params": {},
    }


def _per_symbol_signals(
    expression: str,
    panel: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
) -> dict[str, np.ndarray]:
    """逐品种 DSL evaluate 参考（对齐到共同日期）。"""
    from fts.factor_engine.expr_dsl import build_registry, evaluate, parse_expression

    node = parse_expression(expression)
    reg = build_registry()
    out: dict[str, np.ndarray] = {}
    for sym, df in panel.items():
        arr = np.asarray(evaluate(node, df, reg), dtype=np.float64)
        out[sym] = pd.Series(arr, index=df.index).reindex(common_dates).to_numpy(dtype=np.float64)
    return out


def test_execute_factor_panel_matches_per_symbol() -> None:
    """面板化执行与逐品种 evaluate 逐列一致（首尾缺口面板，无内部缺口）。"""
    panel = _make_panel_staggered(30, 600, seed=51)
    common = prealign_panel(panel, min_symbols=5).dates
    expr = "ts_zscore(close, 10)"
    mat = execute_factor_panel(_operator_factor(expr), panel, common)
    assert mat is not None
    assert mat.shape == (len(common), len(panel))
    ref = _per_symbol_signals(expr, panel, common)
    for j, sym in enumerate(panel):
        assert _arr_equal_nan(mat[:, j], ref[sym]), f"列不一致: {sym}"


def test_execute_factor_panel_unsupported_op_returns_none() -> None:
    """DataFrame 上不可按列的算子（zscore 标量守卫）→ 安全回退 None。"""
    panel = _make_panel_staggered(20, 400, seed=54)
    common = prealign_panel(panel, min_symbols=5).dates
    assert execute_factor_panel(_operator_factor("zscore(close)"), panel, common) is None


def test_execute_factor_panel_internal_gap_falls_back() -> None:
    """内部缺口 → 面板化与逐品种滚动语义不一致 → 安全回退 None。"""
    panel = _make_panel_with_forced_gap(20, 400, seed=55)
    common = prealign_panel(panel, min_symbols=5).dates
    assert execute_factor_panel(_operator_factor("ts_zscore(close, 10)"), panel, common) is None


def test_execute_factor_panel_code_factor_returns_none() -> None:
    """代码因子（kind 非 operator）→ None。"""
    panel = _make_panel_staggered(10, 300, seed=56)
    common = prealign_panel(panel, min_symbols=3).dates
    assert execute_factor_panel(_simple_factor_program(), panel, common) is None


def test_build_forward_return_matrix_matches_spec() -> None:
    """前向收益矩阵：逐品种先算后对齐（与 evaluation_chain._cs_execute_factors 同口径）。"""
    panel = _make_panel_staggered(15, 300, seed=57)
    common = prealign_panel(panel, min_symbols=3).dates
    mat = build_forward_return_matrix(panel, common, forward_days=5)
    assert mat.shape == (len(common), len(panel))
    for j, sym in enumerate(panel):
        closes = panel[sym]["close"].to_numpy(dtype=np.float64)
        fwd = np.zeros(len(closes))
        if len(closes) > 5:
            fwd[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
        expected = pd.Series(fwd, index=panel[sym].index).reindex(common).to_numpy(dtype=np.float64)
        assert _arr_equal_nan(mat[:, j], expected), f"列不一致: {sym}"


def test_cross_section_eval_operator_switch_identical() -> None:
    """算子因子接入对照：use_panel_vector on/off 评估产出一致（信号恒逐品种，仅 IC 矩阵化）。"""
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    panel = _make_panel_staggered(30, 600, seed=52)
    common = prealign_panel(panel, min_symbols=5).dates
    factor = _operator_factor("ts_zscore(close, 10)")
    kw = {"factor": factor, "panel_data": panel, "common_dates": common, "oos_ratio": 0.3}
    bt_old = cross_section_evaluate_backtest(**kw, use_panel_vector=False)
    bt_new = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    for key in ("ic", "icir", "t_stat", "win_rate", "ic_t_stat", "sharpe", "max_drawdown", "sign_flip_rate"):
        assert bt_old.get(key) == pytest.approx(bt_new.get(key), abs=1e-12, nan_ok=True), f"指标不一致: {key}"


def test_default_panel_vector_enabled(monkeypatch) -> None:
    """Phase 3（plans/37）：默认（未配置）即开启 panel_vector —— 零漂移回归验证。

    清空 FTS_CROSS_SECTION_PANEL_VECTOR 环境变量 + 重置全局配置单例后，
    cross_section_panel_vector 默认应为 True；且不显式传 use_panel_vector 的
    默认调用产出与显式 True 逐位一致（保证切换默认后行为可预期）。
    """
    import fts.config.settings as cfg_mod
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    monkeypatch.delenv("FTS_CROSS_SECTION_PANEL_VECTOR", raising=False)
    monkeypatch.setattr(cfg_mod, "_default_config", None)
    assert cfg_mod.get_config().cross_section_panel_vector is True

    panel = _make_panel_with_gaps(30, 600, seed=71, drop_ratio=0.10)
    common = prealign_panel(panel, min_symbols=5).dates
    factor = _operator_factor("ts_zscore(close, 10)")
    kw = {"factor": factor, "panel_data": panel, "common_dates": common, "oos_ratio": 0.3}
    bt_default = cross_section_evaluate_backtest(**kw)
    bt_on = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    for key in ("ic", "icir", "t_stat", "sharpe", "win_rate"):
        assert bt_default.get(key) == pytest.approx(bt_on.get(key), abs=1e-12, nan_ok=True), f"默认与 on 不一致: {key}"


def test_chain_operator_panel_fallback_per_symbol() -> None:
    """plans/39 §11 回退契约：评估链信号构建不调用 execute_factor_panel。

    真实缺口面板算子因子面板化实测 0.3x（<5x 门槛）登记豁免摘除——信号恒逐
    品种执行，仅 IC 计算走全矩阵化。本测试钉死该契约：将 execute_factor_panel
    打桩为抛错（若被调用必然失败），评估链 use_panel_vector=True 仍正常产出
    且与基准逐位一致。
    """
    from unittest import mock

    import fts.factor_engine.panel_vector as pv
    from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

    panel = _make_panel_with_gaps(30, 600, seed=77, drop_ratio=0.10)
    common = prealign_panel(panel, min_symbols=5).dates
    factor = _operator_factor("ts_zscore(close, 10)")
    kw = {"factor": factor, "panel_data": panel, "common_dates": common, "oos_ratio": 0.3}
    bt_ref = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    with mock.patch.object(
        pv, "execute_factor_panel", side_effect=AssertionError("plans/39 §11 已摘除面板化，不应被调用")
    ):
        bt = cross_section_evaluate_backtest(**kw, use_panel_vector=True)
    for key in ("ic", "icir", "t_stat", "win_rate", "sharpe", "max_drawdown"):
        assert bt.get(key) == pytest.approx(bt_ref.get(key), abs=1e-12, nan_ok=True), f"面板化摘除后指标不一致: {key}"
