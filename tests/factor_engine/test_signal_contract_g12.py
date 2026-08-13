"""G12 信号输出统一契约测试（plans/35 §5.5，v2.103.0+15）。

覆盖：
- to_lots：资金占比→手数（四舍五入 / 资金不足→0 / 超上限截断）
- signal_map_to_factor_signal：int/str 方向映射、score/regime/risk_usage 透传、
  target_lots 计算与 delta 一致性
- SignalValidator：新字段（target_lots/current_lots/delta_lots/score/regime/risk_usage）
  接受与拒绝
- MHF 管道组装：样例 signal_map → FactorSignal → validator 零错误
"""

from __future__ import annotations

from fts.factor_engine.signal_contract import (
    SignalValidator,
    signal_map_to_factor_signal,
    to_lots,
)

_SAMPLE_MAP = {
    "RB0": {"direction": 1, "score": 0.85, "last_close": 3500.0, "position": 0.1},
    "CU0": {"direction": -1, "score": -0.72, "last_close": 70000.0, "position": 0.05},
    "MA0": {"direction": 0, "score": 0.02, "last_close": 2500.0, "position": 0.0},
}


class TestToLots:
    """to_lots 边界。"""

    def test_rounding(self):
        # 0.1 × 1_000_000 / (3500 × 10) = 2.857 → round → 3
        assert to_lots(0.1, 1_000_000, 3500.0, 10) == 3

    def test_insufficient_funds_zero(self):
        # 0.0001 × 1_000 / (3500 × 10) < 0.5 → 0 手
        assert to_lots(0.0001, 1000, 3500.0, 10) == 0

    def test_cap_truncation(self):
        assert to_lots(0.5, 1_000_000, 1000.0, 10, max_lots=5) == 5

    def test_zero_inputs(self):
        assert to_lots(0.0, 1_000_000, 3500.0, 10) == 0
        assert to_lots(0.1, 0, 3500.0, 10) == 0
        assert to_lots(0.1, 1_000_000, 0, 10) == 0
        assert to_lots(0.1, 1_000_000, 3500.0, 0) == 0


class TestSignalMapToFactorSignal:
    """{symbol: {...}} → FactorSignal 转换。"""

    def test_direction_and_fields_carried(self):
        fs = signal_map_to_factor_signal(
            dict(_SAMPLE_MAP),
            signal_id="s-g12-1",
            frequency="30m",
            regime="high_vol",
        )
        assert fs["signal_id"] == "s-g12-1"
        assert fs["frequency"] == "30m"
        assert fs["meta"]["regime"] == "high_vol"
        by_sym = {s["symbol"]: s for s in fs["signals"]}
        assert by_sym["RB0"]["direction"] == "long"
        assert by_sym["CU0"]["direction"] == "short"
        assert by_sym["MA0"]["direction"] == "flat"
        assert by_sym["RB0"]["score"] == 0.85
        assert by_sym["RB0"]["regime"] == "high_vol"
        assert by_sym["MA0"]["position"] == 0.0

    def test_str_direction_passthrough(self):
        fs = signal_map_to_factor_signal({"RB0": {"direction": "long"}})
        assert fs["signals"][0]["direction"] == "long"

    def test_target_lots_computed_and_delta_consistent(self):
        fs = signal_map_to_factor_signal(
            {"RB0": {"direction": 1, "price": 3500.0, "position": 0.1, "current_lots": 1}},
            equity=1_000_000,
            price_multiplier=10,
        )
        sig = fs["signals"][0]
        assert sig["target_lots"] == to_lots(0.1, 1_000_000, 3500.0, 10)  # 3
        assert sig["delta_lots"] == sig["target_lots"] - sig["current_lots"]  # 2

    def test_risk_usage_carried(self):
        fs = signal_map_to_factor_signal({"RB0": {"direction": 1, "risk_usage": 0.35}})
        assert fs["signals"][0]["risk_usage"] == 0.35

    def test_validator_zero_errors(self):
        fs = signal_map_to_factor_signal(dict(_SAMPLE_MAP), signal_id="s-x", frequency="30m")
        assert SignalValidator().validate(fs) == []


class TestValidatorNewFields:
    """G12 新字段校验规则。"""

    def test_accepts_valid_lots(self):
        fs = signal_map_to_factor_signal(
            {"RB0": {"direction": 1, "target_lots": 5, "current_lots": 2, "delta_lots": 3}}
        )
        assert SignalValidator().validate(fs) == []

    def test_rejects_delta_mismatch(self):
        fs = signal_map_to_factor_signal(
            {"RB0": {"direction": 1, "target_lots": 5, "current_lots": 2, "delta_lots": 4}}
        )
        errors = SignalValidator().validate(fs)
        assert any("delta_lots" in e for e in errors)

    def test_rejects_negative_lots(self):
        fs = signal_map_to_factor_signal({"RB0": {"direction": 1, "target_lots": -1}})
        errors = SignalValidator().validate(fs)
        assert any("target_lots" in e for e in errors)

    def test_rejects_risk_usage_out_of_range(self):
        fs = signal_map_to_factor_signal({"RB0": {"direction": 1, "risk_usage": 1.5}})
        errors = SignalValidator().validate(fs)
        assert any("risk_usage" in e for e in errors)

    def test_rejects_non_numeric_score(self):
        fs = signal_map_to_factor_signal({"RB0": {"direction": 1, "score": "abc"}})
        errors = SignalValidator().validate(fs)
        assert any("score" in e for e in errors)
