"""tests/factor_engine/test_contracts_normalize.py — 因子契约规范化函数测试。

覆盖:
    1. normalize_factor_signature: 新版/旧版/空/非列表字段
    2. _map_output_type / detect_factor_market
    3. normalize_factor_program: signature/market/family/symbols/kind 补全全路径
    4. _infer_factor_family: 全部家族推断分支
    5. _get_evolution_version / EVOLUTION_VERSION
"""

from __future__ import annotations

import sys
from pathlib import Path


_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import fts  # noqa: E402
from fts.factor_engine.contracts import (  # noqa: E402
    EVOLUTION_VERSION,
    FactorKind,
    _get_evolution_version,
    _infer_factor_family,
    _map_output_type,
    detect_factor_market,
    normalize_factor_program,
    normalize_factor_signature,
)


# ─── normalize_factor_signature ────────────────────────────


class TestNormalizeSignature:
    def test_empty_returns_defaults(self):
        sig = normalize_factor_signature({})
        assert sig["input_fields"] == ["close"]
        assert sig["output_type"] == "signal"
        assert sig["frequency"] == "daily"
        assert sig["lookback"] == 10

    def test_new_format_passthrough(self):
        sig = normalize_factor_signature(
            {
                "input_fields": ["close", "volume"],
                "output_type": "score",
                "frequency": "1h",
                "lookback": 5,
            }
        )
        assert sig["input_fields"] == ["close", "volume"]
        assert sig["output_type"] == "score"
        assert sig["frequency"] == "1h"
        assert sig["lookback"] == 5

    def test_legacy_format_mapping(self):
        sig = normalize_factor_signature({"inputs": ["close"], "outputs": ["signal"], "feature_dim": 1})
        assert sig["input_fields"] == ["close"]
        assert sig["output_type"] == "signal"

    def test_legacy_outputs_without_signal_maps_score(self):
        sig = normalize_factor_signature({"inputs": ["close"], "outputs": ["return"]})
        assert sig["output_type"] == "score"

    def test_input_fields_scalar_wrapped(self):
        sig = normalize_factor_signature({"input_fields": "close"})
        assert sig["input_fields"] == ["close"]


# ─── _map_output_type / detect_factor_market ───────────────


class TestMapOutputType:
    def test_signal_in_outputs(self):
        assert _map_output_type(["signal", "return"]) == "signal"

    def test_no_signal_returns_score(self):
        assert _map_output_type(["return"]) == "score"
        assert _map_output_type([]) == "score"


class TestDetectFactorMarket:
    def test_valid_hint_passthrough(self):
        assert detect_factor_market(None, "futures") == "futures"
        assert detect_factor_market(None, "stock") == "stock"
        assert detect_factor_market(None, "etf") == "etf"
        assert detect_factor_market(None, "bond") == "bond"
        assert detect_factor_market(None, "multi") == "multi"

    def test_invalid_hint_ignored(self):
        assert detect_factor_market(None, "crypto") == "multi"  # 无 symbols → multi

    def test_no_symbols_returns_multi(self):
        assert detect_factor_market(None, None) == "multi"
        assert detect_factor_market([], None) == "multi"

    def test_pure_futures(self):
        assert detect_factor_market(["RB0", "CU0"], None) == "futures"

    def test_pure_stock(self):
        assert detect_factor_market(["600519", "000001"], None) == "stock"

    def test_mixed_returns_multi(self):
        assert detect_factor_market(["RB0", "600519"], None) == "multi"

    def test_unknown_symbols_returns_multi(self):
        assert detect_factor_market(["XYZ1"], None) == "multi"


# ─── normalize_factor_program ──────────────────────────────


class TestNormalizeFactorProgram:
    def test_signature_dict_normalized(self):
        f = normalize_factor_program({"signature": {"inputs": ["close"], "outputs": ["signal"]}})
        assert f["signature"]["input_fields"] == ["close"]
        assert f["signature"]["output_type"] == "signal"

    def test_signature_missing_gets_default(self):
        f = normalize_factor_program({"name": "x"})
        assert f["signature"]["lookback"] == 10

    def test_market_filled_from_hint(self):
        f = normalize_factor_program({"name": "x"}, market_hint="stock")
        assert f["market"] == "stock"

    def test_market_filled_from_symbols(self):
        f = normalize_factor_program({"name": "x", "symbols": ["RB0"]})
        assert f["market"] == "futures"

    def test_market_missing_without_hint(self):
        f = normalize_factor_program({"name": "x"})
        assert f["market"] == "multi"

    def test_family_standard_kept(self):
        f = normalize_factor_program({"name": "x", "family": "trend"})
        assert f["family"] == "trend"

    def test_family_nonstandard_reinferred(self):
        f = normalize_factor_program({"name": "momentum_alpha"})
        assert f["family"] == "trend"

    def test_symbols_filled_empty(self):
        f = normalize_factor_program({"name": "x"})
        assert f["symbols"] == []

    def test_factor_version_filled(self):
        f = normalize_factor_program({"name": "x"})
        assert f["factor_version"] == "v2"

    def test_is_multi_symbol_flags(self):
        assert normalize_factor_program({"name": "x", "symbols": ["A", "B"]})["is_multi_symbol"] is True
        assert normalize_factor_program({"name": "x", "symbols": ["A"]})["is_multi_symbol"] is False

    def test_kind_inferred_operator(self):
        f = normalize_factor_program({"name": "x", "expression": "ts_mean(close, 5)"})
        assert f["kind"] == FactorKind.OPERATOR

    def test_kind_inferred_code(self):
        f = normalize_factor_program({"name": "x"})
        assert f["kind"] == FactorKind.CODE

    def test_kind_existing_kept(self):
        f = normalize_factor_program({"name": "x", "kind": FactorKind.CODE})
        assert f["kind"] == FactorKind.CODE

    def test_returns_new_dict(self):
        original = {"name": "x"}
        normalized = normalize_factor_program(original)
        assert normalized is not original


# ─── _infer_factor_family ──────────────────────────────────


class TestInferFactorFamily:
    def test_trend_keywords(self):
        for name in ("trend_follow", "momentum_5d", "breakout_20", "follow_trend"):
            assert _infer_factor_family({"name": name}) == "trend"

    def test_mean_reversion_keywords(self):
        for name in ("mean_reversion", "reversion_5", "regression_alpha", "bounce_ratio"):
            assert _infer_factor_family({"name": name}) == "mean_reversion"

    def test_carry_keywords(self):
        for name in ("carry_ratio", "spread_3m", "arbitrage_zz", "basis_change"):
            assert _infer_factor_family({"name": name}) == "carry"

    def test_volume_keywords(self):
        for name in ("volume_ratio", "money_flow", "capital_inflow"):
            assert _infer_factor_family({"name": name}) == "volume"

    def test_volatility_keywords(self):
        for name in ("volatility_20", "garch_fit", "variance_ratio"):
            assert _infer_factor_family({"name": name}) == "volatility"

    def test_fundamental_keywords(self):
        for name in ("fundamental_pe", "value_factor", "quality_score", "growth_rate"):
            assert _infer_factor_family({"name": name}) == "fundamental"

    def test_liquidity_keywords(self):
        for name in ("liquidity_amihud", "liquid_ratio", "depth_5"):
            assert _infer_factor_family({"name": name}) == "liquidity"

    def test_library_prefixes(self):
        assert _infer_factor_family({"name": "qlib_alpha1"}) == "qlib"
        assert _infer_factor_family({"name": "gtja_191"}) == "gtja"
        assert _infer_factor_family({"name": "alpha_001"}) == "wq101"
        assert _infer_factor_family({"name": "wq_alpha"}) == "wq101"
        assert _infer_factor_family({"name": "fut_trend"}) == "trend"

    def test_microstructure_from_code(self):
        assert _infer_factor_family({"name": "x", "code": "open_interest_chg"}) == "microstructure"
        assert _infer_factor_family({"name": "x", "code": "order_flow"}) == "microstructure"

    def test_macro_from_input_fields(self):
        factor = {"name": "x", "signature": {"input_fields": ["macro_gdp"]}}
        assert _infer_factor_family(factor) == "macro"

    def test_unknown_returns_other(self):
        assert _infer_factor_family({"name": "zzz_unknown"}) == "other"


# ─── 版本号 ────────────────────────────────────────────────


class TestEvolutionVersion:
    def test_evolution_version_matches_fts(self):
        assert EVOLUTION_VERSION == fts.__version__

    def test_get_version_from_modules(self):
        assert _get_evolution_version() == fts.__version__
