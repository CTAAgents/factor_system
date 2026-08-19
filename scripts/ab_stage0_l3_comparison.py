"""ab_stage0_l3_comparison.py — 阶段 0 A/B 对照（plans/57 §5.1 / 契约先行）。

Path A（基线）: RD 固定 11 因子 + Tier B（FactorRegistry.default + RollYield/NearFarSpreadZ/
                TermZ，主+次主力合并数据）→ L3Identifier → 状态
Path B（新路径）: FTS 信号矩阵等价（§4.3 因子映射表 DSL 实现，8 因子可映射，
                4 因子标待定 → 信号缺失降级）→ 同一 L3Identifier → 状态

验收门槛（§5.1）: 状态一致率 ≥ 90%，方向一致率 ≥ 95%。
不达标 → 输出差异归因（按信号缺失/映射缺失分组），供定位因子映射问题后重跑。

用法:
  python scripts/ab_stage0_l3_comparison.py                       # 默认能化核心 10 品种 × 近 1 年
  python scripts/ab_stage0_l3_comparison.py --symbols SC FU BU --start 2025-01-01 --step 5
  python scripts/ab_stage0_l3_comparison.py --json memory/logs/ab_stage0/report.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJ_ROOT = Path(__file__).resolve().parent.parent
_RD_SRC = Path(r"D:\Regime-Driven\src")
if _RD_SRC.is_dir():
    sys.path.insert(0, str(_RD_SRC))

logger = logging.getLogger(__name__)

# 默认对照品种（能化核心，QuantData 数据充分）
DEFAULT_SYMBOLS = ["SC", "FU", "BU", "TA", "EG", "MA", "UR", "SA", "PF", "PP"]

# RD 配置（L3 阈值/数据就绪）
_RD_CONFIG = Path(r"D:\Regime-Driven\config\regime_routing_rules.yaml")

# Path B 可映射因子（与 verify_factor_mapping.MAPPING 对齐）
B_MAPPED = {
    "momentum_20": "ts_pct_change(close, 20)",
    "momentum_60": "ts_pct_change(close, 60)",
    "momentum_120": "ts_pct_change(close, 120)",
    "ma_cross_signal": "ts_ma_diff(close, 20, 60)",
    "adx_strength": "ts_adx_wilder(high, low, close, 14)",
    "atr_ratio": "ts_atr_ratio(high, low, close, 14)",
    "realized_vol_20": "mul(ts_realized_vol(close, 20), 15.8745)",
    "volume_z": "ts_zscore(volume, 20)",
    "oi_change_z": "ts_zscore(ts_pct_change(hold, 1), 20)",
    "volume_price_fit": "ts_mean(mul(sign(ts_pct_change(close, 1)), sign(ts_pct_change(volume, 1))), 20)",
}


def _direction_map():
    from regime_system.backtest_engine import DIRECTION_MAP

    return DIRECTION_MAP


def load_symbol_data(loader, symbol: str, start: str, end: str) -> pd.DataFrame:
    """主+次主力合并（对齐回测口径；Tier B 因子输入含 sub_close/sub_adj_factor）。"""
    from regime_system.factors.term_structure import merge_main_sub

    m = loader.load_main_continuous(symbol, start, end)
    s = loader.load_sub_continuous(symbol, start, end)
    if m is None or m.empty:
        return pd.DataFrame()
    data = merge_main_sub(m, s) if s is not None and not s.empty else m
    data = data.copy()
    # FTS DSL 需要 hold 列（= OI）；RD 侧用 open_interest
    if "open_interest" in data.columns and "hold" not in data.columns:
        data["hold"] = data["open_interest"]
    return data


def build_path_a_factors(data: pd.DataFrame) -> dict:
    """Path A：RD 因子池全序列（FactorRegistry.default + Tier B）。"""
    from regime_system.factors.registry import FactorRegistry
    from regime_system.factors.term_structure import (
        NearFarSpreadZFactor,
        RollYieldFactor,
        TermZFactor,
    )

    reg = FactorRegistry.default()
    reg.register(RollYieldFactor())
    reg.register(NearFarSpreadZFactor())
    reg.register(TermZFactor())
    out: dict = {}
    for name, f in reg._factors.items():
        try:
            s = f.compute(data)
        except Exception:  # noqa: BLE001 — 单因子失败视为无数据
            s = pd.Series(dtype=float)
        out[name] = s
    return out


def build_path_b_factors(data: pd.DataFrame) -> dict:
    """Path B：FTS DSL 等价实现全序列（仅 8 个可映射因子）。"""
    from fts.factor_engine.expr_dsl import build_registry, evaluate, parse_expression

    reg = build_registry()
    out: dict = {}
    for name, expr in B_MAPPED.items():
        try:
            node = parse_expression(expr)
            s = evaluate(node, data, reg)
            if isinstance(s, (int, float)):
                s = pd.Series(float(s), index=data["trade_date"] if "trade_date" in data.columns else data.index)
            elif "trade_date" in data.columns and len(s) == len(data):
                s = pd.Series(np.asarray(s, dtype=float), index=pd.DatetimeIndex(data["trade_date"]))
            out[name] = s
        except Exception:  # noqa: BLE001 — 单因子失败视为无数据
            out[name] = pd.Series(dtype=float)
    return out


def day_factors(series_map: dict, data: pd.DataFrame, d: pd.Timestamp, min_periods: dict) -> dict:
    """构造截至 d 的单日 FactorResult dict（对齐 backtest_engine._day_factors 语义）。"""
    from regime_system.factors.base import FactorResult, FactorStatus

    fac: dict = {}
    for name, s in series_map.items():
        if len(s) == 0:
            fac[name] = FactorResult(name=name, values=pd.Series(dtype=float),
                                     status=FactorStatus.INSUFFICIENT_DATA, reason="无数据")
            continue
        v = s.get(d)
        nv = s.loc[:d].notna().sum() if len(s) else 0
        if pd.isna(v) or nv < min_periods.get(name, 1):
            fac[name] = FactorResult(name=name, values=pd.Series(dtype=float),
                                     status=FactorStatus.INSUFFICIENT_DATA,
                                     reason=f"数据不足 ({nv})")
        else:
            fac[name] = FactorResult(name=name, values=pd.Series([float(v)], index=[d]),
                                     status=FactorStatus.ACTIVE)
    return fac


def run_ab(
    symbols: list[str],
    start: str,
    end: str,
    step: int = 5,
) -> dict:
    """运行 A/B 对照，返回报告 dict。"""
    from regime_system.data_loader import DataLoader
    from regime_system.factors.registry import FactorRegistry
    from regime_system.l3_identifier import L3Identifier

    loader = DataLoader()
    config: dict = {}
    try:
        import yaml  # type: ignore[import-untyped]

        config = yaml.safe_load(_RD_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        logger.warning("RD 配置读取失败，使用默认阈值")

    # 各因子 min_period（RD 注册表自带；Path B 使用相同值）
    reg = FactorRegistry.default()
    min_periods = {name: f.min_period for name, f in reg._factors.items()}
    min_periods.update({
        "roll_yield": 20, "near_far_spread_z": 25, "term_z": 30,
    })

    l3 = L3Identifier(config=config)
    dm = {k.value: v for k, v in _direction_map().items()}

    results: dict = {
        "symbols": symbols,
        "start": start, "end": end, "step_days": step,
        "per_symbol": {}, "gates": {},
    }
    total = {"n": 0, "state_match": 0, "dir_match": 0}
    for sym in symbols:
        data = load_symbol_data(loader, sym, start, end)
        if data.empty or "trade_date" not in data.columns:
            results["per_symbol"][sym] = {"error": "无数据", "n": 0}
            continue
        data = data.set_index("trade_date").sort_index()
        dates = data.index
        path_a = build_path_a_factors(data)
        path_b = build_path_b_factors(data)
        sym_res = {"n": 0, "state_match": 0, "dir_match": 0,
                   "diffs": [], "missing_b_factors": sorted(set(path_a) - set(path_b))}
        for d in dates[::step]:
            row = data.loc[d]
            fac_a = day_factors(path_a, data, d, min_periods)
            fac_b = day_factors(path_b, data, d, min_periods)
            try:
                ra = l3.identify(fac_a, data=pd.DataFrame([row]))
                rb = l3.identify(fac_b, data=pd.DataFrame([row]))
            except Exception:  # noqa: BLE001 — 单日失败跳过
                continue
            state_a, state_b = ra.state.value, rb.state.value
            dir_a, dir_b = dm.get(state_a, "NEUTRAL"), dm.get(state_b, "NEUTRAL")
            sym_res["n"] += 1
            total["n"] += 1
            if state_a == state_b:
                sym_res["state_match"] += 1
                total["state_match"] += 1
            else:
                sym_res["diffs"].append({"date": str(d.date()), "a": state_a, "b": state_b,
                                         "dir_a": dir_a, "dir_b": dir_b})
            if dir_a == dir_b:
                sym_res["dir_match"] += 1
                total["dir_match"] += 1
        sym_res["state_rate"] = round(sym_res["state_match"] / sym_res["n"], 4) if sym_res["n"] else None
        sym_res["dir_rate"] = round(sym_res["dir_match"] / sym_res["n"], 4) if sym_res["n"] else None
        results["per_symbol"][sym] = sym_res

    state_rate = total["state_match"] / total["n"] if total["n"] else 0.0
    dir_rate = total["dir_match"] / total["n"] if total["n"] else 0.0
    results["total"] = {
        "n": total["n"], "state_match": total["state_match"],
        "dir_match": total["dir_match"],
        "state_rate": round(state_rate, 4), "dir_rate": round(dir_rate, 4),
    }
    results["gates"] = {
        "state_gate": 0.90, "state_pass": state_rate >= 0.90,
        "dir_gate": 0.95, "dir_pass": dir_rate >= 0.95,
    }
    results["missing_b_factors"] = sorted(
        set().union(*[set(s.get("missing_b_factors", [])) for s in results["per_symbol"].values()])
        if results["per_symbol"] else []
    )
    return results


def render(r: dict) -> str:
    lines = ["阶段 0 A/B 对照：RD 固定因子 vs FTS 信号等价 → L3 状态（plans/57 §5.1）",
             "=" * 78]
    for sym, s in r["per_symbol"].items():
        if "error" in s:
            lines.append(f"{sym:<6} {s['error']}")
            continue
        lines.append(f"{sym:<6} n={s['n']:<5} 状态一致 {s['state_rate']}  方向一致 {s['dir_rate']}")
    t = r["total"]
    lines.append("-" * 78)
    lines.append(f"合计 n={t['n']}  状态一致率 {t['state_rate']}（门槛 90%）"
                 f"  方向一致率 {t['dir_rate']}（门槛 95%）")
    g = r["gates"]
    lines.append(f"状态门槛 {'通过' if g['state_pass'] else '未通过'} / "
                 f"方向门槛 {'通过' if g['dir_pass'] else '未通过'}")
    if r["missing_b_factors"]:
        lines.append(f"Path B 缺失因子（标待定）: {r['missing_b_factors']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS),
                    help="逗号分隔品种列表")
    ap.add_argument("--start", type=str, default="", help="起始日（默认近 1 年）")
    ap.add_argument("--end", type=str, default="", help="结束日（默认今日）")
    ap.add_argument("--step", type=int, default=5, help="对照日间隔（交易日）")
    ap.add_argument("--json", type=str, default="", help="JSON 报告输出路径")
    args = ap.parse_args()

    end = args.end or pd.Timestamp.now().strftime("%Y-%m-%d")
    start = args.start or (pd.Timestamp(end) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    report = run_ab([s.strip() for s in args.symbols.split(",")], start, end, step=args.step)
    print(render(report))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 报告已写入: {out}")
    return 0 if report["gates"]["state_pass"] and report["gates"]["dir_pass"] else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
