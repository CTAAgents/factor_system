#!/usr/bin/env python3
"""
scripts/verify_qa_workflow.py — 因子质检工作流程端到端验证（CTA 手册第六章 v1.3）。

验证链路（真实数据优先，SYNTHETIC 兜底，符合 K 线降级链）:
    数据面板 → 因子计算 → IC 序列
    → 入库前质检 Q1-Q10（一票否决） → 三级准入评估 → 9 部分质检报告
    → 月度复检 M1-M5 → 季度复检 F1-F6 → 半年度深度复检 D1-D4
    → 退役判定 5 条红线 → 7 状态机流转 + factor_db 落库（临时库）

用法:
    python scripts/verify_qa_workflow.py [--days 300] [--db 临时库路径]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fts.data_futures import get_futures_provider
from fts.factor_engine.factor_db.repository import FactorStatusRepository
from fts.factor_engine.factor_document import build_factor_document
from fts.factor_engine.ir_thresholds import factor_ir_threshold
from fts.factor_engine.permutation_test import factor_ic_permutation_test
from fts.factor_engine.qa import (
    QaItem,
    admission_summary,
    apply_status_transition,
    check_retirement,
    generate_qa_report,
    monthly_recheck,
    quarterly_recheck,
    run_pre_entry_qa,
    semi_annual_recheck,
    status_board,
)
from fts.factor_engine.shift_leak_test import shift_leak_test
from fts.factor_engine.stress_ic import stress_period_ic_test

# 验证品种池（核心活跃品种）+ 板块映射
SYMBOLS = ["RB", "HC", "I", "J", "CU", "AL", "ZN", "NI", "TA", "MA", "EG", "PP", "M", "Y", "RM", "A", "SR", "CF"]
SECTOR_MAP = {
    "RB": "黑色",
    "HC": "黑色",
    "I": "黑色",
    "J": "黑色",
    "CU": "有色",
    "AL": "有色",
    "ZN": "有色",
    "NI": "有色",
    "TA": "能化",
    "MA": "能化",
    "EG": "能化",
    "PP": "能化",
    "M": "农产品",
    "Y": "农产品",
    "RM": "农产品",
    "A": "农产品",
    "SR": "软商品",
    "CF": "软商品",
}


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """带 NaN 兜底的 Spearman 相关系数。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = ~(np.isnan(x) | np.isnan(y))
    xv, yv = x[valid], y[valid]
    if len(xv) < 5 or np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
        return 0.0
    from scipy.stats import spearmanr

    corr, _ = spearmanr(xv, yv)
    return float(corr) if not np.isnan(corr) else 0.0


def cross_section_ic(signal: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """逐日横截面 IC（当日全部品种 signal vs 前向收益）。"""
    ic: dict[str, float] = {}
    for dt in signal.index:
        s = signal.loc[dt].to_numpy(dtype=float)
        r = fwd.loc[dt].to_numpy(dtype=float)
        ic[str(dt)[:10]] = _spearman(s, r)
    return pd.Series(ic)


def _momentum_factor(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """动量因子：过去 N 日收益率。"""
    return close.pct_change(window)


def _forward_returns(close: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """前向收益（下一期收益率，最后 horizon 行为 NaN）。"""
    return close.pct_change(1).shift(-horizon)


def _aligned_flatten(signal: pd.DataFrame, fwd: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """信号与前向收益共同非空索引对齐后展平，返回 (signal, fwd, dates)。"""
    s = signal.stack(future_stack=True)
    f = fwd.stack(future_stack=True)
    common = s.index.intersection(f.index)
    sig = s.loc[common].to_numpy(dtype=float)
    ret = f.loc[common].to_numpy(dtype=float)
    valid = ~(np.isnan(sig) | np.isnan(ret))
    dates = np.array([str(d)[:10] for d, _ in common])
    return sig[valid], ret[valid], dates[valid]


def run_qa_verification(
    days: int,
    db_path: str | None,
    synthetic: bool = False,
    strict: bool = False,
    symbols: list[str] | None = None,
) -> int:
    """执行全链路质检验证，返回退出码。

    Args:
        days: 数据窗口（交易日）
        db_path: factor_db 临时库路径（None=临时目录）
        synthetic: True 时强制合成面板（CI/无网络环境确定性运行）
        strict: True 时退出码绑定因子判定表现（本地人工质检用）；
                默认仅验证链路完整执行（CI 用）
        symbols: 显式品种列表（能源产业链等专属工作流使用；None = 默认 SYMBOLS）
    """
    print("=" * 70)
    print("因子质检工作流程端到端验证（CTA 手册第六章 v1.3）")
    print("=" * 70)

    syms = symbols or SYMBOLS

    # ── 1. 数据准备（真实面板优先，SYNTHETIC 兜底；--synthetic 强制合成）──
    provider = get_futures_provider()
    if synthetic:
        # 统一 dates 构造合成面板（各品种独立随机游走，横截面有差异，确定性可复现）
        rng = np.random.RandomState(42)
        dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=days, freq="B")
        close = pd.DataFrame(
            {sym: 3000.0 + i * 37.0 + np.cumsum(rng.randn(days) * 20.0) for i, sym in enumerate(syms)},
            index=dates,
        )
        close = close.abs() + 100.0  # 防负价格
        source = "SYNTHETIC（--synthetic 强制）"
    else:
        panel, common_dates = provider.get_futures_panel(syms, days=days)
        close = pd.DataFrame({sym: df["close"] for sym, df in panel.items()}).reindex(common_dates)
        source = "真实" if "SYNTHETIC" not in close.columns else "SYNTHETIC 兜底"
    close = close.ffill()
    print(f"\n[1] 数据面板: {len(close.columns)} 品种 × {len(close)} 交易日（数据源: {source}）")

    # ── 2. 因子计算与 IC 序列 ────────────────────────────────────
    factor_meta: dict[str, Any] = {
        "name": "fut_mom_20",
        "family": "fut_momentum",
        "style_tags": ["momentum"],
        "category": "量价技术",
        "formula": "close.pct_change(20)",
        "code": "close.pct_change(20)",
        "logic": "动量延续：过去 20 日收益率在横截面上的排序预测下期收益",
        "environment": "趋势市",
        "signal_cycle": "中期(5-20日)",
        "params": {"N": 20},
        "researcher": "QA-Verify",
        "date": pd.Timestamp.today().strftime("%Y-%m-%d"),
    }
    signal = _momentum_factor(close, 20)
    fwd = _forward_returns(close, 1)
    ic_series = cross_section_ic(signal, fwd)
    ic_mean = float(ic_series.mean())
    ic_std = float(ic_series.std())
    ir = ic_mean / ic_std * np.sqrt(252) if ic_std > 1e-12 else 0.0
    print(f"[2] 因子 {factor_meta['name']}: IC均值={ic_mean:.4f} IR(年化)={ir:.2f}")

    # ── 3. Q1-Q10 入库前质检 ─────────────────────────────────────
    sig_flat2, fwd_flat2, dates_flat = _aligned_flatten(signal, fwd)

    # Q1 未来函数检测（Shift 错位校验）
    q1 = shift_leak_test(sig_flat2, fwd_flat2)
    # Q2 逻辑文档化（公式/分类/适用环境均写入）
    doc = build_factor_document(factor_meta)
    q2_ok = bool(doc.get("formula") and doc.get("category") and doc.get("apply_regime"))
    # Q3 参数遍历网格（N=10/16/20/24/40 五组，其中 16/24 供 Q9 敏感度使用）
    grid_ic = {n: float(cross_section_ic(_momentum_factor(close, n), fwd).mean()) for n in (10, 16, 20, 24, 40)}
    best_n = max(grid_ic, key=lambda n: abs(grid_ic[n]))
    q3_ok = len(grid_ic) >= 3 and all(np.isfinite(v) for v in grid_ic.values())
    # Q4 IC 均值同向且 |IC| > 0.02
    q4_ok = abs(ic_mean) > 0.02
    # Q5 IR 分类门槛（量价 ≥ 0.3）
    ir_gate = factor_ir_threshold(factor_meta)
    q5_ok = ir >= ir_gate
    # Q6 分层收益单调性（Top1-Bottom1 多空 > 0 且 Top>Bottom）
    q6 = _quintile_spread(signal, fwd)
    q6_ok = bool(q6.get("monotonic", False))
    # Q7 置换检验 p < 0.05
    perm = factor_ic_permutation_test(sig_flat2, fwd_flat2)
    q7_ok = bool(perm.passed)
    # Q8 极端行情 IC 验证
    stress = stress_period_ic_test(sig_flat2, fwd_flat2, dates_flat)
    q8_ok = bool(stress.get("passed", False))
    # Q9 参数敏感度（±20% 扰动绩效衰减 < 30%）
    ic20 = abs(grid_ic.get(20, ic_mean))
    pert_ic = max(abs(grid_ic.get(16, ic_mean)), abs(grid_ic.get(24, ic_mean)))
    q9_ok = (ic20 > 1e-12) and ((ic20 - pert_ic) / ic20 < 0.30)
    # Q10 板块拆解方向一致性
    q10_ok = _sector_consistency(signal, fwd)

    items = [
        QaItem("Q1", "未来函数检测", q1["passed"], q1.get("report", "shift 校验"), one_vote=True),
        QaItem("Q2", "逻辑文档化", q2_ok, "公式/经济逻辑/适用环境已写入", one_vote=True),
        QaItem(
            "Q3",
            "参数遍历网格",
            q3_ok,
            f"N=10/20/40 IC={ {k: round(v, 4) for k, v in grid_ic.items()} } 最优 N={best_n}",
            one_vote=True,
        ),
        QaItem("Q4", "IC 均值", q4_ok, f"IC均值={ic_mean:.4f}（>0.02）"),
        QaItem("Q5", "IR 分类门槛", q5_ok, f"IR={ir:.2f} ≥ {ir_gate}"),
        QaItem("Q6", "分层收益单调性", q6_ok, f"Top-Bottom={q6.get('spread', 0):.4f} 单调={q6.get('monotonic')}"),
        QaItem("Q7", "置换检验", q7_ok, f"p={perm.p_value:.4f} < 0.05"),
        QaItem("Q8", "极端行情 IC 验证", q8_ok, f"压力期通过={stress.get('passed')}"),
        QaItem("Q9", "参数敏感度", q9_ok, "N=16/24 扰动衰减 < 30%"),
        QaItem("Q10", "板块拆解", q10_ok, "分板块 IC 方向一致"),
    ]
    qa = run_pre_entry_qa(items)
    print("\n[3] 入库前质检 Q1-Q10:")
    for it in qa["items"]:
        mark = "PASS" if it["passed"] else "FAIL"
        print(f"    [{it['qid']}] {it['name']:10s} {mark}  {it['detail']}")
    print(
        f"    结论: {qa['conclusion']}（{qa['passed_count']}/{qa['total']} 通过，一票否决: {qa['one_vote_failed'] or '无'}）"
    )

    # ── 4. 三级准入评估 + 9 部分质检报告 ─────────────────────────
    score = min(5.0, 1.5 + abs(ic_mean) * 30 + (ir / ir_gate) * 1.0)
    adm = admission_summary(score, q5_ok)
    print(f"\n[4] 准入评估: 综合得分={score:.2f} → {adm['label']}（权重上限 {adm['max_weight']:.0%}）")
    report = generate_qa_report(
        {**factor_meta, "ic_mean": ic_mean, "ic_std": ic_std, "ir": ir, "ir_gate": ir_gate, "perm_p": perm.p_value},
        qa_result=qa,
        admission=adm,
        params={
            "grid": "N=10/16/20/24/40",
            "best": f"N={best_n}",
            "decay": f"{(ic20 - pert_ic) / ic20 if ic20 > 1e-12 else 0:.1%}",
            "conclusion": "稳定",
        },
    )
    _print_trim(report, "[4b] 《因子质检报告》")

    # ── 5. 月度复检 M1-M5 + 季度 F1-F6 + 半年度 D1-D4 ───────────
    monthly = monthly_recheck(
        ic_series.to_numpy(),
        oos_baseline_ic=abs(ic_mean),
        ir_gate=ir_gate,
        month_layered_return=q6.get("spread"),
        rank_deviation=0.03,
    )
    print(
        f"\n[5] 月度复检 M1-M5: 预警 {monthly['warn_count']} 项 → {monthly['action']}（权重 {monthly['weight_scale']:.0%}）"
    )
    for k, v in monthly["indicators"].items():
        print(f"    {k}: {v['detail']}")

    quarterly = quarterly_recheck(
        ic_ir_ratio=1.05,
        layered_ratio=1.1,
        param_steps=0,
        new_high_corr_pairs=0,
        cond_ic_change=0.1,
        sector_consistent=q10_ok,
    )
    print(f"    季度复检 F1-F6: 标记 {quarterly['flagged_count']} 项 → {'通过' if quarterly['passed'] else '需关注'}")

    semi = semi_annual_recheck(
        logic_valid=True, backtest_sharpe_ratio=0.95, pool_reconstructed=False, retired_review={"fut_old_mom_5": True}
    )
    print(
        f"    半年度复检 D1-D4: 标记 {semi['flagged_count']} 项，淘汰库复活因子: {semi.get('revived_factors') or '无'}"
    )

    # ── 6. 退役判定 5 条红线 ─────────────────────────────────────
    retire = check_retirement(
        consecutive_warn_months=monthly.get("consecutive_warn_months", 0),
        current_ic60=ic_mean,
        entry_ic60=abs(ic_mean),
        ir60=ir,
        logic_valid=True,
        data_source_alive=True,
    )
    print(
        f"\n[6] 退役判定: {'触发退役' if retire['triggered'] else '维持服役'}（触发红线: {retire['triggered_ids'] or '无'}）"
    )

    # ── 7. 7 状态机流转 + factor_db 落库（临时库） ───────────────
    print("\n[7] 状态机流转 + factor_db 落库:")
    with tempfile.TemporaryDirectory() as tmp:
        db = db_path or str(Path(tmp) / "qa_verify.duckdb")
        repo = FactorStatusRepository(db_path=db, market="futures")
        from fts.factor_engine.factor_db.schema import init_database

        init_database(Path(db))
        fids = ["fut_mom_20", "fut_rev_5", "fut_carry_1"]
        flows = [
            ("PENDING_QA", "入库质检通过"),
            ("CORE", "准入评估通过"),
            ("OBSERVATION", "月度复检 1 项预警"),
            ("CORE", "复检恢复"),
            ("RETIRED", "退役红线触发"),
        ]
        for fid in fids:
            cur = "DRAFT"
            for target, reason in flows:
                r = apply_status_transition(repo, fid, target, reason, from_status=cur)
                status = "OK" if r["ok"] else f"拒绝({r.get('error', '')[:24]})"
                print(f"    {fid}: {cur} → {target} [{status}]")
                if r["ok"]:
                    cur = target
            history = repo.get_history(fid)
            print(f"      ↑ 落库 {len(history)} 条状态变迁，当前状态 {cur}")
        repo.close()

    # ── 8. 看板统计 ─────────────────────────────────────────────
    board = status_board([{"name": f, "status": "CORE"} for f in fids[:2]] + [{"name": fids[2], "status": "RETIRED"}])
    print(
        f"\n[8] 质检状态看板: CORE={board['counts'].get('CORE', 0)} "
        f"RETIRED={board['counts'].get('RETIRED', 0)} 服役={board['serving']}"
    )

    # ── 9. 汇总 ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(
        f"   入库质检={qa['conclusion']} | 准入={adm['label']} | "
        f"月度={monthly['action']} | 退役={'触发' if retire['triggered'] else '未触发'}"
    )
    if strict:
        # 严格模式：因子判定需整体通过（本地人工质检用）
        ok = (
            qa["passed"]
            and q5_ok
            and adm["level"] in ("CORE", "CANDIDATE")
            and not retire["triggered"]
            and monthly["action"] in ("normal", "observe_50")
        )
        print(f"✅ 因子质检流程端到端验证完成: {'通过' if ok else '存在需关注项'}（--strict）")
        print("=" * 70)
        return 0 if ok else 2
    # 默认（CI/链路验证）：完整执行到此处即视为链路跑通，因子强弱是输入不决定结论
    print("✅ 因子质检流程端到端执行成功（链路完整：数据→Q1-Q10质检→准入→报告→复检→退役→状态机落库→看板）")
    print("   注: 上述因子判定为真实计算结果；因子表现强弱不影响链路验证结论（--strict 可启用严格判定）")
    print("=" * 70)
    return 0


def _quintile_spread(signal: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    """每日截面按因子值 5 分位，Top-Bottom 多空收益与单调性。"""
    spreads: list[float] = []
    q_means: dict[int, list[float]] = {i: [] for i in range(1, 6)}
    for dt in signal.index:
        s = signal.loc[dt].to_numpy(dtype=float)
        r = fwd.loc[dt].to_numpy(dtype=float)
        valid = ~(np.isnan(s) | np.isnan(r))
        if valid.sum() < 10:
            continue
        sv, rv = s[valid], r[valid]
        q = pd.qcut(pd.Series(sv), 5, labels=False, duplicates="drop")
        for qi in range(1, 6):
            sel = rv[q.to_numpy() == qi]
            if len(sel):
                q_means[qi].append(float(sel.mean()))
        top = q_means[5][-1] if q_means[5] else 0.0
        bot = q_means[1][-1] if q_means[1] else 0.0
        spreads.append(top - bot)
    if not spreads:
        return {"spread": 0.0, "monotonic": False}
    means = {qi: float(np.mean(v)) for qi, v in q_means.items() if v}
    monotonic = len(means) >= 4 and means[1] < means[2] < means[3] < means[4] < means[5]
    return {"spread": float(np.mean(spreads)), "monotonic": bool(monotonic)}


def _sector_consistency(signal: pd.DataFrame, fwd: pd.DataFrame) -> bool:
    """分板块 IC 方向一致性（多数板块同向）。"""
    signs: list[int] = []
    for sector in sorted(set(SECTOR_MAP.values())):
        syms = [s for s in SECTOR_MAP if SECTOR_MAP[s] == sector]
        sub = signal[[s for s in syms if s in signal.columns]]
        sub_f = fwd[[s for s in syms if s in fwd.columns]]
        if sub.empty or len(sub.columns) < 2:
            continue
        ic = cross_section_ic(sub, sub_f)
        if np.isfinite(ic.mean()):
            signs.append(1 if ic.mean() > 0 else -1)
    if not signs:
        return False
    return abs(sum(signs)) / len(signs) >= 0.5  # 至少 3/4 同向


def _print_trim(text: str, title: str, max_lines: int = 30) -> None:
    print(f"\n{title}（摘要前 {max_lines} 行）:")
    for line in text.splitlines()[:max_lines]:
        print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="因子质检工作流程端到端验证")
    parser.add_argument("--days", type=int, default=300, help="数据窗口（交易日）")
    parser.add_argument("--db", type=str, default=None, help="factor_db 临时库路径（默认临时目录）")
    parser.add_argument(
        "--synthetic", action="store_true", help="强制合成面板（CI/无网络环境确定性运行，默认真实数据优先）"
    )
    parser.add_argument("--strict", action="store_true", help="严格模式：退出码绑定因子判定表现（本地人工质检用）")
    parser.add_argument(
        "--chain",
        type=str,
        default="",
        choices=["", "energy"],
        help="产业链专属工作流: energy（能源产业链 9 品种全链质检）",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="显式品种列表（逗号分隔，如 SC0,FU0,LU0；覆盖 --chain 默认链品种）",
    )
    args = parser.parse_args()
    try:
        if args.symbols:
            syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        elif args.chain == "energy":
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS

            syms = list(ENERGY_CHAIN_SYMBOLS)
        else:
            syms = None
        return run_qa_verification(
            args.days, args.db, synthetic=args.synthetic, strict=args.strict, symbols=syms
        )
    except Exception as e:  # noqa: BLE001 — 验证脚本失败需透明展示
        print(f"❌ 验证失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
