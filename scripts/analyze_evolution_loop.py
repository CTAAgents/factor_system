"""scripts/analyze_evolution_loop.py — evolution_loop.py 职责盘点静态分析工具

背景: evolution_loop.py (5117 行, EvolutionLoop God Class) 拟按"先 B(Mixin 抽取)后 C(组合式重构)"
路径重构。本脚本为重构前置盘点的可复现工具, 输出:

  1. 方法概览: 每个方法 行数 / 读 self 属性数 / 写 self 属性数 / 调用的 self 方法 / 依赖的外部模块
  2. 属性读点矩阵: 每个 self 属性被哪些方法读 / 写 (按被读方法数降序, 即共享度排序)

用法:
    python scripts/analyze_evolution_loop.py                 # 终端输出两份清单
    python scripts/analyze_evolution_loop.py --json out.json # 同时导出结构化 JSON

输出可被后续 Mixin 抽取步骤反复运行以验证"每步迁移前后属性读写点集合不变"。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

# 项目根: 本脚本位于 scripts/ 下
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET = PROJECT_ROOT / "fts" / "factor_engine" / "evolution_loop.py"
ENGINE_DIR = PROJECT_ROOT / "fts" / "factor_engine"

# 常见第三方/标准库模块名 (不计入"委托已有模块"判定, 仅用于过滤)
SKIP_MODULES = {"np", "pd", "json", "math", "logging", "sys", "os", "time", "datetime"}


def scan_engine_module_names() -> set[str]:
    """扫描 fts/factor_engine/*.py 的模块名, 用于检测"薄包装委托已有模块"。”"""
    names = set()
    for p in ENGINE_DIR.glob("*.py"):
        if p.name in {"__init__.py", "evolution_loop.py"}:
            continue
        names.add(p.stem)
    return names


def scan_mixin_method_names() -> set[str]:
    """收集被 EvolutionLoop 继承的 Mixin 方法名（34 计划起跨文件拆分）。

    evolution_uct.py 等 Mixin 文件中的方法经多继承挂到 EvolutionLoop 上，
    其方法名必须纳入"方法调用"识别集合，否则 self.<mixin_method>() 会被
    误记为实例属性读点。
    """
    names: set[str] = set()
    for fname in ("evolution_uct.py", "evolution_trace.py", "evolution_channels.py", "evolution_seeds.py"):
        p = ENGINE_DIR / fname
        if not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(item.name)
    return names


def method_stats(
    m: ast.FunctionDef | ast.AsyncFunctionDef,
    engine_modules: set[str],
    class_method_names: set[str],
) -> dict:
    """解析单个方法: self 属性读写点 / self 方法调用 / 外部模块依赖。

    self.<attr> 引用若命中类方法名集合则归为"方法调用"（自引用），
    否则归为实例属性读写——避免属性读点清单混入方法引用噪音。
    """
    reads: set[str] = set()  # 实例属性读
    writes: set[str] = set()  # 实例属性写
    self_calls: set[str] = set()  # self.方法() 调用（含属性式方法引用）
    ext_modules: set[str] = set()  # 委托的外部模块

    for node in ast.walk(m):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            attr = node.attr
            if isinstance(node.ctx, ast.Store):
                if attr in class_method_names:
                    continue  # 方法不可能被写，防御
                writes.add(attr)
            else:
                if attr in class_method_names:
                    self_calls.add(attr)  # 方法引用（调用或装饰器）
                else:
                    reads.add(attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            fv = node.func.value
            if isinstance(fv, ast.Name) and fv.id == "self":
                self_calls.add(node.func.attr)
            elif isinstance(fv, ast.Name) and fv.id in engine_modules:
                ext_modules.add(fv.id)
            elif isinstance(fv, ast.Attribute) and isinstance(fv.value, ast.Name) and fv.value.id in engine_modules:
                ext_modules.add(fv.value.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in engine_modules:
            ext_modules.add(node.func.id)

    return {
        "name": m.name,
        "line": m.lineno,
        "end_line": m.end_lineno,
        "n_lines": m.end_lineno - m.lineno + 1,
        "n_args": len(m.args.args) + len(m.args.kwonlyargs) + (1 if m.args.vararg else 0) + (1 if m.args.kwarg else 0),
        "reads": sorted(reads),
        "writes": sorted(writes),
        "self_calls": sorted(self_calls),
        "ext_modules": sorted(ext_modules),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="evolution_loop.py 职责盘点分析")
    ap.add_argument("--json", dest="json_out", type=str, default=None, help="导出结构化 JSON 路径")
    args = ap.parse_args()

    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)
    engine_modules = scan_engine_module_names() - SKIP_MODULES

    # 收集类内方法名集合（EvolutionLoop 类 + 继承的 Mixin，用于区分属性与自方法引用）
    class_method_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_method_names.add(item.name)
    class_method_names |= scan_mixin_method_names()

    stats: list[dict] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stats.append(method_stats(item, engine_modules, class_method_names))

    # ── 方法概览 (按行数降序) ──
    print("=" * 100)
    print(f"文件: {TARGET}  总行数: {len(src.splitlines())}  方法数: {len(stats)}")
    print("=" * 100)
    print(f"{'方法':<40}{'行号':>6}{'行数':>6}{'读attr':>6}{'写attr':>6}{'self调用':>8}  外部模块")
    print("-" * 100)
    for s in sorted(stats, key=lambda x: -x["n_lines"]):
        ext = ",".join(s["ext_modules"]) or "-"
        print(
            f"{s['name']:<40}{s['line']:>6}{s['n_lines']:>6}"
            f"{len(s['reads']):>6}{len(s['writes']):>6}{len(s['self_calls']):>8}  {ext}"
        )

    # ── 属性读点矩阵 (按共享度降序) ──
    attr_readers: dict[str, set[str]] = {}
    attr_writers: dict[str, set[str]] = {}
    for s in stats:
        for a in s["reads"]:
            attr_readers.setdefault(a, set()).add(s["name"])
        for a in s["writes"]:
            attr_writers.setdefault(a, set()).add(s["name"])

    all_attrs = set(attr_readers) | set(attr_writers)
    print("\n" + "=" * 100)
    print("属性读点清单 (按被读方法数降序; R=读方法数, W=写方法数, 写:读列出方法)")
    print("=" * 100)
    for a in sorted(all_attrs, key=lambda x: (-len(attr_readers.get(x, set())), x)):
        r = attr_readers.get(a, set())
        w = attr_writers.get(a, set())
        r_str = ",".join(sorted(r)) if r else "-"
        w_str = ",".join(sorted(w)) if w else "-"
        print(f"[{len(r)}R/{len(w)}W] {a}")
        print(f"    写: {w_str}")
        print(f"    读: {r_str}")

    if args.json_out:
        out = {
            "file": str(TARGET),
            "n_lines": len(src.splitlines()),
            "methods": stats,
            "attr_readers": {k: sorted(v) for k, v in attr_readers.items()},
            "attr_writers": {k: sorted(v) for k, v in attr_writers.items()},
        }
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已导出: {args.json_out}")


if __name__ == "__main__":
    main()
