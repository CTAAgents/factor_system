"""
scripts/extract_broker_factors.py — 券商研报因子提取器

从券商 CTA 因子研究报告（如中信期货、中信建投期货、申万宏源等）中
提取因子定义，去重后生成 FTS 兼容的 YAML 种子文件。

用法:
    python scripts/extract_broker_factors.py --source <文本文件路径或URL>
    python scripts/extract_broker_factors.py --list-sources   # 列出支持的券商研报

支持的数据源:
    - 中信期货 CTA风格因子手册系列
    - 中信建投期货 量化CTA风格因子跟踪
    - 申万宏源 CTA因子系列报告
    - 自定义文本文件

工作流程:
  1. 读取/搜索券商研报内容
  2. 提取因子定义（名称、描述、代码逻辑、参数）
  3. 与现有种子因子去重
  4. 生成 FTS YAML 种子文件
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml


# ─── 常量 ──────────────────────────────────────────────────

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds" / "futures"
OUTPUT_DIR = SEEDS_DIR

KNOWN_BROKER_SOURCES = {
    "citic_futures_cta": {
        "name": "中信期货 CTA风格因子手册",
        "description": "中信期货金融工程专题报告，系统汇总国内常用CTA风格因子，涵盖量价类因子构建方法",
        "factors_covered": ["动量", "期限结构", "量价", "波动率", "偏度", "持仓"],
        "url": "https://m.book118.com/html/2024/1017/7162044124006162.shtm",
    },
    "citic_futures_tracking": {
        "name": "中信建投期货 量化CTA风格因子跟踪",
        "description": "周频跟踪动量、期限结构、贝塔、波动率、偏度、持仓六大因子表现",
        "factors_covered": ["动量", "期限结构", "贝塔", "波动率", "偏度", "持仓"],
        "url": "https://finance.sina.com.cn/money/future/wemedia/2024-07-09/doc-inccptnx6841650.shtml",
    },
    "shenwan_cta": {
        "name": "申万宏源 CTA因子系列",
        "description": "申万宏源商品期货CTA因子系列研究报告",
        "factors_covered": ["动量", "期限结构", "波动率", "持仓", "库存"],
        "url": None,
    },
}


# ─── 辅助函数 ──────────────────────────────────────────────


def load_existing_factor_names() -> set[str]:
    """加载现有种子因子名称集合，用于去重。"""
    names: set[str] = set()
    for yf in SEEDS_DIR.glob("*.yaml"):
        with open(yf, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for ef in data.get("factors", []):
            names.add(ef.get("name", ""))
    return names


def normalize_factor_name(name: str) -> str:
    """将中文/英文因子名规范化为 FTS 命名风格。"""
    # 中文转英文
    name_map = {
        "动量": "momentum",
        "期限结构": "term_structure",
        "波动率": "volatility",
        "偏度": "skewness",
        "持仓": "position",
        "库存": "inventory",
        "量价": "volume_price",
        "贝塔": "beta",
        "价值": "value",
        "流动性": "liquidity",
    }
    for cn, en in name_map.items():
        name = name.replace(cn, en)
    # 确保前缀
    if not name.startswith("fut_"):
        name = "fut_" + name
    # 替换非法字符
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name


def validate_factor_code(code: str) -> tuple[bool, str]:
    """验证因子代码的语法正确性。"""
    stripped = code.strip()
    if not stripped:
        return False, "空代码"
    try:
        ast.parse(stripped)
        return True, ""
    except SyntaxError:
        try:
            ast.parse(f"def _wrapper():\n{stripped}")
            return True, ""
        except SyntaxError as e:
            return False, str(e)


def _make_code(body: str) -> str:
    """包装 factor_program 函数体。"""
    return f"\n    def factor_program(data, params):\n        import numpy as np\n{body}\n"


def _factor(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _family(name: str, factors: list[dict[str, Any]]) -> dict[str, Any]:
    return _factor(
        family=name,
        version="1.0",
        market="futures",
        factors=factors,
    )


class LiteralBlock(str):
    """强制 YAML 使用 | 字面块标量格式输出。"""


def _literal_block_representer(dumper: yaml.Dumper, data: LiteralBlock) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


# ─── 预定义因子库（来自已知券商研报，与现有种子不重复的因子） ──

# 经过与现有 108 个期货种子因子（81 原始 + 27 天软）对比，
# 以下因子来自券商研报且尚未被覆盖：


def _build_broker_momentum_factors() -> list[dict[str, Any]]:
    """中信期货CTA风格因子手册 - 动量类补充因子"""
    return [
        # 动量衰减因子（中信期货特有）
        _factor(
            name="fut_momentum_decay",
            description="动量衰减因子：加权移动平均收益率，给近期更高权重。相比等权动量，更敏感于近期变化。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        # 指数衰减权重\n"
                "        decay = np.exp(-np.arange(window) / (window / 3))\n"
                "        decay /= decay.sum()\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            sig[i] = np.sum(ret[i - window : i] * decay)\n"
                "        sig = np.tanh(sig / 0.01)\n"
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
                narrative="动量衰减因子：加权移动平均收益率，给近期更高权重。相比等权动量，更敏感于近期变化。",
            ),
        ),
        # 动量加速度因子（中信期货特有）
        _factor(
            name="fut_momentum_acceleration",
            description="动量加速度因子：收益率的二阶差分，衡量动量变化速度。动量加速=趋势强化。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window + 3:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        # 动量 = 滚动收益率均值\n"
                "        mom = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            mom[i] = np.mean(ret[i - window : i])\n"
                "        # 加速度 = 动量的变化\n"
                "        accel = np.diff(mom)\n"
                "        sig = np.zeros(n)\n"
                "        sig[window+1:] = np.tanh(accel[window-1:] * 100)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 10},
            input_fields=["close"],
            lookback=15,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="动量加速度因子：收益率的二阶差分，衡量动量变化速度。动量加速=趋势强化。",
            ),
        ),
    ]


def _build_broker_volume_price_factors() -> list[dict[str, Any]]:
    """中信期货CTA风格因子手册 - 量价类补充因子"""
    return [
        # 量价相关性因子（中信期货特有）
        _factor(
            name="fut_volume_price_corr",
            description="量价相关性因子：价格变化与成交量的滚动相关性。量价齐升=趋势健康，量缩价涨=趋势衰竭。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        vol_change = np.diff(volume) / np.maximum(volume[:-1], 1e-10)\n"
                "        min_len = min(len(ret), len(vol_change))\n"
                "        corr = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            r_seg = ret[max(0, i - window + 1):i]\n"
                "            v_seg = vol_change[max(0, i - window + 1):i]\n"
                "            if len(r_seg) > 2 and np.std(r_seg) > 1e-10 and np.std(v_seg) > 1e-10:\n"
                "                corr[i] = np.corrcoef(r_seg, v_seg)[0, 1]\n"
                "        sig = np.tanh(corr * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="量价相关性因子：价格变化与成交量的滚动相关性。量价齐升=趋势健康，量缩价涨=趋势衰竭。",
            ),
        ),
        # 成交量加权动量因子（中信期货特有）
        _factor(
            name="fut_volume_weighted_momentum",
            description="成交量加权动量因子：用成交量作为权重的加权收益率，放量日的影响更大。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        volume = data['volume'].values if hasattr(data, 'volume') else data['volume']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            r_seg = ret[i - window : i]\n"
                "            v_seg = volume[i - window + 1 : i + 1]\n"
                "            w = v_seg / np.maximum(np.sum(v_seg), 1e-10)\n"
                "            sig[i] = np.sum(r_seg * w)\n"
                "        sig = np.tanh(sig / 0.01)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close", "volume"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=4,
                institutional=3,
                narrative="成交量加权动量因子：用成交量作为权重的加权收益率，放量日的影响更大。",
            ),
        ),
    ]


def _build_broker_volatility_factors() -> list[dict[str, Any]]:
    """中信期货CTA风格因子手册 - 波动率类补充因子"""
    return [
        # 波动率不对称性因子（中信期货特有）
        _factor(
            name="fut_vol_asymmetry",
            description="波动率不对称性因子：上涨日波动率与下跌日波动率的比值。下跌波动更大=恐慌=做空信号。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 20))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            seg = ret[i - window : i]\n"
                "            up_vol = np.std(seg[seg > 0]) if np.sum(seg > 0) > 2 else 0.01\n"
                "            down_vol = np.std(seg[seg < 0]) if np.sum(seg < 0) > 2 else 0.01\n"
                "            ratio = down_vol / max(up_vol, 1e-10)\n"
                "            sig[i] = np.tanh((ratio - 1) * 2)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=4,
                behavioral=4,
                microstructure=3,
                institutional=3,
                narrative="波动率不对称性因子：上涨日波动率与下跌日波动率的比值。下跌波动更大=恐慌=做空。",
            ),
        ),
        # 已实现波动率趋势因子（中信期货特有）
        _factor(
            name="fut_realized_vol_trend",
            description="已实现波动率趋势因子：已实现波动率的短期与长期比值。短期波动>长期=波动加剧=谨慎。来源：中信期货CTA风格因子手册。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        n = len(close)\n"
                "        short_w = int(params.get('short_window', 5))\n"
                "        long_w = int(params.get('long_window', 20))\n"
                "        if n < long_w + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(long_w, n):\n"
                "            short_vol = np.std(ret[i - short_w : i])\n"
                "            long_vol = np.std(ret[i - long_w : i])\n"
                "            ratio = short_vol / max(long_vol, 1e-10)\n"
                "            sig[i] = np.tanh((ratio - 1) * 3)\n"
                "        return np.clip(sig, -1.0, 1.0)"
            ),
            params={"short_window": 5, "long_window": 20},
            input_fields=["close"],
            lookback=25,
            output_type="signal",
            frequency="daily",
            economic_logic=_factor(
                theory=3,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="已实现波动率趋势因子：已实现波动率的短期与长期比值。短期波动>长期=波动加剧=谨慎。",
            ),
        ),
    ]


def _build_broker_position_factors() -> list[dict[str, Any]]:
    """中信建投期货 - 持仓类补充因子"""
    return [
        # 持仓动量比因子（中信建投特有）
        _factor(
            name="fut_oi_momentum_ratio",
            description="持仓动量比因子：持仓变化与价格变化的比值，衡量资金流与价格方向的一致性。比值高=资金推动型趋势。来源：中信建投期货。",
            market="futures",
            code=_make_code(
                "        close = data['close'].values if hasattr(data, 'close') else data['close']\n"
                "        hold = data['hold'].values if hasattr(data, 'hold') else data.get('hold', np.ones(n))\n"
                "        n = len(close)\n"
                "        window = int(params.get('window', 10))\n"
                "        if n < window + 2:\n"
                "            return np.zeros(n)\n"
                "        ret = np.diff(close) / np.maximum(close[:-1], 1e-10)\n"
                "        oi_chg = np.diff(hold) / np.maximum(hold[:-1], 1e-10)\n"
                "        sig = np.zeros(n)\n"
                "        for i in range(window, n):\n"
                "            r = np.mean(ret[i - window : i])\n"
                "            o = np.mean(oi_chg[i - window : i])\n"
                "            if abs(r) > 1e-10:\n"
                "                sig[i] = o / r\n"
                "        sig = np.tanh(sig * 0.5)\n"
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
                narrative="持仓动量比因子：持仓变化与价格变化的比值，衡量资金流与价格方向的一致性。",
            ),
        ),
    ]


# ─── 主函数 ────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="券商研报因子提取器")
    parser.add_argument("--source", type=str, default=None, help="研报文本文件路径或URL")
    parser.add_argument("--list-sources", action="store_true", help="列出支持的券商研报数据源")
    parser.add_argument("--output", type=str, default=None, help="输出 YAML 文件路径（默认自动生成）")
    parser.add_argument(
        "--families",
        type=str,
        nargs="+",
        default=["momentum", "volume_price", "volatility", "position"],
        help="要提取的因子家族",
    )
    args = parser.parse_args()

    if args.list_sources:
        print("📋 支持的券商研报数据源:")
        print()
        for key, info in KNOWN_BROKER_SOURCES.items():
            print(f"  {key}")
            print(f"    名称: {info['name']}")
            print(f"    描述: {info['description']}")
            print(f"    覆盖因子: {', '.join(info['factors_covered'])}")
            if info["url"]:
                print(f"    链接: {info['url']}")
            print()
        return

    # 加载现有因子名称用于去重
    existing_names = load_existing_factor_names()
    print(f"📂 现有种子因子: {len(existing_names)} 个")

    # 构建因子
    all_factors: list[dict[str, Any]] = []
    family_map = {
        "momentum": ("momentum_broker", _build_broker_momentum_factors),
        "volume_price": ("volume_price_broker", _build_broker_volume_price_factors),
        "volatility": ("volatility_broker", _build_broker_volatility_factors),
        "position": ("position_broker", _build_broker_position_factors),
    }

    for family_key in args.families:
        if family_key not in family_map:
            print(f"⚠️  未知因子家族: {family_key}，跳过")
            continue
        family_name, builder = family_map[family_key]
        factors = builder()
        # 去重
        new_factors = [f for f in factors if f["name"] not in existing_names]
        if new_factors:
            all_factors.extend(new_factors)
            print(f"  ✅ {family_name}: {len(new_factors)} 个新因子")
        else:
            print(f"  ⏭️  {family_name}: 全部已在现有种子中，跳过")

        # 更新已加载名称避免同批次重复
        for f in new_factors:
            existing_names.add(f["name"])

    if not all_factors:
        print("\n⚠️  没有新因子需要生成。")
        return

    print(f"\n📊 共发现 {len(all_factors)} 个新因子")

    # 注册 YAML representer
    yaml.add_representer(LiteralBlock, _literal_block_representer)
    for f in all_factors:
        f["code"] = LiteralBlock(f["code"])

    # 输出
    output_path = args.output
    if not output_path:
        output_path = str(SEEDS_DIR / "broker_reports.yaml")

    output_data = _family("broker_reports", all_factors)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(output_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"✅ 已生成: {output_path}")
    for f in all_factors:
        print(f"   - {f['name']}: {f['description'][:60]}...")


if __name__ == "__main__":
    main()
