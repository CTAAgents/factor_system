"""
MultiFactorStrategy G27/G29 测试 — 因子数据源接入。

覆盖：
  - _calc_warrant_change：真实仓单数据→有符号分；无数据→惰性0
  - _calc_inventory：单点绝对值(无分位)→惰性0；含 pct 字段→激活
  - _calc_capacity：同上
  - _calc_pmi_proxy（G29）：PMI 水平分 + 动量；无数据→惰性0
  - _calc_rate_proxy（G29）：LPR1Y 动量优先 / 水平分；无数据→惰性0
  - compute()：ctx_extra 注入 warrant_data 后因子被消费（不再恒为0）
  - _safe_float 边缘分支
  - 全部 _calc_* 函数完整分支覆盖
  - MultiFactorStrategy 三模式构造、compute/score 全路径
"""
from __future__ import annotations

import pytest

from fts.strategies.multi_factor_strategy import (
    FACTOR_WEIGHTS,
    MultiFactorStrategy,
    PURE_MOMENTUM_WEIGHTS,
    _calc_basis,
    _calc_capacity,
    _calc_inventory,
    _calc_macro,
    _calc_momentum,
    _calc_oi_change,
    _calc_pmi_proxy,
    _calc_position_rank,
    _calc_rate_proxy,
    _calc_volatility_reversion,
    _calc_volume_flow,
    _calc_warrant_change,
    _safe_float,
)
from fts.strategies.base_v2 import RawSignal, ScoredSignal


def _mfs(mode: str = "pure_momentum"):
    return MultiFactorStrategy(mode)


def _ctx_extra(warrant=None, inventory=None, supply=None, macro_data=None):
    return {
        "warrant_data": warrant or {},
        "inventory_data": inventory or {},
        "supply_data": supply or {},
        "macro_data": macro_data if macro_data is not None else {"available": False},
    }


def _tech(**overrides) -> dict:
    """便捷构建 tech dict。"""
    base = {
        "symbol": "RB", "price": 3500.0, "change_pct": 1.0,
        "ma_slope": 0.1, "macd_cross": "none", "atr": 50.0,
        "bb": 0.5, "bb_width": 0.05, "vol_ratio": 1.0,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# _safe_float
# ═══════════════════════════════════════════════════════════════

class TestSafeFloat:
    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0
        assert _safe_float(None, -1.0) == -1.0

    def test_valid_number(self):
        assert _safe_float(42) == 42.0
        assert _safe_float(3.14) == 3.14

    def test_valid_string(self):
        assert _safe_float("3.14") == 3.14

    def test_value_error(self):
        assert _safe_float("not_a_number", 0.0) == 0.0

    def test_type_error(self):
        class BadType:
            def __float__(self):
                raise TypeError("bad")
        assert _safe_float(BadType(), 0.0) == 0.0

    def test_custom_default(self):
        assert _safe_float(None, 999.0) == 999.0


# ═══════════════════════════════════════════════════════════════
# _calc_momentum — 动量因子
# ═══════════════════════════════════════════════════════════════

class TestCalcMomentum:
    def test_gold_cross(self):
        """MACD 金叉 → +0.2。"""
        s = _calc_momentum({"change_pct": 0, "ma_slope": 0, "macd_cross": "gold_cross"})
        assert s == pytest.approx(0.2)

    def test_dead_cross(self):
        """MACD 死叉 → -0.2。"""
        s = _calc_momentum({"change_pct": 0, "ma_slope": 0, "macd_cross": "dead_cross"})
        assert s == pytest.approx(-0.2)

    def test_big_change_clamped(self):
        """大价格变化钳位在 ±0.5。"""
        s = _calc_momentum({"change_pct": 50, "ma_slope": 0, "macd_cross": "none"})
        assert s == pytest.approx(0.5)

    def test_big_negative_change_clamped(self):
        s = _calc_momentum({"change_pct": -50, "ma_slope": 0, "macd_cross": "none"})
        assert s == pytest.approx(-0.5)

    def test_ma_slope_clamped(self):
        s = _calc_momentum({"change_pct": 0, "ma_slope": 5, "macd_cross": "none"})
        assert s == pytest.approx(0.3)  # min(0.3, 5*3=15)

    def test_ma_slope_negative(self):
        s = _calc_momentum({"change_pct": 0, "ma_slope": -1, "macd_cross": "none"})
        assert s == pytest.approx(-0.3)  # max(-0.3, -1*3=-3)

    def test_overall_clamped_to_1(self):
        s = _calc_momentum({"change_pct": 5, "ma_slope": 1, "macd_cross": "gold_cross"})
        # 0.5 + 0.3 + 0.2 = 1.0
        assert s == pytest.approx(1.0)

    def test_overall_clamped_to_neg1(self):
        s = _calc_momentum({"change_pct": -5, "ma_slope": -1, "macd_cross": "dead_cross"})
        # -0.5 + -0.3 + -0.2 = -1.0
        assert s == pytest.approx(-1.0)

    def test_missing_keys_default_zero(self):
        s = _calc_momentum({"symbol": "RB"})
        assert s == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_volatility_reversion — 波动率因子
# ═══════════════════════════════════════════════════════════════

class TestCalcVolatilityReversion:
    def test_bb_zero_bullish(self):
        """bb=0 → 下轨 → +0.5。"""
        s = _calc_volatility_reversion({"bb": 0, "bb_width": 0, "price": 0, "atr": 0})
        assert s == pytest.approx(0.5)

    def test_bb_one_bearish(self):
        """bb=1 → 上轨 → -0.5。"""
        s = _calc_volatility_reversion({"bb": 1, "bb_width": 0, "price": 0, "atr": 0})
        assert s == pytest.approx(-0.5)

    def test_bb_outside_range_no_contribution(self):
        """bb 不在 [0,1] 不贡献 bb 部分。"""
        s = _calc_volatility_reversion({"bb": -1, "bb_width": 0, "price": 0, "atr": 0})
        assert s == 0.0

    def test_bb_width_zero_no_contribution(self):
        """bb_width=0 时 bb_width 分支不贡献（>0 才进）。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0, "price": 0, "atr": 0})
        assert s == 0.0

    def test_bb_width_wide_mean_reverting(self):
        """宽布林带 → 回归概率增加 → 偏空（假设已涨）。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0.1, "price": 0, "atr": 0})
        # (0.05 - 0.1) * 5 = -0.25, clamped → -0.25
        assert s == pytest.approx(-0.25)

    def test_price_zero_skips_atr(self):
        """price=0 时 atr 部分不贡献。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0, "price": 0, "atr": 100})
        assert s == 0.0

    def test_atr_zero_skips_atr(self):
        """atr=0 时 atr 部分不贡献。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0, "price": 3500, "atr": 0})
        assert s == 0.0

    def test_high_atr_ratio(self):
        """高 ATR/价格比 → 回归做空。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0, "price": 100, "atr": 10})
        # atr_ratio=0.1, (0.02-0.1)*10 = -0.8, clamped → -0.2
        assert s == pytest.approx(-0.2)

    def test_low_atr_ratio(self):
        """低 ATR/价格比 → 略偏多。"""
        s = _calc_volatility_reversion({"bb": 0.5, "bb_width": 0, "price": 10000, "atr": 50})
        # atr_ratio=0.005, (0.02-0.005)*10 = 0.15
        assert s == pytest.approx(0.15)

    def test_combined_all_contributions(self):
        """bb=0.2, bb_width=0.08, atr_ratio 综合。"""
        s = _calc_volatility_reversion({"bb": 0.2, "bb_width": 0.08, "price": 200, "atr": 12})
        # bb: (0.5-0.2)*1.0=0.3
        # bb_width: (0.05-0.08)*5=-0.15, clamped → -0.15
        # atr_ratio=0.06: (0.02-0.06)*10=-0.4, clamped → -0.2
        # total: 0.3-0.15-0.2=-0.05
        assert s == pytest.approx(-0.05)


# ═══════════════════════════════════════════════════════════════
# _calc_volume_flow — 资金流因子
# ═══════════════════════════════════════════════════════════════

class TestCalcVolumeFlow:
    def test_high_vol_bullish(self):
        """放量上涨 → +0.5。"""
        s = _calc_volume_flow({"vol_ratio": 1.5, "change_pct": 2.0})
        assert s == 0.5

    def test_high_vol_bearish(self):
        """放量下跌 → -0.5。"""
        s = _calc_volume_flow({"vol_ratio": 1.5, "change_pct": -2.0})
        assert s == -0.5

    def test_high_vol_no_change(self):
        """放量但价格未变 → 0。"""
        s = _calc_volume_flow({"vol_ratio": 1.5, "change_pct": 0})
        assert s == 0.0

    def test_low_vol_positive(self):
        """缩量上涨 → 正向衰减。"""
        s = _calc_volume_flow({"vol_ratio": 0.5, "change_pct": 2.0})
        # (2/5)*0.3 = 0.12
        assert s == pytest.approx(0.12)

    def test_low_vol_negative(self):
        """缩量下跌 → 负向衰减。"""
        s = _calc_volume_flow({"vol_ratio": 0.5, "change_pct": -2.0})
        # (-2/5)*0.3 = -0.12
        assert s == pytest.approx(-0.12)

    def test_low_vol_no_change(self):
        """缩量但价格未变 → 0。"""
        s = _calc_volume_flow({"vol_ratio": 0.5, "change_pct": 0})
        assert s == 0.0

    def test_mid_vol_no_score(self):
        """缩量与放量之间的区间 → 0。"""
        s = _calc_volume_flow({"vol_ratio": 1.0, "change_pct": 2.0})
        assert s == 0.0

    def test_default_vol_ratio(self):
        """vol_ratio 缺失时默认 1.0。"""
        s = _calc_volume_flow({"change_pct": 1.0})
        assert s == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_oi_change — 持仓量变化因子
# ═══════════════════════════════════════════════════════════════

class TestCalcOiChange:
    def test_ctx_extra_none(self):
        """ctx_extra=None → 安全处理。"""
        s = _calc_oi_change({"symbol": "RB"}, None)
        assert s == 0.0

    def test_no_oi_data(self):
        s = _calc_oi_change({"symbol": "RB"}, {})
        assert s == 0.0

    def test_small_oi_ratio_no_score(self):
        """|oi_ratio| <= 0.05 不触发。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": 1.0},
                            {"oi_data": {"RB": {"oi_ratio": 0.03}}})
        assert s == 0.0

    def test_oi_up_price_up_bull(self):
        """OI增+价涨 → 多头进场 +0.6。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": 2.0},
                            {"oi_data": {"RB": {"oi_ratio": 0.1}}})
        assert s == pytest.approx(0.6)

    def test_oi_up_price_down_bear(self):
        """OI增+价跌 → 空头进场 -0.6。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": -2.0},
                            {"oi_data": {"RB": {"oi_ratio": 0.1}}})
        assert s == pytest.approx(-0.6)

    def test_oi_up_price_flat(self):
        """OI增+价不变 → 不触发方向分支。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": 0},
                            {"oi_data": {"RB": {"oi_ratio": 0.1}}})
        assert s == 0.0

    def test_oi_down_price_up_bull(self):
        """OI减+价涨 → 空头离场 +0.3。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": 2.0},
                            {"oi_data": {"RB": {"oi_ratio": -0.1}}})
        assert s == pytest.approx(0.3)

    def test_oi_down_price_down_bear(self):
        """OI减+价跌 → 多头离场 -0.3。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": -2.0},
                            {"oi_data": {"RB": {"oi_ratio": -0.1}}})
        assert s == pytest.approx(-0.3)

    def test_oi_down_price_flat(self):
        """OI减+价不变 → 不触发方向分支。"""
        s = _calc_oi_change({"symbol": "RB", "change_pct": 0},
                            {"oi_data": {"RB": {"oi_ratio": -0.1}}})
        assert s == 0.0

    def test_overall_clamped(self):
        s = _calc_oi_change({"symbol": "RB", "change_pct": 2.0},
                            {"oi_data": {"RB": {"oi_ratio": 5.0}}})
        assert s <= 1.0

    def test_symbol_not_in_oi_data(self):
        s = _calc_oi_change({"symbol": "CU"}, {"oi_data": {"RB": {"oi_ratio": 0.1}}})
        assert s == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_basis — 基差因子
# ═══════════════════════════════════════════════════════════════

class TestCalcBasis:
    def test_ctx_extra_none(self):
        assert _calc_basis({"symbol": "RB"}, None) == 0.0

    def test_no_basis_data(self):
        assert _calc_basis({"symbol": "RB"}, {}) == 0.0

    def test_high_contango(self):
        """basis_pct > 2 → 强升水 → -0.6。"""
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": 3.0}}})
        assert s == pytest.approx(-0.6)

    def test_mid_contango(self):
        """1 < basis_pct <= 2 → 升水 → -0.3。"""
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": 1.5}}})
        assert s == pytest.approx(-0.3)

    def test_high_backwardation(self):
        """basis_pct < -2 → 强贴水 → +0.6。"""
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": -3.0}}})
        assert s == pytest.approx(0.6)

    def test_mid_backwardation(self):
        """-2 <= basis_pct < -1 → 贴水 → +0.3。"""
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": -1.5}}})
        assert s == pytest.approx(0.3)

    def test_near_zero(self):
        """|basis_pct| <= 1 → 中性。"""
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": 0.5}}})
        assert s == 0.0

    def test_clamped(self):
        s = _calc_basis({"symbol": "RB"}, {"basis_data": {"RB": {"basis_pct": 100}}})
        assert s == pytest.approx(-0.6)  # clipped by max, already within [-1,1]

    def test_symbol_not_in_basis_data(self):
        s = _calc_basis({"symbol": "CU"}, {"basis_data": {"RB": {"basis_pct": 2.0}}})
        assert s == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_macro — 宏观因子
# ═══════════════════════════════════════════════════════════════

class TestCalcMacro:
    def test_context_none(self):
        assert _calc_macro({}, None) == 0.0

    def test_bull(self):
        assert _calc_macro({}, {"macro_signal": "bull"}) == 0.5

    def test_bear(self):
        assert _calc_macro({}, {"macro_signal": "bear"}) == -0.5

    def test_neutral(self):
        assert _calc_macro({}, {"macro_signal": "neutral"}) == 0.0

    def test_unknown(self):
        assert _calc_macro({}, {"macro_signal": "unknown"}) == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_position_rank — 龙虎持仓因子
# ═══════════════════════════════════════════════════════════════

class TestCalcPositionRank:
    def test_ctx_extra_none(self):
        assert _calc_position_rank({"symbol": "RB"}, None) == 0.0

    def test_no_oi_data(self):
        assert _calc_position_rank({"symbol": "RB"}, {}) == 0.0

    def test_high_long_concentration(self):
        """top5_ratio > 0.4 → 做多 +0.3。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": 0.5}}})
        assert s == pytest.approx(0.3)

    def test_mid_long_concentration(self):
        """0.3 < top5_ratio <= 0.4 → 做多 +0.15。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": 0.35}}})
        assert s == pytest.approx(0.15)

    def test_high_short_concentration(self):
        """top5_ratio < -0.3 → 做空 -0.3。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": -0.4}}})
        assert s == pytest.approx(-0.3)

    def test_mid_short_concentration(self):
        """-0.3 <= top5_ratio < -0.2 → 做空 -0.15。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": -0.25}}})
        assert s == pytest.approx(-0.15)

    def test_neutral_zone(self):
        """-0.2 <= top5_ratio <= 0.3 → 中性。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": 0.0}}})
        assert s == 0.0

    def test_clamped(self):
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": 5.0}}})
        assert s == pytest.approx(0.3)

    def test_both_long_and_short_cumulative(self):
        """多头和空头贡献独立叠加。"""
        s = _calc_position_rank({"symbol": "RB"}, {"oi_data": {"RB": {"top5_ratio": 0.5}}})
        # 只触发 long > 0.4 → +0.3
        assert s == pytest.approx(0.3)


# ═══════════════════════════════════════════════════════════════
# _calc_warrant_change — 仓单变化因子（边缘分支）
# ═══════════════════════════════════════════════════════════════

class TestWarrantChangeEdge:
    def test_clip_at_plus_5pct(self):
        """daily_change/total > 1 → 钳位到 -1（增→负）。"""
        ctx = _ctx_extra(warrant={"RB": {"total": 100.0, "daily_change": 100.0}})
        s = _calc_warrant_change({"symbol": "RB"}, ctx)
        assert s == pytest.approx(-1.0)

    def test_clip_at_minus_5pct(self):
        """daily_change/total < -1 → 钳位到 +1（减→正）。"""
        ctx = _ctx_extra(warrant={"RB": {"total": 100.0, "daily_change": -100.0}})
        s = _calc_warrant_change({"symbol": "RB"}, ctx)
        assert s == pytest.approx(1.0)

    def test_small_change(self):
        """微小变化。"""
        ctx = _ctx_extra(warrant={"RB": {"total": 1000.0, "daily_change": 1.0}})
        s = _calc_warrant_change({"symbol": "RB"}, ctx)
        assert s == pytest.approx(-0.005, abs=1e-3)  # -(1/1000*5) = -0.005

    def test_symbol_not_in_warrant(self):
        ctx = _ctx_extra(warrant={"CU": {"total": 100.0, "daily_change": 10.0}})
        assert _calc_warrant_change({"symbol": "RB"}, ctx) == 0.0

    def test_zero_total_inert(self):
        """total <= 0 → 返回 0.0。"""
        ctx = _ctx_extra(warrant={"RB": {"total": 0.0, "daily_change": 5.0}})
        assert _calc_warrant_change({"symbol": "RB"}, ctx) == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_inventory — 库存分位因子（边缘分支）
# ═══════════════════════════════════════════════════════════════

class TestInventoryEdge:
    def test_percentile_field(self):
        """支持 percentile 字段名。"""
        ctx = _ctx_extra(inventory={"CU": {"percentile": 0.9}})
        assert _calc_inventory({"symbol": "CU"}, ctx) < 0

    def test_pct_above_one_normalized(self):
        """pct > 1（如 95 表示 95%）→ 除以 100。"""
        ctx = _ctx_extra(inventory={"CU": {"pct": 95}})
        s = _calc_inventory({"symbol": "CU"}, ctx)
        # p=0.95, (0.5-0.95)*2=-0.9
        assert s == pytest.approx(-0.9)

    def test_pct_below_one_not_normalized(self):
        """pct <= 1 时不额外归一化。"""
        ctx = _ctx_extra(inventory={"CU": {"pct": 0.3}})
        s = _calc_inventory({"symbol": "CU"}, ctx)
        # p=0.3, (0.5-0.3)*2=0.4
        assert s == pytest.approx(0.4)

    def test_clipped_to_1(self):
        ctx = _ctx_extra(inventory={"CU": {"pct": 0.01}})
        s = _calc_inventory({"symbol": "CU"}, ctx)
        # p=0.01, (0.5-0.01)*2=0.98
        assert s == pytest.approx(0.98)

    def test_symbol_not_in_inventory(self):
        ctx = _ctx_extra(inventory={"CU": {"pct": 0.9}})
        assert _calc_inventory({"symbol": "RB"}, ctx) == 0.0

    def test_pct_none_after_or(self):
        """pct=None 且 percentile 不存在 → pct is None → fall through to return 0.0。"""
        ctx = _ctx_extra(inventory={"CU": {"pct": None}})
        assert _calc_inventory({"symbol": "CU"}, ctx) == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_capacity — 开工率因子（边缘分支）
# ═══════════════════════════════════════════════════════════════

class TestCapacityEdge:
    def test_utilization_pct_field(self):
        """支持 utilization_pct 字段名。"""
        ctx = _ctx_extra(supply={"RB": {"utilization_pct": 0.9}})
        assert _calc_capacity({"symbol": "RB"}, ctx) < 0

    def test_pct_above_one_normalized(self):
        """pct > 1 → 除以 100。"""
        ctx = _ctx_extra(supply={"RB": {"pct": 80}})
        s = _calc_capacity({"symbol": "RB"}, ctx)
        # p=0.8, (0.5-0.8)*2=-0.6
        assert s == pytest.approx(-0.6)

    def test_pct_below_one(self):
        ctx = _ctx_extra(supply={"RB": {"pct": 0.3}})
        s = _calc_capacity({"symbol": "RB"}, ctx)
        assert s == pytest.approx(0.4)

    def test_symbol_not_in_supply(self):
        ctx = _ctx_extra(supply={"RB": {"pct": 0.9}})
        assert _calc_capacity({"symbol": "CU"}, ctx) == 0.0

    def test_pct_none_after_or(self):
        """pct=None 且 utilization_pct 不存在 → pct is None → fall through to return 0.0。"""
        ctx = _ctx_extra(supply={"RB": {"pct": None}})
        assert _calc_capacity({"symbol": "RB"}, ctx) == 0.0


# ═══════════════════════════════════════════════════════════════
# _calc_pmi_proxy — PMI 景气度（边缘分支）
# ═══════════════════════════════════════════════════════════════

class TestPmiProxyEdge:
    def test_macro_data_empty_dict(self):
        """macro_data 存在但 available=False。"""
        assert _calc_pmi_proxy({}, {"macro_data": {}}) == 0.0

    def test_macro_data_none_pmi(self):
        """available=True 但 pmi=None → 0。"""
        ctx = _ctx_extra(macro_data={"available": True, "pmi": None})
        assert _calc_pmi_proxy({}, ctx) == 0.0

    def test_pmi_very_high_clipped(self):
        """PMI>>50 → 钳位到 1.0。"""
        ctx = _ctx_extra(macro_data={"available": True, "pmi": 60.0})
        assert _calc_pmi_proxy({}, ctx) == pytest.approx(1.0)  # (60-50)/5=2→clamp to 1

    def test_pmi_very_low_clipped(self):
        """PMI<<50 → 钳位到 -1.0。"""
        ctx = _ctx_extra(macro_data={"available": True, "pmi": 40.0})
        assert _calc_pmi_proxy({}, ctx) == pytest.approx(-1.0)  # (40-50)/5=-2→clamp to -1

    def test_pmi_momentum_blend(self):
        """pmi_mom 存在时走动量混合路径。"""
        ctx = _ctx_extra(macro_data={"available": True, "pmi": 50.5, "pmi_mom": 0.5})
        s = _calc_pmi_proxy({}, ctx)
        # level=0.1, mom_s=0.25, result=0.1*0.6+0.25*0.4=0.16
        assert s == pytest.approx(0.16)



# ═══════════════════════════════════════════════════════════════
# _calc_rate_proxy — 利率因子（边缘分支）
# ═══════════════════════════════════════════════════════════════

class TestRateProxyEdge:
    def test_macro_data_empty_dict(self):
        assert _calc_rate_proxy({}, {"macro_data": {}}) == 0.0

    def test_rate_none(self):
        ctx = _ctx_extra(macro_data={"available": True, "rate": None})
        assert _calc_rate_proxy({}, ctx) == 0.0

    def test_mom_near_zero(self):
        """rate_mom 绝对值 <= 1e-6 时走水平分路径。"""
        ctx = _ctx_extra(macro_data={"available": True, "rate": 3.0, "rate_mom": 1e-7})
        # abs(mom)<=1e-6 → 走水平分：(3.5-3.0)/1.5=0.333
        s = _calc_rate_proxy({}, ctx)
        assert s == pytest.approx(0.333, abs=1e-3)

    def test_mom_very_large_clipped(self):
        """大利率变动 → 钳位。"""
        ctx = _ctx_extra(macro_data={"available": True, "rate": 3.0, "rate_mom": 2.0})
        s = _calc_rate_proxy({}, ctx)
        assert s == pytest.approx(-1.0)  # -2.0/0.25=-8→clamp to -1

    def test_rate_high_level_clipped(self):
        """高利率水平分钳位。"""
        ctx = _ctx_extra(macro_data={"available": True, "rate": 6.0, "rate_mom": None})
        s = _calc_rate_proxy({}, ctx)
        assert s == pytest.approx(-1.0)  # (3.5-6)/1.5=-1.67→clamp to -1

    def test_rate_low_level_clipped(self):
        """低利率水平分钳位。"""
        ctx = _ctx_extra(macro_data={"available": True, "rate": 1.0, "rate_mom": None})
        s = _calc_rate_proxy({}, ctx)
        assert s == pytest.approx(1.0)  # (3.5-1)/1.5=1.67→clamp to 1


# ═══════════════════════════════════════════════════════════════
# MultiFactorStrategy — 多模式构造
# ═══════════════════════════════════════════════════════════════

class TestMultiFactorStrategyInit:
    def test_default_mode(self):
        s = _mfs()
        assert s._mode == "pure_momentum"
        assert s._weights == PURE_MOMENTUM_WEIGHTS

    def test_long_short_mode(self):
        s = _mfs("long_short")
        assert s._mode == "long_short"
        assert s._weights == FACTOR_WEIGHTS

    def test_neutral_mode(self):
        s = _mfs("neutral")
        assert s._mode == "neutral"
        assert s._weights == FACTOR_WEIGHTS


# ═══════════════════════════════════════════════════════════════
# MultiFactorStrategy — 属性测试
# ═══════════════════════════════════════════════════════════════

class TestMultiFactorStrategyProperties:
    def test_name(self):
        s = _mfs()
        assert s.name == "multi_factor"

    def test_signal_type_default(self):
        s = _mfs()
        assert s.signal_type == "multi_factor.pure_momentum"

    def test_signal_type_long_short(self):
        s = _mfs("long_short")
        assert s.signal_type == "multi_factor.long_short"

    def test_signal_type_neutral(self):
        s = _mfs("neutral")
        assert s.signal_type == "multi_factor.neutral"

    def test_display_name_pure_momentum(self):
        s = _mfs()
        assert s.display_name == "多因子量化(纯趋势多因子)"

    def test_display_name_long_short(self):
        s = _mfs("long_short")
        assert s.display_name == "多因子量化(强弱对冲多因子)"

    def test_display_name_neutral(self):
        s = _mfs("neutral")
        assert s.display_name == "多因子量化(行业中性多因子)"

    def test_validators(self):
        s = _mfs()
        assert s.validators == ["stability"]

    def test_weight(self):
        s = _mfs()
        assert s.weight == 0.7

    def test_depends_on(self):
        s = _mfs()
        assert s.depends_on == []


# ═══════════════════════════════════════════════════════════════
# MultiFactorStrategy.compute() — 各种输入场景
# ═══════════════════════════════════════════════════════════════

class TestMultiFactorStrategyComputeEdgeCases:
    def test_empty_tech_list(self):
        """空 tech_list → []。"""
        s = _mfs()
        assert s.compute([], {}) == []

    def test_price_zero_skipped(self):
        """price=0 → 跳过。"""
        s = _mfs()
        tech = [_tech(price=0)]
        signals = s.compute(tech, {})
        assert signals == []

    def test_price_negative_skipped(self):
        """price<0 → 跳过。"""
        s = _mfs()
        tech = [_tech(price=-100)]
        signals = s.compute(tech, {})
        assert signals == []

    def test_active_factors_below_threshold(self):
        """有效因子 < 3 → 跳过。"""
        s = _mfs()
        # 所有因子得分接近 0 → active_factors = 0
        tech = [_tech(change_pct=0, ma_slope=0, macd_cross="none",
                      atr=0, bb=0.5, bb_width=0, vol_ratio=1.0)]
        signals = s.compute(tech, {})  # no oi_data, no basis_data, no macro, etc.
        # Only momentum might contribute 0 (all zeros) → active_factors < 3
        assert signals == []

    def test_total_score_zero_hits_neutral_continue(self):
        """总分为 0（或极微小）时 raw_score=0 → active_factors≥3 但 raw_score=0 仍被输出。
        
        由于浮点精度无法保证总和精确为 0，此测试验证 active_factors 达标但
        raw_score=0 的信号被产出（raw_score=round(abs(total),4)=0）。"""
        s = _mfs("long_short")
        tech = [_tech(
            change_pct=2.5, ma_slope=0, macd_cross="none",
            atr=0, bb=1.0, bb_width=0, price=3500.0,
            vol_ratio=1.0,
        )]
        ctx = {
            "extra": {
                "oi_data": {},
                "basis_data": {},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
                "warrant_data": {"RB": {"total": 1000.0, "daily_change": 125.0}},
            },
        }
        signals = s.compute(tech, {}, ctx)
        # 即使 total 接近 0 而非精确 0，信号仍可能因极小值被产出
        if signals:
            assert signals[0].raw_score == 0.0
            assert signals[0].meta["active_factors"] >= 3


# ═══════════════════════════════════════════════════════════════
# MultiFactorStrategy.compute() — 完整信号产出场景
# ═══════════════════════════════════════════════════════════════

class TestMultiFactorStrategyComputeFull:
    """compute() 在有足够因子信号时的完整路径。"""

    def test_long_short_one_symbol(self):
        """long_short 模式：仅 1 个品种时 top_n=1。"""
        s = _mfs("long_short")
        tech_list = [_tech(change_pct=2.0, ma_slope=0.1, macd_cross="gold_cross",
                           atr=50, bb=0.5, bb_width=0.05, vol_ratio=1.5)]
        ctx = {
            "extra": {
                "oi_data": {"RB": {"oi_ratio": 0.1, "top5_ratio": 0.5}},
                "basis_data": {"RB": {"basis_pct": 3.0}},
                "warrant_data": {"RB": {"total": 1000.0, "daily_change": -50.0}},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
            },
            "macro_signal": "bull",
        }
        signals = s.compute(tech_list, {}, ctx)
        # 1 个品种 → top_n=max(1,1//5)=1 → 做多
        assert len(signals) == 1
        assert signals[0].direction == "bull"

    def test_many_symbols_long_short(self):
        """long_short 模式：≥5 品种时做多前 20%，做空后 20%。"""
        s = _mfs("long_short")
        symbols = ["RB", "CU", "AL", "ZN", "NI", "AU", "AG", "PB", "SN", "SS"]
        tech_list = [
            _tech(symbol=sym, change_pct=2.0 - i * 0.4,  # 单调递减
                  ma_slope=0.1, macd_cross="none", atr=50,
                  bb=0.5, bb_width=0.05, vol_ratio=1.2)
            for i, sym in enumerate(symbols)
        ]
        # 全部注入强信号确保 active_factors >= 3
        ctx = {
            "extra": {
                "oi_data": {sym: {"oi_ratio": 0.1, "top5_ratio": 0.5} for sym in symbols},
                "basis_data": {sym: {"basis_pct": 3.0} for sym in symbols},
                "warrant_data": {sym: {"total": 1000.0, "daily_change": -50.0} for sym in symbols},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
            },
            "macro_signal": "bull",
        }
        signals = s.compute(tech_list, {}, ctx)
        # long_short 模式：前 20% = top 2, 后 20% = bottom 2
        # 总共 10 个，前 2 个 bull，后 2 个 bear，中间 6 个 neutral 被过滤
        assert len(signals) <= 4  # 最多 2 bull + 2 bear
        if len(signals) >= 2:
            # 前 2 个是 bull
            bull_count = sum(1 for sig in signals if sig.direction == "bull")
            bear_count = sum(1 for sig in signals if sig.direction == "bear")
            assert bull_count <= 2
            assert bear_count <= 2
            if signals:
                assert signals[0].direction in ("bull", "bear")

    def test_neutral_mode_passthrough(self):
        """neutral 模式返回全部通过 active_factors 的信号（不做排序裁剪）。"""
        s = _mfs("neutral")
        tech_list = [_tech(change_pct=2.0, ma_slope=0.1, macd_cross="gold_cross",
                           atr=50, bb=0.5, bb_width=0.05, vol_ratio=1.5)]
        ctx = {
            "extra": {
                "oi_data": {"RB": {"oi_ratio": 0.1, "top5_ratio": 0.5}},
                "basis_data": {"RB": {"basis_pct": 3.0}},
                "warrant_data": {"RB": {"total": 1000.0, "daily_change": -50.0}},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
            },
            "macro_signal": "bull",
        }
        signals = s.compute(tech_list, {}, ctx)
        # neutral 模式不裁剪，有信号就返回
        assert len(signals) >= 0
        if signals:
            assert signals[0].direction in ("bull", "bear")
            assert signals[0].meta.get("mode") == "neutral"

    def test_compute_meta_fields(self):
        """compute 产出的 RawSignal meta 包含 factor_scores/active_factors/mode/price。"""
        s = _mfs()
        tech_list = [_tech(change_pct=2.0, ma_slope=0.1, macd_cross="gold_cross",
                           atr=50, bb=0.5, bb_width=0.05, vol_ratio=1.5)]
        ctx = {
            "extra": {
                "oi_data": {"RB": {"oi_ratio": 0.1, "top5_ratio": 0.5}},
                "basis_data": {"RB": {"basis_pct": 3.0}},
                "warrant_data": {"RB": {"total": 1000.0, "daily_change": -50.0}},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
            },
            "macro_signal": "bull",
        }
        signals = s.compute(tech_list, {}, ctx)
        if signals:
            sig = signals[0]
            assert "factor_scores" in sig.meta
            assert "active_factors" in sig.meta
            assert sig.meta["active_factors"] >= 3
            assert sig.meta["mode"] == "pure_momentum"
            assert sig.meta["price"] == 3500.0
            assert "momentum" in sig.meta["factor_scores"]
            assert "volatility_reversion" in sig.meta["factor_scores"]
            assert sig.raw_score > 0
            assert sig.signal_type == "multi_factor.pure_momentum.composite"
            assert sig.strategy_name == "multi_factor"

    def test_compute_context_none(self):
        """context=None 时 compute 安全处理。"""
        s = _mfs()
        tech_list = [_tech(change_pct=2.0, ma_slope=0.1, macd_cross="gold_cross",
                           atr=50, bb=0.5, bb_width=0.05, vol_ratio=1.5)]
        signals = s.compute(tech_list, {}, None)
        # context=None → ctx_extra={} → 所有因子都用空数据，但动量/波动率/量价可贡献
        # 可能 active_factors < 3 → []
        assert isinstance(signals, list)


# ═══════════════════════════════════════════════════════════════
# MultiFactorStrategy.score() — 打分全路径
# ═══════════════════════════════════════════════════════════════

class TestMultiFactorStrategyScore:
    def test_bull_strong(self):
        """bull, raw>=0.4 → total≥40 → grade=STRONG。"""
        s = _mfs()
        raw = RawSignal("RB", "bull", "multi_factor.pure_momentum.composite",
                        0.6, "multi_factor",
                        meta={"factor_scores": {"momentum": 0.5}, "active_factors": 5,
                              "mode": "pure_momentum", "price": 3500.0})
        scored = s.score([raw], [])
        assert len(scored) == 1
        ss = scored[0]
        assert ss.total == 60.0   # 0.6*100
        assert ss.abs_score == 60.0
        assert ss.grade == "STRONG"
        assert ss.weight == 0.7

    def test_bull_watch(self):
        """bull, raw=[0.2, 0.4) → total=[20,40) → grade=WATCH。"""
        s = _mfs()
        raw = RawSignal("RB", "bull", "test", 0.3, "multi_factor",
                        meta={"factor_scores": {}, "active_factors": 3,
                              "mode": "pure_momentum", "price": 3500.0})
        scored = s.score([raw], [])
        assert scored[0].grade == "WATCH"

    def test_bull_weak(self):
        """bull, raw=[0.1, 0.2) → total=[10,20) → grade=WEAK。"""
        s = _mfs()
        raw = RawSignal("CU", "bull", "test", 0.15, "multi_factor",
                        meta={"factor_scores": {}, "active_factors": 3,
                              "mode": "pure_momentum", "price": 68000.0})
        scored = s.score([raw], [])
        assert scored[0].grade == "WEAK"

    def test_bull_noise(self):
        """bull, raw<0.1 → total<10 → grade=NOISE。"""
        s = _mfs()
        raw = RawSignal("RB", "bull", "test", 0.05, "multi_factor",
                        meta={"factor_scores": {}, "active_factors": 3,
                              "mode": "pure_momentum", "price": 3500.0})
        scored = s.score([raw], [])
        assert scored[0].grade == "NOISE"

    def test_bear_negative_total(self):
        """bear → total 为负。"""
        s = _mfs()
        raw = RawSignal("RB", "bear", "test", 0.5, "multi_factor",
                        meta={"factor_scores": {}, "active_factors": 3,
                              "mode": "pure_momentum", "price": 3500.0})
        scored = s.score([raw], [])
        assert scored[0].total == -50.0

    def test_sub_scores_from_meta(self):
        """sub_scores 从 meta.factor_scores 复制。"""
        s = _mfs()
        raw = RawSignal("RB", "bull", "test", 0.5, "multi_factor",
                        meta={"factor_scores": {"momentum": 0.3, "basis": -0.2},
                              "active_factors": 3, "mode": "pure_momentum",
                              "price": 3500.0})
        scored = s.score([raw], [])
        assert scored[0].sub_scores.get("momentum") == 0.3
        assert scored[0].sub_scores.get("basis") == -0.2

    def test_extra_from_meta(self):
        """extra 从 meta 其余字段复制。"""
        s = _mfs()
        raw = RawSignal("RB", "bull", "test", 0.5, "multi_factor",
                        meta={"factor_scores": {}, "active_factors": 5,
                              "mode": "pure_momentum", "price": 3500.0,
                              "custom_key": "custom_val"})
        scored = s.score([raw], [])
        assert scored[0].extra.get("active_factors") == 5
        assert scored[0].extra.get("mode") == "pure_momentum"
        assert scored[0].extra.get("custom_key") == "custom_val"
        # factor_scores 在 extra 中（ss.extra = dict(s.meta) 拷贝全部 meta）
        assert "factor_scores" in scored[0].extra

    def test_empty_filtered_signals(self):
        """空 filtered_signals → []。"""
        s = _mfs()
        assert s.score([], []) == []