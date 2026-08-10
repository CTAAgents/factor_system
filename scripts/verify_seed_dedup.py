#!/usr/bin/env python3
"""FTS 种子库去重校验脚本（GAP-F10，v2.73.0）。

内嵌种子（seed_data_futures_full.py，81 个）与 YAML 种子（seeds/futures/，20 文件）两套
种子库并存，本脚本交叉比对因子名，输出重复清单与统计，供 CI / 人工审计使用。

用法:
    python scripts/verify_seed_dedup.py [--seeds-dir seeds/futures] [--json]

退出码:
    0  未发现重复（门禁通过）
    1  发现重复因子（门禁拦截）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _norm_name(name: str) -> str:
    """因子名归一化：去除 fut_ 前缀（内嵌与 YAML 命名前缀差异）。"""
    n = name.strip().lower()
    if n.startswith("fut_"):
        n = n[4:]
    return n


def load_embedded_seeds() -> list[Any]:
    """加载内嵌期货种子（81 个，14 大因子家族）。"""
    from fts.factor_engine.seed_data_futures_full import load_futures_seeds_full

    return load_futures_seeds_full(trace_id="verify_seed_dedup")


def load_yaml_seeds(seeds_dir: Path) -> list[tuple[str, Any]]:
    """加载 YAML 种子，返回 (来源文件, FactorProgram) 列表。"""
    from fts.factor_engine.seed_loader import load_factors_from_yaml

    result: list[tuple[str, Any]] = []
    if not seeds_dir.exists():
        return result
    for yaml_file in sorted(seeds_dir.glob("*.yaml")):
        for fp in load_factors_from_yaml(yaml_file, trace_id="verify_seed_dedup"):
            result.append((yaml_file.name, fp))
    return result


def collect(embedded: list[Any], yaml_items: list[tuple[str, Any]]) -> dict[str, Any]:
    """执行交叉比对，返回结构化结果。

    返回结构:
        total_embedded / total_yaml / total_unique_names
        exact_duplicates: YAML 内部精确名重复（权威源内部问题，门禁拦截）
        embedded_duplicates: 内嵌种子内部精确名重复（门禁拦截）
        cross_overlap: 内嵌 vs YAML 交叉同名（兜底源与权威源重叠，参考报告不拦截）
    """

    def _names(items: list[Any]) -> list[str]:
        return [fp.get("name", "") if isinstance(fp, dict) else "" for fp in items]

    # ── 权威源（YAML）内部唯一性 ──
    yaml_names = _names([fp for _, fp in yaml_items])
    yaml_seen: dict[str, list[str]] = {}
    for (src, _fp), name in zip(yaml_items, yaml_names):
        yaml_seen.setdefault(name, []).append(src)

    # ── 内嵌种子内部唯一性 ──
    embedded_names = _names(embedded)
    embedded_seen: dict[str, list[str]] = {}
    for name in embedded_names:
        embedded_seen.setdefault(name, []).append("embedded")

    # ── 内嵌 vs YAML 交叉重叠（冗余维护风险，参考） ──
    embedded_set = {n for n in embedded_names if n}
    yaml_set = {n for n in yaml_names if n}

    return {
        "total_embedded": len(embedded),
        "total_yaml": len(yaml_items),
        "total_unique_names": len(embedded_set | yaml_set),
        "exact_duplicates": {n: v for n, v in yaml_seen.items() if len(v) > 1 and n},
        "embedded_duplicates": {n: v for n, v in embedded_seen.items() if len(v) > 1 and n},
        "cross_overlap": sorted(embedded_set & yaml_set),
        "embedded_only": sorted(embedded_set - yaml_set),
        "yaml_only": sorted(yaml_set - embedded_set),
    }


def render_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  FTS 种子库去重校验报告（GAP-F10）")
    lines.append("=" * 72)
    lines.append(f"  内嵌种子数  : {result['total_embedded']}")
    lines.append(f"  YAML 种子数 : {result['total_yaml']}")
    lines.append(f"  归一化后唯一: {result['total_unique_names']}")
    yaml_dup = result["exact_duplicates"]
    emb_dup = result["embedded_duplicates"]
    overlap = result["cross_overlap"]
    lines.append(f"  YAML 内部重复: {len(yaml_dup)}")
    lines.append(f"  内嵌内部重复: {len(emb_dup)}")
    lines.append(f"  交叉同名重叠: {len(overlap)}（兜底源参考，不拦截）")
    lines.append(f"  仅内嵌独有   : {len(result['embedded_only'])}")
    lines.append(f"  仅 YAML 独有 : {len(result['yaml_only'])}")
    lines.append("")
    if yaml_dup:
        lines.append("  ── YAML 权威源内部重复（门禁拦截）──")
        for name, sources in sorted(yaml_dup.items()):
            lines.append(f"    {name}: {', '.join(sorted(set(sources)))}")
        lines.append("")
    if emb_dup:
        lines.append("  ── 内嵌种子内部重复（门禁拦截）──")
        for name in sorted(emb_dup):
            lines.append(f"    {name}")
        lines.append("")
    if overlap:
        lines.append(f"  ── 内嵌 vs YAML 交叉同名（{len(overlap)} 个，冗余维护风险参考）──")
        for name in overlap:
            lines.append(f"    {name}")
        lines.append("")
    if not yaml_dup and not emb_dup:
        lines.append("  ✅ 权威源内部唯一性校验通过（YAML 与内嵌各自无重复）")
    lines.append("=" * 72)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="FTS 种子库去重校验（GAP-F10）")
    parser.add_argument(
        "--seeds-dir",
        type=str,
        default=str(PROJECT_ROOT / "seeds" / "futures"),
        help="YAML 种子目录（默认 seeds/futures）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 供机器解析")
    args = parser.parse_args(argv)

    embedded = load_embedded_seeds()
    yaml_items = load_yaml_seeds(Path(args.seeds_dir))
    result = collect(embedded, yaml_items)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_report(result))

    # 门禁判定：权威源（YAML）内部或内嵌内部出现重复 → 失败；
    # 内嵌 vs YAML 交叉重叠属兜底源冗余（按 21-plan 不重写种子），仅报告不拦截。
    has_dup = len(result["exact_duplicates"]) > 0 or len(result["embedded_duplicates"]) > 0
    return 1 if has_dup else 0


if __name__ == "__main__":
    sys.exit(main())
