"""extract_wind_factors.py — 提取 Wind 平台特色期货因子

Wind（万得）金融终端提供丰富的金融数据和分析功能。
本脚本提取 Wind 平台特有的因子（高级波动率、微观结构、高阶统计等），
与 vnpy CTA 标准 TA-Lib 指标互补，生成 wind_cta.yaml 种子文件。

Wind 平台特色因子类别:
  高级波动率: Garman-Klass, Yang-Zhang, Rogers-Satchell, 跳跃风险
  微观结构: 买卖压力, 流动性缺口, 成交量质量
  高阶统计: 偏度, 峰度, 序列相关, 尾部风险
  趋势质量: 效率比, Hurst指数, 分形维度
  量价分析: 累积量, 价格加速度, 量价相关性
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ─── 输出路径 ─────────────────────────────────────────────────
SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "futures"
OUTPUT_FILE = SEEDS_DIR / "wind_cta.yaml"


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

    fields_str = "\n    ".join(field_assignments)
    fields_str = f"\n    {fields_str}" if fields_str else ""

    code = (
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    n = len(data['close'].values if hasattr(data, 'close') else data['close'])\n"
    )
    if fields_str:
        code += fields_str + "\n"
    code += impl + "\n"
    code += "    return np.clip(np.nan_to_num(signal, nan=0.0), -1.0, 1.0)"
    return code


# ─── 因子定义 ──────────────────────────────────────────────────

FACTOR_DEFINITIONS: list[dict[str, Any]] = [
    # ── 1. Garman-Klass 波动率 ──
    {
        "name": "fut_garman_klass",
        "description": "Garman-Klass波动率因子：基于OHLC的日内波动率估计，比传统Close-to-Close波动率更高效。高波动偏空，低波动偏多。Wind平台经典波动率估计器。",
        "input_fields": ["open", "high", "low", "close"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # Garman-Klass 波动率: σ² = 0.5*(ln(H/L))² - (2*ln2-1)*(ln(C/O))²
    log_hl = np.log(high / np.maximum(low, 1e-10))
    log_co = np.log(close / np.maximum(open_, 1e-10))
    gk_var = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    gk_var = np.maximum(gk_var, 0)  # 防止负方差

    # 滚动均值
    gk_vol = np.zeros(n)
    for i in range(window, n):
        gk_vol[i] = np.sqrt(np.mean(gk_var[max(0, i-window+1):i+1]))

    # 归一化：高波动偏空，低波动偏多
    gk_norm = gk_vol / np.maximum(close, 1e-10)
    signal = -np.tanh(gk_norm * 100)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Garman-Klass波动率因子：基于OHLC的日内波动率估计，比传统Close-to-Close波动率更高效。高波动偏空，低波动偏多。",
        },
    },
    # ── 2. Yang-Zhang 波动率 ──
    {
        "name": "fut_yang_zhang",
        "description": "Yang-Zhang波动率因子：考虑跳空的最优波动率估计器，结合隔夜与日内波动。Wind平台最精确波动率估计。高波动偏空，低波动偏多。",
        "input_fields": ["open", "high", "low", "close"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # 隔夜波动
    overnight = np.zeros(n)
    overnight[1:] = np.log(open_[1:] / np.maximum(close[:-1], 1e-10))

    # 日内波动
    log_hl = np.log(high / np.maximum(low, 1e-10))
    log_co = np.log(close / np.maximum(open_, 1e-10))

    # 开收盘波动
    open_close = np.zeros(n)
    open_close[1:] = np.log(close[1:] / np.maximum(open_[1:], 1e-10))

    yz_var = np.zeros(n)
    for i in range(window, n):
        seg_o = overnight[max(0, i-window+1):i+1]
        seg_hl = log_hl[max(0, i-window+1):i+1]
        seg_co = log_co[max(0, i-window+1):i+1]
        seg_oc = open_close[max(0, i-window+1):i+1]

        # σ² = σ_overnight² + k*σ_intraday² + (1-k)*σ_oc²
        var_overnight = np.var(seg_o, ddof=0)
        var_hl = np.var(seg_hl, ddof=0)
        var_co = np.var(seg_co, ddof=0)
        var_oc = np.var(seg_oc, ddof=0)

        k = 0.34 / (1.34 + (window - 1) / (window + 1))
        yz_var[i] = var_overnight + k * var_hl + (1 - k) * var_oc

    yz_vol = np.sqrt(np.maximum(yz_var, 0))

    # 归一化：高波动偏空
    yz_norm = yz_vol / np.maximum(close, 1e-10)
    signal = -np.tanh(yz_norm * 100)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 5,
            "institutional": 3,
            "narrative": "Yang-Zhang波动率因子：考虑跳空的最优波动率估计器，结合隔夜与日内波动。",
        },
    },
    # ── 3. 波动率偏度因子 ──
    {
        "name": "fut_vol_skew",
        "description": "波动率偏度因子：已实现波动率的偏度分布。正偏=波动率右尾长=下跌风险大偏空，负偏=波动率左尾长=反弹潜力偏多。Wind平台高阶波动率因子。",
        "input_fields": ["close"],
        "params": {"window": 20, "vol_window": 10},
        "lookback": 30,
        "impl": """
    window = int(params.get('window', 20))
    vol_window = int(params.get('vol_window', 10))

    # 计算日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 已实现波动率（滚动标准差）
    rv = np.zeros(n)
    for i in range(vol_window, n):
        rv[i] = np.std(ret[max(0, i-vol_window+1):i+1], ddof=0)

    # 滚动偏度
    skew = np.zeros(n)
    for i in range(window, n):
        seg = ret[max(0, i-window+1):i+1]
        if len(seg) > 2 and np.std(seg, ddof=0) > 1e-10:
            skew[i] = np.mean((seg - np.mean(seg)) ** 3) / (np.std(seg, ddof=0) ** 3 + 1e-10)

    # 正偏=下跌风险大偏空，负偏=反弹潜力偏多
    signal = -np.tanh(skew * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "波动率偏度因子：已实现波动率的偏度分布。正偏=波动率右尾长=下跌风险大偏空，负偏相反。",
        },
    },
    # ── 4. 尾部风险因子 ──
    {
        "name": "fut_tail_risk",
        "description": "尾部风险因子：基于极值收益率分布的尾部风险度量。尾部越厚=极端风险越大=偏空。Wind平台风险管理核心因子。",
        "input_fields": ["close"],
        "params": {"window": 30, "quantile": 0.05},
        "lookback": 35,
        "impl": """
    window = int(params.get('window', 30))
    q = float(params.get('quantile', 0.05))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 尾部风险: 下尾与上尾的比值
    tail_ratio = np.zeros(n)
    for i in range(window, n):
        seg = ret[max(0, i-window+1):i+1]
        if len(seg) > 0:
            lower = np.percentile(seg, q * 100)
            upper = np.percentile(seg, (1 - q) * 100)
            # 下尾均值 / 上尾均值（绝对值）
            lower_tail = seg[seg <= lower]
            upper_tail = seg[seg >= upper]
            if len(upper_tail) > 0 and np.abs(np.mean(upper_tail)) > 1e-10:
                tail_ratio[i] = np.abs(np.mean(lower_tail)) / np.abs(np.mean(upper_tail))
            else:
                tail_ratio[i] = 1.0

    # 尾部比 > 1 = 下尾风险更大偏空，< 1 = 上尾机会更大偏多
    signal = -np.tanh((tail_ratio - 1) * 3)
""",
        "economic_logic": {
            "theory": 5,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 4,
            "narrative": "尾部风险因子：基于极值收益率分布的尾部风险度量。尾部越厚=极端风险越大=偏空。",
        },
    },
    # ── 5. 序列相关因子 ──
    {
        "name": "fut_serial_corr",
        "description": "序列相关因子：收益率自相关结构。正自相关=趋势延续做动量，负自相关=均值回归做反转。Wind平台市场效率判断因子。",
        "input_fields": ["close"],
        "params": {"window": 20, "lag": 1},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))
    lag = int(params.get('lag', 1))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # Lag自相关
    autocorr = np.zeros(n)
    for i in range(window + lag, n):
        seg1 = ret[max(0, i-window+1):i+1-lag]
        seg2 = ret[max(0, i-window+lag):i+1]
        if len(seg1) > 1 and np.std(seg1, ddof=0) > 1e-10 and np.std(seg2, ddof=0) > 1e-10:
            autocorr[i] = np.corrcoef(seg1, seg2)[0, 1]
        else:
            autocorr[i] = 0.0

    # 正自相关=趋势延续做动量，负自相关=均值回归做反转
    signal = np.tanh(autocorr * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "序列相关因子：收益率自相关结构。正自相关=趋势延续做动量，负自相关=均值回归做反转。",
        },
    },
    # ── 6. 效率比因子（Kaufman） ──
    {
        "name": "fut_efficiency_ratio",
        "description": "效率比因子：Kaufman效率比=方向变动/总变动。效率比高=强趋势做动量，效率比低=震荡做反转。Wind平台趋势质量判断核心。",
        "input_fields": ["close"],
        "params": {"window": 10},
        "lookback": 15,
        "impl": """
    window = int(params.get('window', 10))

    # 方向变动 = |Close - Close_n|
    # 总变动 = sum(|Close_i - Close_{i-1}|)
    direction = np.zeros(n)
    noise = np.zeros(n)
    for i in range(window, n):
        direction[i] = np.abs(close[i] - close[i-window])
        total_noise = np.sum(np.abs(np.diff(close[max(0, i-window):i+1])))
        noise[i] = total_noise

    # 效率比 = 方向 / 噪音
    er = np.zeros(n)
    mask = noise > 1e-10
    er[mask] = direction[mask] / noise[mask]
    er = np.clip(er, 0, 1)

    # 效率比高=趋势强做动量，效率比低=震荡做反转
    # 映射到[-1, 1]：高效率偏正，低效率偏负
    signal = (er - 0.5) * 2
    signal = np.tanh(signal * 1.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "效率比因子：Kaufman效率比=方向变动/总变动。效率比高=强趋势做动量，低=震荡做反转。",
        },
    },
    # ── 7. Hurst 指数因子 ──
    {
        "name": "fut_hurst",
        "description": "Hurst指数因子：基于重标极差(R/S)分析的Hurst指数。H>0.5=趋势持续做动量，H<0.5=均值回归做反转。Wind平台市场分形结构判断。",
        "input_fields": ["close"],
        "params": {"window": 30, "min_chunk": 5},
        "lookback": 35,
        "impl": """
    window = int(params.get('window', 30))
    min_chunk = int(params.get('min_chunk', 5))

    def _hurst_rs(series):
        n = len(series)
        if n < min_chunk * 2:
            return 0.5
        # R/S 分析
        mean = np.mean(series)
        dev = series - mean
        cumsum = np.cumsum(dev)
        r = np.max(cumsum) - np.min(cumsum)
        s = np.std(series, ddof=0)
        if s < 1e-10:
            return 0.5
        rs = r / s
        return np.log(rs) / np.log(n) if n > 1 else 0.5

    hurst = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        h = _hurst_rs(seg)
        hurst[i] = np.clip(h, 0, 1)

    # H > 0.5 趋势偏多，H < 0.5 反转偏空
    signal = (hurst - 0.5) * 2
    signal = np.tanh(signal * 2)
""",
        "economic_logic": {
            "theory": 5,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "Hurst指数因子：基于重标极差(R/S)分析的Hurst指数。H>0.5=趋势持续做动量，H<0.5=均值回归做反转。",
        },
    },
    # ── 8. 买卖压力因子 ──
    {
        "name": "fut_market_micro",
        "description": "市场微观结构因子：基于HLC的买卖压力指标。Close在H-L区间位置+成交量加权。买入压力大做多，卖出压力大做空。Wind平台微观结构分析因子。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 14},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 14))

    # 买卖压力：价格在H-L区间的位置
    hl_range = np.maximum(high - low, 1e-10)
    pressure = (close - low) / hl_range  # [0, 1]: 0=卖压大, 1=买压大

    # 成交量加权买卖压力
    vwap_pressure = pressure * volume

    # 滚动平均
    avg_pressure = np.zeros(n)
    for i in range(window, n):
        avg_pressure[i] = np.mean(vwap_pressure[max(0, i-window+1):i+1]) / np.maximum(np.mean(volume[max(0, i-window+1):i+1]), 1e-10)

    # 映射到 [-1, 1]
    signal = (avg_pressure - 0.5) * 2
    signal = np.tanh(signal * 2)
""",
        "economic_logic": {
            "theory": 3,
            "behavioral": 3,
            "microstructure": 5,
            "institutional": 3,
            "narrative": "市场微观结构因子：基于HLC的买卖压力指标。买入压力大做多，卖出压力大做空。",
        },
    },
    # ── 9. 流动性缺口因子 ──
    {
        "name": "fut_liquidity_gap",
        "description": "流动性缺口因子：价格冲击的预期成本度量。流动性缺口大=冲击成本高=偏空。Wind平台流动性风险度量。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # Amihud 非流动性指标: |r| / (P * V)
    ret = np.zeros(n)
    ret[1:] = np.abs(close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    amihud = np.zeros(n)
    for i in range(1, n):
        dollar_vol = close[i] * volume[i]
        if dollar_vol > 1e-10:
            amihud[i] = ret[i] / dollar_vol * 1e6  # 缩放

    # 滚动均值
    avg_illiquidity = np.zeros(n)
    for i in range(window, n):
        avg_illiquidity[i] = np.mean(amihud[max(0, i-window+1):i+1])

    # 流动性越差（Amihud越高）偏空
    # 对数缩放
    log_illiq = np.log(np.maximum(avg_illiquidity, 1e-10))
    signal = -np.tanh(log_illiq * 0.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 5,
            "institutional": 3,
            "narrative": "流动性缺口因子：价格冲击的预期成本度量。流动性缺口大=冲击成本高=偏空。",
        },
    },
    # ── 10. 成交量质量因子 ──
    {
        "name": "fut_volume_quality",
        "description": "成交量质量因子：成交量对价格变动的贡献度。量价配合好=趋势可靠做动量，量价背离=趋势可疑做反转。Wind平台量价分析核心。",
        "input_fields": ["close", "volume"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # 价格变动
    price_chg = np.zeros(n)
    price_chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 成交量变化率
    vol_chg = np.zeros(n)
    vol_chg[1:] = (volume[1:] - volume[:-1]) / np.maximum(volume[:-1], 1e-10)

    # 量价相关性（滚动）
    vp_corr = np.zeros(n)
    for i in range(window + 1, n):
        p_seg = price_chg[max(0, i-window+1):i+1]
        v_seg = vol_chg[max(0, i-window+1):i+1]
        if len(p_seg) > 1 and np.std(p_seg, ddof=0) > 1e-10 and np.std(v_seg, ddof=0) > 1e-10:
            vp_corr[i] = np.corrcoef(p_seg, v_seg)[0, 1]
        else:
            vp_corr[i] = 0.0

    # 涨时放量(+corr) = 趋势确认做动量
    # 涨时缩量(-corr) = 趋势背离做反转
    signal = vp_corr * np.sign(price_chg)
    signal = np.tanh(signal * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "成交量质量因子：成交量对价格变动的贡献度。量价配合好=趋势可靠做动量，量价背离=趋势可疑做反转。",
        },
    },
    # ── 11. 价格加速度因子 ──
    {
        "name": "fut_price_velocity",
        "description": "价格加速度因子：动量的一阶差分（加速度）。加速度正则动量加速偏多，加速度负则动量衰减偏空。Wind平台趋势动能二阶分析。",
        "input_fields": ["close"],
        "params": {"window": 10, "accel_window": 5},
        "lookback": 20,
        "impl": """
    window = int(params.get('window', 10))
    accel_window = int(params.get('accel_window', 5))

    # 价格变化率（速度）
    velocity = np.zeros(n)
    velocity[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)

    # 速度的变化率（加速度）
    acceleration = np.zeros(n)
    acceleration[accel_window:] = (velocity[accel_window:] - velocity[:-accel_window]) / np.maximum(np.abs(velocity[:-accel_window]), 1e-10)
    acceleration = np.clip(acceleration, -5, 5)

    # 加速度正=动量加速做多，加速度负=动量衰减做空
    signal = np.tanh(acceleration * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "价格加速度因子：动量的一阶差分（加速度）。加速度正则动量加速偏多，负则动量衰减偏空。",
        },
    },
    # ── 12. 累积量因子 ──
    {
        "name": "fut_accumulation",
        "description": "累积量因子：基于成交量和价格位置的累积量指标。量价位置综合判断趋势强度。Wind平台高级量价分析。",
        "input_fields": ["close", "high", "low", "volume"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # 价格位置: 当日收盘在N日区间的位置
    hh = np.zeros(n)
    ll = np.zeros(n)
    for i in range(window, n):
        hh[i] = np.max(high[max(0, i-window+1):i+1])
        ll[i] = np.min(low[max(0, i-window+1):i+1])

    pos = np.zeros(n)
    range_mask = (hh - ll) > 1e-10
    pos[range_mask] = (close[range_mask] - ll[range_mask]) / (hh[range_mask] - ll[range_mask])

    # 成交量确认：价格在高位时放量 = 趋势确认，缩量 = 背离
    vol_ma = np.zeros(n)
    for i in range(window, n):
        vol_ma[i] = np.mean(volume[max(0, i-window+1):i+1])

    vol_ratio = volume / np.maximum(vol_ma, 1e-10)

    # 累积量 = 价格位置 * 成交量确认
    accumulation = (pos - 0.5) * 2 * np.clip(vol_ratio, 0.5, 2.0)
    signal = np.tanh(accumulation * 1.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "累积量因子：基于成交量和价格位置的累积量指标。量价位置综合判断趋势强度。",
        },
    },
    # ── 13. 多周期动量因子 ──
    {
        "name": "fut_multi_period_mom",
        "description": "多周期动量因子：短中长三期动量加权组合。三周期共振=强趋势信号，分歧=震荡信号。Wind平台综合动量判断。",
        "input_fields": ["close"],
        "params": {"short_window": 5, "mid_window": 20, "long_window": 60},
        "lookback": 65,
        "impl": """
    short_w = int(params.get('short_window', 5))
    mid_w = int(params.get('mid_window', 20))
    long_w = int(params.get('long_window', 60))

    # 三周期动量
    short_mom = np.zeros(n)
    mid_mom = np.zeros(n)
    long_mom = np.zeros(n)

    short_mom[short_w:] = (close[short_w:] - close[:-short_w]) / np.maximum(close[:-short_w], 1e-10)
    mid_mom[mid_w:] = (close[mid_w:] - close[:-mid_w]) / np.maximum(close[:-mid_w], 1e-10)
    long_mom[long_w:] = (close[long_w:] - close[:-long_w]) / np.maximum(close[:-long_w], 1e-10)

    # 归一化到[-1, 1]
    short_sig = np.tanh(short_mom * 10)
    mid_sig = np.tanh(mid_mom * 5)
    long_sig = np.tanh(long_mom * 2)

    # 加权组合：短期权重高，但分歧时降低权重
    consensus = 1 - np.abs(np.std([short_sig, mid_sig, long_sig], axis=0))
    signal = (short_sig * 0.5 + mid_sig * 0.3 + long_sig * 0.2) * consensus
    signal = np.tanh(signal * 1.5)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "多周期动量因子：短中长三期动量加权组合。三周期共振=强趋势信号，分歧=震荡信号。",
        },
    },
    # ── 14. 波动率锥因子 ──
    {
        "name": "fut_vol_cone",
        "description": "波动率锥因子：短期波动率与长期波动率的比值。波动率锥上翘=短期风险加大偏空，下倾=短期风险缓解偏多。Wind平台波动率期限结构分析。",
        "input_fields": ["close"],
        "params": {"short_window": 5, "long_window": 60},
        "lookback": 65,
        "impl": """
    short_w = int(params.get('short_window', 5))
    long_w = int(params.get('long_window', 60))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 短期和长期波动率
    short_vol = np.zeros(n)
    long_vol = np.zeros(n)
    for i in range(long_w, n):
        short_vol[i] = np.std(ret[max(0, i-short_w+1):i+1], ddof=0) * np.sqrt(252 / short_w)
        long_vol[i] = np.std(ret[max(0, i-long_w+1):i+1], ddof=0) * np.sqrt(252 / long_w)

    # 波动率锥 = 短期 / 长期
    vol_cone = np.zeros(n)
    mask = long_vol > 1e-10
    vol_cone[mask] = short_vol[mask] / long_vol[mask]
    vol_cone = np.clip(vol_cone, 0.1, 5.0)

    # 上翘(>1) = 短期风险加大偏空，下倾(<1) = 短期风险缓解偏多
    signal = np.tanh((1 - vol_cone) * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 4,
            "narrative": "波动率锥因子：短期波动率与长期波动率的比值。波动率锥上翘=短期风险加大偏空，下倾相反。",
        },
    },
    # ── 15. 价格分形维度因子 ──
    {
        "name": "fut_fractal_dim",
        "description": "价格分形维度因子：基于Higuchi算法的分形维度。D接近1=趋势强，D接近2=噪音大。Wind平台市场结构分析。",
        "input_fields": ["close"],
        "params": {"window": 30, "kmax": 10},
        "lookback": 35,
        "impl": """
    window = int(params.get('window', 30))
    kmax = int(params.get('kmax', 10))

    def _higuchi_fd(series, k_max):
        n = len(series)
        if n < k_max * 2:
            return 1.5
        lk = np.zeros(k_max)
        for k in range(1, k_max + 1):
            lm = 0.0
            for m in range(k):
                n_m = int(np.floor((n - m) / k))
                if n_m <= 1:
                    continue
                segments = series[m + np.arange(n_m) * k]
                length = np.sum(np.abs(np.diff(segments))) * (n - 1) / (n_m * k)
                lm += length
            lk[k-1] = lm / k if k > 0 else 0
        # 对log(k)和log(L(k))做线性回归
        k_vals = np.arange(1, k_max + 1, dtype=float)
        valid = lk > 1e-10
        if np.sum(valid) < 3:
            return 1.5
        log_k = np.log(k_vals[valid])
        log_l = np.log(lk[valid])
        fd = (np.sum(log_k * log_l) - np.sum(log_k) * np.sum(log_l) / len(log_k)) / \
             (np.sum(log_k ** 2) - np.sum(log_k) ** 2 / len(log_k) + 1e-10)
        return 2.0 - fd  # 分形维度

    fd = np.zeros(n)
    for i in range(window, n):
        seg = close[max(0, i-window+1):i+1]
        fd[i] = _higuchi_fd(seg, kmax)

    # D接近1=趋势强，D接近2=噪音大
    # 低分形维度(趋势强)偏多，高分形维度(噪音大)偏空
    signal = np.tanh((1.5 - fd) * 2)
""",
        "economic_logic": {
            "theory": 5,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "价格分形维度因子：基于Higuchi算法的分形维度。D接近1=趋势强，D接近2=噪音大。",
        },
    },
    # ── 16. 量价相关性稳定性因子 ──
    {
        "name": "fut_vp_corr_stability",
        "description": "量价相关性稳定性因子：量价相关性的时间序列稳定性。稳定性高=量价关系可靠做趋势，稳定性低=量价关系紊乱做反转。Wind平台量价关系深度分析。",
        "input_fields": ["close", "volume"],
        "params": {"window": 20, "sub_window": 10},
        "lookback": 30,
        "impl": """
    window = int(params.get('window', 20))
    sub_w = int(params.get('sub_window', 10))

    # 价格变动和成交量变化
    price_chg = np.zeros(n)
    price_chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol_chg = np.zeros(n)
    vol_chg[1:] = (volume[1:] - volume[:-1]) / np.maximum(volume[:-1], 1e-10)

    # 主窗口和子窗口的量价相关性
    main_corr = np.zeros(n)
    sub_corr = np.zeros(n)
    for i in range(window + sub_w, n):
        seg = slice(max(0, i-window+1), i+1)
        p = price_chg[seg]
        v = vol_chg[seg]
        if np.std(p, ddof=0) > 1e-10 and np.std(v, ddof=0) > 1e-10:
            main_corr[i] = np.corrcoef(p, v)[0, 1]

        sub_seg = slice(max(0, i-sub_w+1), i+1)
        p2 = price_chg[sub_seg]
        v2 = vol_chg[sub_seg]
        if np.std(p2, ddof=0) > 1e-10 and np.std(v2, ddof=0) > 1e-10:
            sub_corr[i] = np.corrcoef(p2, v2)[0, 1]

    # 稳定性 = 1 - |主相关 - 子相关|
    stability = 1 - np.abs(main_corr - sub_corr)
    stability = np.clip(stability, 0, 1)

    # 稳定性高=量价关系可靠做趋势方向
    trend_dir = np.tanh(price_chg * 10)
    signal = stability * trend_dir
    signal = np.tanh(signal * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "量价相关性稳定性因子：量价相关性的时间序列稳定性。稳定性高=量价关系可靠做趋势，低=关系紊乱做反转。",
        },
    },
    # ── 17. 跳跃风险因子 ──
    {
        "name": "fut_jump_risk",
        "description": "跳跃风险因子：收益率的非连续跳跃幅度和频率度量。跳跃频繁=微观结构风险大=偏空。Wind平台微观结构风险分析。",
        "input_fields": ["close"],
        "params": {"window": 20, "jump_threshold": 3.0},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))
    jump_th = float(params.get('jump_threshold', 3.0))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 滚动标准差
    rolling_std = np.zeros(n)
    for i in range(window, n):
        rolling_std[i] = np.std(ret[max(0, i-window+1):i+1], ddof=0)

    # 跳跃检测：|r| > threshold * σ
    jump_count = np.zeros(n)
    jump_magnitude = np.zeros(n)
    for i in range(window, n):
        seg = ret[max(0, i-window+1):i+1]
        seg_std = np.std(seg, ddof=0)
        if seg_std > 1e-10:
            jumps = np.abs(seg) > jump_th * seg_std
            jump_count[i] = np.sum(jumps)
            if np.sum(jumps) > 0:
                jump_magnitude[i] = np.mean(np.abs(seg[jumps])) / seg_std

    # 跳跃频率高+幅度大=风险大偏空
    jump_score = jump_count * jump_magnitude
    signal = -np.tanh(jump_score * 0.1)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 5,
            "institutional": 3,
            "narrative": "跳跃风险因子：收益率的非连续跳跃幅度和频率度量。跳跃频繁=微观结构风险大=偏空。",
        },
    },
    # ── 18. Rogers-Satchell 波动率 ──
    {
        "name": "fut_rogers_satchell",
        "description": "Rogers-Satchell波动率因子：允许漂移项的日内波动率估计器。比Garman-Klass更鲁棒。Wind平台高级波动率估计器。",
        "input_fields": ["open", "high", "low", "close"],
        "params": {"window": 20},
        "lookback": 25,
        "impl": """
    window = int(params.get('window', 20))

    # Rogers-Satchell 波动率: σ² = ln(H/C)*ln(H/O) + ln(L/C)*ln(L/O)
    log_ho = np.log(high / np.maximum(open_, 1e-10))
    log_lo = np.log(low / np.maximum(open_, 1e-10))
    log_hc = np.log(high / np.maximum(close, 1e-10))
    log_lc = np.log(low / np.maximum(close, 1e-10))

    rs_var = log_ho * log_hc + log_lo * log_lc
    rs_var = np.maximum(rs_var, 0)

    # 滚动均值
    rs_vol = np.zeros(n)
    for i in range(window, n):
        rs_vol[i] = np.sqrt(np.mean(rs_var[max(0, i-window+1):i+1]))

    # 归一化：高波动偏空
    rs_norm = rs_vol / np.maximum(close, 1e-10)
    signal = -np.tanh(rs_norm * 100)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "Rogers-Satchell波动率因子：允许漂移项的日内波动率估计器。比Garman-Klass更鲁棒。",
        },
    },
    # ── 19. 波动率峰度因子 ──
    {
        "name": "fut_vol_kurtosis",
        "description": "波动率峰度因子：收益率的峰度。高峰度=厚尾=极端事件风险大偏空。Wind平台高阶矩风险分析。",
        "input_fields": ["close"],
        "params": {"window": 30},
        "lookback": 35,
        "impl": """
    window = int(params.get('window', 30))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 滚动峰度（超额峰度，正态分布=3）
    kurt = np.zeros(n)
    for i in range(window, n):
        seg = ret[max(0, i-window+1):i+1]
        if len(seg) > 3 and np.std(seg, ddof=0) > 1e-10:
            kurt[i] = np.mean((seg - np.mean(seg)) ** 4) / (np.std(seg, ddof=0) ** 4 + 1e-10)

    # 超额峰度 > 3 为厚尾 = 风险偏空
    excess_kurt = kurt - 3
    signal = -np.tanh(excess_kurt * 0.3)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "波动率峰度因子：收益率的峰度。高峰度=厚尾=极端事件风险大偏空。",
        },
    },
    # ── 20. 涨跌不对称因子 ──
    {
        "name": "fut_asymmetry",
        "description": "涨跌不对称因子：上涨日和下跌日的收益率不对称性。上行/下行波动率比<1=下跌风险更大偏空。Wind平台风险收益不对称分析。",
        "input_fields": ["close"],
        "params": {"window": 30},
        "lookback": 35,
        "impl": """
    window = int(params.get('window', 30))

    # 日收益率
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

    # 上行/下行波动率比
    asym = np.zeros(n)
    for i in range(window, n):
        seg = ret[max(0, i-window+1):i+1]
        up_ret = seg[seg > 0]
        down_ret = seg[seg < 0]
        up_vol = np.std(up_ret, ddof=0) if len(up_ret) > 1 else 0
        down_vol = np.std(down_ret, ddof=0) if len(down_ret) > 1 else 0
        if down_vol > 1e-10:
            asym[i] = up_vol / down_vol
        elif up_vol > 1e-10:
            asym[i] = 2.0  # 只有上涨
        else:
            asym[i] = 1.0

    # 上行/下行波动率比 < 1 = 下跌更剧烈 = 偏空
    signal = np.tanh((asym - 1) * 2)
""",
        "economic_logic": {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "涨跌不对称因子：上涨日和下跌日的收益率不对称性。上行/下行波动率比<1=下跌风险更大偏空。",
        },
    },
]


# ─── 去重：检查与现有种子因子的重复 ──────────────────────────


def load_existing_futures_names() -> set[str]:
    """加载现有期货种子因子的所有名称。"""
    names: set[str] = set()
    seeds_dir = Path(__file__).resolve().parent.parent / "seeds" / "futures"

    for yaml_file in sorted(seeds_dir.glob("*.yaml")):
        if yaml_file.name == "wind_cta.yaml":
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
    """生成 wind_cta.yaml 种子文件。

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
        "family": "wind_cta",
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
    print(f"\n✅ 成功生成 {count} 个 Wind 平台因子到 {OUTPUT_FILE}")
