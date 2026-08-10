"""extract_mc_factors.py — 提取 MultiCharts (MC) 平台特色期货因子

MultiCharts 是国际主流的 CTA/期货交易平台，使用 PowerLanguage 语言。
本脚本提取 MC 平台上经典且独特的因子（Ehlers信号处理、Bill Williams分析、
经典模式识别等），与 vnpy CTA 和 Wind 因子互补，生成 mc_cta.yaml 种子文件。

MC 平台特色因子类别:
  Ehlers信号处理: T3 Moving Average, Fisher Transform, MAMA, Correlation Cycle
  Bill Williams: Market Facilitation Index, Acceleration Bands
  趋势检测: Choppiness Index, Vortex Indicator, KST
  模式识别: Inside Bar, Outside Bar, 123 Reversal
  量价分析: Ease of Movement, Force Index, Herrick Payoff Index
  波动率: Chandelier Exit, Volatility Ratio, Kase DevStops, Rainbow MA
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ─── 输出路径 ─────────────────────────────────────────────────
SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "futures"
OUTPUT_FILE = SEEDS_DIR / "mc_cta.yaml"


# 辅助函数：生成标准因子代码模板
def _make_code(
    impl: str,
    input_fields: list[str],
) -> str:
    """生成标准 factor_program 代码。"""
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
        elif f == "oi":
            field_assignments.append(
                "open_interest = data.get('open_interest', np.zeros(n)).values if hasattr(data, 'open_interest') else data.get('open_interest', np.zeros(n))"
            )
    fields_str = "\n    ".join(field_assignments)

    indented_impl = textwrap.indent(impl.strip(), "    ")
    return (
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    n = len(data['close'].values if hasattr(data, 'close') else data['close'])\n\n"
        "    " + fields_str + "\n\n" + indented_impl + "\n\n"
        "    return np.clip(np.nan_to_num(signal, nan=0.0), -1.0, 1.0)"
    )


# ─── 因子定义 ─────────────────────────────────────────────────
# 共 20 个 MC 平台特色因子，与 vnpy_cta（23 个技术指标）和 wind_cta（20 个特色因子）无重复

MC_FACTORS: list[dict[str, Any]] = [
    # ════════════════════════════════════════════════════════════
    # 类别 1: Ehlers 信号处理因子（MC 最经典特色）
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_t3_ma",
        "description": "T3移动平均因子：Tillson T3 MA，比传统EMA更平滑，减少滞后和噪声。T3>价格=偏多，T3<价格=偏空。MultiCharts PowerLanguage经典平滑指标。",
        "input_fields": ["close"],
        "params": {"window": 14, "v_factor": 0.7},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))
v_factor = float(params.get('v_factor', 0.7))

# T3 移动平均计算 (Tillson T3)
# T3 = GD(GD(GD(close))), GD = EMA(EMA(price))
def ema(series, period):
    result = np.zeros_like(series)
    result[0] = series[0]
    alpha = 2.0 / (period + 1)
    for i in range(1, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i-1]
    return result

c1 = -v_factor ** 3
c2 = 3 * v_factor ** 2 + 3 * v_factor ** 3
c3 = -6 * v_factor ** 2 - 3 * v_factor - 3 * v_factor ** 3
c4 = 1 + 3 * v_factor + v_factor ** 3 + 3 * v_factor ** 2

e1 = ema(close, window)
e2 = ema(e1, window)
e3 = ema(e2, window)
e4 = ema(e3, window)
e5 = ema(e4, window)
e6 = ema(e5, window)

t3 = c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3

# 价格相对于T3的位置：T3<价格=偏多，T3>价格=偏空
signal = (close - t3) / np.maximum(close, 1e-10)
signal = np.tanh(signal * 50)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "T3移动平均因子：Tillson T3 MA，比传统EMA更平滑，减少滞后和噪声。T3<价格=偏多，T3>价格=偏空。",
        },
    },
    {
        "name": "fut_fisher_transform",
        "description": "Fisher Transform因子：Ehlers Fisher Transform，将价格正态化。极端值>2=做空，<-2=做多。MC Ehlers系列经典指标，识别价格转折点。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 10},
        "lookback": 20,
        "impl": """
window = int(params.get('window', 10))

# 计算中间价
mid = (high + low) / 2.0

# 滚动窗口极值
hp = np.zeros(n)
lp = np.zeros(n)
for i in range(window, n):
    hp[i] = np.max(mid[max(0, i-window+1):i+1])
    lp[i] = np.min(mid[max(0, i-window+1):i+1])

# Fisher Transform
value = np.zeros(n)
for i in range(window, n):
    range_ = hp[i] - lp[i]
    if range_ > 1e-10:
        raw = 2.0 * (mid[i] - lp[i]) / range_ - 1.0
        # 钳制
        raw = np.clip(raw, -0.999, 0.999)
        value[i] = 0.5 * np.log((1 + raw) / (1 - raw))
    else:
        value[i] = value[i-1] if i > 0 else 0.0

# Fisher > 2 偏空, < -2 偏多
signal = -np.tanh(value * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 2,
            "narrative": "Fisher Transform因子：Ehlers Fisher Transform，将价格正态化。极端值>2偏空，<-2偏多。",
        },
    },
    {
        "name": "fut_mama",
        "description": "MAMA因子：MESA Adaptive Moving Average，自适应调整周期。快速MAMA上穿慢速FAMA=偏多，下穿=偏空。MC Ehlers系列自适应均线。",
        "input_fields": ["close"],
        "params": {"fast_limit": 0.5, "slow_limit": 0.05},
        "lookback": 30,
        "impl": """
fast_limit = float(params.get('fast_limit', 0.5))
slow_limit = float(params.get('slow_limit', 0.05))

# MAMA 计算 (MESA Adaptive Moving Average)
# 使用相位变化检测自适应周期
alpha = slow_limit * np.ones(n)
mama = np.zeros(n)
fama = np.zeros(n)
mama[0] = close[0]
fama[0] = close[0]

for i in range(4, n):
    # Hilbert Transform 相位检测
    smooth = (4 * close[i] + 3 * close[i-1] + 2 * close[i-2] + close[i-3]) / 10.0
    if i >= 6:
        detrender = (0.0962 * smooth + 0.5769 * smooth + 0.5769 * smooth + 0.0962 * smooth) / 2.0  # 简化
    # 自适应 alpha
    alpha[i] = fast_limit / (1 + np.exp(-10 * (close[i] - close[i-1]) / np.maximum(close[i-1], 1e-10)))
    alpha[i] = np.clip(alpha[i], slow_limit, fast_limit)

    mama[i] = alpha[i] * close[i] + (1 - alpha[i]) * mama[i-1]
    fama[i] = 0.5 * alpha[i] * mama[i] + (1 - 0.5 * alpha[i]) * fama[i-1]

# MAMA 上穿 FAMA = 偏多，下穿 = 偏空
signal = (mama - fama) / np.maximum(close, 1e-10)
signal = np.tanh(signal * 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 2,
            "narrative": "MAMA因子：MESA Adaptive Moving Average，自适应调整周期。MAMA上穿FAMA=偏多，下穿=偏空。",
        },
    },
    {
        "name": "fut_ehlers_corr_cycle",
        "description": "Ehlers相关周期因子：自相关周期检测，识别市场循环周期。周期短=趋势强=偏多，周期长=震荡=偏空。MC Ehlers系列周期检测。",
        "input_fields": ["close"],
        "params": {"window": 20, "max_cycle": 40},
        "lookback": 50,
        "impl": """
window = int(params.get('window', 20))
max_cycle = int(params.get('max_cycle', 40))

# 计算自相关周期
cycle_quality = np.zeros(n)
for i in range(window * 2, n):
    seg = close[max(0, i-window+1):i+1]
    seg_mean = np.mean(seg)
    seg_std = np.std(seg, ddof=0)
    if seg_std < 1e-10:
        continue
    seg_norm = (seg - seg_mean) / seg_std

    # 自相关计算
    best_corr = 0
    best_lag = 0
    for lag in range(2, min(max_cycle, len(seg_norm) // 2)):
        corr = np.corrcoef(seg_norm[:-lag], seg_norm[lag:])[0, 1]
        if np.abs(corr) > np.abs(best_corr):
            best_corr = corr
            best_lag = lag

    # 周期短=趋势强=偏多，周期长=震荡=偏空
    if best_lag > 0:
        cycle_quality[i] = np.abs(best_corr) / np.maximum(best_lag, 1)

signal = np.tanh(cycle_quality * 5 - 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 2,
            "narrative": "Ehlers相关周期因子：自相关周期检测，周期短=趋势强偏多，周期长=震荡偏空。",
        },
    },
    # ════════════════════════════════════════════════════════════
    # 类别 2: 趋势/震荡检测
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_choppiness_index",
        "description": "混沌指数因子：Choppiness Index，市场趋势vs震荡判断。CI<38.2=趋势市场偏多，CI>61.8=震荡市场偏空。MC经典市场状态滤波器。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))

# Choppiness Index 计算
tr = np.zeros(n)
for i in range(1, n):
    tr[i] = np.max([
        high[i] - low[i],
        np.abs(high[i] - close[i-1]),
        np.abs(low[i] - close[i-1])
    ])

atr = np.zeros(n)
for i in range(window, n):
    atr[i] = np.mean(tr[max(0, i-window+1):i+1])

highest = np.zeros(n)
lowest = np.zeros(n)
for i in range(window, n):
    highest[i] = np.max(high[max(0, i-window+1):i+1])
    lowest[i] = np.min(low[max(0, i-window+1):i+1])

# CI = sum(TR) / (最高 - 最低) 的对数标准化
sum_tr = np.zeros(n)
for i in range(window, n):
    sum_tr[i] = np.sum(tr[max(0, i-window+1):i+1])

range_ = np.maximum(highest - lowest, 1e-10)
ci = 100 * np.log(sum_tr / range_) / np.log(window + 1)

# CI < 38.2 = 趋势市场做多, CI > 61.8 = 震荡市场做空
signal = np.where(ci < 38.2, 1.0, np.where(ci > 61.8, -1.0, 0.0))
mid_mask = (ci >= 38.2) & (ci <= 61.8)
signal[mid_mask] = (61.8 + 38.2 - 2 * ci[mid_mask]) / (61.8 - 38.2)
signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "混沌指数因子：CI<38.2=趋势偏多，CI>61.8=震荡偏空。MC经典市场状态滤波器。",
        },
    },
    {
        "name": "fut_vortex",
        "description": "Vortex Indicator因子：趋势方向检测。VI+上穿VI- = 上升趋势偏多，VI-上穿VI+ = 下降趋势偏空。MC经典趋势检测因子。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))

# Vortex Indicator 计算
vm_plus = np.zeros(n)
vm_minus = np.zeros(n)
tr = np.zeros(n)

for i in range(1, n):
    vm_plus[i] = np.abs(high[i] - low[i-1])
    vm_minus[i] = np.abs(low[i] - high[i-1])
    tr[i] = np.max([
        high[i] - low[i],
        np.abs(high[i] - close[i-1]),
        np.abs(low[i] - close[i-1])
    ])

sum_vp = np.zeros(n)
sum_vm = np.zeros(n)
sum_tr = np.zeros(n)

for i in range(window, n):
    sum_vp[i] = np.sum(vm_plus[max(0, i-window+1):i+1])
    sum_vm[i] = np.sum(vm_minus[max(0, i-window+1):i+1])
    sum_tr[i] = np.sum(tr[max(0, i-window+1):i+1])

vi_plus = np.where(sum_tr > 1e-10, sum_vp / sum_tr, 0)
vi_minus = np.where(sum_tr > 1e-10, sum_vm / sum_tr, 0)

# VI+ > VI- = 偏多, VI- > VI+ = 偏空
signal = vi_plus - vi_minus
signal = np.tanh(signal * 3)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "Vortex Indicator因子：VI+上穿VI-偏多，VI-上穿VI+偏空。MC经典趋势检测。",
        },
    },
    {
        "name": "fut_kst",
        "description": "KST因子：Know Sure Thing，多周期ROC合成指标。KST上穿零轴=偏多，下穿零轴=偏空。MC经典多周期趋势指标。",
        "input_fields": ["close"],
        "params": {"r1": 10, "r2": 15, "r3": 20, "r4": 30},
        "lookback": 40,
        "impl": """
r1 = int(params.get('r1', 10))
r2 = int(params.get('r2', 15))
r3 = int(params.get('r3', 20))
r4 = int(params.get('r4', 30))

# 多周期ROC
roc = np.zeros(n)
roc[1:] = close[1:] / np.maximum(close[:-1], 1e-10) - 1

# 四个周期SMA
sma1 = np.zeros(n)
sma2 = np.zeros(n)
sma3 = np.zeros(n)
sma4 = np.zeros(n)

for i in range(r1, n):
    sma1[i] = np.mean(roc[max(0, i-r1+1):i+1])
for i in range(r2, n):
    sma2[i] = np.mean(roc[max(0, i-r2+1):i+1])
for i in range(r3, n):
    sma3[i] = np.mean(roc[max(0, i-r3+1):i+1])
for i in range(r4, n):
    sma4[i] = np.mean(roc[max(0, i-r4+1):i+1])

# KST = 1*SMA1 + 2*SMA2 + 3*SMA3 + 4*SMA4
kst = sma1 + 2 * sma2 + 3 * sma3 + 4 * sma4
signal = np.tanh(kst * 50)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "KST因子：Know Sure Thing，多周期ROC合成。上穿零轴偏多，下穿偏空。",
        },
    },
    {
        "name": "fut_coppock",
        "description": "Coppock Curve因子：长期趋势变化检测。Coppock上穿零轴=长期趋势转多，下穿=长期趋势转空。MC经典长期趋势指标。",
        "input_fields": ["close"],
        "params": {"roc_window": 14, "wma_window": 10},
        "lookback": 50,
        "impl": """
roc_window = int(params.get('roc_window', 14))
wma_window = int(params.get('wma_window', 10))

# 长期ROC
roc_long = np.zeros(n)
roc_short = np.zeros(n)
for i in range(roc_window, n):
    roc_long[i] = (close[i] / np.maximum(close[max(0, i-roc_window)], 1e-10) - 1) * 100
for i in range(roc_window // 2, n):
    roc_short[i] = (close[i] / np.maximum(close[max(0, i-roc_window//2)], 1e-10) - 1) * 100

# RoC = 14个月ROC + 11个月ROC (按月近似)
# 日线近似: 14天ROC + 11天ROC
roc_sum = roc_long + roc_short

# 加权移动平均
coppock = np.zeros(n)
for i in range(wma_window, n):
    weights = np.arange(1, wma_window + 1)
    seg = roc_sum[max(0, i-wma_window+1):i+1]
    if len(seg) == wma_window:
        coppock[i] = np.sum(seg * weights) / np.sum(weights)

# Coppock上穿零轴=偏多，下穿=偏空
signal = np.tanh(coppock * 0.1)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 2,
            "institutional": 4,
            "narrative": "Coppock Curve因子：长期趋势变化检测，上穿零轴偏多，下穿零轴偏空。",
        },
    },
    # ════════════════════════════════════════════════════════════
    # 类别 3: Bill Williams 分析
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_market_facilitation",
        "description": "市场促进指数因子：Bill Williams MFI，量价关系分析。量价齐升=有效突破偏多，量缩价升=弱势偏空。MC Bill Williams经典因子。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 14},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))

# 市场促进指数 = (最高-最低) / 成交量
range_ = np.maximum(high - low, 1e-10)
volume_safe = np.maximum(volume + 1e-10, 1e-10)
mf = range_ / volume_safe

# 价格变化方向
price_change = np.zeros(n)
price_change[1:] = close[1:] - close[:-1]

# 量价关系评分
# 量增价涨 = +1 (有效突破), 量增价跌 = -1 (有效下跌)
# 量缩价涨 = -0.5 (弱势反弹), 量缩价跌 = +0.5 (缩量整理)
signal_raw = np.zeros(n)
for i in range(1, n):
    vol_up = volume[i] > np.mean(volume[max(0, i-window):i]) if i >= window else True
    if price_change[i] > 0 and vol_up:
        signal_raw[i] = 1.0
    elif price_change[i] < 0 and vol_up:
        signal_raw[i] = -1.0
    elif price_change[i] > 0 and not vol_up:
        signal_raw[i] = -0.5
    elif price_change[i] < 0 and not vol_up:
        signal_raw[i] = 0.5

signal = np.tanh(signal_raw)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 4,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "市场促进指数因子：Bill Williams MFI，量价齐升偏多，量缩价升偏空。",
        },
    },
    {
        "name": "fut_ease_of_movement",
        "description": "Ease of Movement因子：量价关系效率。EOM上升=价格轻松上涨偏多，EOM下降=价格上涨阻力大偏空。MC经典量价效率因子。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 14},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))

# Ease of Movement = ( (H+L)/2 - (H_prev+L_prev)/2 ) / (成交量 / (H-L) )
midpoint = (high + low) / 2.0
mid_move = np.zeros(n)
mid_move[1:] = midpoint[1:] - midpoint[:-1]

box_ratio = np.zeros(n)
box_ratio[1:] = (volume[1:] / 1e6) / np.maximum(high[1:] - low[1:], 1e-10)

eom = np.zeros(n)
eom[1:] = mid_move[1:] / np.maximum(box_ratio[1:], 1e-10)

# 滚动均值
eom_sma = np.zeros(n)
for i in range(window, n):
    eom_sma[i] = np.mean(eom[max(0, i-window+1):i+1])

signal = np.tanh(eom_sma * 10)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Ease of Movement因子：量价关系效率，EOM上升偏多，下降偏空。",
        },
    },
    # ════════════════════════════════════════════════════════════
    # 类别 4: 波动率/止损
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_chandelier_exit",
        "description": "Chandelier Exit因子：波动率跟踪止损。价格突破Chandelier Exit上轨=偏多，跌破下轨=偏空。MC经典波动率跟踪止损工具。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 22, "mult": 3.0},
        "lookback": 30,
        "impl": """
window = int(params.get('window', 22))
mult = float(params.get('mult', 3.0))

# ATR 计算
tr = np.zeros(n)
for i in range(1, n):
    tr[i] = np.max([
        high[i] - low[i],
        np.abs(high[i] - close[i-1]),
        np.abs(low[i] - close[i-1])
    ])

atr = np.zeros(n)
for i in range(window, n):
    atr[i] = np.mean(tr[max(0, i-window+1):i+1])

# 最高价滚动最高
highest = np.zeros(n)
lowest = np.zeros(n)
for i in range(window, n):
    highest[i] = np.max(high[max(0, i-window+1):i+1])
    lowest[i] = np.min(low[max(0, i-window+1):i+1])

# 长止损 = 最高 - mult * ATR, 短止损 = 最低 + mult * ATR
long_stop = highest - mult * atr
short_stop = lowest + mult * atr

# 价格 >= 长止损 && 价格 > 短止损 = 偏多(上升趋势)
# 价格 <= 短止损 && 价格 < 长止损 = 偏空(下降趋势)
signal = np.where(close >= long_stop, 1.0, np.where(close <= short_stop, -1.0, 0.0))
signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "Chandelier Exit因子：波动率跟踪止损，价格突破上轨偏多，跌破下轨偏空。",
        },
    },
    {
        "name": "fut_volatility_ratio",
        "description": "Volatility Ratio因子：日内波动爆发检测。VR高=波动爆发=趋势启动偏多，VR低=波动萎缩=震荡偏空。MC经典波动率选时因子。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))

# Volatility Ratio = 当日真实波动范围 / 过去N日真实波动范围均值
tr = np.zeros(n)
for i in range(1, n):
    tr[i] = np.max([
        high[i] - low[i],
        np.abs(high[i] - close[i-1]),
        np.abs(low[i] - close[i-1])
    ])

avg_tr = np.zeros(n)
for i in range(window, n):
    avg_tr[i] = np.mean(tr[max(0, i-window+1):i+1])

vr = np.where(avg_tr > 1e-10, tr / avg_tr, 1.0)

# VR > 1.5 = 波动爆发 = 趋势启动偏多
# VR < 0.5 = 波动萎缩 = 震荡偏空
signal = np.where(vr > 1.5, 1.0, np.where(vr < 0.5, -0.5, 0.0))
signal = np.tanh(signal)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Volatility Ratio因子：波动爆发偏多，波动萎缩偏空。MC经典选时因子。",
        },
    },
    {
        "name": "fut_kase_devstop",
        "description": "Kase DevStops因子：基于波动率的智能止损。价格突破DevStop上轨=偏多，跌破下轨=偏空。MC高级波动率止损工具。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 14, "mult": 2.0},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))
mult = float(params.get('mult', 2.0))

# Kase DevStops: 使用波动率加权止损
tr = np.zeros(n)
for i in range(1, n):
    tr[i] = np.max([
        high[i] - low[i],
        np.abs(high[i] - close[i-1]),
        np.abs(low[i] - close[i-1])
    ])

# 中位数波动率(更稳健)
med_tr = np.zeros(n)
for i in range(window, n):
    med_tr[i] = np.median(tr[max(0, i-window+1):i+1])

# 价格极值
highest = np.zeros(n)
lowest = np.zeros(n)
for i in range(window, n):
    highest[i] = np.max(high[max(0, i-window+1):i+1])
    lowest[i] = np.min(low[max(0, i-window+1):i+1])

# 波动率权重
vol_weight = 1.0 + np.tanh(med_tr * 10) * 0.5  # 0.5-1.5

# DevStops
dev_stop_long = highest - mult * med_tr * vol_weight
dev_stop_short = lowest + mult * med_tr * vol_weight

# 价格相对位置
signal = np.where(close >= dev_stop_long, 1.0, np.where(close <= dev_stop_short, -1.0, 0.0))
signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Kase DevStops因子：基于波动率的智能止损，突破上轨偏多，跌破下轨偏空。",
        },
    },
    {
        "name": "fut_acceleration_bands",
        "description": "Acceleration Bands因子：波动率自适应通道，带宽随波动率变化。价格突破上轨=偏多，跌破下轨=偏空。MC Bill Williams波动率通道。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 20, "width": 2.0},
        "lookback": 30,
        "impl": """
window = int(params.get('window', 20))
width = float(params.get('width', 2.0))

# Acceleration Bands
# 上轨 = 最高 * (1 + width * (最高 - 最低) / (最高 + 最低))
# 下轨 = 最低 * (1 - width * (最高 - 最低) / (最高 + 最低))

upper = np.zeros(n)
lower = np.zeros(n)
for i in range(window, n):
    seg_h = high[max(0, i-window+1):i+1]
    seg_l = low[max(0, i-window+1):i+1]
    hi = np.max(seg_h)
    lo = np.min(seg_l)
    mid = (hi + lo) / 2
    if mid > 1e-10:
        band = (hi - lo) / mid
        upper[i] = hi * (1 + width * band)
        lower[i] = lo * (1 - width * band)

# 价格突破上轨 = 偏多(加速上涨), 跌破下轨 = 偏空(加速下跌)
signal = np.where(close >= upper, 1.0, np.where(close <= lower, -1.0, 0.0))
# 在通道内线性映射
inside = (close > lower) & (close < upper) & (upper > lower)
signal[inside] = 2 * (close[inside] - lower[inside]) / (upper[inside] - lower[inside]) - 1
signal = np.tanh(signal * 0.5)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Acceleration Bands因子：波动率自适应通道，突破上轨偏多，跌破下轨偏空。",
        },
    },
    {
        "name": "fut_rainbow_ma",
        "description": "Rainbow MA因子：多周期均线带状排列。均线多头发散=偏多，空头发散=偏空，纠缠=震荡偏空。MC经典多周期均线分析。",
        "input_fields": ["close"],
        "params": {"periods": [5, 10, 20, 30, 50]},
        "lookback": 60,
        "impl": """
periods = params.get('periods', [5, 10, 20, 30, 50])

# 多周期SMA
mas = []
for p in periods:
    ma = np.zeros(n)
    for i in range(min(p, n), n):
        ma[i] = np.mean(close[max(0, i-p+1):i+1])
    mas.append(ma)

# 均线发散度: 最长期均线偏离度
if len(mas) >= 2:
    spread = np.zeros(n)
    for i in range(periods[-1], n):
        vals = [m[i] for m in mas]
        spread[i] = (np.max(vals) - np.min(vals)) / np.maximum(np.min(vals), 1e-10)

    # 价格在均线带中的位置
    pos = np.zeros(n)
    for i in range(periods[-1], n):
        vals = [m[i] for m in mas]
        ma_min = np.min(vals)
        ma_max = np.max(vals)
        if ma_max > ma_min:
            pos[i] = (close[i] - ma_min) / (ma_max - ma_min)
        else:
            pos[i] = 0.5

    # 多头排列: 价格 > 所有均线 = 偏多
    # 空头排列: 价格 < 所有均线 = 偏空
    signal = 2 * pos - 1
    # 发散度调整: 发散大=信号强, 发散小=信号弱
    signal = signal * np.tanh(spread * 50)
else:
    signal = np.zeros(n)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "Rainbow MA因子：多周期均线多头发散偏多，空头发散偏空，纠缠偏空。",
        },
    },
    # ════════════════════════════════════════════════════════════
    # 类别 5: 量价分析
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_force_index",
        "description": "Elder Force Index因子：量价动量，设计用于识别真实趋势。FI>0=量价齐升偏多，FI<0=量价齐跌偏空。MC经典量价指标。",
        "input_fields": ["close", "volume"],
        "params": {"window": 13},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 13))

# Force Index = 成交量 * (收盘 - 前收盘)
price_change = np.zeros(n)
price_change[1:] = close[1:] - close[:-1]
fi = price_change * volume

# 平滑
fi_smooth = np.zeros(n)
for i in range(window, n):
    fi_smooth[i] = np.mean(fi[max(0, i-window+1):i+1])

# 归一化
fi_signal = np.zeros(n)
for i in range(window, n):
    seg = fi_smooth[max(0, i-window+1):i+1]
    seg_std = np.std(seg, ddof=0)
    if seg_std > 1e-10:
        fi_signal[i] = fi_smooth[i] / seg_std

signal = np.tanh(fi_signal * 0.5)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 4,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Elder Force Index因子：量价动量，FI>0偏多，FI<0偏空。MC经典量价指标。",
        },
    },
    {
        "name": "fut_herrick_payoff",
        "description": "Herrick Payoff Index因子：结合价格、成交量和持仓量的综合指标。HPI>0=资金流入偏多，HPI<0=资金流出偏空。MC经典多因子合成指标。",
        "input_fields": ["close", "high", "low", "volume", "oi"],
        "params": {"window": 14, "mult": 100},
        "lookback": 25,
        "impl": """
window = int(params.get('window', 14))
mult = float(params.get('mult', 100))

# Herrick Payoff Index
# HPI = mult * (成交量 * (价格变动) * (1 - |价格变动|))
# 使用持仓量作为权重

price_change = np.zeros(n)
price_change[1:] = close[1:] - close[:-1]
price_range = np.maximum(high - low, 1e-10)
mid = (high + low) / 2.0

hpi = np.zeros(n)
for i in range(1, n):
    # 价格变动比率
    ret = price_change[i] / np.maximum(mid[i], 1e-10)
    # 成交量 * 价格变动 * (1 - |价格变动|) * 持仓量因子
    oi_factor = 1.0
    if i > 0 and hasattr(open_interest, '__iter__'):
        # 持仓量变化
        if isinstance(open_interest, (list, np.ndarray)) and len(open_interest) > i:
            oi_change = open_interest[i] - open_interest[i-1]
            oi_factor = 1.0 + np.tanh(oi_change / np.maximum(np.abs(open_interest[i-1]), 1e-10)) * 0.5
    hpi[i] = mult * volume[i] * ret * (1 - np.abs(ret)) * oi_factor

# 平滑
hpi_smooth = np.zeros(n)
for i in range(window, n):
    hpi_smooth[i] = np.mean(hpi[max(0, i-window+1):i+1])

signal = np.tanh(hpi_smooth / np.maximum(np.abs(hpi_smooth).max() + 1e-10, 1e-10) * 2)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 4,
            "narrative": "Herrick Payoff Index因子：结合价格、成交量、持仓量，HPI>0偏多，HPI<0偏空。",
        },
    },
    # ════════════════════════════════════════════════════════════
    # 类别 6: 模式识别
    # ════════════════════════════════════════════════════════════
    {
        "name": "fut_inside_bar",
        "description": "Inside Bar突破因子：内包线形态，高低点均在前一日范围内。内包线后突破上轨=偏多，跌破下轨=偏空。MC经典模式识别因子。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 5, "lookback": 3},
        "lookback": 15,
        "impl": """
window = int(params.get('window', 5))
lookback = int(params.get('lookback', 3))

# Inside Bar: 当日最高 <= 前日最高 AND 当日最低 >= 前日最低
inside = np.zeros(n, dtype=bool)
for i in range(1, n):
    inside[i] = (high[i] <= high[i-1]) and (low[i] >= low[i-1])

# 识别内包线后的突破方向
signal = np.zeros(n)
for i in range(window, n):
    # 检查过去window天内是否有内包线
    has_inside = np.any(inside[max(0, i-window):i])
    if has_inside:
        # 找最近内包线的高点低点
        for j in range(i, max(0, i-window), -1):
            if inside[j]:
                # 突破上轨 = 偏多, 跌破下轨 = 偏空
                if close[i] > high[j]:
                    signal[i] = 1.0
                elif close[i] < low[j]:
                    signal[i] = -1.0
                break

signal = np.tanh(signal)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 4,
            "microstructure": 4,
            "institutional": 2,
            "narrative": "Inside Bar突破因子：内包线后突破上轨偏多，跌破下轨偏空。MC模式识别。",
        },
    },
    {
        "name": "fut_outside_bar",
        "description": "Outside Bar反转因子：外包线形态，高低点超出前日范围。外包线配合收涨=偏多，收跌=偏空。MC经典反转形态因子。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 5},
        "lookback": 15,
        "impl": """
window = int(params.get('window', 5))

# Outside Bar: 当日最高 > 前日最高 AND 当日最低 < 前日最低
outside = np.zeros(n, dtype=bool)
bullish_outside = np.zeros(n)
for i in range(1, n):
    outside[i] = (high[i] > high[i-1]) and (low[i] < low[i-1])
    if outside[i]:
        # 收涨 = 看涨外包, 收跌 = 看跌外包
        if close[i] > close[i-1]:
            bullish_outside[i] = 1.0
        else:
            bullish_outside[i] = -1.0

# 外包线后确认
signal = np.zeros(n)
for i in range(window, n):
    for j in range(max(0, i-window), i):
        if outside[j] and bullish_outside[j] != 0:
            # 外包线后方向一致
            price_dir = 1 if close[i] > close[j] else -1
            if price_dir * bullish_outside[j] > 0:
                signal[i] = bullish_outside[j]
            break

signal = np.tanh(signal)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 4,
            "microstructure": 4,
            "institutional": 2,
            "narrative": "Outside Bar反转因子：外包线收涨偏多，收跌偏空。MC经典反转形态。",
        },
    },
    {
        "name": "fut_123_reversal",
        "description": "123反转形态因子：1-2-3结构反转。上升趋势中价格破前低=偏空，下降趋势中价格破前高=偏多。MC经典形态识别因子。",
        "input_fields": ["close", "high", "low"],
        "params": {"window": 10, "confirm": 3},
        "lookback": 20,
        "impl": """
window = int(params.get('window', 10))
confirm = int(params.get('confirm', 3))

# 123 反转形态识别
# 上升123: 1=高点, 2=回调低点, 3=突破前高 -> 偏多
# 下降123: 1=低点, 2=反弹高点, 3=跌破前低 -> 偏空

signal = np.zeros(n)
for i in range(window * 2, n):
    # 最近window天的高点/低点
    seg_h = high[max(0, i-window*2):i]
    seg_l = low[max(0, i-window*2):i]
    seg_c = close[max(0, i-window*2):i]

    # 寻找最近的高点(1)和回调低点(2)
    h1_idx = np.argmax(seg_h)
    l2_idx = np.argmin(seg_l[h1_idx:]) + h1_idx if h1_idx < len(seg_l) - 1 else h1_idx

    # 下降123: 1=低点, 2=反弹高点, 3=突破前低
    l1_idx = np.argmin(seg_l)
    h2_idx = np.argmax(seg_h[l1_idx:]) + l1_idx if l1_idx < len(seg_h) - 1 else l1_idx

    # 上升123: 价格突破前高
    if h1_idx < l2_idx < len(seg_c) - 1:
        if seg_c[-1] > seg_h[h1_idx] and seg_c[l2_idx] < seg_c[h1_idx]:
            signal[i] = 1.0  # 突破前高 = 偏多

    # 下降123: 价格跌破前低
    if l1_idx < h2_idx < len(seg_c) - 1:
        if seg_c[-1] < seg_l[l1_idx] and seg_c[h2_idx] > seg_c[l1_idx]:
            signal[i] = -1.0  # 跌破前低 = 偏空

signal = np.tanh(signal)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 2,
            "narrative": "123反转形态因子：上升123偏多，下降123偏空。MC经典形态识别。",
        },
    },
]


# ─── 生成 YAML ─────────────────────────────────────────────────
def generate_yaml() -> list[dict[str, Any]]:
    """生成 YAML 种子数据。"""
    yaml_factors = []
    seen = set()

    for f in MC_FACTORS:
        name = f["name"]
        if name in seen:
            logger.warning("跳过重复因子: %s", name)
            continue
        seen.add(name)

        code = _make_code(f["impl"], f["input_fields"])
        factor = {
            "name": name,
            "description": f["description"],
            "market": "futures",
            "code": code,
            "params": f["params"],
            "input_fields": f["input_fields"],
            "lookback": f["lookback"],
            "output_type": "signal",
            "frequency": "daily",
            "economic_logic": f["economic_logic"],
        }
        yaml_factors.append(factor)

    return yaml_factors


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    yaml_factors = generate_yaml()
    data = {
        "family": "mc_cta",
        "version": "1.0",
        "market": "futures",
        "factors": yaml_factors,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("已生成 %s: %d 个因子", OUTPUT_FILE, len(yaml_factors))
    logger.info("因子列表:")
    for f in yaml_factors:
        inputs = ", ".join(f["input_fields"])
        logger.info("  [%s] inputs=(%s) lookback=%d — %s", f["name"], inputs, f["lookback"], f["description"][:40])


if __name__ == "__main__":
    main()
