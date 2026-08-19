"""review_legacy_factors.py — P0 存量因子集中重审管道（plans/57 §6.8，新系统首轮资产填充）。

8 阶段管道（本脚本落地 Stage 0/5/6，Stage 1-4/7 由 L2 评估链/子链画像组件接管并回填）：

  Stage 0 清点分层: 按现状态分层（L1 active / L2 shadow / L3 degraded / L4 deleted）
      + 覆盖子链分组，生成重审清单（trace_id 贯穿，FactorRepository 查询）
  Stage 5 统计护栏: 362+ 因子集中重审 → 分族 FDR/BH 校正（复检/转正/恢复三族独立），
      audit 抽查（promote 100% / observe 30% / retire 10%）
  Stage 6 结论落库: promote / observe / retire 按差异化标准（基础 IC/IR 最低门槛 +
      FDR q≤0.05），dry-run 灰度（FTS_REVIEW_LEGACY_APPLY=1 才落库，对齐 FTS_ENERGY_QA_REVIEW_APPLY 模式）

差异化评估标准（§6.8）: 旧截面 IC 标准升级为"策略输入"视角——通用因子看状态识别
贡献（KW/ΔAUC/领先性），特异因子看品种特异显著性（三护栏）；基础 IC/IR 保留为
最低门槛（防纯噪音混入）。Stage 3/4 的完整评估依赖 L2 评估链，本脚本以 catalog
既有评估字段（ic/icir/sharpe）作为基础门槛，并输出待深审清单。

用法:
  python scripts/review_legacy_factors.py                # dry-run 报告（不落库）
  python scripts/review_legacy_factors.py --apply        # 落库（FTS_REVIEW_LEGACY_APPLY=1 等效）
  python scripts/review_legacy_factors.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

logger = logging.getLogger(__name__)

# 重审族（§6.8.2）：现状态 → 族名 / 检验问题 / 期望保留率
FAMILY = {
    "active": ("L1 复检", 0.65),      # 期望保留率 ~60-80%（§6.8 诚实声明）
    "shadow": ("L2 转正", 0.10),      # 影子期转正 5-10%
    "degraded": ("L3 恢复", 0.05),    # 恢复 <5%
    "deleted": ("L4 豁免", 1.0),
}
FDR_ALPHA = 0.05

# 基础最低门槛（防纯噪音混入；Stage 3 差异化标准接入后为辅助）
MIN_IC_BASELINE = 0.0
MIN_ICIR_BASELINE = 0.0


# ─── Stage 0：清点分层 ─────────────────────────────────────


def inventory_legacy_factors(
    market: str = "energy",
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """从因子资产库清点存量因子并按族分层（§6.8 Stage 0）。"""
    from fts.factor_engine.factor_db.repository import FactorRepository

    repo = FactorRepository(market=market, db_path=db_path)
    factors: list[dict[str, Any]] = []
    try:
        factors = repo.list_factors(market=market, limit=10_000)
    except Exception:  # noqa: BLE001 — 库不可用时返回空，调用方告警
        logger.warning("因子资产库清点失败（market=%s）", market)
    return factors


def stratify(factors: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按现状态分层（族）。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for f in factors:
        out.setdefault(f.get("status", "unknown"), []).append(f)
    return out


# ─── Stage 5：FDR/BH 校正 ──────────────────────────────────


def bh_correction(p_values: Sequence[float], alpha: float = FDR_ALPHA) -> dict[str, Any]:
    """Benjamini-Hochberg FDR 校正（分族调用，防 362 集中重审放大假阳性）。

    qᵢ = pᵢ × m / rankᵢ（rank 升序）；q ≤ alpha 通过。

    :param p_values: 单族因子主检验 p 值（每因子一个，避免 IC/KW/ΔAUC 多 p 混入）
    :param alpha: FDR 阈值（默认 0.05）
    :return: {"passed": np.ndarray(bool), "q_values": np.ndarray, "alpha": alpha}
    """
    p = np.asarray([float(x) for x in p_values], dtype=float)
    m = len(p)
    if m == 0:
        return {"passed": np.zeros(0, dtype=bool), "q_values": np.zeros(0), "alpha": alpha}
    order = np.argsort(p)
    ranked = np.arange(1, m + 1)
    q = np.full(m, np.nan)
    q[order] = np.minimum.accumulate(p[order] * m / ranked[::-1][::-1])
    # 单调化（确保 q 单调非降）
    q_sorted = q[order]
    for i in range(m - 2, -1, -1):
        q_sorted[i] = min(q_sorted[i], q_sorted[i + 1])
    q[order] = q_sorted
    return {"passed": q <= alpha, "q_values": q, "alpha": alpha}


def audit_sample(factors: Sequence[dict[str, Any]], conclusion: dict[str, str]) -> dict[str, int]:
    """audit 抽查（§6.8.2）：promote 100% 必审 / observe 30% / retire 10%。"""
    from collections import Counter

    cnt = Counter(conclusion.values())
    audit = {
        "promote_total": int(cnt.get("promote", 0)),
        "promote_audit": int(cnt.get("promote", 0)),  # 100% 必审
        "observe_audit": int(round(cnt.get("observe", 0) * 0.30)),
        "retire_audit": int(round(cnt.get("retire", 0) * 0.10)),
    }
    return audit


# ─── Stage 6：结论落库 ─────────────────────────────────────


def build_conclusion(
    factors: Sequence[dict[str, Any]],
    family_p_values: dict[str, list[float]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """差异化标准结论（§6.8）：基础 IC/IR 门槛 + 分族 FDR 通过。

    :param factors: 存量因子（含 factor_id/status/ic/icir/sharpe）
    :param family_p_values: {族: [p 值]}（每因子一个主检验 p；Stage 3 评估产出）
    :return: ({factor_id: conclusion}, {族: FDR 结果})
    """
    layers = stratify(factors)
    conclusion: dict[str, str] = {}
    fdr_results: dict[str, dict[str, Any]] = {}
    for status, members in layers.items():
        family_name, _ = FAMILY.get(status, ("unknown", 0.05))
        p = family_p_values.get(status, [0.5] * len(members))
        if len(p) < len(members):
            p = p + [0.5] * (len(members) - len(p))
        bh = bh_correction(p[: len(members)])
        fdr_results[status] = {
            "family": family_name,
            "n": len(members),
            "passed_fdr": int(bh["passed"].sum()),
            "alpha": bh["alpha"],
        }
        for i, f in enumerate(members):
            fid = f["factor_id"]
            ic = float(f.get("ic") or 0.0)
            icir = float(f.get("icir") or 0.0)
            if status == "deleted":
                conclusion[fid] = "retire"  # 豁免（除非 scope 独特，见 §6.8 L4）
            elif bh["passed"][i] and ic >= MIN_IC_BASELINE and icir >= MIN_ICIR_BASELINE:
                if status == "active":
                    conclusion[fid] = "promote"  # 复检保留
                elif status == "shadow":
                    conclusion[fid] = "promote"  # 影子转正
                else:  # degraded
                    conclusion[fid] = "promote"  # 误杀恢复
            elif status == "active":
                conclusion[fid] = "observe"  # 复检未过 → 降级观察
            else:
                conclusion[fid] = "retire"
    return conclusion, fdr_results


def apply_conclusions(
    conclusions: dict[str, str],
    market: str = "energy",
    db_path: Optional[str] = None,
    trace_id: str = "",
) -> dict[str, int]:
    """结论落库（Stage 6）：promote→active / observe→shadow / retire→retired。"""
    from fts.factor_engine.factor_db.repository import FactorStatusRepository

    status_map = {"promote": "active", "observe": "shadow", "retire": "retired"}
    repo = FactorStatusRepository(db_path=db_path, market=market)
    counts: dict[str, int] = {}
    try:
        for fid, conclusion in conclusions.items():
            target = status_map.get(conclusion)
            if not target:
                continue
            repo.log_transition(
                fid,
                from_status="",
                to_status=target,
                reason=f"P0 存量因子集中重审（plans/57 §6.8, {trace_id}）",
                snapshot={"conclusion": conclusion},
            )
            counts[target] = counts.get(target, 0) + 1
    finally:
        repo.close()
    return counts


# ─── 报告 ──────────────────────────────────────────────────


def render(report: dict[str, Any]) -> str:
    lines = ["P0 存量因子集中重审（plans/57 §6.8）— " +
             ("DRY-RUN（未落库）" if not report["applied"] else "已落库"), "=" * 78]
    for status, meta in report["stratify"].items():
        family, _ = FAMILY.get(status, ("unknown", 0.05))
        lines.append(f"  {status:<10} {meta['n']:>4} 个  [族: {family}]")
    lines.append("-" * 78)
    for status, fr in report["fdr"].items():
        lines.append(f"  {status:<10} FDR 通过 {fr['passed_fdr']}/{fr['n']} (q≤{fr['alpha']})")
    lines.append("-" * 78)
    from collections import Counter

    c = Counter(report["conclusions"].values())
    lines.append(f"结论: promote={c.get('promote', 0)} observe={c.get('observe', 0)} "
                 f"retire={c.get('retire', 0)}")
    audit = report["audit"]
    lines.append(f"audit 抽查: promote {audit['promote_audit']}/{audit['promote_total']}（100% 必审）, "
                 f"observe {audit['observe_audit']}, retire {audit['retire_audit']}")
    if report["applied"]:
        lines.append(f"已落库: {report['applied_counts']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", type=str, default="energy")
    ap.add_argument("--apply", action="store_true", help="正式落库（等效 FTS_REVIEW_LEGACY_APPLY=1）")
    ap.add_argument("--json", type=str, default="")
    ap.add_argument("--pvalues", type=str, default="", help="可选：{status: [p值]} JSON 文件")
    args = ap.parse_args()

    apply_enabled = args.apply or os.environ.get("FTS_REVIEW_LEGACY_APPLY", "0") == "1"
    trace_id = f"fts.review_legacy.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    factors = inventory_legacy_factors(market=args.market)
    layers = stratify(factors)
    stratify_report = {s: {"n": len(m)} for s, m in layers.items()}
    if not factors:
        logger.warning("存量因子清点为空（market=%s）——确认因子资产库存在", args.market)

    family_p: dict[str, list[float]] = {}
    if args.pvalues:
        family_p = json.loads(Path(args.pvalues).read_text(encoding="utf-8"))
    conclusions, fdr = build_conclusion(factors, family_p)
    audit = audit_sample(factors, conclusions)

    report: dict[str, Any] = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "market": args.market,
        "stratify": stratify_report,
        "fdr": fdr,
        "conclusions": conclusions,
        "audit": audit,
        "applied": False,
        "applied_counts": {},
    }
    if apply_enabled and factors:
        report["applied"] = True
        report["applied_counts"] = apply_conclusions(conclusions, market=args.market, trace_id=trace_id)
    print(render(report))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
