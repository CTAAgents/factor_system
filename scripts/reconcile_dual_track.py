"""reconcile_dual_track.py — 阶段 1 双轨对账（plans/57 §5.2）。

输入: 两条轨道（FTS 基准轨 A / RD 新轨 B）的逐日输出——因子权重向量、
组合仓位矩阵、组合收益序列——按信号版本指纹对齐后计算三级对账指标。

门槛（§5.2 表）:
  信号级: 因子权重向量余弦相似度（同因子集）≥ 0.85
  组合级: 方向一致率 ≥ 95% / 敞口差 ≤ 5% / 换手差 ≤ 20%
  绩效级: 滚动 60 日累计收益差 / 回撤差 ≤ 基准年化波动 20%

任一超门槛 → 暂停退役，按"信号层 → 合成层 → 校验层"定位（§5.2 不一致处理）。

用法:
  python scripts/reconcile_dual_track.py --weights-a a.json --weights-b b.json \
      --positions-a pos_a.csv --positions-b pos_b.csv --returns-a r_a.csv --returns-b r_b.csv
  python scripts/reconcile_dual_track.py --demo        # 合成数据演示对账机器
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 门槛（§5.2）
GATES = {
    "signal_cosine": 0.85,
    "direction_rate": 0.95,
    "exposure_diff": 0.05,
    "turnover_diff": 0.20,
}


# ─── 信号级：因子权重余弦相似度 ───────────────────────────


def cosine_similarity(
    weights_a: dict[str, float],
    weights_b: dict[str, float],
) -> float:
    """因子权重向量余弦相似度（同因子集对齐；缺失维度按 0 计）。"""
    fids = sorted(set(weights_a) | set(weights_b))
    if not fids:
        return 1.0
    va = np.array([weights_a.get(f, 0.0) for f in fids], dtype=float)
    vb = np.array([weights_b.get(f, 0.0) for f in fids], dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na <= 0 or nb <= 0:
        return 1.0 if (na == nb) else 0.0
    return float(va @ vb / (na * nb))


# ─── 组合级：方向 / 敞口 / 换手 ───────────────────────────


def direction_consistency(pos_a: pd.DataFrame, pos_b: pd.DataFrame) -> float:
    """方向一致率：两轨对同一标的的持仓方向（多/空/空仓）一致比例。"""
    common = sorted(set(pos_a.columns) & set(pos_b.columns))
    if not common:
        return 0.0
    a = pos_a[common].reindex(pos_b.index).fillna(0.0)
    b = pos_b[common].fillna(0.0)
    sign_a = np.sign(a.values)
    sign_b = np.sign(b.values)
    return float(np.mean(sign_a == sign_b))


def exposure_diff(pos_a: pd.DataFrame, pos_b: pd.DataFrame) -> float:
    """总敞口差：|Σ|pos_a| − Σ|pos_b|| / max(Σ|pos_b|, 1e-9)，按日均。"""
    exp_a = pos_a.abs().sum(axis=1).mean()
    exp_b = pos_b.abs().sum(axis=1).mean()
    denom = max(exp_b, 1e-9)
    return float(abs(exp_a - exp_b) / denom)


def turnover_diff(pos_a: pd.DataFrame, pos_b: pd.DataFrame) -> float:
    """换手差：日均换手（仓位矩阵逐日绝对值差）相对差。"""
    turn_a = pos_a.diff().abs().sum(axis=1).mean()
    turn_b = pos_b.diff().abs().sum(axis=1).mean()
    denom = max(turn_b, 1e-9)
    return float(abs(turn_a - turn_b) / denom)


# ─── 绩效级：滚动收益 / 回撤 ─────────────────────────────


def rolling_return_diff(ra: pd.Series, rb: pd.Series, window: int = 60) -> float:
    """滚动 60 日累计收益差（日均绝对差）。"""
    c_a = (1 + ra).rolling(window).apply(np.prod, raw=True)
    c_b = (1 + rb).rolling(window).apply(np.prod, raw=True)
    d = (c_a - c_b).abs()
    d = d[d.notna()]
    return float(d.mean()) if len(d) else float("nan")


def drawdown_diff(ra: pd.Series, rb: pd.Series) -> float:
    """最大回撤差（绝对差）。"""
    def _mdd(r: pd.Series) -> float:
        c = (1 + r.fillna(0.0)).cumprod()
        peak = c.cummax()
        return float(((c - peak) / peak).min())

    return abs(_mdd(ra) - _mdd(rb))


# ─── 统一对账入口 ─────────────────────────────────────────


def reconcile(
    weights_a: dict[str, float],
    weights_b: dict[str, float],
    pos_a: pd.DataFrame,
    pos_b: pd.DataFrame,
    ret_a: pd.Series,
    ret_b: pd.Series,
    base_annual_vol: Optional[float] = None,
    window: int = 60,
) -> dict[str, Any]:
    """三级对账（§5.2）。

    :param weights_a/b: 因子权重（信号级）
    :param pos_a/b: 仓位矩阵（日期 × 标的，组合级）
    :param ret_a/b: 组合收益序列（绩效级）
    :param base_annual_vol: 基准年化波动（绩效级门槛基准；None 用两轨均值年化波动）
    :param window: 滚动收益窗口
    :return: {"metrics": {...}, "gates": {...}, "pass": bool}
    """
    signal_cosine = cosine_similarity(weights_a, weights_b)
    dir_rate = direction_consistency(pos_a, pos_b)
    exp_diff = exposure_diff(pos_a, pos_b)
    turn_diff = turnover_diff(pos_a, pos_b)
    rret_diff = rolling_return_diff(ret_a, ret_b, window=window)
    mdd_diff = drawdown_diff(ret_a, ret_b)

    if base_annual_vol is None:
        vols = []
        for r in (ret_a, ret_b):
            s = r.dropna()
            if len(s) > 2:
                vols.append(float(s.std() * math.sqrt(252)))
        base_annual_vol = float(np.mean(vols)) if vols else 0.10

    per_threshold = max(base_annual_vol * 0.20, 1e-6)
    metrics = {
        "signal_cosine": round(signal_cosine, 4),
        "direction_rate": round(dir_rate, 4),
        "exposure_diff": round(exp_diff, 4),
        "turnover_diff": round(turn_diff, 4),
        "rolling_return_diff_60d": round(rret_diff, 6),
        "drawdown_diff": round(mdd_diff, 6),
        "base_annual_vol": round(base_annual_vol, 4),
    }
    gates = {
        "signal_cosine": signal_cosine >= GATES["signal_cosine"],
        "direction_rate": dir_rate >= GATES["direction_rate"],
        "exposure_diff": exp_diff <= GATES["exposure_diff"],
        "turnover_diff": turn_diff <= GATES["turnover_diff"],
        "performance": rret_diff <= per_threshold and mdd_diff <= per_threshold,
    }
    return {
        "metrics": metrics,
        "gates": gates,
        "pass": all(gates.values()),
    }


def render(r: dict[str, Any]) -> str:
    m, g = r["metrics"], r["gates"]
    lines = ["阶段 1 双轨对账（plans/57 §5.2）", "=" * 70]
    lines.append(f"信号级 因子权重余弦相似度 {m['signal_cosine']:.4f} (≥{GATES['signal_cosine']}) "
                 f"{'✅' if g['signal_cosine'] else '❌'}")
    lines.append(f"组合级 方向一致率 {m['direction_rate']:.4f} (≥{GATES['direction_rate']}) "
                 f"{'✅' if g['direction_rate'] else '❌'}  敞口差 {m['exposure_diff']:.4f} "
                 f"(≤{GATES['exposure_diff']}) {'✅' if g['exposure_diff'] else '❌'}  "
                 f"换手差 {m['turnover_diff']:.4f} (≤{GATES['turnover_diff']}) "
                 f"{'✅' if g['turnover_diff'] else '❌'}")
    th = max(m["base_annual_vol"] * 0.20, 1e-6)
    lines.append(f"绩效级 60 日累计收益差 {m['rolling_return_diff_60d']:.6f} / 回撤差 "
                 f"{m['drawdown_diff']:.6f} (≤基准年化波动×20%≈{th:.6f}) "
                 f"{'✅' if g['performance'] else '❌'}")
    lines.append("-" * 70)
    lines.append("总体：" + ("✅ 全部门槛通过，可进入阶段 2 退役" if r["pass"]
                            else "❌ 存在超门槛项，暂停退役（§5.2 不一致处理）"))
    return "\n".join(lines)


def _demo() -> dict[str, Any]:
    """合成数据演示对账机器（同源近一致两轨）。"""
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2025-01-01", periods=300)
    n = len(dates)
    w_a = {"f1": 0.4, "f2": 0.3, "f3": 0.3}
    w_b = {"f1": 0.4, "f2": 0.3, "f3": 0.3}  # 同因子集近一致
    pos_a = pd.DataFrame(rng.normal(0.2, 0.1, (n, 3)) * np.sign(rng.normal(0, 1, (n, 3))),
                         index=dates, columns=["RB", "CU", "TA"])
    pos_b = pos_a * 0.98  # 轻微差异
    ret_a = pd.Series(rng.normal(0.0005, 0.01, n), index=dates)
    ret_b = ret_a + rng.normal(0, 0.0005, n)
    return reconcile(w_a, w_b, pos_a, pos_b, ret_a, ret_b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="合成数据演示")
    ap.add_argument("--weights-a", type=str, default="")
    ap.add_argument("--weights-b", type=str, default="")
    ap.add_argument("--positions-a", type=str, default="")
    ap.add_argument("--positions-b", type=str, default="")
    ap.add_argument("--returns-a", type=str, default="")
    ap.add_argument("--returns-b", type=str, default="")
    ap.add_argument("--base-annual-vol", type=float, default=None)
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    if args.demo:
        report = _demo()
    else:
        if not all([args.weights_a, args.weights_b, args.positions_a,
                    args.positions_b, args.returns_a, args.returns_b]):
            print("需提供完整双轨输入文件，或用 --demo 演示")
            return 2
        weights_a = json.loads(Path(args.weights_a).read_text(encoding="utf-8"))
        weights_b = json.loads(Path(args.weights_b).read_text(encoding="utf-8"))
        pos_a = pd.read_csv(args.positions_a, index_col=0, parse_dates=True)
        pos_b = pd.read_csv(args.positions_b, index_col=0, parse_dates=True)
        ret_a = pd.read_csv(args.returns_a, index_col=0, parse_dates=True).iloc[:, 0]
        ret_b = pd.read_csv(args.returns_b, index_col=0, parse_dates=True).iloc[:, 0]
        report = reconcile(weights_a, weights_b, pos_a, pos_b, ret_a, ret_b,
                           base_annual_vol=args.base_annual_vol)
    print(render(report))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
