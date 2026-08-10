"""算子演化基础层 — FactorKind 与契约扩展测试。"""

from fts.factor_engine.contracts import FactorKind, _infer_factor_family, normalize_factor_program


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


# ─── 因子库来源子家族推断（qlib / gtja / wq101）─────────────


def test_infer_family_prefix_qlib():
    assert _infer_factor_family({"name": "qlib_001", "code": "x"}) == "qlib"


def test_infer_family_prefix_gtja():
    assert _infer_factor_family({"name": "gtja_001", "code": "x"}) == "gtja"


def test_infer_family_prefix_alpha_maps_to_wq101():
    assert _infer_factor_family({"name": "alpha_001", "code": "x"}) == "wq101"


def test_infer_family_prefix_wq_maps_to_wq101():
    assert _infer_factor_family({"name": "wq_alpha9", "code": "x"}) == "wq101"


def test_infer_family_prefix_fut_keeps_trend():
    assert _infer_factor_family({"name": "fut_tsmom", "code": "x"}) == "trend"


def test_infer_family_cross_section_falls_back_to_other():
    # 非库来源前缀的横截面因子不应误归入子家族
    assert _infer_factor_family({"name": "cross_rank", "code": "x"}) == "other"


def test_normalize_accepts_new_family_values():
    for fam in ("qlib", "gtja", "wq101"):
        factor = normalize_factor_program({"name": "x", "code": "y", "family": fam})
        assert factor["family"] == fam


def test_normalize_reinfers_legacy_yaml_family():
    # 旧 YAML 的 qlib158/gtja191 为非标准值，应按名称前缀重新推断
    factor = normalize_factor_program({"name": "qlib_010", "code": "y", "family": "qlib158"})
    assert factor["family"] == "qlib"
    factor = normalize_factor_program({"name": "gtja_010", "code": "y", "family": "gtja191"})
    assert factor["family"] == "gtja"
