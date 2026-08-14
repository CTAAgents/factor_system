"""算子演化基础层 — FactorKind 与契约扩展测试。"""

from fts.factor_engine.contracts import FactorKind, normalize_factor_program


def test_factor_kind_members():
    assert FactorKind.OPERATOR.value == "operator"
    assert FactorKind.CODE.value == "code"
    assert FactorKind.HYBRID.value == "hybrid"


def test_legacy_factor_defaults_to_code():
    factor = normalize_factor_program(
        {
            "name": "legacy",
            "code": "def factor_program(data, params): return data['close']",
        }
    )
    assert factor["kind"] == FactorKind.CODE


def test_expression_factor_inferred_as_operator():
    factor = normalize_factor_program(
        {
            "name": "op",
            "code": "x",
            "expression": "rank(close)",
        }
    )
    assert factor["kind"] == FactorKind.OPERATOR


def test_new_fields_are_optional():
    factor = normalize_factor_program({"name": "legacy", "code": "x"})
    # 可选字段缺失时不应抛错
    assert factor.get("max_lookback") is None
