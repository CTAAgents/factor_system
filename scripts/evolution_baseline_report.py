#!/usr/bin/env python3
"""
scripts/evolution_baseline_report.py — Phase 0 演化基线统计（只读）。

26 号计划（plans/26-autoresearch-evolution-optimization-plan.md）Phase 0：
统计 L2 演化真实数据（memory/evolution/ 下 state.json + traces + tracking），
输出晋升率 / 失败归因分布 / 演化方法分布 / 后段代次产出 / 连续零晋升代数，
为决策门 G1（P0-1 是否按原收益预期推进）与 G3（P1-3 是否实施）提供数据依据。

只读：不修改任何生产数据/配置文件。

用法:
    python scripts/evolution_baseline_report.py [--root memory/evolution] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# ─── 常量 ─────────────────────────────────────────────────

# 失败轨迹文件名模式: l2_{run_hash}_{yyyymmdd}T{hms}_g{gen}_{type}_fct_{...}.json
_TRACE_NAME_RE = re.compile(
    r"^l2_(?P<run_hash>[0-9a-f]+)_(?P<ts>\d{8}T\d{6})_g(?P<gen>\d+)_(?P<ftype>\w+)_fct_",
)

# 失败类型 → 可读归类（Phase 0 归因维度）
FAIL_TYPE_LABEL: dict[str, str] = {
    "audit_fail": "审计未通过(6项强制审计)",
    "ablation_fail": "消融伪相关",
    "robustness_fail": "鲁棒性不足",
    "causal_fail": "因果审查未通过",
    "quality_filtered": "质量评分卡淘汰",
    "verifier_failed": "Verifier 未通过",
    "prefilter": "快速预筛拦截",
    "runtime": "运行时校验失败",
    "seed_verifier": "种子 Verifier 未通过",
}

# state.json 关键字段（用于 run 级统计）
_STATE_FIELDS = (
    "run_id",
    "started_at",
    "last_generation",
    "total_factors_evaluated",
    "total_factors_promoted",
    "tokens_consumed",
    "status",
    "last_error",
    "evolution_method_counts",
)


# ─── 工具 ─────────────────────────────────────────────────


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    """安全加载 JSON；解析失败返回 None（不阻断统计）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _classify_fail_type(raw: str) -> str:
    """将文件名中的原始失败类型映射为可读归类。"""
    return FAIL_TYPE_LABEL.get(raw, raw)


# ─── 统计模块 ─────────────────────────────────────────────


def scan_state_files(root: Path) -> list[dict[str, Any]]:
    """扫描 root 下所有 state.json，返回 run 级统计列表。"""
    runs: list[dict[str, Any]] = []
    for sp in sorted(root.rglob("state.json")):
        data = _safe_load_json(sp)
        if data is None:
            continue
        run: dict[str, Any] = {"path": str(sp.relative_to(root))}
        for key in _STATE_FIELDS:
            run[key] = data.get(key)
        runs.append(run)
    return runs


def scan_traces(root: Path) -> dict[str, Any]:
    """扫描 root 下所有 traces/*.json，聚合失败轨迹统计。

    Returns:
        {
            "total": int,                 # 轨迹总数
            "by_type": {label: count},    # 失败归因分布
            "by_date": {date: count},     # 按运行日期
            "by_run": {parent_trace_id: {"gen_max": int, "count": int}},
            "by_generation": {gen: count},# 按代次分布
            "failed_factor_names": [...]  # 样本（前 20 个被拒因子名）
        }
    """
    by_type: Counter[str] = Counter()
    by_date: Counter[str] = Counter()
    by_run: Counter[str] = Counter()
    run_gen_max: dict[str, int] = {}
    by_generation: Counter[int] = Counter()
    failed_names: list[str] = []

    for fp in sorted(root.rglob("traces/*.json")):
        m = _TRACE_NAME_RE.match(fp.name)
        if m:
            raw_type = m.group("ftype")
            gen = int(m.group("gen"))
            date = m.group("ts")[:8]
            by_type[_classify_fail_type(raw_type)] += 1
            by_date[date] += 1
            by_generation[gen] += 1
            run_key = f"l2_{m.group('run_hash')}"
            by_run[run_key] += 1
            run_gen_max[run_key] = max(run_gen_max.get(run_key, -1), gen)
        data = _safe_load_json(fp)
        if data and isinstance(data, dict) and data.get("factor_name"):
            failed_names.append(str(data["factor_name"]))

    return {
        "total": len(list(root.rglob("traces/*.json"))),
        "by_type": dict(by_type.most_common()),
        "by_date": dict(sorted(by_date.items())),
        "by_run": dict(by_run.most_common()),
        "run_gen_max": run_gen_max,
        "by_generation": dict(sorted(by_generation.items())),
        "failed_factor_names": failed_names[:20],
    }


def count_elite_snapshots(root: Path) -> dict[str, int]:
    """统计 elite 快照数量（tracking 状态分布 + 常见 elite 目录计数）。"""
    result: dict[str, int] = {}
    # elite_tracker 状态分布（memory/evolution/**/tracking/*.json）
    tracking = list(root.rglob("tracking/*.json"))
    status_counts: Counter[str] = Counter()
    for tp in tracking:
        data = _safe_load_json(tp)
        if data:
            status_counts[str(data.get("status", "unknown"))] += 1
    result["tracking_total"] = len(tracking)
    result["tracking_by_status"] = dict(status_counts.most_common())
    # 常见 elite 目录（供参考；仅统计存在的）
    for d in (
        Path("memory/knowledge/factors/futures_elite"),
        Path("memory/knowledge/factors/stocks_elite"),
        Path("memory/evolution/futures/test_elite"),
    ):
        if d.exists():
            result[str(d)] = len(list(d.glob("fct_*.json")))
    return result


def build_report(root: Path) -> dict[str, Any]:
    """聚合全部统计并构建 Phase 0 报告。"""
    runs = scan_state_files(root)
    traces = scan_traces(root)
    elites = count_elite_snapshots(root)

    # 晋升率汇总（有效 run：evaluated > 0）
    evaluated_total = sum(int(r.get("total_factors_evaluated") or 0) for r in runs)
    promoted_total = sum(int(r.get("total_factors_promoted") or 0) for r in runs)
    promote_rate = (promoted_total / evaluated_total) if evaluated_total > 0 else None

    # 无产出连续代数（P1-3 决策依据）：有 evaluated 的 run 中 last_generation 最大值
    active_runs = [r for r in runs if int(r.get("total_factors_evaluated") or 0) > 0]
    max_gen_no_promote = 0
    no_promote_runs = [r for r in active_runs if int(r.get("total_factors_promoted") or 0) == 0]
    if no_promote_runs:
        max_gen_no_promote = max(int(r.get("last_generation") or 0) for r in no_promote_runs)

    return {
        "generated_at": datetime.now().isoformat(),
        "data_root": str(root),
        "state_runs": runs,
        "promote_summary": {
            "runs_total": len(runs),
            "evaluated_total": evaluated_total,
            "promoted_total": promoted_total,
            "promote_rate": promote_rate,
        },
        "max_consecutive_zero_promote_generations": max_gen_no_promote,
        "traces": traces,
        "elites": elites,
    }


# ─── 输出 ─────────────────────────────────────────────────


def render_markdown(report: dict[str, Any]) -> str:
    """渲染人类可读的 Markdown 报告。"""
    lines: list[str] = []
    lines.append("# Phase 0 演化基线统计报告")
    lines.append("")
    lines.append(f"> 生成时间: {report['generated_at']}")
    lines.append(f"> 数据根: `{report['data_root']}`")
    lines.append("")

    # 晋升率
    ps = report["promote_summary"]
    rate = f"{ps['promote_rate']:.2%}" if ps["promote_rate"] is not None else "N/A（无评估记录）"
    lines.append("## 1. 晋升率汇总")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|:-----|:----|")
    lines.append(f"| run 总数 | {ps['runs_total']} |")
    lines.append(f"| 累计评估因子 | {ps['evaluated_total']} |")
    lines.append(f"| 累计晋升因子 | {ps['promoted_total']} |")
    lines.append(f"| **晋升率** | **{rate}** |")
    lines.append(f"| 连续零晋升最大代数 | **{report['max_consecutive_zero_promote_generations']}**（P1-3 决策依据） |")
    lines.append("")

    # 各 run 明细
    lines.append("## 2. 各 run 状态明细")
    lines.append("")
    lines.append("| run_id | 起始 | 代数 | 评估 | 晋升 | 状态 | 方法分布 |")
    lines.append("|:-------|:-----|:-----|:-----|:-----|:-----|:---------|")
    for r in report["state_runs"]:
        mc = r.get("evolution_method_counts")
        mc_str = ""
        if isinstance(mc, dict):
            mc_str = ", ".join(f"{k}={v}" for k, v in mc.items())
        lines.append(
            f"| `{r.get('run_id', '?')}` | {r.get('started_at', '?')[:16]} "
            f"| {r.get('last_generation')} | {r.get('total_factors_evaluated')} "
            f"| {r.get('total_factors_promoted')} | {r.get('status')} | {mc_str} |"
        )
    lines.append("")

    # 失败归因分布
    tr = report["traces"]
    lines.append("## 3. 失败归因分布（traces 文件名归类）")
    lines.append("")
    lines.append(f"失败轨迹总数: {tr['total']}")
    lines.append("")
    lines.append("| 归因 | 数量 | 占比 |")
    lines.append("|:-----|:-----|:-----|")
    for label, cnt in tr["by_type"].items():
        pct = f"{cnt / tr['total']:.1%}" if tr["total"] else "N/A"
        lines.append(f"| {label} | {cnt} | {pct} |")
    lines.append("")

    # 失败轨迹按日期
    lines.append("## 4. 失败轨迹按运行日期")
    lines.append("")
    lines.append("| 日期 | 失败轨迹数 |")
    lines.append("|:-----|:-----------|")
    for date, cnt in tr["by_date"].items():
        lines.append(f"| {date} | {cnt} |")
    lines.append("")

    # 后段代次产出（P1-3）
    lines.append("## 5. 代次分布（失败轨迹按 generation）")
    lines.append("")
    gens = tr["by_generation"]
    if gens:
        total = sum(gens.values())
        lines.append("| generation | 失败轨迹数 | 累计占比 |")
        lines.append("|:-----------|:-----------|:---------|")
        cum = 0
        for g, cnt in sorted(gens.items()):
            cum += cnt
            lines.append(f"| {g} | {cnt} | {cum / total:.1%} |")
        # 后 30% 代次产出占比
        max_gen = max(gens)
        tail = [g for g in gens if g >= int(max_gen * 0.7)]
        tail_cnt = sum(gens[g] for g in tail)
        lines.append("")
        lines.append(f"后 30% 代次（gen ≥ {int(max_gen * 0.7)}）失败轨迹占比: **{tail_cnt / total:.1%}**（越高说明演化主要在后段失败/空转）")
    else:
        lines.append("无代次数据。")
    lines.append("")

    # elite 快照
    lines.append("## 6. elite 快照与追踪")
    lines.append("")
    el = report["elites"]
    lines.append("| 路径 | 因子数 |")
    lines.append("|:-----|:-------|")
    for k, v in el.items():
        if isinstance(v, int):
            lines.append(f"| `{k}` | {v} |")
        elif isinstance(v, dict):
            lines.append(f"| `{k}` | {v} |")
    lines.append("")

    # 决策门结论
    lines.append("## 7. 决策门结论（G1 / G3）")
    lines.append("")
    rate_v = ps["promote_rate"]
    if rate_v is None:
        lines.append("- **G1**：无评估记录，P0-1 无法基于历史数据判定收益；建议先跑通一次演化产出基线。")
    elif rate_v >= 0.05:
        lines.append(f"- **G1**：晋升率 {rate_v:.2%} ≥ 5%，生成质量非首要瓶颈，P0-1 边际收益有限 → 仅执行 P0-2。")
    else:
        lines.append(f"- **G1**：晋升率 {rate_v:.2%} < 5%，瓶颈在生成质量/评估过严 → 按原计划推进 P0-1/P0-2。")
    tail_pct = None
    gens2 = tr.get("by_generation", {})
    if gens2:
        max_gen2 = max(gens2)
        tail_g = [g for g in gens2 if g >= int(max_gen2 * 0.7)]
        tail_pct = sum(gens2[g] for g in tail_g) / sum(gens2.values())
    if tail_pct is not None and tail_pct > 0.5:
        lines.append(f"- **G3**：后 30% 代次失败占比 {tail_pct:.1%} > 50%，演化后段空转严重 → P1-3 值得实施。")
    else:
        lines.append(f"- **G3**：后 30% 代次失败占比 {tail_pct:.1%}，P1-3 收益有限 → 维持关闭并登记原因。")
    lines.append("")
    return "\n".join(lines)


# ─── 入口 ─────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 演化基线统计（只读）")
    parser.add_argument("--root", default="memory/evolution", help="演化数据根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    args = parser.parse_args()

    root = Path(args.root)
    report = build_report(root)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(report))


if __name__ == "__main__":  # pragma: no cover
    main()
