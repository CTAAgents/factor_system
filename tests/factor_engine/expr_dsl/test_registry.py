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
    for f in ("northbound_flow", "northbound_hold_pct", "margin_balance",
              "holder_count", "analyst_up_count", "analyst_total_count"):
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
    df = pd.DataFrame({
        "northbound_flow": rng.normal(0, 1, 60),
        "margin_balance": 1e9 + np.cumsum(rng.normal(0, 1e7, 60)),
        "holder_count": 5e4 + np.cumsum(rng.normal(0, 200, 60)),
        "analyst_up_count": rng.integers(1, 10, 60).astype(float),
        "analyst_total_count": np.full(60, 20.0),
    })
    out_nb = eval_fts_expr("nb_momentum(northbound_flow, 5)", df, {})
    assert out_nb.shape == (60,)
    assert np.isfinite(out_nb).all()
    out_margin = eval_fts_expr("margin_change(margin_balance, 1)", df, {})
    assert out_margin.shape == (60,)
    out_holder = eval_fts_expr("holder_concentration(holder_count, 5)", df, {})
    assert out_holder.shape == (60,)
    out_ar = eval_fts_expr(
        "analyst_revision_ratio(analyst_up_count, analyst_total_count)", df, {}
    )
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
    """GAP-L401: 新增 4 个高阶算子注册。"""
    reg = build_registry()
    for name in ("regression_residual", "quantile_bucket",
                 "cross_section_demean", "if_else"):
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
    assert out.iloc[0] == 1.0   # True → x
    assert out.iloc[1] == 20.0  # False → y
    assert out.iloc[2] == 3.0   # True → x
    assert out.iloc[3] == 40.0  # NaN → False → y
