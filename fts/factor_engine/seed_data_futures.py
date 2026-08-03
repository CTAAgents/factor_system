"""
fts.factor_engine.seed_data_futures — 期货专用种子因子库

基于 D:/Futures/因子库/期货因子库.html 的因子分类体系，
实现期货横截面因子演化的种子因子。

因子来源:
    - 第一层（长期稳定核心池）: 8 个核心期货因子
    - 第二层（备选增强因子）: 4 个备选因子
    - 第三层（机构版核心因子 V1.0）: 9 个机构因子（去重后）
    - 总计: 21 个期货专用种子因子

数据字段:
    - DuckDB kline_cache: date, open, high, low, close, volume, hold(持仓量), settle

HARNESS §契约优先: 每个因子符合 FactorProgram 接口。

版本: v1.1.0
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


# ─── 机构版期货核心因子（V1.0 新增，去重后9个）──────────

_FS_BASIS_MOM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    N = int(params.get('N', 20))
    if n < N + 22:
        return np.zeros(n)
    # 基差代理：spot ≈ 近月(5D均线)，future ≈ 主力(20D均线)
    # basis = spot_price - future_price ≈ MA5 - MA20
    ma_spot = np.convolve(close, np.ones(5)/5, mode='same')
    ma_future = np.convolve(close, np.ones(N)/N, mode='same')
    basis = ma_spot - ma_future  # 负值=Contango, 正值=Back
    # 基差动量: basis_t / basis_{t-N} - 1
    basis_mom = np.zeros(n)
    basis_mom[N:] = basis[N:] / np.maximum(np.abs(basis[:-N]), 1e-10) - 1.0
    # 基差动量 > 0 (Back加深) → 做多；< 0 (Contango加深) → 做空
    # 滚动zscore标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(N + 22, n):
        win = basis_mom[max(0, i-60):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = (basis_mom[i] - mu) / s
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_FS_TS_SLOPE_CHG_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    lookback = int(params.get('lookback', 5))
    if n < 30:
        return np.zeros(n)
    # 先算展期收益率（同 FS_ROLL_YIELD 逻辑）
    # roll_yield ≈ log(MA5 / MA20) 近似
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_long = np.convolve(close, np.ones(20)/20, mode='same')
    roll_yield = np.log(np.maximum(ma_short, 1e-10) / np.maximum(ma_long, 1e-10))
    # 期限斜率变化: roll_yield_t - roll_yield_{t-lookback}
    slope_chg = np.zeros(n)
    slope_chg[lookback:] = roll_yield[lookback:] - roll_yield[:-lookback]
    # 滚动zscore标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(30, n):
        win = slope_chg[max(0, i-60):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = (slope_chg[i] - mu) / s
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_MT_TSMOM_120_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    N = int(params.get('N', 120))
    min_periods = int(params.get('min_periods', 80))
    if n < min_periods:
        return np.zeros(n)
    # 120日收益率: close_t / close_{t-120} - 1
    ret = (close - np.roll(close, N)) / np.maximum(np.roll(close, N), 1e-10)
    ret[:min_periods] = 0
    # 滚动zscore标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(min_periods + 20, n):
        win = ret[max(0, i-120):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = (ret[i] - mu) / s
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_MT_XSMOM_20_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    N = int(params.get('N', 20))
    if n < N + 2:
        return np.zeros(n)
    # 20日收益率: close_t / close_{t-N} - 1
    ret = np.zeros(n)
    ret[N:] = (close[N:] - close[:-N]) / np.maximum(close[:-N], 1e-10)
    # 截面rank代理: 用历史滚动分位数 (注册表: rank, [0, 1])
    rank = np.zeros(n)
    for i in range(N + 10, n):
        hist = ret[max(0, i-252):i+1]
        rank[i] = (ret[i] > hist).mean()  # 分位数 [0, 1]
    # 正向: 截面排名越高 → 做多
    return np.clip(rank, 0.0, 1.0)
"""

_MT_MA_BREAK_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    short = int(params.get('short', 20))
    long = int(params.get('long', 60))
    if n < long + 1:
        return np.zeros(n)
    # 双均线: (MA_short - MA_long) / MA_long
    ma_short = np.convolve(close, np.ones(short)/short, mode='same')
    ma_long = np.convolve(close, np.ones(long)/long, mode='same')
    ma_diff = (ma_short - ma_long) / np.maximum(ma_long, 1e-10)
    # 滚动zscore标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(long + 20, n):
        win = ma_diff[max(0, i-120):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = (ma_diff[i] - mu) / s
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_FA_INV_REVERSE_CODE = """
def factor_program(data, params):
    import numpy as np
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(0))
    n = len(hold)
    window = int(params.get('window', 60))
    min_periods = int(params.get('min_periods', 20))
    if n < min_periods:
        return np.zeros(n)
    # 库存代理: 持仓量。注册表公式: -zscore(inventory_t; window=60)
    # 反向因子: 库存越高→越看空
    zscore = np.zeros(n)
    for i in range(min_periods, n):
        win = hold[max(0, i-window+1):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            zscore[i] = (hold[i] - mu) / s
    # 反向: 取负，zscore 标准化 (注册表: zscore, [-3, 3])
    score = -zscore
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_FA_INV_CHG_CODE = """
def factor_program(data, params):
    import numpy as np
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(0))
    n = len(hold)
    ma_window = int(params.get('ma_window', 60))
    if n < ma_window + 2:
        return np.zeros(n)
    # 库存代理: 持仓量。注册表公式: -(inventory_t - inventory_{t-1}) / inventory_ma60
    inv_chg = np.zeros(n)
    inv_chg[1:] = hold[1:] - hold[:-1]  # 日差分
    ma = np.convolve(hold, np.ones(ma_window)/ma_window, mode='same')
    # 库存变化率归一化: -(日变化 / MA60)
    raw = np.zeros(n)
    raw[ma_window:] = -inv_chg[ma_window:] / np.maximum(ma[ma_window:], 1e-10)
    # 滚动zscore标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(ma_window + 20, n):
        win = raw[max(0, i-120):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = (raw[i] - mu) / s
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""

_FA_PROFIT_QT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    lookback = int(params.get('lookback', 252))
    if n < lookback + 5:
        return np.zeros(n)
    # 利润代理: 展期收益率 (Back=盈利, Contango=亏损)
    # 注册表公式: rank(processing_profit_t) / total_obs → 做均值回复
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_long = np.convolve(close, np.ones(20)/20, mode='same')
    profit_proxy = (ma_short - ma_long) / np.maximum(ma_long, 1e-10)
    # 滚动分位数 rank/total_obs (注册表: quantile, [0, 1])
    qt = np.zeros(n)
    for i in range(lookback, n):
        hist = profit_proxy[max(0, i-lookback+1):i+1]
        qt[i] = (profit_proxy[i] > hist).mean()
    # 反向: 利润分位数过高→做空；过低→做多
    # 1 - qt 使得高分位→低信号 (反向)
    signal = 1.0 - qt
    return np.clip(signal, 0.0, 1.0)
"""

_MP_SKEW_60_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 60))
    min_periods = int(params.get('min_periods', 30))
    if n < min_periods:
        return np.zeros(n)
    # 日收益率
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 60日滚动偏度: skewness(日收益; window=60)
    skew = np.zeros(n)
    for i in range(min_periods, n):
        win_start = max(0, i - window + 1)
        r = returns[win_start:i+1]
        if len(r) < min_periods:
            continue
        mu = np.mean(r)
        s = np.std(r)
        if s > 1e-10:
            skew[i] = np.mean((r - mu)**3) / (s**3)
    # 反向: 负偏度→做多 (风险溢价); zscore 标准化 (注册表: zscore, [-3, 3])
    score = np.zeros(n)
    for i in range(min_periods + 10, n):
        win = skew[max(0, i-120):i+1]
        mu = np.mean(win)
        s = np.std(win)
        if s > 1e-10:
            score[i] = -(skew[i] - mu) / s  # 反向取负
    score = np.clip(score, -3.0, 3.0) / 3.0
    return np.clip(score, -1.0, 1.0)
"""


# ─── 期货种子因子定义 ─────────────────────────────────────

# 分类:
#   第一层: 长期稳定核心池（8个）
#   第二层: 备选增强因子（4个）
#   第三层: 机构版核心因子（V1.0 新增，去重后9个）
#   总计: 21个

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
    # ═══ 第三层：机构版核心因子（V1.0 新增，去重后9个） ═══
    {
        "name": "fs_basis_mom",
        "code": _FS_BASIS_MOM_CODE,
        "params": {"N": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=80,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=4, institutional=5,
            narrative="基差动量（FS_BASIS_MOM）：basis_t / basis_{t-N} - 1，N=20。"
                      "basis = spot_price - future_price（MA5-MA20代理）。"
                      "基差动量>0（Back加深）做多，<0（Contango加深）做空。"
                      "zscore标准化[-3,3]，月频调仓，机构权重10%-15%。",
        ),
    },
    {
        "name": "fs_ts_slope_chg",
        "code": _FS_TS_SLOPE_CHG_CODE,
        "params": {"lookback": 5},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=60,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=4,
            narrative="期限斜率变化（FS_TS_SLOPE_CHG）：roll_yield_t - roll_yield_{t-5}。"
                      "基于展期收益率的短期边际变化，用于周频复合。"
                      "zscore标准化[-3,3]，周频调仓，机构权重5%-10%。",
        ),
    },
    {
        "name": "mt_tsmom_120",
        "code": _MT_TSMOM_120_CODE,
        "params": {"N": 120, "min_periods": 80},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=140,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="时序动量120D（MT_TSMOM_120）：close_t / close_{t-120} - 1。"
                      "长周期趋势跟踪，与60D动量合成使用更稳健。"
                      "zscore标准化[-3,3]，月频调仓，min_periods=80防冷启动。"
                      "机构权重10%-20%。",
        ),
    },
    {
        "name": "mt_xsmom_20",
        "code": _MT_XSMOM_20_CODE,
        "params": {"N": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=260,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="截面动量20D（MT_XSMOM_20）：截面rank(close_t/close_{t-20}-1)。"
                      "用历史滚动分位数作为截面rank代理，输出[0,1]。"
                      "rank标准化，周/月频调仓，机构权重5%-15%。",
        ),
    },
    {
        "name": "mt_ma_break",
        "code": _MT_MA_BREAK_CODE,
        "params": {"short": 20, "long": 60},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=140,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=3, institutional=4,
            narrative="均线突破（MT_MA_BREAK）：(MA_20 - MA_60) / MA_60。"
                      "双均线差的动量因子工程化包装，可用Donchian/布林带替代。"
                      "zscore标准化[-3,3]，月频调仓，机构权重5%-10%。",
        ),
    },
    {
        "name": "fa_inv_reverse",
        "code": _FA_INV_REVERSE_CODE,
        "params": {"window": 60, "min_periods": 20},
        "signature": FactorSignature(
            input_fields=["hold"],
            output_type="signal",
            frequency="daily",
            lookback=65,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="库存反向因子（FA_INV_REVERSE）：-zscore(inventory_t; window=60)。"
                      "以持仓量作为库存代理，低库存→做多，高库存→做空。"
                      "头部私募差异化Alpha核心来源，zscore标准化[-3,3]。"
                      "周/月频调仓，机构权重10%-20%。",
        ),
    },
    {
        "name": "fa_inv_chg",
        "code": _FA_INV_CHG_CODE,
        "params": {"ma_window": 60},
        "signature": FactorSignature(
            input_fields=["hold"],
            output_type="signal",
            frequency="daily",
            lookback=140,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="库存变化率（FA_INV_CHG）：-(inventory_t - inventory_{t-1}) / inventory_ma60。"
                      "以持仓量日差分/MA60归一化，捕捉去库速度。"
                      "zscore标准化[-3,3]，周频调仓，机构权重5%-10%。",
        ),
    },
    {
        "name": "fa_profit_qt",
        "code": _FA_PROFIT_QT_CODE,
        "params": {"lookback": 252},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=260,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="利润分位数（FA_PROFIT_QT）：rank(profit_proxy_t) / total_obs。"
                      "以展期收益率作为产业链利润代理，252日回看。"
                      "利润过高做空、过低做多，quantile标准化[0,1]。"
                      "月频调仓，机构权重5%-15%。",
        ),
    },
    {
        "name": "mp_skew_60",
        "code": _MP_SKEW_60_CODE,
        "params": {"window": 60, "min_periods": 30},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=140,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="收益偏度60D（MP_SKEW_60）：skewness(日收益; window=60)。"
                      "负偏品种有风险溢价→做多，截面标准化后使用。"
                      "zscore标准化[-3,3]，月频调仓，机构权重3%-8%。",
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
        list[FactorProgram] — 21 个期货专用种子因子。
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