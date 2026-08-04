"""
tests/scenarios/definitions.py — 宏观行为场景定义

HARNESS §11-logic-review-plan.md §A.2:
    定义 20 个典型市场片段，每个场景包含：
    - 市场状态描述
    - 输入数据生成函数
    - 期望行为
    - 容差范围

场景类型:
    1. 趋势类          — 连续上涨/下跌/冲高回落
    2. 反转类          — 超买超卖/均值回归
    3. 流动性类        — 低流动性/放量异动
    4. 事件类          — 跳空/换月/跳空缺口
    5. 震荡类          — 横盘/窄幅波动/高波动率
    6. 期货特有        — 换月日/基差/升贴水
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd


@dataclass
class ScenarioDefinition:
    """单个场景定义。

    Attributes:
        name: 场景名称（唯一标识）
        description: 场景描述
        category: 场景分类（trend/reversal/liquidity/event/oscillation/futures）
        generate_data: 生成场景数据的函数，返回 (data, metadata)
        expected_behavior: 期望行为描述
        expected_signal_range: 期望信号范围 (min, max)，None 表示不检查
        check_fn: 自定义验证函数，接收 (signal, metadata) 返回 (passed, message)
        tolerance: 容差（用于数值比较）
    """
    name: str
    description: str
    category: str
    generate_data: Callable[[], tuple[pd.DataFrame, dict]]
    expected_behavior: str
    expected_signal_range: Optional[tuple[float, float]] = None
    check_fn: Optional[Callable[[np.ndarray, dict], tuple[bool, str]]] = None
    tolerance: float = 0.05


# ─── 场景数据生成函数 ─────────────────────────────────────


def _gen_trend_up() -> tuple[pd.DataFrame, dict]:
    """连续上涨趋势（5 日涨幅 > 5%）。"""
    np.random.seed(101)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.linspace(0, 8, n) + np.random.randn(n) * 0.3
    volume = np.random.randint(5000, 15000, n).astype(float)
    # 最后 5 天放量上涨
    volume[-5:] *= 2
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"lookback_5d_return": (close[-1] - close[-6]) / close[-6]}


def _gen_trend_down() -> tuple[pd.DataFrame, dict]:
    """连续下跌趋势（5 日跌幅 > 5%）。"""
    np.random.seed(102)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 - np.linspace(0, 6, n) + np.random.randn(n) * 0.3
    volume = np.random.randint(5000, 15000, n).astype(float)
    # 最后 5 天放量下跌
    volume[-5:] *= 1.5
    data = pd.DataFrame({
        "open": close + 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"lookback_5d_return": (close[-1] - close[-6]) / close[-6]}


def _gen_sudden_gap_down() -> tuple[pd.DataFrame, dict]:
    """突发利空 — 价格跳空低开（当日开盘 < 前日收盘 2%）。"""
    np.random.seed(103)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.5
    # 某一天跳空低开 2.5%
    gap_day = 70
    open_gap = close[gap_day - 1] * 0.975
    close[gap_day] = open_gap + np.random.randn() * 0.2
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": np.random.randint(3000, 12000, n).astype(float),
    }, index=dates)
    data.iloc[gap_day, data.columns.get_loc("open")] = open_gap
    return data, {"gap_day": gap_day, "gap_pct": (open_gap - close[gap_day - 1]) / close[gap_day - 1]}


def _gen_sudden_gap_up() -> tuple[pd.DataFrame, dict]:
    """突发利好 — 价格跳空高开（当日开盘 > 前日收盘 2%）。"""
    np.random.seed(104)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.5
    gap_day = 65
    open_gap = close[gap_day - 1] * 1.025
    close[gap_day] = open_gap + np.random.randn() * 0.2
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": np.random.randint(3000, 12000, n).astype(float),
    }, index=dates)
    data.iloc[gap_day, data.columns.get_loc("open")] = open_gap
    return data, {"gap_day": gap_day, "gap_pct": (open_gap - close[gap_day - 1]) / close[gap_day - 1]}


def _gen_low_liquidity() -> tuple[pd.DataFrame, dict]:
    """低流动性品种（成交量 < 10 分位）。"""
    np.random.seed(105)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.5
    volume = np.random.randint(50, 200, n).astype(float)  # 极低成交量
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.2,
        "low": close - np.abs(np.random.randn(n)) * 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"volume_pct": np.percentile(volume, 10)}


def _gen_high_volume_spike() -> tuple[pd.DataFrame, dict]:
    """放量异动（成交量 > 均值 3 倍）。"""
    np.random.seed(106)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    volume = np.random.randint(3000, 8000, n).astype(float)
    # 某天成交量突然放大 4 倍
    spike_day = 80
    volume[spike_day] = np.mean(volume) * 4
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"spike_day": spike_day, "volume_ratio": volume[spike_day] / np.mean(volume)}


def _gen_consolidation() -> tuple[pd.DataFrame, dict]:
    """横盘震荡（20 日波动率 < 20 分位）。"""
    np.random.seed(107)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.15  # 极窄波动
    volume = np.random.randint(3000, 8000, n).astype(float)
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.05,
        "high": close + np.abs(np.random.randn(n)) * 0.1,
        "low": close - np.abs(np.random.randn(n)) * 0.1,
        "close": close, "volume": volume,
    }, index=dates)
    vol_20 = np.std(close[-20:]) / np.mean(close[-20:])
    return data, {"volatility_20d": vol_20}


def _gen_high_volatility() -> tuple[pd.DataFrame, dict]:
    """高波动率（20 日波动率 > 80 分位）。"""
    np.random.seed(108)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 1.5)  # 高波动
    volume = np.random.randint(5000, 20000, n).astype(float)
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.5,
        "high": close + np.abs(np.random.randn(n)) * 1.0,
        "low": close - np.abs(np.random.randn(n)) * 1.0,
        "close": close, "volume": volume,
    }, index=dates)
    vol_20 = np.std(close[-20:]) / np.mean(close[-20:])
    return data, {"volatility_20d": vol_20}


def _gen_rally_fade() -> tuple[pd.DataFrame, dict]:
    """冲高回落（日内大幅冲高后回落）。"""
    np.random.seed(109)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.5
    # 最后一天冲高回落
    close[-1] = close[-2] * 1.01  # 收盘微涨
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": np.random.randint(3000, 12000, n).astype(float),
    }, index=dates)
    # 最后一天 high 显著高于 close
    data.iloc[-1, data.columns.get_loc("high")] = close[-2] * 1.03
    return data, {"intraday_range_pct": (data["high"].iloc[-1] - close[-1]) / close[-1]}


def _gen_mean_reversion_overbought() -> tuple[pd.DataFrame, dict]:
    """超买（连续 5 日涨幅 > 8%）— 预期均值回归。"""
    np.random.seed(110)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.3
    # 最后 5 天快速拉升
    close[-5:] = close[-6] * np.cumprod(1 + np.array([0.015, 0.02, 0.018, 0.016, 0.012]))
    volume = np.random.randint(5000, 15000, n).astype(float)
    volume[-5:] *= 1.5
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"5d_return": (close[-1] - close[-6]) / close[-6]}


def _gen_mean_reversion_oversold() -> tuple[pd.DataFrame, dict]:
    """超卖（连续 5 日跌幅 > 8%）— 预期均值回归。"""
    np.random.seed(111)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.3
    close[-5:] = close[-6] * np.cumprod(1 - np.array([0.015, 0.025, 0.02, 0.018, 0.01]))
    volume = np.random.randint(5000, 15000, n).astype(float)
    volume[-5:] *= 1.3
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"5d_return": (close[-1] - close[-6]) / close[-6]}


def _gen_volume_divergence() -> tuple[pd.DataFrame, dict]:
    """价量背离（价格上涨但成交量递减）。"""
    np.random.seed(112)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.linspace(0, 5, n) + np.random.randn(n) * 0.3
    volume = np.linspace(10000, 2000, n).astype(float) + np.random.randn(n) * 500
    volume = np.clip(volume, 1000, 20000)
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"price_trend": "up", "volume_trend": "down"}


def _gen_breakout_with_volume() -> tuple[pd.DataFrame, dict]:
    """放量突破（价格突破前高 + 成交量放大）。"""
    np.random.seed(113)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.5
    # 前 80 天横盘，后 20 天突破
    close[:80] = 100 + np.random.randn(80) * 0.3
    close[80:] = 102 + np.linspace(0, 3, 20) + np.random.randn(20) * 0.2
    volume = np.random.randint(3000, 8000, n).astype(float)
    volume[80:] *= 2  # 突破放量
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"breakout_pct": (close[-1] - np.max(close[:80])) / np.max(close[:80])}


def _gen_head_and_shoulders() -> tuple[pd.DataFrame, dict]:
    """头肩顶形态（左肩→头→右肩）— 预期转空。"""
    np.random.seed(114)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    # 头肩顶：左肩 25-35, 头 45-55, 右肩 70-80
    base = 100
    close = np.zeros(n)
    close[:25] = base + np.linspace(0, 3, 25) + np.random.randn(25) * 0.3
    close[25:35] = base + 3 - np.linspace(0, 2, 10) + np.random.randn(10) * 0.3  # 左肩回落
    close[35:45] = base + 2 + np.linspace(0, 5, 10) + np.random.randn(10) * 0.3  # 头
    close[45:55] = base + 5 - np.linspace(0, 3, 10) + np.random.randn(10) * 0.3  # 头回落
    close[55:70] = base + 3 + np.linspace(0, 2, 15) + np.random.randn(15) * 0.3  # 右肩
    close[70:80] = base + 4 - np.linspace(0, 2, 10) + np.random.randn(10) * 0.3  # 右肩回落
    close[80:] = base + 2 - np.linspace(0, 1, 20) + np.random.randn(20) * 0.3  # 破位
    volume = np.random.randint(4000, 10000, n).astype(float)
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"pattern": "head_and_shoulders"}


def _gen_double_bottom() -> tuple[pd.DataFrame, dict]:
    """W 底形态（双重底）— 预期转多。"""
    np.random.seed(115)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    base = 100
    close = np.zeros(n)
    close[:20] = base - np.linspace(0, 3, 20) + np.random.randn(20) * 0.3  # 第一次下跌
    close[20:35] = base - 2 + np.linspace(0, 2, 15) + np.random.randn(15) * 0.3  # 反弹
    close[35:55] = base - 1 - np.linspace(0, 2, 20) + np.random.randn(20) * 0.3  # 二次探底
    close[55:70] = base - 2 + np.linspace(0, 3, 15) + np.random.randn(15) * 0.3  # 突破
    close[70:] = base + 2 + np.linspace(0, 2, 30) + np.random.randn(30) * 0.3  # 上涨
    volume = np.random.randint(4000, 10000, n).astype(float)
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"pattern": "double_bottom"}


def _gen_futures_rollover() -> tuple[pd.DataFrame, dict]:
    """期货换月日（主力合约切换日附近）。"""
    np.random.seed(116)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    # 换月日附近（第 50 天）
    roll_day = 50
    close[roll_day - 5:roll_day + 5] += np.random.randn(10) * 0.5  # 换月波动
    volume = np.random.randint(5000, 15000, n).astype(float)
    volume[roll_day - 3:roll_day + 3] *= 2  # 换月放量
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"roll_day": roll_day}


def _gen_basis_widening() -> tuple[pd.DataFrame, dict]:
    """基差扩大（期货贴水/升水加剧）。"""
    np.random.seed(117)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.2)
    spot = close + np.random.randn(n) * 0.1
    basis = -np.linspace(0, 3, n) + np.random.randn(n) * 0.1  # 贴水持续扩大
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": np.random.randint(3000, 10000, n).astype(float),
        "spot": spot, "basis": basis,
    }, index=dates)
    return data, {"basis_start": basis[0], "basis_end": basis[-1]}


def _gen_vwap_volume_manipulation() -> tuple[pd.DataFrame, dict]:
    """成交量操控（尾盘巨量成交影响 VWAP）。"""
    np.random.seed(118)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.3
    volume = np.random.randint(3000, 8000, n).astype(float)
    # 某天尾盘巨量成交推高 VWAP
    manip_day = 75
    volume[manip_day] = np.mean(volume) * 5
    # 用 amount 近似 VWAP
    amount = close * volume
    amount[manip_day] = close[manip_day] * volume[manip_day] * 1.02  # 成交价偏高
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume, "amount": amount,
        "vwap": amount / np.maximum(volume, 1),
    }, index=dates)
    return data, {"manip_day": manip_day}


def _gen_gap_fill() -> tuple[pd.DataFrame, dict]:
    """缺口回补（跳空后逐步回补缺口）。"""
    np.random.seed(119)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.3
    gap_day = 60
    # 跳空上涨
    gap_up = close[gap_day - 1] * 1.03
    close[gap_day] = gap_up
    # 随后逐步回落回补缺口
    close[gap_day + 1:gap_day + 10] = np.linspace(gap_up, close[gap_day - 1] * 1.005, 9)
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": np.random.randint(3000, 10000, n).astype(float),
    }, index=dates)
    return data, {"gap_day": gap_day, "gap_fill_days": 10}


def _gen_gradual_recovery() -> tuple[pd.DataFrame, dict]:
    """缓慢修复（大跌后缓慢回升）。"""
    np.random.seed(120)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    crash_day = 40
    close[crash_day] = close[crash_day - 1] * 0.95  # 单日大跌 5%
    close[crash_day + 1:] = close[crash_day] + np.linspace(0, 4, n - crash_day - 1) + np.random.randn(n - crash_day - 1) * 0.2
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": np.random.randint(4000, 12000, n).astype(float),
    }, index=dates)
    return data, {"crash_day": crash_day, "recovery_pct": (close[-1] - close[crash_day]) / close[crash_day]}


def _gen_sideways_with_volume_surge() -> tuple[pd.DataFrame, dict]:
    """横盘放量（价格不动但成交量异常放大）— 可能积累/派发。"""
    np.random.seed(121)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.2  # 极窄波动
    volume = np.random.randint(3000, 6000, n).astype(float)
    # 中间一段放量
    volume[40:60] = np.random.randint(10000, 20000, 20).astype(float)
    data = pd.DataFrame({
        "open": close - 0.05, "high": close + 0.15, "low": close - 0.15,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"volume_surge_ratio": np.mean(volume[40:60]) / np.mean(volume[:40])}


def _gen_momentum_exhaustion() -> tuple[pd.DataFrame, dict]:
    """动量衰竭（连续大涨后涨速放缓）。"""
    np.random.seed(122)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = np.zeros(n)
    close[:50] = 100 + np.linspace(0, 10, 50) + np.random.randn(50) * 0.3  # 快速上涨
    close[50:] = 110 + np.linspace(0, 1, 50) + np.random.randn(50) * 0.2  # 涨速放缓
    volume = np.random.randint(5000, 15000, n).astype(float)
    volume[50:] *= 0.7  # 缩量
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"early_slope": (close[49] - close[0]) / 49, "late_slope": (close[-1] - close[50]) / 49}


def _gen_multi_peak_reversal() -> tuple[pd.DataFrame, dict]:
    """多重顶（三次冲击同一压力位失败）— 预期转空。"""
    np.random.seed(123)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = np.zeros(n)
    close[:20] = 95 + np.linspace(0, 5, 20) + np.random.randn(20) * 0.3
    close[20:35] = 100 - np.linspace(0, 3, 15) + np.random.randn(15) * 0.3
    close[35:50] = 97 + np.linspace(0, 3, 15) + np.random.randn(15) * 0.3  # 二次触顶 100
    close[50:65] = 100 - np.linspace(0, 2, 15) + np.random.randn(15) * 0.3
    close[65:80] = 98 + np.linspace(0, 2, 15) + np.random.randn(15) * 0.3  # 三次触顶
    close[80:] = 100 - np.linspace(0, 4, 20) + np.random.randn(20) * 0.3  # 破位下跌
    volume = np.random.randint(4000, 10000, n).astype(float)
    data = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.3, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"pattern": "triple_top"}


def _gen_gradual_accumulation() -> tuple[pd.DataFrame, dict]:
    """缓慢吸筹（价格微涨 + 成交量温和放大）。"""
    np.random.seed(124)
    n = 100
    dates = pd.date_range("2024-06-01", periods=n, freq="D")
    close = 100 + np.linspace(0, 3, n) + np.random.randn(n) * 0.2
    volume = np.linspace(3000, 12000, n).astype(float) + np.random.randn(n) * 500
    volume = np.clip(volume, 2000, 15000)
    data = pd.DataFrame({
        "open": close - 0.05, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": volume,
    }, index=dates)
    return data, {"price_change_pct": (close[-1] - close[0]) / close[0]}


# ─── 自定义验证函数 ────────────────────────────────────────


def _check_mean_reversion(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """超买超卖场景：信号应反转（与近期趋势方向相反）。"""
    ret_5d = metadata.get("5d_return", 0)
    last_signal = signal[-1] if len(signal) > 0 else 0
    # 超买（涨幅 > 8%）时信号应 ≤ 0（做空倾向）
    # 超卖（跌幅 > 8%）时信号应 ≥ 0（做多倾向）
    if ret_5d > 0.08:
        passed = last_signal <= 0.3
        return passed, f"超买信号={last_signal:.4f}, 期望≤0.3"
    elif ret_5d < -0.08:
        passed = last_signal >= -0.3
        return passed, f"超卖信号={last_signal:.4f}, 期望≥-0.3"
    return True, "无需检查"


def _check_breakout(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """放量突破场景：信号应偏正（动量信号）。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    passed = last_signal > -0.5
    return passed, f"突破信号={last_signal:.4f}, 期望>-0.5"


def _check_gap_down(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """跳空低开场景：信号应转负或降低。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    gap_day = metadata.get("gap_day", 0)
    if gap_day > 0 and gap_day < len(signal):
        pre_gap_signal = signal[gap_day - 1] if gap_day > 0 else 0
        post_gap_signal = signal[gap_day]
        # 跳空后信号应下降
        passed = post_gap_signal <= pre_gap_signal + 0.3
        return passed, f"跳空前={pre_gap_signal:.4f}, 跳空后={post_gap_signal:.4f}"
    return True, "无需检查"


def _check_gap_up(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """跳空高开场景：信号应转正或升高。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    gap_day = metadata.get("gap_day", 0)
    if gap_day > 0 and gap_day < len(signal):
        pre_gap_signal = signal[gap_day - 1] if gap_day > 0 else 0
        post_gap_signal = signal[gap_day]
        passed = post_gap_signal >= pre_gap_signal - 0.3
        return passed, f"跳空前={pre_gap_signal:.4f}, 跳空后={post_gap_signal:.4f}"
    return True, "无需检查"


def _check_rollover_stability(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """换月日场景：信号不应剧烈突变。"""
    roll_day = metadata.get("roll_day", 0)
    if roll_day > 0 and roll_day < len(signal) - 1:
        before = signal[roll_day - 1] if roll_day > 0 else 0
        after = signal[roll_day + 1] if roll_day + 1 < len(signal) else 0
        diff = abs(after - before)
        passed = diff < 0.5
        return passed, f"换月日前信号={before:.4f}, 后={after:.4f}, 差={diff:.4f}"
    return True, "无需检查"


def _check_low_liquidity(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """低流动性场景：信号绝对值应偏低。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    passed = abs(last_signal) < 0.6
    return passed, f"低流动性信号={last_signal:.4f}, 期望|信号|<0.6"


def _check_sideways(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """横盘震荡场景：信号应接近 0。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    passed = abs(last_signal) < 0.4
    return passed, f"横盘信号={last_signal:.4f}, 期望|信号|<0.4"


def _check_volume_divergence(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """价量背离场景：信号应偏谨慎。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    # 价升量缩 — 信号不应过于积极
    passed = last_signal < 0.7
    return passed, f"价量背离信号={last_signal:.4f}, 期望<0.7"


def _check_pattern_reversal(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """头肩顶/多重顶场景：后期信号应偏空。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    passed = last_signal < 0.5
    return passed, f"顶部形态信号={last_signal:.4f}, 期望<0.5"


def _check_pattern_recovery(signal: np.ndarray, metadata: dict) -> tuple[bool, str]:
    """W 底/缓慢修复场景：后期信号应偏多。"""
    last_signal = signal[-1] if len(signal) > 0 else 0
    passed = last_signal > -0.5
    return passed, f"底部形态信号={last_signal:.4f}, 期望>-0.5"


# ─── 全部场景列表 ─────────────────────────────────────────

ALL_SCENARIOS: list[ScenarioDefinition] = [
    # ── 趋势类 ──
    ScenarioDefinition(
        name="trend_up",
        description="连续上涨趋势（5 日涨幅 > 5%），放量上涨",
        category="trend",
        generate_data=_gen_trend_up,
        expected_behavior="信号应偏正（动量因子）或回落（均值回归因子），但不能无变化",
        expected_signal_range=(-0.3, 1.0),
    ),
    ScenarioDefinition(
        name="trend_down",
        description="连续下跌趋势（5 日跌幅 > 5%），放量下跌",
        category="trend",
        generate_data=_gen_trend_down,
        expected_behavior="信号应偏负（动量因子）或回升（均值回归因子），但不能无变化",
        expected_signal_range=(-1.0, 0.3),
    ),
    ScenarioDefinition(
        name="momentum_exhaustion",
        description="动量衰竭 — 连续大涨后涨速明显放缓，成交量萎缩",
        category="trend",
        generate_data=_gen_momentum_exhaustion,
        expected_behavior="后期信号应弱于前期，反映动量减弱",
        expected_signal_range=(-0.5, 0.8),
    ),
    # ── 反转类 ──
    ScenarioDefinition(
        name="overbought_reversal",
        description="超买 — 连续 5 日涨幅 > 8%，放量，预期均值回归",
        category="reversal",
        generate_data=_gen_mean_reversion_overbought,
        expected_behavior="信号应 ≤ 0.3（做空倾向或至少不强烈做多）",
        check_fn=_check_mean_reversion,
    ),
    ScenarioDefinition(
        name="oversold_reversal",
        description="超卖 — 连续 5 日跌幅 > 8%，预期均值回归",
        category="reversal",
        generate_data=_gen_mean_reversion_oversold,
        expected_behavior="信号应 ≥ -0.3（做多倾向或至少不强烈做空）",
        check_fn=_check_mean_reversion,
    ),
    ScenarioDefinition(
        name="rally_fade",
        description="冲高回落 — 日内大幅冲高后收盘回落",
        category="reversal",
        generate_data=_gen_rally_fade,
        expected_behavior="信号应反映冲高回落，不应过于积极",
        expected_signal_range=(-0.5, 0.7),
    ),
    # ── 流动性类 ──
    ScenarioDefinition(
        name="low_liquidity",
        description="低流动性品种 — 成交量 < 10 分位",
        category="liquidity",
        generate_data=_gen_low_liquidity,
        expected_behavior="信号绝对值应偏低",
        check_fn=_check_low_liquidity,
    ),
    ScenarioDefinition(
        name="high_volume_spike",
        description="放量异动 — 成交量 > 均值 3 倍",
        category="liquidity",
        generate_data=_gen_high_volume_spike,
        expected_behavior="放量日信号应发生变化",
        expected_signal_range=(-1.0, 1.0),
    ),
    ScenarioDefinition(
        name="volume_divergence",
        description="价量背离 — 价格上涨但成交量递减",
        category="liquidity",
        generate_data=_gen_volume_divergence,
        expected_behavior="信号应偏谨慎，不应过于积极做多",
        check_fn=_check_volume_divergence,
    ),
    ScenarioDefinition(
        name="sideways_with_volume_surge",
        description="横盘放量 — 价格不动但成交量异常放大",
        category="liquidity",
        generate_data=_gen_sideways_with_volume_surge,
        expected_behavior="信号应反映积累/派发的不确定性",
        expected_signal_range=(-0.5, 0.5),
    ),
    # ── 事件类 ──
    ScenarioDefinition(
        name="gap_down_crash",
        description="突发利空 — 跳空低开 > 2%",
        category="event",
        generate_data=_gen_sudden_gap_down,
        expected_behavior="跳空后信号应转负或明显降低",
        check_fn=_check_gap_down,
    ),
    ScenarioDefinition(
        name="gap_up_rally",
        description="突发利好 — 跳空高开 > 2%",
        category="event",
        generate_data=_gen_sudden_gap_up,
        expected_behavior="跳空后信号应转正或明显升高",
        check_fn=_check_gap_up,
    ),
    ScenarioDefinition(
        name="gap_fill",
        description="缺口回补 — 跳空上涨后逐步回落回补缺口",
        category="event",
        generate_data=_gen_gap_fill,
        expected_behavior="回补过程中信号应逐步减弱",
        expected_signal_range=(-0.5, 0.7),
    ),
    ScenarioDefinition(
        name="breakout_with_volume",
        description="放量突破 — 价格突破前高 + 成交量放大",
        category="event",
        generate_data=_gen_breakout_with_volume,
        expected_behavior="突破后信号应偏正",
        check_fn=_check_breakout,
    ),
    # ── 震荡类 ──
    ScenarioDefinition(
        name="consolidation_sideways",
        description="横盘震荡 — 20 日波动率 < 20 分位",
        category="oscillation",
        generate_data=_gen_consolidation,
        expected_behavior="信号应接近 0 或小幅波动",
        check_fn=_check_sideways,
    ),
    ScenarioDefinition(
        name="high_volatility",
        description="高波动率 — 20 日波动率 > 80 分位",
        category="oscillation",
        generate_data=_gen_high_volatility,
        expected_behavior="信号绝对值可能较大，反映市场不确定性",
        expected_signal_range=(-1.0, 1.0),
    ),
    # ── 形态类 ──
    ScenarioDefinition(
        name="head_and_shoulders",
        description="头肩顶形态 — 经典顶部反转形态",
        category="oscillation",
        generate_data=_gen_head_and_shoulders,
        expected_behavior="右肩破位后信号应偏空",
        check_fn=_check_pattern_reversal,
    ),
    ScenarioDefinition(
        name="double_bottom",
        description="W 底形态 — 经典底部反转形态",
        category="oscillation",
        generate_data=_gen_double_bottom,
        expected_behavior="突破颈线后信号应偏多",
        check_fn=_check_pattern_recovery,
    ),
    ScenarioDefinition(
        name="triple_top_reversal",
        description="多重顶 — 三次冲击同一压力位失败",
        category="oscillation",
        generate_data=_gen_multi_peak_reversal,
        expected_behavior="第三次冲击失败后信号应偏空",
        check_fn=_check_pattern_reversal,
    ),
    # ── 期货特有 ──
    ScenarioDefinition(
        name="futures_rollover",
        description="期货换月日 — 主力合约切换日附近",
        category="futures",
        generate_data=_gen_futures_rollover,
        expected_behavior="换月日附近信号不应剧烈突变",
        check_fn=_check_rollover_stability,
    ),
    ScenarioDefinition(
        name="basis_widening",
        description="基差扩大 — 期货贴水/升水加剧",
        category="futures",
        generate_data=_gen_basis_widening,
        expected_behavior="基差持续扩大时信号应反映价差变化",
        expected_signal_range=(-1.0, 1.0),
    ),
    ScenarioDefinition(
        name="vwap_volume_manipulation",
        description="成交量操控 — 尾盘巨量成交影响 VWAP",
        category="futures",
        generate_data=_gen_vwap_volume_manipulation,
        expected_behavior="操控日 vwap 相关信号应出现异常变化",
        expected_signal_range=(-1.0, 1.0),
    ),
    ScenarioDefinition(
        name="gradual_recovery",
        description="缓慢修复 — 大跌后逐步回升，修复过程缓慢",
        category="trend",
        generate_data=_gen_gradual_recovery,
        expected_behavior="修复期信号应逐步转正",
        check_fn=_check_pattern_recovery,
    ),
    ScenarioDefinition(
        name="gradual_accumulation",
        description="缓慢吸筹 — 价格微涨 + 成交量温和放大",
        category="trend",
        generate_data=_gen_gradual_accumulation,
        expected_behavior="吸筹期信号应偏正但不过于激进",
        expected_signal_range=(-0.3, 0.8),
    ),
]


__all__ = ["ALL_SCENARIOS", "ScenarioDefinition"]