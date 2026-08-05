"""
fts.factor_engine.seed_data_futures_full — 期货全量种子因子库（14大因子家族）

基于 D:/Futures/因子库/期货因子库.html 的完整因子分类体系。

14 大因子家族:
   1. 动量因子家族 (5 个): 截面动量 / 时序动量 / 短期反转 / 复合动量 / 基差动量
   2. 期限结构因子家族 (3 个): 展期收益 / 稳定样本期限结构 / 基差因子
   3. 持仓/资金流因子家族 (3 个): 持仓量 / 仓单 / 对冲压力
   4. 流动性因子家族 (3 个): 换手率 / 买卖价差 / Amihud 非流动性
   5. 偏度/峰度/高阶矩因子家族 (3 个): 偏度 / 上行偏度 / 峰度
   6. 波动率因子家族 (2 个): 变异系数 CV / 下行波动率
   7. 基本面因子家族 (4 个): 量价相关性 / 趋势强度 / 振幅 / 移动大数据(近似)
   8. 拥挤度因子家族 (6 个): 成交额/波动/换手率/乖离率等 6 维度拥挤度
   9. Alpha/量价行为因子家族 (4 个): 时序回归 / BIAS 乖离率 / GP Alpha / 华泰 Alpha
  10. 高频因子家族 (6 个): 日频近似版（报价不平衡/成交不平衡/历史收益/换手率/价差/下行波动率）
  11. 期权隐含信息因子家族 (3 个): 日频波动率期限结构 / 偏度 / PCR
  12. 市场环境因子家族 (8 个): 4 宏观(CPI/利率/出口/美债) + 4 市场(趋势/投机/轮动/集中度)
  13. CTA注册表补充因子 V2.0 (7 个): 5日/22日时序动量 / 基差水平 / 年化波动率 / 成交持仓比 / 长期反转 / 持仓量变化率
  14. 算子字典种子因子 (24 个): K线形态(4) / 多空平衡(1) / 反转(1) / 动量(2) / 波动率(3) / 趋势(2) / 极值位置(2) / 量价相关(1) / 成交量(2) / 持仓量(2) / 期限结构(1) / 结算价偏离(1) / VWAP改造(2)

总计: 81 个因子

数据字段（DuckDB kline_cache）:
    date, open, high, low, close, volume, hold(持仓量), settle(结算价)

HARNESS §契约优先: 每个因子符合 FactorProgram 接口。

版本: v2.0.0
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .contracts import EconomicLogic, FactorSignature
from .factor_program import create_factor_program


# ══════════════════════════════════════════════════════════════════════════════
# 家族 1: 动量因子家族 (5 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_XSMOM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    j = int(params.get('lookback', 20))
    k = int(params.get('holding', 1))
    if n < j + 2:
        return np.zeros(n)
    # 截面动量：过去 J 日收益率
    ret = np.zeros(n)
    ret[j:] = (close[j:] - close[:-j]) / np.maximum(close[:-j], 1e-10)
    # 滞后 k 日执行（简化）
    sig = np.zeros(n)
    sig[j+k:] = np.tanh(ret[j:-k] / 0.05)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_TSMOM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    k = int(params.get('lookback_months', 3))
    j = int(params.get('skip_days', 20))
    window = k * 21
    if n < window + j + 1:
        return np.zeros(n)
    # 时序动量：过去收益率 sign(收益) 决定方向，滞后 1 个月
    ret = np.zeros(n)
    ret[window+j:] = (close[window+j:] - close[j:n-window]) / np.maximum(close[j:n-window], 1e-10)
    sig = np.sign(ret) * 0.5
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_SHORT_REVERSAL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    ret = np.zeros(n)
    ret[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    # 短期反转：做多跌多的，做空涨多的
    sig = -np.tanh(ret / 0.02)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_COMPOSITE_MOMENTUM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    j = int(params.get('lookback', 243))
    k = int(params.get('holding', 10))
    if n < j + k + 1:
        return np.zeros(n)
    # XSMOM 成分
    ret = np.zeros(n)
    ret[j:] = (close[j:] - close[:-j]) / np.maximum(close[:-j], 1e-10)
    xs = np.tanh(ret / 0.05)
    # TSMOM 成分
    ts = np.sign(ret) * 0.5
    # 复合动量
    sig = xs * 0.6 + ts * 0.4
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_BASIS_MOMENTUM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    j = int(params.get('lookback', 243))
    k = int(params.get('holding', 3))
    if n < j + k + 1:
        return np.zeros(n)
    # 基差动量：用 MA 比例近似近远月价差的同比变化
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_long = np.convolve(close, np.ones(20)/20, mode='same')
    basis = (ma_short - ma_long) / np.maximum(ma_long, 1e-10)
    # 基差的变化率
    basis_chg = np.zeros(n)
    basis_chg[j:] = (basis[j:] - basis[:-j]) / np.maximum(np.abs(basis[:-j]), 1e-10)
    sig = np.tanh(basis_chg * 5)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 2: 期限结构因子家族 (3 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_ROLL_YIELD_FULL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('lookback', 5))
    hold = int(params.get('holding', 15))
    if n < window + 5 + hold:
        return np.zeros(n)
    # 用多周期 MA 斜率近似展期收益率
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_long = np.convolve(close, np.ones(window)/window, mode='same')
    roll_yield = (ma_short - ma_long) / np.maximum(ma_long, 1e-10)
    sig = np.clip(roll_yield / 0.1, -1.0, 1.0)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_STABLE_TERM_STRUCTURE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    lookback = int(params.get('lookback', 5))
    hold = int(params.get('holding', 15))
    if n < lookback * 3 + 5:
        return np.zeros(n)
    # 用 OLS 斜率稳定性近似期限结构稳定性
    ma_short = np.convolve(close, np.ones(5)/5, mode='same')
    ma_mid = np.convolve(close, np.ones(10)/10, mode='same')
    slope = (ma_short - ma_mid) / np.maximum(ma_mid, 1e-10)
    # 滑动窗口内斜率的标准差 -> 稳定性
    stable = np.zeros(n)
    for i in range(lookback, n):
        seg = slope[max(0, i-lookback+1):i+1]
        if len(seg) > 1:
            # 标准差越小越稳定
            stable[i] = -np.std(seg)
    sig = np.tanh(stable * 50)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_BASIS_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    settle = data.get('settle', close).values if hasattr(data.get('settle', close), 'values') else data.get('settle', close)
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 基差因子：用 close/settle 偏离近似（settle=结算价）
    # 若无结算价，用 close 的短长 MA 比替代
    try:
        basis = (close - settle) / np.maximum(settle, 1e-10)
    except Exception:
        ma_s = np.convolve(close, np.ones(3)/3, mode='same')
        ma_l = np.convolve(close, np.ones(20)/20, mode='same')
        basis = (ma_s - ma_l) / np.maximum(ma_l, 1e-10)
    sig = np.clip(basis / 0.02, -1.0, 1.0)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 3: 持仓/资金流因子家族 (3 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_OPEN_INTEREST_FULL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(len(close)))
    n = len(close)
    j = int(params.get('lookback', 5))
    if n < j + 2:
        return np.zeros(n)
    oi_chg = np.zeros(n)
    oi_chg[j:] = (hold[j:] - hold[:-j]) / np.maximum(hold[:-j], 1e-10)
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    sig = np.where(oi_chg > 0.03, np.tanh(chg / 0.02) * 0.6,
                   np.where(oi_chg < -0.03, -np.tanh(chg / 0.02) * 0.3, 0))
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_WAREHOUSE_RECEIPT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    j = int(params.get('lookback', 3))
    k = int(params.get('holding', 1))
    if n < j + k + 2:
        return np.zeros(n)
    # 仓单因子：AKShare 仓单数据需外部注入，用持仓量变化近似
    # 仓单增加表示现货供应增加，利空价格
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(n))
    if np.max(np.abs(hold)) > 0:
        wr_chg = np.zeros(n)
        wr_chg[j:] = (hold[j:] - hold[:-j]) / np.maximum(hold[:-j], 1e-10)
        sig = -np.tanh(wr_chg * 5)
    else:
        sig = np.zeros(n)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HEDGE_PRESSURE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(len(close)))
    n = len(close)
    j = int(params.get('lookback', 243))
    if n < j + 2:
        return np.zeros(n)
    # 对冲压力 = 持仓量变化率 × 价格方向
    # 套保者增加空头(持仓增加+价格下跌) = 对冲压力增大
    oi_chg = np.zeros(n)
    oi_chg[j:] = (hold[j:] - hold[:-j]) / np.maximum(hold[:-j], 1e-10)
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 对冲压力增大(持仓增+价格跌) => 做空；对冲压力减小 => 做多
    hp = oi_chg * np.sign(ret)
    sig = -np.tanh(hp * 5)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 4: 流动性因子家族 (3 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_TURNOVER_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close)))
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 换手率 = volume / hold
    turnover = np.zeros(n)
    for i in range(1, n):
        turnover[i] = volume[i] / max(hold[i], 1)
    avg_turn = np.convolve(turnover, np.ones(window)/window, mode='same')
    # 换手率偏离 -> 异常流动性
    dev = (turnover - avg_turn) / np.maximum(avg_turn, 1e-10)
    sig = np.tanh(dev * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_BID_ASK_SPREAD_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 用 (high-low)/close 近似买卖价差
    spread = (high - low) / np.maximum(close, 1e-10)
    avg_spread = np.convolve(spread, np.ones(window)/window, mode='same')
    # 价差扩大 -> 流动性恶化 -> 做空(流动性溢价)
    sig = np.tanh((spread - avg_spread) / np.maximum(avg_spread, 1e-10) * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_AMIHUD_FULL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    abs_ret = np.abs(returns)
    amihud = np.zeros(n)
    for i in range(1, n):
        turnover = volume[i] * close[i]
        if turnover > 1e-10:
            amihud[i] = abs_ret[i] / turnover
    avg = np.array([np.mean(amihud[max(0, i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
    z = (avg - np.mean(avg[window:])) / np.maximum(np.std(avg[window:]), 1e-10)
    sig = -np.tanh(z)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 5: 偏度/峰度/高阶矩因子家族 (3 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_SKEWNESS_FULL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    skew = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        mu, s = np.mean(r), np.std(r)
        if s > 1e-10:
            skew[i] = np.mean((r - mu)**3) / (s**3)
    sig = -np.tanh(skew * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_UP_SKEWNESS_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    uskew = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        pos = r[r > 0]
        if len(pos) > 2 and np.std(pos) > 1e-10:
            mu = np.mean(pos)
            uskew[i] = np.mean((pos - mu)**3) / (np.std(pos)**3)
    sig = -np.tanh(uskew * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_KURTOSIS_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 14))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    kurt = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        mu, s = np.mean(r), np.std(r)
        if s > 1e-10:
            kurt[i] = np.mean((r - mu)**4) / (s**4) - 3
    # 高峰度 -> 极端风险 -> 做空
    sig = -np.tanh(kurt * 0.5)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 6: 波动率因子家族 (2 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_CV_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    # 变异系数 = std / mean
    cv = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        mu = np.mean(seg)
        if mu > 1e-10:
            cv[i] = np.std(seg) / mu
    # CV 高 => 波动大 => 均值回归做空
    sig = -np.tanh(cv * 50)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_DOWN_VOL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    dvol = np.zeros(n)
    for i in range(window, n):
        neg = returns[max(0, i-window+1):i+1]
        neg = neg[neg < 0]
        if len(neg) > 1:
            dvol[i] = np.std(neg)
    # 下行波动率高 => 风险大 => 做空
    sig = -np.tanh(dvol * 50)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 7: 基本面因子家族 (4 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_VOLUME_PRICE_CORR_FULL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 63))
    if n < window:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    corr = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        v = volume[i-window+1:i+1]
        if np.std(r) > 1e-10 and np.std(v) > 1e-10:
            corr[i] = np.corrcoef(r, v)[0, 1]
    # 正相关(上涨放量下跌缩量) => 做多；负相关 => 做空
    sig = np.tanh(corr * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_TREND_STRENGTH_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 243))
    if n < window + 1:
        return np.zeros(n)
    # 趋势强度 = 位移/路程 = (close[t]-close[t-window]) / sum(|close[i]-close[i-1]|)
    displacement = np.zeros(n)
    displacement[window:] = close[window:] - close[:-window]
    path = np.zeros(n)
    for i in range(1, n):
        path[i] = path[i-1] + abs(close[i] - close[i-1])
    path_ret = np.zeros(n)
    path_ret[window:] = path[window:] - path[:-window]
    ts = np.zeros(n)
    for i in range(window, n):
        if path_ret[i] > 1e-10:
            ts[i] = displacement[i] / path_ret[i]
    # 趋势强度高(位移/路程接近1) => 趋势跟踪
    sig = np.tanh(ts * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_AMPLITUDE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 63))
    if n < window + 1:
        return np.zeros(n)
    # 振幅 = (high-low)/close 的滚动均值
    amp = (high - low) / np.maximum(close, 1e-10)
    avg_amp = np.array([np.mean(amp[max(0, i-window+1):i+1]) for i in range(n)])
    # 振幅收缩 -> 突破前兆 -> 做多；振幅扩大 -> 见顶 -> 做空
    chg = np.zeros(n)
    chg[window:] = (avg_amp[window:] - avg_amp[:-window]) / np.maximum(avg_amp[:-window], 1e-10)
    sig = -np.tanh(chg * 10)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MOBILE_BIG_DATA_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 移动大数据因子近似版：用量价加速度替代
    # 原版使用人流/物流等另类数据，此处用成交量加速度代理
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol_chg = np.zeros(n)
    vol_chg[1:] = (volume[1:] - volume[:-1]) / np.maximum(volume[:-1], 1e-10)
    # 加速度 = 价格加速 + 成交量加速
    accel = np.zeros(n)
    for i in range(window, n):
        ret_slope = np.polyfit(range(window), ret[i-window+1:i+1], 1)[0] if np.std(ret[i-window+1:i+1]) > 1e-10 else 0
        vol_slope = np.polyfit(range(window), vol_chg[i-window+1:i+1], 1)[0] if np.std(vol_chg[i-window+1:i+1]) > 1e-10 else 0
        accel[i] = ret_slope + vol_slope * 0.5
    sig = np.tanh(accel * 50)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 8: 拥挤度因子家族 (6 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_CROWD_VOLUME_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    # 成交额拥挤度：成交量 vs 3年90%分位数
    hist = volume[:window]
    p90 = np.percentile(hist, 90) if len(hist) > 0 else 1e10
    ratio = volume / max(p90, 1)
    sig = -np.tanh((ratio - 1) * 5)
    # 成交拥挤 => 过热 => 做空
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_CROWD_VOLATILITY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # Beta 拥挤度：波动率 vs 历史 90% 分位数
    hist_vol = np.array([np.std(returns[max(0, i-20+1):i+1]) for i in range(window)])
    p90 = np.percentile(hist_vol, 90) if len(hist_vol) > 0 else 1e10
    recent_vol = np.array([np.std(returns[max(0, i-20+1):i+1]) for i in range(n)])
    ratio = recent_vol / max(p90, 1)
    sig = -np.tanh((ratio - 1) * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_CROWD_TURNOVER_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(volume)))
    n = len(volume)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    turnover = np.zeros(n)
    for i in range(1, n):
        turnover[i] = volume[i] / max(hold[i], 1)
    hist = turnover[:window]
    p90 = np.percentile(hist, 90) if len(hist) > 0 else 1e10
    ratio = turnover / max(p90, 1)
    sig = -np.tanh((ratio - 1) * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_CROWD_BIAS_VOLUME_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    ma = np.convolve(volume, np.ones(window)/window, mode='same')
    bias = (volume - ma) / np.maximum(ma, 1e-10)
    hist = bias[:window]
    p90 = np.percentile(hist, 90) if len(hist) > 0 else 1e10
    ratio = bias / max(p90, 1)
    sig = -np.tanh((ratio - 1) * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_CROWD_BIAS_AMOUNT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    amount = close * volume
    ma = np.convolve(amount, np.ones(window)/window, mode='same')
    bias = (amount - ma) / np.maximum(ma, 1e-10)
    hist = bias[:window]
    p90 = np.percentile(hist, 90) if len(hist) > 0 else 1e10
    ratio = bias / max(p90, 1)
    sig = -np.tanh((ratio - 1) * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_CROWD_COMPOSITE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close)))
    n = len(volume)
    window = int(params.get('window', 60))
    if n < window * 2:
        return np.zeros(n)
    # 复合拥挤度：3个维度及以上触发记为拥挤
    turnover = np.zeros(n)
    for i in range(1, n):
        turnover[i] = volume[i] / max(hold[i], 1)
    amount = close * volume
    # 多维度信号
    signals = []
    for arr in [volume, turnover, amount]:
        ma = np.convolve(arr, np.ones(window)/window, mode='same')
        bias = (arr - ma) / np.maximum(ma, 1e-10)
        hist = bias[:window]
        p90 = np.percentile(hist, 90) if len(hist) > 0 else 1e10
        sig = (bias / max(p90, 1)) > 1
        signals.append(sig.astype(float))
    # 3个维度中 ≥2 个拥挤 => 综合拥挤
    composite = np.mean(signals, axis=0)
    sig = -np.tanh(composite * 5)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 9: Alpha/量价行为因子家族 (4 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_TIME_SERIES_REGRESSION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    j = int(params.get('lookback', 243))
    k = int(params.get('holding', 10))
    if n < j + k + 1:
        return np.zeros(n)
    # 时序回归：close ~ time 的 OLS 斜率，综合趋势强度和流畅性
    slope = np.zeros(n)
    r2 = np.zeros(n)
    for i in range(j, n):
        y = close[i-j+1:i+1]
        x = np.arange(j)
        if np.std(y) > 1e-10 and np.std(x) > 1e-10:
            slope[i] = np.corrcoef(x, y)[0, 1] * (np.std(y) / np.std(x))
            r2[i] = np.corrcoef(x, y)[0, 1]**2
    # R² 高的趋势更可靠
    quality = r2 * slope
    sig = np.tanh(quality * 100)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_BIAS_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    bias = (close - ma) / np.maximum(ma, 1e-10)
    # 正乖离(价格在均线上) => 超买 => 做空
    # 负乖离(价格在均线下) => 超卖 => 做多
    sig = -np.tanh(bias * 10)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_GP_ALPHA1_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    if n < 30:
        return np.zeros(n)
    # GP Alpha 1 近似: (close - vwap) * volume / (high - low + 1e-10)
    vwap = np.convolve(close * volume, np.ones(5)/5, mode='same') / np.maximum(np.convolve(volume, np.ones(5)/5, mode='same'), 1e-10)
    hl = np.maximum(high - low, 1e-10)
    raw = (close - vwap) * volume / hl
    sig = np.tanh(raw / np.maximum(np.std(raw[30:]), 1e-10) * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HT_ALPHA_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window * 2:
        return np.zeros(n)
    # 华泰 Alpha 因子近似：多维度量价行为组合
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 维度1: 日内强度 (close-low)/(high-low)
    hl = np.maximum(high - low, 1e-10)
    intraday = (close - low) / hl
    # 维度2: 量价趋势
    vwap = np.convolve(close * volume, np.ones(5)/5, mode='same') / np.maximum(np.convolve(volume, np.ones(5)/5, mode='same'), 1e-10)
    vp_trend = (close - vwap) / np.maximum(vwap, 1e-10)
    # 维度3: 动量加速度
    mom = np.zeros(n)
    mom[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    accel = np.zeros(n)
    for i in range(window, n):
        accel[i] = mom[i] - mom[i-1] if i > 0 else 0
    # 综合
    sig = (intraday - 0.5) * 0.3 + np.tanh(vp_trend * 5) * 0.3 + np.tanh(accel * 50) * 0.4
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 10: 高频因子家族 — 日频近似版 (6 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_HF_QUOTE_IMBALANCE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 日频近似：用 (high - close) / (close - low) 近似报价不平衡
    ask_vol = high - close
    bid_vol = close - low
    hl = ask_vol + bid_vol + 1e-10
    imbalance = (bid_vol - ask_vol) / hl
    sig = np.tanh(imbalance * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HF_TRADE_IMBALANCE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 日频近似：用价格变化方向 × 成交量 近似成交不平衡
    chg = np.zeros(n)
    chg[1:] = np.sign(close[1:] - close[:-1])
    trade_imb = chg * volume
    avg = np.convolve(trade_imb, np.ones(window)/window, mode='same')
    sig = np.tanh(avg / np.maximum(np.convolve(volume, np.ones(window)/window, mode='same'), 1e-10) * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HF_HISTORICAL_RETURN_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    # 高频历史收益因子：短周期收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    avg_ret = np.convolve(ret, np.ones(window)/window, mode='same')
    sig = np.tanh(avg_ret / 0.02)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HF_TURNOVER_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(volume)))
    n = len(volume)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    # 高频换手率因子
    turn = np.zeros(n)
    for i in range(1, n):
        turn[i] = volume[i] / max(hold[i], 1)
    avg = np.convolve(turn, np.ones(window)/window, mode='same')
    dev = (turn - avg) / np.maximum(avg, 1e-10)
    sig = np.tanh(dev * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HF_SPREAD_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    # 高频价差因子：日频近似
    spread = (high - low) / np.maximum(close, 1e-10)
    avg = np.convolve(spread, np.ones(window)/window, mode='same')
    dev = (spread - avg) / np.maximum(avg, 1e-10)
    sig = np.tanh(dev * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_HF_DOWN_VOL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    dvol = np.zeros(n)
    for i in range(window, n):
        neg = returns[max(0, i-window+1):i+1]
        neg = neg[neg < 0]
        if len(neg) > 1:
            dvol[i] = np.std(neg)
    sig = -np.tanh(dvol * 50)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 11: 期权隐含信息因子家族 — 日频近似版 (3 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_OPTION_VOL_TERM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window * 2:
        return np.zeros(n)
    # 日频近似：用不同周期波动率之差近似波动率期限结构
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol_short = np.array([np.std(returns[max(0, i-5+1):i+1]) for i in range(n)])
    vol_long = np.array([np.std(returns[max(0, i-window+1):i+1]) for i in range(n)])
    # 短波动率 - 长波动率 > 0 = 近月IV > 远月IV = 看空
    spread = (vol_short - vol_long) / np.maximum(vol_long, 1e-10)
    sig = -np.tanh(spread * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_OPTION_SKEW_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 日频近似：用上行/下行波动率比近似 IV 偏斜
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    up_vol = np.zeros(n)
    down_vol = np.zeros(n)
    for i in range(window, n):
        r = returns[i-window+1:i+1]
        pos = r[r > 0]
        neg = r[r < 0]
        up_vol[i] = np.std(pos) if len(pos) > 1 else 0
        down_vol[i] = np.std(neg) if len(neg) > 1 else 0
    # SKEW = 上行波动率 / 下行波动率，比例高 => 看跌保护需求大 => 看空
    skew = up_vol / np.maximum(down_vol, 1e-10)
    sig = -np.tanh((skew - 1) * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_OPTION_PCR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 日频近似：用成交量方向比近似 PCR
    # 下跌日成交量 / 上涨日成交量
    chg = np.zeros(n)
    chg[1:] = close[1:] - close[:-1]
    down_vol = np.where(chg < 0, volume, 0)
    up_vol = np.where(chg > 0, volume, 0)
    cum_down = np.convolve(down_vol, np.ones(window)/window, mode='same')
    cum_up = np.convolve(up_vol, np.ones(window)/window, mode='same')
    pcr = cum_down / np.maximum(cum_up, 1e-10)
    # PCR 高 => 看跌成交多 => 看空
    sig = -np.tanh((pcr - 1) * 3)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 12: 市场环境因子家族 (8 个)
# ══════════════════════════════════════════════════════════════════════════════

_FUT_MACRO_CPI_CODE = """
def factor_program(data, params):
    import numpy as np
    # CPI 宏观环境因子：需外部注入 cpi 字段
    # 无数据时用 close 的长期趋势替代
    close = data['close'].values if hasattr(data, 'close') else data['close']
    cpi = data.get('cpi', None)
    if cpi is not None:
        cpi_arr = cpi.values if hasattr(cpi, 'values') else cpi
        sig = -np.tanh((cpi_arr - np.mean(cpi_arr)) / np.maximum(np.std(cpi_arr), 1e-10))
    else:
        n = len(close)
        if n < 60:
            return np.zeros(n)
        trend = np.zeros(n)
        trend[60:] = (close[60:] - close[:-60]) / np.maximum(close[:-60], 1e-10)
        sig = np.tanh(trend * 10) * 0.3
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MACRO_INTEREST_RATE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    rate = data.get('rate', None)
    if rate is not None:
        rate_arr = rate.values if hasattr(rate, 'values') else rate
        sig = -np.tanh((rate_arr - np.mean(rate_arr)) / np.maximum(np.std(rate_arr), 1e-10))
    else:
        n = len(close)
        if n < 30:
            return np.zeros(n)
        vol = np.array([np.std(close[max(0, i-30+1):i+1]) for i in range(n)])
        sig = -np.tanh(vol / np.maximum(np.mean(vol), 1e-10) * 2) * 0.3
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MACRO_EXPORT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    export = data.get('export', None)
    if export is not None:
        export_arr = export.values if hasattr(export, 'values') else export
        sig = np.tanh((export_arr - np.mean(export_arr)) / np.maximum(np.std(export_arr), 1e-10))
    else:
        n = len(close)
        if n < 20:
            return np.zeros(n)
        ma = np.convolve(close, np.ones(20)/20, mode='same')
        slope = np.zeros(n)
        slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
        sig = np.tanh(slope * 30) * 0.3
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MACRO_US_BOND_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    us_bond = data.get('us_bond', None)
    if us_bond is not None:
        us_arr = us_bond.values if hasattr(us_bond, 'values') else us_bond
        sig = -np.tanh((us_arr - np.mean(us_arr)) / np.maximum(np.std(us_arr), 1e-10))
    else:
        n = len(close)
        if n < 20:
            return np.zeros(n)
        # 美债收益率上升 => 商品承压的近似
        sig = np.zeros(n) * 0.2
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MKT_TREND_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 60))
    if n < window + 1:
        return np.zeros(n)
    # 市场趋势强度+方向：R² × 斜率符号
    # R² 衡量趋势强度，斜率符号决定方向（上涨=+，下跌=-）
    trend = np.zeros(n)
    for i in range(window, n):
        y = close[i-window+1:i+1]
        x = np.arange(window)
        if np.std(y) > 1e-10:
            r2 = np.corrcoef(x, y)[0, 1]**2
            slope_sign = np.sign(y[-1] - y[0])
            trend[i] = r2 * slope_sign
    sig = np.tanh(trend * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MKT_SPECULATION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close)))
    n = len(volume)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 市场投机度 = 成交量 / 持仓量
    turn = np.zeros(n)
    for i in range(1, n):
        turn[i] = volume[i] / max(hold[i], 1)
    avg = np.convolve(turn, np.ones(window)/window, mode='same')
    dev = (turn - avg) / np.maximum(avg, 1e-10)
    # 投机度高 => 情绪过热 => 做空
    sig = -np.tanh(dev * 3)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MKT_ROTATION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window * 2:
        return np.zeros(n)
    # 市场轮动速度：用品种间相关性变化率近似
    # 单品种内用高频波动 vs 低频波动比
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol_high = np.array([np.std(returns[max(0, i-5+1):i+1]) for i in range(n)])
    vol_low = np.array([np.std(returns[max(0, i-window+1):i+1]) for i in range(n)])
    ratio = vol_high / np.maximum(vol_low, 1e-10)
    sig = np.tanh(ratio * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_MKT_CONCENTRATION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 成交集中度：用单个品种成交量占比的波动近似
    amount = close * volume
    ma = np.convolve(amount, np.ones(window)/window, mode='same')
    dev = (amount - ma) / np.maximum(ma, 1e-10)
    # 成交集中 => 资金集中 => 趋势延续
    sig = np.tanh(dev * 2)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 13: CTA注册表补充因子 V2.0 (7 个) — 来自期货CTA因子注册表
# ══════════════════════════════════════════════════════════════════════════════

_FUT_TSMOM_5D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    ret = np.zeros(n)
    ret[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    sig = np.tanh(ret / 0.02)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_TSMOM_22D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 22))
    if n < window + 1:
        return np.zeros(n)
    ret = np.zeros(n)
    ret[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    sig = np.tanh(ret / 0.05)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_BASIS_LEVEL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    settle = data.get('settle', close).values if hasattr(data.get('settle', close), 'values') else data.get('settle', close)
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    basis = (close - settle) / np.maximum(settle, 1e-10)
    avg_basis = np.convolve(basis, np.ones(window)/window, mode='same')
    sig = np.clip(avg_basis / 0.02, -1.0, 1.0)
    return sig
"""

_FUT_VOLATILITY_ANNUAL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol = np.array([np.std(returns[max(0, i-window+1):i+1]) * np.sqrt(252) for i in range(n)])
    sig = -np.tanh(vol / 0.5)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_LIQUIDITY_RATIO_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close)))
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    turnover = np.zeros(n)
    for i in range(1, n):
        turnover[i] = volume[i] / max(hold[i], 1)
    avg_turn = np.convolve(turnover, np.ones(window)/window, mode='same')
    dev = (turnover - avg_turn) / np.maximum(avg_turn, 1e-10)
    sig = np.tanh(dev * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_LONG_TERM_REVERSAL_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 120))
    if n < window + 1:
        return np.zeros(n)
    ret = np.zeros(n)
    ret[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
    sig = -np.tanh(ret / 0.1)
    return np.clip(sig, -1.0, 1.0)
"""

_FUT_OI_CHANGE_RATE_CODE = """
def factor_program(data, params):
    import numpy as np
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(1000))
    n = len(hold)
    window = int(params.get('window', 5))
    if n < window + 1:
        return np.zeros(n)
    oi_chg = np.zeros(n)
    oi_chg[window:] = (hold[window:] - hold[:-window]) / np.maximum(hold[:-window], 1e-10)
    sig = np.tanh(oi_chg * 5)
    return np.clip(sig, -1.0, 1.0)
"""

# ══════════════════════════════════════════════════════════════════════════════
# 家族 14: 算子字典种子因子 (10 个) — 来自 factor_operator_dictionary.xlsx
# ══════════════════════════════════════════════════════════════════════════════

_SEED_KBAR_MID_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    open_ = data['open'].values if hasattr(data, 'open') else data['open']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # K线中点：(close - open) / open
    sig = (close - open_) / np.maximum(open_, 1e-10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_KBAR_UPPER_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    open_ = data['open'].values if hasattr(data, 'open') else data['open']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 上影线：(high - max(open, close)) / open
    sig = (high - np.maximum(open_, close)) / np.maximum(open_, 1e-10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_KBAR_LOWER_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    open_ = data['open'].values if hasattr(data, 'open') else data['open']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 下影线：(min(open, close) - low) / open
    sig = (np.minimum(open_, close) - low) / np.maximum(open_, 1e-10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_KBAR_SHIFT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    open_ = data['open'].values if hasattr(data, 'open') else data['open']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # K线偏移：(2*close - high - low) / open，收盘在K线中的位置
    sig = (2 * close - high - low) / np.maximum(open_, 1e-10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_BULL_BEAR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 多空力量不平衡度：((close - low) - (high - close)) / (high - low + 1e-12)
    hl_range = high - low + 1e-12
    sig = ((close - low) - (high - close)) / hl_range
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_ARGMAX_CLOSE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 20日最高价新鲜度：argmax(close, 20) / 20
    sig = np.zeros(n)
    for i in range(window, n):
        seg = close[i-window+1:i+1]
        sig[i] = np.argmax(seg) / window
    return sig
"""

_SEED_ARGMIN_CLOSE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    window = int(params.get('window', 20))
    if n < window + 1:
        return np.zeros(n)
    # 20日最低价新鲜度：argmin(close, 20) / 20
    sig = np.zeros(n)
    for i in range(window, n):
        seg = close[i-window+1:i+1]
        sig[i] = np.argmin(seg) / window
    return sig
"""

_SEED_VOL_CHG_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    if n < 2:
        return np.zeros(n)
    # 成交量变化率：log(volume) - log(delay(volume, 1))
    sig = np.zeros(n)
    sig[1:] = np.log(np.maximum(volume[1:], 1e-10)) - np.log(np.maximum(volume[:-1], 1e-10))
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VWAP_PROXY_1_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # VWAP粗糙近似：(close - (high+low+close)/3) / ((high+low+close)/3 + 1e-12)
    # ⚠️ 低置信度，VWAP的粗糙近似
    typical_price = (high + low + close) / 3.0
    sig = (close - typical_price) / np.maximum(typical_price, 1e-12)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VWAP_PROXY_2_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    settle = data.get('settle', close).values if hasattr(data.get('settle', close), 'values') else data.get('settle', close)
    n = len(close)
    if n < 6:
        return np.zeros(n)
    # VWAP替代版：rank(close - settle) * rank(settle / delay(settle, 5))
    # ⚠️ 低置信度，用结算价替代VWAP
    close_settle_diff = close - settle
    settle_ratio = settle / np.maximum(np.roll(settle, 5), 1e-10)
    # 简化版rank：用符号代替
    sig = np.sign(close_settle_diff) * np.sign(settle_ratio - 1.0)
    return np.clip(sig, -1.0, 1.0)
"""

# ─── 家族 14 新增因子 (14 个) — 来自 factor_operator_dictionary.xlsx 补充 ───

_SEED_REVERSAL_1D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 1日反转：rank(-delta(close, 1))
    delta_1d = np.zeros(n)
    delta_1d[1:] = close[1:] - close[:-1]
    # 简化版rank：用符号代替
    sig = -np.sign(delta_1d)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_MOM_5D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 6:
        return np.zeros(n)
    # 5日动量：(close - delay(close, 5)) / delay(close, 5)
    delay_5d = np.roll(close, 5)
    delay_5d[:5] = close[:5]  # 前5个用自身填充
    ret = (close - delay_5d) / np.maximum(delay_5d, 1e-10)
    sig = np.tanh(ret * 10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_MOM_20D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 21:
        return np.zeros(n)
    # 20日动量：(close - delay(close, 20)) / delay(close, 20)
    delay_20d = np.roll(close, 20)
    delay_20d[:20] = close[:20]
    ret = (close - delay_20d) / np.maximum(delay_20d, 1e-10)
    sig = np.tanh(ret * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VOL_5D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 6:
        return np.zeros(n)
    # 5日波动率：std(returns, 5)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol = np.array([np.std(returns[max(0,i-4):i+1]) for i in range(n)])
    sig = np.tanh(vol * 20)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VOL_20D_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 21:
        return np.zeros(n)
    # 20日波动率：std(returns, 20)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol = np.array([np.std(returns[max(0,i-19):i+1]) for i in range(n)])
    sig = np.tanh(vol * 10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VOL_RATIO_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 21:
        return np.zeros(n)
    # 波动率比：std(returns, 5) / (std(returns, 20) + 1e-12)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol_5 = np.array([np.std(returns[max(0,i-4):i+1]) for i in range(n)])
    vol_20 = np.array([np.std(returns[max(0,i-19):i+1]) for i in range(n)])
    ratio = vol_5 / (vol_20 + 1e-12)
    sig = np.tanh((ratio - 1.0) * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_TREND_SLOPE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 21:
        return np.zeros(n)
    # 趋势斜率：slope(close, 20) / close
    x = np.arange(20)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    slope = np.zeros(n)
    for i in range(19, n):
        y = close[i-19:i+1]
        y_mean = y.mean()
        slope[i] = np.sum((x - x_mean) * (y - y_mean)) / (x_var + 1e-10)
    sig = np.tanh(slope / close * 100)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_TREND_RSQR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 21:
        return np.zeros(n)
    # 趋势拟合优度：rsquare(close, 20)
    x = np.arange(20)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    rsqr = np.zeros(n)
    for i in range(19, n):
        y = close[i-19:i+1]
        y_mean = y.mean()
        slope = np.sum((x - x_mean) * (y - y_mean)) / (x_var + 1e-10)
        intercept = y_mean - slope * x_mean
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y_mean) ** 2)
        rsqr[i] = 1 - ss_res / (ss_tot + 1e-10)
    sig = np.tanh((rsqr - 0.5) * 4)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VP_CORR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(close)
    window = int(params.get('window', 10))
    if n < window + 1:
        return np.zeros(n)
    # 量价相关性：corr(rank(volume), rank(close), 10)
    # 简化版rank：用归一化值代替
    rank_close = (close - close.mean()) / (close.std() + 1e-10)
    rank_volume = (volume - volume.mean()) / (volume.std() + 1e-10)
    corr = np.zeros(n)
    for i in range(window, n):
        c = rank_close[i-window:i]
        v = rank_volume[i-window:i]
        corr[i] = np.corrcoef(c, v)[0, 1] if np.std(c) > 1e-10 and np.std(v) > 1e-10 else 0
    sig = np.tanh(corr * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_VOL_RATIO_VOLUME_CODE = """
def factor_program(data, params):
    import numpy as np
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    n = len(volume)
    if n < 21:
        return np.zeros(n)
    # 成交量比：volume / mean(volume, 20)
    vol_mean_20 = np.convolve(volume, np.ones(20)/20, mode='same')
    vol_mean_20[:20] = volume[:20]
    ratio = volume / np.maximum(vol_mean_20, 1e-10)
    sig = np.tanh((ratio - 1.0) * 2)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_OI_CHG_CODE = """
def factor_program(data, params):
    import numpy as np
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(len(data['close'])))
    n = len(hold)
    if n < 2:
        return np.zeros(n)
    # 持仓量变化率：(oi - delay(oi, 1)) / (delay(oi, 1) + 1e-12)
    delay_1d = np.roll(hold, 1)
    delay_1d[0] = hold[0]
    chg = (hold - delay_1d) / (delay_1d + 1e-12)
    sig = np.tanh(chg * 5)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_OI_RET_CONFIRM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.zeros(len(close)))
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 持仓确认：sign(close - delay(close,1)) * sign(oi - delay(oi,1))
    close_delta = np.zeros(n)
    close_delta[1:] = close[1:] - close[:-1]
    hold_delta = np.zeros(n)
    hold_delta[1:] = hold[1:] - hold[:-1]
    sig = np.sign(close_delta) * np.sign(hold_delta)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_SPREAD_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if n < 6:
        return np.zeros(n)
    # 期限结构/展期收益：(close_near - close_far) / close_near
    # 用5日均线近似近月，20日均线近似远月
    close_near = np.convolve(close, np.ones(5)/5, mode='same')
    close_far = np.convolve(close, np.ones(20)/20, mode='same')
    spread = (close_near - close_far) / np.maximum(close_near, 1e-10)
    sig = np.tanh(spread * 10)
    return np.clip(sig, -1.0, 1.0)
"""

_SEED_SETTLE_BIAS_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    settle = data.get('settle', close).values if hasattr(data.get('settle', close), 'values') else data.get('settle', close)
    n = len(close)
    if n < 2:
        return np.zeros(n)
    # 结算价偏离：(close - settle) / (settle + 1e-12)
    bias = (close - settle) / (settle + 1e-12)
    sig = np.tanh(bias * 10)
    return np.clip(sig, -1.0, 1.0)
"""


# ══════════════════════════════════════════════════════════════════════════════
# 全量种子因子定义
# ══════════════════════════════════════════════════════════════════════════════

# 总计: 57 个子因子（13 大因子家族）

_FUTURES_FULL_DEFINITIONS: list[dict[str, Any]] = [

    # ─── 家族 1: 动量因子家族 (5 个) ────────────────────
    {
        "name": "fut_xsmom",
        "code": _FUT_XSMOM_CODE,
        "params": {"lookback": 20, "holding": 1},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="截面动量(XSMOM)：做多过去收益高的品种，做空过去收益低的品种。期货月频动量效应显著。"),
    },
    {
        "name": "fut_tsmom",
        "code": _FUT_TSMOM_CODE,
        "params": {"lookback_months": 3, "skip_days": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=85),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="时序动量(TSMOM)：资产自身历史收益为正则做多，为负则做空。AQR 跨资产58种资产25年数据验证。"),
    },
    {
        "name": "fut_short_reversal",
        "code": _FUT_SHORT_REVERSAL_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=4, behavioral=4, microstructure=3, institutional=3,
            narrative="短期反转：3-5日价格反转效应，做多跌多的做空涨多的。与月频动量共存。"),
    },
    {
        "name": "fut_composite_momentum",
        "code": _FUT_COMPOSITE_MOMENTUM_CODE,
        "params": {"lookback": 243, "holding": 10},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=250),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="复合动量：XSMOM+TSMOM 结合，同时考虑截面排序和方向。中信最优参数(J=243,K=10)年化9.61%夏普0.95。"),
    },
    {
        "name": "fut_basis_momentum",
        "code": _FUT_BASIS_MOMENTUM_CODE,
        "params": {"lookback": 243, "holding": 3},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=250),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="基差动量：期限结构同比变化率，剔除季节性影响。中信最优(J=243,K=3)年化6.89%夏普0.81。"),
    },

    # ─── 家族 2: 期限结构因子家族 (3 个) ────────────────
    {
        "name": "fut_roll_yield_carry",
        "code": _FUT_ROLL_YIELD_FULL_CODE,
        "params": {"lookback": 5, "holding": 15},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=5, behavioral=3, microstructure=4, institutional=5,
            narrative="展期收益(Roll Yield/Carry)：Back结构做多获得展期收益，Contango做空。华泰年化9.63%夏普1.94。"),
    },
    {
        "name": "fut_stable_term_structure",
        "code": _FUT_STABLE_TERM_STRUCTURE_CODE,
        "params": {"lookback": 5, "holding": 15},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="稳定样本期限结构：OLS回归判断期限结构稳定性。稳定样本回看5日调仓15日年化12.61%夏普1.19。"),
    },
    {
        "name": "fut_basis_factor",
        "code": _FUT_BASIS_FACTOR_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "settle"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="基差因子：用 close/settle 偏离近似基差，无结算价时用 MA 比替代。"),
    },

    # ─── 家族 3: 持仓/资金流因子家族 (3 个) ─────────────
    {
        "name": "fut_open_interest_full",
        "code": _FUT_OPEN_INTEREST_FULL_CODE,
        "params": {"lookback": 5},
        "signature": FactorSignature(input_fields=["close", "hold"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=5, institutional=4,
            narrative="持仓量因子：持仓增加+价格上涨=多头主导，持仓增加+价格下跌=空头主导。期货特有持仓量因子。"),
    },
    {
        "name": "fut_warehouse_receipt",
        "code": _FUT_WAREHOUSE_RECEIPT_CODE,
        "params": {"lookback": 3, "holding": 1},
        "signature": FactorSignature(input_fields=["close", "hold"], output_type="signal", frequency="daily", lookback=8),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="仓单因子：仓单增加=现货供应增加=利空。中信最优(J=3,K=1)年化10.10%夏普1.48。需AKShare仓单数据注入。"),
    },
    {
        "name": "fut_hedge_pressure",
        "code": _FUT_HEDGE_PRESSURE_CODE,
        "params": {"lookback": 243},
        "signature": FactorSignature(input_fields=["close", "hold"], output_type="signal", frequency="daily", lookback=248),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="对冲压力因子：套保者增加空头(持仓增+价格跌)=对冲压力增大。中信年化6.0%夏普0.90。"),
    },

    # ─── 家族 4: 流动性因子家族 (3 个) ──────────────────
    {
        "name": "fut_turnover",
        "code": _FUT_TURNOVER_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume", "hold"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="换手率因子：成交量/持仓量。2022年后流动性因子表现较好。"),
    },
    {
        "name": "fut_bid_ask_spread",
        "code": _FUT_BID_ASK_SPREAD_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="买卖价差因子：用(high-low)/close近似价差。价差扩大=流动性恶化=做空。"),
    },
    {
        "name": "fut_amihud_full",
        "code": _FUT_AMIHUD_FULL_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="Amihud非流动性因子：|收益率|/成交额。非流动性高=难交易=流动性溢价做空。"),
    },

    # ─── 家族 5: 偏度/峰度/高阶矩因子家族 (3 个) ───────
    {
        "name": "fut_skewness_full",
        "code": _FUT_SKEWNESS_FULL_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="偏度因子：负偏度品种存在风险溢价。期货尾部风险大，负偏度需风险溢价补偿。"),
    },
    {
        "name": "fut_upside_skewness",
        "code": _FUT_UP_SKEWNESS_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="上行偏度因子：仅计算正收益的偏度。中信回看20日持仓4日年化5.5%夏普0.90。"),
    },
    {
        "name": "fut_kurtosis",
        "code": _FUT_KURTOSIS_CODE,
        "params": {"window": 14},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=20),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="峰度因子：高峰度=极端风险=做空。高阶矩因子中表现最优，中信回看14日持仓2日年化6.0%夏普0.95。"),
    },

    # ─── 家族 6: 波动率因子家族 (2 个) ──────────────────
    {
        "name": "fut_cv",
        "code": _FUT_CV_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="变异系数因子(CV)：标准差/均值。中信回看5日年化7.5%夏普0.98。CV高=波动大=均值回归做空。"),
    },
    {
        "name": "fut_downside_volatility",
        "code": _FUT_DOWN_VOL_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="实际下行波动率因子：仅计算负收益部分的波动率。华泰期货，回归系数<0。"),
    },

    # ─── 家族 7: 基本面因子家族 (4 个) ──────────────────
    {
        "name": "fut_volume_price_corr_full",
        "code": _FUT_VOLUME_PRICE_CORR_FULL_CODE,
        "params": {"window": 63},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=68),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="量价相关性因子：筛选上涨放量下跌缩量的品种。中信回看63日年化7%夏普1.1。"),
    },
    {
        "name": "fut_trend_strength",
        "code": _FUT_TREND_STRENGTH_CODE,
        "params": {"window": 243},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=248),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="趋势强度因子：位移/路程比，衡量价格趋势流畅性。中信回看243日年化7%夏普0.9。"),
    },
    {
        "name": "fut_amplitude",
        "code": _FUT_AMPLITUDE_CODE,
        "params": {"window": 63},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=68),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="振幅因子：(high-low)/close 滚动均值。振幅收缩=突破前兆做多，振幅扩大=见顶做空。中信年化9.7%夏普1.3。"),
    },
    {
        "name": "fut_mobile_big_data",
        "code": _FUT_MOBILE_BIG_DATA_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=3, institutional=3,
            narrative="移动大数据因子(近似版)：用量价加速度替代人流/物流数据。东证原版样本外夏普3.19。"),
    },

    # ─── 家族 8: 拥挤度因子家族 (6 个) ──────────────────
    {
        "name": "fut_crowd_volume",
        "code": _FUT_CROWD_VOLUME_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["volume"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="成交额拥挤度：成交量 vs 3年90%分位数。成交拥挤=过热=做空。广发6维度拥挤度之一。"),
    },
    {
        "name": "fut_crowd_volatility",
        "code": _FUT_CROWD_VOLATILITY_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="波动拥挤度：Beta 拥挤度，波动率 vs 历史90%分位数。广发6维度拥挤度之二。"),
    },
    {
        "name": "fut_crowd_turnover",
        "code": _FUT_CROWD_TURNOVER_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["volume", "hold"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="换手率拥挤度：换手率 vs 历史90%分位数。广发行业拥挤度6信号之一。"),
    },
    {
        "name": "fut_crowd_bias_volume",
        "code": _FUT_CROWD_BIAS_VOLUME_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["volume"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="成交量乖离率拥挤度：成交量乖离率 vs 历史90%分位数。广发行业拥挤度6信号之一。"),
    },
    {
        "name": "fut_crowd_bias_amount",
        "code": _FUT_CROWD_BIAS_AMOUNT_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="成交额乖离率拥挤度：成交额乖离率 vs 历史90%分位数。广发行业拥挤度6信号之一。"),
    },
    {
        "name": "fut_crowd_composite",
        "code": _FUT_CROWD_COMPOSITE_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["close", "volume", "hold"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="复合拥挤度因子：3维度(成交量/换手率/成交额)中≥2个拥挤=综合拥挤。广发行业最优参数。"),
    },

    # ─── 家族 9: Alpha/量价行为因子家族 (4 个) ──────────
    {
        "name": "fut_time_series_regression",
        "code": _FUT_TIME_SERIES_REGRESSION_CODE,
        "params": {"lookback": 243, "holding": 10},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=250),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4,
            narrative="时序回归因子：close~time OLS 斜率，综合考虑趋势强度和流畅性(R²加权)。中信年化8.90%夏普1.34。"),
    },
    {
        "name": "fut_bias",
        "code": _FUT_BIAS_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=4, microstructure=3, institutional=3,
            narrative="乖离率因子(BIAS)：价格偏离均线的程度。正乖离=超买做空，负乖离=超卖做多。"),
    },
    {
        "name": "fut_gp_alpha1",
        "code": _FUT_GP_ALPHA1_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "high", "low", "volume"], output_type="signal", frequency="daily", lookback=30),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="GP Alpha 1：遗传规划挖掘的截面alpha。近似(close-vwap)*volume/(high-low)。夏普1.72年化9.32%。"),
    },
    {
        "name": "fut_ht_alpha",
        "code": _FUT_HT_ALPHA_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "high", "low", "volume"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="华泰Alpha因子量价行为：日内强度+量价趋势+动量加速度三维组合。华泰期货Alpha因子。"),
    },

    # ─── 家族 10: 高频因子家族 — 日频近似 (6 个) ───────
    {
        "name": "fut_hf_quote_imbalance",
        "code": _FUT_HF_QUOTE_IMBALANCE_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=5, institutional=2,
            narrative="高频报价不平衡因子(日频近似)：(high-close)/(close-low)近似报价不平衡。华泰最强高频因子之一。"),
    },
    {
        "name": "fut_hf_trade_imbalance",
        "code": _FUT_HF_TRADE_IMBALANCE_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=5, institutional=2,
            narrative="高频成交不平衡因子(日频近似)：价格方向×成交量近似成交不平衡。华泰可复现基础高频因子。"),
    },
    {
        "name": "fut_hf_historical_return",
        "code": _FUT_HF_HISTORICAL_RETURN_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=2,
            narrative="高频历史收益因子(日频近似)：短周期收益率滚动均值。华泰可复现基础高频因子。"),
    },
    {
        "name": "fut_hf_turnover",
        "code": _FUT_HF_TURNOVER_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["volume", "hold"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=2,
            narrative="高频换手率因子(日频近似)：volume/hold 短周期滚动。华泰可复现基础高频因子。"),
    },
    {
        "name": "fut_hf_spread",
        "code": _FUT_HF_SPREAD_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=2,
            narrative="高频报价价差因子(日频近似)：(high-low)/close 短周期滚动。华泰可复现基础高频因子。"),
    },
    {
        "name": "fut_hf_down_vol",
        "code": _FUT_HF_DOWN_VOL_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=2,
            narrative="高频实际下行波动率因子(日频近似)：仅负收益部分波动率。华泰自研130+最强因子之一。"),
    },

    # ─── 家族 11: 期权隐含信息因子家族 — 日频近似 (3 个) ─
    {
        "name": "fut_option_vol_term",
        "code": _FUT_OPTION_VOL_TERM_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="波动率期限结构因子(日频近似)：短波动率-长波动率。华泰当月IV-次月IV年化23.1%。"),
    },
    {
        "name": "fut_option_skew",
        "code": _FUT_OPTION_SKEW_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="IV偏斜因子(日频近似)：上行波动率/下行波动率比。东证47个期权指标中SKEW效果较好。"),
    },
    {
        "name": "fut_option_pcr",
        "code": _FUT_OPTION_PCR_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=3,
            narrative="PCR类因子(日频近似)：下跌日成交量/上涨日成交量比。东证PCR近月优于远月。"),
    },

    # ─── 家族 12: 市场环境因子家族 (8 个) ──────────────
    {
        "name": "fut_macro_cpi",
        "code": _FUT_MACRO_CPI_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="CPI宏观环境因子：CPI上升=通胀压力=商品承压做空。需外部注入cpi数据或降级为趋势近似。"),
    },
    {
        "name": "fut_macro_interest_rate",
        "code": _FUT_MACRO_INTEREST_RATE_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="利率宏观环境因子：利率上升=融资成本增加=商品承压做空。需外部注入rate数据。"),
    },
    {
        "name": "fut_macro_export",
        "code": _FUT_MACRO_EXPORT_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="出口总值宏观因子：出口增长=外需强劲=商品利好做多。需外部注入export数据。"),
    },
    {
        "name": "fut_macro_us_bond",
        "code": _FUT_MACRO_US_BOND_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="美债收益率宏观因子：美债收益率上升=美元走强=商品承压做空。需外部注入us_bond数据。"),
    },
    {
        "name": "fut_mkt_trend",
        "code": _FUT_MKT_TREND_CODE,
        "params": {"window": 60},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=65),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=3, institutional=3,
            narrative="市场趋势强度：R²衡量趋势强度。中信商品市场4因子之一。"),
    },
    {
        "name": "fut_mkt_speculation",
        "code": _FUT_MKT_SPECULATION_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume", "hold"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="市场投机度：成交量/持仓量。投机度高=情绪过热=做空。中信商品市场4因子之一。"),
    },
    {
        "name": "fut_mkt_rotation",
        "code": _FUT_MKT_ROTATION_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=3, institutional=3,
            narrative="市场轮动速度：高频波动/低频波动比。轮动快=风格切换频繁。中信商品市场4因子之一。"),
    },
    {
        "name": "fut_mkt_concentration",
        "code": _FUT_MKT_CONCENTRATION_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="成交集中度：成交额偏离度。成交集中=资金集中=趋势延续。中信商品市场4因子之一。"),
    },

    # ─── 家族 13: CTA注册表补充因子 V2.0 (7 个) ──────────
    {
        "name": "tsmom_5d",
        "code": _FUT_TSMOM_5D_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="5日时序动量：短期价格趋势跟踪。CTA注册表核心因子。"),
    },
    {
        "name": "tsmom_22d",
        "code": _FUT_TSMOM_22D_CODE,
        "params": {"window": 22},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=30),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="22日时序动量：月度价格趋势跟踪。CTA注册表核心因子。"),
    },
    {
        "name": "basis_level",
        "code": _FUT_BASIS_LEVEL_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "settle"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="基差水平因子：(close-settle)/settle 滚动均值。CTA注册表核心因子。"),
    },
    {
        "name": "volatility_annual",
        "code": _FUT_VOLATILITY_ANNUAL_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="年化波动率因子：20日滚动波动率年化。高波动做空（波动率溢价）。CTA注册表核心因子。"),
    },
    {
        "name": "liquidity_ratio",
        "code": _FUT_LIQUIDITY_RATIO_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close", "volume", "hold"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="成交持仓比因子：volume/hold 偏离度。流动性异常信号。CTA注册表核心因子。"),
    },
    {
        "name": "long_term_reversal",
        "code": _FUT_LONG_TERM_REVERSAL_CODE,
        "params": {"window": 120},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=130),
        "economic_logic": EconomicLogic(theory=4, behavioral=4, microstructure=3, institutional=3,
            narrative="长期反转因子：120日收益率反转。做多跌多的做空涨多的。CTA注册表核心因子。"),
    },
    {
        "name": "oi_change_rate",
        "code": _FUT_OI_CHANGE_RATE_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(input_fields=["hold"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="持仓量变化率因子：5日持仓量变化率。资金流入/流出信号。CTA注册表核心因子。"),
    },

    # ─── 家族 14: 算子字典种子因子 (10 个) — 来自 factor_operator_dictionary.xlsx ──
    {
        "name": "seed_kbar_mid",
        "code": _SEED_KBAR_MID_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "open"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="K线中点：(close - open)/open。直接使用，期货K线更连续。来源：Qlib-KMID / GTJA#2。"),
    },
    {
        "name": "seed_kbar_upper",
        "code": _SEED_KBAR_UPPER_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["high", "open", "close"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="上影线：(high - max(open, close)) / open。来源：Qlib-KUP / GTJA系列。"),
    },
    {
        "name": "seed_kbar_lower",
        "code": _SEED_KBAR_LOWER_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["open", "close", "low"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="下影线：(min(open, close) - low) / open。来源：Qlib-KLOW / GTJA系列。"),
    },
    {
        "name": "seed_kbar_shift",
        "code": _SEED_KBAR_SHIFT_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "open", "high", "low"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=4, institutional=3,
            narrative="K线偏移：(2*close - high - low) / open。收盘在K线中的位置。来源：Qlib-KSFT / GTJA系列。"),
    },
    {
        "name": "seed_bull_bear",
        "code": _SEED_BULL_BEAR_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=1),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="多空力量不平衡度：((close - low) - (high - close)) / (high - low)。来源：GTJA#2 / Alpha#2。"),
    },
    {
        "name": "seed_argmax_close",
        "code": _SEED_ARGMAX_CLOSE_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="20日最高价新鲜度：argmax(close, 20) / 20。来源：WQ#1 / Qlib-IMAX20。"),
    },
    {
        "name": "seed_argmin_close",
        "code": _SEED_ARGMIN_CLOSE_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="20日最低价新鲜度：argmin(close, 20) / 20。来源：Qlib-IMIN20。"),
    },
    {
        "name": "seed_vol_chg",
        "code": _SEED_VOL_CHG_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["volume"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="成交量变化率：log(volume) - log(delay(volume, 1))。来源：WQ#2 / GTJA#1。"),
    },
    {
        "name": "seed_vwap_proxy_1",
        "code": _SEED_VWAP_PROXY_1_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "high", "low"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=2, behavioral=3, microstructure=3, institutional=2,
            narrative="VWAP粗糙近似：(close - (H+L+C)/3) / ((H+L+C)/3)。⚠️ 低置信度，VWAP的粗糙近似。来源：WQ#5改造。"),
    },
    {
        "name": "seed_vwap_proxy_2",
        "code": _SEED_VWAP_PROXY_2_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "settle"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=2, behavioral=3, microstructure=3, institutional=2,
            narrative="VWAP替代版：sign(close-settle) * sign(settle/delay(settle,5)-1)。⚠️ 低置信度，用结算价替代VWAP。来源：WQ#11改造。"),
    },
    # ─── 家族 14 新增因子 (14 个) — 来自 factor_operator_dictionary.xlsx 补充 ──
    {
        "name": "seed_reversal_1d",
        "code": _SEED_REVERSAL_1D_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=4, behavioral=4, microstructure=3, institutional=3,
            narrative="1日反转：rank(-delta(close, 1))。来源：WQ#4 / WQ#38简化。"),
    },
    {
        "name": "seed_mom_5d",
        "code": _SEED_MOM_5D_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="5日动量：(close - delay(close, 5)) / delay(close, 5)。来源：Qlib-ROC5 / WQ#24简化。"),
    },
    {
        "name": "seed_mom_20d",
        "code": _SEED_MOM_20D_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="20日动量：(close - delay(close, 20)) / delay(close, 20)。来源：Qlib-ROC20。"),
    },
    {
        "name": "seed_vol_5d",
        "code": _SEED_VOL_5D_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="5日波动率：std(returns, 5)。来源：Qlib-STD5 / WQ#34。"),
    },
    {
        "name": "seed_vol_20d",
        "code": _SEED_VOL_20D_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="20日波动率：std(returns, 20)。来源：Qlib-STD20 / WQ#22。"),
    },
    {
        "name": "seed_vol_ratio",
        "code": _SEED_VOL_RATIO_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3,
            narrative="波动率比：std(returns, 5) / (std(returns, 20) + 1e-12)。来源：WQ#34 / GTJA#55。"),
    },
    {
        "name": "seed_trend_slope",
        "code": _SEED_TREND_SLOPE_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="趋势斜率：slope(close, 20) / close。来源：Qlib-BETA20。"),
    },
    {
        "name": "seed_trend_rsqr",
        "code": _SEED_TREND_RSQR_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="趋势拟合优度：rsquare(close, 20)。来源：Qlib-RSQR20。"),
    },
    {
        "name": "seed_vp_corr",
        "code": _SEED_VP_CORR_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(input_fields=["close", "volume"], output_type="signal", frequency="daily", lookback=15),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="量价相关性：corr(rank(volume), rank(close), 10)。来源：WQ#3 / WQ#6 / GTJA#14。"),
    },
    {
        "name": "seed_vol_ratio_volume",
        "code": _SEED_VOL_RATIO_VOLUME_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["volume"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="成交量比：volume / mean(volume, 20)。来源：Qlib-VMA20 / WQ#21。"),
    },
    {
        "name": "seed_oi_chg",
        "code": _SEED_OI_CHG_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["hold"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="持仓量变化率：(oi - delay(oi, 1)) / (delay(oi, 1) + 1e-12)。期货特有。"),
    },
    {
        "name": "seed_oi_ret_confirm",
        "code": _SEED_OI_RET_CONFIRM_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "hold"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=4,
            narrative="持仓确认：sign(close - delay(close,1)) * sign(oi - delay(oi,1))。期货特有。"),
    },
    {
        "name": "seed_spread",
        "code": _SEED_SPREAD_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=25),
        "economic_logic": EconomicLogic(theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="期限结构/展期收益：(close_near - close_far) / close_near。期货特有。"),
    },
    {
        "name": "seed_settle_bias",
        "code": _SEED_SETTLE_BIAS_CODE,
        "params": {},
        "signature": FactorSignature(input_fields=["close", "settle"], output_type="signal", frequency="daily", lookback=5),
        "economic_logic": EconomicLogic(theory=3, behavioral=4, microstructure=4, institutional=3,
            narrative="结算价偏离：(close - settle) / (settle + 1e-12)。期货特有。"),
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# 家族映射表 — 用于加载时的分家族日志追踪
# ══════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)

_FACTOR_FAMILY_MAP: dict[str, tuple[int, str]] = {}

_FAMILY_SUMMARY: dict[int, tuple[str, list[str]]] = {
    1: ("动量因子家族", ["fut_xsmom", "fut_tsmom", "fut_short_reversal", "fut_composite_momentum", "fut_basis_momentum"]),
    2: ("期限结构因子家族", ["fut_roll_yield_carry", "fut_stable_term_structure", "fut_basis_factor"]),
    3: ("持仓/资金流因子家族", ["fut_open_interest_full", "fut_warehouse_receipt", "fut_hedge_pressure"]),
    4: ("流动性因子家族", ["fut_turnover", "fut_bid_ask_spread", "fut_amihud_full"]),
    5: ("偏度/峰度/高阶矩因子家族", ["fut_skewness_full", "fut_upside_skewness", "fut_kurtosis"]),
    6: ("波动率因子家族", ["fut_cv", "fut_downside_volatility"]),
    7: ("基本面因子家族", ["fut_volume_price_corr_full", "fut_trend_strength", "fut_amplitude", "fut_mobile_big_data"]),
    8: ("拥挤度因子家族", ["fut_crowd_volume", "fut_crowd_volatility", "fut_crowd_turnover", "fut_crowd_bias_volume", "fut_crowd_bias_amount", "fut_crowd_composite"]),
    9: ("Alpha/量价行为因子家族", ["fut_time_series_regression", "fut_bias", "fut_gp_alpha1", "fut_ht_alpha"]),
    10: ("高频因子家族", ["fut_hf_quote_imbalance", "fut_hf_trade_imbalance", "fut_hf_historical_return", "fut_hf_turnover", "fut_hf_spread", "fut_hf_down_vol"]),
    11: ("期权隐含信息因子家族", ["fut_option_vol_term", "fut_option_skew", "fut_option_pcr"]),
    12: ("市场环境因子家族", ["fut_macro_cpi", "fut_macro_interest_rate", "fut_macro_export", "fut_macro_us_bond", "fut_mkt_trend", "fut_mkt_speculation", "fut_mkt_rotation", "fut_mkt_concentration"]),
    13: ("CTA注册表补充因子 V2.0", ["tsmom_5d", "tsmom_22d", "basis_level", "volatility_annual", "liquidity_ratio", "long_term_reversal", "oi_change_rate"]),
    14: ("算子字典种子因子", ["seed_kbar_mid", "seed_kbar_upper", "seed_kbar_lower", "seed_kbar_shift", "seed_bull_bear", "seed_argmax_close", "seed_argmin_close", "seed_vol_chg", "seed_vwap_proxy_1", "seed_vwap_proxy_2", "seed_reversal_1d", "seed_mom_5d", "seed_mom_20d", "seed_vol_5d", "seed_vol_20d", "seed_vol_ratio", "seed_trend_slope", "seed_trend_rsqr", "seed_vp_corr", "seed_vol_ratio_volume", "seed_oi_chg", "seed_oi_ret_confirm", "seed_spread", "seed_settle_bias"]),
}

for _fid, (_fname, _factors) in _FAMILY_SUMMARY.items():
    for _fn in _factors:
        _FACTOR_FAMILY_MAP[_fn] = (_fid, _fname)


# ─── 加载器 ───────────────────────────────────────────────

def load_futures_seeds_full(
    trace_id: Optional[str] = None,
) -> list[FactorProgram]:
    """加载全量期货种子因子（14大因子家族，81个子因子）。

    Args:
        trace_id: 全链路 trace_id。

    Returns:
        list[FactorProgram] — 81 个期货专用种子因子。
    """
    from .factor_program import create_factor_program

    logger.info(
        "[futures_seed] 开始加载 14 大因子家族 (总计 81 个因子), trace_id=%s", trace_id,
    )

    result: list[FactorProgram] = []
    family_loaded: dict[int, int] = {}

    for defn in _FUTURES_FULL_DEFINITIONS:
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

        # ── 分家族加载进度追踪 ──
        factor_name = defn["name"]
        if factor_name in _FACTOR_FAMILY_MAP:
            fid, fname = _FACTOR_FAMILY_MAP[factor_name]
            family_loaded[fid] = family_loaded.get(fid, 0) + 1
            expected_count = len(_FAMILY_SUMMARY[fid][1])
            if family_loaded[fid] == expected_count:
                logger.info(
                    "[futures_seed] ★ 家族 %2d 加载完成: %s (%d/%d 个因子), trace_id=%s",
                    fid, fname, expected_count, expected_count, trace_id,
                )

    # ── 最终汇总验证 ──
    total = len(result)
    logger.info(
        "[futures_seed] ✅ 全部加载完成: 总计 %d 个因子, 涉及 %d 个家族, trace_id=%s",
        total, len(family_loaded), trace_id,
    )

    # 校验: 确保所有 14 个家族都已加载
    missing_families = [fid for fid in range(1, 15) if fid not in family_loaded]
    if missing_families:
        logger.error(
            "[futures_seed] ❌ 缺少家族: %s, trace_id=%s", missing_families, trace_id,
        )
    elif total != 81:
        logger.warning(
            "[futures_seed] ⚠ 因子总数异常: 期望 81, 实际 %d, trace_id=%s", total, trace_id,
        )

    return result


def get_futures_full_seed_count() -> int:
    """返回全量期货种子因子数量。"""
    return len(_FUTURES_FULL_DEFINITIONS)


__all__ = [
    "load_futures_seeds_full",
    "get_futures_full_seed_count",
]