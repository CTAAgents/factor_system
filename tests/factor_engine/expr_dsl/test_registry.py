"""FTS-Expr 算子注册表测试。"""

import numpy as np
import pandas as pd

from fts.factor_engine.expr_dsl.registry import (
    A_SHARE_FIELDS,
    L0_FIELDS,
    build_registry,
    verify_registry_consistency,
)
from fts.factor_engine.expr_dsl.runtime import eval_fts_expr


def test_registry_has_operator_count():
    reg = build_registry()
    assert len(reg) >= 40


def test_registry_has_all_categories():
    reg = build_registry()
    cats = {m.category for m in reg.values()}
    assert {"L0", "L1", "L2", "L3", "L4", "L5"} <= cats


def test_registry_metadata_complete():
    reg = build_registry()
    meta = reg["ts_mean"]
    assert meta.category == "L1"
    assert meta.params == ("x", "n")
    assert "n" in meta.int_params
    assert meta.param_bounds["n"] == (2, 250)
    assert meta.lookback_param == "n"
    assert meta.economic_meaning


def test_registry_l0_fields():
    assert "close" in L0_FIELDS and "volume" in L0_FIELDS


def test_ts_mean_func_works_on_series():
    reg = build_registry()
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = reg["ts_mean"].func(s, 3)
    assert list(out.iloc[2:]) == [2.0, 3.0, 4.0]


# ─── GAP-S12: A 股特有算子 ──────────────────────────────────


def test_registry_has_a_share_fields():
    """A 股特有数据字段应注册为 L0 字段访问器。"""
    reg = build_registry()
    for f in (
        "northbound_flow",
        "northbound_hold_pct",
        "margin_balance",
        "holder_count",
        "analyst_up_count",
        "analyst_total_count",
    ):
        assert f in A_SHARE_FIELDS
        assert f in reg
        assert reg[f].category == "L0"
        assert reg[f].economic_meaning  # 经济语义标签非空


def test_registry_has_a_share_domain_ops():
    """A 股特有领域算子注册 + 元数据完整。"""
    reg = build_registry()
    for name, params, lb_param in (
        ("nb_momentum", ("x", "n"), "n"),
        ("margin_change", ("x", "n"), "n"),
        ("holder_concentration", ("x", "n"), "n"),
        ("analyst_revision_ratio", ("up", "total"), None),
    ):
        meta = reg[name]
        assert meta.category == "L5"
        assert meta.params == params
        assert meta.economic_meaning
        if lb_param is not None:
            assert meta.lookback_param == lb_param
            assert meta.param_bounds[lb_param] == (2, 250) or lb_param == "n"


def test_a_share_domain_op_evaluation():
    """A 股领域算子可在 DSL 中执行（合成数据）。"""
    rng = np.random.default_rng(7)
    df = pd.DataFrame(
        {
            "northbound_flow": rng.normal(0, 1, 60),
            "margin_balance": 1e9 + np.cumsum(rng.normal(0, 1e7, 60)),
            "holder_count": 5e4 + np.cumsum(rng.normal(0, 200, 60)),
            "analyst_up_count": rng.integers(1, 10, 60).astype(float),
            "analyst_total_count": np.full(60, 20.0),
        }
    )
    out_nb = eval_fts_expr("nb_momentum(northbound_flow, 5)", df, {})
    assert out_nb.shape == (60,)
    assert np.isfinite(out_nb).all()
    out_margin = eval_fts_expr("margin_change(margin_balance, 1)", df, {})
    assert out_margin.shape == (60,)
    out_holder = eval_fts_expr("holder_concentration(holder_count, 5)", df, {})
    assert out_holder.shape == (60,)
    out_ar = eval_fts_expr("analyst_revision_ratio(analyst_up_count, analyst_total_count)", df, {})
    assert out_ar.shape == (60,)
    assert float(np.nanmax(out_ar)) <= 1.0


# ─── GAP-S10: 双注册表一致性校验 ─────────────────────────────


def test_registry_consistency_no_mismatch():
    """expr_dsl 与 feature_ops.OperatorRegistry 重叠算子输出一致。"""
    report = verify_registry_consistency()
    assert report["consistent"], f"双注册表算子漂移: {report['mismatched'] + report['errors']}"
    # 重叠算子必须存在（验证校验确实覆盖到共享核心）
    assert report["overlapping"] >= 10


# ─── GAP-L401 高阶算子（v2.66.0） ────────────────────────


def test_registry_has_new_l4_operators():
    """GAP-L401: 新增 6 个高阶算子注册（4 + corr/cross_section_rank）。"""
    reg = build_registry()
    for name in (
        "regression_residual",
        "quantile_bucket",
        "cross_section_demean",
        "if_else",
        "corr",
        "cross_section_rank",
    ):
        assert name in reg
        assert reg[name].category == "L4"
        assert reg[name].economic_meaning


def test_regression_residual_func():
    """regression_residual: y = 2x + noise → 残差去 beta 后与 x 无关。"""
    import numpy as np

    reg = build_registry()
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(size=100))
    y = pd.Series(2.0 * x.values + rng.normal(0.0, 0.1, size=100))
    resid = reg["regression_residual"].func(y, x, 20)
    valid = resid.dropna()
    assert len(valid) >= 50  # 至少 20 窗口后生效
    # 残差与 x 的相关性应显著低于 y 与 x 的相关性
    corr_yx = y.corr(x)
    corr_resid_x = valid.corr(x.reindex(valid.index))
    assert corr_resid_x < corr_yx


def test_quantile_bucket_func():
    """quantile_bucket: 均匀序列 → 分为 n 个桶。"""
    import numpy as np

    reg = build_registry()
    s = pd.Series(np.linspace(0.0, 1.0, 100))
    buckets = reg["quantile_bucket"].func(s, 4)
    assert buckets.dropna().nunique() <= 4
    assert buckets.min() >= 0


def test_cross_section_demean_func():
    """cross_section_demean: 去均值后均值为 0。"""
    import numpy as np

    reg = build_registry()
    s = pd.Series(np.array([1.0, 2.0, 3.0, 10.0]))
    out = reg["cross_section_demean"].func(s)
    assert abs(out.mean()) < 1e-10


def test_if_else_func():
    """if_else: 条件 True 取 x，False 取 y（NaN 安全）。"""
    import numpy as np

    reg = build_registry()
    cond = pd.Series([True, False, True, np.nan])
    x = pd.Series([1.0, 2.0, 3.0, 4.0])
    y = pd.Series([10.0, 20.0, 30.0, 40.0])
    out = reg["if_else"].func(cond, x, y)
    assert out.iloc[0] == 1.0  # True → x
    assert out.iloc[1] == 20.0  # False → y
    assert out.iloc[2] == 3.0  # True → x
    assert out.iloc[3] == 40.0  # NaN → False → y


def test_corr_metadata():
    """corr: 双序列窗口相关，元数据完整（int 窗口/边界/PIT lookback）。"""
    reg = build_registry()
    meta = reg["corr"]
    assert meta.category == "L4"
    assert meta.params == ("x", "y", "n")
    assert "n" in meta.int_params
    assert meta.param_bounds["n"] == (2, 250)
    assert meta.lookback_param == "n"
    assert meta.economic_meaning


def test_corr_func():
    """corr: 强正相关双序列 → 滚动相关接近 1；窗口不足为 NaN。"""
    reg = build_registry()
    rng = np.random.default_rng(3)
    x = pd.Series(rng.normal(size=120))
    y = pd.Series(2.0 * x.values + rng.normal(0.0, 0.01, size=120))
    out = reg["corr"].func(x, y, 20)
    valid = out.dropna()
    assert len(valid) >= 90  # 20 窗口后生效
    assert abs(float(valid.mean())) > 0.9  # 高相关
    assert out.iloc[:19].isna().all()  # 前 window-1 期 NaN


def test_cross_section_rank_metadata():
    """cross_section_rank: 横截面排名元数据完整。"""
    reg = build_registry()
    meta = reg["cross_section_rank"]
    assert meta.category == "L4"
    assert meta.params == ("x",)
    assert meta.economic_meaning


def test_cross_section_rank_func():
    """cross_section_rank: 0-1 归一化排名，单调保持，NaN 透传。"""
    reg = build_registry()
    s = pd.Series([3.0, 1.0, np.nan, 2.0, 5.0, 4.0])
    out = reg["cross_section_rank"].func(s)
    assert out.isna().sum() == 1  # NaN 透传
    valid = out.dropna()
    assert valid.min() >= 0.0 and valid.max() <= 1.0  # 0-1 归一化
    assert float(valid.max()) == 1.0  # 最大值排名 1.0
    # 单调保持：原值大的排名 ≥ 原值小的排名
    assert out.iloc[4] >= out.iloc[0] >= out.iloc[1]


# ─── GAP-I202 组合/跨标的算子单一事实源（v2.75.0） ───────────────


def test_registry_has_ts_slope_ts_quantile():
    """GAP-I202: ts_slope/ts_quantile 注册 + 元数据（L1/参数边界/经济语义）。"""
    reg = build_registry()
    meta = reg["ts_slope"]
    assert meta.category == "L1"
    assert meta.params == ("x", "n")
    assert "n" in meta.int_params
    assert meta.param_bounds["n"] == (2, 250)
    assert meta.lookback_param == "n"
    assert meta.economic_meaning

    meta_q = reg["ts_quantile"]
    assert meta_q.category == "L1"
    assert meta_q.params == ("x", "n", "q")
    assert "q" in meta_q.float_params
    assert meta_q.param_bounds["q"] == (0.0, 1.0)
    assert meta_q.lookback_param == "n"
    assert meta_q.economic_meaning


def test_ts_slope_func():
    """ts_slope: 单调上升序列 → 正斜率；下降序列 → 负斜率。"""
    reg = build_registry()
    n = 60
    up = pd.Series(np.linspace(0.0, 10.0, n))
    down = pd.Series(np.linspace(10.0, 0.0, n))
    slope_up = reg["ts_slope"].func(up, 10)
    slope_down = reg["ts_slope"].func(down, 10)
    assert slope_up.iloc[:9].isna().all()  # 前 window-1 期 NaN
    valid_up = slope_up.dropna()
    valid_down = slope_down.dropna()
    assert len(valid_up) >= n - 10
    assert float(valid_up.mean()) > 0.0  # 上升 → 正斜率
    assert float(valid_down.mean()) < 0.0  # 下降 → 负斜率


def test_ts_quantile_func():
    """ts_quantile: 滚动中位数（q=0.5）介于窗口 min/max 之间；q 越界抛错。"""
    reg = build_registry()
    s = pd.Series(np.linspace(0.0, 1.0, 40))
    out = reg["ts_quantile"].func(s, 8, 0.5)
    assert out.iloc[:7].isna().all()
    valid = out.dropna()
    assert len(valid) >= 30
    assert float(valid.min()) >= 0.0 and float(valid.max()) <= 1.0
    import pytest

    with pytest.raises(ValueError, match="q 必须在"):
        reg["ts_quantile"].func(s, 8, 1.5)


def test_feature_ops_registry_has_combo_ops():
    """GAP-I202: feature_ops.OperatorRegistry（GP 侧）含组合/跨标的算子。"""
    from fts.factor_engine.feature_ops import OperatorRegistry

    gp_reg = OperatorRegistry()
    names = {op.name for op in gp_reg.list_operators()}
    for name in (
        "ts_slope",
        "ts_quantile",
        "regression_residual",
        "quantile_bucket",
        "cross_section_demean",
        "if_else",
        "corr",
        "cross_section_rank",
    ):
        assert name in names, f"GP 注册表缺算子: {name}"


def test_gp_registry_call_new_ops():
    """GAP-I202: GP 注册表可调用新算子（单一事实源可执行）。"""
    from fts.factor_engine.feature_ops import OperatorRegistry

    gp_reg = OperatorRegistry()
    rng = np.random.default_rng(11)
    x = pd.Series(rng.normal(0, 1, 80))
    y = pd.Series(2.0 * x.values + rng.normal(0, 0.1, 80))
    slope = gp_reg.call("ts_slope", x, 10)
    assert slope.shape == (80,)
    assert slope.dropna().shape[0] >= 60
    resid = gp_reg.call("regression_residual", y, x, 10)
    assert resid.dropna().shape[0] >= 60
    corr = gp_reg.call("corr", x, y, 10)
    assert float(corr.dropna().abs().mean()) > 0.9


def test_registry_consistency_required_shared():
    """GAP-I202: 组合/跨标的算子强制双注册表共享（unshared_required 为空）。"""
    report = verify_registry_consistency()
    assert report["unshared_required"] == [], f"组合/跨标的算子未共享: {report['unshared_required']}"
    assert report["consistent"]
    # 新算子已进入重叠集合（不再 only_dsl）
    for name in (
        "ts_slope",
        "ts_quantile",
        "regression_residual",
        "quantile_bucket",
        "cross_section_demean",
        "if_else",
        "corr",
        "cross_section_rank",
    ):
        assert name in report["matched"], f"{name} 未通过一致性匹配"


def test_ts_slope_dsl_evaluation():
    """GAP-I202: ts_slope 可在 DSL 表达式中执行。"""
    df = pd.DataFrame({"close": np.linspace(1.0, 2.0, 60)})
    out = eval_fts_expr("ts_slope(close, 10)", df, {})
    out = np.asarray(out, dtype=float)
    assert out.shape == (60,)
    valid = out[~np.isnan(out)]
    assert len(valid) >= 45
    assert float(valid.mean()) > 0.0
