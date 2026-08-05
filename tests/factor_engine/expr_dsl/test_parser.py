"""FTS-Expr DSL 解析器测试。"""
import pytest

from fts.factor_engine.expr_dsl.ast import ExprNode
from fts.factor_engine.expr_dsl.parser import FTSExprError, parse_expression


def test_parse_field():
    node = parse_expression("close")
    assert node.kind == "field" and node.op == "close" and node.args == []


def test_parse_simple_call():
    node = parse_expression("rank(close)")
    assert node.kind == "op" and node.op == "rank"
    assert len(node.args) == 1
    assert node.args[0].op == "close"


def test_parse_nested_call():
    node = parse_expression("rank(ts_zscore(close, 60))")
    inner = node.args[0]
    assert inner.op == "ts_zscore"
    assert inner.args[0].op == "close"
    assert inner.args[1].kind == "const" and inner.args[1].op == "60"


def test_parse_int_const():
    node = parse_expression("ts_mean(close, 20)")
    const = node.args[1]
    assert const.kind == "const" and const.op == "20"


def test_parse_float_const():
    node = parse_expression("mul(close, 2.0)")
    assert node.args[1].kind == "const" and node.args[1].op == "2.0"


def test_parse_negative_const():
    node = parse_expression("mul(close, -1)")
    assert node.args[1].kind == "const" and node.args[1].op == "-1"


def test_parse_whitespace():
    node = parse_expression("  rank(  ts_zscore( close , 60 ) )  ")
    assert node.op == "rank"


def test_parse_error_unbalanced():
    with pytest.raises(FTSExprError):
        parse_expression("rank(close")


def test_parse_error_trailing_junk():
    with pytest.raises(FTSExprError):
        parse_expression("rank(close) junk")


def test_parse_error_missing_ident():
    with pytest.raises(FTSExprError):
        parse_expression("(close)")
