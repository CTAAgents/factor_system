"""
scripts/energy_chain_degradation_dryrun.py — 能源链精英因子真实周期质检（退化检测）· 纯 Dry-run
=========================================================================================
【安全承诺 — 只读 Dry-run，绝无副作用】
  1) 不读写 factor_catalog_energy.duckdb 除 SELECT 外任何操作（apply=False / commit=False）
  2) 不移动、不删除、不覆盖 memory/knowledge/factors/energy_chain_elite/ 下任何 JSON
  3) 不创建 _deprecated 目录、不 shutil.move 任何文件
  4) 仅写报告到 reports/energy_chain/{date}/qa/

三路检测（与方案一致）：
  A. reaudit 新标准 Q1-Q10 全量重审（run_reaudit market=energy, apply=False）
  B. IC 退化重验证（curr_IC vs hist_IC；ic_threshold=0.02 / drop_threshold=0.30）
  C. FactorInspector Sharpe 血缘退化（threshold=-0.20, commit=False）

品种池：ENERGY_CHAIN_SYMBOLS（12），days=300（PX0 最短 700+ 行 × 300 天满足）
输出目录：reports/energy_chain/{today}/qa/
  - reaudit_energy_elite_{today}.log / reaudit_energy_elite_{today}.json
  - degradation_revalidation_energy_{today}.log / degradation_revalidation_energy_{today}.csv
  - inspector_degradation_energy_{today}.log
  - energy_degradation_summary_{today}.md（三路合并摘要）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

# 抑制 numpy/scipy 运行时警告
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")
warnings.filterwarnings("ignore", category=FutureWarning, module="numpy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REPORTS_ROOT = PROJECT_ROOT / "reports"


# ──────────────────────────────────────────────────────────
# 日志/工具
# ──────────────────────────────────────────────────────────


class TeeLogger:
    """同时写控制台 + 文件，无第三方依赖。"""

    def __init__(self, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = log_path.open("w", encoding="utf-8")
        self.started = time.time()

    def log(self, msg: str = "", *args, **kwargs) -> None:
        if args:
            try:
                msg = msg % args
            except Exception:
                msg = msg + " " + " ".join(str(a) for a in args)
        line = msg
        if msg:
            elapsed = time.time() - self.started
            line = f"[{elapsed:7.1f}s] {msg}"
        print(line, flush=True)
        self._fp.write(line + "\n")
        self._fp.flush()

    def __call__(self, msg: str = "", *args, **kwargs) -> None:
        """与 .log() 等价；保留旧代码 log(...) 写法无需替换。"""
        self.log(msg, *args, **kwargs)

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            pass


def _generate_trace_id(prefix: str) -> str:
    return f"fts.energy_qa.{prefix}.{datetime.now().strftime('%Y%m%d%H%M%S')}"


# ──────────────────────────────────────────────────────────
# B 路：IC 退化重验证（直接复用 futures_factor_revalidation._compute_current_ic
#        语义，但从 FactorRepository + energy_chain_elite 加载，且绝不 move 文件）
# ──────────────────────────────────────────────────────────


def _load_energy_active_elite_with_hist(provider_panel_syms: set[str]) -> list[dict[str, Any]]:
    """从 DuckDB 取 market=energy active elite，再从 energy_chain_elite JSON 补 hist_ic。"""
    from fts.config import get_config
    from fts.factor_engine.factor_db.repository import FactorRepository

    cfg = get_config()
    elite_dir = PROJECT_ROOT / cfg.get_elite_dir("energy")
    repo = FactorRepository(market="energy")
    try:
        rows = (
            repo.list_factors(
                market="energy", status="active", is_elite=True, limit=10000, sort_by="sharpe", sort_order="desc"
            )
            or []
        )
    finally:
        repo.close()

    result: list[dict[str, Any]] = []
    for f in rows:
        fid = f.get("factor_id", "")
        fp = elite_dir / f"{fid}.json"
        payload: dict[str, Any] = dict(f)
        hist_ic = 0.0
        if fp.exists():
            try:
                j = json.loads(fp.read_text(encoding="utf-8"))
                ev = j.get("evaluation") or {}
                bt = ev.get("level_1_backtest") or {}
                hist_ic = abs(float(bt.get("ic") or 0.0))
                payload["_json_file"] = str(fp)
                payload["_json_data"] = j
            except (json.JSONDecodeError, OSError, ValueError):
                payload["_json_file"] = str(fp)
        # 兜底：catalog 自带 ic 字段（晋级时写入的 ic），若 JSON 没读到则用它
        if hist_ic <= 0:
            try:
                hist_ic = abs(float(f.get("ic") or 0.0))
            except (TypeError, ValueError):
                hist_ic = 0.0
        payload["_hist_ic"] = hist_ic
        result.append(payload)
    return result


def compute_energy_curr_ic(factor_row: dict[str, Any], panel: dict[str, Any], common_dates) -> float:
    """对齐 futures_factor_revalidation._compute_current_ic；
    factor_row 若带 _json_data（有 code）则用 JSON，否则用 catalog 的 code。
    """
    # 直接 import 原脚本里的实现，保证与期货路径完全一致，不复制算法
    from scripts.futures_factor_revalidation import _compute_current_ic

    # 组装成 _compute_current_ic 所需形状（带 code/params/evaluation.level_1_backtest.ic 等）
    j = factor_row.get("_json_data")
    if isinstance(j, dict):
        proxy = dict(j)
    else:
        proxy = {
            "factor_id": factor_row.get("factor_id", ""),
            "name": factor_row.get("name", ""),
            "code": factor_row.get("code") or "",
            "params": factor_row.get("params") or {},
            "evaluation": {"level_1_backtest": {"ic": factor_row.get("_hist_ic", 0.0)}},
        }
    return _compute_current_ic(proxy, panel, list(common_dates))


def run_ic_revalidation(
    *, panel, common_dates, ic_threshold: float, drop_threshold: float, log: TeeLogger, csv_path: Path
) -> list[dict[str, Any]]:
    log("=" * 72)
    log(
        "[B] IC 退化重验证（Energy Dry-run）：ic_threshold=%.4f drop_threshold=%.2f days=%d",
        ic_threshold,
        drop_threshold,
        300,
    )
    log("=" * 72)

    factors = _load_energy_active_elite_with_hist(set(panel.keys()))
    log(f"[B] 待审因子: {len(factors)} 个（market=energy active elite）")
    log(f"[B] 面板: {len(panel)} 个品种 × {len(common_dates)} 交易日")

    results: list[dict[str, Any]] = []
    n_ok = n_warn = n_critical = 0
    for i, f in enumerate(factors, 1):
        fid = f.get("factor_id", "")
        name = f.get("name", fid)
        hist_ic = float(f.get("_hist_ic") or 0.0)
        try:
            curr_ic = compute_energy_curr_ic(f, panel, common_dates)
        except Exception as e:  # noqa: BLE001
            curr_ic = float("nan")
            f["_err"] = f"{type(e).__name__}: {e}"
        if not np.isfinite(curr_ic):
            curr_ic = 0.0
        curr_ic_abs = abs(curr_ic)
        ic_drop = 1.0 - (curr_ic_abs / max(hist_ic, 1e-10)) if hist_ic > 1e-10 else 0.0
        is_weak = curr_ic_abs < ic_threshold
        is_dropped = ic_drop > drop_threshold and hist_ic > ic_threshold
        if is_weak and is_dropped:
            status = "CRITICAL"
            n_critical += 1
        elif is_weak or is_dropped:
            status = "WARN"
            n_warn += 1
        else:
            status = "OK"
            n_ok += 1

        reasons: list[str] = []
        if is_weak:
            reasons.append(f"|IC|<{ic_threshold}")
        if is_dropped:
            reasons.append(f"降幅>{drop_threshold:.0%}")
        if f.get("_err"):
            reasons.append(f"计算异常:{f['_err']}")

        rec = {
            "factor_id": fid,
            "name": name,
            "status": status,
            "hist_ic": round(hist_ic, 6),
            "curr_ic": round(curr_ic_abs, 6),
            "ic_drop": round(ic_drop, 6),
            "is_weak": is_weak,
            "is_dropped": is_dropped,
            "decision_dryrun": {
                "OK": "retain",
                "WARN": "shadow (观察)",
                "CRITICAL": "retire (建议淘汰)",
            }[status],
            "reasons": "+".join(reasons) if reasons else "—",
        }
        results.append(rec)
        if i % 5 == 0 or i == len(factors) or status != "OK":
            icon = {"OK": "✅", "WARN": "⚠️", "CRITICAL": "🔴"}.get(status, "❓")
            log(
                f"      [{i}/{len(factors)}] {icon} {name}({fid}): "
                f"curr|IC|={curr_ic_abs:.4f} hist|IC|={hist_ic:.4f} 降幅={ic_drop:.1%} "
                f"-> {rec['decision_dryrun']} {'(' + rec['reasons'] + ')' if rec['reasons'] != '—' else ''}"
            )

    log(f"[B] 汇总: ✅OK={n_ok} ⚠️WARN={n_warn} 🔴CRITICAL={n_critical}")

    # CSV 输出
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = (
            list(results[0].keys())
            if results
            else [
                "factor_id",
                "name",
                "status",
                "hist_ic",
                "curr_ic",
                "ic_drop",
                "is_weak",
                "is_dropped",
                "decision_dryrun",
                "reasons",
            ]
        )
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow(r)
    log(f"[B] CSV 已写: {csv_path}")
    return results


# ──────────────────────────────────────────────────────────
# A 路：reaudit 新标准
# ──────────────────────────────────────────────────────────


def run_reaudit_energy(*, days: int, log: TeeLogger, raw_json_path: Path) -> dict[str, Any]:
    log("=" * 72)
    log(f"[A] reaudit 新标准 Q1-Q10 全量重审（Energy Dry-run, apply=False, days={days}）")
    log("=" * 72)

    from fts.cli import _prepare_futures_data
    from fts.data_futures import ENERGY_CHAIN_SYMBOLS
    from fts.monitor.reaudit import run_reaudit

    trace_id = _generate_trace_id("reaudit")
    log(f"[A] trace_id={trace_id}")
    log(f"[A] 准备专属面板（ENERGY_CHAIN_SYMBOLS 12 品种 × {days} 天） ...")
    panel, common_dates, fwd_ret = _prepare_futures_data(days=days, symbols=list(ENERGY_CHAIN_SYMBOLS))
    log(f"[A] 面板: {len(panel)} 品种 × {len(common_dates)} 交易日")

    report = run_reaudit(
        market="energy",
        days=days,
        trace_id=trace_id,
        apply=False,  # 🔒 关键：Dry-run
        factor_ids=None,
        panel=panel,
        common_dates=common_dates,
        fwd_ret=fwd_ret,
        out_json=False,  # 报告输出统一由我们控制到 reports/energy_chain/...
    )

    counts = report.counts or {}
    log(f"[A] 处理 {report.total} 个因子")
    log(
        f"[A] 汇总: retain={counts.get('retain', 0)} shadow={counts.get('shadow', 0)} "
        f"retire={counts.get('retire', 0)} error={counts.get('error', 0)}"
    )

    # 写一份专属 JSON（避免与 futures 混）
    raw_json_path.parent.mkdir(parents=True, exist_ok=True)
    raw_json_path.write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "generated_at": report.generated_at,
                "total": report.total,
                "counts": counts,
                "results": report.results,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    log(f"[A] 明细 JSON: {raw_json_path}")
    return {"trace_id": trace_id, "report": report, "counts": counts, "results": report.results}


# ──────────────────────────────────────────────────────────
# C 路：FactorInspector Sharpe 血缘退化
# ──────────────────────────────────────────────────────────


def run_inspector_sharpe_deg(*, log: TeeLogger, threshold: float) -> dict[str, Any]:
    log("=" * 72)
    log(f"[C] FactorInspector Sharpe 血缘退化检测（Dry-run, threshold={threshold}）")
    log("=" * 72)
    from fts.factor_engine.factor_inspector import FactorInspector

    insp = FactorInspector(market="energy")
    trace_id = _generate_trace_id("inspector")
    log(f"[C] trace_id={trace_id}")

    result = insp.inspect_and_downgrade(
        threshold=threshold,
        market="energy",
        commit=False,  # 🔒 关键：Dry-run
    )
    log(
        f"[C] audited={result.get('audited_count', '?')} "
        f"degraded_detected={result.get('degraded_count', len(result.get('degraded_factors', [])))} "
        f"downgraded={result.get('downgraded_count', 0)}(应为0,Dry-run) "
        f"skipped={result.get('skipped_count', 0)} error={result.get('error_count', 0)}"
    )
    for d in result.get("degraded_factors", []) or []:
        log(
            f"      🔻 {d.get('factor_name', '?')}({d.get('factor_id', '?')}): "
            f"degradation_score={d.get('degradation_score', 0.0):.3f} "
            f"recommendation={d.get('recommendation', '')}"
        )
    return result


# ──────────────────────────────────────────────────────────
# 汇总报告
# ──────────────────────────────────────────────────────────


def write_summary_md(
    path: Path,
    *,
    today: str,
    elapsed: float,
    a_counts: dict,
    b_stats: dict,
    c_counts: dict,
    b_rows: list[dict[str, Any]],
    a_raw: list[dict[str, Any]],
    c_deg: list[dict[str, Any]],
    panel_n: int,
    dates_n: int,
) -> None:
    lines: list[str] = []

    def w(s=""):
        lines.append(s)

    w(f"# 能源链精英因子 · 周期质检（退化检测）Dry-run 汇总 — {today}")
    w()
    w(f"- 生成时间: {datetime.now().isoformat()}")
    w(f"- 耗时: {elapsed:.1f}s")
    w(f"- 品种池: ENERGY_CHAIN_SYMBOLS × {panel_n} 个，共同交易日 {dates_n}")
    w("- 判据（默认阈值，Dry-run 仅建议）：")
    w("  - IC 退化：|IC|<0.02 或 IC 降幅>30%")
    w("  - Sharpe 退化：Sharpe 相对变化 < -20%")
    w("  - reaudit：evaluation + verifier + audit + robustness 全通过=retain")
    w()

    # 统计总览
    w("## 一、三路检测总览")
    w()
    w("| 路径 | 总因子 | retain/OK | 观察(warn/shadow) | 建议淘汰(retire/critical) | 异常/错 |")
    w("|---|---:|---:|---:|---:|---:|")
    a_retain = a_counts.get("retain", 0)
    a_shadow = a_counts.get("shadow", 0)
    a_retire = a_counts.get("retire", 0)
    a_err = a_counts.get("error", 0)
    a_total = sum(a_counts.values()) or "-"
    w(f"| [A] reaudit Q1-Q10 | {a_total} | {a_retain} | {a_shadow} | {a_retire} | {a_err} |")

    b_ok = b_stats.get("ok", 0)
    b_warn = b_stats.get("warn", 0)
    b_crit = b_stats.get("critical", 0)
    b_total = b_ok + b_warn + b_crit
    w(f"| [B] IC 退化 | {b_total} | {b_ok} | {b_warn} | {b_crit} | — |")

    c_total = c_counts.get("audited_count", 0)
    c_deg_n = c_counts.get("degraded_count", 0)
    c_er = c_counts.get("error_count", 0)
    w(f"| [C] Sharpe 血缘 | {c_total} | {max(c_total - c_deg_n - c_er, 0)} | — | {c_deg_n} | {c_er} |")
    w()

    # 并集建议淘汰/观察
    w("## 二、建议降级/淘汰（并集）")
    w()
    w("> 注：Dry-run 仅汇总建议，未执行任何 DuckDB 回写 / JSON 移除。")
    w("> 若任意一路命中 retire/critical/degraded 即进入本清单。")
    w()
    flagged: dict[str, dict[str, Any]] = {}

    def mark(fid, name, channel, detail):
        if fid not in flagged:
            flagged[fid] = {"name": name or fid, "channels": [], "details": []}
        flagged[fid]["channels"].append(channel)
        flagged[fid]["details"].append(detail)

    for r in b_rows:
        if r["status"] in ("CRITICAL", "WARN"):
            mark(
                r["factor_id"],
                r["name"],
                "B-IC",
                f"{r['status']}:hist={r['hist_ic']:.4f} curr={r['curr_ic']:.4f} drop={r['ic_drop']:.1%} {r['reasons']}",
            )
    for r in a_raw:
        dec = r.get("decision")
        if dec in ("shadow", "retire", "error"):
            mark(
                r.get("factor_id", "?"),
                r.get("name"),
                f"A-reaudit:{dec}",
                f"ev_passed={r.get('evaluation_passed')} vr={r.get('verifier_passed')} ar={r.get('audit_passed')} rr={r.get('robustness_passed')} err={r.get('error') or ''}",
            )
    for r in c_deg or []:
        mark(
            r.get("factor_id", "?"),
            r.get("factor_name"),
            "C-Sharpe",
            f"score={r.get('degradation_score', 0.0):.3f} rec={r.get('recommendation', '')}",
        )

    if not flagged:
        w("(本次三路 Dry-run 均未触发建议降级/淘汰)")
    else:
        w("| factor_id | 名称 | 命中路数 | 命中通道 | 证据摘要 |")
        w("|---|---|---:|---|---|")
        for fid, info in sorted(flagged.items(), key=lambda kv: (-len(kv[1]["channels"]), kv[0])):
            chs = " + ".join(info["channels"])
            det = " \\| ".join(info["details"])
            w(f"| {fid} | {info['name']} | {len(info['channels'])} | {chs} | {det} |")
    w()

    # 完整 IC 结果 Top/Bottom 20
    w("## 三、IC 当前值 Top 20 / Bottom 20")
    w()
    if b_rows:
        sorted_b = sorted(b_rows, key=lambda x: -x["curr_ic"])
        w("### Top 20 curr|IC|")
        w()
        w("| # | 因子 | curr\\|IC\\| | hist\\|IC\\| | 降幅 | 状态 |")
        w("|---:|---|---:|---:|---:|---|")
        for i, r in enumerate(sorted_b[:20], 1):
            w(
                f"| {i} | {r['name']}({r['factor_id']}) | {r['curr_ic']:.4f} | {r['hist_ic']:.4f} | {r['ic_drop']:.1%} | {r['status']} |"
            )
        w()
        w("### Bottom 20 curr|IC|（排除 CRITICAL 即最需关注）")
        w()
        w("| # | 因子 | curr\\|IC\\| | hist\\|IC\\| | 降幅 | 状态 |")
        w("|---:|---|---:|---:|---:|---|")
        for i, r in enumerate(sorted_b[-20:], 1):
            w(
                f"| {i} | {r['name']}({r['factor_id']}) | {r['curr_ic']:.4f} | {r['hist_ic']:.4f} | {r['ic_drop']:.1%} | {r['status']} |"
            )
    w()

    # 行动建议
    w("## 四、下一步")
    w()
    if flagged:
        w(f"1. 本报告共识别 **{len(flagged)}** 个候选退化因子，建议人工复核：")
        for fid, info in sorted(flagged.items(), key=lambda kv: (-len(kv[1]["channels"]), kv[0])):
            w(f"   - [{len(info['channels'])}路] `{fid}` {info['name']} ← {', '.join(info['channels'])}")
        w("2. 人工确认后，回复 `--apply`，系统将按下列规则落库：")
    else:
        w("1. 本次 Dry-run 零命中，所有 energy 活跃精英因子通过三路质检。")
        w("2. 如仍要执行落库生成状态变更记录（全员 retain 标记），回复 `--apply retain-all`。")
    w(
        "   - CRITICAL / retire → `is_elite=false, status=degraded/retired` + 从 energy_chain_elite JSON 移入 _deprecated"
    )
    w("   - WARN / shadow → `status=shadow`（保留 is_elite=true，观察池）")
    w("   - OK / retain → 追加 revalidation/reaudit/inspector 三次留痕")
    w()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ──────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=300, help="回溯交易日（默认 300）")
    ap.add_argument("--ic-threshold", type=float, default=0.02, help="B 路 IC 绝对值下限（默认 0.02）")
    ap.add_argument("--drop-threshold", type=float, default=0.30, help="B 路 IC 降幅比例阈值（默认 0.30）")
    ap.add_argument("--sharpe-drop", type=float, default=-0.20, help="C 路 Sharpe 相对变化阈值（默认 -0.20）")
    ap.add_argument(
        "--date", type=str, default=date.today().isoformat(), help="报告子目录日期（YYYY-MM-DD，默认当日；对齐 QA 用）"
    )
    args = ap.parse_args()

    today = args.date
    out_dir = REPORTS_ROOT / "energy_chain" / today / "qa"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    overall_log = TeeLogger(out_dir / "energy_degradation_dryrun_console.log")

    overall_log.log("=== 能源链因子退化检测（Dry-run）===")
    overall_log.log(f"日期目录: {today} | 输出: {out_dir}")
    overall_log.log(
        f"参数: days={args.days} ic_threshold={args.ic_threshold} "
        f"drop_threshold={args.drop_threshold} sharpe_drop={args.sharpe_drop}"
    )
    overall_log.log()

    # 先准备一次公共面板（A 路也会准备，但可复用）
    from fts.cli import _prepare_futures_data
    from fts.data_futures import ENERGY_CHAIN_SYMBOLS

    overall_log.log("[0/3] 准备能源链公共面板 ...")
    panel, common_dates, _fwd_ret = _prepare_futures_data(days=args.days, symbols=list(ENERGY_CHAIN_SYMBOLS))
    overall_log.log(f"[0/3] 面板就绪: {len(panel)} 品种 × {len(common_dates)} 交易日")
    overall_log.log()

    # [A] reaudit
    a_log = TeeLogger(out_dir / f"reaudit_energy_elite_{today}.log")
    try:
        a_res = run_reaudit_energy(
            days=args.days,
            log=a_log,
            raw_json_path=out_dir / f"reaudit_energy_elite_{today}.json",
        )
    except Exception as e:  # noqa: BLE001
        a_log.log(f"[A] 执行异常: {type(e).__name__}: {e}")
        import traceback

        a_log.log(traceback.format_exc())
        a_res = {"trace_id": "", "counts": {}, "results": []}
    finally:
        a_log.close()
    overall_log.log(f"[A] 完成 → counts={a_res.get('counts')}")
    overall_log.log()

    # [B] IC 退化
    b_log = TeeLogger(out_dir / f"degradation_revalidation_energy_{today}.log")
    try:
        b_rows = run_ic_revalidation(
            panel=panel,
            common_dates=common_dates,
            ic_threshold=args.ic_threshold,
            drop_threshold=args.drop_threshold,
            log=b_log,
            csv_path=out_dir / f"degradation_revalidation_energy_{today}.csv",
        )
    except Exception as e:  # noqa: BLE001
        b_log.log(f"[B] 执行异常: {type(e).__name__}: {e}")
        import traceback

        b_log.log(traceback.format_exc())
        b_rows = []
    finally:
        b_log.close()
    b_stats = {
        "ok": sum(1 for r in b_rows if r["status"] == "OK"),
        "warn": sum(1 for r in b_rows if r["status"] == "WARN"),
        "critical": sum(1 for r in b_rows if r["status"] == "CRITICAL"),
    }
    overall_log.log(f"[B] 完成 → stats={b_stats}")
    overall_log.log()

    # [C] Inspector
    c_log = TeeLogger(out_dir / f"inspector_degradation_energy_{today}.log")
    try:
        c_res = run_inspector_sharpe_deg(
            log=c_log,
            threshold=args.sharpe_drop,
        )
    except Exception as e:  # noqa: BLE001
        c_log.log(f"[C] 执行异常: {type(e).__name__}: {e}")
        import traceback

        c_log.log(traceback.format_exc())
        c_res = {
            "audited_count": 0,
            "degraded_count": 0,
            "error_count": 0,
            "degraded_factors": [],
            "downgraded_count": 0,
            "skipped_count": 0,
        }
    finally:
        c_log.close()
    c_counts = {
        "audited_count": c_res.get("audited_count", c_res.get("total_audited", 0)),
        "degraded_count": c_res.get("degraded_count", len(c_res.get("degraded_factors", []) or [])),
        "error_count": c_res.get("error_count", 0),
    }
    overall_log.log(f"[C] 完成 → counts={c_counts}")
    overall_log.log()

    elapsed = time.time() - t0
    summary_path = out_dir / f"energy_degradation_summary_{today}.md"
    write_summary_md(
        summary_path,
        today=today,
        elapsed=elapsed,
        a_counts=a_res.get("counts", {}),
        b_stats=b_stats,
        c_counts=c_counts,
        b_rows=b_rows,
        a_raw=a_res.get("results", []),
        c_deg=c_res.get("degraded_factors", []) or [],
        panel_n=len(panel),
        dates_n=len(common_dates),
    )
    overall_log.log(f"[*] 汇总 MD: {summary_path}")
    overall_log.log(f"[*] 总耗时: {elapsed:.1f}s")
    overall_log.log()
    overall_log.log("🔒 Dry-run 声明：本次执行未更新 DuckDB、未移动/删除任何 energy_chain_elite JSON、未创建 _deprecated。")
    overall_log.log("   请人工查看 energy_degradation_summary_*.md → 确认后回复 '--apply' 进入落库阶段。")

    overall_log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
