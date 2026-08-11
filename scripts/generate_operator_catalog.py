#!/usr/bin/env python3
"""generate_operator_catalog.py — C8 算子目录自动生成（幂等）。

读取 fts.factor_engine.expr_dsl.registry.build_registry() 元数据，
生成 docs/harness/_data/operator_catalog.yaml（name/category/params/
bounds/economic_meaning），供文档引用保持一致。重复运行内容一致。

用法:
    python scripts/generate_operator_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = PROJECT_ROOT / "docs" / "harness" / "_data" / "operator_catalog.yaml"

# 项目根动态加入 sys.path（运行于 scripts/ 目录时保证 fts 可导入）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_catalog_rows() -> list[dict]:
    """从 DSL 注册表提取算子元数据行（确定性排序）。"""
    from fts.factor_engine.expr_dsl.registry import build_registry

    reg = build_registry()
    rows = []
    for name in sorted(reg):
        meta = reg[name]
        rows.append(
            {
                "name": meta.name,
                "category": meta.category,
                "params": list(meta.params),
                "int_params": sorted(meta.int_params),
                "float_params": sorted(meta.float_params),
                "param_bounds": dict(sorted(meta.param_bounds.items())),
                "lookback_param": meta.lookback_param or "",
                "differentiable": meta.differentiable,
                "economic_meaning": meta.economic_meaning,
            }
        )
    return rows


def render_yaml(rows: list[dict]) -> str:
    """手写确定性 YAML（无外部 yaml 依赖，输出稳定）。"""
    lines = [
        "# FTS 算子目录（自动生成 by scripts/generate_operator_catalog.py，勿手改）",
        f"# 算子总数: {len(rows)}",
        "operators:",
    ]
    for r in rows:
        lines.append(f"  - name: {r['name']}")
        lines.append(f"    category: {r['category']}")
        lines.append(f"    params: [{', '.join(r['params'])}]")
        if r["int_params"]:
            lines.append(f"    int_params: [{', '.join(r['int_params'])}]")
        if r["float_params"]:
            lines.append(f"    float_params: [{', '.join(r['float_params'])}]")
        if r["param_bounds"]:
            b = ", ".join(f"{k}: {v[0]}-{v[1]}" for k, v in r["param_bounds"].items())
            lines.append(f"    param_bounds: {{{b}}}")
        if r["lookback_param"]:
            lines.append(f"    lookback_param: {r['lookback_param']}")
        lines.append(f"    differentiable: {str(r['differentiable']).lower()}")
        lines.append(f"    economic_meaning: {r['economic_meaning'] or '-'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        rows = build_catalog_rows()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 读取算子注册表失败: {e}", file=sys.stderr)
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = render_yaml(rows)
    changed = True
    if OUT_PATH.exists():
        try:
            changed = OUT_PATH.read_text(encoding="utf-8") != text
        except OSError:
            changed = True
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"✅ operator_catalog.yaml 生成: {len(rows)} 算子 ({'更新' if changed else '无变化, 幂等'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
