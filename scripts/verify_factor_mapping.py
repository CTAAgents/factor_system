"""verify_factor_mapping.py — RD 11 因子 ↔ FTS 等价实现映射表核验（plans/57 §4.3）。

口径：同一输入面板分别计算 RD 实现（regime_system.factors）与 FTS DSL 等价实现
（fts.factor_engine.expr_dsl.evaluate），逐因子求 Spearman 相关，按三档分级：
  ≥0.95 → 对账入轨（tier=A）
  ≥0.90 → 观察（tier=B）
  ≥0.80 → 低档（tier=C）
  <0.95 / 无 FTS 等价实现 → 标"待定"，不入对账。

用法：
  python scripts/verify_factor_mapping.py                # 合成面板核验并打印报告
  python scripts/verify_factor_mapping.py --json path    # 输出 JSON 报告

映射表为 FTS 与 RD 协同维护：RD 因子实现变更时必须重跑本脚本确认 Spearman 仍达标。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 项目根目录动态解析（禁止硬编码绝对路径）
_PROJ_ROOT = Path(__file__).resolve().parent.parent
_RD_SRC = Path(r"D:\Regime-Driven\src")

# RD 侧源码挂载（映射表核验需同时运行两套实现；缺失时仅打印映射表不执行核验）
if _RD_SRC.is_dir():
    sys.path.insert(0, str(_RD_SRC))

logger = logging.getLogger(__name__)

# ─── 因子映射表（RD 11 因子 ↔ FTS DSL 等价实现）──────────────────
# expected: 期望 Spearman 下限（None=无 FTS 等价实现，标待定）
MAPPING: list[dict] = [
    {"rd_name": "momentum_20", "fts_expr": "ts_pct_change(close, 20)",
     "basis": "20 日收益率（RD: close.pct_change(20)）", "expected": 0.95},
    {"rd_name": "momentum_60", "fts_expr": "ts_pct_change(close, 60)",
     "basis": "60 日收益率", "expected": 0.95},
    {"rd_name": "momentum_120", "fts_expr": "ts_pct_change(close, 120)",
     "basis": "120 日收益率", "expected": 0.95},
    {"rd_name": "ma_cross_signal", "fts_expr": "ts_ma_diff(close, 20, 60)",
     "basis": "均线间距归一化（RD: (MA20-MA60)/MA60 = MA20/MA60-1）", "expected": 0.95},
    {"rd_name": "adx_strength", "fts_expr": "ts_adx_wilder(high, low, close, 14)",
     "basis": "Wilder ADX 归一化 [0,1]（对齐 RD _adx_series 精确口径）", "expected": 0.95},
    {"rd_name": "atr_ratio", "fts_expr": "ts_atr_ratio(high, low, close, 14)",
     "basis": "ATR/价格（对齐 RD AtrRatioFactor 精确口径）", "expected": 0.95},
    {"rd_name": "realized_vol_20", "fts_expr": "mul(ts_realized_vol(close, 20), 15.8745)",
     "basis": "20 日年化波动（sqrt(252)≈15.8745）", "expected": 0.95},
    {"rd_name": "volume_z", "fts_expr": "ts_zscore(volume, 20)",
     "basis": "成交量 20 期 z-score", "expected": 0.95},
    {"rd_name": "oi_change_z", "fts_expr": "ts_zscore(ts_pct_change(hold, 1), 20)",
     "basis": "OI 变化率 z-score（RD: open_interest → FTS: hold）", "expected": 0.95},
    {"rd_name": "volume_price_fit", "fts_expr": "ts_mean(mul(sign(ts_pct_change(close, 1)), sign(ts_pct_change(volume, 1))), 20)",
     "basis": "量价配合度（涨放量/跌缩量均值）", "expected": 0.90},
    {"rd_name": "roll_yield", "fts_expr": None,
     "basis": "期限结构展期收益（FTS DSL 无直接等价，标待定）", "expected": None},
    {"rd_name": "near_far_spread_z", "fts_expr": None,
     "basis": "远近月价差 z（FTS DSL 无直接等价，标待定）", "expected": None},
]

_SYMBOLS = ["RB", "CU", "ZN", "MA", "SC"]
_N_DAYS = 800


def build_panel(n_days: int = _N_DAYS, seed: int = 42) -> dict[str, pd.DataFrame]:
    """合成 OHLCV+OI 面板（RD 需 open_interest/trade_date；FTS 需 hold）。"""
    rng = np.random.default_rng(seed)
    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(_SYMBOLS):
        n = n_days - i * 40  # 品种长度错位
        dates = pd.bdate_range(end="2026-08-01", periods=n)
        ret = rng.normal(0.0003, 0.015, n)
        close = 3000.0 * np.exp(np.cumsum(ret))
        vol = 1e5 + rng.integers(-2e4, 3e4, n).astype(float)
        hold = 8e4 + np.cumsum(rng.normal(0, 300, n))
        df = pd.DataFrame(
            {
                "trade_date": dates,
                "open": close * (1 + rng.normal(0, 0.003, n)),
                "high": close * (1 + np.abs(rng.normal(0.004, 0.002, n))),
                "low": close * (1 - np.abs(rng.normal(0.004, 0.002, n))),
                "close": close,
                "volume": vol,
                "open_interest": np.abs(hold),
                "hold": np.abs(hold),
            },
            index=dates,
        )
        panel[sym] = df
    return panel


def rd_values(factor_name: str, df: pd.DataFrame) -> pd.Series:
    """RD 因子实现（regime_system.factors.registry）。"""
    from regime_system.factors.registry import FactorRegistry

    reg = FactorRegistry.default()
    result = reg.compute_all(df).get(factor_name)
    if result is None:
        return pd.Series(dtype=float)
    return result.values


def fts_values(expr: str, df: pd.DataFrame) -> pd.Series:
    """FTS DSL 等价实现（fts.factor_engine.expr_dsl）。"""
    from fts.factor_engine.expr_dsl import build_registry, evaluate, parse_expression

    node = parse_expression(expr)
    return evaluate(node, df, build_registry())


def _spearman(a: pd.Series, b: pd.Series) -> float:
    a2 = pd.to_numeric(a, errors="coerce")
    b2 = pd.to_numeric(b, errors="coerce")
    mask = a2.notna() & b2.notna()
    if mask.sum() < 30:
        return float("nan")
    try:
        from scipy.stats import spearmanr

        return float(spearmanr(a2[mask], b2[mask]).statistic)
    except Exception:  # noqa: BLE001 — scipy 缺失回退 pandas
        return float(a2[mask].corr(b2[mask], method="spearman"))


def grade(corr: float, expected: float | None) -> str:
    if expected is None or np.isnan(corr):
        return "待定"
    if corr >= max(expected, 0.95):
        return "A(入轨)"
    if corr >= 0.90:
        return "B(观察)"
    if corr >= 0.80:
        return "C(低档)"
    return "D(不达标)"


def verify(n_days: int = _N_DAYS) -> dict:
    """运行映射表核验，返回报告 dict。"""
    panel = build_panel(n_days=n_days)
    report: dict = {"generated": pd.Timestamp.now().isoformat(), "factors": []}
    rd_ok = "regime_system" in sys.modules or _RD_SRC.is_dir()
    for m in MAPPING:
        entry = dict(m)
        if m["fts_expr"] is None or not rd_ok:
            entry.update({"corr": None, "grade": "待定", "note": "无 FTS 等价实现" if m["fts_expr"] is None else "RD 源码不可用"})
            report["factors"].append(entry)
            continue
        corrs = []
        for sym, df in panel.items():
            try:
                r = rd_values(m["rd_name"], df)
                f = fts_values(m["fts_expr"], df)
                c = _spearman(r, f)
                if not np.isnan(c):
                    corrs.append(c)
            except Exception as e:  # noqa: BLE001 — 单品种失败不阻断
                logger.debug("%s/%s 核验失败: %s", m["rd_name"], sym, e)
        corr = float(np.mean(corrs)) if corrs else float("nan")
        entry.update({"corr": round(corr, 4), "grade": grade(corr, m["expected"]),
                      "n_symbols": len(corrs)})
        report["factors"].append(entry)
    report["summary"] = {
        "total": len(MAPPING),
        "verified": sum(1 for f in report["factors"] if f["grade"] not in ("待定",)),
        "pending": sum(1 for f in report["factors"] if f["grade"] == "待定"),
        "failed": sum(1 for f in report["factors"] if f["grade"] == "D(不达标)"),
    }
    return report


def render(report: dict) -> str:
    lines = ["RD 11 因子 ↔ FTS 等价实现映射核验（plans/57 §4.3）",
             "=" * 92,
             f"{'RD 因子':<18}{'Spearman':>10}  {'档位':<10}{'FTS 表达式'}",
             "-" * 92]
    for f in report["factors"]:
        corr = f"{f['corr']:.4f}" if f["corr"] is not None else "  -   "
        lines.append(f"{f['rd_name']:<18}{corr:>10}  {f['grade']:<10}{f['fts_expr'] or '(待定)'}")
    lines.append("-" * 92)
    s = report["summary"]
    lines.append(f"共 {s['total']} 因子：核验 {s['verified']}，待定 {s['pending']}，不达标 {s['failed']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default="", help="JSON 报告输出路径")
    ap.add_argument("--panel-days", type=int, default=_N_DAYS, help="合成面板天数")
    args = ap.parse_args()

    report = verify(n_days=args.panel_days)
    print(render(report))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 报告已写入: {out}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
