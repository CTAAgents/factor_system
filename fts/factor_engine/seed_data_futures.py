"""
fts.factor_engine.seed_data_futures — 期货专用种子因子库

基于 D:/Futures/因子库/期货因子库.html 的因子分类体系，
实现期货横截面因子演化的种子因子。

因子来源:
    - 第一层（长期稳定核心池）: 8 个核心期货因子
    - 第二层（备选增强因子）: 4 个备选因子
    - 总计: 12 个期货专用种子因子

数据字段:
    - DuckDB kline_cache: date, open, high, low, close, volume, hold(持仓量), settle

HARNESS §契约优先: 每个因子符合 FactorProgram 接口。

版本: v1.0.0
"""
from __future__ import annotations

from typing import Any, Optional

from .contracts import EconomicLogic, FactorProgram, FactorSignature
from .factor_program import create_factor_program


# ─── 期货因子代码模板 ─────────────────────────────────────

# 以下因子均使用期货数据字段: close, open, high, low, volume, hold(持仓量)
# 每个因子返回 [-1, 1] 的信号值，正值=做多，负值=做空

_FUT_ROLL_YIELD_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 5:
        return np.zeros(n)
    # 用不同周期的移动平均近似期限结构
    # 短期均线 ≈ 近月，长期均线 ≈ 远月
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_long = np.convolve(close, np.ones(window)/window, mode='same')
    # 展期收益率 ≈ (短均线 - 长均线) / 长均线
    roll_yield = (ma_short - ma_long) / np.maximum(ma_long, 1e-10)
    # Back结构(正展期收益) → 做多；Contango(负展期收益) → 做空
    score = np.clip(roll_yield, -0.1, 0.1) / 0.1
    return np.clip(score, -1.0, 1.0)
"""

_FUT_MOMENTUM_60D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 60))
    if n < window:
        return np.zeros(n)
    # 60日收益率
    ret = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    ret[:window] = 0
    # 动量信号
    score = np.tanh(ret / 0.05)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_REVERSAL_5D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window:
        return np.zeros(n)
    # 5日收益率 → 短期反转
    ret = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    ret[:window] = 0
    # 反转因子：做多跌多的，做空涨多的
    score = -np.tanh(ret / 0.02)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_VOLATILITY_20D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window:
        return np.zeros(n)
    # 日收益率
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 20日滚动波动率
    vol = np.array([
        np.std(returns[max(0, i-window+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    # 波动率均值回归：高波动 → 做空(回归)，低波动 → 做多(扩张)
    vol_zscore = (vol - np.mean(vol[window:])) / np.maximum(np.std(vol[window:]), 1e-10)
    score = -np.tanh(vol_zscore * 0.5)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_SKEWNESS_20D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window:
        return np.zeros(n)
    # 日收益率
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 20日滚动偏度
    skew = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        mu = np.mean(r)
        s = np.std(r)
        if s > 1e-10:
            skew[i] = np.mean((r - mu)**3) / (s**3)
    # 负偏度 → 风险溢价 → 做多；正偏度 → 做空
    score = -np.tanh(skew * 2.0)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_VOLUME_FLOW_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 成交量移动平均
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    # 价格变化
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 放量上涨 → 多头；放量下跌 → 空头
    score = np.where(
        vol_ratio > 1.2,
        np.tanh(chg / 0.02) * 0.5,
        np.where(vol_ratio < 0.8, np.tanh(chg / 0.05) * 0.3, 0)
    )
    return np.clip(score, -1.0, 1.0)
"""

_FUT_OPEN_INTEREST_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(len(close)))
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 持仓量变化率
    oi_chg = np.zeros(n)
    oi_chg[window:] = (hold[window:] - hold[:-window]) / np.maximum(hold[:-window], 1e-10)
    # 价格变化
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 持仓增加+价格上涨 = 多头主导；持仓增加+价格下跌 = 空头主导
    # 持仓减少 = 平仓离场，信号减弱
    score = np.where(
        oi_chg > 0.05,
        np.tanh(chg / 0.02) * 0.6,
        np.where(oi_chg < -0.05, -np.tanh(chg / 0.02) * 0.3, 0)
    )
    return np.clip(score, -1.0, 1.0)
"""

_FUT_ADX_TREND_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 14))
    if n < window * 2:
        return np.zeros(n)
    # 简化的趋势强度：用价格通道宽度近似ADX
    rolling_high = np.array([
        np.max(high[max(0, i-window+1):i+1]) for i in range(n)
    ])
    rolling_low = np.array([
        np.min(low[max(0, i-window+1):i+1]) for i in range(n)
    ])
    channel_width = (rolling_high - rolling_low) / np.maximum(close, 1e-10)
    # 趋势强度：通道宽度越宽趋势越强
    trend_chg = np.zeros(n)
    trend_chg[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    # 强趋势+方向 = 趋势跟踪
    trend_strength = np.tanh(channel_width * 10)
    score = trend_strength * np.tanh(trend_chg / 0.05)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_VOLATILITY_MOMENTUM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window * 2:
        return np.zeros(n)
    # 日收益率
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 近期波动率 vs 远期波动率
    vol_short = np.array([
        np.std(returns[max(0, i-window//2+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    vol_long = np.array([
        np.std(returns[max(0, i-window+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    # 波动率动量：短期波动率上升 → 做多波动率(做空品种)
    vol_mom = (vol_short - vol_long) / np.maximum(vol_long, 1e-10)
    score = -np.tanh(vol_mom * 5)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_CURVE_SLOPE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 5:
        return np.zeros(n)
    # 用不同周期均线斜率近似期限结构曲线斜率
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_mid = np.convolve(close, np.ones(10)/10, mode='same')
    ma_long = np.convolve(close, np.ones(window)/window, mode='same')
    # 曲线斜率 = (短-中)/中 - (中-长)/长 = 曲线曲率
    slope1 = (ma_short - ma_mid) / np.maximum(ma_mid, 1e-10)
    slope2 = (ma_mid - ma_long) / np.maximum(ma_long, 1e-10)
    curvature = slope1 - slope2
    # 凸曲线(近月升水+远月贴水) → 做多
    score = np.tanh(curvature * 50)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_VOLUME_PRICE_CORR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window:
        return np.zeros(n)
    # 日收益率
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 价量相关性
    corr = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        v = volume[i-window+1:i+1]
        if np.std(r) > 1e-10 and np.std(v) > 1e-10:
            corr[i] = np.corrcoef(r, v)[0, 1]
    # 价量正相关 → 趋势健康；负相关 → 趋势存疑
    score = np.tanh(corr * 2)
    return np.clip(score, -1.0, 1.0)
"""

_FUT_AMIHUD_LIQUIDITY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window:
        return np.zeros(n)
    # 日收益率绝对值
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    abs_ret = np.abs(returns)
    # Amihud非流动性 = |收益率| / 成交额(成交量*价格)
    amihud = np.zeros(n)
    for i in range(1, n):
        turnover = volume[i] * close[i]
        if turnover > 1e-10:
            amihud[i] = abs_ret[i] / turnover
    # 滚动均值
    amihud_avg = np.array([
        np.mean(amihud[max(0, i-window+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    # 非流动性高(难交易) → 做空(流动性溢价)；非流动性低(易交易) → 做多
    ilq_zscore = (amihud_avg - np.mean(amihud_avg[window:])) / np.maximum(np.std(amihud_avg[window:]), 1e-10)
    score = -np.tanh(ilq_zscore)
    return np.clip(score, -1.0, 1.0)
"""


# ─── 期货种子因子定义 ─────────────────────────────────────

# 分类:
#   第一层: 长期稳定核心池（8个）
#   第二层: 备选增强因子（4个）
#   总计: 12个

_FUTURES_SEED_DEFINITIONS: list[dict[str, Any]] = [
    # ═══ 第一层：长期稳定核心池 ═══
    {
        "name": "fut_roll_yield",
        "code": _FUT_ROLL_YIELD_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=4, institutional=5,
            narrative="展期收益率：Back结构做多获得展期收益，Contango做空避免展期损耗。"
                      "期货特有因子，基于不同周期均线近似期限结构。",
        ),
    },
    {
        "name": "fut_momentum_60d",
        "code": _FUT_MOMENTUM_60D_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=65,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="60日动量：趋势跟踪核心因子，捕捉中期趋势延续性。"
                      "期货市场趋势性较强，中长周期动量效果优于股票。",
        ),
    },
    {
        "name": "fut_reversal_5d",
        "code": _FUT_REVERSAL_5D_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=10,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=4, microstructure=3, institutional=3,
            narrative="短期5D反转：过度反应后的均值回归。期货市场短期波动大，"
                      "反转效应在5日窗口显著。",
        ),
    },
    {
        "name": "fut_volatility_20d",
        "code": _FUT_VOLATILITY_20D_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=25,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="20日波动率均值回归：高波后回归，低波后扩张。"
                      "波动率锥理论在期货市场有效性高。",
        ),
    },
    {
        "name": "fut_skewness_20d",
        "code": _FUT_SKEWNESS_20D_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=25,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="20日偏度：负偏度品种存在风险溢价。期货市场尾部风险大，"
                      "负偏度代表暴跌风险，需要风险溢价补偿。",
        ),
    },
    {
        "name": "fut_volume_flow",
        "code": _FUT_VOLUME_FLOW_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=15,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=5, institutional=4,
            narrative="资金流：放量方向反映主力资金意图。期货市场成交量"
                      "放大往往伴随趋势启动。",
        ),
    },
    {
        "name": "fut_open_interest",
        "code": _FUT_OPEN_INTEREST_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(
            input_fields=["close", "hold"],
            output_type="signal",
            frequency="daily",
            lookback=15,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=5, institutional=4,
            narrative="持仓量变化：持仓增加+价格上涨=多头主导，持仓增加+"
                      "价格下跌=空头主导。期货特有持仓量因子。",
        ),
    },
    {
        "name": "fut_amihud_liquidity",
        "code": _FUT_AMIHUD_LIQUIDITY_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=25,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="Amihud非流动性：流动性差的品种有流动性溢价。"
                      "期货市场流动性差异大，作为因子和风控指标。",
        ),
    },
    # ═══ 第二层：备选增强因子 ═══
    {
        "name": "fut_adx_trend",
        "code": _FUT_ADX_TREND_CODE,
        "params": {"window": 14},
        "signature": FactorSignature(
            input_fields=["close", "high", "low"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="ADX趋势强度：强趋势跟随，弱趋势反向。期货市场"
                      "趋势/震荡切换频繁，需要趋势强度信号。",
        ),
    },
    {
        "name": "fut_volatility_momentum",
        "code": _FUT_VOLATILITY_MOMENTUM_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="波动率动量：高波动持续做空，低波动持续做多。"
                      "波动率聚集效应在期货市场显著。",
        ),
    },
    {
        "name": "fut_curve_slope",
        "code": _FUT_CURVE_SLOPE_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=4,
            narrative="曲线斜率：用多周期均线关系近似期限结构曲率。"
                      "凸曲线(近强远弱)做多，凹曲线(近弱远强)做空。",
        ),
    },
    {
        "name": "fut_volume_price_corr",
        "code": _FUT_VOLUME_PRICE_CORR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=25,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="价量相关性：正相关趋势健康，负相关趋势存疑。"
                      "期货市场价量关系是趋势可靠性的重要指标。",
        ),
    },
]


# ─── 加载器 ───────────────────────────────────────────────

def load_futures_seeds(
    trace_id: Optional[str] = None,
) -> list[FactorProgram]:
    """加载期货专用种子因子。

    Args:
        trace_id: 全链路 trace_id。

    Returns:
        list[FactorProgram] — 12 个期货专用种子因子。
    """
    result: list[FactorProgram] = []
    for defn in _FUTURES_SEED_DEFINITIONS:
        fp = create_factor_program(
            name=defn["name"],
            code=defn["code"],
            params=defn["params"],
            signature=defn["signature"],
            economic_logic=defn["economic_logic"],
            source="seed",
            parent_id=None,
            generation=0,
            trace_id=trace_id,
        )
        result.append(fp)
    return result


def get_futures_seed_count() -> int:
    """返回期货种子因子数量。"""
    return len(_FUTURES_SEED_DEFINITIONS)


__all__ = [
    "load_futures_seeds",
    "get_futures_seed_count",
]