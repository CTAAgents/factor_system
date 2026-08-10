"""extract_vnpy_cta_factors.py — 从 vnpy ArrayManager 提取 CTA 技术指标因子

vnpy 的 ArrayManager 类封装了基于 TA-Lib 的经典 CTA 技术指标。
本脚本将这些指标转换为 FTS 的 SeedCandidate 格式，与现有期货种子因子去重后，
生成 vnpy_cta.yaml 种子文件。

vnpy ArrayManager 指标列表（基于 TA-Lib）:
  趋势类: SMA, EMA, WMA, MACD, TRIX, SAR
  摆动类: RSI, KDJ(Stoch), Williams %R, CCI, Ultimate Oscillator
  通道类: Bollinger Bands, Keltner Channel, Donchian Channel
  趋势强度: ADX, Aroon
  量价类: OBV, MFI
  波动类: ATR, NATR, StdDev
  其他: ROC, APO, PPO, LinearReg, Correlation
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ─── 输出路径 ─────────────────────────────────────────────────
SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "futures"
OUTPUT_FILE = SEEDS_DIR / "vnpy_cta.yaml"

# ─── 指标定义 — 每个指标对应一个因子 ─────────────────────────

# 每个指标定义为一个 dict:
#   name: 因子名（fut_ 前缀）
#   description: 中文描述
#   code: factor_program 函数体
#   params: 默认参数
#   input_fields: 输入字段
#   lookback: 最大回看窗口
#   economic_logic: 经济逻辑评分

# 现有 125 个期货因子名前缀列表（用于去重检查）
_EXISTING_NAMES: set[str] = set()


# 辅助函数：生成标准因子代码模板
def _make_code(
    impl: str,
    input_fields: list[str],
    params_code: str = "",
) -> str:
    """生成标准 factor_program 代码。

    Args:
        impl: 指标计算实现代码（Python 代码块）
        input_fields: 输入字段列表
        params_code: 参数提取代码
    """
    field_assignments = []
    for f in input_fields:
        if f == "close":
            field_assignments.append("close = data['close'].values if hasattr(data, 'close') else data['close']")
        elif f == "high":
            field_assignments.append("high = data['high'].values if hasattr(data, 'high') else data['high']")
        elif f == "low":
            field_assignments.append("low = data['low'].values if hasattr(data, 'low') else data['low']")
        elif f == "open":
            field_assignments.append("open_ = data['open'].values if hasattr(data, 'open') else data['open']")
        elif f == "volume":
            field_assignments.append("volume = data['volume'].values if hasattr(data, 'volume') else data['volume']")
        elif f == "vwap":
            field_assignments.append(
                "vwap = data.get('vwap', close).values if hasattr(data, 'vwap') else data.get('vwap', close)"
            )

    fields_str = "\n    ".join(field_assignments)
    fields_str = f"\n    {fields_str}" if fields_str else ""

    code = (
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    n = len(data['close'].values if hasattr(data, 'close') else data['close'])\n"
    )
    if fields_str:
        code += fields_str + "\n"
    if params_code:
        code += params_code + "\n"
    code += impl + "\n"
    code += "    return np.clip(np.nan_to_num(signal, nan=0.0), -1.0, 1.0)"
    return code


# ─── 因子定义 ──────────────────────────────────────────────────

FACTOR_DEFINITIONS: list[dict[str, Any]] = [
    # ── 1. RSI — 相对强弱指数 ──
    {
        "name": "fut_rsi",
        "description": "RSI因子：相对强弱指数。RSI<30超卖做多，RSI>70超买做空。CTA经典反转信号。",
        "input_fields": ["close"],
        "params": {"window": 14, "oversold": 30, "overbought": 70},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))
    oversold = float(params.get('oversold', 30))
    overbought = float(params.get('overbought', 70))

    # 计算价格变化
    delta = np.zeros(n)
    delta[1:] = close[1:] - close[:-1]

    # 上涨和下跌
    gain = np.maximum(delta, 0)
    loss = -np.minimum(delta, 0)

    # 平均上涨/下跌（SMA）
    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)

    for i in range(min(window, n)):
        avg_gain[i] = np.mean(gain[max(0, i-window+1):i+1]) if i > 0 else 0
        avg_loss[i] = np.mean(loss[max(0, i-window+1):i+1]) if i > 0 else 0

    for i in range(window, n):
        avg_gain[i] = (avg_gain[i-1] * (window - 1) + gain[i]) / window
        avg_loss[i] = (avg_loss[i-1] * (window - 1) + loss[i]) / window

    # RSI 计算
    rs = np.zeros(n)
    mask = avg_loss > 1e-10
    rs[mask] = avg_gain[mask] / avg_loss[mask]
    rsi = 100 - (100 / (1 + rs))

    # 超买做空 (-1), 超卖做多 (+1)
    signal = np.where(rsi > overbought, -1.0, np.where(rsi < oversold, 1.0, 0.0))
    # 中间区域线性映射
    mid_mask = (rsi >= oversold) & (rsi <= overbought)
    signal[mid_mask] = (oversold + overbought - 2 * rsi[mid_mask]) / (overbought - oversold)
    signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "RSI因子：相对强弱指数。RSI<30超卖做多，RSI>70超买做空。CTA经典反转信号。",
        },
    },
    # ── 2. Bollinger Bands — 布林带突破 ──
    {
        "name": "fut_bollinger",
        "description": "布林带因子：价格突破上下轨做趋势跟踪，带宽收缩做均值回归。CTA经典波动率通道策略。",
        "input_fields": ["close"],
        "params": {"window": 20, "num_std": 2.0},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))
    num_std = float(params.get('num_std', 2.0))

    # 计算布林带
    sma = np.zeros(n)
    std = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        sma[i] = np.mean(seg)
        std[i] = np.std(seg, ddof=0)

    upper = sma + num_std * std
    lower = sma - num_std * std

    # 价格突破上轨做多，突破下轨做空
    # 越靠近上轨信号越正，越靠近下轨信号越负
    bandwidth = np.maximum(upper - lower, 1e-10)
    signal = (close - sma) / (bandwidth / 2)
    signal = np.clip(signal, -1.0, 1.0)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "布林带因子：价格突破上下轨做趋势跟踪，带宽收缩做均值回归。CTA经典波动率通道策略。",
        },
    },
    # ── 3. ATR — 平均真实波动范围 ──
    {
        "name": "fut_atr",
        "description": "ATR因子：平均真实波动范围。ATR高=波动大=降低仓位偏空，ATR低=波动小=趋势延续偏多。CTA波动率管理核心。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # 计算真实波动范围
    high_low = np.zeros(n)
    high_close = np.zeros(n)
    low_close = np.zeros(n)
    high_low[1:] = high[1:] - low[1:]
    high_close[1:] = np.abs(high[1:] - close[:-1])
    low_close[1:] = np.abs(low[1:] - close[:-1])

    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    # ATR = SMA of TR
    atr = np.zeros(n)
    for i in range(min(window, n)):
        if i > 0:
            atr[i] = np.mean(tr[max(0, i-window+1):i+1])
    for i in range(window, n):
        atr[i] = (atr[i-1] * (window - 1) + tr[i]) / window

    # ATR 归一化：高波动偏空，低波动偏多
    norm_atr = atr / np.maximum(close, 1e-10)
    signal = -np.tanh(norm_atr * 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "ATR因子：平均真实波动范围。ATR高=波动大=降低仓位偏空，ATR低=波动小=趋势延续偏多。CTA波动率管理核心。",
        },
    },
    # ── 4. Keltner Channel — 肯特纳通道突破 ──
    {
        "name": "fut_keltner",
        "description": "肯特纳通道因子：基于EMA+ATR的通道突破系统。突破上轨做多，突破下轨做空。CTA经典突破策略。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 20, "atr_window": 10, "multiplier": 2.0},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))
    atr_window = int(params.get('atr_window', 10))
    multiplier = float(params.get('multiplier', 2.0))

    # EMA 中线
    alpha = 2.0 / (window + 1)
    ema = np.zeros(n)
    ema[0] = close[0]
    for i in range(1, n):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i-1]

    # ATR 计算
    high_low = np.zeros(n)
    high_close = np.zeros(n)
    low_close = np.zeros(n)
    high_low[1:] = high[1:] - low[1:]
    high_close[1:] = np.abs(high[1:] - close[:-1])
    low_close[1:] = np.abs(low[1:] - close[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    atr = np.zeros(n)
    for i in range(min(atr_window, n)):
        if i > 0:
            atr[i] = np.mean(tr[max(0, i-atr_window+1):i+1])
    for i in range(atr_window, n):
        atr[i] = (atr[i-1] * (atr_window - 1) + tr[i]) / atr_window

    upper = ema + multiplier * atr
    lower = ema - multiplier * atr

    # 突破上轨做多，突破下轨做空
    signal = np.where(close > upper, 1.0, np.where(close < lower, -1.0, 0.0))
    # 通道内线性映射
    in_channel = (close >= lower) & (close <= upper)
    half_width = np.maximum(upper - lower, 1e-10)
    signal[in_channel] = 2 * (close[in_channel] - ema[in_channel]) / half_width[in_channel]
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "肯特纳通道因子：基于EMA+ATR的通道突破系统。突破上轨做多，突破下轨做空。CTA经典突破策略。",
        },
    },
    # ── 5. Donchian Channel — 唐奇安通道（海龟交易法） ──
    {
        "name": "fut_donchian",
        "description": "唐奇安通道因子：N日最高价突破做多，N日最低价突破做空。海龟交易法则核心。CTA经典趋势跟踪系统。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # 计算唐奇安通道
    upper = np.zeros(n)
    lower = np.zeros(n)
    mid = np.zeros(n)
    for i in range(window, n):
        upper[i] = np.max(high[max(0, i-window+1):i+1])
        lower[i] = np.min(low[max(0, i-window+1):i+1])
        mid[i] = (upper[i] + lower[i]) / 2

    # 突破上轨做多，突破下轨做空
    signal = np.where(close > upper, 1.0, np.where(close < lower, -1.0, 0.0))
    # 通道内：价格在通道中的位置
    in_channel = (close >= lower) & (close <= upper) & (upper > lower)
    range_width = np.maximum(upper[in_channel] - lower[in_channel], 1e-10)
    signal[in_channel] = 2 * (close[in_channel] - mid[in_channel]) / range_width
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 4,
            "narrative": "唐奇安通道因子：N日最高价突破做多，N日最低价突破做空。海龟交易法则核心。CTA经典趋势跟踪系统。",
        },
    },
    # ── 6. ADX — 平均趋向指数 ──
    {
        "name": "fut_adx",
        "description": "ADX因子：平均趋向指数。ADX>25=强趋势做动量，ADX<20=弱趋势做反转。CTA趋势强度判断核心。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # 计算方向运动
    up_move = np.zeros(n)
    down_move = np.zeros(n)
    up_move[1:] = high[1:] - high[:-1]
    down_move[1:] = low[:-1] - low[1:]

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        if up_move[i] > down_move[i] and up_move[i] > 0:
            plus_dm[i] = up_move[i]
        if down_move[i] > up_move[i] and down_move[i] > 0:
            minus_dm[i] = down_move[i]

    # ATR 用于归一化
    high_low = np.zeros(n)
    high_close = np.zeros(n)
    low_close = np.zeros(n)
    high_low[1:] = high[1:] - low[1:]
    high_close[1:] = np.abs(high[1:] - close[:-1])
    low_close[1:] = np.abs(low[1:] - close[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    # 平滑
    atr = np.zeros(n)
    pdi = np.zeros(n)
    ndi = np.zeros(n)
    for i in range(min(window, n)):
        if i > 0:
            atr[i] = np.mean(tr[max(0, i-window+1):i+1])
            pdi[i] = 100 * np.mean(plus_dm[max(0, i-window+1):i+1]) / max(atr[i], 1e-10)
            ndi[i] = 100 * np.mean(minus_dm[max(0, i-window+1):i+1]) / max(atr[i], 1e-10)
    for i in range(window, n):
        atr[i] = (atr[i-1] * (window - 1) + tr[i]) / window
        pdi[i] = 100 * (pdi[i-1] * (window - 1) + plus_dm[i]) / max(atr[i] * window, 1e-10)
        ndi[i] = 100 * (ndi[i-1] * (window - 1) + minus_dm[i]) / max(atr[i] * window, 1e-10)

    # ADX = abs(+DI - -DI) / (+DI + -DI) 的平滑
    dx = np.zeros(n)
    di_sum = pdi + ndi
    mask = di_sum > 1e-10
    dx[mask] = 100 * np.abs(pdi[mask] - ndi[mask]) / di_sum[mask]

    adx = np.zeros(n)
    for i in range(min(window, n)):
        if i > 0:
            adx[i] = np.mean(dx[max(0, i-window+1):i+1])
    for i in range(window, n):
        adx[i] = (adx[i-1] * (window - 1) + dx[i]) / window

    # ADX>25 强趋势做动量方向，ADX<20 弱趋势偏向反转
    # 结合 +DI/-DI 方向
    trend_bias = np.where(pdi > ndi, 1.0, -1.0)
    signal = np.where(adx > 25, trend_bias, np.where(adx < 20, -trend_bias * 0.5, 0.0))
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "ADX因子：平均趋向指数。ADX>25=强趋势做动量，ADX<20=弱趋势做反转。CTA趋势强度判断核心。",
        },
    },
    # ── 7. CCI — 商品通道指数 ──
    {
        "name": "fut_cci",
        "description": "CCI因子：商品通道指数。CCI>100超买做空，CCI<-100超卖做多。CTA经典摆动指标。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # 典型价格 = (H + L + C) / 3
    tp = (high + low + close) / 3

    # SMA of TP
    sma_tp = np.zeros(n)
    mean_dev = np.zeros(n)
    for i in range(window, n):
        seg = tp[max(0, i-window+1):i+1]
        sma_tp[i] = np.mean(seg)
        mean_dev[i] = np.mean(np.abs(seg - sma_tp[i]))

    # CCI = (TP - SMA(TP)) / (0.015 * Mean Deviation)
    cci = np.zeros(n)
    denom = 0.015 * np.maximum(mean_dev, 1e-10)
    cci[window:] = (tp[window:] - sma_tp[window:]) / denom[window:]

    # CCI>100 超买做空，CCI<-100 超卖做多
    signal = np.where(cci > 100, -1.0, np.where(cci < -100, 1.0, 0.0))
    # 中间区域线性映射
    mid = np.abs(cci) <= 100
    signal[mid] = -cci[mid] / 100.0
    signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "CCI因子：商品通道指数。CCI>100超买做空，CCI<-100超卖做多。CTA经典摆动指标。",
        },
    },
    # ── 8. KDJ (Stochastic) — 随机指标 ──
    {
        "name": "fut_kdj",
        "description": "KDJ因子：随机指标。K线向上突破D线金叉做多，死叉做空。CTA经典短线反转信号。",
        "input_fields": ["close", "high", "low"],
        "params": {"k_window": 9, "d_window": 3},
        "lookback": 15,
        "impl": """
    k_window = int(params.get('k_window', 9))
    d_window = int(params.get('d_window', 3))

    # 计算 K 值
    k = np.zeros(n)
    for i in range(k_window, n):
        hh = np.max(high[max(0, i-k_window+1):i+1])
        ll = np.min(low[max(0, i-k_window+1):i+1])
        if hh > ll:
            k[i] = 100 * (close[i] - ll) / (hh - ll)
        else:
            k[i] = 50

    # D 值 = K 的 SMA
    d = np.zeros(n)
    for i in range(d_window, n):
        d[i] = np.mean(k[max(0, i-d_window+1):i+1])

    # K > D 金叉做多，K < D 死叉做空
    # 超买区 (K>80) 做空，超卖区 (K<20) 做多
    signal = np.where(k > 80, -1.0, np.where(k < 20, 1.0, 0.0))
    # 结合 K-D 交叉
    crossover = np.sign(k - d)
    zero_mask = np.abs(signal) < 0.01
    signal[zero_mask] = crossover[zero_mask] * 0.5
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "KDJ因子：随机指标。K线向上突破D线金叉做多，死叉做空。CTA经典短线反转信号。",
        },
    },
    # ── 9. Williams %R — 威廉指标 ──
    {
        "name": "fut_williams",
        "description": "威廉%R因子：Williams %R。%R>-20超买做空，%R<-80超卖做多。CTA短线反转信号。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # Williams %R = (HH - Close) / (HH - LL) * -100
    wr = np.zeros(n)
    for i in range(window, n):
        hh = np.max(high[max(0, i-window+1):i+1])
        ll = np.min(low[max(0, i-window+1):i+1])
        if hh > ll:
            wr[i] = -100 * (hh - close[i]) / (hh - ll)
        else:
            wr[i] = -50

    # %R > -20 超买做空，%R < -80 超卖做多
    signal = np.where(wr > -20, -1.0, np.where(wr < -80, 1.0, 0.0))
    # 中间区域线性映射
    mid = (wr >= -80) & (wr <= -20)
    signal[mid] = (-wr[mid] - 50) / 30.0
    signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "威廉%R因子：Williams %R。%R>-20超买做空，%R<-80超卖做多。CTA短线反转信号。",
        },
    },
    # ── 10. MFI — 资金流量指标 ──
    {
        "name": "fut_mfi",
        "description": "MFI因子：资金流量指标。量价结合的RSI。MFI>80超买做空，MFI<20超卖做多。CTA量价分析核心。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # 典型价格
    tp = (high + low + close) / 3

    # 资金流 = TP * Volume
    money_flow = tp * volume

    # 正/负资金流
    pos_mf = np.zeros(n)
    neg_mf = np.zeros(n)
    for i in range(1, n):
        if tp[i] > tp[i-1]:
            pos_mf[i] = money_flow[i]
        elif tp[i] < tp[i-1]:
            neg_mf[i] = money_flow[i]

    # MF Ratio = Sum(Positive MF) / Sum(Negative MF)
    mfr = np.zeros(n)
    for i in range(window, n):
        pos_sum = np.sum(pos_mf[max(0, i-window+1):i+1])
        neg_sum = np.sum(neg_mf[max(0, i-window+1):i+1])
        if neg_sum > 1e-10:
            mfr[i] = pos_sum / neg_sum
        else:
            mfr[i] = 100.0

    # MFI = 100 - 100 / (1 + MFR)
    mfi = 100 - 100 / (1 + mfr)

    # MFI>80 超买做空，MFI<20 超卖做多
    signal = np.where(mfi > 80, -1.0, np.where(mfi < 20, 1.0, 0.0))
    mid = (mfi >= 20) & (mfi <= 80)
    signal[mid] = (20 + 80 - 2 * mfi[mid]) / 60.0
    signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "MFI因子：资金流量指标。量价结合的RSI。MFI>80超买做空，MFI<20超卖做多。CTA量价分析核心。",
        },
    },
    # ── 11. OBV — 能量潮 ──
    {
        "name": "fut_obv",
        "description": "OBV因子：能量潮。OBV与价格背离做反转，OBV趋势确认做动量。CTA量价配合核心指标。",
        "input_fields": ["close", "volume"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # OBV 计算
    obv = np.zeros(n)
    obv[0] = volume[0]
    for i in range(1, n):
        if close[i] > close[i-1]:
            obv[i] = obv[i-1] + volume[i]
        elif close[i] < close[i-1]:
            obv[i] = obv[i-1] - volume[i]
        else:
            obv[i] = obv[i-1]

    # OBV 与价格的趋势一致性
    # 计算 OBV 和价格的斜率方向
    obv_slope = np.zeros(n)
    price_slope = np.zeros(n)
    for i in range(window, n):
        obv_slope[i] = (obv[i] - obv[i-window]) / max(obv[i-window], 1e-10)
        price_slope[i] = (close[i] - close[i-window]) / max(close[i-window], 1e-10)

    # 正相关 = 趋势确认（动量），负相关 = 背离（反转）
    corr = obv_slope * price_slope
    signal = np.tanh(corr * 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "OBV因子：能量潮。OBV与价格背离做反转，OBV趋势确认做动量。CTA量价配合核心指标。",
        },
    },
    # ── 12. Parabolic SAR — 抛物线停损 ──
    {
        "name": "fut_sar",
        "description": "SAR因子：抛物线停损转向。价格在SAR上方做多，下方做空。CTA经典趋势跟踪止损系统。",
        "input_fields": ["close", "high", "low"],
        "params": {"acceleration": 0.02, "max_acceleration": 0.2},
        "lookback": 30,
        "impl": """
    acceleration = float(params.get('acceleration', 0.02))
    max_acc = float(params.get('max_acceleration', 0.2))

    # 简化 SAR 计算
    sar = np.zeros(n)
    ep = np.zeros(n)  # 极值点
    af = np.zeros(n)  # 加速因子
    trend = np.zeros(n)  # 1=上升, -1=下降

    # 初始化
    if n > 1:
        trend[0] = 1 if close[0] <= close[1] else -1
        if trend[0] > 0:
            sar[0] = np.min(low[:2])
            ep[0] = np.max(high[:2])
        else:
            sar[0] = np.max(high[:2])
            ep[0] = np.min(low[:2])
        af[0] = acceleration

    for i in range(1, n):
        sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])

        if trend[i-1] > 0:  # 上升趋势
            sar[i] = min(sar[i], low[i-1], low[i-2] if i >= 2 else low[i-1])
        else:  # 下降趋势
            sar[i] = max(sar[i], high[i-1], high[i-2] if i >= 2 else high[i-1])

        # 反转判断
        if trend[i-1] > 0 and close[i] < sar[i]:
            trend[i] = -1
            sar[i] = ep[i-1]
            ep[i] = low[i]
            af[i] = acceleration
        elif trend[i-1] < 0 and close[i] > sar[i]:
            trend[i] = 1
            sar[i] = ep[i-1]
            ep[i] = high[i]
            af[i] = acceleration
        else:
            trend[i] = trend[i-1]
            af[i] = min(af[i-1] + acceleration, max_acc)
            if trend[i] > 0:
                ep[i] = max(ep[i-1], high[i])
            else:
                ep[i] = min(ep[i-1], low[i])

    # 价格在SAR上方做多，下方做空
    signal = np.where(close > sar, np.tanh((close - sar) / np.maximum(sar, 1e-10) * 10),
                      -np.tanh((sar - close) / np.maximum(sar, 1e-10) * 10))
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "SAR因子：抛物线停损转向。价格在SAR上方做多，下方做空。CTA经典趋势跟踪止损系统。",
        },
    },
    # ── 13. Aroon — 阿隆指标 ──
    {
        "name": "fut_aroon",
        "description": "Aroon因子：阿隆指标。AroonUp>AroonDown做多，反之做空。CTA趋势方向判断。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 25},
        "lookback": 30,
        "impl": """
    window = int(params.get('window', 25))

    aroon_up = np.zeros(n)
    aroon_down = np.zeros(n)

    for i in range(window, n):
        seg_high = high[max(0, i-window+1):i+1]
        seg_low = low[max(0, i-window+1):i+1]
        hh_idx = np.argmax(seg_high)
        ll_idx = np.argmin(seg_low)
        aroon_up[i] = 100 * (window - 1 - hh_idx) / (window - 1)
        aroon_down[i] = 100 * (window - 1 - ll_idx) / (window - 1)

    # AroonUp > AroonDown 做多，反之做空
    diff = aroon_up - aroon_down
    signal = np.tanh(diff / 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Aroon因子：阿隆指标。AroonUp>AroonDown做多，反之做空。CTA趋势方向判断。",
        },
    },
    # ── 14. TRIX — 三重指数移动平均 ──
    {
        "name": "fut_trix",
        "description": "TRIX因子：三重指数移动平均。TRIX上穿Signal做多，下穿做空。CTA趋势跟踪指标。",
        "input_fields": ["close"],
        "params": {"window": 15, "signal_window": 9},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 15))
    signal_window = int(params.get('signal_window', 9))

    # 三次 EMA
    def _ema(arr, w):
        a = 2.0 / (w + 1)
        result = np.zeros_like(arr)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = a * arr[i] + (1 - a) * result[i-1]
        return result

    ema1 = _ema(close, window)
    ema2 = _ema(ema1, window)
    ema3 = _ema(ema2, window)

    # TRIX = EMA3 的百分比变化率
    trix = np.zeros(n)
    trix[1:] = (ema3[1:] - ema3[:-1]) / np.maximum(ema3[:-1], 1e-10) * 100

    # Signal 线
    signal_line = _ema(trix, signal_window)

    # TRIX > Signal 做多，< Signal 做空
    signal = np.tanh((trix - signal_line) * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "TRIX因子：三重指数移动平均。TRIX上穿Signal做多，下穿做空。CTA趋势跟踪指标。",
        },
    },
    # ── 15. ROC — 价格变化率 ──
    {
        "name": "fut_roc",
        "description": "ROC因子：价格变化率。ROC上穿零轴做多，下穿零轴做空。CTA动量反转双用指标。",
        "input_fields": ["close"],
        "params": {"window": 12},
        "lookback": 18,
        "impl": """
    window = int(params.get('window', 12))

    # ROC = (Close - Close_n) / Close_n * 100
    roc = np.zeros(n)
    roc[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10) * 100

    # ROC 正做多，负做空
    signal = np.tanh(roc / 10)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "ROC因子：价格变化率。ROC上穿零轴做多，下穿零轴做空。CTA动量反转双用指标。",
        },
    },
    # ── 16. NATR — 归一化平均真实波动范围 ──
    {
        "name": "fut_natr",
        "description": "NATR因子：归一化ATR。ATR/Close，波动率占比信号。NATR高=波动率占比大=偏回落。CTA波动率管理。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # ATR 计算
    high_low = np.zeros(n)
    high_close = np.zeros(n)
    low_close = np.zeros(n)
    high_low[1:] = high[1:] - low[1:]
    high_close[1:] = np.abs(high[1:] - close[:-1])
    low_close[1:] = np.abs(low[1:] - close[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    atr = np.zeros(n)
    for i in range(min(window, n)):
        if i > 0:
            atr[i] = np.mean(tr[max(0, i-window+1):i+1])
    for i in range(window, n):
        atr[i] = (atr[i-1] * (window - 1) + tr[i]) / window

    # NATR = ATR / Close * 100
    natr = atr / np.maximum(close, 1e-10) * 100

    # NATR 高 = 波动占比大 = 偏回落
    signal = -np.tanh(natr / 5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "NATR因子：归一化ATR。ATR/Close，波动率占比信号。NATR高=波动率占比大=偏回落。CTA波动率管理。",
        },
    },
    # ── 17. Linear Regression — 线性回归斜率 ──
    {
        "name": "fut_linearreg",
        "description": "线性回归因子：线性回归斜率。斜率正做多，负做空。CTA趋势方向与强度判断。",
        "input_fields": ["close"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))
    x = np.arange(window, dtype=float)
    sx = np.sum(x)
    sxx = np.sum(x * x)
    denom = window * sxx - sx * sx

    slope = np.zeros(n)
    for i in range(window, n):
        y = close[max(0, i-window+1):i+1]
        sy = np.sum(y)
        sxy = np.sum(x * y)
        if abs(denom) > 1e-10:
            slope[i] = (window * sxy - sx * sy) / denom

    # 斜率归一化
    norm_slope = slope / np.maximum(close, 1e-10)
    signal = np.tanh(norm_slope * 100)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "线性回归因子：线性回归斜率。斜率正做多，负做空。CTA趋势方向与强度判断。",
        },
    },
    # ── 18. APO — 绝对价格振荡器 ──
    {
        "name": "fut_apo",
        "description": "APO因子：绝对价格振荡器。快EMA-慢EMA。APO正则做多，负则做空。CTA趋势动能指标。",
        "input_fields": ["close"],
        "params": {"fast_period": 12, "slow_period": 26},
        "lookback": 30,
        "impl": """
    fast_period = int(params.get('fast_period', 12))
    slow_period = int(params.get('slow_period', 26))

    def _ema(arr, w):
        a = 2.0 / (w + 1)
        result = np.zeros_like(arr)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = a * arr[i] + (1 - a) * result[i-1]
        return result

    fast_ema = _ema(close, fast_period)
    slow_ema = _ema(close, slow_period)

    # APO = Fast EMA - Slow EMA
    apo = fast_ema - slow_ema

    # 归一化
    norm_apo = apo / np.maximum(close, 1e-10)
    signal = np.tanh(norm_apo * 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "APO因子：绝对价格振荡器。快EMA-慢EMA。APO正则做多，负则做空。CTA趋势动能指标。",
        },
    },
    # ── 19. PPO — 百分比价格振荡器 ──
    {
        "name": "fut_ppo",
        "description": "PPO因子：百分比价格振荡器。(快EMA-慢EMA)/慢EMA。PPO正则做多，负则做空。CTA趋势动能指标。",
        "input_fields": ["close"],
        "params": {"fast_period": 12, "slow_period": 26},
        "lookback": 30,
        "impl": """
    fast_period = int(params.get('fast_period', 12))
    slow_period = int(params.get('slow_period', 26))

    def _ema(arr, w):
        a = 2.0 / (w + 1)
        result = np.zeros_like(arr)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = a * arr[i] + (1 - a) * result[i-1]
        return result

    fast_ema = _ema(close, fast_period)
    slow_ema = _ema(close, slow_period)

    # PPO = (Fast EMA - Slow EMA) / Slow EMA * 100
    ppo = np.zeros(n)
    denom = np.maximum(np.abs(slow_ema), 1e-10)
    ppo = (fast_ema - slow_ema) / denom * 100

    signal = np.tanh(ppo / 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "PPO因子：百分比价格振荡器。(快EMA-慢EMA)/慢EMA。PPO正则做多，负则做空。CTA趋势动能指标。",
        },
    },
    # ── 20. MACD Histogram — MACD 柱状线 ──
    {
        "name": "fut_macd_hist",
        "description": "MACD柱状线因子：MACD快线-信号线。柱状线正则做多，负则做空。CTA经典趋势跟踪指标。",
        "input_fields": ["close"],
        "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
        "lookback": 30,
        "impl": """
    fast_period = int(params.get('fast_period', 12))
    slow_period = int(params.get('slow_period', 26))
    signal_period = int(params.get('signal_period', 9))

    def _ema(arr, w):
        a = 2.0 / (w + 1)
        result = np.zeros_like(arr)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = a * arr[i] + (1 - a) * result[i-1]
        return result

    fast_ema = _ema(close, fast_period)
    slow_ema = _ema(close, slow_period)

    # MACD 快线
    macd_line = fast_ema - slow_ema

    # Signal 线
    signal_line = _ema(macd_line, signal_period)

    # 柱状线 = MACD - Signal
    histogram = macd_line - signal_line

    # 柱状线正则做多，负则做空
    norm_hist = histogram / np.maximum(close, 1e-10)
    signal = np.tanh(norm_hist * 100)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "MACD柱状线因子：MACD快线-信号线。柱状线正则做多，负则做空。CTA经典趋势跟踪指标。",
        },
    },
    # ── 21. WMA — 加权移动平均 ──
    {
        "name": "fut_wma_cross",
        "description": "WMA交叉因子：加权移动平均价格位置。价格在WMA上方做多，下方做空。CTA趋势跟踪。",
        "input_fields": ["close"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # WMA 计算
    weights = np.arange(1, window + 1, dtype=float)
    wma = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        wma[i] = np.sum(seg * weights) / np.sum(weights)

    # 价格在 WMA 上方做多，下方做空
    signal = np.where(close > wma, 1.0, -1.0)
    # 距 WMA 距离归一化
    dist = (close - wma) / np.maximum(wma, 1e-10)
    signal = np.tanh(dist * 20)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "WMA交叉因子：加权移动平均价格位置。价格在WMA上方做多，下方做空。CTA趋势跟踪。",
        },
    },
    # ── 22. Ultimate Oscillator — 终极振荡器 ──
    {
        "name": "fut_ultosc",
        "description": "Ultimate Oscillator因子：终极振荡器。多周期加权超买超卖判断。UO>70超买做空，UO<30超卖做多。CTA综合摆动指标。",
        "input_fields": ["close", "high", "low"],
        "params": {"period1": 7, "period2": 14, "period3": 28},
        "lookback": 35,
        "impl": """
    p1 = int(params.get('period1', 7))
    p2 = int(params.get('period2', 14))
    p3 = int(params.get('period3', 28))

    def _calc_uo(arr_h, arr_l, arr_c, period):
        n = len(arr_c)
        bp = np.zeros(n)  # 买入压力
        tr = np.zeros(n)  # 真实波动
        for i in range(1, n):
            bp[i] = arr_c[i] - min(arr_l[i], arr_c[i-1])
            tr[i] = max(arr_h[i], arr_c[i-1]) - min(arr_l[i], arr_c[i-1])
        avg_bp = np.zeros(n)
        avg_tr = np.zeros(n)
        for i in range(period, n):
            avg_bp[i] = np.sum(bp[max(0, i-period+1):i+1])
            avg_tr[i] = np.sum(tr[max(0, i-period+1):i+1])
        result = np.zeros(n)
        mask = avg_tr > 1e-10
        result[mask] = avg_bp[mask] / avg_tr[mask]
        return result

    uo1 = _calc_uo(high, low, close, p1)
    uo2 = _calc_uo(high, low, close, p2)
    uo3 = _calc_uo(high, low, close, p3)

    # UO = 4*UO1 + 2*UO2 + UO3
    uo = (4 * uo1 + 2 * uo2 + uo3) / 7 * 100

    # UO>70 超买做空，UO<30 超卖做多
    signal = np.where(uo > 70, -1.0, np.where(uo < 30, 1.0, 0.0))
    mid = (uo >= 30) & (uo <= 70)
    signal[mid] = (30 + 70 - 2 * uo[mid]) / 40.0
    signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "Ultimate Oscillator因子：终极振荡器。多周期加权超买超卖判断。UO>70超买做空，UO<30超卖做多。CTA综合摆动指标。",
        },
    },
    # ── 23. Bollinger Squeeze — 布林带收缩 ──
    {
        "name": "fut_boll_squeeze",
        "description": "布林带收缩因子：带宽/价格比率。带宽收缩=蓄力突破，带宽扩张=趋势延续。CTA波动率周期判断。",
        "input_fields": ["close"],
        "params": {"window": 20, "num_std": 2.0},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))
    num_std = float(params.get('num_std', 2.0))

    # 计算布林带带宽
    bandwidth = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        sma = np.mean(seg)
        std = np.std(seg, ddof=0)
        bandwidth[i] = 2 * num_std * std / max(sma, 1e-10)

    # 带宽的 Z-score
    bw_mean = np.mean(bandwidth[max(0, n-100):n]) if n > 100 else np.mean(bandwidth[max(0, n-window):n])
    bw_std = np.std(bandwidth[max(0, n-100):n]) if n > 100 else np.std(bandwidth[max(0, n-window):n])

    if bw_std > 1e-10:
        bw_z = (bandwidth - bw_mean) / bw_std
    else:
        bw_z = np.zeros(n)

    # 带宽收缩到极低 = 即将突破（做多/空方向由后续价格决定）
    # 这里用带宽的负值：收缩蓄力偏正，扩张偏负
    signal = -np.tanh(bw_z * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "布林带收缩因子：带宽/价格比率。带宽收缩=蓄力突破，带宽扩张=趋势延续。CTA波动率周期判断。",
        },
    },
]


# ─── 去重：检查与现有种子因子的重复 ──────────────────────────


def load_existing_futures_names() -> set[str]:
    """加载现有期货种子因子的所有名称。"""
    names: set[str] = set()
    seeds_dir = Path(__file__).resolve().parent.parent / "seeds" / "futures"

    for yaml_file in sorted(seeds_dir.glob("*.yaml")):
        if yaml_file.name == "vnpy_cta.yaml":
            continue  # 跳过自身
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "factors" in data:
                for factor in data["factors"]:
                    if "name" in factor:
                        names.add(factor["name"])
        except Exception as e:
            logger.warning("读取 %s 失败: %s", yaml_file, e)

    return names


def check_duplicate(name: str, existing: set[str]) -> bool:
    """检查因子名是否重复。"""
    return name in existing


# ─── 生成 YAML 文件 ──────────────────────────────────────────


def generate_yaml(output_path: Path | str = OUTPUT_FILE) -> int:
    """生成 vnpy_cta.yaml 种子文件。

    Returns:
        int: 写入的因子数量
    """
    existing_names = load_existing_futures_names()
    logger.info("现有期货因子数量: %d", len(existing_names))

    new_factors: list[dict] = []
    skipped: list[str] = []

    for factor_def in FACTOR_DEFINITIONS:
        name = factor_def["name"]

        if check_duplicate(name, existing_names):
            skipped.append(name)
            logger.info("跳过重复因子: %s", name)
            continue

        # 生成 factor_program 代码
        code = _make_code(
            impl=factor_def["impl"],
            input_fields=factor_def["input_fields"],
        )

        # 构建 YAML 因子条目
        entry = {
            "name": name,
            "description": factor_def["description"],
            "market": "futures",
            "code": code,
            "params": factor_def["params"],
            "input_fields": factor_def["input_fields"],
            "lookback": factor_def["lookback"],
            "output_type": "signal",
            "frequency": "daily",
            "economic_logic": factor_def["economic_logic"],
        }
        new_factors.append(entry)

    # 写入 YAML
    yaml_content = {
        "family": "vnpy_cta",
        "version": "1.0",
        "market": "futures",
        "factors": new_factors,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info("=" * 50)
    logger.info("生成完成: %s", output_path)
    logger.info("  新因子数: %d", len(new_factors))
    logger.info("  跳过重复: %d", len(skipped))
    if skipped:
        logger.info("  重复列表: %s", ", ".join(skipped))
    logger.info("=" * 50)

    return len(new_factors)


# ─── 主入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    count = generate_yaml()
    print(f"\n✅ 成功生成 {count} 个 vnpy CTA 因子到 {OUTPUT_FILE}")
