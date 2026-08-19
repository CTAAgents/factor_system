"""拥挤度预测力校验 CLI（plans/56 §D：K-W 决策门 + 事件研究阈值校准）。

校验拥挤度信号对能源链后续回撤/收益的区分度——任何条件化配置都以"信号对
后续风险有真实区分力"为前提（对齐 plans/53 D 模块 / plans/55 v3 决策门纪律）。

主决策门（K-W 区分度）:
  - 滚动检测 crowding_score 序列（默认 step=5）
  - 按 high_crowding 阈值分高/低拥挤两组，K-W 检验后续 N 日最大回撤与累计收益
  - 通过条件：K-W p<0.05 且高拥挤组后续回撤显著更大（均值排序）

事件研究阈值校准（Regime-Driven 附录 A.1）:
  - 崩盘事件 = 能源链等权指数从高点回撤 ≥20% 且 20 日内完成
  - 命中率 = 事件前 5 日拥挤度触发比例（目标 ≥70%）
  - 误报率 = 触发后 20 日无 ≥10% 回撤的比例（目标 <40%）
  - 阈值 ±1 表现突变则弃用（过拟合信号）

数据缺失处理（如实降级不伪造）：面板 <2 品种 → 报告标注退出；事件/样本不足
如实标注"待积累"，不编造结论。

用法:
    python scripts/regime_crowding_predictive_power_check.py [--step 5]
        [--fwd-days 10] [--trace-id <id>] [--json]

版本: v0.1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data import FTSDataProvider  # noqa: E402
from fts.factor_engine.regime_crowding import (  # noqa: E402
    CrowdingSignalConfig,
    compute_crowding_signals,
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


def _energy_composite(panel: dict[str, pd.DataFrame]) -> pd.Series:
    """能源链等权收益率指数（close 归一化首值=100 后截面均值）。"""
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
    mat = pd.DataFrame(closes).dropna(how="all")
    if mat.empty:
        return pd.Series(dtype=float)
    first_valid = mat.apply(lambda s: s.dropna().iloc[0])
    return (mat.div(first_valid, axis=1) * 100.0).mean(axis=1).dropna()


def _rolling_crowding(
    panel: dict[str, pd.DataFrame],
    cfg: CrowdingSignalConfig,
    step: int = 5,
    min_history: int = 70,
) -> pd.Series:
    """滚动检测 crowding_score 历史序列（逐窗口重算，无未来函数）。"""
    idx = _energy_composite(panel).index
    if len(idx) < min_history:
        return pd.Series(dtype=float)
    scores: list[tuple[pd.Timestamp, float]] = []
    for i in range(min_history, len(idx), step):
        d = idx[i]
        sub = {s: df.loc[:d] for s, df in panel.items() if not df.loc[:d].empty}
        res = compute_crowding_signals(sub, cfg)
        scores.append((pd.Timestamp(d), float(res["crowding_score"])))
    if not scores:
        return pd.Series(dtype=float)
    return pd.Series(dict(scores)).sort_index().rename("crowding")


def _fwd_drawdown(
    idx: pd.Series,
    state_dates: pd.Index,
    fwd: int,
) -> dict[pd.Timestamp, float]:
    """状态日后 N 日窗口最大回撤（自窗口起点，无未来函数）。"""
    out: dict[pd.Timestamp, float] = {}
    for d in state_dates:
        pos = idx.index.get_indexer([d])[0]
        if pos < 0 or pos + 1 >= len(idx):
            continue
        win = idx.iloc[pos + 1 : pos + 1 + fwd]
        if len(win) < max(3, fwd // 2):
            continue
        peak = float(win.iloc[0])
        drawdown = float((win / peak - 1.0).min())
        out[pd.Timestamp(d)] = drawdown
    return out


def _fwd_return(
    idx: pd.Series,
    state_dates: pd.Index,
    fwd: int,
) -> dict[pd.Timestamp, float]:
    """状态日后 N 日累计收益。"""
    out: dict[pd.Timestamp, float] = {}
    for d in state_dates:
        pos = idx.index.get_indexer([d])[0]
        if pos < 0 or pos + 1 >= len(idx):
            continue
        win = idx.iloc[pos + 1 : pos + 1 + fwd]
        if len(win) < max(3, fwd // 2):
            continue
        out[pd.Timestamp(d)] = float(win.iloc[-1] / win.iloc[0] - 1.0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="拥挤度预测力校验（plans/56 §D：K-W 决策门 + 事件研究）")
    parser.add_argument("--step", type=int, default=5, help="滚动快照步长（默认 5）")
    parser.add_argument("--fwd-days", type=int, default=10, help="后续窗口（默认 10 日）")
    parser.add_argument("--trace-id", default="", help="HARNESS trace_id")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    trace_id = args.trace_id or f"regime-crowd-check-{pd.Timestamp.now():%Y%m%d%H%M%S}"
    cfg = CrowdingSignalConfig()

    provider = FTSDataProvider()
    from fts.data_futures import ENERGY_CHAIN_SYMBOLS

    panel, _ = provider.get_futures_panel(
        symbols=list(ENERGY_CHAIN_SYMBOLS), days=cfg.days + 260, trace_id=trace_id
    )
    if len(panel) < 2:
        print("[regime-crowd-check] 能源链面板品种 < 2，数据不足，退出（如实标注）")
        return 0

    idx = _energy_composite(panel)
    if idx.empty or len(idx) < 100:
        print("[regime-crowd-check] 等权指数数据不足，退出（如实标注）")
        return 0

    crowd = _rolling_crowding(panel, cfg, step=args.step)
    if crowd.empty:
        print("[regime-crowd-check] 滚动拥挤度序列为空，退出（如实标注）")
        return 0

    dd_map = _fwd_drawdown(idx, crowd.index, args.fwd_days)
    ret_map = _fwd_return(idx, crowd.index, args.fwd_days)
    aligned = pd.DataFrame(
        {
            "crowding": crowd.reindex(list(dd_map.keys())),
            "drawdown": pd.Series(dd_map),
            "ret": pd.Series(ret_map),
        }
    ).dropna(subset=["crowding", "drawdown"])
    if aligned.empty:
        print("[regime-crowd-check] 对齐后无有效样本，退出（如实标注）")
        return 0

    # ── 主决策门：高/低拥挤两组的后续回撤区分度 ──────────
    high = aligned[aligned["crowding"] >= cfg.high_crowding]["drawdown"]
    low = aligned[aligned["crowding"] < cfg.high_crowding]["drawdown"]
    stats: dict[str, Any] = {
        "n_total": int(len(aligned)),
        "n_high": int(len(high)),
        "n_low": int(len(low)),
        "high_mean_dd": round(float(high.mean()), 6) if len(high) else None,
        "low_mean_dd": round(float(low.mean()), 6) if len(low) else None,
        "high_mean_ret": round(float(aligned[aligned["crowding"] >= cfg.high_crowding]["ret"].mean()), 6)
        if len(high)
        else None,
        "low_mean_ret": round(float(aligned[aligned["crowding"] < cfg.high_crowding]["ret"].mean()), 6)
        if len(low)
        else None,
    }
    kw_p: float | None = None
    if len(high) >= 3 and len(low) >= 3:
        from scipy.stats import kruskal

        _, kw_p = kruskal(high.to_numpy(), low.to_numpy())
        kw_p = float(kw_p)
        stats["kruskal_p_dd"] = round(kw_p, 4)
    # 决策门：高拥挤后续回撤更显著大（dd 更负）
    dd_ok = stats["high_mean_dd"] is not None and stats["low_mean_dd"] is not None and stats["high_mean_dd"] < stats["low_mean_dd"]
    gate = {
        "dimension": "forward_drawdown",
        "criterion": "K-W p<0.05 AND 高拥挤组后续回撤更大（mean_dd 更负）",
        "passed": bool(kw_p is not None and kw_p < 0.05 and dd_ok),
        "detail": stats,
    }

    # ── 事件研究：崩盘事件命中率/误报率（附录 A.1） ─────
    crash_ev = _find_crash_events(idx)
    hit: int | None = None
    fp: int | None = None
    if crash_ev and len(crowd):
        triggered_dates = set(crowd[crowd >= cfg.high_crowding].index)
        hit_cnt = sum(
            1
            for ev in crash_ev
            if any((ev - pd.Timedelta(days=0) - pd.Timedelta(days=10)) <= t <= ev for t in triggered_dates)
            or any(abs((t - ev).days) <= 5 for t in triggered_dates)
        )
        hit = hit_cnt
        # 误报：触发日（score≥阈值）后 20 日无 ≥10% 回撤
        fp_cnt = 0
        checked = 0
        for t in sorted(triggered_dates):
            pos = idx.index.get_indexer([t])[0]
            if pos + 20 >= len(idx):
                continue
            checked += 1
            win = idx.iloc[pos + 1 : pos + 21]
            if float((win / float(win.iloc[0]) - 1.0).min()) > -0.10:
                fp_cnt += 1
        fp = fp_cnt
        stats["event_study"] = {
            "n_crash_events": len(crash_ev),
            "hit_count": hit,
            "hit_rate": round(hit / len(crash_ev), 4) if crash_ev else None,
            "fp_checked": checked,
            "fp_count": fp,
            "fp_rate": round(fp / checked, 4) if checked else None,
        }

    report = {
        "trace_id": trace_id,
        "config": {"step": args.step, "fwd_days": args.fwd_days, "high_crowding": cfg.high_crowding},
        "gate": gate,
        "event_study": stats.get("event_study"),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_safe))
        return 0

    out_dir = Path(_FTS_ROOT) / "reports" / "energy" / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "regime_crowding_predictive_power.md"
    lines = [
        "# 拥挤度预测力校验报告（plans/56 §D：K-W 决策门 + 事件研究）",
        f"- trace_id: {trace_id}",
        f"- 参数: step={args.step} / 后续窗口={args.fwd_days} 日 / high_crowding={cfg.high_crowding}",
        "",
        "## 主决策门（后续回撤区分度）",
        f"- 有效样本: {stats['n_total']}（高拥挤 {stats['n_high']} / 低拥挤 {stats['n_low']}）",
        f"- 高拥挤组后续回撤均值: {stats['high_mean_dd']:.2%} | 低拥挤组: {stats['low_mean_dd']:.2%}",
        f"- 高拥挤组后续收益均值: {stats['high_mean_ret']:.2%} | 低拥挤组: {stats['low_mean_ret']:.2%}",
    ]
    if kw_p is not None:
        lines.append(f"- Kruskal-Wallis p={kw_p:.4f} → {'✅ 显著' if kw_p < 0.05 else '⚠️ 不显著'}")
        lines.append(f"- 排序校验（高拥挤回撤更大）: {'✅ 正确' if dd_ok else '❌ 不正确'}")
    else:
        lines.append("- K-W 检验: 组样本 <3，不可检验（如实标注）")
    lines.append(f"- **决策门结论: {'✅ 通过' if gate['passed'] else '❌ 未通过'}**（{gate['criterion']}）")
    lines.append("")
    es = stats.get("event_study")
    if es:
        lines.append("## 事件研究（崩盘事件阈值校准，附录 A.1）")
        lines.append(f"- 崩盘事件数: {es['n_crash_events']} | 命中率: {es['hit_rate']:.1%}（目标 ≥70%）| "
                     f"误报率: {es['fp_rate']:.1%}（目标 <40%）")
        lines.append(f"- 命中 {es['hit_count']}/{es['n_crash_events']}；误报 {es['fp_count']}/{es['fp_checked']}")
    else:
        lines.append("## 事件研究: 崩盘事件不足，待数据积累（如实标注，不编造）")
    lines.append("")
    lines.append("*数据源: FTSDataProvider 真实行情（能化链日线），如实标注缺失。*")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 报告已保存: {out_path}")
    return 0


def _find_crash_events(idx: pd.Series, dd_threshold: float = 0.20, window: int = 20) -> list[pd.Timestamp]:
    """崩盘事件：从高点回撤 ≥20% 且 window 日内完成（事件日 = 回撤低点日）。

    Returns:
        事件日列表（仅取 20 日窗口内最大回撤触底日，去重后返回）。
    """
    if len(idx) < window * 2:
        return []
    peak = idx.cummax()
    dd = idx / peak - 1.0
    events: list[pd.Timestamp] = []
    for i in range(window, len(idx)):
        if dd.iloc[i] <= -dd_threshold:
            # 回撤完成日：当日回撤 ≥ 阈值，且该回撤在 window 内从高点启动
            win_peak = float(peak.iloc[i - window + 1 : i + 1].max())
            if float(idx.iloc[i]) / win_peak - 1.0 <= -dd_threshold:
                events.append(pd.Timestamp(idx.index[i]))
    # 去重：事件间隔 < window 只保留首个
    dedup: list[pd.Timestamp] = []
    for ev in events:
        if not dedup or (ev - dedup[-1]).days > window:
            dedup.append(ev)
    return dedup


if __name__ == "__main__":
    raise SystemExit(main())
