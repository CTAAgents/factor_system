"""FTS-Expr 算子注册表测试。"""
import pandas as pd

from fts.factor_engine.expr_dsl.registry import L0_FIELDS, build_registry


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
