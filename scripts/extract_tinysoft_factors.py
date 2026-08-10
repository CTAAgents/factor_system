"""
scripts/extract_tinysoft_factors.py — 天软商品期货因子提取与生成

从天软因子算法文档中提取因子，补充 FTS 期货种子因子库。
仅包含与现有种子因子不重复的因子。

分类与来源:
  1. 动量因子 (12 个): 主力涨幅、上涨次数占比、涨幅占比、均线偏离比值、
     快慢均线偏离率、隔夜趋势因子、日内动量因子、日内累计振幅因子、
     日内GK波动趋势因子、日内RS波动趋势因子、日内PK波动趋势因子、日内YZ波动趋势因子
  2. 期限结构因子: ✅ 已完全覆盖，跳过
  3. 量价因子 (2 个新增): 均价突破因子、标准化均价突破因子
  4. 价值因子 (1 个): 主力对数价格差
  5. 持仓因子 (3 个新增): 持仓量涨幅、持仓金额变化比值、持仓金额涨幅
  6. 库存因子 (2 个新增): 仓单水平、仓单涨幅
  7. 波动率因子 (7 个新增): 变异系数因子2、特质波动率、RS波动率因子、
     GK波动率因子、PK波动率因子、YZ波动率因子、波动率因子

用法:
    python scripts/extract_tinysoft_factors.py
"""

from __future__ import annotations

import os
from typing import Any

import yaml


class LiteralBlock(str):
    """强制 YAML 使用 | 字面块标量格式输出。"""


def _literal_block_representer(dumper: yaml.Dumper, data: LiteralBlock) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


# ─── 辅助函数 ─────────────────────────────────────────────


def _factor(**kwargs: Any) -> dict[str, Any]:
    """按顺序构建因子字典，确保 YAML 字段顺序与现有种子文件一致。"""
    return dict(kwargs)


def _make_code(body: str) -> str:
    """包装 factor_program 函数体，生成完整的 code 字符串。"""
    return f"\n    def factor_program(data, params):\n        import numpy as np\n{body}\n"


def _family(name: str, factors: list[dict[str, Any]]) -> dict[str, Any]:
    return _factor(
        family=name,
        version="1.0",
        market="futures",
        factors=factors,
    )


# ─── 1. 动量因子（新增 12 个） ─────────────────────────────


def _build_momentum_factors() -> list[dict[str, Any]]:
    return [
        # 1.1 主力涨幅
        _factor(
            name="fut_main_contract_return",
            description="主力涨幅：主力合约过去 N 日价格涨幅，衡量趋势动量强度。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('lookback', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window:] = np.tanh(ret / 0.05)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"lookback": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=4,
                narrative="主力涨幅：主力合约过去 N 日价格涨幅，衡量趋势动量强度。",
            ),
        ),
        # 1.2 上涨次数占比
        _factor(
            name="fut_up_day_ratio",
            description="上涨次数占比：过去 N 日内上涨天数占比，判断趋势持续性。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('lookback', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        up_ratio = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            up_ratio[i] = np.sum(ret[i - window : i] > 0) / window\n"
                "        sig = (up_ratio - 0.5) * 2\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"lookback": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=2,
                institutional=3,
                narrative="上涨次数占比：过去 N 日内上涨天数占比，判断趋势持续性。",
            ),
        ),
        # 1.3 涨幅占比
        _factor(
            name="fut_up_return_ratio",
            description="涨幅占比：过去 N 日内上涨收益占全部收益的比例，衡量上涨动能强度。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('lookback', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        ratio = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            seg = ret[i - window : i]\n"
                "            up_sum = np.sum(seg[seg > 0])\n"
                "            total = np.sum(np.abs(seg))\n"
                "            ratio[i] = up_sum / max(total, 1e-10)\n"
                "        sig = (ratio - 0.5) * 2\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"lookback": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=2,
                institutional=3,
                narrative="涨幅占比：过去 N 日内上涨收益占全部收益的比例，衡量上涨动能强度。",
            ),
        ),
        # 1.4 均线偏离比值
        _factor(
            name="fut_price_ma_ratio",
            description="均线偏离比值：价格与均线的比值，衡量价格偏离程度。价格远高于均线=超买。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 1:\n"
                "            return np.zeros(n)\n"
                "        ma = np.convolve(close, np.ones(window) / window, mode='same')\n"
                "        ratio = close / np.maximum(ma, 1e-10)\n"
                "        sig = np.tanh((ratio - 1.0) * 5)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=4,
                microstructure=3,
                institutional=3,
                narrative="均线偏离比值：价格与均线的比值，衡量价格偏离程度。价格远高于均线=超买。",
            ),
        ),
        # 1.5 快慢均线偏离率
        _factor(
            name="fut_ma_crossover",
            description="快慢均线偏离率：(快均线-慢均线)/慢均线，MACD 核心思想。快线上穿慢线=做多。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        fast = int(params.get('fast', 5))\n"
                "        slow = int(params.get('slow', 20))\n"
                "        if n < slow + 1:\n"
                "            return np.zeros(n)\n"
                "        ma_fast = np.convolve(close, np.ones(fast) / fast, mode='same')\n"
                "        ma_slow = np.convolve(close, np.ones(slow) / slow, mode='same')\n"
                "        dev = (ma_fast - ma_slow) / np.maximum(ma_slow, 1e-10)\n"
                "        sig = np.tanh(dev * 20)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"fast": 5, "slow": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="快慢均线偏离率：(快均线-慢均线)/慢均线，MACD 核心思想。快线上穿慢线=做多。",
            ),
        ),
        # 1.6 隔夜趋势因子
        _factor(
            name="fut_overnight_trend",
            description="隔夜趋势因子：(开盘-前收盘)/前收盘，隔夜跳空方向。隔夜上涨=利多延续。",
            market="futures",
            code=_make_code(
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 3:\n"
                "            return np.zeros(n)\n"
                "        overnight = (open - np.roll(close, 1)) / np.maximum(np.roll(close, 1), 1e-10)\n"
                "        overnight[0] = 0\n"
                "        window = int(params.get('window', 5))\n"
                "        avg_overnight = np.convolve(overnight, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh(avg_overnight * 20)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 5},
            input_fields=["open", "close"],
            lookback=10,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=4,
                microstructure=4,
                institutional=3,
                narrative="隔夜趋势因子：(开盘-前收盘)/前收盘，隔夜跳空方向。隔夜上涨=利多延续。",
            ),
        ),
        # 1.7 日内动量因子
        _factor(
            name="fut_intraday_momentum",
            description="日内动量因子：(收盘-开盘)/开盘，衡量日内趋势强度。日内强势=短期动量。",
            market="futures",
            code=_make_code(
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 2:\n"
                "            return np.zeros(n)\n"
                "        intra_ret = (close - open) / np.maximum(open, 1e-10)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_intra = np.convolve(intra_ret, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh(avg_intra * 20)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["open", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内动量因子：(收盘-开盘)/开盘，衡量日内趋势强度。日内强势=短期动量。",
            ),
        ),
        # 1.8 日内累计振幅因子
        _factor(
            name="fut_intraday_amplitude",
            description="日内累计振幅因子：日内振幅的滚动平均，衡量价格活跃度。振幅扩大=趋势启动。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 2:\n"
                "            return np.zeros(n)\n"
                "        amp = (high - low) / np.maximum(close, 1e-10)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_amp = np.convolve(amp, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh((avg_amp - np.mean(avg_amp)) / np.maximum(np.std(avg_amp), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["high", "low", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内累计振幅因子：日内振幅的滚动平均，衡量价格活跃度。振幅扩大=趋势启动。",
            ),
        ),
        # 1.9 日内GK波动趋势因子
        _factor(
            name="fut_gk_vol_trend",
            description="日内GK波动趋势因子：Garman-Klass 波动率估计的趋势方向。GK波动率上升=波动加剧=趋势延续。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        hl = np.log(high / np.maximum(low, 1e-10))\n"
                "        co = np.log(close / np.maximum(open, 1e-10))\n"
                "        gk = 0.5 * hl ** 2 - (2 * np.log(2) - 1) * co ** 2\n"
                "        gk = np.clip(gk, 0, None)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_gk = np.convolve(gk, np.ones(window) / window, mode='same')\n"
                "        # 波动率趋势方向\n"
                "        trend = np.gradient(avg_gk)\n"
                "        sig = np.tanh(trend * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["high", "low", "open", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内GK波动趋势因子：Garman-Klass 波动率估计的趋势方向。GK波动率上升=波动加剧=趋势延续。",
            ),
        ),
        # 1.10 日内RS波动趋势因子
        _factor(
            name="fut_rs_vol_trend",
            description="日内RS波动趋势因子：Rogers-Satchell 波动率估计的趋势方向。RS对漂移项更稳健。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        ho = np.log(high / np.maximum(open, 1e-10))\n"
                "        hc = np.log(high / np.maximum(close, 1e-10))\n"
                "        lo = np.log(low / np.maximum(open, 1e-10))\n"
                "        lc = np.log(low / np.maximum(close, 1e-10))\n"
                "        rs = ho * hc + lo * lc\n"
                "        rs = np.clip(rs, 0, None)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_rs = np.convolve(rs, np.ones(window) / window, mode='same')\n"
                "        trend = np.gradient(avg_rs)\n"
                "        sig = np.tanh(trend * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["high", "low", "open", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内RS波动趋势因子：Rogers-Satchell 波动率估计的趋势方向。RS对漂移项更稳健。",
            ),
        ),
        # 1.11 日内PK波动趋势因子
        _factor(
            name="fut_pk_vol_trend",
            description="日内PK波动趋势因子：Parkinson 波动率估计的趋势方向。仅用高低价，计算简单。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        hl = np.log(high / np.maximum(low, 1e-10))\n"
                "        pk = hl ** 2 / (4 * np.log(2))\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_pk = np.convolve(pk, np.ones(window) / window, mode='same')\n"
                "        trend = np.gradient(avg_pk)\n"
                "        sig = np.tanh(trend * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["high", "low", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内PK波动趋势因子：Parkinson 波动率估计的趋势方向。仅用高低价，计算简单。",
            ),
        ),
        # 1.12 日内YZ波动趋势因子
        _factor(
            name="fut_yz_vol_trend",
            description="日内YZ波动趋势因子：Yang-Zhang 波动率估计的趋势方向。YZ综合了隔夜和日内波动，最全面。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        co = np.log(close / np.maximum(open, 1e-10))\n"
                "        oc = np.log(open / np.maximum(np.roll(close, 1), 1e-10))\n"
                "        oc[0] = 0\n"
                "        hl = np.log(high / np.maximum(low, 1e-10))\n"
                "        ho = np.log(high / np.maximum(open, 1e-10))\n"
                "        hc = np.log(high / np.maximum(close, 1e-10))\n"
                "        lo = np.log(low / np.maximum(open, 1e-10))\n"
                "        lc = np.log(low / np.maximum(close, 1e-10))\n"
                "        rs = ho * hc + lo * lc\n"
                "        rs = np.clip(rs, 0, None)\n"
                "        k = 0.34 / (1.34 + (window + 1) / (window - 1)) if window > 1 else 0.34\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        # 简化的 Yang-Zhang\n"
                "        yz = np.zeros(n)\n"
                "        for i in range(1, n):\n"
                "            start = max(0, i - window + 1)\n"
                "            seg_o = oc[start:i+1]\n"
                "            seg_c = co[start:i+1]\n"
                "            seg_rs = rs[start:i+1]\n"
                "            yz[i] = np.var(seg_o) + 0.5 * np.var(seg_c) + 0.5 * np.mean(seg_rs)\n"
                "        trend = np.gradient(yz)\n"
                "        sig = np.tanh(trend * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["high", "low", "open", "close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="日内YZ波动趋势因子：Yang-Zhang 波动率估计的趋势方向。YZ综合了隔夜和日内波动，最全面。",
            ),
        ),
    ]


# ─── 3. 量价因子（新增 2 个） ──────────────────────────────


def _build_volume_price_factors() -> list[dict[str, Any]]:
    return [
        # 3.1 均价突破因子
        _factor(
            name="fut_vwap_break",
            description="均价突破因子：价格突破 VWAP 的程度。价格>VWAP=多头强势，突破幅度越大信号越强。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 1:\n"
                "            return np.zeros(n)\n"
                "        typical_price = (high + low + close) / 3\n"
                "        vwap = np.convolve(typical_price * volume, np.ones(window), mode='same') / np.maximum(np.convolve(volume, np.ones(window), mode='same'), 1e-10)\n"
                "        break_dev = (close - vwap) / np.maximum(vwap, 1e-10)\n"
                "        sig = np.tanh(break_dev * 20)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "high", "low", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="均价突破因子：价格突破 VWAP 的程度。价格>VWAP=多头强势，突破幅度越大信号越强。",
            ),
        ),
        # 3.2 标准化均价突破因子
        _factor(
            name="fut_norm_vwap_break",
            description="标准化均价突破因子：用波动率标准化的 VWAP 偏离，避免不同品种量级差异。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        typical_price = (high + low + close) / 3\n"
                "        vwap = np.convolve(typical_price * volume, np.ones(window), mode='same') / np.maximum(np.convolve(volume, np.ones(window), mode='same'), 1e-10)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        vol = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            vol[i] = np.std(ret[i - window : i])\n"
                "        break_dev = (close - vwap) / np.maximum(vwap, 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            if vol[i] > 1e-10:\n"
                "                sig[i] = break_dev[i] / vol[i]\n"
                "        sig = np.tanh(sig * 5)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "high", "low", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="标准化均价突破因子：用波动率标准化的 VWAP 偏离，避免不同品种量级差异。",
            ),
        ),
    ]


# ─── 4. 价值因子（新增 1 个） ──────────────────────────────


def _build_value_factors() -> list[dict[str, Any]]:
    return [
        _factor(
            name="fut_log_price_diff",
            description="主力对数价格差：合约价格的对数差分，衡量价格水平变化。价格越低=价值越高=做多。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 60))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        log_p = np.log(np.maximum(close, 1e-10))\n"
                "        # 价格偏离长期均值程度\n"
                "        ma = np.convolve(log_p, np.ones(window) / window, mode='same')\n"
                "        dev = ma - log_p\n"
                "        # 价格越低(dev越大)越做多(均值回归)\n"
                "        sig = np.tanh(dev * 3)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 60},
            input_fields=["close"],
            lookback=65,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=4,
                microstructure=2,
                institutional=4,
                narrative="主力对数价格差：合约价格的对数差分，衡量价格水平变化。价格越低=价值越高=做多。",
            ),
        ),
    ]


# ─── 5. 持仓因子（新增 3 个） ──────────────────────────────


def _build_position_factors() -> list[dict[str, Any]]:
    return [
        # 5.1 持仓量涨幅
        _factor(
            name="fut_oi_growth",
            description="持仓量涨幅：持仓量的 N 日增长率。持仓快速增长=资金流入=趋势确认。",
            market="futures",
            code=_make_code(
                "        hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close) if 'close' in data else 1))\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        oi_growth = (hold[window:] - hold[:-window]) / np.maximum(hold[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window:] = np.tanh(oi_growth * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["close", "hold"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=4,
                narrative="持仓量涨幅：持仓量的 N 日增长率。持仓快速增长=资金流入=趋势确认。",
            ),
        ),
        # 5.2 持仓金额变化比值
        _factor(
            name="fut_oi_value_change",
            description="持仓金额变化比值：持仓金额(持仓量×价格)的变化率，结合价格和持仓量双重信息。",
            market="futures",
            code=_make_code(
                "        hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close) if 'close' in data else 1))\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        oi_value = hold * close\n"
                "        change = (oi_value[window:] - oi_value[:-window]) / np.maximum(oi_value[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window:] = np.tanh(change * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["close", "hold"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=4,
                narrative="持仓金额变化比值：持仓金额(持仓量×价格)的变化率，结合价格和持仓量双重信息。",
            ),
        ),
        # 5.3 持仓金额涨幅
        _factor(
            name="fut_oi_value_growth",
            description="持仓金额涨幅：持仓金额的 N 日增长率（绝对值）。衡量资金流入流出规模。",
            market="futures",
            code=_make_code(
                "        hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(len(close) if 'close' in data else 1))\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        oi_value = hold * close\n"
                "        growth = (oi_value[window:] - oi_value[:-window]) / np.maximum(oi_value[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window:] = np.tanh(growth * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "hold"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=4,
                narrative="持仓金额涨幅：持仓金额的 N 日增长率（绝对值）。衡量资金流入流出规模。",
            ),
        ),
    ]


# ─── 6. 库存因子（新增 2 个） ──────────────────────────────


def _build_inventory_factors() -> list[dict[str, Any]]:
    return [
        # 6.1 仓单水平
        _factor(
            name="fut_wr_level",
            description="仓单水平：仓单数量的原始水平值。仓单高=现货供应充足=利空。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        # 注：仓单数据需要外部数据源，这里用成交量近似\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        # 仓单水平用成交量的长期均值作为代理\n"
                "        window = int(params.get('window', 60))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_vol = np.convolve(volume, np.ones(window) / window, mode='same')\n"
                "        level = (volume - avg_vol) / np.maximum(avg_vol, 1e-10)\n"
                "        sig = -np.tanh(level * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 60},
            input_fields=["close", "volume"],
            lookback=65,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=2,
                microstructure=3,
                institutional=4,
                narrative="仓单水平：仓单数量的原始水平值。仓单高=现货供应充足=利空。",
            ),
        ),
        # 6.2 仓单涨幅
        _factor(
            name="fut_wr_growth",
            description="仓单涨幅：仓单数量的 N 日增长率。仓单增长加快=供应过剩=利空。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        # 仓单增长率用成交量增长率代理\n"
                "        vol_growth = (volume[window:] - volume[:-window]) / np.maximum(volume[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window:] = -np.tanh(vol_growth * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=2,
                microstructure=3,
                institutional=4,
                narrative="仓单涨幅：仓单数量的 N 日增长率。仓单增长加快=供应过剩=利空。",
            ),
        ),
    ]


# ─── 7. 波动率因子（新增 7 个） ────────────────────────────


def _build_volatility_factors() -> list[dict[str, Any]]:
    return [
        # 7.1 变异系数因子2
        _factor(
            name="fut_cv2",
            description="变异系数因子2：标准差/均值，用滚动窗口末端的波动率替代均值，强调尾部波动。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        cv2 = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            seg = close[i - window : i]\n"
                "            seg_ret = ret[i - window : i]\n"
                "            std_val = np.std(seg_ret)\n"
                "            mean_val = np.mean(seg)\n"
                "            cv2[i] = std_val / max(mean_val, 1e-10) if mean_val > 0 else 0\n"
                "        sig = np.tanh((cv2 - np.mean(cv2)) / np.maximum(np.std(cv2), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="变异系数因子2：标准差/均值，用滚动窗口末端的波动率替代均值，强调尾部波动。",
            ),
        ),
        # 7.2 特质波动率
        _factor(
            name="fut_idiosyncratic_vol",
            description="特质波动率：回归残差的波动率，剥离市场因子后的特质波动。特质波动率高=高估=做空。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        ivol = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            seg = ret[i - window : i]\n"
                "            # 以均值为市场基准，残差=收益-均值\n"
                "            mu = np.mean(seg)\n"
                "            resid = seg - mu\n"
                "            ivol[i] = np.std(resid)\n"
                "        sig = np.tanh((ivol - np.mean(ivol)) / np.maximum(np.std(ivol), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=4,
                narrative="特质波动率：回归残差的波动率，剥离市场因子后的特质波动。特质波动率高=高估=做空。",
            ),
        ),
        # 7.3 RS波动率因子
        _factor(
            name="fut_rs_vol",
            description="RS波动率因子：Rogers-Satchell 波动率估计，对漂移项稳健。高RS波动率=风险加大=做空。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        ho = np.log(high / np.maximum(open, 1e-10))\n"
                "        hc = np.log(high / np.maximum(close, 1e-10))\n"
                "        lo = np.log(low / np.maximum(open, 1e-10))\n"
                "        lc = np.log(low / np.maximum(close, 1e-10))\n"
                "        rs = ho * hc + lo * lc\n"
                "        rs = np.clip(rs, 0, None)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_rs = np.convolve(rs, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh((avg_rs - np.mean(avg_rs)) / np.maximum(np.std(avg_rs), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["high", "low", "open", "close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="RS波动率因子：Rogers-Satchell 波动率估计，对漂移项稳健。高RS波动率=风险加大=做空。",
            ),
        ),
        # 7.4 GK波动率因子
        _factor(
            name="fut_gk_vol",
            description="GK波动率因子：Garman-Klass 波动率估计，利用OHLC四价信息。高GK波动率=做空。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        hl = np.log(high / np.maximum(low, 1e-10))\n"
                "        co = np.log(close / np.maximum(open, 1e-10))\n"
                "        gk = 0.5 * hl ** 2 - (2 * np.log(2) - 1) * co ** 2\n"
                "        gk = np.clip(gk, 0, None)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_gk = np.convolve(gk, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh((avg_gk - np.mean(avg_gk)) / np.maximum(np.std(avg_gk), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["high", "low", "open", "close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="GK波动率因子：Garman-Klass 波动率估计，利用OHLC四价信息。高GK波动率=做空。",
            ),
        ),
        # 7.5 PK波动率因子
        _factor(
            name="fut_pk_vol",
            description="PK波动率因子：Parkinson 波动率估计，仅用高低价。高PK波动率=做空。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        hl = np.log(high / np.maximum(low, 1e-10))\n"
                "        pk = hl ** 2 / (4 * np.log(2))\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        avg_pk = np.convolve(pk, np.ones(window) / window, mode='same')\n"
                "        sig = np.tanh((avg_pk - np.mean(avg_pk)) / np.maximum(np.std(avg_pk), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["high", "low", "close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="PK波动率因子：Parkinson 波动率估计，仅用高低价。高PK波动率=做空。",
            ),
        ),
        # 7.6 YZ波动率因子
        _factor(
            name="fut_yz_vol",
            description="YZ波动率因子：Yang-Zhang 波动率估计，综合隔夜和日内波动。最全面的OHLC波动率估计。",
            market="futures",
            code=_make_code(
                "        high = data['high'].values if hasattr(data, 'high') else data['high']\n"
                "        low = data['low'].values if hasattr(data, 'low') else data['low']\n"
                "        open = data['open'].values if hasattr(data, 'open') else data['open']\n"
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        if n < 5:\n"
                "            return np.zeros(n)\n"
                "        co = np.log(close / np.maximum(open, 1e-10))\n"
                "        oc = np.log(open / np.maximum(np.roll(close, 1), 1e-10))\n"
                "        oc[0] = 0\n"
                "        ho = np.log(high / np.maximum(open, 1e-10))\n"
                "        hc = np.log(high / np.maximum(close, 1e-10))\n"
                "        lo = np.log(low / np.maximum(open, 1e-10))\n"
                "        lc = np.log(low / np.maximum(close, 1e-10))\n"
                "        rs = ho * hc + lo * lc\n"
                "        rs = np.clip(rs, 0, None)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window:\n"
                "            return np.zeros(n)\n"
                "        yz = np.zeros(n)\n"
                "        for i in range(1, n):\n"
                "            start = max(0, i - window + 1)\n"
                "            seg_o = oc[start:i+1]\n"
                "            seg_c = co[start:i+1]\n"
                "            seg_rs = rs[start:i+1]\n"
                "            yz[i] = np.var(seg_o) + 0.5 * np.var(seg_c) + 0.5 * np.mean(seg_rs)\n"
                "        sig = np.tanh((yz - np.mean(yz)) / np.maximum(np.std(yz), 1e-10))\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["high", "low", "open", "close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="YZ波动率因子：Yang-Zhang 波动率估计，综合隔夜和日内波动。最全面的OHLC波动率估计。",
            ),
        ),
        # 7.7 波动率因子
        _factor(
            name="fut_volatility_directional",
            description="波动率因子：滚动波动率的变化方向。波动率上升=风险加大=做空，波动率下降=风险消化=做多。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 3:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        vol = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            vol[i] = np.std(ret[i - window : i])\n"
                "        # 波动率变化方向\n"
                "        vol_change = np.diff(vol)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window+1:] = -np.tanh(vol_change[window-1:] * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="波动率因子：滚动波动率的变化方向。波动率上升=风险加大=做空，波动率下降=风险消化=做多。",
            ),
        ),
    ]


# ─── 主函数 ────────────────────────────────────────────────


def main():
    factors = []
    factors.extend(_build_momentum_factors())
    factors.extend(_build_volume_price_factors())
    factors.extend(_build_value_factors())
    factors.extend(_build_position_factors())
    factors.extend(_build_inventory_factors())
    factors.extend(_build_volatility_factors())

    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seeds", "futures")
    os.makedirs(output_dir, exist_ok=True)

    # 注册自定义 YAML representer，使 code 字段使用 | 字面块格式
    yaml.add_representer(LiteralBlock, _literal_block_representer)

    # 将每个因子的 code 字段包装为 LiteralBlock，强制 | 风格输出
    for f in factors:
        f["code"] = LiteralBlock(f["code"])

    # 生成天软因子 YAML
    tinysoft_data = _family("tinysoft", factors)
    output_path = os.path.join(output_dir, "tinysoft.yaml")
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(tinysoft_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"✅ 已生成天软因子种子文件: {output_path}")
    print(f"   共 {len(factors)} 个因子")
    for f in factors:
        print(f"   - {f['name']}: {f['description'][:50]}...")

    # 分类统计
    category_counts = {
        "动量因子": len(_build_momentum_factors()),
        "量价因子": len(_build_volume_price_factors()),
        "价值因子": len(_build_value_factors()),
        "持仓因子": len(_build_position_factors()),
        "库存因子": len(_build_inventory_factors()),
        "波动率因子": len(_build_volatility_factors()),
    }
    print("\n📊 分类统计:")
    for cat, cnt in category_counts.items():
        print(f"   {cat}: {cnt} 个")
    print(f"   {'合计':>12}: {len(factors)} 个")


if __name__ == "__main__":
    main()
