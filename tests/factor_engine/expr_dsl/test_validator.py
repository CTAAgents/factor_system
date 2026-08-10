"""FTS-Expr 校验器与 PIT 静态分析测试。"""

import pytest

from fts.factor_engine.expr_dsl.parser import parse_expression
from fts.factor_engine.expr_dsl.registry import build_registry
from fts.factor_engine.expr_dsl.validator import (
    DSLValidationError,
    collect_fields,
    validate_expr,
)

REG = build_registry()


def test_validate_valid_expr():
    node = parse_expression("rank(ts_zscore(close, 60))")
    errors, max_lb = validate_expr(node, REG)
    assert errors == []
    assert max_lb == 60


def test_validate_unknown_operator():
    node = parse_expression("foo(close)")
    errors, _ = validate_expr(node, REG)
    assert any("未知算子" in e for e in errors)


def test_validate_unknown_field():
    node = parse_expression("ts_mean(volume_extra, 20)")
    errors, _ = validate_expr(node, REG)
    assert any("未知字段" in e for e in errors)


def test_validate_wrong_arity():
    # 注: 原计划用 ts_mean(close) — 解析器会因缺 ')' 抛 FTSExprError,
    # 改用 ts_mean(close, 1, 2)（语法合法、参数个数错误）使断言落在校验器 arity 检查上
    node = parse_expression("ts_mean(close, 1, 2)")
    errors, _ = validate_expr(node, REG)
    assert any("期望 2 个参数" in e for e in errors)


def test_validate_param_bounds():
    node = parse_expression("ts_mean(close, 999)")
    errors, _ = validate_expr(node, REG)
    assert any("越界" in e for e in errors)


def test_max_lookback_nested_takes_max():
    node = parse_expression("add(ts_mean(close, 20), ts_std(close, 60))")
    _, max_lb = validate_expr(node, REG)
    assert max_lb == 60


def test_max_lookback_dynamic_uses_upper_bound():
    # lookback 参数为算子输出时无法静态确定 → 取边界上限 250
    node = parse_expression("ts_mean(close, ts_mean(volume, 5))")
    _, max_lb = validate_expr(node, REG)
    assert max_lb == 250


def test_collect_fields():
    node = parse_expression("rank(ts_zscore(close, 60))")
    assert collect_fields(node) == {"close"}


def test_validate_raises_utility():
    from fts.factor_engine.expr_dsl.parser import FTSExprError

    with pytest.raises(DSLValidationError):
        raise DSLValidationError("x")
    assert issubclass(DSLValidationError, FTSExprError)
