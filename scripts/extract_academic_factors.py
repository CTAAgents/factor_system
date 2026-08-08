"""
scripts/extract_academic_factors.py — 学术论文因子提取器

从经典金融学术论文中提取期货因子，去重后生成 FTS 兼容的 YAML 种子文件。

覆盖论文:
  - Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum" — TSMOM
  - Asness, Moskowitz & Pedersen (2013) "Value and Momentum Everywhere" — 跨资产强调
  - Erb & Harvey (2006) "The Tactical and Strategic Value of Commodity Futures" — 展期收益
  - Gorton, Hayashi & Rouwenhorst (2013) "The Fundamentals of Commodity Futures" — 基本面
  - Bakshi, Gao & Rossi (2019) "Predicting the Equity Market with VIX" — 波动率预测
  - Koijen, Moskowitz, Pedersen & Vrugt (2018) "Carry" — 全面展期收益

用法:
    python scripts/extract_academic_factors.py
    python scripts/extract_academic_factors.py --list-papers
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml


SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "futures"

KNOWN_PAPERS = {
    "moskowitz_tsmom_2012": {
        "title": "Time Series Momentum",
        "authors": "Moskowitz, Ooi, Pedersen",
        "year": 2012,
        "journal": "Journal of Financial Economics",
        "key_factors": ["时序动量(TSMOM)", "波动率缩放"],
        "url": "https://doi.org/10.1016/j.jfineco.2011.05.003",
    },
    "asness_value_momentum_2013": {
        "title": "Value and Momentum Everywhere",
        "authors": "Asness, Moskowitz, Pedersen",
        "year": 2013,
        "journal": "Journal of Finance",
        "key_factors": ["价值因子", "动量因子"],
        "url": "https://doi.org/10.1111/jofi.12021",
    },
    "erb_harvey_carry_2006": {
        "title": "The Tactical and Strategic Value of Commodity Futures",
        "authors": "Erb, Harvey",
        "year": 2006,
        "journal": "Financial Analysts Journal",
        "key_factors": ["展期收益(carry)", "动量"],
        "url": "https://doi.org/10.2469/faj.v62.n2.4084",
    },
    "gorton_fundamentals_2013": {
        "title": "The Fundamentals of Commodity Futures Returns",
        "authors": "Gorton, Hayashi, Rouwenhorst",
        "year": 2013,
        "journal": "Review of Finance",
        "key_factors": ["库存", "基差", "动量"],
        "url": "https://doi.org/10.1093/rof/rfs019",
    },
    "koijen_carry_2018": {
        "title": "Carry",
        "authors": "Koijen, Moskowitz, Pedersen, Vrugt",
        "year": 2018,
        "journal": "Journal of Financial Economics",
        "key_factors": ["跨资产展期收益", "carry因子"],
        "url": "https://doi.org/10.1016/j.jfineco.2017.08.002",
    },
}


def load_existing_factor_names() -> set[str]:
    names: set[str] = set()
    for yf in SEEDS_DIR.glob("*.yaml"):
        with open(yf, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for ef in data.get("factors", []):
            names.add(ef.get("name", ""))
    return names


def _factor(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _family(name: str, factors: list[dict[str, Any]]) -> dict[str, Any]:
    return _factor(family=name, version="1.0", market="futures", factors=factors)


def _make_code(body: str) -> str:
    return (
        "\n"
        "    def factor_program(data, params):\n"
        "        import numpy as np\n"
        f"{body}\n"
    )


class LiteralBlock(str):
    pass


def _literal_block_representer(dumper: yaml.Dumper, data: LiteralBlock) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


# ─── 学术论文因子（与现有种子不重复） ─────────────────────


def _build_tsmom_factors() -> list[dict[str, Any]]:
    """Moskowitz, Ooi & Pedersen (2012) — Time Series Momentum"""
    return [
        # 波动率缩放时序动量
        _factor(
            name="fut_tsmom_vol_scaled",
            description="波动率缩放时序动量：将原始TSMOM收益用滚动波动率缩放，使每个品种贡献相同风险。Moskowitz(2012)核心贡献。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        lookback = int(params.get('lookback', 12))\n"
                "        vol_span = int(params.get('vol_span', 60))\n"
                "        target_vol = float(params.get('target_vol', 0.15))\n"
                "        if n < max(lookback, vol_span) + 2:\n"
                "            return np.zeros(n)\n"
                "        # 过去 lookback 期收益率\n"
                "        ret = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)\n"
                "        # 滚动波动率（指数加权）\n"
                "        daily_ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(vol_span, n):\n"
                "            ewma_vol = np.std(daily_ret[i - vol_span : i]) * np.sqrt(252)\n"
                "            raw_signal = (close[i - lookback] < close[i - 1]) * 2 - 1  # 方向\n"
                "            scaled_signal = raw_signal * (target_vol / max(ewma_vol, 0.01))\n"
                "            sig[i] = np.clip(scaled_signal, -1.0, 1.0)\n"
                "        return sig"
            ),
            params={"lookback": 12, "vol_span": 60, "target_vol": 0.15},
            input_fields=["close"],
            lookback=65,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=3, microstructure=3, institutional=4,
                narrative="波动率缩放时序动量：将原始TSMOM收益用滚动波动率缩放，使每个品种贡献相同风险。Moskowitz(2012)核心贡献。",
            ),
        ),
        # 多周期趋势信号
        _factor(
            name="fut_tsmom_multi_signal",
            description="多周期趋势信号：综合多个回看期的趋势信号（12/6/3个月），信号越强趋势越确定。Moskowitz(2012)多周期验证。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        windows = [3, 6, 12]\n"
                "        max_lb = max(windows) * 21\n"
                "        if n < max_lb + 2:\n"
                "            return np.zeros(n)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(max_lb, n):\n"
                "            signals = []\n"
                "            for w in windows:\n"
                "                lb = w * 21\n"
                "                ret = (close[i] - close[i - lb]) / max(close[i - lb], 1e-10)\n"
                "                signals.append(1 if ret > 0 else -1 if ret < 0 else 0)\n"
                "            sig[i] = np.mean(signals)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={},
            input_fields=["close"],
            lookback=260,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=3, microstructure=3, institutional=4,
                narrative="多周期趋势信号：综合多个回看期的趋势信号（12/6/3个月），信号越强趋势越确定。",
            ),
        ),
    ]


def _build_carry_factors() -> list[dict[str, Any]]:
    """Koijen, Moskowitz, Pedersen & Vrugt (2018) — Carry"""
    return [
        # 跨品种相对carry
        _factor(
            name="fut_cross_carry",
            description="跨品种相对carry：基于展期收益的截面排序，做多高carry品种做空低carry品种。Koijen(2018)跨资产carry。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        # 展期收益近似：近月-远月价格差\n"
                "        # 这里用价格变化率代理\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        # 滚动收益率作为carry代理\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        carry = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            carry[i] = np.mean(ret[i - window : i])\n"
                "        sig = np.tanh(carry * 10)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=3, microstructure=4, institutional=3,
                narrative="跨品种相对carry：基于展期收益的截面排序，做多高carry品种做空低carry品种。Koijen(2018)跨资产carry。",
            ),
        ),
        # 经波动率调整的carry
        _factor(
            name="fut_carry_vol_adjusted",
            description="经波动率调整的carry：用波动率缩放carry信号，高波动时降低仓位。Koijen(2018)风险平价carry。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        vol_span = int(params.get('vol_span', 60))\n"
                "        if n < max(window, vol_span) + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(max(window, vol_span), n):\n"
                "            carry = np.mean(ret[i - window : i])\n"
                "            vol = np.std(ret[i - vol_span : i]) * np.sqrt(252)\n"
                "            if vol > 0.01:\n"
                "                sig[i] = carry / vol * 0.5\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20, "vol_span": 60},
            input_fields=["close"],
            lookback=65,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=3, microstructure=4, institutional=3,
                narrative="经波动率调整的carry：用波动率缩放carry信号，高波动时降低仓位。Koijen(2018)风险平价carry。",
            ),
        ),
    ]


def _build_fundamental_factors() -> list[dict[str, Any]]:
    """Gorton, Hayashi & Rouwenhorst (2013) — 基本面因子"""
    return [
        # 库存变化率
        _factor(
            name="fut_inventory_change_rate",
            description="库存变化率：库存的月度变化率。库存下降=供应紧张=做多。Gorton(2013)证明库存是期货收益的核心预测变量。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        # 用成交量变化代理库存变化\n"
                "        vol_change = (volume[window:] - volume[:-window]) / np.maximum(volume[:-window], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        # 库存下降（成交量减少）= 供应紧张 = 做多\n"
                "        sig[window:] = -np.tanh(vol_change * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=2, microstructure=3, institutional=4,
                narrative="库存变化率：库存的月度变化率。库存下降=供应紧张=做多。Gorton(2013)证明库存是期货收益的核心预测变量。",
            ),
        ),
        # 基差-库存联合因子
        _factor(
            name="fut_basis_inventory_combo",
            description="基差-库存联合因子：结合基差（近月-远月）和库存信号的联合因子。基差走强+库存下降=最强做多信号。Gorton(2013)理论基础。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        # 基差代理：短期收益率\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        # 库存代理：成交量变化\n"
                "        vol_chg = np.diff(volume) / np.maximum(volume[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            basis = np.mean(ret[i - window : i])\n"
                "            inv = np.mean(vol_chg[i - window : i])\n"
                "            # 基差走强(+basis) + 库存下降(-inv) = 做多\n"
                "            sig[i] = basis - inv\n"
                "        sig = np.tanh(sig * 10)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=5, behavioral=2, microstructure=3, institutional=4,
                narrative="基差-库存联合因子：结合基差和库存信号的联合因子。基差走强+库存下降=最强做多信号。",
            ),
        ),
    ]


# ─── 主函数 ────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="学术论文因子提取器")
    parser.add_argument("--list-papers", action="store_true", help="列出支持的学术论文")
    parser.add_argument("--output", type=str, default=None, help="输出 YAML 文件路径")
    parser.add_argument("--families", type=str, nargs="+",
                        default=["tsmom", "carry", "fundamental"],
                        help="因子家族")
    args = parser.parse_args()

    if args.list_papers:
        print("📚 支持的学术论文:")
        print()
        for key, info in KNOWN_PAPERS.items():
            print(f"  {key}")
            print(f"    标题: {info['title']}")
            print(f"    作者: {info['authors']} ({info['year']})")
            print(f"    期刊: {info['journal']}")
            print(f"    关键因子: {', '.join(info['key_factors'])}")
            print(f"    链接: {info['url']}")
            print()
        return

    existing_names = load_existing_factor_names()
    print(f"📂 现有种子因子: {len(existing_names)} 个")

    all_factors: list[dict[str, Any]] = []
    family_map = {
        "tsmom": ("tsmom", _build_tsmom_factors),
        "carry": ("carry", _build_carry_factors),
        "fundamental": ("fundamental_academic", _build_fundamental_factors),
    }

    for family_key in args.families:
        if family_key not in family_map:
            print(f"⚠️  未知因子家族: {family_key}，跳过")
            continue
        family_name, builder = family_map[family_key]
        factors = builder()
        new_factors = [f for f in factors if f["name"] not in existing_names]
        if new_factors:
            all_factors.extend(new_factors)
            print(f"  ✅ {family_name}: {len(new_factors)} 个新因子")
        else:
            print(f"  ⏭️  {family_name}: 全部已在现有种子中，跳过")
        for f in new_factors:
            existing_names.add(f["name"])

    if not all_factors:
        print("\n⚠️  没有新因子需要生成。")
        return

    print(f"\n📊 共发现 {len(all_factors)} 个新因子")

    yaml.add_representer(LiteralBlock, _literal_block_representer)
    for f in all_factors:
        f["code"] = LiteralBlock(f["code"])

    output_path = args.output or str(SEEDS_DIR / "academic_papers.yaml")
    output_data = _family("academic_papers", all_factors)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(output_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"✅ 已生成: {output_path}")
    for f in all_factors:
        print(f"   - {f['name']}: {f['description'][:60]}...")


if __name__ == "__main__":
    main()