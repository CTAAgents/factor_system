#!/usr/bin/env python3
"""
scripts/audit_failure_breakdown.py — 审计失败子项分布专项排查（只读）。

26 号计划 Phase 0 后续（G1 归因修正）：核对 audit_fail 轨迹中 6/7 项审计的
实际执行与失败分布，判断是否存在门槛过严 / 数据缺失导致的全量误杀
（参考 GAP-073 短样本 OOS 误杀先例）。

输出:
    1. 审计项 passed / failed / skipped 分布
    2. 失败项主导（哪几项贡献了 audit_failed）
    3. 按日期分层（区分 GAP-073 修复前后）
    4. WalkForward 窗口完成数分布（n_windows_completed）

用法:
    python scripts/audit_failure_breakdown.py [--root memory/evolution] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_TRACE_NAME_RE = re.compile(
    r"^l2_(?P<run_hash>[0-9a-f]+)_(?P<ts>\d{8}T\d{6})_g(?P<gen>\d+)_(?P<ftype>\w+)_fct_",
)


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def analyze(root: Path) -> dict[str, Any]:
    """统计 audit_fail 轨迹的审计子项状态分布。"""
    item_status: Counter[str] = Counter()  # (item_name, status)
    item_name_total: Counter[str] = Counter()
    failed_contrib: Counter[str] = Counter()  # 每条 audit_failed 轨迹的 failed 项
    oos_failed_by_date: Counter[str] = Counter()
    wf_windows: Counter[int] = Counter()
    audit_fail_total = 0
    audit_report_pass_rate: list[float] = []
    oos_evidence_samples: list[str] = []
    # GAP-078 修复效果模拟：oos_consistency failed 轨迹按走航窗口数重分类
    oos_failed_total = 0
    oos_failed_skippable = 0  # evaluation.walk_forward 存在且 n_windows<2 → 修复转 skipped
    oos_failed_real = 0  # 其余（walk_forward 缺失或窗口≥2）→ 修复后仍 failed

    for fp in sorted(root.rglob("traces/*.json")):
        m = _TRACE_NAME_RE.match(fp.name)
        if not m or m.group("ftype") != "audit_fail":
            continue
        audit_fail_total += 1
        data = _safe_load_json(fp)
        if data is None:
            continue
        report = data.get("audit_report")
        if not isinstance(report, dict):
            continue
        items = report.get("items", [])
        if not isinstance(items, list):
            continue
        pr = report.get("pass_rate")
        if isinstance(pr, (int, float)):
            audit_report_pass_rate.append(float(pr))
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "?"))
            status = str(it.get("status", "?"))
            item_status[(name, status)] += 1
            item_name_total[name] += 1
            if status == "failed":
                failed_contrib[name] += 1
                if name == "oos_consistency":
                    oos_failed_total += 1
                    date = m.group("ts")[:8]
                    oos_failed_by_date[date] += 1
                    ev = data.get("evaluation")
                    wf = ev.get("walk_forward") if isinstance(ev, dict) else None
                    nw = wf.get("n_windows_completed") if isinstance(wf, dict) else None
                    if isinstance(nw, int) and nw < 2:
                        oos_failed_skippable += 1
                    else:
                        oos_failed_real += 1
                    ev_str = str(it.get("evidence", ""))[:80]
                    if len(oos_evidence_samples) < 15:
                        oos_evidence_samples.append(f"{date} g{m.group('gen')}: {ev_str} (n_windows={nw})")
        # 走航窗口数
        ev = data.get("evaluation")
        if isinstance(ev, dict):
            wf = ev.get("walk_forward")
            if isinstance(wf, dict):
                nw = wf.get("n_windows_completed")
                if isinstance(nw, int):
                    wf_windows[nw] += 1

    return {
        "audit_fail_total": audit_fail_total,
        "item_status": dict(item_status.most_common()),
        "item_name_total": dict(item_name_total.most_common()),
        "failed_contrib": dict(failed_contrib.most_common()),
        "oos_failed_by_date": dict(sorted(oos_failed_by_date.items())),
        "oos_repair_sim": {
            "oos_failed_total": oos_failed_total,
            "skippable": oos_failed_skippable,
            "still_failed": oos_failed_real,
        },
        "wf_windows": dict(sorted(wf_windows.items())),
        "audit_report_pass_rate": {
            "count": len(audit_report_pass_rate),
            "mean": sum(audit_report_pass_rate) / len(audit_report_pass_rate) if audit_report_pass_rate else None,
            "zero": sum(1 for p in audit_report_pass_rate if p == 0.0),
        },
        "oos_evidence_samples": oos_evidence_samples,
    }


def render_markdown(r: dict[str, Any]) -> str:
    """渲染审计子项排查 Markdown 报告。"""
    lines: list[str] = []
    lines.append("# 审计失败子项分布排查报告")
    lines.append("")
    lines.append(f"> 生成: {datetime.now().isoformat()}")
    lines.append(f"> audit_fail 轨迹总数: {r['audit_fail_total']}")
    lines.append("")

    lines.append("## 1. 审计项状态分布（name × status）")
    lines.append("")
    lines.append("| 审计项 | passed | failed | skipped | 合计 |")
    lines.append("|:-------|:-------|:-------|:--------|:-----|")
    names = sorted(r["item_name_total"])
    for name in names:
        p = r["item_status"].get((name, "passed"), 0)
        f = r["item_status"].get((name, "failed"), 0)
        s = r["item_status"].get((name, "skipped"), 0)
        lines.append(f"| {name} | {p} | {f} | {s} | {r['item_name_total'][name]} |")
    lines.append("")

    lines.append("## 2. failed 贡献（每条 audit_failed 轨迹中失败的审计项）")
    lines.append("")
    lines.append("| 审计项 | failed 次数 | 占 audit_fail 轨迹比例 |")
    lines.append("|:-------|:------------|:----------------------|")
    for name, cnt in r["failed_contrib"].items():
        lines.append(f"| {name} | {cnt} | {cnt / r['audit_fail_total']:.1%} |")
    lines.append("")

    lines.append("## 3. oos_consistency failed 按日期（GAP-073 修复 v2.98.0 = 08-10 前后）")
    lines.append("")
    lines.append("| 日期 | oos_consistency failed |")
    lines.append("|:-----|:----------------------|")
    for date, cnt in r["oos_failed_by_date"].items():
        lines.append(f"| {date} | {cnt} |")
    lines.append("")

    lines.append("## 4. WalkForward 窗口完成数分布（n_windows_completed）")
    lines.append("")
    lines.append("| n_windows_completed | 轨迹数 |")
    lines.append("|:--------------------|:-------|")
    for nw, cnt in r["wf_windows"].items():
        lines.append(f"| {nw} | {cnt} |")
    lines.append("")

    pr = r["audit_report_pass_rate"]
    lines.append("## 5. audit_report pass_rate 分布")
    lines.append("")
    lines.append(
        f"- 样本 {pr['count']}，均值 {pr['mean']:.2f}，pass_rate=0 的轨迹 {pr['zero']} "
        f"（占 {pr['zero'] / pr['count']:.1%}）"
    )
    lines.append("")

    sim = r["oos_repair_sim"]
    lines.append("## 6. GAP-078 修复效果模拟（oos_consistency failed 重分类）")
    lines.append("")
    lines.append("| 分类 | 数量 |")
    lines.append("|:-----|:-----|")
    lines.append(f"| oos_consistency failed 总数 | {sim['oos_failed_total']} |")
    lines.append(f"| → 修复后转 skipped（走航 n_windows<2） | **{sim['skippable']}** "
                 f"（{sim['skippable'] / sim['oos_failed_total']:.1%}） |")
    lines.append(f"| → 修复后仍 failed（真实 OOS 不一致/走航缺失） | {sim['still_failed']} "
                 f"（{sim['still_failed'] / sim['oos_failed_total']:.1%}） |")
    lines.append("")

    lines.append("## 7. oos_consistency 失败 evidence 样本（前 15 条）")
    lines.append("")
    for s in r["oos_evidence_samples"]:
        lines.append(f"- `{s}`")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="审计失败子项分布专项排查（只读）")
    parser.add_argument("--root", default="memory/evolution", help="演化数据根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = analyze(Path(args.root))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_markdown(result))


if __name__ == "__main__":  # pragma: no cover
    main()
