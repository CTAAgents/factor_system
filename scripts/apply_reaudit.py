"""
FTS 重审结果处置回写脚本（reaudit → DuckDB）。

根据 scripts/reaudit_futures_elite.py 的输出 JSON 对因子库做处置:

    retain  → status 保持 active，记录 reaudited_at（metadata 追加 reaudit 记录）
    shadow  → metadata 追加 shadow_pool（观察期 5 交易日，L3 暂不纳入组合）
    retire  → status 置 retired + factor_status_history 留痕（from=active, to=retired）
    error   → 不处置，仅汇总

用法:
    python scripts/apply_reaudit.py memory/logs/evolution/futures/reaudit_20260813.json [--dry-run]

trace_id: fts.reaudit.apply.{ts}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _add_shadow_pool(now: datetime | None = None) -> dict[str, str]:
    """构造影子池观察标记（与 evolution_futures._build_shadow_pool 同构）。"""
    import numpy as np

    now = now or datetime.now()
    end = np.busday_offset(now.date(), 5, roll="forward")
    observe_until = datetime.combine(end.astype(object), datetime.min.time())
    return {
        "promoted_at": now.isoformat(),
        "observe_trading_days": 5,
        "observe_until": observe_until.isoformat(),
        "reason": "reaudit_robustness_failed",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("result_json", type=Path, help="reaudit 输出 JSON")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    ap.add_argument("--only-retire", action="store_true", help="只处理 retire 分支（补跑用）")
    args = ap.parse_args()

    data = json.loads(args.result_json.read_text(encoding="utf-8"))
    results = data["results"]
    if args.only_retire:
        results = [r for r in results if r["decision"] == "retire"]
    from collections import Counter

    counts = Counter(r["decision"] for r in results)
    print(f"载入结果 {len(results)} 条: {dict(counts)}")
    if args.dry_run:
        print("[dry-run] 不写库")
        return 0

    from fts.factor_engine.factor_db.repository import FactorRepository, FactorStatusRepository

    repo = FactorRepository(market="futures")
    srepo = FactorStatusRepository(market="futures")
    ok = {"retain": 0, "shadow": 0, "retire": 0}
    errors: list[str] = []
    try:
        for r in results:
            fid = r["factor_id"]
            decision = r["decision"]
            try:
                f = repo.get_factor(fid)
                if not f:
                    errors.append(f"{fid} 不存在")
                    continue
                meta = f.get("metadata") or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                meta["reaudit"] = {
                    "at": r.get("processed_at"),
                    "trace_id": r.get("trace_id"),
                    "decision": decision,
                    "ic": r.get("ic"),
                    "sharpe": r.get("sharpe"),
                    "audit_passed": r.get("audit_passed"),
                    "audit_failures": r.get("audit_failures", []),
                    "robustness_pass_rate": r.get("robustness_pass_rate"),
                    "grade": r.get("grade"),
                }
                if decision == "retain":
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid, "active", "active",
                        f"新标准全量重审通过（reaudit {r.get('trace_id','')}）",
                        snapshot={"reaudit": meta["reaudit"]},
                    )
                    ok["retain"] += 1
                elif decision == "shadow":
                    meta["shadow_pool"] = _add_shadow_pool()
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid, "active(shadow)", "active(shadow)",
                        f"重审鲁棒性未达标，降级观察池（robust={r.get('robustness_pass_rate')}）",
                        snapshot={"reaudit": meta["reaudit"], "shadow_pool": meta["shadow_pool"]},
                    )
                    ok["shadow"] += 1
                elif decision == "retire":
                    srepo.update_factor_status(fid, "retired")
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid, "active", "retired",
                        f"重审不合格淘汰（audit={r.get('audit_passed')} failed={r.get('audit_failures')}）",
                        snapshot={"reaudit": meta["reaudit"]},
                    )
                    ok["retire"] += 1
                # error 不处置
            except Exception as e:  # noqa: BLE001
                errors.append(f"{fid}: {type(e).__name__}: {e}")
    finally:
        repo.close()
        srepo.close()

    print("=" * 60)
    print(f"处置完成: retain={ok['retain']} shadow={ok['shadow']} retire={ok['retire']}")
    if errors:
        print(f"错误 {len(errors)} 条:")
        for e in errors[:20]:
            print(f"  {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
