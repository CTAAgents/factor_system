"""
loop_engine/seed_pool.py — 种子池

HARNESS §11-loop-engineering.md §2.2:
    从 multi_factor_strategy.py 提取 12 个内置因子作为种子，启动 L2 演化。

种子因子清单（来自 multi_factor_strategy.py FACTOR_WEIGHTS + A 股补充）:
    1. momentum              动量因子（全市场）
    2. volatility_reversion  波动率回归（全市场）
    3. volume_flow           资金流（全市场）
    4. oi_change             持仓量变化（期货）
    5. basis                 基差（期货）
    6. inventory_pct         库存分位（期货）
    7. capacity              开工率（期货）
    8. macro_regime          宏观制度（全市场）
    9. rate_proxy            利率代理（全市场）
    10. pmi_proxy            PMI 代理（全市场）
    11. position_rank        龙虎持仓（期货）
    12. warrant_change       仓单变化（期货）
    13. value_factor         价值因子（A 股）
    14. quality_factor       质量因子（A 股）
    15. size_factor          市值因子（A 股）

版本: v8.10.0
"""

from __future__ import annotations

from typing import Any, Optional

from .contracts import EconomicLogic, FactorProgram, FactorSignature
from .factor_program import create_factor_program


# ─── 种子因子代码模板 ─────────────────────────────────────

# 每个种子因子以可执行 Python 代码形式提供，符合 factor_program() 接口约束。
# 参数空间用于 optuna 微观演化搜索。

_SEED_MOMENTUM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 价格变化率
    chg = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    chg[:window] = 0
    # MA 斜率
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    ma_slope = np.zeros(n)
    if n > 1:
        ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
    score = 0.5 * np.tanh(chg / 0.05) + 0.3 * np.tanh(ma_slope * 30) + 0.2 * np.tanh(chg / 0.1)
    return np.clip(score, -1.0, 1.0)
"""

_SEED_VOLATILITY_REVERSION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    bb_width_threshold = float(params.get('bb_width_threshold', 0.05))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 布林带
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
    upper = ma + 2 * std
    lower = ma - 2 * std
    bb_pos = (close - lower) / np.maximum(upper - lower, 1e-10)
    bb_pos = np.clip(bb_pos, 0, 1)
    # 高波动回归：bb_pos 接近 1 偏空，接近 0 偏多
    score = (0.5 - bb_pos) * 1.0
    return np.clip(score, -1.0, 1.0)
"""

_SEED_VOLUME_FLOW_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 10))
    n = len(close)
    if n < window + 1:
        return np.zeros(n)
    # 量比
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    # 价格变化
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 放量上涨 → 多头；放量下跌 → 空头
    score = np.where(
        vol_ratio > 1.3,
        np.tanh(chg / 0.02) * 0.5,
        np.where(vol_ratio < 0.7, np.tanh(chg / 0.05) * 0.3, 0)
    )
    return np.clip(score, -1.0, 1.0)
"""

_SEED_OI_CHANGE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'open_interest' in (data.columns if hasattr(data, 'columns') else data):
        oi = data['open_interest'].values if hasattr(data, 'open_interest') else data['open_interest']
        window = int(params.get('window', 5))
        if n < window + 1:
            return np.zeros(n)
        oi_prev = np.roll(oi, window)
        oi_prev[:window] = oi[:window]
        oi_ratio = (oi - oi_prev) / np.maximum(oi_prev, 1e-10)
        chg = np.zeros(n)
        chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
        score = np.where(
            (oi_ratio > 0.05) & (chg > 0), 0.6,
            np.where(
                (oi_ratio > 0.05) & (chg < 0), -0.6,
                np.where(
                    (oi_ratio < -0.05) & (chg > 0), 0.3,
                    np.where((oi_ratio < -0.05) & (chg < 0), -0.3, 0)
                )
            )
        )
        return np.clip(score, -1.0, 1.0)
    else:
        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
        window = int(params.get('window', 5))
        if n < window + 1:
            return np.zeros(n)
        vol_prev = np.roll(volume, window)
        vol_prev[:window] = volume[:window]
        vol_ratio = (volume - vol_prev) / np.maximum(vol_prev, 1e-10)
        chg = np.zeros(n)
        chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
        score = np.where(
            (vol_ratio > 0.3) & (chg > 0), 0.5,
            np.where(
                (vol_ratio > 0.3) & (chg < 0), -0.5,
                np.where(
                    (vol_ratio < -0.3) & (chg > 0), 0.2,
                    np.where((vol_ratio < -0.3) & (chg < 0), -0.2, 0)
                )
            )
        )
        return np.clip(score, -1.0, 1.0)
"""

_SEED_BASIS_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'basis_pct' in (data.columns if hasattr(data, 'columns') else data):
        basis_pct = data['basis_pct'].values if hasattr(data, 'basis_pct') else data['basis_pct']
        threshold = float(params.get('threshold', 1.0))
        score = np.zeros(n)
        score = np.where(basis_pct > threshold, -0.6,
                np.where(basis_pct > threshold/2, -0.3,
                np.where(basis_pct < -threshold, 0.6,
                np.where(basis_pct < -threshold/2, 0.3, 0))))
        return np.clip(score, -1.0, 1.0)
    else:
        high = data['high'].values if hasattr(data, 'high') else data['high']
        low = data['low'].values if hasattr(data, 'low') else data['low']
        window = 20
        if n < window:
            return np.zeros(n)
        range_ratio = (close - low) / np.maximum(high - low, 1e-10)
        score = (0.5 - range_ratio) * 1.0
        return np.clip(score, -1.0, 1.0)
"""

_SEED_INVENTORY_PCT_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'inventory_pct' in (data.columns if hasattr(data, 'columns') else data):
        pct = data['inventory_pct'].values if hasattr(data, 'inventory_pct') else data['inventory_pct']
        score = (0.5 - pct) * 2.0
        return np.clip(score, -1.0, 1.0)
    else:
        window = 20
        if n < window:
            return np.zeros(n)
        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
        avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
        vol_ratio = volume / np.maximum(avg_vol, 1e-10)
        score = np.tanh((1 - vol_ratio) * 2) * 0.5
        return np.clip(score, -1.0, 1.0)
"""

_SEED_CAPACITY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'capacity_pct' in (data.columns if hasattr(data, 'columns') else data):
        pct = data['capacity_pct'].values if hasattr(data, 'capacity_pct') else data['capacity_pct']
        score = (0.5 - pct) * 2.0
        return np.clip(score, -1.0, 1.0)
    else:
        window = 30
        if n < window:
            return np.zeros(n)
        vol_std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
        avg_std = np.convolve(vol_std, np.ones(window)/window, mode='same')
        vol_ratio = vol_std / np.maximum(avg_std, 1e-10)
        score = np.tanh((1 - vol_ratio) * 1.5) * 0.5
        return np.clip(score, -1.0, 1.0)
"""

_SEED_MACRO_REGIME_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'macro_signal' in (data.columns if hasattr(data, 'columns') else data):
        macro = data['macro_signal'].values if hasattr(data, 'macro_signal') else data['macro_signal']
        score = np.where(macro == 'bull', 0.5,
                np.where(macro == 'bear', -0.5, 0))
        return np.clip(score, -1.0, 1.0)
    else:
        window = 60
        if n < window:
            return np.zeros(n)
        trend = np.zeros(n)
        trend[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
        score = np.tanh(trend * 10) * 0.3
        return np.clip(score, -1.0, 1.0)
"""

_SEED_RATE_PROXY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'rate_mom' in (data.columns if hasattr(data, 'columns') else data):
        rate_mom = data['rate_mom'].values if hasattr(data, 'rate_mom') else data['rate_mom']
        score = -np.tanh(rate_mom / 0.25)
        return np.clip(score, -1.0, 1.0)
    else:
        window = 30
        if n < window:
            return np.zeros(n)
        vol_std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
        score = -np.tanh(vol_std / np.maximum(np.mean(vol_std), 1e-10) * 2) * 0.3
        return np.clip(score, -1.0, 1.0)
"""

_SEED_PMI_PROXY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'pmi' in (data.columns if hasattr(data, 'columns') else data):
        pmi = data['pmi'].values if hasattr(data, 'pmi') else data['pmi']
        pmi_mom = data['pmi_mom'].values if hasattr(data, 'pmi_mom') in (data.columns if hasattr(data, 'columns') else data) else None
        level = np.tanh((pmi - 50.0) / 5.0)
        if pmi_mom is not None:
            mom = np.tanh(pmi_mom / 1.0) * 0.5
            score = level * 0.6 + mom * 0.4
        else:
            score = level
        return np.clip(score, -1.0, 1.0)
    else:
        window = 20
        if n < window:
            return np.zeros(n)
        ma = np.convolve(close, np.ones(window)/window, mode='same')
        ma_slope = np.zeros(n)
        if n > 1:
            ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
        score = np.tanh(ma_slope * 50) * 0.4
        return np.clip(score, -1.0, 1.0)
"""

_SEED_POSITION_RANK_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'top5_ratio' in (data.columns if hasattr(data, 'columns') else data):
        top5 = data['top5_ratio'].values if hasattr(data, 'top5_ratio') else data['top5_ratio']
        score = np.where(top5 > 0.4, 0.3,
                np.where(top5 > 0.3, 0.15,
                np.where(top5 < -0.3, -0.3,
                np.where(top5 < -0.2, -0.15, 0))))
        return np.clip(score, -1.0, 1.0)
    else:
        window = 10
        if n < window:
            return np.zeros(n)
        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
        avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
        vol_ratio = volume / np.maximum(avg_vol, 1e-10)
        chg = np.zeros(n)
        chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
        score = np.where(vol_ratio > 1.5, np.tanh(chg / 0.02) * 0.3, 0)
        return np.clip(score, -1.0, 1.0)
"""

_SEED_WARRANT_CHANGE_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'warrant_change_pct' in (data.columns if hasattr(data, 'columns') else data):
        pct = data['warrant_change_pct'].values if hasattr(data, 'warrant_change_pct') else data['warrant_change_pct']
        score = -np.tanh(pct / 5.0) * 0.4
        return np.clip(score, -1.0, 1.0)
    else:
        window = 15
        if n < window:
            return np.zeros(n)
        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
        vol_ma = np.convolve(volume, np.ones(window)/window, mode='same')
        vol_dev = (volume - vol_ma) / np.maximum(vol_ma, 1e-10)
        score = -np.tanh(vol_dev) * 0.2
        return np.clip(score, -1.0, 1.0)
"""

# ─── A 股种子因子 ─────────────────────────────────────────

_SEED_VALUE_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用价格/成交量比值近似估值（价格低+放量=价值凸显）
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    pct_rank = np.argsort(np.argsort(close)) / max(n - 1, 1)  # 0~1 价格分位
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    # 低价+放量 → 价值信号
    score = (1 - pct_rank) * np.tanh(vol_ratio * 0.5) - 0.3
    return np.clip(score, -1.0, 1.0)
"""

_SEED_QUALITY_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用价格稳定性近似质量（低波动+稳定上升=高质量）
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    rolling_vol = np.array([
        np.std(returns[max(0, i-window+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    ma_slope = np.zeros(n)
    if n > 1:
        ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
    # 低波动+正斜率=高质量
    quality_score = np.tanh(-rolling_vol * 20 + 0.5) + np.tanh(ma_slope * 30)
    return np.clip(quality_score, -1.0, 1.0)
"""

_SEED_SIZE_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用成交量/价格近似市值效应（小市值效应代理）
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_deviation = volume / np.maximum(avg_vol, 1e-10)  # 成交量偏离
    price_level = close / np.maximum(np.mean(close[:window]), 1e-10)  # 价格水平
    # 小市值代理：低成交量+低价 = 偏小盘
    size_proxy = np.tanh(1.0 / (price_level + 0.1)) * np.tanh(1.0 / (vol_deviation + 0.1))
    # 小盘溢价：做多小盘
    score = size_proxy * 0.5
    return np.clip(score, -1.0, 1.0)
"""


# ─── 种子因子定义 ─────────────────────────────────────────

_SEED_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "momentum",
        "code": _SEED_MOMENTUM_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=5,
            narrative="动量因子：投资者过度反应/反应不足导致价格延续。理论支撑=行为金融学动量效应。",
        ),
    },
    {
        "name": "volatility_reversion",
        "code": _SEED_VOLATILITY_REVERSION_CODE,
        "params": {"window": 20, "bb_width_threshold": 0.05},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="波动率回归：高波动后均值回归。理论支撑=波动率锥与均值回归现象。",
        ),
    },
    {
        "name": "volume_flow",
        "code": _SEED_VOLUME_FLOW_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=15,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=5, institutional=4,
            narrative="资金流：放量方向反映知情交易者意图。理论支撑=微观结构信息不对称。",
        ),
    },
    {
        "name": "oi_change",
        "code": _SEED_OI_CHANGE_CODE,
        "params": {"window": 5},
        "signature": FactorSignature(
            input_fields=["close", "open_interest"],
            output_type="signal",
            frequency="daily",
            lookback=10,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=5, institutional=4,
            narrative="持仓量变化：OI+价格组合反映多空意图。理论支撑=持仓量与价格关系理论。",
        ),
    },
    {
        "name": "basis",
        "code": _SEED_BASIS_CODE,
        "params": {"threshold": 1.0},
        "signature": FactorSignature(
            input_fields=["basis_pct"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=4, institutional=5,
            narrative="基差因子：期现价差反映套利机会与交割回归。理论支撑=持有成本模型。",
        ),
    },
    {
        "name": "inventory_pct",
        "code": _SEED_INVENTORY_PCT_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["inventory_pct"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=2, microstructure=4, institutional=5,
            narrative="库存分位：累库偏空、去库偏多。理论支撑=供需均衡理论。",
        ),
    },
    {
        "name": "capacity",
        "code": _SEED_CAPACITY_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["capacity_pct"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=2, microstructure=4, institutional=5,
            narrative="开工率：供应充裕偏空、收紧偏多。理论支撑=供给侧分析。",
        ),
    },
    {
        "name": "macro_regime",
        "code": _SEED_MACRO_REGIME_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["macro_signal"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="宏观制度：bull/bear/neutral 三态。理论支撑=宏观周期理论。",
        ),
    },
    {
        "name": "rate_proxy",
        "code": _SEED_RATE_PROXY_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["rate_mom"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=5,
            narrative="利率代理：LPR1Y 环比。理论支撑=利率平价与融资成本理论。",
        ),
    },
    {
        "name": "pmi_proxy",
        "code": _SEED_PMI_PROXY_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["pmi", "pmi_mom"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=5,
            narrative="PMI 代理：制造业景气度。理论支撑=景气周期理论。",
        ),
    },
    {
        "name": "position_rank",
        "code": _SEED_POSITION_RANK_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["top5_ratio"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=5, institutional=3,
            narrative="龙虎持仓：会员持仓集中度变化。理论支撑=知情交易者理论。",
        ),
    },
    {
        "name": "warrant_change",
        "code": _SEED_WARRANT_CHANGE_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["warrant_change_pct"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=2, microstructure=4, institutional=5,
            narrative="仓单变化：可交割供应压力。理论支撑=交割机制与供需理论。",
        ),
    },
    # ── A 股种子因子 ──
    {
        "name": "value_factor",
        "code": _SEED_VALUE_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=4,
            narrative="价值因子：低价+放量近似估值安全边际。理论支撑=价值投资理论。",
        ),
    },
    {
        "name": "quality_factor",
        "code": _SEED_QUALITY_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="质量因子：低波动+稳定上涨代理盈利能力。理论支撑=质量溢价理论。",
        ),
    },
    {
        "name": "size_factor",
        "code": _SEED_SIZE_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=4, microstructure=3, institutional=3,
            narrative="市值因子：成交量+价格代理市值大小。理论支撑=小市值效应。",
        ),
    },
]


# ─── 种子池管理器 ─────────────────────────────────────────

class SeedPool:
    """种子池管理器 — 加载/查询/注入种子因子。

    Usage:
        pool = SeedPool()
        all_seeds = pool.load_all_seeds()
        seed_by_name = pool.get_seed("momentum")
    """

    def __init__(self, trace_id: Optional[str] = None):
        self._trace_id = trace_id
        self._cache: dict[str, FactorProgram] = {}

    def load_all_seeds(self) -> list[FactorProgram]:
        """加载全部 12 个种子因子。"""
        if not self._cache:
            for defn in _SEED_DEFINITIONS:
                fp = create_factor_program(
                    name=defn["name"],
                    code=defn["code"],
                    params=defn["params"],
                    signature=defn["signature"],
                    economic_logic=defn["economic_logic"],
                    source="seed",
                    parent_id=None,
                    generation=0,
                    trace_id=self._trace_id,
                )
                self._cache[defn["name"]] = fp
        return list(self._cache.values())

    def get_seed(self, name: str) -> Optional[FactorProgram]:
        """按名称获取种子因子。"""
        if not self._cache:
            self.load_all_seeds()
        return self._cache.get(name)

    def count(self) -> int:
        """返回种子因子总数。"""
        return len(_SEED_DEFINITIONS)

    def list_names(self) -> list[str]:
        """返回种子因子名称列表。"""
        return [d["name"] for d in _SEED_DEFINITIONS]

    def inject_from_l1(
        self,
        candidate: dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> FactorProgram:
        """L1 注入接口 — 将 L1 Bootstrapping 产出的候选因子注入种子池。

        HARNESS §11-loop-engineering.md §15: L1 → L2 种子池入口。

        Args:
            candidate: SeedCandidate 字典（必须包含 name/code/params/signature/
                       economic_logic 字段）
            trace_id: 全链路 trace_id（None 时使用 candidate 中的 trace_id）

        Returns:
            FactorProgram — 注入后的因子程序（source="bootstrapping"）

        Raises:
            ValueError: candidate 缺少必需字段
        """
        required = ("name", "code", "params", "signature", "economic_logic")
        for k in required:
            if k not in candidate:
                raise ValueError(f"SeedCandidate 缺少必需字段: {k}")

        injected_trace = trace_id or candidate.get("trace_id") or self._trace_id
        fp = create_factor_program(
            name=candidate["name"],
            code=candidate["code"],
            params=candidate["params"],
            signature=candidate["signature"],
            economic_logic=candidate["economic_logic"],
            source="bootstrapping",
            parent_id=candidate.get("candidate_id"),
            generation=0,
            trace_id=injected_trace,
        )
        # 注入到缓存（按 candidate_id 索引，避免与内置种子碰撞）
        cache_key = f"l1:{candidate.get('candidate_id', candidate['name'])}"
        self._cache[cache_key] = fp
        return fp

    def list_injected_l1(self) -> list[FactorProgram]:
        """列出所有从 L1 注入的种子因子。"""
        return [
            fp for k, fp in self._cache.items()
            if k.startswith("l1:") and fp.get("source") == "bootstrapping"
        ]


def get_default_seed_pool() -> SeedPool:
    """获取默认种子池实例。"""
    return SeedPool()


__all__ = [
    "SeedPool",
    "get_default_seed_pool",
]
