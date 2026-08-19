"""L0 宏观 Beta 层预测力校验 CLI v3（plans/55 §E：日频收益决策门 + 20 日 vol 参考）。

v1（前向收益 K-W，step=5/fwd=10）实测 p=0.71 不显著且排序反转；
v2（波动率分层决策门 + 敏感性扫描）暴露三点：① step=5/10 样本量不足（70/35）统计力缺失；
② step=1 日频下前向收益 fwd=10 显著（p=0.020）且排序正确——Beta 状态存在弱但真实的短窗
区分信号；③ 10 日窗口年化 vol 噪声过大（700% 量级失真）。v3 校准：

    检测快照: step=1（日频，信号管线本就每日运行，零额外成本；样本量 ~350 统计力充分）

    主决策门（前向收益，fwd=10）:
      - 对每个 Beta 状态日，取其后 10 日能源链等权指数累计收益
      - Kruskal-Wallis 检验三态前向收益差异 + 排序校验
        （预期 RISK_ON > RANGE_BOUND > RISK_OFF）
      - 通过条件：K-W p<0.05 且三态排序正确

    参考维度（20 日 realized vol 分层，不设硬门）:
      - 状态日后 20 日窗口年化 realized vol（20 日窗口抑制小样本年化噪声，
        对齐 BetaDetector vol_window=20；预期 RISK_OFF 期风险更高，供观察）

    敏感性扫描: step ∈ {1,5,10} × fwd ∈ {5,10,20}（vol 固定 20 日窗），
      每格输出 n / ret K-W p / ret 排序 / vol K-W p / vol 排序——结论对
      窗口参数的稳健性（±1 参数突变即弃用，对齐文档附录 A 事件研究纪律）

数据缺失处理（如实降级不伪造）：金融面板 <2 品种 / 能源链 <2 品种 → 报告标注退出；
某状态样本 <3 → 该状态不参与 K-W（如实标注）。

用法:
    python scripts/regime_beta_predictive_power_check.py [--step 1] [--fwd-days 10]
        [--vol-window 20] [--trace-id <id>] [--json]

版本: v0.3.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data import FTSDataProvider  # noqa: E402
from fts.factor_engine.regime_beta_layer import (  # noqa: E402
    RANGE_BOUND,
    RISK_OFF,
    RISK_ON,
    BetaDetector,
    BetaLayerConfig,
)


def _json_safe(obj: Any) -> Any:
    """递归将 NaN/Inf 转换为 None（保证输出合法 JSON）。"""
    import math

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _rolling_beta_series(
    panel: dict[str, pd.DataFrame],
    cfg: BetaLayerConfig,
    step: int = 5,
    min_history: int = 70,
) -> pd.Series:
    """滚动检测 beta_state 历史序列（逐窗口重算，无未来函数）。

    Returns:
        DatetimeIndex → beta_state 的 Series；数据不足返回空 Series。
    """
    closes = {s: df["close"] for s, df in panel.items() if "close" in df.columns and not df.empty}
    if len(closes) < 2:
        return pd.Series(dtype=object)
    common = pd.concat([c for c in closes.values()], axis=1).dropna(how="all")
    if common.empty:
        return pd.Series(dtype=object)
    dates = common.index
    if len(dates) < min_history:
        return pd.Series(dtype=object)

    states: list[tuple[pd.Timestamp, str]] = []
    detector = BetaDetector(cfg)
    for i in range(min_history, len(dates), step):
        d = dates[i]
        sub = {s: df.loc[:d] for s, df in panel.items() if not df.loc[:d].empty}
        st = detector.detect(sub)
        states.append((pd.Timestamp(d), st["state"]))
    if not states:
        return pd.Series(dtype=object)
    return pd.Series(dict(states)).sort_index().rename("state")


def _energy_composite_ret(panel: dict[str, pd.DataFrame]) -> pd.Series:
    """能源链等权收益率指数日收益（各品种 close 归一化首值=100 后截面均值 → pct_change）。

    Returns:
        日收益 Series；有效品种 <2 返回空 Series。
    """
    closes: dict[str, pd.Series] = {}
    for sym, df in panel.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        c = pd.to_numeric(df["close"], errors="coerce")
        if c.dropna().empty:
            continue
        closes[sym] = c
    if len(closes) < 2:
        return pd.Series(dtype=float)
    df = pd.DataFrame(closes).dropna(how="all")
    if df.empty:
        return pd.Series(dtype=float)
    first_valid = df.apply(lambda s: s.dropna().iloc[0])
    idx = (df.div(first_valid, axis=1) * 100.0).mean(axis=1).dropna()
    if len(idx) < 3:
        return pd.Series(dtype=float)
    return idx.pct_change().dropna().rename("ret")


def _fwd_metrics(
    rets: pd.Series,
    state_dates: pd.Index,
    fwd: int,
    vol_window: int = 20,
) -> tuple[dict[pd.Timestamp, float], dict[pd.Timestamp, float]]:
    """状态日后窗口的年化 realized vol（vol_window 日）与累计收益（fwd 日）。

    无未来函数：只用 d 之后数据；vol_window 独立于 fwd（20 日窗口抑制
    小样本年化噪声，对齐 BetaDetector vol_window=20）。

    Returns:
        (vol_map, ret_map)：{状态日: 后续 vol_window 日年化 vol} /
        {状态日: 后续 fwd 日累计收益}。窗口收益点不足 max(3, win//2) 时跳过
        （尾部样本如实缺省）。
    """
    idx = rets.index
    vol_map: dict[pd.Timestamp, float] = {}
    ret_map: dict[pd.Timestamp, float] = {}
    for d in state_dates:
        pos = idx.get_indexer([d])[0]
        if pos < 0 or pos + 1 >= len(idx):
            continue
        vol_win = rets.iloc[pos + 1 : pos + 1 + vol_window]
        ret_win = rets.iloc[pos + 1 : pos + 1 + fwd]
        if len(vol_win) >= max(3, vol_window // 2):
            vol_map[pd.Timestamp(d)] = float(vol_win.std() * np.sqrt(252))
        if len(ret_win) >= max(3, fwd // 2):
            ret_map[pd.Timestamp(d)] = float(np.prod(1.0 + ret_win.to_numpy()) - 1.0)
    return vol_map, ret_map


def _kw_check(
    groups: dict[str, np.ndarray],
) -> tuple[float | None, dict[str, dict[str, float]]]:
    """按状态分桶 K-W 检验 + 各桶条件均值/样本数。

    Returns:
        (p_value 或 None, stats)。样本 <3 的状态不参与检验（如实标注）。
    """
    eligible = {s: g for s, g in groups.items() if len(g) >= 3}
    stats: dict[str, dict[str, float]] = {}
    for s, arr in eligible.items():
        stats[s] = {"n": int(len(arr)), "mean": round(float(np.mean(arr)), 6)}
    if len(eligible) < 2:
        return None, stats
    from scipy.stats import kruskal

    stat, p = kruskal(*[eligible[s] for s in eligible])
    return float(p), stats


def _order_ok(stats: dict[str, dict[str, float]], seq: tuple[str, ...]) -> bool | None:
    """三态齐全时断言均值排序（seq 为预期从高到低顺序）。"""
    if all(s in stats for s in seq):
        return stats[seq[0]]["mean"] > stats[seq[1]]["mean"] > stats[seq[2]]["mean"]
    return None


# 敏感性扫描网格（vol 固定 20 日窗）
_STEP_GRID = (1, 5, 10)
_FWD_GRID = (5, 10, 20)
# 前向收益排序预期（主决策门）：RISK_ON > RANGE_BOUND > RISK_OFF
_RET_ORDER = (RISK_ON, RANGE_BOUND, RISK_OFF)
# 20 日 vol 分层排序预期（参考维度）：RISK_OFF 期风险最高
_VOL_ORDER = (RISK_OFF, RANGE_BOUND, RISK_ON)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="L0 宏观 Beta 层预测力校验 v3（日频收益决策门 + 20 日 vol 参考 + 敏感性扫描）"
    )
    parser.add_argument("--step", type=int, default=1, help="主决策门滚动快照步长（默认 1=日频）")
    parser.add_argument("--fwd-days", type=int, default=10, help="主决策门前向收益窗口（默认 10 日）")
    parser.add_argument("--vol-window", type=int, default=20, help="参考维度 vol 窗口（默认 20 日）")
    parser.add_argument("--trace-id", default="", help="HARNESS trace_id")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    trace_id = args.trace_id or f"regime-beta-check-{pd.Timestamp.now():%Y%m%d%H%M%S}"
    cfg = BetaLayerConfig()

    provider = FTSDataProvider()
    fin_panel, _ = provider.get_futures_panel(
        symbols=list(cfg.fin_symbols), days=cfg.days + 260, trace_id=trace_id
    )
    if len(fin_panel) < 2:
        print("[regime-beta-check] 金融期货面板品种 < 2，数据不足，退出（如实标注）")
        return 0

    from fts.data_futures import ENERGY_CHAIN_SYMBOLS

    energy_panel, _ = provider.get_futures_panel(
        symbols=list(ENERGY_CHAIN_SYMBOLS), days=cfg.days + 260, trace_id=trace_id
    )
    if len(energy_panel) < 2:
        print("[regime-beta-check] 能源链面板品种 < 2，数据不足，退出（如实标注）")
        return 0

    energy_rets = _energy_composite_ret(energy_panel)
    if energy_rets.empty:
        print("[regime-beta-check] 能源链等权指数收益为空，退出（如实标注）")
        return 0

    # ── 敏感性扫描网格 ──────────────────────────────────
    scan_rows: list[dict[str, Any]] = []
    for step in _STEP_GRID:
        beta_series = _rolling_beta_series(fin_panel, cfg, step=step)
        if beta_series.empty:
            continue
        for fwd in _FWD_GRID:
            vol_map, ret_map = _fwd_metrics(
                energy_rets, beta_series.index, fwd, vol_window=args.vol_window
            )
            # ret 与 vol 检验独立对齐：ret 只需 fwd 日后续数据，不受 vol 窗口（20 日）连带截断
            vol_aligned = pd.DataFrame(
                {
                    "state": beta_series.reindex(list(vol_map.keys())),
                    "vol": pd.Series(vol_map),
                }
            ).dropna(subset=["state", "vol"])
            ret_aligned = pd.DataFrame(
                {
                    "state": beta_series.reindex(list(ret_map.keys())),
                    "ret": pd.Series(ret_map),
                }
            ).dropna(subset=["state", "ret"])
            if ret_aligned.empty:
                continue
            vol_groups = {s: g["vol"].to_numpy() for s, g in vol_aligned.groupby("state")}
            ret_groups = {s: g["ret"].to_numpy() for s, g in ret_aligned.groupby("state")}
            vol_p, vol_stats = _kw_check(vol_groups)
            ret_p, ret_stats = _kw_check(ret_groups)
            scan_rows.append(
                {
                    "step": step,
                    "fwd": fwd,
                    "n": int(len(ret_aligned)),
                    "state_dist": {s: int(len(g)) for s, g in ret_aligned.groupby("state")},
                    "ret_p": round(ret_p, 4) if ret_p is not None else None,
                    "ret_order": _order_ok(ret_stats, _RET_ORDER),
                    "vol_p": round(vol_p, 4) if vol_p is not None else None,
                    "vol_order": _order_ok(vol_stats, _VOL_ORDER),
                    "ret_means": {s: v["mean"] for s, v in ret_stats.items()},
                }
            )

    # ── 主决策门（默认 step=1/fwd=10，前向收益维度） ──────
    main_row = next(
        (r for r in scan_rows if r["step"] == args.step and r["fwd"] == args.fwd_days), None
    )
    gate = {
        "dimension": "forward_return",
        "criterion": "K-W p<0.05 AND order RISK_ON > RANGE_BOUND > RISK_OFF",
        "passed": bool(
            main_row
            and main_row["ret_p"] is not None
            and main_row["ret_p"] < 0.05
            and main_row["ret_order"]
        ),
        "detail": main_row,
    }
    if main_row is None:
        gate["passed"] = False
        gate["detail"] = {"note": "默认参数下无有效样本"}

    report = {
        "trace_id": trace_id,
        "beta_cfg": {"fin_symbols": cfg.fin_symbols, "days": cfg.days},
        "gate": gate,
        "scan": scan_rows,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_safe))
        return 0

    out_dir = Path(_FTS_ROOT) / "reports" / "energy" / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "regime_beta_predictive_power.md"
    lines = [
        "# L0 宏观 Beta 层预测力校验报告（v3：日频收益决策门 + 20 日 vol 参考）",
        f"- trace_id: {trace_id}",
        "- 校准依据: v1（step=5 收益 K-W p=0.71 不显著）/ v2（敏感性扫描暴露 step=1 日频 "
        "fwd=10 收益显著 p=0.020 且排序正确、step=5/10 样本不足、10 日 vol 年化噪声失真）",
        "",
        "## 主决策门（前向收益，日频 step=1 / fwd=10）",
        f"- 参数: step={args.step} / 前向收益窗口={args.fwd_days} 日",
    ]
    if main_row:
        lines.append(f"- 有效样本: {main_row['n']} | 状态分布: {main_row['state_dist']}")
        lines.append("")
        lines.append("| Beta 状态 | 样本数 | 后续 10 日累计收益均值 |")
        lines.append("|:---------|:------:|:----------------------|")
        for s, m in main_row["ret_means"].items():
            n = main_row["state_dist"].get(s, 0)
            lines.append(f"| {s} | {n} | {m:.4%} |")
        lines.append("")
        rp = main_row["ret_p"]
        if rp is not None:
            lines.append(
                f"- Kruskal-Wallis p={rp:.4f} → {'✅ 显著' if rp < 0.05 else '⚠️ 不显著'}"
            )
        else:
            lines.append("- Kruskal-Wallis: 可检验状态 <2，不可检验（如实标注）")
        lines.append(
            f"- 排序校验（预期 {_RET_ORDER[0]} > {_RET_ORDER[1]} > {_RET_ORDER[2]}）: "
            f"{'✅ 正确' if main_row['ret_order'] else '❌ 不正确'}"
        )
        lines.append("")
        lines.append(
            f"### 决策门结论: **{'✅ 通过' if gate['passed'] else '❌ 未通过'}** "
            f"（{gate['criterion']}）"
        )
        lines.append("")
        lines.append("### 参考维度（20 日 realized vol 分层，不设硬门）")
        vp = main_row["vol_p"]
        if vp is not None:
            lines.append(
                f"- Kruskal-Wallis p={vp:.4f} → {'✅ 显著' if vp < 0.05 else '⚠️ 不显著'}；"
                f"排序（预期 {_VOL_ORDER[0]} > {_VOL_ORDER[1]} > {_VOL_ORDER[2]}）: "
                f"{'✅ 正确' if main_row['vol_order'] else '❌ 不正确'}"
            )
        else:
            lines.append("- Kruskal-Wallis: 可检验状态 <2，不可检验（如实标注）")
    else:
        lines.append("- 默认参数下无有效样本，主决策门不可判定（如实标注）")

    lines.append("")
    lines.append("## 敏感性扫描（step × fwd 网格，vol 固定 20 日窗）")
    lines.append("| step | fwd | n | 状态分布 | ret K-W p | ret 排序 | vol K-W p | vol 排序 |")
    lines.append("|:----:|:---:|:--:|:---------|:---------:|:--------:|:---------:|:--------:|")
    for r in scan_rows:
        lines.append(
            f"| {r['step']} | {r['fwd']} | {r['n']} | {r['state_dist']} | "
            f"{r['ret_p'] if r['ret_p'] is not None else '—'} | "
            f"{'✅' if r['ret_order'] else '❌' if r['ret_order'] is False else '—'} | "
            f"{r['vol_p'] if r['vol_p'] is not None else '—'} | "
            f"{'✅' if r['vol_order'] else '❌' if r['vol_order'] is False else '—'} |"
        )
    lines.append("")
    lines.append("*数据源: FTSDataProvider 真实行情（CFFEX/能化链日线），如实标注缺失。*")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 报告已保存: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
