"""
FTS 存量期货精英因子新标准全量重审。

背景: 2026-08-11~13 系统升级引入新准入标准（GAP-079 oos 判定修复、
GAP-096 cross_symbol A+C 双机制、鲁棒性审查 11 项测试、质量评分卡）。
存量 active elite 因子（factor_audit_reports=0、factor_quality_scores=0）
未经新标准检验。本脚本对全部 active elite 因子复用演化准入链
（横截面评估 → Verifier → 审计 → 鲁棒性 → 质量评分卡）重新检验。

处置建议规则:
    retain  = evaluation + verifier + audit + robustness 全过
    shadow  = robustness 失败（新标准最严维度，降级观察池重新观察）
    retire  = audit 失败或 evaluation 不合格
    error   = 因子代码不可执行（单独列出，人工核查）

用法:
    python scripts/reaudit_futures_elite.py [--limit N] [--offset N] [--days 750] [--json]
    --limit/--offset 支持分段运行与断点续跑（结果增量追加到结果文件）。

输出:
    memory/logs/evolution/futures/reaudit_{date}.json   (全量结果)
    控制台打印汇总。

trace_id: fts.reaudit.{ts}
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

OUT_DIR = _PROJECT_ROOT / "memory" / "logs" / "evolution" / "futures"


def load_active_elite() -> list[dict[str, Any]]:
    """读取全部 active elite 因子（DuckDB SSOT）。"""
    from fts.factor_engine.factor_db.repository import FactorRepository

    repo = FactorRepository(market="futures")
    try:
        rows = repo.list_factors(
            market="futures", status="active", is_elite=True, limit=10000, sort_by="sharpe", sort_order="desc"
        )
        return rows or []
    finally:
        repo.close()


def build_factor_program(f: dict[str, Any]) -> dict[str, Any] | None:
    """从 catalog 行构造 FactorProgram（code 为空返回 None）。"""
    code = f.get("code") or ""
    if not code:
        return None
    return {
        "factor_id": f["factor_id"],
        "name": f.get("name", f["factor_id"]),
        "code": code,
        "params": f.get("params") or {},
        "family": f.get("family") or "other",
        "market": "futures",
        "economic_logic": f.get("economic_logic") or {},
    }


def summarize_result(record: dict[str, Any]) -> str:
    """按处置规则给单因子判定。"""
    if record.get("error"):
        return "error"
    ev_passed = record["evaluation_passed"]
    vr_passed = record["verifier_passed"]
    ar_passed = record["audit_passed"]
    rr_passed = record["robustness_passed"]
    if ev_passed and vr_passed and ar_passed and rr_passed:
        return "retain"
    if not rr_passed:
        return "shadow"
    return "retire"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个因子（0=全部）")
    ap.add_argument("--offset", type=int, default=0, help="跳过前 N 个因子（断点续跑）")
    ap.add_argument("--days", type=int, default=750, help="回溯天数（与演化同口径，默认 750）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果文件")
    args = ap.parse_args()

    trace_id = f"fts.reaudit.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    print(f"[reaudit] trace_id={trace_id}")

    from fts.cli import _prepare_futures_data, _relaxed_futures_audit_config, _relaxed_futures_quality_config
    from fts.config import get_config
    from fts.factor_engine.evolution_futures import EvolutionLoop
    from fts.factor_engine.seed_pool import SeedPool

    cfg = get_config()

    # ── 数据准备（与演化同口径） ──
    print("[reaudit] 准备期货横截面面板 ...")
    panel, common_dates, fwd_ret = _prepare_futures_data(days=args.days, max_symbols=0)
    first_sym = list(panel.keys())[0]
    print(f"[reaudit] 面板品种={len(panel)} 共同日期={len(common_dates)}")

    loop = EvolutionLoop(
        data=panel[first_sym],
        forward_returns=fwd_ret,
        elite_dir=cfg.get_elite_dir("futures"),
        memory_dir=cfg.memory_dir + "/evolution/futures",
        llm_client=None,
        seed_pool=SeedPool(market="futures"),
        n_trials_micro=10,
        cross_section_data=panel,
        cross_section_dates=common_dates,
        market="futures",
        quality_card_config=_relaxed_futures_quality_config(),
        audit_config=_relaxed_futures_audit_config(),
    )

    factors = load_active_elite()
    total = len(factors)
    if args.limit > 0:
        factors = factors[args.offset : args.offset + args.limit]
    else:
        factors = factors[args.offset :]
    print(f"[reaudit] active elite 总数={total}，本次处理 {len(factors)} 个 (offset={args.offset})")

    results: list[dict[str, Any]] = []
    for i, f in enumerate(factors, 1):
        fid = f["factor_id"]
        name = f.get("name", fid)
        fp = build_factor_program(f)
        rec: dict[str, Any] = {
            "factor_id": fid,
            "name": name,
            "trace_id": trace_id,
            "processed_at": datetime.now().isoformat(),
        }
        if fp is None:
            rec["error"] = "catalog 无 code"
            results.append(rec)
            print(f"[{i}/{len(factors)}] {name:<40} ERROR(无code)")
            continue

        try:
            ev = loop._evaluate_cross_section(fp, trace_id)
            l1 = ev.get("level_1_backtest") or {}
            rec["evaluation_passed"] = bool(ev.get("passed"))
            rec["ic"] = l1.get("ic")
            rec["sharpe"] = l1.get("sharpe")
            rec["icir"] = l1.get("icir")
            rec["failure_reasons"] = ev.get("failure_reasons", [])

            vr = loop.verifier.check(ev)
            rec["verifier_passed"] = bool(vr.get("passed", True))
            rec["verifier_details"] = vr.get("details", {}) if isinstance(vr, dict) else {}

            ar = loop._run_factor_audit(fp, ev, trace_id)
            ar_passed = bool(ar.passed)
            rec["audit_passed"] = ar_passed
            rec["audit_failures"] = [it.name for it in ar.failed_items]
            rec["audit_pass_rate"] = ar.pass_rate

            rr = loop._run_robustness_check(fp, ev, trace_id)
            rsum = (rr or {}).get("summary", {})
            rec["robustness_passed"] = bool(rr.get("passed", True))
            rec["robustness_pass_rate"] = rsum.get("overall_pass_rate")
            rec["robustness_summary"] = rsum

            qi = loop.quality_inspector.inspect(factor=fp, evaluation=ev)
            rec["grade"] = getattr(qi, "grade", None) or (qi.get("grade") if isinstance(qi, dict) else None)
            rec["quality_score"] = getattr(qi, "total_score", None) or (qi.get("total_score") if isinstance(qi, dict) else None)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["error_trace"] = traceback.format_exc(limit=3)

        rec["decision"] = summarize_result(rec)
        results.append(rec)
        status = {"retain": "✅保留", "shadow": "🟡降级观察", "retire": "🔴淘汰", "error": "⚠️异常"}[rec["decision"]]
        print(
            f"[{i}/{len(factors)}] {name:<42} {status} "
            f"ic={rec.get('ic')} sharpe={rec.get('sharpe')} "
            f"audit={rec.get('audit_passed')} robust={rec.get('robustness_pass_rate')} "
            f"grade={rec.get('grade')}"
        )

    # ── 汇总 ──
    from collections import Counter

    counts = Counter(r["decision"] for r in results)
    print("\n" + "=" * 70)
    print(f"[reaudit] 汇总 (本次 {len(results)} 个): retain={counts['retain']} shadow={counts['shadow']} retire={counts['retire']} error={counts['error']}")
    print("=" * 70)

    if args.json:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_fp = OUT_DIR / f"reaudit_{datetime.now().strftime('%Y%m%d')}.json"
        payload: dict[str, Any] = {"trace_id": trace_id, "generated_at": datetime.now().isoformat(), "results": results}
        # 增量追加：同文件已有结果时合并（断点续跑支持）
        if out_fp.exists():
            try:
                old = json.loads(out_fp.read_text(encoding="utf-8"))
                old_ids = {r["factor_id"] for r in old["results"]}
                merged = old["results"] + [r for r in results if r["factor_id"] not in old_ids]
                payload["results"] = merged
            except Exception:  # noqa: BLE001
                pass
        out_fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"[reaudit] 结果已保存: {out_fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
