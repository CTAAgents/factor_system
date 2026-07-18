"""
Tests for fts.strategies.base_v2 — v2 策略可插拔框架 100% 覆盖率。

覆盖：
  - ScoredSignal 默认构造 & to_dict() 全字段组合
  - BaseStrategyV2 抽象类属性默认值 & 具体子类实现
  - StrategyV1Adapter 适配器全链路
  - format_reason() 各种参数组合
  - _copy_fields() 正常 / 大写回退 / 前缀子分 / extra
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from unittest import mock

import pytest

from fts.strategies.base_v2 import (
    BaseStrategyV2,
    RawSignal,
    ScoredSignal,
    StrategyV1Adapter,
    _copy_fields,
    format_reason,
)


# ═══════════════════════════════════════════════════════════════
# ScoredSignal — 默认构造 & to_dict()
# ═══════════════════════════════════════════════════════════════

class TestScoredSignalDefaults:
    """ScoredSignal dataclass 构造与默认值。"""

    def test_bare_minimum(self):
        """最少必要字段构造。"""
        s = ScoredSignal(symbol="RB", direction="bull", signal_type="test.sig", strategy_name="test")
        assert s.total == 0.0
        assert s.abs_score == 0.0
        assert s.grade == "NOISE"
        assert s.weight == 1.0
        assert s.price == 0.0
        assert s._tdx_patched is False
        assert s.reason == ""
        assert s.sub_scores == {}
        assert s.extra == {}

    def test_all_fields_construct(self):
        """全部字段构造。"""
        s = ScoredSignal(
            symbol="CU", direction="bear", signal_type="mf.vr", strategy_name="multi_factor",
            total=-35.0, abs_score=35.0, grade="WATCH", weight=0.7,
            price=68000.0, change_pct=-2.5, volume=12000,
            adx=25.0, rsi=30.0, cci=-120.0,
            ma_slope=-0.05, macd_cross="dead_cross",
            dc20_break="below", ma_align="bearish",
            z_score=-2.1, stage="decline", atr=800.0,
            _tdx_patched=True,
            sub_scores={"dc20": 0.8, "bb50": 0.3},
            extra={"mode": "pure_momentum", "active_factors": 5},
            _raw_total=45.0, _raw_grade="STRONG",
            _validator_demoted=True,
            _validator_reason="atr_too_high",
            reason="手动原因",
        )
        assert s.symbol == "CU"
        assert s.direction == "bear"
        assert s._raw_total == 45.0
        assert s._raw_grade == "STRONG"
        assert s._validator_demoted is True


class TestScoredSignalToDict:
    """ScoredSignal.to_dict() 全场景。"""

    def test_empty_reason_auto_generate(self):
        """reason 为空 → 自动调用 format_reason 生成。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="trend.momentum",
            strategy_name="trend", grade="STRONG", abs_score=75.0,
            price=3500.0, change_pct=2.0, rsi=65.0, adx=30.0,
        )
        d = s.to_dict()
        assert d["reason"]  # 非空
        assert "[trend.momentum]" in d["reason"]
        assert "dir=bull" in d["reason"]
        assert "grade=STRONG" in d["reason"]

    def test_non_empty_reason_preserved(self):
        """reason 已设置 → 原样返回。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="trend.momentum",
            strategy_name="trend",
            reason="[custom] 人工设定理由",
        )
        d = s.to_dict()
        assert d["reason"] == "[custom] 人工设定理由"

    def test_rsi_cci_zero_not_in_metrics(self):
        """rsi/cci/adx/z_score/price 为零时不加入 metrics。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test", grade="WEAK", abs_score=15.0,
            rsi=0.0, cci=0.0, adx=0.0, price=0.0,
        )
        d = s.to_dict()
        # 所有 metric 字段为零 → metrics 为 None → reason 不含 "RSI=" 之类
        assert "RSI=" not in d["reason"]
        assert "CCI=" not in d["reason"]

    def test_rsi_nonzero_in_metrics(self):
        """rsi > 0 时出现在 metrics 中。"""
        s = ScoredSignal(
            symbol="RB", direction="bear", signal_type="test",
            strategy_name="test", grade="WEAK", abs_score=10.0,
            rsi=18.5, price=3500.0, z_score=-2.5,
        )
        d = s.to_dict()
        assert "RSI=18.5" in d["reason"]
        assert "Z=-2.5" in d["reason"]

    def test_raw_total_raw_grade_present(self):
        """_raw_total 和 _raw_grade 不为 None 时写入。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test",
            _raw_total=82.0, _raw_grade="STRONG",
        )
        d = s.to_dict()
        assert d["_raw_total"] == 82.0
        assert d["_raw_grade"] == "STRONG"

    def test_raw_total_raw_grade_absent(self):
        """_raw_total 和 _raw_grade 为 None 时不写入。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test",
        )
        d = s.to_dict()
        assert "_raw_total" not in d
        assert "_raw_grade" not in d

    def test_sub_scores_dc_bb_vol_prefixed(self):
        """sub_scores 中 dc*/bb*/vol* 前缀键展平到 dict。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test",
            sub_scores={
                "dc20": 0.85, "dc55": 0.6, "bb50": 0.3,
                "vol_ratio": 1.5, "vol_ma": 0.9,
                "momentum": 0.4,  # 非 dc/bb/vol 前缀 → 不展平
            },
        )
        d = s.to_dict()
        assert d.get("dc20") == 1  # round(0.85)
        assert d.get("dc55") == 1  # round(0.6)
        assert d.get("bb50") == 0  # round(0.3)
        assert d.get("vol_ratio") == 2  # round(1.5)
        # sub_scores 全部展平到 dict（含非 dc/bb/vol 前缀项）
        assert d.get("momentum") == 0  # round(0.4) = 0

    def test_sub_scores_non_float_not_rounded(self):
        """sub_scores 中非 float 值不 round。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test",
            sub_scores={"dc20": "gold_cross", "bb50": 0.3},
        )
        d = s.to_dict()
        assert d.get("dc20") == "gold_cross"  # 字符串不 round
        assert d.get("bb50") == 0  # float → round

    def test_extra_items_merged(self):
        """extra dict 项合并到顶层 dict。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test",
            extra={"mode": "pure_momentum", "active_factors": 5, "_raw_total": 42.0},
        )
        d = s.to_dict()
        assert d.get("mode") == "pure_momentum"
        assert d.get("active_factors") == 5

    def test_to_dict_rounding_consistency(self):
        """重要数值字段四舍五入一致性。"""
        s = ScoredSignal(
            symbol="RB", direction="bull", signal_type="test",
            strategy_name="test", total=75.6, abs_score=75.6,
            grade="STRONG", weight=0.7,
            price=3512.35, change_pct=1.234, volume=8888,
            adx=25.678, rsi=62.345, cci=150.789,
            ma_slope=0.1234, z_score=-0.5678, atr=45.678,
        )
        d = s.to_dict()
        assert d["total"] == 76       # round(75.6)
        assert d["abs"] == 76         # round(75.6)
        assert d["price"] == 3512.3   # round(3512.35, 1) banker's rounding
        assert d["change_pct"] == 1.23  # round(1.234, 2)
        assert d["adx"] == 25.7       # round(25.678, 1)
        assert d["rsi"] == 62.3       # round(62.345, 1)
        assert d["cci"] == 150.8      # round(150.789, 1)
        assert d["ma_slope"] == 0.12  # round(0.1234, 2)
        assert d["z_score"] == -0.57  # round(-0.5678, 2)
        assert d["atr"] == 45.7       # round(45.678, 1)


# ═══════════════════════════════════════════════════════════════
# BaseStrategyV2 — 抽象基类 & 具体子类
# ═══════════════════════════════════════════════════════════════

class TestBaseStrategyV2Abstract:
    """BaseStrategyV2 抽象基类属性 / 方法默认值。"""

    def test_cannot_instantiate_abstract(self):
        """不能直接实例化（有抽象方法 score）。"""
        with pytest.raises(TypeError):
            BaseStrategyV2()  # type: ignore

    def test_enabled_default_true(self):
        """enabled 类属性默认为 True。"""
        assert BaseStrategyV2.enabled is True


class _ConcreteStrategy(BaseStrategyV2):
    """具体策略子类（用于测试基类默认行为）。"""

    @property
    def name(self) -> str:
        return "test_concrete"

    def score(self, filtered_signals, tech_list, context=None):
        return []


class _CustomStrategy(BaseStrategyV2):
    """定制各属性的子类。"""

    enabled = False

    @property
    def name(self) -> str:
        return "custom_strat"

    @property
    def display_name(self) -> str:
        return "定制策略"

    @property
    def signal_type(self) -> str:
        return "custom.signal"

    @property
    def validators(self) -> list[str]:
        return ["atr_vol_timing", "stability"]

    @property
    def weight(self) -> float:
        return 0.5

    @property
    def depends_on(self) -> list[str]:
        return ["base_strat"]

    def compute(self, tech_list, kline_data, context=None):
        return [RawSignal(symbol="RB", direction="bull", signal_type="custom", raw_score=1.0, strategy_name=self.name)]

    def score(self, filtered_signals, tech_list, context=None):
        return [ScoredSignal(symbol="RB", direction="bull", signal_type="custom", strategy_name=self.name)]


class TestConcreteStrategyDefaults:
    """具体子类继承基类默认属性。"""

    def setup_method(self):
        self.s = _ConcreteStrategy()

    def test_display_name_defaults_to_name(self):
        assert self.s.display_name == "test_concrete"

    def test_signal_type_defaults_to_name(self):
        assert self.s.signal_type == "test_concrete"

    def test_validators_empty(self):
        assert self.s.validators == []

    def test_weight_one(self):
        assert self.s.weight == 1.0

    def test_depends_on_empty(self):
        assert self.s.depends_on == []

    def test_compute_default_empty(self):
        assert self.s.compute([], {}) == []

    def test_filter_identity(self):
        raw = [RawSignal(symbol="RB", direction="bull", signal_type="x", raw_score=1.0, strategy_name="test")]
        assert self.s.filter(raw) is raw

    def test_enabled_inherited(self):
        assert self.s.enabled is True


class TestCustomStrategyOverrides:
    """具体子类覆盖所有属性。"""

    def setup_method(self):
        self.s = _CustomStrategy()

    def test_name(self):
        assert self.s.name == "custom_strat"

    def test_display_name(self):
        assert self.s.display_name == "定制策略"

    def test_signal_type(self):
        assert self.s.signal_type == "custom.signal"

    def test_validators(self):
        assert self.s.validators == ["atr_vol_timing", "stability"]

    def test_weight(self):
        assert self.s.weight == 0.5

    def test_depends_on(self):
        assert self.s.depends_on == ["base_strat"]

    def test_compute_returns_signals(self):
        signals = self.s.compute([], {})
        assert len(signals) == 1
        assert signals[0].symbol == "RB"

    def test_score_returns_scored(self):
        scored = self.s.score([], [])
        assert len(scored) == 1
        assert isinstance(scored[0], ScoredSignal)

    def test_enabled_false(self):
        assert self.s.enabled is False


# ═══════════════════════════════════════════════════════════════
# StrategyV1Adapter — v1 → v2 适配器
# ═══════════════════════════════════════════════════════════════

class _MockV1Strategy:
    """模拟 v1 BaseStrategy。"""
    name = "v1_channel"
    display_name = "通道突破(v1)"

    def score(self, tech_list, mode="full", kline_data=None,
              df_map=None, period="daily", window_mode="fixed"):
        return {
            "all_ranked": [
                {
                    "symbol": "RB", "direction": "bull",
                    "signal_type": "breakout", "total": 65.0,
                    "abs": 65.0, "grade": "STRONG",
                    "price": 3500.0, "change_pct": 2.5,
                    "volume": 10000, "adx": 30.0, "rsi": 70.0,
                    "cci": 180.0, "_tdx_patched": True,
                    "dc20": 0.9, "bb50": 0.7, "vol_ratio": 1.5,
                    "extra": {"custom_key": "val"},
                    "_raw_total": 65.0,
                },
            ]
        }


class _MockV1StrategyUpper:
    """模拟输出大写字段的 v1 策略（legacy_numpy / TDX bridge）。"""

    name = "v1_upper"
    display_name = "大写字段(v1)"

    def score(self, tech_list, mode="full", kline_data=None,
              df_map=None, period="daily", window_mode="fixed"):
        return {
            "all_ranked": [
                {
                    "symbol": "CU", "direction": "bear",
                    "signal_type": "momentum", "total": -40.0,
                    "abs": 40.0, "grade": "WATCH",
                    "ADX": 28.5, "RSI14": 32.0, "VOL": 8000,
                    "ATR14": 450.0, "CCI20": -150.0,
                    "_tdx_patched": True,
                    "extra": {},
                },
            ]
        }


class _MockV1StrategyEmpty:
    """模拟无 all_ranked 的 v1 策略。"""

    name = "v1_empty"
    display_name = "空结果(v1)"

    def score(self, tech_list, mode="full", kline_data=None,
              df_map=None, period="daily", window_mode="fixed"):
        return {"all_ranked": []}


class TestStrategyV1AdapterConstruction:
    """StrategyV1Adapter 构造。"""

    def test_custom_params(self):
        adapter = StrategyV1Adapter(
            _MockV1Strategy(),
            signal_type="adapted.breakout",
            validators=["stability"],
            weight=0.8,
        )
        assert adapter._sig_type == "adapted.breakout"
        assert adapter._validators == ["stability"]
        assert adapter._weight == 0.8

    def test_none_defaults(self):
        """signal_type/validators/weight 为 None 时使用 v1 默认值。"""
        adapter = StrategyV1Adapter(_MockV1Strategy())
        # signal_type → v1.name（但实际是 _v1.name）
        assert adapter._sig_type == "v1_channel"
        assert adapter._validators == []
        assert adapter._weight == 1.0


class TestStrategyV1AdapterProperties:
    """Adapter 属性代理。"""

    def setup_method(self):
        self.adapter = StrategyV1Adapter(
            _MockV1Strategy(),
            signal_type="adapted.breakout",
            validators=["stability"],
            weight=0.8,
        )

    def test_name(self):
        assert self.adapter.name == "v1_channel"

    def test_signal_type(self):
        assert self.adapter.signal_type == "adapted.breakout"

    def test_validators(self):
        assert self.adapter.validators == ["stability"]

    def test_weight(self):
        assert self.adapter.weight == 0.8

    def test_display_name(self):
        assert self.adapter.display_name == "通道突破(v1)"


class TestStrategyV1AdapterCompute:
    """Adapter.compute() 返回 pass-through RawSignal 列表。"""

    def test_compute_returns_raw_signals(self):
        adapter = StrategyV1Adapter(_MockV1Strategy())
        tech_list = [
            {"symbol": "RB", "price": 3500.0},
            {"symbol": "CU", "price": 68000.0},
        ]
        signals = adapter.compute(tech_list, {})
        assert len(signals) == 2
        assert all(isinstance(s, RawSignal) for s in signals)
        assert signals[0].symbol == "RB"
        assert signals[0].direction == "neutral"
        # signal_type = f"{self._sig_type}.raw", _sig_type = v1_strategy.name = "v1_channel"
        assert signals[0].signal_type == "v1_channel.raw"
        assert signals[0].raw_score == 0.0
        assert signals[0].strategy_name == "v1_channel"
        assert signals[0].meta == {"symbol": "RB", "price": 3500.0}
        assert signals[1].symbol == "CU"

    def test_filter_identity(self):
        adapter = StrategyV1Adapter(_MockV1Strategy())
        raw = [RawSignal("RB", "bull", "x", 1.0, "v1")]
        assert adapter.filter(raw) is raw


class TestStrategyV1AdapterScore:
    """Adapter.score() 调用 _v1.score 并转换结果。"""

    def test_score_normal(self):
        adapter = StrategyV1Adapter(
            _MockV1Strategy(),
            signal_type="adapted.breakout",
            validators=["stability"],
            weight=0.8,
        )
        tech_list = [{"symbol": "RB"}]
        filtered = [RawSignal("RB", "bull", "adapted.breakout.raw", 0.0, "v1_channel")]
        scored = adapter.score(filtered, tech_list, {})
        assert len(scored) == 1
        s = scored[0]
        assert s.symbol == "RB"
        assert s.direction == "bull"
        assert s.signal_type == "adapted.breakout.breakout"
        assert s.total == 65.0
        assert s.abs_score == 65.0
        assert s.grade == "STRONG"
        assert s.weight == 0.8
        # _copy_fields 复制的字段
        assert s.price == 3500.0
        assert s.change_pct == 2.5
        assert s.volume == 10000
        assert s.adx == 30.0
        assert s.rsi == 70.0
        assert s.cci == 180.0
        assert s._tdx_patched is True
        # sub_scores: dc*/bb*/vol* 前缀
        assert s.sub_scores.get("dc20") == 0.9
        assert s.sub_scores.get("bb50") == 0.7
        assert s.sub_scores.get("vol_ratio") == 1.5
        # extra
        assert s.extra.get("custom_key") == "val"
        assert s.extra.get("_raw_total") == 65.0
        # reason 自动生成
        assert s.reason
        assert "adapted.breakout.breakout" in s.reason

    def test_score_empty_all_ranked(self):
        adapter = StrategyV1Adapter(_MockV1StrategyEmpty())
        scored = adapter.score([], [])
        assert scored == []

    def test_score_with_context(self):
        """context 中的 mode/kline_data/df_map 等应透传。"""
        mock_v1 = mock.Mock()
        mock_v1.name = "v1_mock"
        mock_v1.display_name = "MockV1"
        mock_v1.score.return_value = {"all_ranked": []}

        adapter = StrategyV1Adapter(mock_v1)
        ctx = {
            "mode": "scan",
            "kline_data": {"RB": ("RB", [])},
            "df_map": {"RB": "df"},
            "period": "weekly",
            "window_mode": "rolling",
        }
        adapter.score([], [{"symbol": "RB"}], context=ctx)
        mock_v1.score.assert_called_once_with(
            [{"symbol": "RB"}],
            mode="scan",
            kline_data={"RB": ("RB", [])},
            df_map={"RB": "df"},
            period="weekly",
            window_mode="rolling",
        )

    def test_score_context_none_fallback(self):
        """context 为 None 时使用默认参数调用 _v1.score。"""
        mock_v1 = mock.Mock()
        mock_v1.name = "v1_mock"
        mock_v1.display_name = "MockV1"
        mock_v1.score.return_value = {"all_ranked": []}

        adapter = StrategyV1Adapter(mock_v1)
        adapter.score([], [{"symbol": "RB"}], context=None)
        mock_v1.score.assert_called_once_with(
            [{"symbol": "RB"}],
            mode="full",
            kline_data=None,
            df_map=None,
            period="daily",
            window_mode="fixed",
        )

    def test_score_upper_case_fallback(self):
        """大写字段（ADX/RSI14/VOL/ATR14/CCI20）通过 _upper_fallback 回退。"""
        adapter = StrategyV1Adapter(
            _MockV1StrategyUpper(),
            signal_type="upper",
        )
        filtered = [RawSignal("CU", "bear", "upper.raw", 0.0, "v1_upper")]
        scored = adapter.score(filtered, [{"symbol": "CU"}])
        assert len(scored) == 1
        s = scored[0]
        assert s.adx == 28.5
        assert s.rsi == 32.0
        assert s.volume == 8000
        assert s.atr == 450.0
        assert s.cci == -150.0
        assert s._tdx_patched is True


# ═══════════════════════════════════════════════════════════════
# format_reason — 结构化 reason 字符串
# ═══════════════════════════════════════════════════════════════

class TestFormatReason:
    """format_reason() 各种参数组合。"""

    def test_all_parameters(self):
        s = format_reason(
            "mean_reversion.rsi", "bull", "STRONG",
            metrics={"RSI": 18.3, "ADX": 14.2},
            strength=0.85,
            note="超卖反弹",
        )
        assert "[mean_reversion.rsi]" in s
        assert "dir=bull" in s
        assert "grade=STRONG" in s
        assert "RSI=18.3" in s
        assert "ADX=14.2" in s
        assert "强度=0.85" in s
        assert "超卖反弹" in s

    def test_without_metrics(self):
        s = format_reason("trend.momentum", "bear", "WATCH", strength=0.65)
        assert "[trend.momentum]" in s
        assert "dir=bear" in s
        assert "grade=WATCH" in s
        assert "强度=0.65" in s
        # 不应有 "RSI=" 等指标
        assert "=" not in [p for p in s.split(" | ") if p.startswith("dir=")]

    def test_without_strength(self):
        s = format_reason("arbitrage.base", "neutral", "NOISE", metrics={"Z": 0.5})
        assert "[arbitrage.base]" in s
        assert "dir=neutral" in s
        assert "grade=NOISE" in s
        assert "Z=0.5" in s
        assert "强度=" not in s

    def test_with_note(self):
        s = format_reason("test.sig", "bull", "WEAK", note="仅参考")
        assert "[test.sig]" in s
        assert "dir=bull" in s
        assert "grade=WEAK" in s
        assert "仅参考" in s

    def test_minimal(self):
        """最简调用：仅 signal_type + direction + grade。"""
        s = format_reason("minimal", "bull", "STRONG")
        assert s == "[minimal] | dir=bull | grade=STRONG"

    def test_metrics_none(self):
        """metrics=None 等价于不传。"""
        s = format_reason("test", "bull", "WEAK", metrics=None, strength=0.3)
        assert "[test]" in s
        assert "强度=0.30" in s
        assert "RSI=" not in s

    def test_strength_zero(self):
        """strength=0 应渲染为 0.00。"""
        s = format_reason("test", "bear", "NOISE", strength=0.0)
        assert "强度=0.00" in s

    def test_empty_note(self):
        """note 为空字符串时不追加。"""
        s = format_reason("test", "bull", "STRONG", note="")
        assert s == "[test] | dir=bull | grade=STRONG"


# ═══════════════════════════════════════════════════════════════
# _copy_fields — v1 结果→ScoredSignal 字段复制
# ═══════════════════════════════════════════════════════════════

class TestCopyFields:
    """_copy_fields() 正常字段 / 大写回退 / 子分 / extra。"""

    def test_normal_fields(self):
        """正常传递的小写字段全部复制。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {
            "price": 3500.0, "change_pct": 1.5, "volume": 10000,
            "adx": 25.0, "rsi": 60.0, "cci": 150.0,
            "ma_slope": 0.05, "macd_cross": "gold_cross",
            "dc20_break": "above", "ma_align": "bullish",
            "z_score": 1.2, "stage": "uptrend", "atr": 45.0,
            "_tdx_patched": True,
            "extra": {},
        }
        _copy_fields(src, dst)
        assert dst.price == 3500.0
        assert dst.change_pct == 1.5
        assert dst.volume == 10000
        assert dst.adx == 25.0
        assert dst.rsi == 60.0
        assert dst.cci == 150.0
        assert dst.ma_slope == 0.05
        assert dst.macd_cross == "gold_cross"
        assert dst.dc20_break == "above"
        assert dst.ma_align == "bullish"
        assert dst.z_score == 1.2
        assert dst.stage == "uptrend"
        assert dst.atr == 45.0
        assert dst._tdx_patched is True

    def test_upper_case_fallback(self):
        """小写字段为 None 时从大写回退。"""
        dst = ScoredSignal("CU", "bear", "test", "test")
        src = {
            "ADX": 28.0, "RSI14": 32.0, "VOL": 8000,
            "ATR14": 450.0, "CCI20": -150.0,
            "_tdx_patched": True,
            "extra": {},
        }
        _copy_fields(src, dst)
        assert dst.adx == 28.0
        assert dst.rsi == 32.0
        assert dst.volume == 8000
        assert dst.atr == 450.0
        assert dst.cci == -150.0

    def test_upper_case_multiple_fallback(self):
        """_upper_fallback 多候选：adx 在 ADX 为空时尝试 ADX14。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {"ADX14": 26.5, "RSI": 55.0, "ATR": 100.0, "_tdx_patched": False, "extra": {}}
        _copy_fields(src, dst)
        assert dst.adx == 26.5
        assert dst.rsi == 55.0
        assert dst.atr == 100.0

    def test_sub_scores_dc_bb_vol_prefixed(self):
        """dc*/bb*/vol* 前缀键进入 sub_scores。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {
            "price": 3500.0,
            "dc20": 0.9, "dc55": 0.7, "bb50": 0.3,
            "vol_ratio": 1.5, "vol_ma": 0.8,
            "momentum": 0.4,  # 非前缀 → 不进入 sub_scores
            "_tdx_patched": False,
            "extra": {},
        }
        _copy_fields(src, dst)
        assert dst.sub_scores.get("dc20") == 0.9
        assert dst.sub_scores.get("dc55") == 0.7
        assert dst.sub_scores.get("bb50") == 0.3
        assert dst.sub_scores.get("vol_ratio") == 1.5
        assert dst.sub_scores.get("vol_ma") == 0.8
        assert "momentum" not in dst.sub_scores

    def test_extra_dict(self):
        """extra 键复制到 dst.extra。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {"_tdx_patched": False, "extra": {"mode": "pure_momentum", "factors": 5}}
        _copy_fields(src, dst)
        assert dst.extra.get("mode") == "pure_momentum"
        assert dst.extra.get("factors") == 5

    def test_extra_special_keys(self):
        """_raw_total / _raw_grade / _validator_* 等特殊键写入 dst.extra。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {
            "_tdx_patched": False,
            "extra": {},
            "_raw_total": 80.0,
            "_raw_grade": "STRONG",
            "_validator_demoted": True,
            "_validator_reason": "volume_too_low",
            "_oi_surge_reversal": True,
            "_strangle_compressed": 0.5,
            "_basis_conflict": "divergent",
        }
        _copy_fields(src, dst)
        assert dst.extra["_raw_total"] == 80.0
        assert dst.extra["_raw_grade"] == "STRONG"
        assert dst.extra["_validator_demoted"] is True
        assert dst.extra["_validator_reason"] == "volume_too_low"
        assert dst.extra["_oi_surge_reversal"] is True
        assert dst.extra["_strangle_compressed"] == 0.5
        assert dst.extra["_basis_conflict"] == "divergent"

    def test_none_fields_skipped(self):
        """None 字段不 setattr，保留 dst 默认值。"""
        dst = ScoredSignal("RB", "bull", "test", "test")
        src = {"_tdx_patched": False, "extra": {}}
        _copy_fields(src, dst)
        # 所有字段未在 src 中 → 保留默认值 0.0
        assert dst.price == 0.0
        assert dst.change_pct == 0.0
        assert dst.volume == 0
        assert dst.adx == 0.0
        assert dst._tdx_patched is False


class TestRulesInit:
    """fts.strategies.rules 占位模块导入测试。"""

    def test_rules_importable(self):
        from fts.strategies.rules import __version__
        assert __version__ == "0.1.0"
